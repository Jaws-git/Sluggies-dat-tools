"""Export, skin weights (SKN): the in-place encoder (``encode_skin_weights_inplace``), the Hammerspace encoder
(``encode_skin_hammerspace``) with its phases (donor index, vertex classification, role replay, entry
build, size message), and the helpers that tell whether a model's skin was edited at all.

The classification and entry phases take plain values and are tested without Blender. Imports
``ExportInplaceMesh`` and ``ExportBones`` among the ``Export*`` modules, and never ``ExportSluggies``.
"""

import math
import struct
from dataclasses import dataclass, field
from .SkinWeights import quantize_skin_weights, MAX_BONE_INFLUENCES_PER_VERTEX
from . import ExportMode
from . import SkinnedRebuildExport
from .ExportInplaceMesh import (
    _comp_size_skin,
    _from_bytes,
    _get_custom_split_normals,
    _pack_quantized_component,
    _to_bytes,
)
from .ExportBones import (
    _get_vgroup_weight,
    _parse_bone_group_name,
)


def _skn_block_size(skin_data: dict, flush_ind_size: int = None, edited: bool = False) -> int:
    """Calculate total byte size of an SKN block from a SkinData or SkinDataEdited dict.

    Mirrors the logic of patch_skn_dat.skn_block_size so the Blender exporter
    can compare original vs edited sizes without importing from SluggiesTools.
    With *edited*, each entry's *Edited field is measured where present
    (encode_skin_weights_inplace: does the edited layout still fit?).
    """
    sk1s   = skin_data.get('SK1s',   [])
    sk2s   = skin_data.get('SK2s',   [])
    skaccs = skin_data.get('SKAccs', [])
    if flush_ind_size is None:
        flush_ind_size = skin_data.get('FlushIndSize', 0)

    def _a4(n):
        return (n + 3) & ~3

    def _field(e, key):
        return _a4(len(_to_bytes((edited and e.get(key + 'Edited')) or e[key])))

    total  = 0x24                 # SKN header
    total += len(sk1s)   * 0x40  # SK1 structs
    total += len(sk2s)   * 0x74  # SK2 structs
    total += len(skaccs) * 0x44  # SKAcc structs

    for e in sk1s:
        total += _field(e, 'BindPoseData')
    for e in sk2s:
        total += _field(e, 'BindPoseData')
        total += _field(e, 'WeightData')
    for e in skaccs:
        total += _field(e, 'BindPoseData')
        total += _field(e, 'DestIndexData')
        total += _field(e, 'WeightData')

    if flush_ind_size:
        total += _a4(flush_ind_size * 2)

    return total


def encode_skin_weights_inplace(candidates, data, warnings, use_custom_normals=False):
    """Re-pack SK1/SK2/SKAcc bind-pose source data and SK2/SKAcc weight bytes
    from Blender vertex positions/normals and vertex groups (in-place mode).

    Supports variable vertex counts per entry: trailing vertices that have lost
    all bone influence are trimmed from the source arrays, reducing entry size.
    Middle-removed vertices (still needed to preserve dest-slot ordering for
    subsequent kept vertices) retain their original source data.

    For SKAcc entries, any vertex can be freely removed since dest indices are
    explicit; duplicate-dest entries are always preserved verbatim.

    Weights: a vertex that has its donor bones at unchanged weights keeps the
    donor's bytes, so an export without changes writes the donor's weight
    arrays. A vertex with changed weights gets its Blender weights
    normalized to 256 units (``_changed_weight_units``, as in Hammerspace
    mode), shared among the donor's bones of that vertex: only those have a
    record to hold a weight.

    Writes into each SK entry:
    - 'BindPoseDataEdited'     (SK1 / SK2 / SKAcc)
    - 'WeightDataEdited'       (SK2 / SKAcc)
    - 'DestIndexDataEdited'    (SKAcc only, when count changes)
    - 'VertexCntEdited'        (any type, when count changes)

    Returns True if any entries were written, False if none were, and None
    on size overflow (the edit then needs Hammerspace).
    """
    skin_data = data["SluggiesModel"].get("SkinData")
    if not skin_data or not (skin_data.get("SK1s") or skin_data.get("SK2s") or skin_data.get("SKAccs")):
        return False
    quant_info  = skin_data["QuantizeInfo"]
    use_compression  = data["SluggiesModel"].get("UseCompression", True)
    vertex_size = 6 * _comp_size_skin(quant_info)
    fmt_nibble  = quant_info >> 4
    is_float    = fmt_nibble in [4, 7, 0xa]
    divisor     = 1 << (quant_info & 0xF)
    submeshes   = data["SluggiesModel"].get("Submeshes", [])

    skinned_bone_ids = set()
    for _e in skin_data.get("SK1s", []):
        skinned_bone_ids.add(_e["BoneIndex"])
    for _e in skin_data.get("SK2s", []):
        skinned_bone_ids.add(_e["BoneIndex1"])
        skinned_bone_ids.add(_e["BoneIndex2"])
    for _e in skin_data.get("SKAccs", []):
        skinned_bone_ids.add(_e["BoneIndex"])

    # Build per-submesh (vtx_start, vtx_count) using cumulative vertex counts.
    # gplVertexArr is a byte offset from the start of the runtime dest buffer;
    # gplVertexArr // vertex_size gives a global vertex index that runs
    # sequentially across all submeshes (0, 1, 2, … total_verts-1).
    # This matches how extract_bone_data in export.py maps SK entries to Blender
    # vertices — do NOT subtract (VertexBufferOffset - gpl_base) here.
    sub_ranges = []
    cumulative = 0
    for sm in submeshes:
        vb = sm["VertexBuffer"]
        vb_cs     = _comp_size_skin(vb["VertexBufferQuantizeInfo"])
        vtx_count = vb["VertexBufferLength"] // (vb["VertexBufferCompCount"] * vb_cs)
        sub_ranges.append((cumulative, vtx_count, vb["VertexBufferOffset"]))
        cumulative += vtx_count

    # Map VertexBufferOffset → object for quick lookup
    obj_by_vb = {str(obj["VertexBufferOffset"]): obj for obj in candidates if "VertexBufferOffset" in obj}

    # Pre-compute custom split normals per object when requested
    custom_normals_cache = {}
    if use_custom_normals:
        for _obj in candidates:
            if "VertexBufferOffset" in _obj:
                _cn = _get_custom_split_normals(_obj)
                if _cn is not None:
                    custom_normals_cache[str(_obj["VertexBufferOffset"])] = _cn

    def resolve(global_vtx):
        for j, (start, count, vb_off) in enumerate(sub_ranges):
            if start <= global_vtx < start + count:
                return obj_by_vb.get(str(vb_off)), global_vtx - start
        return None, global_vtx

    def pack_val(v, context):
        if is_float:
            if not math.isfinite(float(v)):
                context = context() if callable(context) else context
                raise ValueError(f"{context}: coordinate/component is not finite ({v})")
            return struct.pack('>f', float(v))
        return _pack_quantized_component(v, divisor, context)

    def encode_vertex(obj, local_v):
        vd  = obj.data.vertices[local_v]
        _cn = custom_normals_cache.get(str(obj["VertexBufferOffset"]))
        nx, ny, nz = (
            (_cn[local_v].x, _cn[local_v].y, _cn[local_v].z)
            if _cn is not None
            else (vd.normal.x, vd.normal.y, vd.normal.z)
        )
        buf = bytearray()
        for component, val in zip(
            ('x', 'y', 'z', 'nx', 'ny', 'nz'),
            (vd.co.x, vd.co.y, vd.co.z, nx, ny, nz),
        ):
            buf.extend(pack_val(val, lambda component=component: f"{obj.name} vertex {local_v} {component}"))
        return bytes(buf)

    # Weights, per vertex: the donor's own bytes while the vertex has its
    # donor bones at unchanged weights, else the Blender weights normalized
    # to 256 units (the rule of the Hammerspace encoder,
    # _changed_weight_units). Only the donor's bones of a vertex can be
    # stored in place, so the units are shared among those.
    donor = _index_donor_skin(skin_data, vertex_size)
    units_cache = {}

    def weight_units(obj, local_v, global_v):
        """``{bone_id: units of 256}`` of a vertex with changed weights, or
        None when it keeps the donor's bytes."""
        if global_v in units_cache:
            return units_cache[global_v]
        donor_totals = donor.weight_total.get(global_v, {})
        parsed = []
        for g in obj.data.vertices[local_v].groups:
            if g.weight <= 0:
                continue
            bone_id = _parse_bone_group_name(obj.vertex_groups[g.group].name)
            if bone_id is not None and bone_id in donor_totals:
                parsed.append((bone_id, g.weight))
        parsed.sort(key=lambda item: (-item[1], item[0]))
        if not parsed:
            units = {}
        elif {bone_id for bone_id, _ in parsed} == set(donor_totals):
            units = _changed_weight_units(parsed, donor_totals)
        else:
            units = {}
            for bone_id, unit in quantize_skin_weights(parsed, target_sum=256):
                units[bone_id] = units.get(bone_id, 0) + unit
        units_cache[global_v] = units
        return units

    wrote_any = False

    # --- SK1 entries (source data only, no weights) ---
    # Re-encode in dest-slot order (position i → dest slot gplBase + vtx_off + i).
    # Trailing vertices with weight=0 are trimmed; middle-removed ones keep
    # their original source bytes so subsequent kept vertices stay at the
    # correct dest slot.
    for sk1 in skin_data.get("SK1s", []):
        bone_id      = sk1["BoneIndex"]
        n            = sk1["VertexCnt"]
        vtx_off      = sk1.get("VertexOffset", 0)
        global_start = (sk1["GplVertexArrValue"] + vtx_off) // vertex_size
        orig_src     = _to_bytes(sk1["BindPoseData"])

        # Detect full SK1 reassignment (all vertices moved from bone_A to one
        # other skinned bone via vertex-group rename/transfer).
        reassigned_bone = None
        seen_any = False
        unresolved = False
        candidate_ids = set()
        any_orig_weight = False
        for i in range(n):
            obj, local_v = resolve(global_start + i)
            if obj is None:
                unresolved = True
                continue
            seen_any = True
            if _get_vgroup_weight(obj, f"bone_{bone_id}", local_v) > 0:
                any_orig_weight = True

            ids_here = set()
            for g in obj.data.vertices[local_v].groups:
                if g.weight <= 0:
                    continue
                vg_name = obj.vertex_groups[g.group].name
                parsed = _parse_bone_group_name(vg_name)
                if parsed is not None and parsed in skinned_bone_ids:
                    ids_here.add(parsed)
            if len(ids_here) != 1:
                candidate_ids = set()
                break
            candidate_ids |= ids_here

        if seen_any and (not unresolved) and (not any_orig_weight) and len(candidate_ids) == 1:
            only_id = next(iter(candidate_ids))
            if only_id != bone_id:
                reassigned_bone = only_id

        target_bone_id = reassigned_bone if reassigned_bone is not None else bone_id
        target_bone_name = f"bone_{target_bone_id}"
        if reassigned_bone is not None:
            sk1["BoneIndexEdited"] = target_bone_id
        else:
            sk1.pop("BoneIndexEdited", None)

        last_kept  = -1
        slot_bytes = []
        for i in range(n):
            obj, local_v = resolve(global_start + i)
            if obj is None:
                # Vertex outside imported submeshes — treat as kept, preserve.
                slot_bytes.append(orig_src[vtx_off + i * vertex_size : vtx_off + (i + 1) * vertex_size])
                last_kept = i
            else:
                if _get_vgroup_weight(obj, target_bone_name, local_v) > 0:
                    slot_bytes.append(encode_vertex(obj, local_v))
                    last_kept = i
                else:
                    # Vertex removed — keep original for middle-ordering.
                    slot_bytes.append(orig_src[vtx_off + i * vertex_size : vtx_off + (i + 1) * vertex_size])

        new_count = last_kept + 1 if last_kept >= 0 else 0
        src = orig_src[:vtx_off] + b''.join(slot_bytes[:new_count])

        sk1["BindPoseDataEdited"] = _from_bytes(src, use_compression)
        if new_count != n:
            sk1["VertexCntEdited"] = new_count
        wrote_any = True

    # --- SK2 entries (source data + blend weights) ---
    # A vertex is "kept" if it still has at least one non-zero weight for
    # either bone in this pair.  Both weights zero → candidate for trailing trim.
    for sk2 in skin_data.get("SK2s", []):
        bone1        = sk2["BoneIndex1"]
        bone2        = sk2["BoneIndex2"]
        bone1_name   = f"bone_{bone1}"
        bone2_name   = f"bone_{bone2}"
        n            = sk2["VertexCnt"]
        vtx_off      = sk2.get("VertexOffset", 0)
        global_start = (sk2["GplVertexArrValue"] + vtx_off) // vertex_size
        orig_src     = _to_bytes(sk2["BindPoseData"])
        orig_wt      = _to_bytes(sk2["WeightData"])

        last_kept  = -1
        slot_src   = []
        slot_wt    = []
        for i in range(n):
            obj, local_v = resolve(global_start + i)
            if obj is None:
                slot_src.append(orig_src[vtx_off + i * vertex_size : vtx_off + (i + 1) * vertex_size])
                slot_wt.append(bytes([orig_wt[i * 2], orig_wt[i * 2 + 1]]))
                last_kept = i
            else:
                w1 = _get_vgroup_weight(obj, bone1_name, local_v)
                w2 = _get_vgroup_weight(obj, bone2_name, local_v)
                if w1 > 0 or w2 > 0:
                    slot_src.append(encode_vertex(obj, local_v))
                    units = weight_units(obj, local_v, global_start + i)
                    if units is None:
                        slot_wt.append(bytes([orig_wt[i * 2], orig_wt[i * 2 + 1]]))
                    else:
                        # A pair bone at the full 256 units is stored as 255 (one byte).
                        slot_wt.append(bytes([min(255, units.get(bone1, 0)), min(255, units.get(bone2, 0))]))
                    last_kept = i
                else:
                    # Both bones removed — keep original for middle-ordering.
                    slot_src.append(orig_src[vtx_off + i * vertex_size : vtx_off + (i + 1) * vertex_size])
                    slot_wt.append(bytes([orig_wt[i * 2], orig_wt[i * 2 + 1]]))

        new_count = last_kept + 1 if last_kept >= 0 else 0
        src = orig_src[:vtx_off] + b''.join(slot_src[:new_count])
        wt  = b''.join(slot_wt[:new_count])

        sk2["BindPoseDataEdited"] = _from_bytes(src, use_compression)
        sk2["WeightDataEdited"]   = _from_bytes(wt, use_compression)
        if new_count != n:
            sk2["VertexCntEdited"] = new_count
        wrote_any = True

    # --- SKAcc entries (source data + dest indices + weights) ---
    # Since dest indices are explicit, any vertex can be freely removed without
    # breaking the dest-slot ordering of other vertices.
    # Duplicate-dest entries are always preserved verbatim (same reasoning as
    # before: Blender stores only one weight per (bone, vertex) pair).
    for skacc in skin_data.get("SKAccs", []):
        bone_id   = skacc["BoneIndex"]
        bone_name = f"bone_{bone_id}"
        n         = skacc["VertexCnt"]
        dest_base = skacc["GplDestArrValue"] // vertex_size
        orig_src  = _to_bytes(skacc["BindPoseData"])
        orig_wt   = _to_bytes(skacc["WeightData"])
        dest_idxs = list(struct.unpack(f'>{n}H', _to_bytes(skacc["DestIndexData"])))

        dest_count = {}
        for di in dest_idxs:
            dest_count[di] = dest_count.get(di, 0) + 1
        dup_dests = {di for di, cnt in dest_count.items() if cnt > 1}

        new_src  = bytearray()
        new_dest = bytearray()
        new_wt   = bytearray()
        seen_dests = set()

        for i in range(n):
            di       = dest_idxs[i]
            global_v = dest_base + di
            obj, local_v = resolve(global_v)

            if di in dup_dests or obj is None:
                # Preserve verbatim — dup-dest or unknown vertex. A dup-dest
                # vertex with changed weights gets its weight on the first
                # record and 0 on the others (one weight per bone in Blender).
                units = weight_units(obj, local_v, global_v) if obj is not None else None
                if units is None:
                    weight = orig_wt[i]
                else:
                    weight = 0 if di in seen_dests else min(255, units.get(bone_id, 0))
                new_src  += orig_src[i * vertex_size : (i + 1) * vertex_size]
                new_dest += struct.pack('>H', di)
                new_wt   += bytes([weight])
            else:
                w = _get_vgroup_weight(obj, bone_name, local_v)
                if w > 0:
                    units = weight_units(obj, local_v, global_v)
                    new_src  += encode_vertex(obj, local_v)
                    new_dest += struct.pack('>H', di)
                    new_wt   += bytes([orig_wt[i] if units is None else min(255, units.get(bone_id, 0))])
                # else: vertex removed from this SKAcc entry — skip entirely.
            seen_dests.add(di)

        new_count = len(new_dest) // 2
        skacc["BindPoseDataEdited"]  = _from_bytes(bytes(new_src),  use_compression)
        skacc["WeightDataEdited"]    = _from_bytes(bytes(new_wt),   use_compression)
        if new_count != n:
            skacc["DestIndexDataEdited"] = _from_bytes(bytes(new_dest), use_compression)
            skacc["VertexCntEdited"]     = new_count
        wrote_any = True

    # --- Size check ---
    # Edited size can never exceed original because we only shrink or keep equal.
    # This guard catches any unforeseen logic errors.
    orig_size = _skn_block_size(skin_data)
    edit_size = _skn_block_size(skin_data, edited=True)

    if edit_size > orig_size:
        overflow = edit_size - orig_size
        # Strip all edited fields so the patcher ignores this model.
        for sk1 in skin_data.get("SK1s", []):
            for k in ('BindPoseDataEdited', 'VertexCntEdited', 'BoneIndexEdited'):
                sk1.pop(k, None)
        for sk2 in skin_data.get("SK2s", []):
            for k in ('BindPoseDataEdited', 'WeightDataEdited', 'VertexCntEdited'):
                sk2.pop(k, None)
        for skacc in skin_data.get("SKAccs", []):
            for k in ('BindPoseDataEdited', 'WeightDataEdited', 'DestIndexDataEdited', 'VertexCntEdited'):
                skacc.pop(k, None)
        warnings.append(
            f"SKN block too large for in-place patch. "
            f"Original: {orig_size} B, Edited: {edit_size} B, "
            f"Overflow: +{overflow} B. Reduce vertex count or simplify bone influences."
        )
        return None

    return wrote_any


def skin_membership_changed(candidates, data):
    """Whether a vertex of an exported skinned mesh now has other skinning
    bones than in the donor. The in-place encoder above cannot store that
    (it only rewrites weights inside each vertex's original entries), so the
    export then uses Hammerspace."""
    skin_data = data["SluggiesModel"].get("SkinData")
    if not skin_data:
        return False
    vertex_size = 6 * _comp_size_skin(skin_data["QuantizeInfo"])
    donor_sets = ExportMode.donor_skin_bone_sets(skin_data, vertex_size)
    model = data["SluggiesModel"]
    # A weight on any bone of the skeleton is a membership change too (the
    # body is then rebuilt with that bone): added bones and original bones the
    # donor never skins both drive skinning (Dolphin 2026-10-12, 2026-10-24).
    skinned_bone_ids = SkinnedRebuildExport.allowed_skin_bones(
        model.get("BoneHierarchyEdited") or model.get("BoneHierarchy")
    )
    if donor_sets:
        skinned_bone_ids |= set().union(*donor_sets.values())

    obj_by_vb = {str(obj["VertexBufferOffset"]): obj for obj in candidates if "VertexBufferOffset" in obj}
    edited_sets = {}
    start = 0
    for sm in data["SluggiesModel"].get("Submeshes", []):
        vb = sm["VertexBuffer"]
        count = vb["VertexBufferLength"] // (
            vb["VertexBufferCompCount"] * _comp_size_skin(vb["VertexBufferQuantizeInfo"])
        )
        obj = obj_by_vb.get(str(vb["VertexBufferOffset"]))
        if obj is not None and vb.get("VertexBufferCompCount") == 6:
            names = {group.index: group.name for group in obj.vertex_groups}
            for local_v, vertex in enumerate(obj.data.vertices[:count]):
                bones = set()
                for g in vertex.groups:
                    if g.weight <= 0:
                        continue
                    bone_id = _parse_bone_group_name(names.get(g.group, ""))
                    if bone_id is not None and bone_id in skinned_bone_ids:
                        bones.add(bone_id)
                edited_sets[start + local_v] = bones
        start += count
    return ExportMode.skin_membership_changed(donor_sets, edited_sets)


def skinned_donor_objects(candidates, data):
    """``{id(obj): (submesh_index, obj)}`` for the exported objects that are a
    donor *skinned* submesh (``VertexBufferCompCount == 6``), matched by
    ``VertexBufferOffset``.

    A model can include rigid/static submeshes (cc=3) whose vertices attach to
    a single non-skinned bone but still carry a ``bone_<id>`` vertex group from
    import; those never take part in SK1/SK2/SKAcc classification. Custom
    submeshes have no ``VertexBufferOffset`` at all and never match.
    """
    mapping = {}
    for submesh_index, submesh in enumerate(data["SluggiesModel"].get("Submeshes", [])):
        if submesh["VertexBuffer"].get("VertexBufferCompCount") != 6:
            continue
        vb_off = str(submesh["VertexBuffer"]["VertexBufferOffset"])
        for obj in candidates:
            if "VertexBufferOffset" in obj and str(obj["VertexBufferOffset"]) == vb_off:
                mapping[id(obj)] = (submesh_index, obj)
                break
    return mapping


def _skin_component_packer(quant_info):
    """``pack(value, context)`` for one bind-pose component (position or
    normal) in the quantization of ``SkinData.QuantizeInfo``."""
    is_float = (quant_info >> 4) in [4, 7, 0xa]
    divisor = 1 << (quant_info & 0xF)

    def pack_val(v, context):
        if is_float:
            if not math.isfinite(float(v)):
                context = context() if callable(context) else context
                raise ValueError(f"{context}: coordinate/component is not finite ({v})")
            return struct.pack('>f', float(v))
        return _pack_quantized_component(v, divisor, context)

    return pack_val


def _submesh_vertex_ranges(submeshes):
    """``(starts, counts)``: the first global vertex index and the vertex
    count of each submesh (for global <-> local mapping)."""
    starts = []
    counts = []
    cumulative = 0
    for sm in submeshes:
        vb = sm["VertexBuffer"]
        vb_cs = _comp_size_skin(vb["VertexBufferQuantizeInfo"])
        count = vb["VertexBufferLength"] // (vb["VertexBufferCompCount"] * vb_cs)
        starts.append(cumulative)
        counts.append(count)
        cumulative += count
    return starts, counts


@dataclass
class _DonorSkinIndex:
    """The donor SkinData, looked up by global vertex index.

    ``membership`` is used to detect whether the Blender vertex groups
    changed which bones influence a vertex; the entry maps and
    ``skacc_weight`` replay the donor's role assignment for the vertices
    whose bone set is unchanged.
    """
    vertex_stride: int               # 6 components (xyz + nxnynz) * component size
    skinned_bone_ids: set            # bones with any SK1/SK2/SKAcc entry
    sk1_entry_of: dict               # global vertex -> SK1s index
    sk2_entry_of: dict               # global vertex -> SK2s index
    first_sk1_entry_by_bone: dict    # bone id -> first SK1s index
    first_sk2_entry_by_pair: dict    # (lo, hi) -> first SK2s index
    membership: dict                 # global vertex -> {bone id}
    skacc_weight: dict               # (bone id, global vertex) -> donor weight byte
    weight_total: dict               # global vertex -> {bone id: units of 256, all its records summed}
    sk2_weights: dict = field(default_factory=dict)   # SK2s index -> its decoded WeightData


def _index_donor_skin(skin_data, vertex_stride):
    """Build the ``_DonorSkinIndex`` of *skin_data*."""
    # Only bones that already appear in the original SK data should feed the
    # rebuild.  Non-skinned bones own static submesh geometry and have NO SK
    # entries; their vertex groups must not be promoted to SK1/SK2/SKAcc entries
    # or the new block will exceed the original's size even with no mesh changes.
    index = _DonorSkinIndex(vertex_stride, set(), {}, {}, {}, {}, {}, {}, {})

    def add_weight(vertex, bone_id, units):
        totals = index.weight_total.setdefault(vertex, {})
        totals[bone_id] = totals.get(bone_id, 0) + units

    # entry-index maps (NOT bone-id maps): a bone can legitimately own more
    # than one SK1/SK2 entry (different gplVertexArr destinations) — grouping
    # by bone id alone would silently merge them and change the entry count
    # even with no membership edit at all. Verified against production data:
    # Luigi's bone 51 owns two separate SK1 entries.
    # The first_* maps are the fallback target for a vertex reassigned TO a
    # bone from elsewhere: reuse that bone's first existing entry rather than
    # always minting a new one.
    for idx, e in enumerate(skin_data.get('SK1s', [])):
        index.skinned_bone_ids.add(e['BoneIndex'])
        index.first_sk1_entry_by_bone.setdefault(e['BoneIndex'], idx)
        first = (e['GplVertexArrValue'] + e.get('VertexOffset', 0)) // vertex_stride
        for i in range(e['VertexCnt']):
            index.sk1_entry_of[first + i] = idx
            index.membership.setdefault(first + i, set()).add(e['BoneIndex'])
            add_weight(first + i, e['BoneIndex'], 256)
    for idx, e in enumerate(skin_data.get('SK2s', [])):
        index.skinned_bone_ids.add(e['BoneIndex1'])
        index.skinned_bone_ids.add(e['BoneIndex2'])
        pair = (min(e['BoneIndex1'], e['BoneIndex2']), max(e['BoneIndex1'], e['BoneIndex2']))
        index.first_sk2_entry_by_pair.setdefault(pair, idx)
        first = (e['GplVertexArrValue'] + e.get('VertexOffset', 0)) // vertex_stride
        pair_weights = index.sk2_weights[idx] = _to_bytes(e['WeightData'])
        for i in range(e['VertexCnt']):
            index.sk2_entry_of[first + i] = idx
            index.membership.setdefault(first + i, set()).update(
                (e['BoneIndex1'], e['BoneIndex2']))
            add_weight(first + i, e['BoneIndex1'], pair_weights[2 * i])
            add_weight(first + i, e['BoneIndex2'], pair_weights[2 * i + 1])
    # skacc_weight is the literal donor weight byte, for replaying an
    # unchanged vertex's SKAcc contribution exactly rather than re-deriving
    # it. Donor duplicate-dest (twist/blend) entries are handled separately
    # by _restore_duplicate_dest_skaccs, so keeping only the first match
    # here is fine.
    for e in skin_data.get('SKAccs', []):
        index.skinned_bone_ids.add(e['BoneIndex'])
        n = e['VertexCnt']
        if n == 0:
            continue
        dests = struct.unpack(f'>{n}H', _to_bytes(e['DestIndexData']))
        weights = _to_bytes(e['WeightData'])
        dest_base = e.get('GplDestArrValue', 0) // vertex_stride
        for pos, di in enumerate(dests):
            index.membership.setdefault(dest_base + di, set()).add(e['BoneIndex'])
            index.skacc_weight.setdefault((e['BoneIndex'], dest_base + di), weights[pos])
            add_weight(dest_base + di, e['BoneIndex'], weights[pos])
    return index


@dataclass
class _SkinGroups:
    """Result of classifying every vertex of the exported skinned meshes.

    SK1/SK2 group keys are donor ENTRY INDEXES when a vertex keeps its
    original entry's bone(s), falling back to the bone's/pair's first
    existing entry, or a synthetic ('new_sk1', bone_id) / ('new_sk2', lo, hi)
    key when reassigned to a bone/pair with no existing entry at all. This
    preserves entry count for the (overwhelmingly common) no-membership-
    change case, since a bone owning multiple separate SK1/SK2 entries must
    not be silently merged into one.
    """
    sk1: dict = field(default_factory=dict)    # entry key -> [(sub_idx, local_v, obj)]
    sk2: dict = field(default_factory=dict)    # entry key -> [(sub_idx, local_v, w_lo, w_hi, obj)]
    skacc: dict = field(default_factory=dict)  # bone_id -> [(sub_idx, local_v, weight_unit, dest_local_v, obj)]
    ignored_group_names: set = field(default_factory=set)
    unweighted: list = field(default_factory=list)    # (object name, vertex index)
    overweighted: list = field(default_factory=list)  # (object name, vertex index, influence count)
    membership_edited: bool = False
    # global vertex -> {bone id: units of 256}: bones unchanged, weights repainted
    refreshed: dict = field(default_factory=dict)


def _vertex_skin_influences(obj, v, skinned_bone_ids, groups, orig_bones):
    """``[(bone_id, weight)]`` of vertex *v* from its ``bone_<id>`` groups,
    strongest first and cut to the influence limit. Groups of bones the donor
    does not skin are noted in *groups* and left out. A vertex that still has
    exactly its donor bones (*orig_bones*) is not cut: original models hold up
    to five bones on a vertex, in the donor's own layout."""
    parsed = []
    for vge in v.groups:
        vg = obj.vertex_groups[vge.group]
        if not vg.name.startswith("bone_") or vge.weight <= 0.0:
            continue
        try:
            bone_id = int(vg.name[5:])
        except ValueError:
            continue
        if bone_id not in skinned_bone_ids:
            groups.ignored_group_names.add(vg.name)
            continue
        parsed.append((bone_id, vge.weight))
    parsed.sort(key=lambda x: -x[1])

    if len(parsed) > MAX_BONE_INFLUENCES_PER_VERTEX and {bone_id for bone_id, _ in parsed} != orig_bones:
        # The weakest weights are left out (quantize_skin_weights
        # normalizes the rest); more bones did not work in-game.
        groups.overweighted.append((obj.name, v.index, len(parsed)))
        parsed.sort(key=lambda x: (-x[1], x[0]))
        del parsed[MAX_BONE_INFLUENCES_PER_VERTEX:]
    return parsed


def _changed_weight_units(parsed, donor_totals):
    """``{bone_id: units of 256}`` when the weights of a vertex differ from
    the donor's, else None. *parsed* is ``[(bone_id, weight)]`` on the donor's
    bones, *donor_totals* ``{bone_id: units}``.

    Unchanged means every weight still rounds to the donor's units (an
    imported weight is units / 256, and original vertices need not sum to 256:
    255 and 257 occur), or the weights give the donor's units once normalized
    (all scaled alike). Changed weights are normalized to 256 units with the
    largest-remainder rule, as for a rebuilt body.
    """
    if all(abs(weight * 256 - donor_totals.get(bone_id, 0)) < 0.5 for bone_id, weight in parsed):
        return None
    units: dict[int, int] = {}
    for bone_id, unit in quantize_skin_weights(parsed, target_sum=256):
        units[bone_id] = units.get(bone_id, 0) + unit
    if all(units.get(bone_id, 0) == donor_totals.get(bone_id, 0) for bone_id, _ in parsed):
        return None
    return units


def _replay_donor_roles(skin_data, donor, groups, parsed, global_idx, sub_idx, v_idx, obj):
    """Classify a vertex whose bone set is unchanged from the donor.

    Membership (WHICH bones influence a vertex) always comes from Blender's
    current vertex groups. But the donor's choice of WHICH ROLE each bone
    plays — SK2 pair partner vs. SKAcc supplement — is an authored structural
    fact, not simply "the two heaviest weights": verified against production
    data (Luigi), 103 vertices have a real 3rd (SKAcc) influence, and picking
    the top-2-by-weight bones as the SK2 pair does NOT reliably reproduce the
    donor's actual pair (Blender's re-imported weight order need not match
    the donor's role assignment). So the donor's own role assignment is
    replicated exactly here.

    The weight bytes are the donor's own only while the weights are unchanged
    too (``_changed_weight_units``); a repainted vertex gets its Blender
    weights in the donor's roles and is noted in ``groups.refreshed``. A bone
    whose share rounds to nothing keeps its record with weight 0, so the
    entries stay the donor's.
    """
    units = _changed_weight_units(parsed, donor.weight_total.get(global_idx, {}))
    if units is not None and global_idx not in donor.sk1_entry_of:
        # An SK1 vertex has no weight record of its own to rewrite.
        groups.refreshed[global_idx] = units
    else:
        units = None

    def replay_skaccs(bone_ids):
        for bone_id in bone_ids:
            byte = donor.skacc_weight.get((bone_id, global_idx))
            if byte is not None:
                if units is not None:
                    byte = min(255, units.get(bone_id, 0))
                groups.skacc.setdefault(bone_id, []).append((sub_idx, v_idx, byte, v_idx, obj))

    if global_idx in donor.sk2_entry_of:
        entry_idx = donor.sk2_entry_of[global_idx]
        e = skin_data['SK2s'][entry_idx]
        b_lo = min(e['BoneIndex1'], e['BoneIndex2'])
        b_hi = max(e['BoneIndex1'], e['BoneIndex2'])
        first = (e['GplVertexArrValue'] + e.get('VertexOffset', 0)) // donor.vertex_stride
        k = global_idx - first
        wt_bytes = donor.sk2_weights[entry_idx]      # decoded once per entry, not per vertex
        w1_byte, w2_byte = wt_bytes[2 * k], wt_bytes[2 * k + 1]
        w_lo, w_hi = (w1_byte, w2_byte) if e['BoneIndex1'] <= e['BoneIndex2'] else (w2_byte, w1_byte)
        if units is not None:
            # A pair bone at the full 256 units is stored as 255 (one byte).
            w_lo, w_hi = min(255, units.get(b_lo, 0)), min(255, units.get(b_hi, 0))
        groups.sk2.setdefault(entry_idx, []).append((sub_idx, v_idx, w_lo, w_hi, obj))
        replay_skaccs(b for b, _ in parsed if b not in (b_lo, b_hi))
        return

    if global_idx in donor.sk1_entry_of:
        entry_idx = donor.sk1_entry_of[global_idx]
        sk1_bone = skin_data['SK1s'][entry_idx]['BoneIndex']
        groups.sk1.setdefault(entry_idx, []).append((sub_idx, v_idx, obj))
        replay_skaccs(b for b, _ in parsed if b != sk1_bone)
        return

    # SKAcc-only vertex: no SK1/SK2 primary in the donor at all.
    replay_skaccs(b for b, _ in parsed)


def _assign_edited_roles(skin_data, donor, groups, parsed, orig_bones, global_idx, sub_idx, v_idx, obj):
    """Classify a vertex whose bone set differs from the donor's.

    There is no donor role assignment to replicate, so quantize fresh — but
    keep the vertex in its donor skinning CATEGORY (SK1 / SK2 /
    accumulation-only) whenever the new bone set allows it. The build tool
    lays entries out without reordering vertices, and donor data never lets
    an SK1/SK2 entry share a 32-byte cache line with another entry or with an
    accumulation-only slot (verified across all 361 skinned models), so a
    category change mid-entry cannot be laid out.
    """
    new_bones = {bone_id for bone_id, _ in parsed}
    quantized = quantize_skin_weights(parsed, target_sum=256)
    merged: dict[int, int] = {}
    for bone_id, unit in quantized:
        merged[bone_id] = merged.get(bone_id, 0) + unit

    donor_sk1 = donor.sk1_entry_of.get(global_idx)
    donor_sk2 = donor.sk2_entry_of.get(global_idx)
    if donor_sk1 is None and donor_sk2 is None and orig_bones:
        # Donor accumulation-only vertex: its slot lives in the memClr
        # tail, so every influence stays an SKAcc contribution. A lone
        # full-weight bone clamps 256 -> 255 (same MVP loss as SK2).
        for bone_id, unit in sorted(merged.items()):
            groups.skacc.setdefault(bone_id, []).append(
                (sub_idx, v_idx, min(255, unit), v_idx, obj))
        return

    if len(parsed) == 1:
        bone_id = parsed[0][0]
        sk1_key = donor_sk1
        if sk1_key is None or skin_data['SK1s'][sk1_key]['BoneIndex'] != bone_id:
            sk1_key = donor.first_sk1_entry_by_bone.get(bone_id, ('new_sk1', bone_id))
        groups.sk1.setdefault(sk1_key, []).append((sub_idx, v_idx, obj))
        return

    # SK2 pair: the donor's surviving direct bone(s) first, then the
    # bones that replaced lost ones, then the rest by weight — so a
    # remapped SKAcc supplement bone stays a supplement.
    ranked = [bone_id for bone_id, _ in parsed]
    if donor_sk2 is not None:
        donor_pair = skin_data['SK2s'][donor_sk2]
        survivors = [b for b in (donor_pair['BoneIndex1'], donor_pair['BoneIndex2'])
                     if b in new_bones]
    elif donor_sk1 is not None:
        donor_bone = skin_data['SK1s'][donor_sk1]['BoneIndex']
        survivors = [donor_bone] if donor_bone in new_bones else []
    else:
        survivors = []
    replacements = [b for b in ranked if b not in orig_bones]
    top_bones = []
    for bone_id in survivors + replacements + ranked:
        if bone_id not in top_bones:
            top_bones.append(bone_id)
    top_bones = top_bones[:2]
    b_lo, b_hi = min(top_bones), max(top_bones)
    # A single dominant bone can legitimately claim the full 256-unit
    # budget (e.g. the second influence quantizes to zero); clamping
    # to 255 here loses at most 1/256 of a unit — accepted for MVP
    # rather than reclassifying the vertex as SK1 after the fact.
    w_lo = min(255, merged.get(b_lo, 0))
    w_hi = min(255, merged.get(b_hi, 0))
    sk2_key = donor.first_sk2_entry_by_pair.get((b_lo, b_hi), ('new_sk2', b_lo, b_hi))
    groups.sk2.setdefault(sk2_key, []).append((sub_idx, v_idx, w_lo, w_hi, obj))

    if len(parsed) > 2:
        for bone_id, unit in quantized:
            if bone_id in (b_lo, b_hi):
                continue
            groups.skacc.setdefault(bone_id, []).append((sub_idx, v_idx, unit, v_idx, obj))


def _classify_skin_vertices(obj_to_sub, skin_data, donor, sub_vtx_starts):
    """Sort every vertex of the exported skinned meshes into SK1 / SK2 /
    SKAcc groups (see ``encode_skin_hammerspace`` for the rules)."""
    groups = _SkinGroups()
    for _, (sub_idx, obj) in obj_to_sub.items():
        for v in obj.data.vertices:
            v_idx = v.index
            global_idx = sub_vtx_starts[sub_idx] + v_idx
            orig_bones = donor.membership.get(global_idx, set())
            parsed = _vertex_skin_influences(obj, v, donor.skinned_bone_ids, groups, orig_bones)

            if not parsed:
                # Real donor models legitimately leave some submesh-0
                # vertices with no SK1/SK2/SKAcc coverage at all (static/
                # unused position slots) — that is not an error. Only a
                # vertex that DID have a donor bone influence and lost it
                # is a genuine regression worth rejecting.
                if orig_bones:
                    groups.unweighted.append((obj.name, v_idx))
                    groups.membership_edited = True
                continue

            # Only a vertex whose bone set actually changed falls back to the
            # general influence-count heuristic; an unchanged one replays the
            # donor's roles, with the donor's weight bytes unless the weights
            # were repainted.
            if {bone_id for bone_id, _ in parsed} == orig_bones:
                _replay_donor_roles(skin_data, donor, groups, parsed, global_idx, sub_idx, v_idx, obj)
            else:
                groups.membership_edited = True
                _assign_edited_roles(
                    skin_data, donor, groups, parsed, orig_bones, global_idx, sub_idx, v_idx, obj)
    return groups


def _report_skin_classification(groups, warnings):
    """Add the classification warnings; raise ``ValueError`` when a vertex
    lost every donor bone influence."""
    by_object: dict[str, list[int]] = {}
    for name, idx, _count in groups.overweighted:
        by_object.setdefault(name, []).append(idx)
    for name, indices in by_object.items():
        warnings.append(SkinnedRebuildExport.limited_warning(name, indices))

    if groups.unweighted:
        detail = "; ".join(f"{name} vertex {idx}" for name, idx in groups.unweighted)
        ignored_detail = (
            f" Ignored vertex groups (not donor-skinned bones): "
            f"{', '.join(sorted(groups.ignored_group_names))}."
            if groups.ignored_group_names else ""
        )
        raise ValueError(
            f"{len(groups.unweighted)} vertex(es) have no valid donor bone influence: "
            f"{detail}.{ignored_detail}"
        )

    for name in sorted(groups.ignored_group_names):
        warnings.append(
            f"Vertex group '{name}' does not resolve to a donor-skinned bone; ignored.")


def _restore_duplicate_dest_skaccs(skin_data, skacc_groups, obj_to_sub,
                                   sub_vtx_starts, sub_vtx_counts, vertex_stride, warnings, refreshed):
    """Rebuild the donor SKAcc entries that accumulate twice onto one dest slot.

    A Blender vertex group holds only one weight per (vertex, bone), so such
    entries cannot survive the round trip. They are rebuilt in *skacc_groups*
    from the donor dest/weight arrays, taking only the bind-pose source from
    Blender. A vertex with repainted weights (*refreshed*) gets its new
    weight on its first record and 0 on the others.
    """
    obj_by_sub = {sub_idx: obj for _, (sub_idx, obj) in obj_to_sub.items()}

    def resolve_global_vertex(global_idx):
        for sub_idx, start in enumerate(sub_vtx_starts):
            if start <= global_idx < start + sub_vtx_counts[sub_idx]:
                obj = obj_by_sub.get(sub_idx)
                if obj is None:
                    return None
                local = global_idx - start
                if local >= len(obj.data.vertices):
                    return None
                return sub_idx, local, obj
        return None

    for e in skin_data.get('SKAccs', []):
        n = e['VertexCnt']
        if n == 0:
            continue
        dests = list(struct.unpack(f'>{n}H', _to_bytes(e['DestIndexData'])))
        if len(set(dests)) == n:
            continue
        weights = _to_bytes(e['WeightData'])
        dest_base = e.get('GplDestArrValue', 0) // vertex_stride
        current = {sub_vtx_starts[entry[0]] + entry[1]
                   for entry in skacc_groups.get(e['BoneIndex'], [])}
        if current != {dest_base + di for di in dests}:
            # This bone's accumulation membership was edited: restoring the
            # donor entry would silently undo the edit (or resurrect a bone
            # that lost every vertex), so keep the Blender-built entry.
            warnings.append(
                f"SKAcc bone {e['BoneIndex']}: membership edited, duplicate dest "
                f"slots (donor 255+1 weights) rebuilt from Blender weights")
            continue
        rebuilt = []
        seen = set()
        for di, w in zip(dests, weights):
            loc = resolve_global_vertex(dest_base + di)
            if loc is None:
                rebuilt = None
                break
            units = refreshed.get(dest_base + di)
            if units is not None:
                w = 0 if di in seen else min(255, units.get(e['BoneIndex'], 0))
            seen.add(di)
            sub_idx, local_v, obj = loc
            rebuilt.append((sub_idx, local_v, w, di, obj))
        if rebuilt is None:
            warnings.append(
                f"SKAcc bone {e['BoneIndex']}: duplicate dest slots could not be "
                f"resolved to mesh vertices; entry left as rebuilt from Blender")
            continue
        skacc_groups[e['BoneIndex']] = rebuilt


def _donor_aligned_weight_edits(skin_data, vertex_stride, refreshed, use_compression):
    """``[(donor SK2/SKAcc entry, WeightDataEdited)]`` for the entries that
    hold a vertex with repainted weights (*refreshed*).

    The array has the donor's length and order, so the Hammerspace builder
    can put it in place of ``WeightData`` without touching the layout: it is
    the field an in-place export writes. A vertex that an SKAcc entry writes
    twice gets its weight on the first record and 0 on the others.
    """
    edits = []
    if not refreshed:
        return edits
    for e in skin_data.get('SK2s', []):
        first = (e['GplVertexArrValue'] + e.get('VertexOffset', 0)) // vertex_stride
        weights = bytearray(_to_bytes(e['WeightData']))
        donor_weights = bytes(weights)
        for i in range(e['VertexCnt']):
            units = refreshed.get(first + i)
            if units is not None:
                weights[2 * i] = min(255, units.get(e['BoneIndex1'], 0))
                weights[2 * i + 1] = min(255, units.get(e['BoneIndex2'], 0))
        if weights != donor_weights:
            edits.append((e, _from_bytes(bytes(weights), use_compression)))
    for e in skin_data.get('SKAccs', []):
        n = e['VertexCnt']
        if n == 0:
            continue
        dests = struct.unpack(f'>{n}H', _to_bytes(e['DestIndexData']))
        weights = bytearray(_to_bytes(e['WeightData']))
        donor_weights = bytes(weights)
        dest_base = e.get('GplDestArrValue', 0) // vertex_stride
        seen = set()
        for pos, di in enumerate(dests):
            units = refreshed.get(dest_base + di)
            if units is not None:
                weights[pos] = 0 if di in seen else min(255, units.get(e['BoneIndex'], 0))
            seen.add(di)
        if weights != donor_weights:
            edits.append((e, _from_bytes(bytes(weights), use_compression)))
    return edits


def _encode_skin_source(entries, pack_val, custom_normals_cache, use_compression):
    """BindPoseData (position + normal per member) of one SK entry."""
    raw = bytearray()
    for entry in entries:
        obj_ref = entry[-1]
        v       = obj_ref.data.vertices[entry[1]]
        _cn     = custom_normals_cache.get(id(obj_ref))
        nx, ny, nz = (_cn[v.index].x, _cn[v.index].y, _cn[v.index].z) if _cn is not None else (v.normal.x, v.normal.y, v.normal.z)
        for component, val in zip(
            ('x', 'y', 'z', 'nx', 'ny', 'nz'),
            (v.co.x, v.co.y, v.co.z, nx, ny, nz),
        ):
            raw += pack_val(
                val,
                lambda v=v, component=component, obj_ref=obj_ref:
                    f"{obj_ref.name} vertex {v.index} SKN {component}",
            )
    return _from_bytes(bytes(raw), use_compression)


def _build_skin_entries(skin_data, groups, encode_src, use_compression):
    """``(SK1s, SK2s, SKAccs)`` entry lists of SkinDataEdited from *groups*.

    ``groups.sk1`` / ``groups.sk2`` keys are either a donor SK1s/SK2s entry
    INDEX (int) or a ('new_sk1', bone_id) / ('new_sk2', lo, hi) sentinel for
    a bone/pair with no existing donor entry. Each key is resolved back to
    its BoneIndex(es)/GplVertexArrValue: an existing entry's GPL value is a
    harmless best-effort hint for the no-membership-change case (Geometry
    Rebuild recomputes it unconditionally whenever MembershipEdited is set),
    and a synthetic entry gets a 0 placeholder — never read as final data
    either.
    """
    orig_sk1s_list = skin_data.get('SK1s', [])
    orig_sk2s_list = skin_data.get('SK2s', [])

    def _sk1_identity(key):
        if isinstance(key, int):
            e = orig_sk1s_list[key]
            return e['BoneIndex'], e.get('GplVertexArrValue', 0)
        return key[1], 0  # ('new_sk1', bone_id)

    def _sk2_identity(key):
        if isinstance(key, int):
            e = orig_sk2s_list[key]
            b_lo = min(e['BoneIndex1'], e['BoneIndex2'])
            b_hi = max(e['BoneIndex1'], e['BoneIndex2'])
            return b_lo, b_hi, e.get('GplVertexArrValue', 0)
        return key[1], key[2], 0  # ('new_sk2', lo, hi)

    _orig_skacc_gda = {
        e['BoneIndex']: e.get('GplDestArrValue', 0)
        for e in skin_data.get('SKAccs', [])
    }

    # VertexIndices: skinned position-slot index of each member, in payload
    # order. The build tool splits an entry into contiguous runs from these
    # instead of value-matching bind-pose records (ambiguous for duplicates).
    def encode_vertex_indices(entries):
        return _from_bytes(
            struct.pack(f'>{len(entries)}H', *(entry[1] for entry in entries)), use_compression)

    new_sk1s = []
    for key in sorted(groups.sk1, key=lambda k: _sk1_identity(k)[0]):
        entries = groups.sk1[key]
        bone_id, gpl_value = _sk1_identity(key)
        new_sk1s.append({
            "BoneIndex": bone_id,
            "VertexCnt": len(entries),
            "BindPoseData": encode_src(entries),
            "VertexIndices": encode_vertex_indices(entries),
            "GplVertexArrValue": gpl_value,
        })

    new_sk2s = []
    for key in sorted(groups.sk2, key=lambda k: _sk2_identity(k)[:2]):
        entries = groups.sk2[key]
        b_lo, b_hi, gpl_value = _sk2_identity(key)
        wt = bytearray()
        for e in entries:
            wt.append(max(0, min(255, e[2])))
            wt.append(max(0, min(255, e[3])))
        new_sk2s.append({
            "BoneIndex1": b_lo, "BoneIndex2": b_hi,
            "VertexCnt": len(entries),
            "BindPoseData": encode_src(entries),
            "WeightData": _from_bytes(bytes(wt), use_compression),
            "VertexIndices": encode_vertex_indices(entries),
            "GplVertexArrValue": gpl_value,
        })

    new_skaccs = []
    for bone_id, entries in sorted(groups.skacc.items()):
        wt   = bytearray()
        dest = bytearray()
        for e in entries:
            wt   += bytes([max(0, min(255, e[2]))])
            dest += struct.pack('>H', e[3])
        new_skaccs.append({
            "BoneIndex": bone_id, "VertexCnt": len(entries),
            "BindPoseData":   encode_src(entries),
            "WeightData":    _from_bytes(bytes(wt), use_compression),
            "DestIndexData": _from_bytes(bytes(dest), use_compression),
            "GplDestArrValue": _orig_skacc_gda.get(bone_id, 0),
        })

    return new_sk1s, new_sk2s, new_skaccs


def _skin_size_message(skin_data, candidate_edited, flush_ind_size):
    """The export note on how the rebuilt SKN block's size compares with the
    donor's, or ``None`` when it is the same."""
    orig_size = _skn_block_size(skin_data)
    edit_size = _skn_block_size(candidate_edited, flush_ind_size=flush_ind_size)

    # Per-category vertex totals — used in both feedback paths.
    orig_sk1s   = skin_data.get('SK1s',   [])
    orig_sk2s   = skin_data.get('SK2s',   [])
    orig_skaccs = skin_data.get('SKAccs', [])
    new_sk1s    = candidate_edited['SK1s']
    new_sk2s    = candidate_edited['SK2s']
    new_skaccs  = candidate_edited['SKAccs']
    orig_v1 = sum(e['VertexCnt'] for e in orig_sk1s)
    orig_v2 = sum(e['VertexCnt'] for e in orig_sk2s)
    orig_v3 = sum(e['VertexCnt'] for e in orig_skaccs)
    edit_v1 = sum(e['VertexCnt'] for e in new_sk1s)
    edit_v2 = sum(e['VertexCnt'] for e in new_sk2s)
    edit_v3 = sum(e['VertexCnt'] for e in new_skaccs)

    def _sk_stats(n1, v1, n2, v2, n3, v3, total):
        return (
            f"{total} B  "
            f"SK1: {n1} entries / {v1} verts  "
            f"SK2: {n2} entries / {v2} verts  "
            f"SKAcc: {n3} entries / {v3} verts"
        )

    stats = (
        f"  Original: {_sk_stats(len(orig_sk1s), orig_v1, len(orig_sk2s), orig_v2, len(orig_skaccs), orig_v3, orig_size)}\n"
        f"  Edited:   {_sk_stats(len(new_sk1s), edit_v1, len(new_sk2s), edit_v2, len(new_skaccs), edit_v3, edit_size)}"
    )

    if edit_size > orig_size:
        # Size growth is fine in hammerspace mode — the block is written to the
        # extended region of the dat file with no in-place size constraint.
        # (The hard overflow guard only applies to in-place mode, which uses
        # encode_skin_weights_inplace instead of this function.)
        return f"SKN block grew by {edit_size - orig_size} B (hammerspace accommodates this).\n{stats}"

    # The hammerspace rebuild always uses vertex_offset=0, so BindPoseData blobs
    # omit the VertexOffset prefix bytes present in original SK1/SK2 entries.
    # Add those bytes back to edit_size so the comparison reflects actual payload
    # content: when SK1/SK2/SKAcc entries and vertex counts are identical the sizes
    # compare as equal and no spurious shrinkage message is shown.
    def _a4_vo(n): return (n + 3) & ~3
    vo_adjustment = (
        sum(_a4_vo(e.get('VertexOffset', 0)) for e in orig_sk1s) +
        sum(_a4_vo(e.get('VertexOffset', 0)) for e in orig_sk2s)
    )
    effective_shrinkage = orig_size - (edit_size + vo_adjustment)
    if effective_shrinkage <= 0:
        return None

    msg = f"SKN block shrunk by {effective_shrinkage} B; patcher will zero-pad the remainder.\n{stats}"
    if not (edit_v1 == orig_v1 and edit_v2 == orig_v2 and edit_v3 == orig_v3):
        msg += (
            "\n  Note: SKAcc vertex count differs from original — this is normal"
            " when some SKAcc dest slots fall outside submesh 0 (e.g. Head/Accessory"
            " submeshes). Those vertices are not part of the edited mesh and do not"
            " affect main-mesh skinning. Hammerspace write can proceed."
        )
    return msg


def encode_skin_hammerspace(candidates, data, warnings, use_custom_normals=False):
    """Rebuild SK1/SK2/SKAcc from Blender vertex groups for hammerspace export.

    Membership (which bone(s) influence a vertex) is read directly from
    Blender's current bone_<id> vertex groups — NOT from donor SK1/SK2/SKAcc
    structure — so real bone reassignment (moving a vertex group between
    donor bones) round-trips correctly.

    Splitting rules (vertices whose bone set is unchanged replicate the donor
    role assignment exactly; for edited vertices):
      donor accumulation-only → SKAcc for every influence
      1 influence  → SK1
      2+ influences → SK2 (donor's surviving pair bones, then replacement
                      bones, then by weight) + SKAcc (remainder)
    Each SK1/SK2 entry carries VertexIndices; the build tool splits entries
    into contiguous runs without reordering vertices.

    Weights are normalized and quantized together with the deterministic
    largest-remainder rule (SkinWeights.quantize_skin_weights) so output does
    not depend on Blender vertex-group iteration order.

    A vertex that had a donor bone influence but now resolves to zero is a
    hard export error (raises ValueError naming every offending object+
    vertex) — real donor models legitimately leave some vertices with no
    SK1/SK2/SKAcc coverage at all (static/unused position slots), so a
    vertex with no influence in BOTH the donor and the current Blender
    groups is not an error. Vertex groups that don't resolve to a donor-
    skinned bone are ignored with a warning (deduped by group name), unless
    doing so leaves a previously-weighted vertex unweighted, in which case
    the unweighted error wins and includes the ignored names too.

    A vertex whose set of bones differs from the donor's is not handled
    here since add-on 0.8.5: the body takes the SkinnedRebuild route
    (``_skinned_rebuild_plan``), so this encoder raises if it meets one.

    Bone pairs in SK2 are stored with the lower BoneId first.
    Source data (bind-pose XYZ + NxNyNz) is encoded with SkinData.QuantizeInfo.
    Writes 'SkinDataEdited' at the model level.  Returns (ok, message).

    Phases: index the donor (``_index_donor_skin``), classify the vertices
    (``_classify_skin_vertices``), report (``_report_skin_classification``),
    restore duplicate-dest SKAccs (``_restore_duplicate_dest_skaccs``),
    encode (``_build_skin_entries``), size note (``_skin_size_message``).
    """
    skin_data = data["SluggiesModel"].get("SkinData")
    if not skin_data:
        return True, None   # unskinned model — nothing to encode, not an error

    submeshes  = data["SluggiesModel"].get("Submeshes", [])
    quant_info = skin_data["QuantizeInfo"]
    use_compression = data["SluggiesModel"].get("UseCompression", True)
    pack_val   = _skin_component_packer(quant_info)

    obj_to_sub = skinned_donor_objects(candidates, data)
    if not obj_to_sub:
        # No skinned donor mesh took part in this export -- e.g. only a custom
        # submesh was selected. There is nothing to classify, and writing the
        # empty result would replace the donor's whole SK1/SK2/SKAcc structure
        # with nothing. The caller leaves SkinData (and any SkinDataEdited from
        # an earlier run) alone instead.
        return True, None

    # The donor structure is used to detect whether the Blender vertex-group
    # edits changed skin membership anywhere in the model, and to replay the
    # donor's roles for unchanged vertices. SkinDataEdited is treated as the
    # complete source of truth for entry membership once an edit is detected;
    # membership must not be re-derived from stale donor per-entry structure.
    vertex_stride = 6 * _comp_size_skin(quant_info)
    donor = _index_donor_skin(skin_data, vertex_stride)
    sub_vtx_starts, sub_vtx_counts = _submesh_vertex_ranges(submeshes)

    # Pre-compute custom split normals per object when requested
    custom_normals_cache = {}
    if use_custom_normals:
        for _obj_id, (_, _obj) in obj_to_sub.items():
            _cn = _get_custom_split_normals(_obj)
            if _cn is not None:
                custom_normals_cache[_obj_id] = _cn

    groups = _classify_skin_vertices(obj_to_sub, skin_data, donor, sub_vtx_starts)
    _report_skin_classification(groups, warnings)
    _restore_duplicate_dest_skaccs(
        skin_data, groups.skacc, obj_to_sub, sub_vtx_starts, sub_vtx_counts, vertex_stride, warnings,
        groups.refreshed)

    def encode_src(entries):
        return _encode_skin_source(entries, pack_val, custom_normals_cache, use_compression)

    new_sk1s, new_sk2s, new_skaccs = _build_skin_entries(skin_data, groups, encode_src, use_compression)
    candidate_edited = {
        "QuantizeInfo": quant_info,
        "SK1s":         new_sk1s,
        "SK2s":         new_sk2s,
        "SKAccs":       new_skaccs,
    }
    if groups.membership_edited:
        # A changed bone set takes the body rebuild (SkinnedRebuild), which
        # is decided before this encoder runs; reaching this means the body
        # object was not planned for it.
        raise ValueError(
            "vertices moved between bones, but the body was not routed to the body rebuild; "
            "export the body object itself (not only other parts) so its skin can be rebuilt"
        )

    flush_ind_size = skin_data.get('FlushIndSize', 0)
    message = _skin_size_message(skin_data, candidate_edited, flush_ind_size)
    candidate_edited["FlushIndSize"] = flush_ind_size
    data["SluggiesModel"]["SkinDataEdited"] = candidate_edited
    # Repainted weights also go onto the donor entries, in the donor's order:
    # that is what the Hammerspace builder applies when the layout is kept.
    for entry, weight_data in _donor_aligned_weight_edits(
            skin_data, vertex_stride, groups.refreshed, use_compression):
        entry["WeightDataEdited"] = weight_data
    if groups.refreshed:
        note = f"Skin weights changed on {len(groups.refreshed)} vertex(es) that keep their bones."
        message = f"{message}\n{note}" if message else note
    return True, message


def _skin_data_edited_is_empty(data):
    """True when SkinDataEdited exists but carries no SK entry at all while the
    donor SkinData does.

    That combination is not something a real edit can produce -- it is the
    signature of an export that ran without the skinned mesh selected, before
    `skinned_donor_objects` gated the encode. Applying it would strip the
    model's skinning, and the patcher rejects it outright.
    """
    model = data.get("SluggiesModel", {})
    edited = model.get("SkinDataEdited")
    donor = model.get("SkinData")
    if not edited or not donor:
        return False
    lists = ("SK1s", "SK2s", "SKAccs")
    return (
        not any(edited.get(name) for name in lists)
        and any(donor.get(name) for name in lists)
    )


def _purge_skn_edited(data):
    """Strip all *Edited keys from SkinData SK entries and remove SkinDataEdited.

    Called at the start of every export so stale fields from a previous run
    never bleed into the new output.
    """
    model = data.get("SluggiesModel", {})
    model.pop("SkinDataEdited", None)
    skin = model.get("SkinData")
    if not skin:
        return
    sk1_keys   = ("BindPoseDataEdited", "VertexCntEdited", "BoneIndexEdited")
    sk2_keys   = ("BindPoseDataEdited", "WeightDataEdited", "VertexCntEdited")
    skacc_keys = ("BindPoseDataEdited", "WeightDataEdited", "DestIndexDataEdited", "VertexCntEdited")
    for e in skin.get("SK1s",   []):
        for k in sk1_keys:   e.pop(k, None)
    for e in skin.get("SK2s",   []):
        for k in sk2_keys:   e.pop(k, None)
    for e in skin.get("SKAccs", []):
        for k in skacc_keys: e.pop(k, None)
