"""Check / Repair Models: the rules, on plain data.

Pure Python, no ``bpy`` import, so it is unit-testable outside Blender like
``HostBones.py``. ``ModelCheck.py`` reads the scene into the plain records
used here, runs the checks and applies the fixes; ``ModelCheckUI.py`` is the
dialog. The exporter calls ``ModelCheck.export_errors`` for the
error-severity checks.
"""

from __future__ import annotations

import math
import os
import re
import struct
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    from . import FieldCodec
except ImportError:  # imported flat by the unit tests
    import FieldCodec

ERROR = "ERROR"
WARNING = "WARNING"
INFO = "INFO"
SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}

# The importer's donor mesh names: <ChunkNumber>_<ModelOffset>_submesh<i>[_edit]
_MESH_NAME_RE = re.compile(r'^(\d+)_(0x[0-9a-fA-F]+)_submesh(\d+)(_edit)?(\.\d+)?$')
_BONE_NAME_RE = re.compile(r'^bone_(\d+)$')
_DONOR_SURFACE_RE = re.compile(r'^sm(\d+)_ds\d+$')

# The mesh properties that tie an object to its .sluggie submesh
# (ExportSluggies.REQUIRED_PROPS).
REQUIRED_PROPS = (
    "VertexBufferOffset",
    "VertexBufferLength",
    "VertexBufferCompCount",
    "VertexBufferQuantizeInfo",
)

# Properties the importer puts on a donor surface material.
DONOR_MATERIAL_PROPS = ("SurfaceId", "TextureIndex", "WrapS", "WrapT", "DisplayStateId", "ShaderMode")
# ... and the ones Add material puts on a new surface.
NEW_SURFACE_PROPS = ("SurfaceId", "SluggiesSurfaceOwner", "TemplateSource", "TemplateTextureIndex",
                     "WrapS", "WrapT")

# Marks a main body that Join or Replace meshes set aside (`<name>.backup`):
# in no model, and no stray mesh either.
BACKUP_PROP = "SluggiesBackup"

DEFAULT_TEMPLATE = "builtin:rigid_spec_v1"
DEFAULT_STADIUM_TEMPLATE = "builtin:stadium_shdw_opaque_v1"

GX_MAX_TEXTURE_DIMENSION = 1024
MAX_BONE_INFLUENCES = 4   # 5 failed in-game (2026-10-23); keep equal to SkinWeights.MAX_BONE_INFLUENCES_PER_VERTEX
U16_LIMIT = 0xFFFF   # vertices (and a custom submesh's triangles) one mesh can index


@dataclass
class Finding:
    check_id: str        # "unsaved_image", "bone_names", ...
    severity: str        # ERROR | WARNING | INFO
    object_name: str     # group key; the armature's name for model-level findings
    text: str            # dialog label, a few words
    detail: str          # long text: tooltip and export log
    fix_id: Optional[str] = None     # key into ModelCheck.FIXES, None = no automatic fix
    fix_args: dict = field(default_factory=dict)  # plain data the fix needs
    action_id: Optional[str] = None  # key into ModelCheckUI.ACTIONS: a button for a manual fix
    action_args: dict = field(default_factory=dict)  # its "object" is the one it selects first


def sort_findings(findings: Iterable[Finding]) -> List[Finding]:
    """Grouped by object (first appearance order), errors first in a group."""
    findings = list(findings)
    first = {}
    for index, finding in enumerate(findings):
        first.setdefault(finding.object_name, index)
    return sorted(findings, key=lambda f: (first[f.object_name], SEVERITY_ORDER[f.severity]))


def fix_summary(fixed: int, failed: int, left: int) -> str:
    return f"Fixed {fixed}, failed {failed}, {left} left"


def plural(count: int, word: str, plural_word: Optional[str] = None) -> str:
    return f"{count} {word if count == 1 else (plural_word or word + 's')}"


# ---------------------------------------------------------------------------
# Names and matching
# ---------------------------------------------------------------------------

def bone_id_from_name(name) -> Optional[int]:
    m = _BONE_NAME_RE.match(name or '')
    return int(m.group(1)) if m else None


def donor_surface_submesh(surface_id) -> Optional[int]:
    """sm<N>_ds<M> -> N, else None."""
    m = _DONOR_SURFACE_RE.match(surface_id or '')
    return int(m.group(1)) if m else None


def submesh_index_from_mesh_name(name, chunk_number=None, model_offset=None) -> Optional[int]:
    """The submesh index in an importer mesh name, when the name's model
    matches (*chunk_number*/*model_offset* None: not compared)."""
    m = _MESH_NAME_RE.match(name or '')
    if not m:
        return None
    if chunk_number is not None and int(m.group(1)) != int(chunk_number):
        return None
    if model_offset is not None and int(m.group(2), 16) != int(str(model_offset), 16):
        return None
    return int(m.group(3))


def vertex_buffer_vertex_count(vertex_buffer: dict) -> Optional[int]:
    comp_count = int(vertex_buffer.get("VertexBufferCompCount", 3) or 0)
    size = 4 if (int(vertex_buffer.get("VertexBufferQuantizeInfo", 0)) >> 4) in (4, 7, 0xA) else 2
    length = vertex_buffer.get("VertexBufferLength")
    if not comp_count or length is None:
        return None
    return int(length) // (comp_count * size)


def match_submesh(name: str, vertex_count: int, surface_submeshes: Set[int], model: dict) -> Optional[int]:
    """The submesh an object belongs to, for restoring its properties: the
    importer's name, else its donor materials' sm<N>, else the only submesh
    with its vertex count. None when nothing or more than one matches."""
    submeshes = model.get("Submeshes") or []
    index = submesh_index_from_mesh_name(name, model.get("ChunkNumber"), model.get("ModelOffset"))
    if index is not None and index < len(submeshes):
        return index
    if len(surface_submeshes) == 1:
        index = next(iter(surface_submeshes))
        return index if index < len(submeshes) else None
    if surface_submeshes:
        return None
    same_count = [
        i for i, sm in enumerate(submeshes)
        if vertex_buffer_vertex_count(sm.get("VertexBuffer") or {}) == vertex_count
    ]
    return same_count[0] if len(same_count) == 1 else None


def mesh_props_for_submesh(model: dict, index: int) -> dict:
    vb = (model.get("Submeshes") or [])[index].get("VertexBuffer") or {}
    return {prop: vb.get(prop) for prop in REQUIRED_PROPS}


def submesh_index_by_offset(model: dict, vertex_buffer_offset) -> Optional[int]:
    for index, sm in enumerate(model.get("Submeshes") or []):
        if str((sm.get("VertexBuffer") or {}).get("VertexBufferOffset")) == str(vertex_buffer_offset):
            return index
    return None


def display_state_for_surface(model: dict, surface_id) -> Optional[dict]:
    for sm in model.get("Submeshes") or []:
        for ds in sm.get("DisplayStates") or []:
            if ds.get("SurfaceId") == surface_id:
                return ds
    return None


def wrap_modes_for_texture(model: dict, submesh_index: int, texture_index) -> Tuple[int, int]:
    """(WrapS, WrapT) of the submesh's UV channel bound to *texture_index*,
    else of its first channel, else (1, 1) -- the importer's choice."""
    channels = ((model.get("Submeshes") or [])[submesh_index].get("UVChannels")) or []
    channel = next((ch for ch in channels if ch.get("TextureIndex") == texture_index), None)
    channel = channel or (channels[0] if channels else {})
    return int(channel.get("WrapS", 1)), int(channel.get("WrapT", 1))


def specular_strength_from_display_state(ds: dict) -> Optional[int]:
    if ds.get("DisplayStateId") != 7:
        return None
    try:
        return bytes.fromhex(ds.get("DisplayStateParamBytes", "000000"))[0]
    except (ValueError, IndexError):
        return 0


def default_template(stadium: bool) -> str:
    return DEFAULT_STADIUM_TEMPLATE if stadium else DEFAULT_TEMPLATE


def is_low_folder(folder_name: str) -> bool:
    return '_l_' in (folder_name or '').lower()


def low_partner_matches(low_folder: str, high_folder: str) -> bool:
    """Whether *high_folder* is the main model of Low model folder
    *low_folder* (the rule ExportSluggies._resolve_export_texture_context uses)."""
    low, high = (low_folder or '').lower(), (high_folder or '').lower()
    marker = low.find('_l_')
    if marker < 0 or '_l_' in high:
        return False
    return high.endswith('_' + low[marker + 3:])


_OFFSET_PREFIX_RE = re.compile(r'^\d+_')


# A model folder may hold extra .sluggie files (variants saved under a new
# name, same model identity). The rules below pick one of them; the
# command line tools keep their own copy (SluggiesTools/model_files.py).
def is_exported_name(path: str) -> bool:
    """Whether a ``.sluggie`` carries the name the model export gives it:
    its folder's name plus ``.sluggie``."""
    folder = os.path.basename(os.path.dirname(os.path.abspath(path)))
    return os.path.normcase(os.path.basename(path)) == os.path.normcase(folder + '.sluggie')


def variant_key(path: str) -> str:
    """The name a High and a Low ``.sluggie`` share: the basename without
    ``.sluggie``, its leading block offset and a leading ``L_``."""
    name = os.path.basename(path or '')
    if name.lower().endswith('.sluggie'):
        name = name[:-len('.sluggie')]
    name = _OFFSET_PREFIX_RE.sub('', name)
    if name[:2].lower() == 'l_':
        name = name[2:]
    return name.lower()


def prefer_exported(paths: Iterable[str]) -> Optional[str]:
    """The exported file among *paths*, else the only path, else None."""
    paths = list(paths)
    exported = [p for p in paths if is_exported_name(p)]
    if len(exported) == 1:
        return exported[0]
    return paths[0] if len(paths) == 1 else None


def pick_partner(candidates: Iterable[str], own_path: str) -> Optional[str]:
    """The High/Low partner of *own_path* among *candidates*: the one with
    the same :func:`variant_key`, else the exported file, else the only
    candidate; None when that leaves several."""
    candidates = list(candidates)
    key = variant_key(own_path)
    named = [c for c in candidates if variant_key(c) == key]
    if len(named) == 1:
        return named[0]
    return prefer_exported(candidates)


def model_identity(model: dict) -> Optional[Tuple[int, int, str]]:
    """``(ChunkNumber, FileIndex, ModelOffset)`` of a ``SluggiesModel``, or
    None when a field is missing: the game model a ``.sluggie`` patches."""
    chunk, index, offset = model.get("ChunkNumber"), model.get("FileIndex"), model.get("ModelOffset")
    if chunk is None or index is None or offset is None:
        return None
    return int(chunk), int(index), str(offset).lower()


def overwrite_error(source_model: dict, target_model: Optional[dict], target_name: str) -> Optional[str]:
    """Why an export of *source_model* may not replace the existing
    ``.sluggie`` *target_model* (None when it may): a file of another game
    model, or no ``.sluggie`` at all (*target_model* None)."""
    if target_model is None:
        return (f"{target_name} exists but is not a readable .sluggie file. Pick a new file "
                "name or a .sluggie of this model.")
    source, target = model_identity(source_model), model_identity(target_model)
    if source is not None and target != source:
        return (f"{target_name} belongs to a different model (directory {target[0] if target else '?'}, "
                f"file {target[1] if target else '?'}, offset {target[2] if target else '?'}) than the "
                f"selection (directory {source[0]}, file {source[1]}, offset {source[2]}). Pick a new "
                "file name or a .sluggie of this model.")
    return None


OUTPUT_ASSETS = '2_Output_Assets'
_FIND_DEPTH = 3   # 2_Output_Assets/<character>/<model>.gpl/<file>.sluggie


def output_assets_root(path: str) -> Optional[str]:
    """The ``2_Output_Assets`` folder *path* lies in, or None."""
    parts = os.path.normpath(os.path.abspath(path)).split(os.sep)
    for i in range(len(parts) - 1, -1, -1):
        if os.path.normcase(parts[i]) == os.path.normcase(OUTPUT_ASSETS):
            return os.sep.join(parts[:i + 1])
    return None


def find_sluggie_file(file_name: str, folders: Iterable[str], accept=None) -> str:
    """The first existing ``<folder>/.../<file_name>`` for *folders*, in order,
    or ''. Each folder is tried itself, then its direct subfolders (the
    ``<character>/<model>.gpl/`` export layout), then the whole
    ``2_Output_Assets`` tree it lies in. *accept(path)* -> bool rejects a
    namesake of another model. ``.sluggie`` names carry the model's input
    offset, so a name hit is normally the file that moved."""
    if not file_name:
        return ''
    seen = set()

    def ok(path):
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            return False
        seen.add(key)
        return os.path.isfile(path) and (accept is None or accept(path))

    folders = [f for f in folders if f]
    for folder in [f for f in folders if os.path.isdir(f)]:
        direct = os.path.join(folder, file_name)
        if ok(direct):
            return direct
        try:
            subdirs = [e.path for e in os.scandir(folder) if e.is_dir()]
        except OSError:
            subdirs = []
        for sub in subdirs:
            candidate = os.path.join(sub, file_name)
            if ok(candidate):
                return candidate
    roots = []   # also of a recorded folder that is gone: its tree may still hold the file
    for folder in folders:
        root = output_assets_root(folder)
        if root and os.path.isdir(root) and root not in roots:
            roots.append(root)
    for root in roots:
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath.count(os.sep) - base_depth >= _FIND_DEPTH:
                dirnames[:] = []
            if file_name in filenames or any(os.path.normcase(n) == os.path.normcase(file_name) for n in filenames):
                candidate = os.path.join(dirpath, file_name)
                if ok(candidate):
                    return candidate
    return ''


def clipboard_text(path: str) -> str:
    """What the exporter copies for the patcher: the bare name, which it
    finds in 2_Output_Assets, or the full path of a file elsewhere."""
    parts = os.path.normcase(os.path.abspath(path)).split(os.sep)
    return os.path.basename(path) if os.path.normcase('2_Output_Assets') in parts[:-1] else os.path.abspath(path)


def visible_collections(node) -> Set[str]:
    """Names of the collections shown in the viewport. *node* is the view
    layer's collection tree as ``(name, hidden, [child nodes])``; a
    collection is hidden with its own flag or any ancestor's. One linked
    into several parents counts as shown when any of its places shows it."""
    visible = set()

    def walk(node, parent_hidden):
        name, hidden, children = node
        hidden = parent_hidden or hidden
        if not hidden:
            visible.add(name)
        for child in children:
            walk(child, hidden)

    walk(node, False)
    return visible


def free_file_name(file_name: str, taken: Iterable[str]) -> str:
    """*file_name*, or ``<stem>_<n>.png`` with the lowest free n."""
    taken = {name.lower() for name in taken}
    if file_name.lower() not in taken:
        return file_name
    stem, ext = os.path.splitext(file_name)
    n = 1
    while f"{stem}_{n}{ext}".lower() in taken:
        n += 1
    return f"{stem}_{n}{ext}"


# ---------------------------------------------------------------------------
# Bones
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BoneInfo:
    name: str
    parent_name: Optional[str]
    user_added: bool


def donor_bone_problems(bones: Sequence[BoneInfo], hierarchy: Sequence[dict],
                        stadium: bool = False) -> Dict[str, List[str]]:
    """Original bones renamed, deleted or re-parented in Blender, and added
    bones without a parent. ``{kind: [bone names]}``; kinds 'renamed',
    'missing', 'reparented', 'orphan'. Export carries the original bones
    from the .sluggie, so the Blender-side change is ignored there. In a
    *stadium* an added bone without a parent is a new root bone, which export
    keeps (Dolphin 2026-10-18), so it is no problem there."""
    problems = {"renamed": [], "missing": [], "reparented": [], "orphan": []}
    by_id = {}
    for bone in bones:
        if bone.user_added:
            if bone.parent_name is None and not stadium:
                problems["orphan"].append(bone.name)
            continue
        bone_id = bone_id_from_name(bone.name)
        if bone_id is None:
            problems["renamed"].append(bone.name)
        else:
            by_id[bone_id] = bone
    for entry in hierarchy:
        bone_id = int(entry["BoneId"])
        bone = by_id.get(bone_id)
        if bone is None:
            problems["missing"].append(f"bone_{bone_id}")
            continue
        parent = entry.get("ParentBoneId")
        expected = None if parent is None else f"bone_{int(parent)}"
        if bone.parent_name != expected:
            problems["reparented"].append(bone.name)
    return problems


def root_bone_scale_ok(value) -> bool:
    try:
        values = [float(v) for v in value]
    except (TypeError, ValueError):
        return False
    return len(values) == 3 and all(math.isfinite(v) for v in values)


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------

def donor_covered_vertices(bone_hierarchy: Sequence[dict], submesh_index: int) -> Set[int]:
    """Vertices of *submesh_index* the .sluggie gives at least one bone
    influence (BoneHierarchy[].VertexInfluences, '>Hf' records)."""
    covered = set()
    for bone in bone_hierarchy or []:
        for entry in bone.get("VertexInfluences") or []:
            if entry.get("SubmeshIndex") != submesh_index:
                continue
            raw = FieldCodec.decode_field(entry.get("Influences") or b"")
            for vertex, weight in struct.iter_unpack('>Hf', raw[:len(raw) - len(raw) % 6]):
                if weight > 0:
                    covered.add(vertex)
    return covered


def allowed_skin_bones(bone_ids: Iterable[int], added_bone_ids: Iterable[int]) -> Set[int]:
    """Bones a body vertex may be weighted to: every original and added bone
    (SkinnedRebuildExport.allowed_skin_bones)."""
    return set(bone_ids) | set(added_bone_ids)


def unweighted_vertices(
    vertex_weights: Sequence[dict],
    used_vertices: Set[int],
    allowed: Set[int],
    parent_of: Dict[int, Optional[int]],
    donor_covered: Optional[Set[int]] = None,
) -> List[int]:
    """Body vertices the exporter refuses: used by a face, and no positive
    weight on an allowed bone or on a bone below one (export moves such
    weights to the nearest allowed ancestor). With *donor_covered* (same
    vertex count as the donor), vertices the original model leaves without
    influence are not counted: vanilla bodies have some."""
    def reaches_allowed(bone):
        seen = set()
        while bone is not None and bone not in seen:
            if bone in allowed:
                return True
            seen.add(bone)
            bone = parent_of.get(bone)
        return False

    result = []
    for vertex, weights in enumerate(vertex_weights):
        if vertex not in used_vertices:
            continue
        if donor_covered is not None and vertex not in donor_covered:
            continue
        if not any(weight > 0 and reaches_allowed(bone) for bone, weight in weights.items()):
            result.append(vertex)
    return result


def overweighted_vertices(vertex_weights: Sequence[dict], limit: int = MAX_BONE_INFLUENCES) -> List[int]:
    return [v for v, weights in enumerate(vertex_weights)
            if sum(1 for w in weights.values() if w > 0) > limit]


def limited_weights(weights: dict, limit: int = MAX_BONE_INFLUENCES) -> dict:
    """The *limit* strongest positive influences, normalized to 1."""
    kept = sorted(((b, w) for b, w in weights.items() if w > 0), key=lambda item: (-item[1], item[0]))[:limit]
    total = sum(w for _b, w in kept)
    return {b: w / total for b, w in kept} if total > 0 else {}


def host_bone_vote(vertex_bone_ids: Sequence[Set[int]]) -> Tuple[bool, Optional[int]]:
    """(rule ok, the bone most vertices are on) for the one-bone rule of a
    custom submesh: every vertex in exactly one bone_<id> group, all the same."""
    counts: Dict[int, int] = {}
    ok = True
    for ids in vertex_bone_ids:
        if len(ids) != 1:
            ok = False
        for bone_id in ids:
            counts[bone_id] = counts.get(bone_id, 0) + 1
    if len(counts) > 1:
        ok = False
    if not vertex_bone_ids:
        ok = False
    majority = min(counts, key=lambda b: (-counts[b], b)) if counts else None
    return ok, majority


# ---------------------------------------------------------------------------
# Size estimate
# ---------------------------------------------------------------------------

def _ceil_div(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor if value > 0 else 0


def image_payload_size(width: int, height: int, gx_format: int) -> int:
    """Encoded bytes of one GX image level (texture_helper._image_payload_size)."""
    if gx_format in (0x0, 0x8):
        return _ceil_div(width, 8) * _ceil_div(height, 8) * 32
    if gx_format in (0x1, 0x2, 0x9):
        return _ceil_div(width, 8) * _ceil_div(height, 4) * 32
    if gx_format in (0x3, 0x4, 0x5, 0xA):
        return _ceil_div(width, 4) * _ceil_div(height, 4) * 32
    if gx_format == 0x6:
        return _ceil_div(width, 4) * _ceil_div(height, 4) * 64
    if gx_format == 0xE:
        return _ceil_div(width, 4) * _ceil_div(height, 4) * 8
    raise ValueError(f"unsupported GX image format 0x{gx_format:08X}")


def clamp_texture_dimensions(width: int, height: int, max_dimension: int = GX_MAX_TEXTURE_DIMENSION):
    """Mirrors texture_helper.clamp_texture_dimensions."""
    longest = max(width, height)
    if longest <= max_dimension:
        return width, height
    factor = longest / max_dimension
    return (
        min(max_dimension, max(1, round(width / factor))),
        min(max_dimension, max(1, round(height / factor))),
    )


def texture_bytes(width: int, height: int, gx_format: int, additional_mips: int = 0,
                  palette_entries: int = 0) -> int:
    """Encoded bytes of one texture: the clamped mip chain plus its palette."""
    width, height = clamp_texture_dimensions(width, height)
    total = 0
    for level in range(int(additional_mips) + 1):
        total += image_payload_size(max(1, width >> level), max(1, height >> level), gx_format)
    if gx_format in (0x8, 0x9, 0xA):
        total += int(palette_entries or 0) * 2
    return total


def descriptor_texture_bytes(descriptor: dict, size: Optional[Tuple[int, int]] = None) -> int:
    """Bytes of a donor texture, re-encoded at PNG *size* when given."""
    width, height = size or (int(descriptor.get("Width", 0)), int(descriptor.get("Height", 0)))
    try:
        return texture_bytes(width, height, int(descriptor.get("Format", 0xE)),
                             int(descriptor.get("AdditionalMipCount", 0) or 0),
                             int(descriptor.get("PaletteEntries", 0) or 0))
    except ValueError:
        return int(descriptor.get("ImageDataCapacity") or descriptor.get("ImageDataLength") or 0)


def stored_texture_bytes(descriptor: dict) -> int:
    """What the original model block spends on this texture."""
    return int(descriptor.get("ImageDataCapacity") or descriptor.get("ImageDataLength") or 0)


@dataclass(frozen=True)
class MeshCounts:
    """One exported mesh's sizes, read from Blender."""
    vertices: int
    triangles: int
    uv_channels: int
    color_channels: int
    has_normal_buffer: bool
    comp_count: int        # position (+normal) components per vertex record
    comp_size: int         # bytes per component


def mesh_bytes(counts: MeshCounts) -> int:
    """Rough GPL bytes of a mesh with no donor to scale from (a custom
    submesh): vertex records, one UV/colour/normal entry per vertex and the
    stripified draw list (one index per attribute per corner, strips about
    halve it)."""
    corners = counts.triangles * 3
    records = counts.vertices * counts.comp_count * counts.comp_size
    uvs = counts.vertices * counts.uv_channels * 4
    colors = counts.vertices * counts.color_channels * 4
    normals = counts.vertices * 6 if counts.has_normal_buffer else 0
    attributes = 1 + counts.uv_channels + counts.color_channels + (1 if counts.has_normal_buffer else 0)
    draw_list = corners * attributes * 2 // 2
    return records + uvs + colors + normals + draw_list


def _decoded_length(value) -> int:
    try:
        return len(FieldCodec.decode_field(value or b""))
    except ValueError:
        return 0


def donor_mesh_parts(submesh: dict) -> Tuple[int, int]:
    """(per-vertex bytes, per-triangle bytes) the original block spends on
    a submesh: vertex buffer, a separate normal buffer, UV and colour
    coordinates -- these grow with the vertices -- and its primitive lists,
    which grow with the triangles."""
    vb = submesh.get("VertexBuffer") or {}
    vertex_part = int(vb.get("VertexBufferLength") or 0)
    normals = submesh.get("NormalBuffer") or {}
    if normals.get("NormalBufferLength"):
        start = int(str(vb.get("VertexBufferOffset", "0")), 16) if vb.get("VertexBufferOffset") else 0
        offset = int(str(normals.get("NormalBufferOffset", "0")), 16) if normals.get("NormalBufferOffset") else -1
        if not start <= offset < start + vertex_part:   # interleaved: already counted
            vertex_part += int(normals["NormalBufferLength"])
    vertex_part += sum(int(ch.get("UVChannelLength") or 0) for ch in submesh.get("UVChannels") or [])
    vertex_part += sum(int(ch.get("ColorChannelLength") or 0) for ch in submesh.get("ColorChannels") or [])
    triangle_part = sum(int(ds.get("PrimListLength") or 0) for ds in submesh.get("DisplayStates") or [])
    return vertex_part, triangle_part


def skin_bytes(skin_data: Optional[dict]) -> int:
    """The SKN payload of the original body: bind poses, weights and index
    arrays (entry headers left out)."""
    if not skin_data:
        return 0
    total = _decoded_length(skin_data.get("FlushIndData"))
    for key in ("SK1s", "SK2s", "SKAccs"):
        for entry in skin_data.get(key) or []:
            total += sum(_decoded_length(entry.get(field_))
                         for field_ in ("BindPoseData", "WeightData", "DestIndexData"))
    return total


def scaled_mesh_delta(submesh: dict, vertices: int, triangles: int, skin: int = 0) -> int:
    """Bytes a donor submesh grows by (negative: shrinks) at *vertices* /
    *triangles*: its stored per-vertex and per-triangle bytes (plus *skin*,
    the body's skin data) scaled by the ratio. An unchanged mesh gives 0."""
    donor_vertices = vertex_buffer_vertex_count(submesh.get("VertexBuffer") or {}) or 0
    donor_triangles = int(submesh.get("FacesCount") or 0)
    vertex_part, triangle_part = donor_mesh_parts(submesh)
    vertex_part += skin
    delta = 0
    if donor_vertices:
        delta += round(vertex_part * vertices / donor_vertices) - vertex_part
    if donor_triangles:
        delta += round(triangle_part * triangles / donor_triangles) - triangle_part
    return delta


def estimate_size(model: dict, mesh_counts: Dict[int, Tuple[int, int]], custom_mesh_bytes: int,
                  texture_sizes: Dict[str, Tuple[int, int]],
                  new_textures: Sequence[Tuple[int, int, int]]) -> Tuple[int, int]:
    """(model bytes, texture bytes) of the patched model.

    Starts from the original block (``ModelLength``) minus its stored
    textures, then adds :func:`scaled_mesh_delta` per edited submesh index in
    *mesh_counts* (index -> (vertices, triangles); submesh 0 carries the
    skin data along), plus the custom submeshes. Textures: every donor
    descriptor at its current PNG size (*texture_sizes*: file name ->
    (w, h); the descriptor's own size otherwise), plus *new_textures*
    (width, height, template texture index)."""
    descriptors = model.get("TextureDescriptors") or []
    submeshes = model.get("Submeshes") or []
    stored = sum(stored_texture_bytes(d) for d in descriptors)
    model_bytes = max(0, int(model.get("ModelLength") or 0) - stored)
    for index, (vertices, triangles) in mesh_counts.items():
        if 0 <= index < len(submeshes):
            skin = skin_bytes(model.get("SkinData")) if index == 0 else 0
            model_bytes += scaled_mesh_delta(submeshes[index], vertices, triangles, skin)
    model_bytes += custom_mesh_bytes
    textures = sum(
        descriptor_texture_bytes(d, texture_sizes.get(d.get("TextureFileName")))
        for d in descriptors
    )
    by_index = {int(d.get("TextureIndex", i)): d for i, d in enumerate(descriptors)}
    for width, height, template_index in new_textures:
        template = by_index.get(template_index) or {"Format": 0xE}
        textures += descriptor_texture_bytes(
            {**template, "AdditionalMipCount": template.get("AdditionalMipCount", 0)}, (width, height))
    return max(0, model_bytes), textures


def format_kb(value: int) -> str:
    return f"{(value + 512) // 1024:,} KB"


def size_line(model_bytes: int, texture_bytes_: int) -> str:
    return (f"Estimated size: {format_kb(model_bytes + texture_bytes_)} total "
            f"(model {format_kb(model_bytes)} + textures {format_kb(texture_bytes_)})")
