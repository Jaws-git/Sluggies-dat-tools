"""``start.py --save-roster FILE`` / ``--load-roster FILE``: roster packs (``pack.py``).

``--save FILE`` reads ``3_Output_Dat`` (``state_cli.run``: the state with
every slot's fingerprint), derives its config and writes the pack. It never
writes the game files.

``--load FILE`` checks the pack, compares it with the game and plans the
load chain (``pack.plan_load``). It writes what the chain reads into
``3_Output_Dat/_gui/pack/`` (``load/roster.json`` + ``icons/`` +
``models/``) and the plan into ``_gui/pack/plan.json``: the per-slot diff,
the commands, refused blocks. ``start.py`` then runs the commands in order
(not with ``--dry-run``), stopping at the first failure. Exit code 1 when
the pack is refused (nothing written).
"""

import argparse
import json
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.normpath(os.path.join(_HERE, '..'))
_HS_DIR = os.path.join(_TOOLS_DIR, 'Hammerspace')
for _path in (_TOOLS_DIR, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import slogger  # noqa: E402

try:
    from ..Dol import dolfile
    from . import dat_hammerspace as dhs
    from . import datfile, derive, pack, state_cli
except ImportError:
    from Dol import dolfile
    import dat_hammerspace as dhs
    import datfile
    import derive
    import pack
    import state_cli

SOURCE = 'roster.pack'
PACK_DIR = 'pack'
LOAD_DIR = 'load'
PLAN_FILE = 'plan.json'
INPUT_DIR = os.path.join(state_cli.ROOT, '1_Input')


def pack_dir(output_dir: str = state_cli.OUTPUT_DIR) -> str:
    return os.path.join(output_dir, state_cli.GUI_DIR, PACK_DIR)


def plan_path(output_dir: str = state_cli.OUTPUT_DIR) -> str:
    return os.path.join(pack_dir(output_dir), PLAN_FILE)


def route(image: dolfile.DolImage, directory: int, file_index: int) -> tuple[int, int] | None:
    """``(offset, length)`` of a directory's file in the DAT (English slot), or None."""
    pointers = dhs.dir_pointers(image)
    if not 0 <= directory < len(pointers):
        return None
    address = pointers[directory] + dhs.RECORD_SIZE * file_index
    if not image.is_mapped(address, dhs.RECORD_SIZE):
        return None
    words = struct.unpack('>12I', image.read(address, dhs.RECORD_SIZE))
    if words[0] != dhs.hh._DAT_FNAME_PTR:
        return None
    offset, length, _alloc = dhs.slot(words, 'en')
    return offset, length


class InputFiles:
    """The vanilla blocks of ``1_Input`` (each read once)."""

    def __init__(self, input_dir: str = INPUT_DIR):
        dol_path, self.dat_path = os.path.join(input_dir, 'main.dol'), os.path.join(input_dir, 'dt_na.dat')
        if not os.path.isfile(dol_path) or not os.path.isfile(self.dat_path):
            raise pack.PackError(f'{input_dir} needs main.dol and dt_na.dat (the vanilla files every pack starts from)')
        with open(dol_path, 'rb') as f:
            self.image = dolfile.DolImage(f.read())
        self._cache = {}

    def vanilla_block(self, directory: int, file_index: int) -> bytes | None:
        key = (directory, file_index)
        if key not in self._cache:
            found = route(self.image, directory, file_index)
            block = None
            if found is not None and found[1] > 0:
                with open(self.dat_path, 'rb') as f:
                    f.seek(found[0])
                    block = f.read(found[1])
            self._cache[key] = block
        return self._cache[key]


class FileLoadEnv(pack.LoadEnv):
    """``pack.LoadEnv`` on the real files: ``BlockValidator`` and ``SlotTarget.slot_pair_problems`` against the
    vanilla files of ``1_Input``."""

    def __init__(self, inputs: InputFiles):
        self.inputs = inputs
        if _HS_DIR not in sys.path:
            sys.path.insert(0, _HS_DIR)

    def slot_problems(self, directory, high, low):
        import LodPartnerGuard
        import SlotTarget
        vanilla = {f: self.inputs.vanilla_block(directory, f) for f in (0, 1)}
        problems = []
        for f, role, block in ((0, 'High', high), (1, 'Low', low)):
            if block is not None:
                errors = SlotTarget.block_errors(block, vanilla[f])
                if errors:
                    problems.append(f'the {role} block fails validation: ' + '; '.join(errors[:3]))
        if problems:
            return problems, []
        summaries = {f: LodPartnerGuard.act_summary(b) if b else None for f, b in vanilla.items()}
        return SlotTarget.slot_pair_problems(high if high is not None else vanilla[0],
                                             low if low is not None else vanilla[1], summaries)

    def equipment_problems(self, directory, file_index, block):
        import SlotTarget
        return SlotTarget.equipment_block_problems(block, directory, file_index)


def save(path: str, output_dir: str = state_cli.OUTPUT_DIR, input_dir: str = INPUT_DIR) -> dict:
    """Write the pack of the output's roster to ``path``; returns its ``pack.json``."""
    st = state_cli.run(output_dir)                   # the state with fingerprints (and the GUI's crops)
    image, dat = state_cli._open(output_dir)
    if dat is None:
        raise pack.PackError('dt_na.dat is missing in the output: there is no roster to save')
    derived = derive.derive(image, dat)
    inputs = InputFiles(input_dir)

    def current_block(char, role):
        ref = (char['blocks'] if role in pack.ROLES else char['equipment'])[role]
        return dat.read(ref['offset'], ref['length'])
    files = pack.pack_files(st, derived, current_block, inputs.vanilla_block)
    pack.write_pack(path, files)
    meta = json.loads(files[pack.META_FILE])
    meta['warnings'] = derived.warnings
    return meta


def plan(path: str, output_dir: str = state_cli.OUTPUT_DIR, input_dir: str = INPUT_DIR) -> pack.LoadPlan:
    """Check the pack at ``path`` and plan its load into the output; writes the plan (and, when it is not refused,
    the files its chain reads)."""
    folder = pack_dir(output_dir)
    os.makedirs(folder, exist_ok=True)
    if os.path.exists(plan_path(output_dir)):
        os.remove(plan_path(output_dir))            # a failed planner leaves no stale chain behind
    p = pack.read_pack(path)
    st = state_cli.run(output_dir)
    image, dat = state_cli._open(output_dir)
    derived = derive.derive(image, dat)
    game_fp = {pack._hex(c['id']): c['fingerprint'] for c in st['characters']}
    load_dir = os.path.join(folder, LOAD_DIR)
    state_file = os.path.join(load_dir, derive.CONFIG_FILE)
    result = pack.plan_load(p, st, game_fp, derived, FileLoadEnv(InputFiles(input_dir)), state_file,
                            lambda name: os.path.join(load_dir, *name.split('/')))
    if result.ok and result.commands:
        pack.extract(p, result, load_dir)
    out = result.to_json()
    out.update(pack=os.path.abspath(path), pack_fingerprints=p.fingerprints, pack_meta=p.meta)
    tmp = plan_path(output_dir) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, plan_path(output_dir))
    return result


def main(argv=None) -> int:
    slogger.configure()
    parser = argparse.ArgumentParser(description='Save or load a roster pack (GUI character grid).')
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument('--save', metavar='FILE', help='write the output\'s roster into a pack')
    what.add_argument('--load', metavar='FILE', help='check a pack and plan its load (start.py runs the chain)')
    parser.add_argument('--output-dir', default=state_cli.OUTPUT_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.save:
            meta = save(args.save, args.output_dir)
        else:
            result = plan(args.load, args.output_dir)
    except (RuntimeError, ValueError, OSError) as exc:   # PackError, StateError, DeriveError, ...
        slogger.error(f'refused, nothing written: {exc}', source=SOURCE)
        return 1
    if args.save:
        size = os.path.getsize(args.save)
        blocks = sum(len(r) for r in meta['blocks'].values())
        slogger.info(f'roster pack written: {args.save} ({size / (1024 * 1024):.2f} MB; {meta["slots"]} slots, '
                     f'{blocks} model blocks, {meta["portraits"]} portraits)', source=SOURCE)
        for warning in meta['warnings']:
            slogger.warning(warning, source=SOURCE)
        return 0
    for d in result.diff:
        if d.status != pack.SAME:
            what = {pack.DIFFERS: 'differs: ' + ', '.join(pack.FIELD_LABELS[f] for f in d.fields),
                    pack.GAME_ONLY: 'only in the game', pack.PACK_ONLY: 'only in the pack'}[d.status]
            slogger.info(f'0x{d.cid:02X}: {what}', source=SOURCE)
    same = sum(d.status == pack.SAME for d in result.diff)
    slogger.info(f'{same} of {len(result.diff)} slots are the same in the pack and the game', source=SOURCE)
    for note in result.notes:
        slogger.info(note, source=SOURCE)
    for warning in result.warnings:
        slogger.warning(warning, source=SOURCE)
    if result.refused:
        for cid, error in result.refused:
            slogger.error(f'refused, nothing written: 0x{cid:02X}: {error}', source=SOURCE)
        return 1
    slogger.info(f'chain: {len(result.commands)} commands'
                 + (' (roster rebuild)' if result.config is not None or result.remove else ' (no roster rebuild)'),
                 source=SOURCE)
    for command in result.commands:
        slogger.info('  start.py ' + ' '.join(f'"{a}"' if ' ' in a else a for a in command), source=SOURCE)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
