"""Structural comparison of two Super Sluggers model blocks.

Verification tool: parses an original model block and a rebuilt
hammerspace block section-by-section and compares them at field/array level.
A bit-by-bit diff is NOT the goal — the rebuilt block legitimately differs in
padding/alignment layout.  What must match:

  GPL : submesh count, per-submesh mesh name, palette names, position/color/
        UV/normal counts + quantize/comp-count fields + raw array bytes,
        display state ids/params/settings + prim list bytes, GPL user data
  ACT : byte-identical (cloned)
  TEX : byte-identical (cloned)
  SKN : header counts + quantize info, per-entry bone indices / vertex counts /
        vertex offsets / gplVertexArr / gplDestArr + source/weight/dest-index
        array bytes, flush index bytes; memClrPtr/Size (reported, small
        formula variance tolerated in 1/325 models)
  TRAIL: trailing ptr6/7/8 sub-section bytes identical

Also validates 32-byte alignment invariants inside the rebuilt block
(block-relative): skinned position data, prim lists, SKN arrays.

Usage:
  py SluggiesTools/Hammerspace/CompareBlocks.py <original_block_file> <rebuilt_block_file>
  (files as written by writeDebugDumps: *_Original.SluggDebugg / *_Hammerspace.SluggDebugg)
"""

import os
import sys

# Initialize universal logger for standalone use.
_CBS_TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
if _CBS_TOOLS_DIR not in sys.path:
    sys.path.insert(0, _CBS_TOOLS_DIR)

import slogger as _slogger
_slogger.configure()

_pass = 0
_fail = 0
_notes = []


def check(label: str, ok: bool, detail: str = '') -> None:
    global _pass, _fail
    if ok:
        _pass += 1
    else:
        _fail += 1
        _slogger.error(f'FAIL  {label}' + (f'  ({detail})' if detail else ''), source="compare.blocks")


def note(msg: str) -> None:
    _notes.append(msg)


def cstr(b: bytes, off: int) -> str:
    end = b.index(0, off)
    return b[off:end].decode('ascii', errors='replace')


from binfmt import color_entry_size as color_stride, comp_size, u16  # noqa: E402,F401
from ModelBlock import GPL_MAGIC, GplSection, ModelBlock, SknSection  # noqa: E402

_HEADER_KEYS = {'GPL': 'gpl', 'ACT': 'act', 'TEX': 'tex', 'SKN': 'skn', 'ptr6': 'p6', 'ptr7': 'p7', 'ptr8': 'p8'}


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_block(blk: bytes) -> dict:
    """The compared fields of one block. A block cut short raises
    ``ModelBlock.ModelBlockError`` (a ``struct.error`` and an ``IndexError``)."""
    model = ModelBlock(blk)
    hdr = {_HEADER_KEYS[name]: pointer for name, pointer in model.pointers().items()}
    ptrs = sorted([p for p in hdr.values() if p] + [len(blk)])

    def section_end(start: int) -> int:
        return min(p for p in ptrs if p > start)

    out = {'hdr': hdr, 'len': len(blk)}

    # ---- GPL ----
    g = hdr['gpl']
    gpl = GplSection(model, g)
    if gpl.magic != GPL_MAGIC:           # not an assert: python -O would skip it
        raise ValueError('GPL magic missing')
    n = gpl.submesh_count
    desc = gpl.descriptor_ptr
    ud_len = gpl.user_data_length
    ud_ptr = gpl.user_data_ptr
    subs = []
    for i in range(n):
        descriptor = gpl.descriptor(desc, i)
        blob = descriptor.layout_ptr                # GPL-relative
        name = cstr(blk, g + descriptor.name_ptr)
        base = g + blob
        layout = gpl.layout(blob)
        pos_h, col_h, uv_h, nor_h, dsp_h = (layout.position_header_ptr, layout.color_header_ptr,
                                            layout.uv_header_ptr, layout.normal_header_ptr,
                                            layout.display_header_ptr)
        m_uv = layout.uv_count

        def hdr8(off, layout=layout):
            header = layout.array_header(off)
            return header.data_ptr, header.count, header.quantize_info, header.comp_count

        p_raw, p_cnt, p_q, p_cc = hdr8(pos_h)
        c_raw, c_cnt, c_q, c_cc = hdr8(col_h)
        pos_bytes = blk[base + p_raw: base + p_raw + p_cnt * comp_size(p_q) * p_cc] if p_raw else b''
        col_bytes = blk[base + c_raw: base + c_raw + c_cnt * color_stride(c_q)] if c_raw else b''
        col_block_off = (base + c_raw) if c_raw else 0

        uvs = []
        for j in range(m_uv):
            o = uv_h + j * layout.UV_HEADER_STRIDE
            raw, cnt, q, cc = hdr8(o)
            palette_ptr = layout.array_header(o).palette_name_ptr
            pal = cstr(blk, base + palette_ptr) if palette_ptr else ''
            data = blk[base + raw: base + raw + cnt * comp_size(q) * cc] if raw else b''
            uvs.append({'cnt': cnt, 'q': q, 'cc': cc, 'pal': pal, 'data': data,
                        'raw_rel': raw, 'palette_ptr': palette_ptr,
                        'block_off': (base + raw) if raw else 0})

        n_raw, n_cnt, n_q, n_cc = hdr8(nor_h)
        amb = layout.array_header(nor_h).ambient
        # standalone normals only (interleaved cc=6 normals live inside pos buffer)
        nor_bytes = b''
        if n_raw and p_cc != 6:
            nor_bytes = blk[base + n_raw: base + n_raw + n_cnt * comp_size(n_q) * n_cc]

        display = layout.display_header_at(dsp_h)
        ds_ptr = display.state_table_ptr
        n_ds = display.state_count
        dss = []
        for k in range(n_ds):
            state = layout.display_state(ds_ptr, k)
            pl_ptr, pl_len = state.primitive_ptr, state.primitive_length
            dss.append({
                'id': state.id, 'params': state.params,
                'setting': state.setting,
                'record_rel': ds_ptr + k * state.SIZE, 'prim_rel': pl_ptr,
                'pl': blk[base + pl_ptr: base + pl_ptr + pl_len] if pl_ptr else b'',
                'pl_block_off': (base + pl_ptr) if pl_ptr else 0,
            })
        subs.append({
            'name': name,
                'layout_rel': blob,
                'name_ptr': descriptor.name_ptr,
                'headers': {'pos': pos_h, 'col': col_h, 'uv': uv_h, 'nor': nor_h, 'display': dsp_h},
                'display': {'primitive_ptr': display.primitive_ptr, 'state_ptr': ds_ptr},
            'pos': {'cnt': p_cnt, 'q': p_q, 'cc': p_cc, 'data': pos_bytes,
                    'raw_rel': p_raw, 'block_off': base + p_raw if p_raw else 0},
            'col': {'cnt': c_cnt, 'q': c_q, 'cc': c_cc, 'data': col_bytes,
                    'raw_rel': c_raw, 'block_off': col_block_off},
            'uvs': uvs,
            'nor': {'cnt': n_cnt, 'q': n_q, 'cc': n_cc, 'amb': amb, 'data': nor_bytes,
                    'raw_rel': n_raw,
                    'raw_rel_pos': (n_raw - p_raw) if (n_raw and p_cc == 6) else None},
            'dss': dss,
        })
    out['gpl'] = {'n': n, 'subs': subs,
                  'ud': blk[g + ud_ptr: g + ud_ptr + ud_len] if ud_ptr else b'',
                  'ud_len': ud_len}

    # ---- ACT / TEX (raw) ----
    out['act'] = blk[hdr['act']: section_end(hdr['act'])] if hdr['act'] else b''
    out['tex'] = blk[hdr['tex']: section_end(hdr['tex'])] if hdr['tex'] else b''

    # ---- SKN ----
    out['skn'] = None
    if hdr['skn']:
        s = hdr['skn']
        skn = SknSection(model, s)
        n1, n2, na = skn.sk1_count, skn.sk2_count, skn.acc_count
        q = skn.quantize_info
        stride = comp_size(q) * 6
        sk1p, sk2p, accp = skn.sk1_ptr, skn.sk2_ptr, skn.acc_ptr
        mcp, mcs = skn.mem_clear_ptr, skn.mem_clear_size
        fip, fis = skn.flush_ptr, skn.flush_count

        sk1s, sk2s, accs = [], [], []
        for i in range(n1):
            entry = skn.sk1(sk1p, i)
            b0 = entry.offset
            src, gva = entry.source_ptr, entry.vertex_array_ptr
            bi, cnt, vo = entry.bone_id, entry.vertex_count, entry.vertex_offset
            sk1s.append({'bi': bi, 'cnt': cnt, 'vo': vo, 'gva': gva,
                         'src': blk[s + src: s + src + vo + cnt * stride],
                         'src_block_off': s + src,
                         'mtx_zero': blk[b0:b0 + entry.MATRIX_SIZE] == bytes(entry.MATRIX_SIZE)})
        for i in range(n2):
            entry = skn.sk2(sk2p, i)
            b0 = entry.offset
            src, wt, gva = entry.source_ptr, entry.weight_ptr, entry.vertex_array_ptr
            bi1, bi2 = entry.bone_id_1, entry.bone_id_2
            cnt, vo = entry.vertex_count, entry.vertex_offset
            sk2s.append({'bi1': bi1, 'bi2': bi2, 'cnt': cnt, 'vo': vo, 'gva': gva,
                         'src': blk[s + src: s + src + vo + cnt * stride],
                         'wt': blk[s + wt: s + wt + cnt * 2],
                         'src_block_off': s + src, 'wt_block_off': s + wt,
                         'mtx_zero': blk[b0:b0 + entry.MATRIX_SIZE] == bytes(entry.MATRIX_SIZE)})
        for i in range(na):
            entry = skn.acc(accp, i)
            b0 = entry.offset
            src, dst, gda, wt = (entry.source_ptr, entry.destination_index_ptr,
                                 entry.destination_array_ptr, entry.weight_ptr)
            bi, cnt = entry.bone_id, entry.vertex_count
            accs.append({'bi': bi, 'cnt': cnt, 'gda': gda,
                         'src': blk[s + src: s + src + cnt * stride],
                         'dst': blk[s + dst: s + dst + cnt * 2],
                         'wt': blk[s + wt: s + wt + cnt],
                         'src_block_off': s + src, 'dst_block_off': s + dst,
                         'wt_block_off': s + wt,
                         'mtx_zero': blk[b0:b0 + entry.MATRIX_SIZE] == bytes(entry.MATRIX_SIZE)})
        out['skn'] = {
            'n1': n1, 'n2': n2, 'na': na, 'q': q,
            'mcp': mcp, 'mcs': mcs, 'fis': fis,
            'flush': blk[s + fip: s + fip + fis * 2] if fip else b'',
            'flush_block_off': (s + fip) if fip else 0,
            'sk1s': sk1s, 'sk2s': sk2s, 'accs': accs,
        }

    # ---- trailing sections ----
    trail = {}
    for key in ('p6', 'p7', 'p8'):
        p = hdr[key]
        trail[key] = blk[p: section_end(p)] if p else b''
    out['trail'] = trail
    return out


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def compare(o: dict, r: dict) -> tuple[int, int]:
    """Compare two parsed blocks. Returns ``(checks passed, checks failed)`` of this comparison; the notes
    are in ``_notes``."""
    global _pass, _fail
    _pass = _fail = 0
    _notes.clear()
    _compare(o, r)
    return _pass, _fail


def _compare(o: dict, r: dict) -> None:
    _slogger.info('--- Header ---', source="compare.blocks")
    for k in ('gpl', 'act', 'tex', 'skn', 'p6', 'p7', 'p8'):
        check(f'header {k} presence', bool(o['hdr'][k]) == bool(r['hdr'][k]),
              f"orig=0x{o['hdr'][k]:X} new=0x{r['hdr'][k]:X}")

    _slogger.info('--- GPL ---', source="compare.blocks")
    og, rg = o['gpl'], r['gpl']
    check('submesh count', og['n'] == rg['n'], f"{og['n']} vs {rg['n']}")
    check('GPL user data length', og['ud_len'] == rg['ud_len'])
    check('GPL user data bytes', og['ud'] == rg['ud'])
    for i, (a, b) in enumerate(zip(og['subs'], rg['subs'])):
        pre = f'sub{i}'
        check(f'{pre} layout pointer', a['layout_rel'] == b['layout_rel'])
        check(f'{pre} name pointer', a['name_ptr'] == b['name_ptr'])
        for key in ('pos', 'col', 'uv', 'nor', 'display'):
            check(f'{pre} {key} header pointer', a['headers'][key] == b['headers'][key])
        for key in ('primitive_ptr', 'state_ptr'):
            check(f'{pre} display {key}', a['display'][key] == b['display'][key])
        check(f'{pre} name', a['name'] == b['name'], f"{a['name']!r} vs {b['name']!r}")
        for fld in ('cnt', 'q', 'cc'):
            check(f'{pre} pos.{fld}', a['pos'][fld] == b['pos'][fld],
                  f"{a['pos'][fld]} vs {b['pos'][fld]}")
        check(f'{pre} pos raw pointer', a['pos']['raw_rel'] == b['pos']['raw_rel'])
        check(f'{pre} pos bytes', a['pos']['data'] == b['pos']['data'],
              f"len {len(a['pos']['data'])} vs {len(b['pos']['data'])}")
        for fld in ('cnt', 'q', 'cc'):
            check(f'{pre} col.{fld}', a['col'][fld] == b['col'][fld])
        check(f'{pre} col raw pointer', a['col']['raw_rel'] == b['col']['raw_rel'])
        check(f'{pre} col bytes', a['col']['data'] == b['col']['data'])
        check(f'{pre} uv channel count', len(a['uvs']) == len(b['uvs']))
        for j, (ua, ub) in enumerate(zip(a['uvs'], b['uvs'])):
            for fld in ('cnt', 'q', 'cc', 'pal'):
                check(f'{pre} uv{j}.{fld}', ua[fld] == ub[fld], f"{ua[fld]} vs {ub[fld]}")
            for fld in ('raw_rel', 'palette_ptr'):
                check(f'{pre} uv{j} {fld}', ua[fld] == ub[fld])
            check(f'{pre} uv{j} bytes', ua['data'] == ub['data'])
        for fld in ('cnt', 'q', 'cc', 'amb', 'raw_rel_pos'):
            check(f'{pre} nor.{fld}', a['nor'][fld] == b['nor'][fld],
                  f"{a['nor'][fld]} vs {b['nor'][fld]}")
        check(f'{pre} nor raw pointer', a['nor']['raw_rel'] == b['nor']['raw_rel'])
        check(f'{pre} nor bytes', a['nor']['data'] == b['nor']['data'])
        check(f'{pre} display state count', len(a['dss']) == len(b['dss']))
        for k, (da, db) in enumerate(zip(a['dss'], b['dss'])):
            check(f'{pre} ds{k} id', da['id'] == db['id'])
            check(f'{pre} ds{k} params', da['params'] == db['params'],
                  f"{da['params'].hex()} vs {db['params'].hex()}")
            check(f'{pre} ds{k} setting', da['setting'] == db['setting'],
                  f"0x{da['setting']:08X} vs 0x{db['setting']:08X}")
            check(f'{pre} ds{k} record pointer', da['record_rel'] == db['record_rel'])
            check(f'{pre} ds{k} primitive pointer', da['prim_rel'] == db['prim_rel'])
            check(f'{pre} ds{k} prim list bytes', da['pl'] == db['pl'],
                  f"len {len(da['pl'])} vs {len(db['pl'])}")

    _slogger.info('--- ACT ---', source="compare.blocks")
    check('ACT bytes identical', o['act'] == r['act'],
          f"len {len(o['act'])} vs {len(r['act'])}")

    _slogger.info('--- TEX ---', source="compare.blocks")
    check('TEX bytes identical', o['tex'] == r['tex'],
          f"len {len(o['tex'])} vs {len(r['tex'])}")

    _slogger.info('--- SKN ---', source="compare.blocks")
    os_, rs = o['skn'], r['skn']
    check('SKN presence', (os_ is None) == (rs is None))
    if os_ and rs:
        for fld in ('n1', 'n2', 'na', 'q', 'fis'):
            check(f'skn.{fld}', os_[fld] == rs[fld], f"{os_[fld]} vs {rs[fld]}")
        if os_['mcp'] == rs['mcp'] and os_['mcs'] == rs['mcs']:
            check('skn memClrPtr/Size', True)
        else:
            note(f"memClr differs: orig ptr=0x{os_['mcp']:X} size=0x{os_['mcs']:X}, "
                 f"rebuilt ptr=0x{rs['mcp']:X} size=0x{rs['mcs']:X} "
                 f"(recalculated — verify against formula variance)")
            check('skn memClrPtr/Size', False, 'see note')
        check('skn flush bytes', os_['flush'] == rs['flush'])
        for lst, keyfields in (('sk1s', ('bi', 'cnt', 'vo', 'gva')),
                               ('sk2s', ('bi1', 'bi2', 'cnt', 'vo', 'gva')),
                               ('accs', ('bi', 'cnt', 'gda'))):
            check(f'skn {lst} count', len(os_[lst]) == len(rs[lst]))
            for i, (a, b) in enumerate(zip(os_[lst], rs[lst])):
                for fld in keyfields:
                    check(f'skn {lst}[{i}].{fld}', a[fld] == b[fld],
                          f"{a[fld]} vs {b[fld]}")
                for fld in ('src', 'wt', 'dst'):
                    if fld in a:
                        check(f'skn {lst}[{i}].{fld} bytes', a[fld] == b[fld],
                              f"len {len(a[fld])} vs {len(b[fld])}")
                check(f'skn {lst}[{i}] matrix zeroed', b['mtx_zero'])

    _slogger.info('--- Trailing sections ---', source="compare.blocks")
    for key in ('p6', 'p7', 'p8'):
        check(f'trailing {key} bytes', o['trail'][key] == r['trail'][key],
              f"len {len(o['trail'][key])} vs {len(r['trail'][key])}")

    _slogger.info('--- Alignment invariants (rebuilt block, block-relative) ---', source="compare.blocks")
    for i, sub in enumerate(r['gpl']['subs']):
        if sub['pos']['cc'] == 6 and sub['pos']['block_off']:
            check(f'sub{i} skinned pos 32-aligned', sub['pos']['block_off'] % 32 == 0,
                  f"off=0x{sub['pos']['block_off']:X}")
        for k, ds in enumerate(sub['dss']):
            if ds['pl_block_off']:
                check(f'sub{i} ds{k} prim list 32-aligned', ds['pl_block_off'] % 32 == 0,
                      f"off=0x{ds['pl_block_off']:X}")
    if rs:
        for lst in ('sk1s', 'sk2s', 'accs'):
            for i, e in enumerate(rs[lst]):
                for fld in ('src_block_off', 'wt_block_off', 'dst_block_off'):
                    if fld in e:
                        check(f'skn {lst}[{i}].{fld} 32-aligned', e[fld] % 32 == 0,
                              f"off=0x{e[fld]:X}")
        if rs['flush_block_off']:
            check('skn flush 32-aligned', rs['flush_block_off'] % 32 == 0)

    _slogger.info('--- SK runtime scratch space (rebuilt block) ---', source="compare.blocks")
    # Runtime skinning writes vertex slots that can extend beyond the stored
    # position array of submesh 0 (scratch space).  Verify no other data
    # array lies inside the write window [pos_start, pos_start + write_end).
    if rs and r['gpl']['subs']:
        stride = comp_size(rs['q']) * 6
        ends = [rs['mcp'] + rs['mcs']] if rs['mcs'] else []
        for e in rs['sk1s'] + rs['sk2s']:
            ends.append(e['gva'] + e['vo'] + e['cnt'] * stride)
        for e in rs['accs']:
            if e['cnt']:
                mx = max(u16(e['dst'], k * 2) for k in range(e['cnt']))
                ends.append(e['gda'] + (mx + 1) * stride)
        if ends:
            sub0 = r['gpl']['subs'][0]
            w_start = sub0['pos']['block_off']
            w_end   = w_start + max(ends)
            ranges = []
            for i, sub in enumerate(r['gpl']['subs']):
                if sub['col']['block_off']:
                    ranges.append((f'sub{i} color', sub['col']['block_off'],
                                   sub['col']['block_off'] + len(sub['col']['data'])))
                for j, uv in enumerate(sub['uvs']):
                    if uv['block_off']:
                        ranges.append((f'sub{i} uv{j}', uv['block_off'],
                                       uv['block_off'] + len(uv['data'])))
                for k, ds in enumerate(sub['dss']):
                    if ds['pl_block_off']:
                        ranges.append((f'sub{i} ds{k} primlist', ds['pl_block_off'],
                                       ds['pl_block_off'] + len(ds['pl'])))
                if i > 0 and sub['pos']['block_off']:
                    ranges.append((f'sub{i} pos', sub['pos']['block_off'],
                                   sub['pos']['block_off'] + len(sub['pos']['data'])))
            for label, a, b in ranges:
                check(f'scratch window clear of {label}', b <= w_start or a >= w_end,
                      f'array 0x{a:X}..0x{b:X} overlaps SK write window '
                      f'0x{w_start:X}..0x{w_end:X}')


def main() -> None:
    if len(sys.argv) != 3:
        _slogger.info(__doc__, source="compare.blocks")
        sys.exit(1)
    with open(sys.argv[1], 'rb') as f:
        orig = f.read()
    with open(sys.argv[2], 'rb') as f:
        rebuilt = f.read()

    _slogger.info(f'original: {len(orig):,} bytes   rebuilt: {len(rebuilt):,} bytes '
          f'(delta {len(rebuilt) - len(orig):+,})', source="compare.blocks")

    o = parse_block(orig)
    r = parse_block(rebuilt)
    passed, failed = compare(o, r)

    _slogger.info(f'===== {passed} checks passed, {failed} failed =====', source="compare.blocks")
    for n in _notes:
        _slogger.info(f'NOTE: {n}', source="compare.blocks")
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
