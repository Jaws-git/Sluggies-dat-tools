"""Stat editor Bridge Mode vs our field table (manual probe).

Drives the stat editor's ``sluggies_bridge.py`` without its window on
the real ``3_Output_Dat/main.dol``:

1. writes the bridge (``cli.export``) into a temporary editor folder and
   opens it in Bridge Mode with the editor's own lists (read from
   ``editor.py`` with ``ast``, as ``probe_stat_editor_layout.py`` does);
2. every value the editor now shows (characters, chemistry in all three
   regions, global tables) must store back to the DOL's bytes through
   ``StatEditor/fields.py``, the defaults must be the bridge baseline, and
   nothing counts as edited yet;
3. changes values the way the editor's buttons do (a stock and a new
   character, u8/u16/f32, chemistry stock x stock / stock x new in one
   direction / new x new, a team-star value, a star-boost op), collects the
   edit file and checks every key against ``fields.py``;
4. writes those edits onto a copy of the DOL (``carry.apply``), reopens Bridge
   Mode on it: the editor must now show the edited values with nothing left
   to send, and ``carry.detect`` must find exactly the sent values;
5. the bridge-file deletion after a send removes ``stat_bridge.json`` and
   nothing else, and keeps a bridge that was rewritten since it was opened.

This is a **probe, not a unit test**: it reads ``1_Input``, ``3_Output_Dat`` and
a stat editor checkout, all outside the repository. Nothing in them is written.

    uv run --project SluggiesTools/_build python SluggiesTools/probe_stat_editor_bridge_mode.py [EDITOR_DIR]

``EDITOR_DIR`` defaults to ``Philenarion Stat Editor/Sluggers-Stat-Editor``.
Exits 0 when every check passes, 1 otherwise.
"""
from __future__ import annotations

import ast
import copy
import json
import pathlib
import struct
import sys
import tempfile

TOOLS_DIR = pathlib.Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent
for _path in (TOOLS_DIR, TOOLS_DIR / 'Roster'):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from Dol import dolfile  # noqa: E402
from StatEditor import bridge, carry, cli, fields  # noqa: E402

DEFAULT_EDITOR = ROOT / 'Philenarion Stat Editor' / 'Sluggers-Stat-Editor'
OUTPUT_DIR = ROOT / '3_Output_Dat'
INPUT_DOL = ROOT / '1_Input' / 'main.dol'
GLOBAL_LISTS = {'speed': 'Speed', 'traj_heights': 'Traj', 'team_stars': 'StarsTeam',
                'star_handicap': 'StarHandicap', 'star_boost': 'StarBoost', 'handicap_params': 'HandicapParams'}


def editor_namespace(editor_py: pathlib.Path) -> dict:
    """The editor's module-level literals (lists, defaults) plus the changed* copies it makes at start-up."""
    ns = {}
    for node in ast.parse(editor_py.read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                ns[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    for name in [n for n in ns if n.startswith('default')]:
        ns['changed' + name[len('default'):]] = copy.deepcopy(ns[name])
    return ns


class Checker:
    def __init__(self):
        self.problems: list[str] = []
        self.checked = 0

    def same(self, what: str, got, want) -> None:
        self.checked += 1
        if got != want:
            self.problems.append(f'{what}: {got!r} != {want!r}')


def stored(value_spec: fields.Value, value) -> bytes:
    return value_spec.pack(value)


def check_values(c: Checker, image: dolfile.DolImage, vanilla: dolfile.DolImage, ns: dict, session) -> None:
    roster = bridge.read_roster(image)
    layouts = bridge.table_layouts(image, roster)
    base = bridge.baseline_rows(vanilla, roster)
    ids_ = session.ids
    for i, cid in enumerate(ids_):
        for f in fields.CHARACTER_FIELDS:
            name = {'stats': 'Stat', 'pitching': 'Pitching', 'size': 'Size'}[f.group]
            raw = image.read(layouts[f.table].row_address(cid) + f.offset, f.size)
            c.same(f'0x{cid:02X} {f.group}.{f.name} (current)', stored(f, ns['changed' + name][i][f.index]), raw)
            want = base[f.table][cid][f.offset:f.offset + f.size]
            c.same(f'0x{cid:02X} {f.group}.{f.name} (default)', stored(f, ns['default' + name][i][f.index]), want)
    matrix = bridge.new_by_new_address(image, layouts['stats']) if roster.new else None
    base_matrix = bridge.new_by_new_baseline(vanilla, roster) if roster.new else None
    n_new = carry.NEW_X_NEW
    for i, a in enumerate(ids_):
        for j, b in enumerate(ids_):
            at = carry.chem_address(a, b, layouts, matrix)
            c.same(f'chemistry 0x{a:02X} x 0x{b:02X} (current)', ns['changedChem'][i][j], image.read(at, 1)[0])
            if a >= 0x65 and b >= 0x65:
                want = base_matrix[(a - 0x66) * n_new + b - 0x66]
            else:
                row, column = (b, a) if b >= 0x65 else (a, b)        # stock x new: the new ID's row
                want = base['stats'][row][fields.CHEM_BASE + column]
            c.same(f'chemistry 0x{a:02X} x 0x{b:02X} (default)', ns['defaultChem'][i][j], want)
    for g in fields.GLOBAL_FIELDS:
        name = GLOBAL_LISTS[g.table]
        t = fields.GLOBALS_BY_KEY[g.table]
        row, column = t.rows.index(g.row), t.columns.index(g.column)
        c.same(f'{g.table}[{g.row}][{g.column}]', stored(g, ns['changed' + name][row][column]),
               image.read(g.address, g.size))


def check_keys(c: Checker, edits: dict) -> None:
    for cid, groups in edits['characters'].items():
        for group, values in groups.items():
            for key, value in values.items():
                c.checked += 1
                try:
                    if group == 'chemistry':
                        int(key, 16)
                        problem = fields.CHEMISTRY.problem(value)
                    else:
                        problem = fields.character_field(group, key).problem(value)
                except (fields.FieldError, ValueError) as exc:
                    problem = str(exc)
                if problem:
                    c.problems.append(f'edit {cid} {group}.{key}: {problem}')
    for table, rows in edits['globals'].items():
        for row, columns in rows.items():
            for column, value in columns.items():
                c.checked += 1
                try:
                    problem = fields.global_field(table, row, column).problem(value)
                except fields.FieldError as exc:
                    problem = str(exc)
                if problem:
                    c.problems.append(f'edit {table}[{row}][{column}]: {problem}')


def to_stat_edits(edits: dict) -> carry.StatEdits:
    out = carry.StatEdits()
    for cid_text, groups in edits['characters'].items():
        cid = int(cid_text, 16)
        for group, values in groups.items():
            for key, value in values.items():
                if group == 'chemistry':
                    out.chemistry[(cid, int(key, 16))] = value
                else:
                    f = fields.character_field(group, key)
                    out.rows.setdefault(cid, {})[f] = f.pack(value)
    for table, rows in edits['globals'].items():
        for row, columns in rows.items():
            for column, value in columns.items():
                g = fields.global_field(table, row, column)
                out.globals[g] = g.pack(value)
    return out


def open_bridge(module, editor_py, folder: pathlib.Path, output_dir: pathlib.Path):
    path = folder / 'Bridge' / 'stat_bridge.json'
    cli.export(str(path), str(output_dir), str(INPUT_DOL.parent))
    ns = editor_namespace(editor_py)
    session = module.load(ns, str(folder / 'editor.py'))      # the Bridge folder is next to editor.py
    if session is None:
        raise SystemExit(f'Bridge Mode refused: {module._problem}')
    return ns, session


def edit_like_the_editor(ns: dict, session) -> dict:
    """Change values as the editor's buttons do; returns what the edit file must contain (by ID)."""
    index = session.index
    new = session.ids[101] if len(session.ids) > 101 else None
    new2 = session.ids[102] if len(session.ids) > 102 else None
    luigi = index[0x01]
    ns['changedStat'][luigi][19] = ns['changedStat'][luigi][19] % 10 + 1          # displayed batting (u8)
    ns['changedStat'][luigi][28] = 99                                           # stamina (u16, other table)
    ns['changedPitching'][luigi][3] = 0.65                                      # change-up speed mult (f32)
    simple = lambda a, b, chem, direction: (                                     # editor.simpleChemChange
        ns['changedChem'][a].__setitem__(b, chem),
        (direction == 2 or session.symmetric(a, b)) and ns['changedChem'][b].__setitem__(a, chem))
    simple(index[0x00], index[0x09], (ns['changedChem'][index[0x00]][index[0x09]] + 1) % 3, 1)
    ns['changedStarsTeam'][3][5] += 7                                           # Monkeys, solo HR (s16)
    ns['changedStarBoost'][2][0] = 3 - ns['changedStarBoost'][2][0]             # add <-> mult op (u32)
    if new is not None:
        ns['changedStat'][index[new]][17] = 123                                 # fielding (u16)
        ns['changedSize'][index[new]][0] = 1.25                                 # gameplay size (f32)
        simple(index[0x00], index[new], (ns['changedChem'][index[0x00]][index[new]] + 1) % 3, 1)   # one-sided
    if new2 is not None:
        simple(index[new], index[new2], (ns['changedChem'][index[new]][index[new2]] + 2) % 3, 1)
    return {'new': new, 'new2': new2}


def check_bridge_deletion(c: Checker, module, editor_py, folder: pathlib.Path) -> None:
    """Session._delete_bridge (what Send to Sluggies calls): only stat_bridge.json goes, and only unchanged."""
    bridge_dir = folder / 'Bridge'
    _ns, session = open_bridge(module, editor_py, folder, OUTPUT_DIR)
    bystanders = [bridge_dir / 'stat_edits.json', bridge_dir / 'other.json', folder / 'stat_bridge.json']
    for p in bystanders:
        p.write_text('{}', encoding='utf-8')
    (bridge_dir / 'stat_bridge.json').write_bytes((bridge_dir / 'stat_bridge.json').read_bytes() + b' ')
    session._delete_bridge()
    c.same('a rewritten bridge is kept', (bridge_dir / 'stat_bridge.json').is_file(), True)
    _ns, session = open_bridge(module, editor_py, folder, OUTPUT_DIR)
    session._delete_bridge()
    c.same('the opened bridge is deleted', (bridge_dir / 'stat_bridge.json').exists(), False)
    c.same('nothing else is deleted', [p.is_file() for p in bystanders], [True] * len(bystanders))
    session._delete_bridge()                               # already gone: no error, nothing else touched
    c.same('a second call deletes nothing', [p.is_file() for p in bystanders], [True] * len(bystanders))


def run(editor_dir: pathlib.Path) -> int:
    editor_py = editor_dir / 'editor.py'
    sys.path.insert(0, str(editor_dir))
    import sluggies_bridge as module

    c = Checker()
    image = dolfile.DolImage((OUTPUT_DIR / 'main.dol').read_bytes())
    vanilla = dolfile.DolImage(INPUT_DOL.read_bytes())
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        ns, session = open_bridge(module, editor_py, tmp / 'editor', OUTPUT_DIR)
        n_new = len(session.ids) - 101
        print(f'Bridge Mode: {len(session.ids)} characters ({n_new} new), {len(ns["comboList"])} dropdown entries')
        check_values(c, image, vanilla, ns, session)
        live = carry.detect(image, vanilla)
        _edits, count, problems = session.collect()
        c.same('values edited right after opening', count, 0)
        c.same('problems right after opening', problems, [])
        if live:
            print(f'note: the output already holds stat edits ({len(live.rows)} IDs, {len(live.chemistry)} '
                  f'chemistry, {len(live.globals)} globals); they show as current values, not as edits')

        picked = edit_like_the_editor(ns, session)
        edits, count, problems = session.collect()
        c.same('problems after editing', problems, [])
        check_keys(c, edits)
        print(f'edit file: {count} values: ' + json.dumps(edits['characters'])[:400])
        new = picked['new']
        if new is not None:
            chem = edits['characters'].get(f'0x{new:02X}', {}).get('chemistry', {})
            c.same('stock x new edited one-sided lands in the new row', '0x00' in chem, True)
            c.same('stock x new is not written from the stock row',
                   f'0x{new:02X}' in edits['characters'].get('0x00', {}).get('chemistry', {}), False)

        patched = dolfile.DolImage(image.to_bytes())
        carry.apply(patched, to_stat_edits(edits))
        out2 = tmp / 'output2'
        out2.mkdir()
        (out2 / 'main.dol').write_bytes(patched.to_bytes())
        ns2, session2 = open_bridge(module, editor_py, tmp / 'editor2', out2)
        _e2, count2, _p2 = session2.collect()
        c.same('values edited after reopening on the patched DOL', count2, 0)
        for name in ('Stat', 'Pitching', 'Size', 'Chem', 'StarsTeam', 'StarBoost'):
            c.same(f'reopened {name} = sent {name}', ns2['changed' + name], ns['changed' + name])
        detected = carry.detect(patched, vanilla)
        expected = carry.detect(image, vanilla)
        for cid, values in to_stat_edits(edits).rows.items():
            for f, raw in values.items():
                c.same(f'detect 0x{cid:02X} {f.group}.{f.name}', detected.rows.get(cid, {}).get(f), raw)
        c.same('detect: only the sent global values changed',
               {g.address for g in detected.globals} - {g.address for g in expected.globals},
               {g.address for g in to_stat_edits(edits).globals} - {g.address for g in expected.globals})

        check_bridge_deletion(c, module, editor_py, tmp / 'editor3')

    print(f'{c.checked} checks, {len(c.problems)} problems')
    for p in c.problems[:40]:
        print('  ' + p)
    return 1 if c.problems else 0


if __name__ == '__main__':
    raise SystemExit(run(pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EDITOR))
