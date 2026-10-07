"""``start.py --roster --config FILE``: plan a roster switch that keeps the slots' customisations (``migrate.py``).

Reads ``3_Output_Dat`` (the state with every slot's ``vanilla`` flags and
stat edits) and derives its config once, merges it with the preset
(``migrate.plan_switch``) and writes:

* ``3_Output_Dat/_gui/switch/roster.json`` + ``icons/``: the merged config,
  with the derived portraits and the preset's own (copied under
  ``migrate.PRESET_ICON_PREFIX``);
* ``3_Output_Dat/_gui/switch/plan.json``: the commands and the per-slot
  lists (carried, reset, dropped).

``start.py`` then runs the commands in order (not with ``--dry-run``),
stopping at the first failure. Writes nothing to the game files. Exit code 1
when the preset is refused (no plan written).
"""

import argparse
import json
import os
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.normpath(os.path.join(_HERE, '..'))
for _path in (_TOOLS_DIR, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import slogger  # noqa: E402

try:
    from . import derive, icons, migrate, runner, slot_cli, state, state_cli
except ImportError:
    import derive
    import icons
    import migrate
    import runner
    import slot_cli
    import state
    import state_cli

SOURCE = 'roster.switch'
SWITCH_DIR = 'switch'
PLAN_FILE = 'plan.json'


def switch_dir(output_dir: str = state_cli.OUTPUT_DIR) -> str:
    return os.path.join(output_dir, state_cli.GUI_DIR, SWITCH_DIR)


def plan_path(output_dir: str = state_cli.OUTPUT_DIR) -> str:
    return os.path.join(switch_dir(output_dir), PLAN_FILE)


def plan(config_path: str, output_dir: str = state_cli.OUTPUT_DIR, icon_dir: str = icons.ICON_DIR) -> migrate.SwitchPlan:
    """Plan the switch to the preset at ``config_path``; writes the merged config and the plan."""
    folder = switch_dir(output_dir)
    os.makedirs(folder, exist_ok=True)
    if os.path.exists(plan_path(output_dir)):
        os.remove(plan_path(output_dir))            # a failed planner leaves no stale chain behind
    preset, _source = runner.load_config(config_path)
    image, dat = state_cli._open(output_dir)
    st = state.read_state(image, dat)
    state_cli.add_vanilla_flags(st)
    inputs = state_cli.input_dir(output_dir)
    st['stat_edits'] = state_cli.read_stat_edits(image, inputs)
    derived = derive.derive(image, dat)
    state_file = os.path.join(folder, derive.CONFIG_FILE)
    result = migrate.plan_switch(preset, st, derived, slot_cli.FileEnv(image, dat, inputs),
                                 state_file, icon_dir)
    missing = [p for p in result.preset_icons.values() if not os.path.isfile(p)]
    if missing:
        raise migrate.SwitchError(f'portrait {missing[0]} not found (the preset names it)')
    derive.write(derive.Derived(result.config, dict(derived.portraits)), folder)
    for name, source in result.preset_icons.items():
        shutil.copyfile(source, os.path.join(folder, derive.ICON_DIR, name))
        blocks = icons.kept_blocks_path(source)
        if os.path.isfile(blocks):
            shutil.copyfile(blocks, icons.kept_blocks_path(os.path.join(folder, derive.ICON_DIR, name)))
    out = result.to_json()
    out['preset'] = os.path.abspath(config_path)
    out['names'] = {f'0x{c["id"]:02X}': state.display_name(c) for c in st['characters']}
    out['warnings'] = list(derived.warnings) + list(st.get('warnings') or [])
    tmp = plan_path(output_dir) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, plan_path(output_dir))
    return result


def _lines(title: str, items: dict, names: dict) -> list[str]:
    if not items:
        return []
    out = [title]
    for cid in sorted(items):
        who = names.get(f'0x{cid:02X}') or f'0x{cid:02X}'
        out.append(f'  {who}' + (f': {migrate.labels(items[cid])}' if items[cid] else ''))
    return out


def main(argv=None) -> int:
    slogger.configure()
    parser = argparse.ArgumentParser(description='Plan a roster switch that keeps the slots\' customisations.')
    parser.add_argument('--config', required=True, help='the roster configuration JSON')
    parser.add_argument('--output-dir', default=state_cli.OUTPUT_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        result = plan(args.config, args.output_dir)
    except (RuntimeError, ValueError, OSError) as exc:   # SwitchError, StateError, DeriveError, RosterDevError, ...
        slogger.error(f'refused, nothing written: {exc}', source=SOURCE)
        return 1
    with open(plan_path(args.output_dir), encoding='utf-8') as f:
        written = json.load(f)
    names = written['names']
    for line in (_lines('kept (on both grids):', result.carried, names)
                 + _lines('reset (leaving the grid):', result.reset, names)
                 + _lines('dropped (new IDs leaving the roster):',
                          {c: f for c, f in result.dropped.items() if f}, names)):
        slogger.info(line, source=SOURCE)
    for note in result.notes:
        slogger.info(note, source=SOURCE)
    for warning in written['warnings']:
        slogger.warning(warning, source=SOURCE)
    slogger.info(f'chain: {len(result.commands)} commands', source=SOURCE)
    for command in result.commands:
        slogger.info('  start.py ' + ' '.join(f'"{a}"' if ' ' in a else a for a in command), source=SOURCE)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
