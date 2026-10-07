"""Pure helpers for the ACT bone GeoId in-place patcher.

A rigid submesh is attached to a bone by the bone record's u16 GeoId field
(the `.sluggie`'s `BoneHierarchy[].GeoIdFieldOffset`). Moving a submesh to
another bone with **Keep offset to bone** changes nothing but these fields:
the old owner takes `0xFFFF`, the new owner takes the submesh index. Blender
writes that as `GeoIdEdited`, and the in-place patcher writes the words back
without touching any other byte, so this path works in place and in
hammerspace alike.

Kept free of top-level side effects (no argument parsing, no file I/O, no
logging), so it can be imported and unit-tested without executing
``patch_inplace.py``'s run-once pipeline.
"""

import struct


def bone_geo_raw_original(bone: dict) -> int:
    """The donor GeoId word of *bone* (`GeoIdRaw`, else `0xFFFF` for a skinned
    bone, else `GeoId`)."""
    if bone.get('GeoIdRaw') is not None:
        return int(bone['GeoIdRaw'])
    if bone.get('Skinned'):
        return 0xFFFF
    return int(bone.get('GeoId', 0xFFFF))


def bone_geo_patches(bone_hierarchy: list[dict], unpatch: bool, abort) -> list[tuple[int, int, bytes]]:
    """Plan the GeoId words to write.

    Returns ``(bone_id, file_offset, raw_u16)`` triples, one per bone that
    carries `GeoIdEdited`: on patch its edited word (skipped when it equals
    the donor word), on unpatch its donor word (the output DAT may hold the
    edited word from an earlier run, so it is always written). Bones without
    `GeoIdEdited` plan nothing either way. *abort* is called with a message
    for an out-of-range value, and, on patch, when an edited bone has no
    `GeoIdFieldOffset` to write to.
    """
    patches: list[tuple[int, int, bytes]] = []
    missing_offsets: list[int] = []
    for bone in bone_hierarchy or []:
        if bone.get('GeoIdEdited') is None:
            continue
        if unpatch:
            target_geo_raw = bone_geo_raw_original(bone)
        else:
            target_geo_raw = int(bone['GeoIdEdited'])

        if target_geo_raw < 0 or target_geo_raw > 0xFFFF:
            abort(
                f"Bone {bone.get('BoneId', '?')}: GeoId value {target_geo_raw} is out of range (0..65535)."
            )

        field_off = bone.get('GeoIdFieldOffset')
        if not field_off:
            missing_offsets.append(int(bone.get('BoneId', -1)))
            continue

        if not unpatch and target_geo_raw == bone_geo_raw_original(bone):
            continue

        patches.append((
            int(bone.get('BoneId', -1)),
            int(field_off, 16),
            struct.pack('>H', target_geo_raw),
        ))

    if missing_offsets and not unpatch:
        abort(
            "This .sluggie contains non-skinned bone reassignment edits but is missing "
            "BoneHierarchy.GeoIdFieldOffset metadata required for ACT in-place patching. "
            "Re-export the model with the latest SluggiesTools export.py, then export from Blender again. "
            f"Affected bones: {', '.join(str(x) for x in sorted(x for x in missing_offsets if x >= 0))}"
        )
    return patches
