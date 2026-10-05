"""``start.py --patch-slot`` / ``--clear-slot``: plan the apply chain (``slot_plan.py``) on the output files.

Reads ``3_Output_Dat``, derives its config, applies the one change and writes:

* ``3_Output_Dat/_gui/slot/roster.json`` + ``icons/``: the changed derived
  config (only when the change needs a roster rebuild);
* ``3_Output_Dat/_gui/slot/plan.json``: the chain's commands, notes and
  warnings, which ``start.py`` then runs in order.

Writes nothing to the game files. Exit code 1 when the change is refused.
"""

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.normpath(os.path.join(_HERE, '..'))
_HS_DIR = os.path.join(_TOOLS_DIR, 'Hammerspace')
for _path in (_TOOLS_DIR, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import slogger  # noqa: E402

try:
    from . import derive, open_slot, slot_plan, slots, state, state_cli, state_icons
except ImportError:
    import derive
    import open_slot
    import slot_plan
    import slots
    import state
    import state_cli
    import state_icons

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

SOURCE = 'roster.slot'
SLOT_DIR = 'slot'
PLAN_FILE = 'plan.json'


def slot_dir(output_dir: str = state_cli.OUTPUT_DIR) -> str:
    return os.path.join(output_dir, state_cli.GUI_DIR, SLOT_DIR)


def plan_path(output_dir: str = state_cli.OUTPUT_DIR) -> str:
    return os.path.join(slot_dir(output_dir), PLAN_FILE)


class FileEnv(slot_plan.Env):
    """``slot_plan.Env`` on the real files: vanilla skeletons from ``1_Input``, the slot's model from the output."""

    def __init__(self, image, dat):
        self.image, self.dat = image, dat
        self._bank, self._pages = None, {}
        if _HS_DIR not in sys.path:
            sys.path.insert(0, _HS_DIR)

    def shows_portraits(self, char, found):
        icon = char.get('icon') or {}
        try:
            if self._bank is None:
                self._bank = state_icons.read_bank(self.image, self.dat)
            for view, path in (('side', found.side), ('front', found.front)):
                ref = icon.get(view)
                if not ref:
                    return False
                if ref['page'] not in self._pages:
                    self._pages[ref['page']] = self._bank.decode_page(ref['page'])
                x, y, w, h = ref['rect']
                with Image.open(path) as img:
                    art = np.asarray(img.convert('RGBA'))
                if not np.array_equal(art, self._pages[ref['page']][y:y + h, x:x + w]):
                    return False
        except (OSError, ValueError, state_icons.IconStateError):
            return False
        return True

    def skeleton(self, source, target):
        import SlotTarget
        try:
            return SlotTarget._skeleton(source, target)
        except SlotTarget.TargetError as exc:
            raise slot_plan.PlanError(str(exc)) from exc

    def current_high_stem(self, cid):
        import LodPartnerGuard
        try:
            directory = slots.model_dir(self.image, cid)
        except slots.SlotError:
            return None
        block = LodPartnerGuard.read_current_block(directory, slot_plan.HIGH_FILE)
        summary = LodPartnerGuard.act_summary(block) if block else None
        return summary.stem if summary and not summary.is_low_poly else None


def run(action: str, target: str, sluggie: str | None = None, output_dir: str = state_cli.OUTPUT_DIR) -> slot_plan.Plan:
    """Plan ``action`` ('patch' / 'clear') for slot ``target`` and write the files the chain reads."""
    cid = slots.parse_id(target)
    image, dat = state_cli._open(output_dir)
    st = state.read_state(image, dat)
    derived = derive.derive(image, dat)
    folder = slot_dir(output_dir)
    state_file = os.path.join(folder, derive.CONFIG_FILE)
    if action == 'patch':
        pair = slot_plan.classify(sluggie)
        plan = slot_plan.plan_patch(st, derived.config, cid, pair, FileEnv(image, dat), state_file,
                                    state.read_names(image, dat))
    else:
        plan = slot_plan.plan_clear(st, derived.config, cid, state_file)
    plan.warnings[:0] = derived.warnings
    os.makedirs(folder, exist_ok=True)
    if plan.config is not None:
        portraits = dict(derived.portraits)
        paths = open_slot.slot_icon_paths() if plan.extra_portraits else {}
        for name, view in plan.extra_portraits.items():
            with Image.open(paths[view]) as img:
                portraits[name] = (img.convert('RGBA'), None)
        derive.write(derive.Derived(plan.config, portraits), folder)
    tmp = plan_path(output_dir) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(plan.to_json(), f, ensure_ascii=False, indent=1)
    os.replace(tmp, plan_path(output_dir))
    return plan


def main(argv=None) -> int:
    slogger.configure()
    parser = argparse.ArgumentParser(description='Plan a slot patch or clear (GUI character grid).')
    parser.add_argument('--patch', nargs=2, metavar=('0xNN', 'FILE'), help='put a .sluggie (and its partner) into a slot')
    parser.add_argument('--clear', metavar='0xNN', help='return a slot to its baseline')
    parser.add_argument('--output-dir', default=state_cli.OUTPUT_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if bool(args.patch) == bool(args.clear):
        parser.error('give --patch 0xNN FILE or --clear 0xNN')
    if os.path.exists(plan_path(args.output_dir)):
        os.remove(plan_path(args.output_dir))       # a refused plan leaves no stale chain behind
    try:
        if args.patch:
            plan = run('patch', args.patch[0], args.patch[1], output_dir=args.output_dir)
        else:
            plan = run('clear', args.clear, output_dir=args.output_dir)
    except (RuntimeError, ValueError) as exc:        # PlanError, SlotError, StateError, DeriveError, config errors
        slogger.error(f'refused, nothing written: {exc}', source=SOURCE)
        return 1
    for note in plan.notes:
        slogger.info(note, source=SOURCE)
    for warning in plan.warnings:
        slogger.warning(warning, source=SOURCE)
    slogger.info(f'chain: {len(plan.commands)} commands' + ('' if plan.config is not None else ' (no roster rebuild)'),
                 source=SOURCE)
    for command in plan.commands:
        slogger.info('  start.py ' + ' '.join(_quote(a) for a in command), source=SOURCE)
    return 0


def _quote(arg: str) -> str:
    return f'"{arg}"' if ' ' in arg else arg


if __name__ == '__main__':
    raise SystemExit(main())
