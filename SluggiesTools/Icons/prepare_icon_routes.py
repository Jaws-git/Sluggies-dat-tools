import argparse
import json
import os
import shutil
import struct
from datetime import datetime, timezone


ICONS_DIR = os.path.dirname(__file__)
TOOLS_DIR = os.path.normpath(os.path.join(ICONS_DIR, '..'))
ROOT_DIR = os.path.normpath(os.path.join(TOOLS_DIR, '..'))

INPUT_DOL = os.path.join(ROOT_DIR, '1_Input', 'main.dol')
INPUT_DAT = os.path.join(ROOT_DIR, '1_Input', 'dt_na.dat')
OUT_DIR = os.path.join(ROOT_DIR, '3_Output_Dat')
OUT_DOL = os.path.join(OUT_DIR, 'main.dol')
OUT_DAT = os.path.join(OUT_DIR, 'dt_na.dat')

ICON_META_DIR = os.path.join(ROOT_DIR, '2_Output_Models', '_ICONS', 'metadata')
DEFAULT_RULES_PATH = os.path.join(ICON_META_DIR, 'icon_reroute_rules.json')
DEFAULT_REPORT_PATH = os.path.join(ICON_META_DIR, 'icon_reroute_report.json')

# From docs: color-wheel table file offset in main.dol
CW_TABLE_FILE_OFFSET = 0x0062D650
CW_STRIDE = 8
CW_COUNT = 101  # char ids 0x00..0x64

# Verified CSS icon resolver internals (disassembly: Debug/icon_route_probe*.py, 2026-07-11).
#
# Resolver flow (function containing documented breakpoint 0x80395DB0):
#   0x80395E10  cmpwi r24, 0     ; char_id < 0     -> invalid path
#   0x80395E18  cmpwi r24, 0x4D  ; char_id >= 0x4D -> team-NPC path (0x80395EA8)
#   valid path: key = char_id << 16, row index resolved via runtime table 0x8071FF78
#   team-NPC path (chars 0x4D..0x64), 0x80395EB8:
#   0x80395EBC  li r0, 151       ; HARDCODED resource row 151 = Pink Yoshi fallback
#
# Row consumption (documented "row hook" 0x8051952C, verified):
#   idx = extsh(runtime_table[key_slot]); idx < 0 -> obj+0xB8 fallback idx
#   row = row_table_base + idx*20 ; page = *(u16*)row
#
# The old "candidate table" patches (0x8062478E / 0x8062479E / 0x80653440) are NOT
# part of this path (confirmed dead ends) and were removed.
_VERIFIED_CODE_PATCHES = {
    # Widen the resolver bounds check so chars 0x4D..0x64 enter the keyed route
    # path (key = char_id << 16) instead of the hardcoded team-NPC fallback.
    # IN-GAME RESULT (2026-07-11): still Pink Yoshi — the keyed path fails its
    # bank-side key registration for unregistered ids (idx = -1 -> obj+0xB8
    # fallback). Kept only as an experiment knob; superseded by
    # per_char_row_from_colorwheel below.
    'widen_resolver_char_bounds': {
        'vaddr': 0x80395E18,
        'old_word': 0x2C18004D,   # cmpwi r24, 0x4D
        'new_word': 0x2C180065,   # cmpwi r24, 0x65 (101)
        'desc': 'resolver bounds check 0x4D -> 0x65 (let unused char ids reach keyed icon route)',
    },
}

# Preferred method: rewrite the team-NPC block (0x80395EA8..0x80395EC0) so the
# runtime row index comes from the color-wheel icon_slot byte per character
# instead of the hardcoded 151 (Pink Yoshi):
#     lis   r4, 0x8063          ; color-wheel base high half
#     rlwinm r0, r24, 3,0,28    ; char_id * 8
#     add   r4, r4, r0
#     lbz   r0, 0x1557(r4)      ; = *(0x80631550 + char_id*8 + 7) = icon_slot
#     lis   r31, 0x8072
#     sth   r0, -136(r31)       ; runtime row-slot table (0x8071FF78)
#     nop
# Stock code from 0x80395EC4 (mr r3,r30 ... bl 0x8051892C) is kept unchanged.
# The two range guards it replaces are dead weight for normal play (ids passed
# here are 0..0x64); out-of-range ids now draw a garbage cell instead of
# routing to the invalid-id path — documented tradeoff.
_PER_CHAR_ROW_PATCH = {
    'vaddr': 0x80395EA8,
    'old_words': [0x2C18004D, 0x41800050, 0x2C180064, 0x41810048, 0x3FE08072, 0x38000097, 0xB01FFF78],
    'new_words': [0x3C808063, 0x57001838, 0x7C840214, 0x88041557, 0x3FE08072, 0xB01FFF78, 0x60000000],
    'desc': 'team-NPC icon block: runtime row index = color-wheel icon_slot byte (per char) instead of hardcoded 151',
}

# Chars routed through the team-NPC block whose icon_slot byte becomes the row
# index. Seeded to 151 (stock Pink Yoshi row) unless a rule overrides.
_TEAM_NPC_CHAR_RANGE = range(0x4D, 0x65)
_PINK_YOSHI_ROW_INDEX = 151

# Optional: change the shared hardcoded fallback row index (li r0, 151) used by
# ALL team-NPC chars 0x4D..0x64. Not per-char, but a cheap in-game probe that
# verifies we control the correct instruction.
_TEAM_NPC_ROW_VADDR = 0x80395EBC
_TEAM_NPC_ROW_OLD_WORD = 0x38000097  # li r0, 151


class IconRoutePrepError(Exception):
    pass


def _ensure_parent(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)


def _char_row_offset(char_id):
    if not isinstance(char_id, int):
        raise IconRoutePrepError(f'char_id must be int, got {type(char_id).__name__}')
    if char_id < 0 or char_id >= CW_COUNT:
        raise IconRoutePrepError(f'char_id out of range: 0x{char_id:02X} (valid 0x00..0x64)')
    return CW_TABLE_FILE_OFFSET + char_id * CW_STRIDE


def _build_default_rules():
    # Color-wheel seeding + verified resolver code patches.
    # With per_char_row_from_colorwheel enabled, set_icon_slot for chars
    # 0x4D..0x64 IS the runtime resource-row index (0..151 stock rows).
    return {
        'version': 3,
        'description': 'Icon route prepatch. For chars 0x4D-0x64, set_icon_slot = resource row index (per_char_row_from_colorwheel).',
        'per_char_row_from_colorwheel': True,
        'widen_resolver_char_bounds': False,
        'team_npc_shared_row_index': None,
        'rules': [
            {'char_id': 0x47, 'set_icon_valid': 1, 'set_icon_slot': 5, 'notes': 'unused Yoshi A (keyed path, slot semantics stock)'},
            {'char_id': 0x48, 'set_icon_valid': 1, 'set_icon_slot': 9, 'notes': 'unused Yoshi B (keyed path, slot semantics stock)'},
            {'char_id': 0x59, 'set_icon_valid': 1, 'set_icon_slot': 10, 'notes': 'unused 0x0D #0 -> test row 10'},
            {'char_id': 0x5A, 'set_icon_valid': 1, 'set_icon_slot': 20, 'notes': 'unused 0x0D #1 -> test row 20'},
            {'char_id': 0x5B, 'set_icon_valid': 1, 'set_icon_slot': 30, 'notes': 'unused 0x0D #2 -> test row 30'},
            {'char_id': 0x5C, 'set_icon_valid': 1, 'set_icon_slot': 40, 'notes': 'unused 0x0D #3 -> test row 40'},
            {'char_id': 0x5D, 'set_icon_valid': 1, 'set_icon_slot': 50, 'notes': 'unused 0x0D #4 -> test row 50'},
            {'char_id': 0x5E, 'set_icon_valid': 1, 'set_icon_slot': 60, 'notes': 'unused 0x0D #5 -> test row 60'},
        ],
    }


def _ensure_rules_file(rules_path):
    if os.path.exists(rules_path):
        return
    _ensure_parent(rules_path)
    with open(rules_path, 'w', encoding='utf-8') as f:
        json.dump(_build_default_rules(), f, indent=2)


def _load_rules(rules_path):
    _ensure_rules_file(rules_path)
    with open(rules_path, 'r', encoding='utf-8') as f:
        payload = json.load(f)
    rules = payload.get('rules', [])
    if not isinstance(rules, list) or not rules:
        raise IconRoutePrepError(f'no rules found in {rules_path}')
    return payload, rules


def _copy_inputs_to_output(overwrite=True):
    if not os.path.exists(INPUT_DOL):
        raise IconRoutePrepError(f'missing input DOL: {INPUT_DOL}')
    if not os.path.exists(INPUT_DAT):
        raise IconRoutePrepError(f'missing input DAT: {INPUT_DAT}')

    os.makedirs(OUT_DIR, exist_ok=True)

    if overwrite or not os.path.exists(OUT_DOL):
        shutil.copyfile(INPUT_DOL, OUT_DOL)
    if overwrite or not os.path.exists(OUT_DAT):
        shutil.copyfile(INPUT_DAT, OUT_DAT)


def _u8(value, field_name):
    if not isinstance(value, int):
        raise IconRoutePrepError(f'{field_name} must be int')
    if value < 0 or value > 0xFF:
        raise IconRoutePrepError(f'{field_name} out of u8 range: {value}')
    return value


def _u16(value, field_name):
    if not isinstance(value, int):
        raise IconRoutePrepError(f'{field_name} must be int')
    if value < 0 or value > 0xFFFF:
        raise IconRoutePrepError(f'{field_name} out of u16 range: {value}')
    return value


def _read_u32be(blob, off):
    return struct.unpack('>I', blob[off:off + 4])[0]


def _build_dol_mapper(dol_bytes):
    section_offsets = [_read_u32be(dol_bytes, i * 4) for i in range(18)]
    section_addrs = [_read_u32be(dol_bytes, 0x48 + i * 4) for i in range(18)]
    section_sizes = [_read_u32be(dol_bytes, 0x90 + i * 4) for i in range(18)]

    def vaddr_to_file(vaddr):
        for i in range(18):
            off = section_offsets[i]
            size = section_sizes[i]
            base = section_addrs[i]
            if off and size and base <= vaddr < base + size:
                return off + (vaddr - base)
        raise IconRoutePrepError(f'virtual address not mapped in DOL: 0x{vaddr:08X}')

    return vaddr_to_file


def _patch_u8_at_vaddr(dol_bytes, vaddr_to_file, vaddr, value):
    value = _u8(value, 'u8 patch value')
    foff = vaddr_to_file(vaddr)
    old = dol_bytes[foff]
    dol_bytes[foff] = value
    return foff, old, value


def _patch_u16_at_vaddr(dol_bytes, vaddr_to_file, vaddr, value):
    value = _u16(value, 'u16 patch value')
    foff = vaddr_to_file(vaddr)
    old = struct.unpack('>H', dol_bytes[foff:foff + 2])[0]
    dol_bytes[foff:foff + 2] = struct.pack('>H', value)
    return foff, old, value


def _patch_verified_block(dol_bytes, vaddr_to_file, vaddr, old_words, new_words, desc):
    """Write a sequence of 32-bit words only if the region matches the stock
    words (or is already fully patched)."""
    foff = vaddr_to_file(vaddr)
    n = len(old_words)
    current = list(struct.unpack(f'>{n}I', dol_bytes[foff:foff + n * 4]))
    if current == list(new_words):
        status = 'already-patched'
    elif current == list(old_words):
        dol_bytes[foff:foff + n * 4] = struct.pack(f'>{n}I', *new_words)
        status = 'patched'
    else:
        raise IconRoutePrepError(
            f'verified block patch mismatch at 0x{vaddr:08X} ({desc}): '
            f'found {[hex(w) for w in current]}'
        )
    return {
        'desc': desc,
        'vaddr_hex': f'0x{vaddr:08X}',
        'file_offset_hex': f'0x{foff:X}',
        'word_count': n,
        'status': status,
    }


def _patch_verified_word(dol_bytes, vaddr_to_file, vaddr, old_word, new_word, desc):
    """Write a 32-bit word only if the current bytes match the expected stock word.

    Returns a report dict. Never writes on mismatch (already-patched is OK).
    """
    foff = vaddr_to_file(vaddr)
    current = struct.unpack('>I', dol_bytes[foff:foff + 4])[0]
    status = None
    if current == new_word:
        status = 'already-patched'
    elif current == old_word:
        dol_bytes[foff:foff + 4] = struct.pack('>I', new_word)
        status = 'patched'
    else:
        raise IconRoutePrepError(
            f'verified code patch mismatch at 0x{vaddr:08X} ({desc}): '
            f'expected 0x{old_word:08X} or 0x{new_word:08X}, found 0x{current:08X}'
        )
    return {
        'desc': desc,
        'vaddr_hex': f'0x{vaddr:08X}',
        'file_offset_hex': f'0x{foff:X}',
        'old_word_hex': f'0x{old_word:08X}',
        'new_word_hex': f'0x{new_word:08X}',
        'found_word_hex': f'0x{current:08X}',
        'status': status,
    }


def _apply_rules_to_dol(dol_path, payload, rules):
    with open(dol_path, 'rb') as f:
        dol_bytes = bytearray(f.read())

    vaddr_to_file = _build_dol_mapper(dol_bytes)

    report_rows = []
    code_patches = []
    warnings = []

    per_char_row = bool(payload.get('per_char_row_from_colorwheel'))
    if per_char_row:
        # Seed icon_slot (= runtime row index under this patch) to the stock
        # Pink Yoshi row for the whole team-NPC range, so non-target chars keep
        # their stock look. User rules below override the targets.
        for cid in _TEAM_NPC_CHAR_RANGE:
            off = _char_row_offset(cid)
            dol_bytes[off + 7] = _PINK_YOSHI_ROW_INDEX
        if payload.get('widen_resolver_char_bounds'):
            warnings.append(
                'widen_resolver_char_bounds ignored: per_char_row_from_colorwheel requires chars '
                '0x4D..0x64 to stay on the team-NPC path (stock bounds check).'
            )

    for idx, rule in enumerate(rules):
            if not isinstance(rule, dict):
                raise IconRoutePrepError(f'rule #{idx + 1} is not an object')

            if 'char_id' not in rule:
                raise IconRoutePrepError(f'rule #{idx + 1} missing char_id')

            char_id = rule['char_id']
            row_off = _char_row_offset(char_id)

            old_row = bytearray(dol_bytes[row_off:row_off + CW_STRIDE])
            if len(old_row) != CW_STRIDE:
                raise IconRoutePrepError(f'unable to read row for char 0x{char_id:02X}')

            new_row = bytearray(old_row)

            if 'copy_species_from_char_id' in rule:
                src_char = rule['copy_species_from_char_id']
                src_off = _char_row_offset(src_char)
                src_row = dol_bytes[src_off:src_off + CW_STRIDE]
                if len(src_row) != CW_STRIDE:
                    raise IconRoutePrepError(f'unable to read source row char 0x{src_char:02X}')
                # Only seed species/captain/model fields, keep variant/flags as-is.
                new_row[0] = src_row[0]
                new_row[1] = src_row[1]
                new_row[2] = src_row[2]

            if 'set_icon_valid' in rule:
                new_row[6] = _u8(rule['set_icon_valid'], 'set_icon_valid')

            if 'set_icon_slot' in rule:
                new_row[7] = _u8(rule['set_icon_slot'], 'set_icon_slot')

            if 'set_candidate_resolver_id' in rule or 'candidate_direct_writes' in rule:
                warnings.append(
                    f'rule #{idx + 1} (char 0x{char_id:02X}): candidate table patches are DEPRECATED '
                    f'(confirmed dead ends by disassembly 2026-07-11) and were skipped.'
                )

            dol_bytes[row_off:row_off + CW_STRIDE] = new_row

            report_rows.append({
                'rule_index': idx + 1,
                'char_id_dec': char_id,
                'char_id_hex': f'0x{char_id:02X}',
                'row_file_offset_hex': f'0x{row_off:X}',
                'old_row_hex': old_row.hex(),
                'new_row_hex': new_row.hex(),
                'old_icon_valid': old_row[6],
                'new_icon_valid': new_row[6],
                'old_icon_slot': old_row[7],
                'new_icon_slot': new_row[7],
                'notes': rule.get('notes', ''),
            })

    # ---- verified resolver code patches (top-level payload flags) ----
    if per_char_row:
        code_patches.append(_patch_verified_block(
            dol_bytes, vaddr_to_file,
            _PER_CHAR_ROW_PATCH['vaddr'],
            _PER_CHAR_ROW_PATCH['old_words'],
            _PER_CHAR_ROW_PATCH['new_words'],
            _PER_CHAR_ROW_PATCH['desc'],
        ))
    elif payload.get('widen_resolver_char_bounds'):
        spec = _VERIFIED_CODE_PATCHES['widen_resolver_char_bounds']
        code_patches.append(_patch_verified_word(
            dol_bytes, vaddr_to_file,
            spec['vaddr'], spec['old_word'], spec['new_word'], spec['desc'],
        ))

    row_idx = payload.get('team_npc_shared_row_index')
    if row_idx is not None:
        if per_char_row:
            warnings.append(
                'team_npc_shared_row_index ignored: per_char_row_from_colorwheel replaces the '
                'shared li r0,151 instruction with the per-char loader.'
            )
        else:
            row_idx = _u16(row_idx, 'team_npc_shared_row_index')
            if row_idx > 0x7FFF:
                raise IconRoutePrepError('team_npc_shared_row_index must be 0..0x7FFF (li immediate)')
            code_patches.append(_patch_verified_word(
                dol_bytes, vaddr_to_file,
                _TEAM_NPC_ROW_VADDR, _TEAM_NPC_ROW_OLD_WORD, 0x38000000 | row_idx,
                f'team-NPC shared fallback row index 151 -> {row_idx} (li r0 immediate)',
            ))

    with open(dol_path, 'wb') as f:
        f.write(dol_bytes)

    return report_rows, code_patches, warnings


def _write_report(report_path, rules_path, applied_rows, code_patches, warnings):
    _ensure_parent(report_path)
    payload = {
        'generated_utc': datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
        'inputs': {
            'input_dol': os.path.relpath(INPUT_DOL, ROOT_DIR).replace('\\', '/'),
            'input_dat': os.path.relpath(INPUT_DAT, ROOT_DIR).replace('\\', '/'),
            'rules_path': os.path.relpath(rules_path, ROOT_DIR).replace('\\', '/'),
        },
        'outputs': {
            'output_dol': os.path.relpath(OUT_DOL, ROOT_DIR).replace('\\', '/'),
            'output_dat': os.path.relpath(OUT_DAT, ROOT_DIR).replace('\\', '/'),
        },
        'patch_scope': 'Color-wheel table edits + verified resolver code patches (disassembly-confirmed).',
        'notes': [
            'Resolver internals decoded 2026-07-11 (Debug/icon_route_probe*.py):',
            'chars >= 0x4D were hard-excluded at 0x80395E18; team-NPC block forces resource row 151 (Pink Yoshi).',
            'widen_resolver_char_bounds lets chars 0x4D..0x64 reach the keyed icon route path.',
            'Remaining known gap: key->row-index registration data for the new ids (icon bank records).',
        ],
        'applied_rule_count': len(applied_rows),
        'applied_rows': applied_rows,
        'code_patch_count': len(code_patches),
        'code_patches': code_patches,
        'warnings': warnings,
    }
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description='Prepare icon reroute prepatch in 3_Output_Dat by editing main.dol color-wheel icon fields.'
    )
    parser.add_argument('--rules', default=DEFAULT_RULES_PATH, help='JSON rules file path')
    parser.add_argument('--report', default=DEFAULT_REPORT_PATH, help='JSON report output path')
    parser.add_argument('--no-overwrite-copy', action='store_true', help='do not recopy INPUT files if output files already exist')
    args = parser.parse_args()

    rules_path = os.path.normpath(args.rules)
    report_path = os.path.normpath(args.report)

    payload, rules = _load_rules(rules_path)
    _copy_inputs_to_output(overwrite=(not args.no_overwrite_copy))
    applied_rows, code_patches, warnings = _apply_rules_to_dol(OUT_DOL, payload, rules)
    _write_report(report_path, rules_path, applied_rows, code_patches, warnings)

    print('Icon route prepatch complete:')
    print(f'  Patched DOL: {os.path.relpath(OUT_DOL, ROOT_DIR)}')
    print(f'  DAT copy:    {os.path.relpath(OUT_DAT, ROOT_DIR)}')
    print(f'  Rules:       {os.path.relpath(rules_path, ROOT_DIR)}')
    print(f'  Report:      {os.path.relpath(report_path, ROOT_DIR)}')
    print(f'  Rows patched: {len(applied_rows)}')
    print(f'  Code patches: {len(code_patches)}')
    for cp in code_patches:
        print(f'    [{cp["status"]}] {cp["desc"]} @ {cp["vaddr_hex"]}')
    for w in warnings:
        print(f'  WARNING: {w}')


if __name__ == '__main__':
    try:
        main()
    except IconRoutePrepError as exc:
        print(f'ERROR: {exc}')
        raise SystemExit(1)
