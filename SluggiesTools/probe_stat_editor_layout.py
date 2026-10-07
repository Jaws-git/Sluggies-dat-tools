"""Stat editor field layout vs the vanilla DOL (manual probe).

Pins the byte layout the Sluggers Stat Editor (Philenarion) assumes for every
table it edits, by comparing its hard-coded vanilla values (``default*`` lists
in ``editor.py``) with the bytes at the addresses its Gecko generator writes
(``geckoGenerate``/``getStatOffset``) in ``1_Input/main.dol``. It also checks
that the editor's per-ID table addresses are our site inventory's tables, and
that ``StatEditor/fields.py`` (its copied lists and every field's place) says
the same as the editor.

The editor is not imported (it opens a Tk window at import); its literal lists,
the base-address constants of ``geckoGenerate`` and the arithmetic of
``getStatOffset`` are read with ``ast``.

This is a **probe, not a unit test**: it reads ``1_Input/main.dol`` and a
stat editor checkout, both outside the repository.

    uv run --project SluggiesTools/_build python SluggiesTools/probe_stat_editor_layout.py [EDITOR_PY]

``EDITOR_PY`` defaults to ``Philenarion Stat Editor/Sluggers-Stat-Editor/editor.py``.
Exits 0 when every field matches, 1 otherwise.
"""
from __future__ import annotations

import ast
import json
import pathlib
import struct
import sys

TOOLS_DIR = pathlib.Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from Dol import dolfile  # noqa: E402
from StatEditor import fields  # noqa: E402

DEFAULT_EDITOR = ROOT / 'Philenarion Stat Editor' / 'Sluggers-Stat-Editor' / 'editor.py'
INPUT_DOL = ROOT / '1_Input' / 'main.dol'
INVENTORY = TOOLS_DIR / 'Dol' / 'site_inventory.json'

RAM = 0x80000000
STAT_U16 = {10, 11, 12, 13, 14, 15, 16, 17, 22, 23, 24, 25}   # geckoGenerate's two-byte stats
FLOAT_TOLERANCE = 5e-4                                         # editor defaults are rounded decimals


def editor_source(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding='utf-8'))


def literal_lists(tree: ast.Module) -> dict:
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return out


def gecko_bases(tree: ast.Module) -> dict[str, int]:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == 'geckoGenerate':
            return {n.targets[0].id: n.value.value for n in node.body
                    if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                    and n.targets[0].id.startswith('base') and isinstance(n.value, ast.Constant)}
    raise SystemExit('geckoGenerate not found in editor.py')


def stat_offset_fn(tree: ast.Module):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == 'getStatOffset':
            namespace: dict = {}
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'editor.py', 'exec'), namespace)
            return namespace['getStatOffset']
    raise SystemExit('getStatOffset not found in editor.py')


def handicap_addresses(tree: ast.Module) -> list[list[int]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'handicapCode':
            for n in node.body:
                if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'addresses':
                    return [[int(a, 16) + RAM for a in row] for row in ast.literal_eval(n.value)]
    raise SystemExit('handicapCode addresses not found in editor.py')


class Checker:
    def __init__(self, image: dolfile.DolImage):
        self.image = image
        self.fields = 0
        self.problems: list[str] = []

    def _bad(self, what: str, address: int, want, got) -> None:
        self.problems.append(f'{what} @0x{address:08X}: editor {want!r}, DOL {got!r}')

    def byte(self, what, address, want):
        self.fields += 1
        got = self.image.read(address, 1)[0]
        if got != want:
            self._bad(what, address, want, got)

    def u16(self, what, address, want, signed=False):
        self.fields += 1
        got = struct.unpack('>h' if signed else '>H', self.image.read(address, 2))[0]
        if got != want:
            self._bad(what, address, want, got)

    def u32(self, what, address, want):
        self.fields += 1
        got = self.image.u32(address)
        if got != want:
            self._bad(what, address, want, got)

    def f32(self, what, address, want):
        self.fields += 1
        got = struct.unpack('>f', self.image.read(address, 4))[0]
        if abs(got - want) > FLOAT_TOLERANCE * max(1.0, abs(want)):
            self._bad(what, address, want, round(got, 6))


def check_field_table(image: dolfile.DolImage, c: Checker, lists: dict, bases: dict, tree: ast.Module) -> None:
    """``StatEditor/fields.py`` against the editor: its copied lists, and every field's value at the place the
    field table names equals the editor's default."""
    for name, ours in fields.EDITOR_LISTS.items():
        if tuple(lists.get(name, ())) != ours:
            c.problems.append(f'fields.EDITOR_LISTS[{name!r}] differs from the editor list')
    inventory = {t['name']: t for t in json.loads(INVENTORY.read_text())['tables']}
    defaults = {'stats': lists['defaultStat'], 'pitching': lists['defaultPitching'], 'size': lists['defaultSize']}

    def same(f, address, want):
        got = f.unpack(image.read(address, f.size))
        ok = abs(got - want) <= FLOAT_TOLERANCE * max(1.0, abs(want)) if f.kind == 'f32' else got == want
        c.fields += 1
        if not ok:
            c.problems.append(f'fields {getattr(f, "name", "")} @0x{address:08X}: editor {want!r}, DOL {got!r}')

    for i in range(101):
        for f in fields.CHARACTER_FIELDS:
            t = inventory[f.table]
            same(f, int(t['address'], 16) + t['header'] + t['row_size'] * i + f.offset, defaults[f.group][i][f.index])
        stats = inventory['stats']
        for k in range(101):
            same(fields.CHEMISTRY, int(stats['address'], 16) + stats['header'] + stats['row_size'] * i
                 + fields.chemistry_offset(k), lists['defaultChem'][i][k])
    speed, traj, stars = lists['defaultSpeed'], lists['defaultTraj'], lists['defaultStarsTeam']
    boost, handicap, hparams = lists['defaultStarBoost'], lists['defaultStarHandicap'], lists['defaultHandicapParams']
    for f in fields.GLOBAL_FIELDS:
        t = fields.GLOBALS_BY_KEY[f.table]
        r, col = t.rows.index(f.row), t.columns.index(f.column)
        want = {'speed': lambda: speed[r][col], 'traj_heights': lambda: traj[r][col],
                'team_stars': lambda: stars[r][col], 'star_handicap': lambda: handicap[r][col],
                'star_boost': lambda: boost[r][col], 'handicap_params': lambda: hparams[r][col]}[f.table]()
        same(f, f.address, want)
    for name, base in (('baseSpeedBase', fields.SPEED_BASERUNNING), ('baseSpeedField', fields.SPEED_FIELDING),
                       ('baseTrajHeight', fields.TRAJ_HEIGHTS), ('baseTeamStars', fields.TEAM_STARS),
                       ('baseHandicap', fields.STAR_HANDICAP), ('baseStarBoost', fields.STAR_BOOSTS)):
        if bases[name] != base:
            c.problems.append(f'fields: {name} 0x{base:08X}, editor 0x{bases[name]:08X}')
    if [list(r) for r in fields.HANDICAP_PARAMS] != handicap_addresses(tree):
        c.problems.append('fields.HANDICAP_PARAMS differs from the editor handicapCode addresses')


def run(editor_py: pathlib.Path) -> int:
    tree = editor_source(editor_py)
    lists = literal_lists(tree)
    bases = {k: v + RAM for k, v in gecko_bases(tree).items()}
    stat_offset = stat_offset_fn(tree)
    image = dolfile.DolImage(INPUT_DOL.read_bytes())
    c = Checker(image)

    stat, chem = lists['defaultStat'], lists['defaultChem']
    pitching, size, speed = lists['defaultPitching'], lists['defaultSize'], lists['defaultSpeed']
    traj, stars = lists['defaultTraj'], lists['defaultStarsTeam']
    boost, handicap, hparams = lists['defaultStarBoost'], lists['defaultStarHandicap'], lists['defaultHandicapParams']
    names = lists['charList']
    shapes = {'charList': len(names), 'defaultStat': (len(stat), len(stat[0])), 'defaultChem': (len(chem), len(chem[0])),
              'defaultPitching': (len(pitching), len(pitching[0])), 'defaultSize': (len(size), len(size[0])),
              'defaultSpeed': len(speed), 'defaultTraj': (len(traj), len(traj[0])),
              'defaultStarsTeam': (len(stars), len(stars[0])), 'defaultStarBoost': len(boost)}
    print('editor shapes:', shapes)

    # Per-ID tables.
    row0 = bases['baseAddress'] + 1                      # editor base is one byte before row 0
    for i in range(101):
        row = row0 + 142 * i
        who = f'0x{i:02X} {names[i]}'
        c.u16(f'{who} stats row ID', row, i)
        for j in range(26):
            at = bases['baseAddress'] + 142 * i + stat_offset(j)
            if j in STAT_U16:
                c.u16(f'{who} stat[{j}]', at, stat[i][j])
            else:
                c.byte(f'{who} stat[{j}]', at, stat[i][j])
        for k in range(101):
            c.byte(f'{who} chem->0x{k:02X}', bases['baseAddress'] + 142 * i + k + 41, chem[i][k])
        c.byte(f'{who} star pitch type', bases['baseStarPitchType'] + i, stat[i][29])
        c.u16(f'{who} stamina', bases['baseStamina'] + 2 * i, stat[i][28])
        c.byte(f'{who} traj', bases['baseTraj'] + 2 * i, stat[i][26])
        c.byte(f'{who} hit curve', bases['baseTraj'] + 2 * i + 1, stat[i][27])
        for j in range(3):
            c.f32(f'{who} windup[{j}]', bases['basePitchingWindup'] + 12 * i + 4 * j, pitching[i][j])
        for j in range(2):
            c.f32(f'{who} changeup[{j}]', bases['baseChangeUp'] + 8 * i + 4 * j, pitching[i][3 + j])
        for j in range(10):
            c.f32(f'{who} catch[{j}]', bases['baseCatchRange'] + 40 * i + 4 * j, size[i][2 + j])
        for j in range(2):
            c.f32(f'{who} hitbox[{j}]', bases['baseHitbox'] + 8 * i + 4 * j, size[i][12 + j])
            c.f32(f'{who} sizescale[{j}]', bases['baseSizeScale'] + 8 * i + 4 * j, size[i][j])

    # Global tables.
    for i in range(43):
        c.f32(f'speed field[{i}]', bases['baseSpeedField'] + 4 * i, speed[i][1])
        c.f32(f'speed base[{i}]', bases['baseSpeedBase'] + 4 * i, speed[i][0])
    for i in range(24):
        for j in range(25):
            c.byte(f'traj height[{i}][{j}]', bases['baseTrajHeight'] + 25 * i + j, traj[i][j])
    for i in range(12):
        for j in range(41):
            c.u16(f'team stars[{i}][{j}]', bases['baseTeamStars'] + 82 * i + 2 * j, stars[i][j], signed=True)
    for i in range(4):
        for j in range(2):
            c.f32(f'star handicap[{i}][{j}]', bases['baseHandicap'] + 8 * i + 4 * j, handicap[i][j])
    for i in range(16):
        # Field 0 (add/mult) is a u32 enum in the DOL; geckoGenerate writes it as a float (editor bug).
        c.u32(f'star boost[{i}][0]', bases['baseStarBoost'] + 12 * i, boost[i][0])
        c.f32(f'star boost[{i}][1]', bases['baseStarBoost'] + 12 * i + 4, boost[i][1])
        for j in range(2, 4):
            c.u16(f'star boost[{i}][{j}]', bases['baseStarBoost'] + 12 * i + 2 * (j + 2), boost[i][j], signed=True)
    for i, row in enumerate(handicap_addresses(tree)):
        for j, at in enumerate(row):
            c.byte(f'handicap param[{i}][{j}]', at, hparams[i][j])
            word = image.u32(at - 3)
            print(f'  handicap param[{i}][{j}] 0x{at:08X}: instruction 0x{word:08X} (opcode {word >> 26})')

    # Editor table addresses vs our inventory.
    inventory = {t['name']: t for t in json.loads(INVENTORY.read_text())['tables']}
    expected = {'stats': row0 - 8, 'pitchwindup': bases['basePitchingWindup'], 'starpitch': bases['baseStarPitchType'],
                'stamina': bases['baseStamina'], 'changeup': bases['baseChangeUp'], 'traj': bases['baseTraj'],
                'catchrange': bases['baseCatchRange'], 'hitbox': bases['baseHitbox'], 'sizescale': bases['baseSizeScale']}
    for name, address in expected.items():
        inv = inventory[name]
        if int(inv['address'], 16) != address or inv['rows'] != 101:
            c.problems.append(f'inventory {name}: 0x{int(inv["address"], 16):08X} x{inv["rows"]}, editor 0x{address:08X}')
        print(f'  {name:12s} editor 0x{address:08X} inventory {inv["address"]} row {inv["row_size"]} {inv["status"]}')
    globals_ = {'speed field': (bases['baseSpeedField'], 43 * 4), 'speed base': (bases['baseSpeedBase'], 43 * 4),
                'traj heights': (bases['baseTrajHeight'], 24 * 25), 'team stars': (bases['baseTeamStars'], 12 * 82),
                'star handicap': (bases['baseHandicap'], 32), 'star boost': (bases['baseStarBoost'], 16 * 12)}
    for gname, (start, length) in globals_.items():
        for inv in inventory.values():
            lo = int(inv['address'], 16)
            hi = lo + inv['header'] + inv['row_size'] * inv['rows']
            if lo < start + length and start < hi:
                c.problems.append(f'global {gname} 0x{start:08X}+0x{length:X} overlaps inventory table {inv["name"]}')
        print(f'  global {gname:13s} 0x{start:08X}+0x{length:X} (file 0x{image.offset_of(start, length):X})')

    check_field_table(image, c, lists, bases, tree)

    print(f'{c.fields} fields checked, {len(c.problems)} mismatches')
    for line in c.problems[:60]:
        print('  ' + line)
    return 1 if c.problems else 0


if __name__ == '__main__':
    raise SystemExit(run(pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EDITOR))
