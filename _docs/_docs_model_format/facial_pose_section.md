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
| `+0x00` | `uint16` | Pose count per position/normal attribute |
| `+0x04` | `uint16` | Facial object count |
| `+0x08` | `uint32` | Facial object table pointer |

Each object-table entry is 12 bytes. Its word at `+0x08` points to a pair of
variable-size attribute records. Each record is `0x0C + pose_count * 4` bytes:

1. Position record, identified by format bytes `03 01 03 02`.
2. Normal record, identified by format bytes `03 02 03 02`.

### Attribute record

| Offset | Type | Meaning |
|---|---:|---|
| `+0x00` | `uint32` | Number of mapped entries per pose |
| `+0x04` | 4 bytes | Attribute/format bytes |
| `+0x08` | `uint32` | Run-list pointer |
| `+0x0c` | `uint32[pose_count]` | Pose-array pointers |

The position run list ends at the normal record's run-list pointer. The normal
run list ends at the first pose array. Each run is two big-endian `uint16`
values: `first_vertex` and `run_length`. Expanding all runs gives one GPL array
index for each pose entry.

Each verified pose entry is a quantized XYZ triplet of three big-endian signed
16-bit values (6 bytes). Pose zero in a position record matches the mapped GPL
position vertices exactly. Pose zero in a normal record likewise matches the
mapped GPL normal vertices.

## Birdo high-detail model

The section contains two facial objects. Their position records map 199 and 451
head vertices respectively, with five poses per object. The corresponding
normal records map 247 and 520 normal entries. The low-detail model contains no
section, which explains why ordinary GPL editing worked there.

`pose_count` is a stored format field rather than an implicit constant. A scan
of 69 original, in-range models with non-null `ptr7` found only the value 5, so
five is the only currently observed count. Exporters and importers must still
derive record sizes and pointer-array lengths from `pose_count` so other counts
remain representable.

## In-place patch rule

For every mapped position vertex and every pose:

```text
patched_pose = original_pose + (edited_GPL_position - original_GPL_position)
```

Applying the same displacement to every pose preserves the original blink and
mouth motion around the edited mesh shape. Pose data must be regenerated from
the original DAT on every patch run so repeated patching does not accumulate
the displacement. `--unpatch` restores the original pose arrays.

This is separate from SK1/SK2/SKAcc skinning. Birdo's facial head remains a
rigid GEO attachment on bone 56; bone 55 has no SK entry or vertex influence in
either LOD and is not responsible for the per-vertex overwrite.