# Facial Pose Section (`ptr7`)

The optional model-header pointer at `+0x18` is block-relative and can point to
facial pose data. This was verified on Birdo's high-detail `catherine.gpl`.
Her low-detail `L_catherine.gpl` has a null `ptr7` and no facial pose section.

The runtime copies selected pose coordinates from this section into the GPL
head position and normal arrays. Consequently, editing only the GPL position
array is temporary for mapped vertices: blinking and mouth animation restore
the original coordinates.

## Verified layout

All pointers below are relative to the start of this section.

| Offset | Type | Meaning |
|---|---:|---|
| `+0x00` | `uint16` | Maximum pose count advertised by the section |
| `+0x02` | `uint16` | Facial object count |
| `+0x04` | `uint16` | Attribute type/descriptor count |
| `+0x08` | `uint32` | Facial object table pointer |

Each object-table entry is 12 bytes:

| Offset | Type | Meaning |
|---|---:|---|
| `+0x00` | `uint16` | Pose count for this object |
| `+0x02` | `uint16` | Attribute count for this object |
| `+0x04` | `uint32` | Attribute record size (`0x0C + pose_count * 4`) |
| `+0x08` | `uint32` | Object attribute-data pointer |

Objects can use either separate three-component position and normal records,
or one six-component record interleaving XYZ and normal XYZ. Some objects also
contain auxiliary attributes that are preserved but are not Blender shape keys.

1. Position record, identified by format bytes `03 01 03 02`.
2. Normal record, identified by format bytes `03 02 03 02`.

### Attribute record

| Offset | Type | Meaning |
|---|---:|---|
| `+0x00` | `uint32` | Number of mapped entries per pose |
| `+0x04` | 4 bytes | Target submesh, attribute kind, component count, component byte width |
| `+0x08` | `uint32` | Run-list pointer |
| `+0x0c` | `uint32[pose_count]` | Pose-array pointers |

Each attribute run list ends at the next attribute's run-list pointer; the last
ends at the first pose array. Each run is two big-endian `uint16`
values: `first_vertex` and `run_length`. Expanding all runs gives one GPL array
index for each pose entry.

Position entries contain either XYZ (6 bytes) or interleaved XYZ+normal XYZ
(12 bytes), using big-endian signed 16-bit components. Pose zero is an absolute rest array: position pose zero
matches the mapped GPL position vertices exactly, and normal pose zero likewise
matches the mapped GPL normal vertices. Poses one and later are displacement
vectors relative to pose zero, not absolute coordinates. A zero triplet in
these poses means that the mapped entry remains at its rest value.

## Birdo high-detail model

The section contains two facial objects. Their position records map 199 and 451
head vertices respectively, with five poses per object. The corresponding
normal records map 247 and 520 normal entries. The low-detail model contains no
section, which explains why ordinary GPL editing worked there.

The section-level maximum pose count is 5 in the surveyed corpus, but each
object stores its own count. Observed object counts range from 2 through 6, so
record sizes and pointer-array lengths must use the per-object value.

## In-place patch rule

For every mapped position vertex in absolute pose zero:

```text
patched_pose = original_pose + (edited_GPL_position - original_GPL_position)
```

Poses one and later are already deltas, so a base mesh edit does not alter them.
Explicit Blender shape-key edits replace their corresponding delta buffers.
`--unpatch` restores all original pose arrays.

This is separate from SK1/SK2/SKAcc skinning. Birdo's facial head remains a
rigid GEO attachment on bone 56; bone 55 has no SK entry or vertex influence in
either LOD and is not responsible for the per-vertex overwrite.