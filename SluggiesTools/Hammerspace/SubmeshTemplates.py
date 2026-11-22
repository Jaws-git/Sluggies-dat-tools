"""Display-state templates of custom submeshes and new surfaces.

A new submesh or surface takes its display-state records from a template: a same-model rigid surface
(``rigid:``), a derived one (``derived:``) or a built-in capture of a vanilla draw list
(``builtin:``, ``_CUSTOM_SUBMESH_BUILTIN_TEMPLATES``; the Blender add-on's ``TemplateSources`` mirrors
that registry and a test pins the two together). This module holds the registry and builds the records.
"""

import drawlist
from binfmt import shader_mode_bytes as _shader_mode_bytes

from SluggieModel import CustomSubmesh, _custom_submesh_position_stride


_CUSTOM_SUBMESH_HAND_VISIBILITY_ROLES = frozenset({'RhSp', 'LhSp', 'SpRf', 'GhSp'})
_CUSTOM_SUBMESH_REJECTED_DERIVED_TYPE6 = '00000375'  # never occurs on a rigid submesh

# Canonical rigid attribute formats, used for entries that don't come
# from a same-model `rigid:` template. Byte strides of the int16/uint8
# formats below; a `rigid:` source may in principle carry the donor
# template's own (near-universally identical) format, but the MVP encodes
# every CustomSubmeshes buffer in these canonical formats regardless of
# source kind, so structural validation can check them uniformly here.
# Positions: CompCount 3 x int16 for the s16 formats the exporter writes, or
# 3 x f32 for the float format 64 -- see _custom_submesh_position_stride.
_CUSTOM_SUBMESH_NORMAL_STRIDE   = 6   # CompCount 3, QuantizeInfo 62 (3 x int16)
_CUSTOM_SUBMESH_UV_STRIDE       = 4   # CompCount 2, QuantizeInfo 62 (2 x int16)
_CUSTOM_SUBMESH_COLOR_STRIDE    = 2   # CompCount 4, QuantizeInfo 48 (RGBA4444)

# Byte-exact captures of complete vanilla rigid draw lists, one per shader
# mode. Every capture source below holds
# exactly these records and nothing else, in canonical order
# (T1 L0, [T1 L1], T4, T3, T6, T7), so `States` is the source submesh's whole
# DisplayStates list and `IdenticalRigidLists` counts the vanilla rigid
# submeshes whose whole list is byte-identical to it (surveyed over all 1,472
# player-folder exports).
#
# Only `States`, `Layers` and `Sha256` drive the build. The T1 texture indices
# and the T3 setting are provenance only: _custom_submesh_builtin_records
# rebinds every layer to the host model's own textures and the caller
# regenerates Type 3 from the host submesh's attribute set. `Layers` is the
# number of T1 records, which is what Type 4 follows -- so the 1-layer `Shdw`
# form keeps `T4 fffffff0` even on a 2-UV-channel host.
#
# `VerifiedInGame` gates a template: _validate_template_source refuses an
# unverified one and the Add submesh / Add material dialogs hide it
# (TemplateSources.BUILTIN_TEMPLATE_NAMES), until a Dolphin test
# confirms it. This registry is the only copy.
_CUSTOM_SUBMESH_BUILTIN_TEMPLATES = {
    'rigid_spec_v1': {
        'States': (
            (1, '000008', '11110000'),
            (1, '000000', '11002003'),
            (4, '000000', 'ffffff10'),
            (3, '000000', '000028a8'),
            (6, '010000', '00000374'),
            (7, '640064', 'Spec'),
        ),
        'Sha256': 'ee88bc44a3fb2a5864203b941519199ec61f1097eafef479eccf87de666dd862',
        'Layers': 2,
        'ShaderMode': 'Spec',
        'VerifiedInGame': True,
        'Provenance': {
            'Model': '33 Toadette/135708512_kinopico.gpl',
            'MeshName': 'pony_l3',
            'SurfaceId': 'sm1_ds5',
            'IdenticalRigidLists': 17,
        },
    },
    # `Shdw` never occurs on a vanilla character, so this capture comes from a
    # map object. Of the 672 rigid surfaces drawn with an effective Type-7
    # `Shdw`, 516 bind one texture layer; `T4 fffffff0` (1 layer),
    # `T6 00000374` and T7 pad `000000` are each the most common value, and
    # this list's Type-1 word matches rigid_spec_v1's in every documented
    # field (layer 0, wraps 1, wrapt 1) and in its undocumented top byte, so
    # the capture differs from the verified `Spec` built-in only in the three
    # fields the shader mode is meant to change.
    'rigid_shdw_v1': {
        'States': (
            (1, '000008', '11110002'),
            (4, '000000', 'fffffff0'),
            (3, '000000', '00000828'),
            (6, '010000', '00000374'),
            (7, '000000', 'Shdw'),
        ),
        'Sha256': '324907992ca4c44273724ddc0b2bc9983c9972cc69bcc5a57fdbb876b452df2b',
        'Layers': 1,
        'ShaderMode': 'Shdw',
        # Declared verified by the user on 2026-11-15 (an earlier in-game test
        # showed no visual anomalies). The first test (Dolphin, 2026-09-26)
        # had failed: the Shdw mode crashed on load and drew black on a
        # character cap, in this 1-layer form and in a copy of the cap's own
        # 2-layer group alike.
        'VerifiedInGame': True,
        'Provenance': {
            'Model': '137 Various A/653486528_manhole01.gpl',
            'MeshName': 'manhole',
            'SurfaceId': 'sm0_ds4',
            'IdenticalRigidLists': 9,
        },
    },
    # `GhSp` occurs on exactly four vanilla rigid surfaces: Birdo's ring and
    # diamond in the high- and low-poly models, all four byte-identical.
    'rigid_ghsp_v1': {
        'States': (
            (1, '000708', '11110003'),
            (1, '000000', '11002004'),
            (4, '000000', 'ffffff10'),
            (3, '000000', '000028a8'),
            (6, '010000', '00000174'),
            (7, '640064', 'GhSp'),
        ),
        'Sha256': '6a5e44b42125c7033de42f0a5b8a2ad9a744d52fb4a496dce4793640f57a1560',
        'Layers': 2,
        'ShaderMode': 'GhSp',
        # Declared verified by the user on 2026-11-15 (an earlier in-game test
        # showed no visual anomalies). An older test drew it invisible on
        # Wario's cap.
        'VerifiedInGame': True,
        'Provenance': {
            'Model': '35 Birdo/142642016_catherine.gpl',
            'MeshName': 'gold_ring',
            'SurfaceId': 'sm1_ds5',
            'IdenticalRigidLists': 4,
        },
    },
    # The hand visibility roles occur only on Mii hands (48 rigid surfaces
    # each). The male and female Mii lists differ solely in the T1 L0 pad
    # byte, which is why only 24 of the 48 are byte-identical to this capture.
    'rigid_rhsp_v1': {
        'States': (
            (1, '000308', '11110000'),
            (1, '000000', '11002003'),
            (4, '000000', 'ffffff10'),
            (3, '000000', '000028a8'),
            (6, '010000', '00000174'),
            (7, '96003c', 'RhSp'),
        ),
        'Sha256': 'e1ac62994ad67caefdad4b9ba68099da4b17aee417142df59cc5c27e063fbcd7',
        'Layers': 2,
        'ShaderMode': 'RhSp',
        'VerifiedInGame': True,  # Dolphin, 2026-09-26
        'Provenance': {
            'Model': '100 Blue Male Mii/333008640_mii_male.gpl',
            'MeshName': 'r_hand',
            'SurfaceId': 'sm7_ds5',
            'IdenticalRigidLists': 24,
        },
    },
    'rigid_lhsp_v1': {
        'States': (
            (1, '000408', '11110000'),
            (1, '000000', '11002003'),
            (4, '000000', 'ffffff10'),
            (3, '000000', '000028a8'),
            (6, '010000', '00000174'),
            (7, '96003c', 'LhSp'),
        ),
        'Sha256': 'c246a603b25923b7978231f0d5d63dfa5eb399d239527a3798ee968e61221ade',
        'Layers': 2,
        'ShaderMode': 'LhSp',
        'VerifiedInGame': True,  # Dolphin, 2026-09-26
        'Provenance': {
            'Model': '100 Blue Male Mii/333008640_mii_male.gpl',
            'MeshName': 'l_hand',
            'SurfaceId': 'sm4_ds5',
            'IdenticalRigidLists': 24,
        },
    },
    # Stadium surfaces (2026-10-01). No vanilla rigid submesh's *whole* list
    # has this shape with the cut-out Type 6, so these two are captured as the
    # effective states in force when one Yoshi Park surface draws
    # (`Provenance.Capture == 'effective'`), the way `derived:` builds its
    # list. Stadium surfaces bind one layer, have no normals (Type 3
    # pos+col0+tex0) and draw with `Shdw`/000000. The two differ only in the
    # T1 pad and in Type 6: 00000374 draws transparent texels opaque black,
    # 00000570 cuts them out. Both were confirmed as `rigid:` clones of these
    # very surfaces, with a root host bone (Dolphin, 2026-10-01). `Normals`
    # False: the custom submesh exports none, like the stadium surfaces.
    # `StadiumOnly`: refused outside STADIUM_CHUNKS -- untested on characters,
    # and rigid_shdw_v1 (the same shader, map-object capture) crashed there.
    'stadium_shdw_opaque_v1': {
        'States': (
            (1, '000530', '1111001e'),
            (4, '000000', 'fffffff0'),
            (3, '000000', '00000ccc'),
            (6, '010000', '00000374'),
            (7, '000000', 'Shdw'),
        ),
        'Sha256': '8ff8989456b2c48477e659a93a9bf4e40ddba523c109580d71642ceb0c5c9713',
        'Layers': 1,
        'ShaderMode': 'Shdw',
        'Normals': False,
        'StadiumOnly': True,
        'VerifiedInGame': True,  # as rigid:sm1_ds4 on sta03 (Dolphin, 2026-10-01)
        'Provenance': {
            'Model': '10 Yoshi Park/31388480_sta03.gpl',
            'MeshName': 'O[v02',
            'SurfaceId': 'sm1_ds4',
            'Capture': 'effective',
        },
    },
    'stadium_shdw_cutout_v1': {
        'States': (
            (1, '0002e8', '11110015'),
            (4, '000000', 'fffffff0'),
            (3, '000000', '00000ccc'),
            (6, '010000', '00000570'),
            (7, '000000', 'Shdw'),
        ),
        'Sha256': '1f1613fdad126805102dd913a8cba1f06933337cba99256dff1f8cd7ccb8abc3',
        'Layers': 1,
        'ShaderMode': 'Shdw',
        'Normals': False,
        'StadiumOnly': True,
        'VerifiedInGame': True,  # as rigid:sm1_ds42 on sta03 (Dolphin, 2026-10-01)
        'Provenance': {
            'Model': '10 Yoshi Park/31388480_sta03.gpl',
            'MeshName': 'O[v02',
            'SurfaceId': 'sm1_ds42',
            'Capture': 'effective',
        },
    },
}


def builtin_template_names(verified_only: bool = True) -> tuple[str, ...]:
    """Registry template names in registration order.

    ``verified_only`` keeps the ones confirmed in game, which is what the dialogs offer and what
    ``_validate_template_source`` accepts. ``TemplateSources`` mirrors the
    result for the Blender side, which cannot import this module.
    """
    return tuple(
        name for name, template in _CUSTOM_SUBMESH_BUILTIN_TEMPLATES.items()
        if template.get('VerifiedInGame') or not verified_only
    )


def _custom_submesh_state_records_sha256(records) -> str:
    """Hash display-state records as their 16-byte headers without pointers."""
    import hashlib
    payload = b''.join(
        bytes([state_id]) + bytes.fromhex(pad) + _shader_mode_bytes(mode)
        for state_id, pad, mode in records
    )
    return hashlib.sha256(payload).hexdigest()


_CUSTOM_SUBMESH_TEXTURE_LAYER_SHIFT = 13
_CUSTOM_SUBMESH_TEXTURE_INDEX_MASK  = 0x1FFF
_CUSTOM_SUBMESH_TYPE4_BY_UV_COUNT   = {1: 'fffffff0', 2: 'ffffff10'}


def _custom_submesh_texture_layer(mode: str) -> tuple[int, int]:
    """Decode a Type-1 ShaderMode hex string into (layer, texture_index)."""
    setting = int(mode, 16)
    return (
        (setting >> _CUSTOM_SUBMESH_TEXTURE_LAYER_SHIFT) & 7,
        setting & _CUSTOM_SUBMESH_TEXTURE_INDEX_MASK,
    )


def _custom_submesh_with_texture_index(mode: str, texture_index: int) -> str:
    """Return *mode* (a Type-1 ShaderMode hex string) with its texture index
    field replaced, keeping the layer and every other bit unchanged."""
    setting = int(mode, 16)
    rebound = (setting & ~_CUSTOM_SUBMESH_TEXTURE_INDEX_MASK) | (texture_index & _CUSTOM_SUBMESH_TEXTURE_INDEX_MASK)
    return f'{rebound:08x}'


def _custom_submesh_type3_descriptors(setting: int) -> list[dict]:
    """Decode a Type-3 setting word into the ordered attribute index layout."""
    descriptors = []
    for key, shift in drawlist._ATTR_BIT_SHIFT.items():
        mode = (setting >> shift) & 0b11
        if mode == 0b00:
            continue
        if mode == 0b01:
            raise ValueError(
                f'Type-3 setting 0x{setting:08X} uses a direct {key} attribute, '
                'which CustomSubmeshes do not support'
            )
        descriptors.append({'key': key, 'index_size': 1 if mode == 0b10 else 2})
    return descriptors


def _custom_submesh_type3_setting(layout: list[dict]) -> int:
    """Encode an attribute index layout as a Type-3 setting (inverse of
    _custom_submesh_type3_descriptors)."""
    setting = 0
    for descriptor in layout:
        shift = drawlist._ATTR_BIT_SHIFT[descriptor['key']]
        setting |= (0b10 if descriptor['index_size'] == 1 else 0b11) << shift
    return setting


def _custom_submesh_index_size_for(count: int) -> int:
    return 1 if count <= 0x100 else 2


def _custom_submesh_canonical_layout(cs: 'CustomSubmesh') -> list[dict]:
    """The attribute index layout for a `derived:`/`builtin:` custom submesh:
    position, then lighting/color0 if present, then one texture{i} per UV
    channel -- index sizes sized to the submesh's own entry counts."""
    layout = [{
        'key': 'position',
        'index_size': _custom_submesh_index_size_for(
            len(cs.vertex_data) // _custom_submesh_position_stride(cs.vertex_quantize_info)
        ),
    }]
    if cs.normal_data:
        layout.append({
            'key': 'lighting',
            'index_size': _custom_submesh_index_size_for(
                len(cs.normal_data) // _CUSTOM_SUBMESH_NORMAL_STRIDE
            ),
        })
    if cs.color_data:
        layout.append({
            'key': 'color0',
            'index_size': _custom_submesh_index_size_for(
                len(cs.color_data) // _CUSTOM_SUBMESH_COLOR_STRIDE
            ),
        })
    for uv in sorted(cs.uv_channels, key=lambda channel: channel.channel_index):
        layout.append({
            'key': f'texture{uv.channel_index}',
            'index_size': _custom_submesh_index_size_for(
                len(uv.uv_data) // _CUSTOM_SUBMESH_UV_STRIDE
            ),
        })
    return layout


def _custom_submesh_rigid_records(states: list, surface_index: int) -> list[list]:
    """`rigid:` template source: clone every DisplayStates record of the
    template submesh verbatim (state_id, pad, mode). Whichever record ends
    up drawing is decided by the caller; every other record's primitive list
    is dropped regardless of what it originally drew."""
    return [
        [
            int(state['DisplayStateId']),
            bytes.fromhex(state.get('DisplayStateParamBytes', '000000')),
            state.get('ShaderMode', ''),
        ]
        for state in states
    ]


def _custom_submesh_derived_records(submesh0_states: list, surface_id: str) -> list[list]:
    """`derived:` template source: build the canonical rigid record order
    from the effective states of a submesh-0 surface (validity is
    already enforced by _validate_custom_submeshes)."""
    surface_index = next(
        index for index, state in enumerate(submesh0_states)
        if state.get('SurfaceId') == surface_id
    )
    layers: dict[int, dict] = {}
    type6 = type7 = None
    for state in submesh0_states[:surface_index + 1]:
        state_id = int(state['DisplayStateId'])
        if state_id == 1:
            layers[_custom_submesh_texture_layer(state['ShaderMode'])[0]] = state
        elif state_id == 6:
            type6 = state
        elif state_id == 7:
            type7 = state

    def _rec(state: dict) -> list:
        return [
            int(state['DisplayStateId']),
            bytes.fromhex(state.get('DisplayStateParamBytes', '000000')),
            state['ShaderMode'],
        ]

    uv_count = 2 if 1 in layers else 1
    records = [_rec(layers[0])]
    if uv_count == 2:
        records.append(_rec(layers[1]))
    records += [
        [4, b'\x00\x00\x00', _CUSTOM_SUBMESH_TYPE4_BY_UV_COUNT[uv_count]],
        [3, b'\x00\x00\x00', '00000000'],  # placeholder; regenerated below
        _rec(type6),
        _rec(type7),
    ]
    return records


def _custom_submesh_builtin_records(model: dict, name: str) -> list[list]:
    """`builtin:` template source: bind the stored canonical rigid list to
    the host model's own submesh-0 textures."""
    template = _CUSTOM_SUBMESH_BUILTIN_TEMPLATES[name]
    submesh0 = (model.get('Submeshes') or [None])[0] or {}
    bindings: dict[int, int] = {}
    for state in submesh0.get('DisplayStates') or []:
        if int(state.get('DisplayStateId', -1)) == 1:
            layer, texture_index = _custom_submesh_texture_layer(state['ShaderMode'])
            bindings.setdefault(layer, texture_index)

    records = []
    for state_id, pad_hex, mode in template['States']:
        if state_id == 1:
            layer, _texture_index = _custom_submesh_texture_layer(mode)
            if layer not in bindings:
                continue
            mode = _custom_submesh_with_texture_index(mode, bindings[layer])
        records.append([state_id, bytes.fromhex(pad_hex), mode])

    # Type 4 follows the T1 records this template actually emits -- the
    # template's own layer count, capped by what the host binds. A 1-layer
    # built-in (`rigid_shdw_v1`) therefore keeps `T4 fffffff0` even when the
    # host submesh binds a layer-1 specular texture.
    uv_count = sum(1 for record in records if record[0] == 1)
    for record in records:
        if record[0] == 4:
            record[2] = _CUSTOM_SUBMESH_TYPE4_BY_UV_COUNT[uv_count]  # placeholder; regenerated below
    return records


def _custom_submesh_builtin_layer_count(model: dict, name: str) -> int:
    """Texture layers built-in *name* actually binds on *model*.

    That is its captured layer count capped by host submesh 0's own bindings,
    which is exactly the number of T1 records _custom_submesh_builtin_records
    emits -- and therefore what Type 4 declares and what the custom submesh's
    UV channel count has to match.
    """
    submesh0 = (model.get('Submeshes') or [None])[0] or {}
    bindings = {
        _custom_submesh_texture_layer(state['ShaderMode'])[0]
        for state in submesh0.get('DisplayStates') or []
        if int(state.get('DisplayStateId', -1)) == 1
    }
    return sum(
        1 for state_id, _pad, mode in _CUSTOM_SUBMESH_BUILTIN_TEMPLATES[name]['States']
        if state_id == 1 and _custom_submesh_texture_layer(mode)[0] in bindings
    )


def _resolve_custom_submesh_records(
    model: dict, cs: 'CustomSubmesh', rigid_surfaces: dict,
) -> tuple[list[list], int, str]:
    """Return (records, drawing_index, kind) for one CustomSubmesh, per its
    TemplateSource kind. ``records``
    entries are mutable ``[state_id, pad_bytes, shader_mode]`` lists; the
    caller still needs to resolve the active Type-3 layout and patch the
    layer-0 texture before turning them into DrawStates."""
    kind, _sep, argument = cs.template_source.partition(':')
    if kind == 'rigid':
        states, surface_index = rigid_surfaces[argument]
        return _custom_submesh_rigid_records(states, surface_index), surface_index, kind
    if kind == 'derived':
        submesh0 = (model.get('Submeshes') or [None])[0]
        records = _custom_submesh_derived_records(submesh0.get('DisplayStates') or [], argument)
        return records, len(records) - 1, kind
    if kind == 'builtin':
        records = _custom_submesh_builtin_records(model, argument)
        return records, len(records) - 1, kind
    raise ValueError(
        f"custom submesh '{cs.custom_submesh_id}': malformed TemplateSource "
        f'{cs.template_source!r}'
    )


def _custom_submesh_active_type3(records: list[list], upto_index: int) -> int | None:
    """Latest Type-3 setting at or before *upto_index* (cumulative-state walk,
    as in _custom_submesh_effective_state)."""
    for state_id, _pad, mode in reversed(records[:upto_index + 1]):
        if state_id == 3:
            return int(mode, 16)
    return None


def _custom_submesh_descriptors(
    cs: 'CustomSubmesh', records: list[list], drawing_index: int, kind: str,
) -> list[dict]:
    """Resolve the active Type-3 attribute layout for the drawing record.

    `rigid:` reuses the template's own Type-3 setting verbatim (clone
    template bytes, change only what's understood); `derived:`/`builtin:`
    regenerate Type-3 for the custom submesh's own attribute set and index
    sizes, overwriting the canonical placeholder record in place.
    """
    if kind == 'rigid':
        setting = _custom_submesh_active_type3(records, drawing_index)
        if setting is None:
            raise ValueError(
                f"custom submesh '{cs.custom_submesh_id}': rigid template has no "
                'active Type-3 attribute layout'
            )
        return _custom_submesh_type3_descriptors(setting)
    layout = _custom_submesh_canonical_layout(cs)
    setting = _custom_submesh_type3_setting(layout)
    for record in records:
        if record[0] == 3:
            record[2] = f'{setting:08x}'
            break
    return layout


def _custom_submesh_patch_layer0_texture(
    records: list[list], upto_index: int, texture_index: int,
) -> None:
    """Patch the effective layer-0 Type-1 texture index to *texture_index*
    (for the new submesh only). Layer 1 (specular) is left as the template/host's own
    binding."""
    for record in reversed(records[:upto_index + 1]):
        state_id, _pad, mode = record
        if state_id == 1 and _custom_submesh_texture_layer(mode)[0] == 0:
            record[2] = _custom_submesh_with_texture_index(mode, texture_index)
            return
    raise ValueError('template has no layer-0 Type-1 texture binding to patch')


# Type-7 shaders whose param byte 0 is a specular strength. In others it means
# something else (Shdw: a bit index) or nothing (the all-zero word), and most
# stadium surfaces have no Type 7 at all.
_CUSTOM_SUBMESH_SPECULAR_SHADER_MODES = frozenset({'Spec', 'LhSp', 'RhSp', 'GhSp'})


def _custom_submesh_patch_specular_strength(
    records: list[list], upto_index: int, strength: int,
) -> bool:
    """Set the specular intensity (param byte 0) of the effective Type-7
    record at or before *upto_index*; bytes 1-2 stay the template's.

    Returns False, changing nothing, when that record is missing or is not a
    specular shader (e.g. a `rigid:` stadium template): the Blender add-on
    gives every new surface a starting strength regardless."""
    if not 0 <= strength <= 255:
        raise ValueError(f'SpecularStrength {strength} is outside 0..255')
    for record in reversed(records[:upto_index + 1]):
        if record[0] == 7:
            if record[2] not in _CUSTOM_SUBMESH_SPECULAR_SHADER_MODES:
                return False
            record[1] = bytes([strength]) + bytes(record[1][1:3]).ljust(2, b'\x00')
            return True
    return False
