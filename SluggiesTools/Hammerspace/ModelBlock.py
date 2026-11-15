"""Bounds-checked reader for one model block (header, GPL, ACT, TEX, SKN).

The block layout is written down once, here: the header's section pointers,
the GPL submesh descriptors, layouts, array headers and display states, the
TEX descriptors, the ACT bone records and the SKN records. The modules that
inspect a block (``BlockValidator``, ``CompareBlocks``, ``LodTextureSync``,
``LodPartnerGuard``, ``UntangledTextures``) keep their rules and read the
fields through the views below.

Every view is an overlay at an absolute offset: it reads nothing when it is
made, and each field is read, bounds-checked, when it is accessed. A read
outside the buffer raises :class:`ModelBlockError`, the only error this
module raises. ``ModelBlock.raw`` and ``ModelBlock.find_nul`` are the two
unchecked accessors (a slice that may come back short, a search): payload
bytes and names are read with them, as the callers always did.

A block may sit inside a larger buffer (a DOL entry with an archive prefix):
``ModelBlock(data, start)`` resolves the header and the section pointers
from ``start`` and checks bounds against the whole buffer.

Format reference: ``_docs/_docs_model_format/``.
"""

from __future__ import annotations

import struct

from act_rebuild import (
    BONE_RECORD_SIZE as ACT_BONE_RECORD_SIZE,
    HEADER_SIZE as ACT_HEADER_SIZE,
)
from binfmt import bti

HEADER_SIZE = 0x20
#: Header word of each section pointer, in file order. The pointers are
#: relative to the block start; zero means the section is absent.
SECTION_POINTER_OFFSETS = {
    'GPL': 0x04,
    'ACT': 0x08,
    'TEX': 0x0C,
    'SKN': 0x10,
    'ptr6': 0x14,
    'ptr7': 0x18,
    'ptr8': 0x1C,
}

GPL_MAGIC = 0x00B749E0

#: Display state id of a texture bind (Type 1); its setting word holds the
#: TEX index in the low 13 bits and the texture layer in the 3 bits above.
TEXTURE_BIND_STATE_ID = 1
TEXTURE_INDEX_MASK = 0x1FFF
TEXTURE_LAYER_SHIFT = 13
TEXTURE_LAYER_MASK = 0x7

#: Bits per pixel of each GX texture format the models use.
TEXTURE_BITS_PER_PIXEL = {0: 4, 1: 8, 2: 8, 3: 16, 4: 16, 5: 16, 6: 32, 8: 4, 9: 8, 10: 16, 14: 4}


def texture_payload_len(fmt: int, width: int, height: int) -> int | None:
    """Bytes of a texture's level-0 image as the export reads and hashes it
    (``tpl.TEXDescriptor._data_length``: no rounding to whole blocks), or
    None for a format outside ``TEXTURE_BITS_PER_PIXEL``."""
    bits_per_pixel = TEXTURE_BITS_PER_PIXEL.get(fmt)
    if bits_per_pixel is None:
        return None
    return (width * height * bits_per_pixel) >> 3


class ModelBlockError(struct.error, IndexError):
    """A read outside the block.

    It is both a ``struct.error`` and an ``IndexError``: those are what the
    hand-written readers this module replaced raised for the same condition,
    so code that guards against either keeps working."""


class ModelBlock:
    """Checked reads over ``data``, for the model that starts at ``start``."""

    __slots__ = ('data', 'start')

    def __init__(self, data, start: int = 0):
        self.data = data
        self.start = start

    # -- raw access -------------------------------------------------------

    def has(self, offset: int, size: int) -> bool:
        """Whether ``size`` bytes at ``offset`` lie inside the buffer."""
        return offset >= 0 and size >= 0 and offset + size <= len(self.data)

    def uint(self, offset: int, size: int) -> int:
        """The ``size``-byte big-endian unsigned integer at ``offset``."""
        end = offset + size
        if offset < 0 or end > len(self.data):
            raise ModelBlockError(
                f'read of {size} byte(s) at 0x{offset:X} is outside the block (0x{len(self.data):X} bytes)'
            )
        return bti(self.data[offset:end])

    def u8(self, offset: int) -> int:
        return self.uint(offset, 1)

    def u16(self, offset: int) -> int:
        return self.uint(offset, 2)

    def u32(self, offset: int) -> int:
        return self.uint(offset, 4)

    def unpack(self, fmt: str, offset: int) -> tuple:
        """``struct.unpack_from(fmt, ...)`` at ``offset``, bounds-checked."""
        size = struct.calcsize(fmt)
        if offset < 0 or offset + size > len(self.data):
            raise ModelBlockError(
                f'read of {size} byte(s) at 0x{offset:X} is outside the block (0x{len(self.data):X} bytes)'
            )
        return struct.unpack_from(fmt, self.data, offset)

    def raw(self, offset: int, size: int) -> bytes:
        """``size`` bytes at ``offset``, NOT bounds-checked: like a slice, the
        result is short (or empty) when the range runs past the buffer."""
        return bytes(self.data[offset:offset + size])

    def find_nul(self, offset: int) -> int:
        """Offset of the first zero byte at or after ``offset``, or -1."""
        return self.data.find(b'\x00', offset)

    # -- header -----------------------------------------------------------

    def pointer(self, name: str) -> int:
        """The header's section pointer ``name`` (block-relative, 0 = absent)."""
        return self.u32(self.start + SECTION_POINTER_OFFSETS[name])

    def pointers(self) -> dict[str, int]:
        """Every section pointer, keyed and ordered as ``SECTION_POINTER_OFFSETS``."""
        return {name: self.u32(self.start + offset) for name, offset in SECTION_POINTER_OFFSETS.items()}

    def _section(self, name: str, view):
        pointer = self.pointer(name)
        return view(self, self.start + pointer) if pointer else None

    def gpl(self) -> GplSection | None:
        """The GPL section, or None when its pointer is zero. The magic is
        not checked here (``GplSection.magic``)."""
        return self._section('GPL', GplSection)

    def act(self) -> ActSection | None:
        return self._section('ACT', ActSection)

    def tex(self) -> TexSection | None:
        return self._section('TEX', TexSection)

    def skn(self) -> SknSection | None:
        return self._section('SKN', SknSection)


class _Field:
    """An unsigned big-endian field of a view, read when accessed."""

    __slots__ = ('rel', 'size')

    def __init__(self, rel: int, size: int):
        self.rel = rel
        self.size = size

    def __get__(self, view, owner=None):
        if view is None:
            return self
        return view.model.uint(view.offset + self.rel, self.size)


class _View:
    """A structure of ``model`` at the absolute offset ``offset``."""

    __slots__ = ('model', 'offset')

    def __init__(self, model: ModelBlock, offset: int):
        self.model = model
        self.offset = offset


# -- GPL --------------------------------------------------------------------

class ArrayHeader(_View):
    """Header of a position, colour, normal or UV array. ``data_ptr`` is
    relative to the submesh layout; zero means no array."""

    SIZE = 0x08
    data_ptr = _Field(0x00, 4)
    count = _Field(0x04, 2)
    quantize_info = _Field(0x06, 1)
    comp_count = _Field(0x07, 1)
    #: UV headers only: layout-relative pointer to the palette name.
    palette_name_ptr = _Field(0x08, 4)

    @property
    def ambient(self) -> float:
        """Normal headers only: the float that follows the header."""
        return self.model.unpack('>f', self.offset + 0x08)[0]


class DisplayState(_View):
    """One 16-byte display-state record."""

    SIZE = 0x10
    id = _Field(0x00, 1)
    setting = _Field(0x04, 4)
    primitive_ptr = _Field(0x08, 4)  # layout-relative
    primitive_length = _Field(0x0C, 4)

    @property
    def params(self) -> bytes:
        """The three parameter bytes after the id (unchecked, see ``ModelBlock.raw``)."""
        return self.model.raw(self.offset + 0x01, 3)

    @property
    def setting_offset(self) -> int:
        return self.offset + 0x04


class DisplayHeader(_View):
    """A submesh's display-state header; both pointers are layout-relative."""

    SIZE = 0x0A
    primitive_ptr = _Field(0x00, 4)
    state_table_ptr = _Field(0x04, 4)
    state_count = _Field(0x08, 2)


class SubmeshLayout(_View):
    """A submesh's layout: pointers (relative to this layout) to its array
    headers and its display-state header."""

    SIZE = 0x18
    #: UV headers follow each other at this distance from ``uv_header_ptr``.
    UV_HEADER_STRIDE = 0x10
    position_header_ptr = _Field(0x00, 4)
    color_header_ptr = _Field(0x04, 4)
    uv_header_ptr = _Field(0x08, 4)
    normal_header_ptr = _Field(0x0C, 4)  # zero: the submesh has no normal data
    display_header_ptr = _Field(0x10, 4)
    uv_count = _Field(0x14, 1)

    def array_header(self, pointer: int) -> ArrayHeader:
        return ArrayHeader(self.model, self.offset + pointer)

    def display_header_at(self, pointer: int) -> DisplayHeader:
        return DisplayHeader(self.model, self.offset + pointer)

    def display_header(self) -> DisplayHeader:
        return self.display_header_at(self.display_header_ptr)

    def display_state(self, state_table_ptr: int, index: int) -> DisplayState:
        return DisplayState(self.model, self.offset + state_table_ptr + index * DisplayState.SIZE)

    def display_states(self):
        """The submesh's display states, in table order."""
        header = self.display_header()
        state_table_ptr = header.state_table_ptr
        for index in range(header.state_count):
            yield self.display_state(state_table_ptr, index)


class SubmeshDescriptor(_View):
    """One entry of the GPL descriptor table; both pointers are GPL-relative."""

    SIZE = 0x08
    layout_ptr = _Field(0x00, 4)
    name_ptr = _Field(0x04, 4)


class GplSection(_View):
    magic = _Field(0x00, 4)
    user_data_length = _Field(0x04, 4)
    user_data_ptr = _Field(0x08, 4)
    submesh_count = _Field(0x0C, 4)
    descriptor_ptr = _Field(0x10, 4)

    def descriptor(self, descriptor_ptr: int, index: int) -> SubmeshDescriptor:
        return SubmeshDescriptor(self.model, self.offset + descriptor_ptr + index * SubmeshDescriptor.SIZE)

    def descriptors(self):
        """The submesh descriptors, in index order."""
        count = self.submesh_count
        descriptor_ptr = self.descriptor_ptr
        for index in range(count):
            yield self.descriptor(descriptor_ptr, index)

    def layout(self, layout_ptr: int) -> SubmeshLayout:
        return SubmeshLayout(self.model, self.offset + layout_ptr)


# -- TEX --------------------------------------------------------------------

class TexDescriptor(_View):
    """One texture descriptor; both pointers are relative to the TEX section."""

    SIZE = 0x20
    image_ptr = _Field(0x00, 4)
    palette_ptr = _Field(0x04, 4)
    height = _Field(0x08, 2)
    width = _Field(0x0A, 2)
    format = _Field(0x17, 1)
    palette_entries = _Field(0x18, 2)


class TexSection(_View):
    HEADER_SIZE = 0x04
    count = _Field(0x00, 2)
    clut_count = _Field(0x02, 2)

    def descriptor(self, index: int) -> TexDescriptor:
        return TexDescriptor(self.model, self.offset + self.HEADER_SIZE + index * TexDescriptor.SIZE)

    def descriptors(self):
        """The texture descriptors, in index order."""
        for index in range(self.count):
            yield self.descriptor(index)


# -- ACT --------------------------------------------------------------------

class Bone(_View):
    """One bone record. The four tree links and ``orientation_ptr`` (the
    bone's SRT) are relative to the ACT section; a link is the linked bone's
    record start."""

    SIZE = ACT_BONE_RECORD_SIZE
    #: Bytes of a record up to and including ``bone_id``.
    LINKED_SIZE = 0x18
    orientation_ptr = _Field(0x00, 4)
    previous_ptr = _Field(0x04, 4)
    next_ptr = _Field(0x08, 4)
    parent_ptr = _Field(0x0C, 4)
    first_child_ptr = _Field(0x10, 4)
    geo_id = _Field(0x14, 2)  # the submesh the bone owns, 0xFFFF for none
    bone_id = _Field(0x16, 2)


class ActSection(_View):
    HEADER_SIZE = ACT_HEADER_SIZE
    bone_count = _Field(0x06, 2)
    root_ptr = _Field(0x0C, 4)
    geo_name_ptr = _Field(0x10, 4)

    def bone(self, index: int) -> Bone:
        """The ``index``-th record of the bone table."""
        return self.bone_at(self.HEADER_SIZE + index * Bone.SIZE)

    def bone_at(self, pointer: int) -> Bone:
        """The bone record a tree link points at."""
        return Bone(self.model, self.offset + pointer)


# -- SKN --------------------------------------------------------------------

class Sk1(_View):
    """A one-bone skinning record. ``source_ptr`` is SKN-relative,
    ``vertex_array_ptr`` (gplVertexArr) relative to the skinned positions."""

    SIZE = 0x40
    MATRIX_SIZE = 0x30
    BONE_ID_FIELDS = (0x38,)
    source_ptr = _Field(0x30, 4)
    vertex_array_ptr = _Field(0x34, 4)
    bone_id = _Field(0x38, 2)
    vertex_count = _Field(0x3A, 2)
    vertex_offset = _Field(0x3C, 1)


class Sk2(_View):
    """A two-bone skinning record (see :class:`Sk1`)."""

    SIZE = 0x74
    MATRIX_SIZE = 0x60
    BONE_ID_FIELDS = (0x6C, 0x6E)
    source_ptr = _Field(0x60, 4)
    weight_ptr = _Field(0x64, 4)
    vertex_array_ptr = _Field(0x68, 4)
    bone_id_1 = _Field(0x6C, 2)
    bone_id_2 = _Field(0x6E, 2)
    vertex_count = _Field(0x70, 2)
    vertex_offset = _Field(0x72, 1)


class SkAcc(_View):
    """An accumulation record. The three array pointers are SKN-relative,
    ``destination_array_ptr`` (gplDestArr) relative to the skinned positions."""

    SIZE = 0x44
    MATRIX_SIZE = 0x30
    BONE_ID_FIELDS = (0x40,)
    source_ptr = _Field(0x30, 4)
    destination_index_ptr = _Field(0x34, 4)
    destination_array_ptr = _Field(0x38, 4)
    weight_ptr = _Field(0x3C, 4)
    bone_id = _Field(0x40, 2)
    vertex_count = _Field(0x42, 2)


class SknSection(_View):
    HEADER_SIZE = 0x24
    sk1_count = _Field(0x00, 2)
    sk2_count = _Field(0x02, 2)
    acc_count = _Field(0x04, 2)
    quantize_info = _Field(0x06, 1)
    sk1_ptr = _Field(0x08, 4)
    sk2_ptr = _Field(0x0C, 4)
    acc_ptr = _Field(0x10, 4)
    mem_clear_ptr = _Field(0x14, 4)  # relative to the skinned positions
    mem_clear_size = _Field(0x18, 4)
    flush_ptr = _Field(0x1C, 4)
    flush_count = _Field(0x20, 4)  # u16 entries

    def sk1(self, sk1_ptr: int, index: int) -> Sk1:
        return Sk1(self.model, self.offset + sk1_ptr + index * Sk1.SIZE)

    def sk2(self, sk2_ptr: int, index: int) -> Sk2:
        return Sk2(self.model, self.offset + sk2_ptr + index * Sk2.SIZE)

    def acc(self, acc_ptr: int, index: int) -> SkAcc:
        return SkAcc(self.model, self.offset + acc_ptr + index * SkAcc.SIZE)

    def entries(self) -> tuple[list[Sk1], list[Sk2], list[SkAcc]]:
        """Every SK1, SK2 and SKAcc record, each list in table order."""
        sk1_ptr, sk2_ptr, acc_ptr = self.sk1_ptr, self.sk2_ptr, self.acc_ptr
        return (
            [self.sk1(sk1_ptr, index) for index in range(self.sk1_count)],
            [self.sk2(sk2_ptr, index) for index in range(self.sk2_count)],
            [self.acc(acc_ptr, index) for index in range(self.acc_count)],
        )

    def bone_id_offsets(self) -> list[int]:
        """Absolute offset of every bone id (a u16) the records name."""
        return [
            entry.offset + field
            for group in self.entries()
            for entry in group
            for field in entry.BONE_ID_FIELDS
        ]
