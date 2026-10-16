"""What a planned chain leaves in each slot's player memory: the projection ``memory_guard`` checks.

A player's heap holds its High model (file 0), Low model (file 1) and one bat or glove (files 2-5,
``game_memory``). :func:`project` replays a planned chain (``slot_plan.plan_batch``'s commands, or a roster pack
load's, ``pack.plan_load``) on the lengths of those files:

* start: the files each slot loads now (the read state's ``blocks`` / ``equipment``);
* the roster rebuild (``final_config``): a new ID whose ``ids[].model`` has no ``routes`` gets a fresh copy of its
  source's files (``model_dirs``: the vanilla ones of ``1_Input``); a new ID without an own directory loads its
  template's files, so its template's check covers it and it is left out;
* ``--patch FILES --target-id 0xNN [--as-low] [--target-file N]``: each file becomes ``{'build': path}``, the block
  its build check measures (``memory_guard.lookup_size``); ``--as-low`` puts the High model in file 1 too;
* ``--patch FILES`` (in-place, the model's own route): the vanilla length (the in-place patcher writes over the
  vanilla block), or a build ref for a hammerspace file;
* ``--unpatch --target-id 0xNN [--target-file N]``: the vanilla files (High + Low, or file N) of the slot's
  directory source;
* ``--write-slot-blocks 0xNN HIGH LOW`` / ``--write-slot-equipment 0xNN N BLOCK``: the block files' sizes.

Only slots whose files change are returned: ``{"0xNN": {"name", "before", "after"}}`` (``{index: length or
build ref}``), the section ``memory_guard.guard`` reads from the plan file.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.normpath(os.path.join(_HERE, '..'))
for _path in (_TOOLS_DIR, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from binfmt import load_sluggie  # noqa: E402

try:
    from . import gear, ids
except ImportError:
    import gear
    import ids

FILES = (0, 1, 2, 3, 4, 5)       # High, Low, bat, left glove, right glove, extra bat
BUILD = 'build'                  # memory_guard.BUILD
SLOT_COMMANDS = ('--patch', '--unpatch', '--write-slot-blocks', '--write-slot-equipment')


def _hex(cid: int) -> str:
    return f'0x{cid:02X}'


def _option(args: tuple, name: str) -> str | None:
    if name not in args:
        return None
    i = args.index(name)
    return args[i + 1] if i + 1 < len(args) else None


def _paths(args: tuple) -> list[str]:
    """The file arguments after the command's flag, up to the next flag."""
    out = []
    for value in args[1:]:
        if value.startswith('--'):
            break
        out.append(value)
    return out


def current_files(char: dict) -> dict[int, int]:
    """The lengths of the files a slot loads now (read state)."""
    files = {}
    blocks = char.get('blocks') or {}
    for role, index in (('high', 0), ('low', 1)):
        if role in blocks:
            files[index] = blocks[role]['length']
    for entry in (char.get('equipment') or {}).values():
        files[entry['file']] = entry['length']
    return files


def _model_of(entry: dict) -> dict | None:
    model = entry.get('model')
    return model if isinstance(model, dict) and model.get('from') is not None else None


def _sluggie(path: str) -> dict:
    try:
        return load_sluggie(path).get('SluggiesModel') or {}
    except (OSError, ValueError):
        return {}


def _size(path: str) -> int | None:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def project(st: dict, final_config: dict | None, commands, vanilla_length, names: dict | None = None,
            read_model=_sluggie) -> dict:
    """The projection of ``commands`` on the slots of ``st`` (module docstring). ``final_config``: the roster
    rebuild's config (None: no rebuild); ``vanilla_length(directory, file)``: the length in ``1_Input`` (None when
    there is none); ``names``: ``{cid: display name}``."""
    names = dict(names or {})
    chars = {c['id']: c for c in st.get('characters') or []}
    files: dict[int, dict] = {}
    source_dir: dict[int, int] = {}              # the directory whose vanilla files are the slot's baseline
    for cid, char in chars.items():
        if cid >= ids.FIRST_NEW and not char.get('own_model_dir'):
            continue                                 # the template's files: its own check covers them
        if not (char.get('blocks') or char.get('equipment')):
            continue
        files[cid] = current_files(char)
        source = cid if cid < ids.FIRST_NEW else char.get('model_source')
        if source is not None:
            source_dir[cid] = source + ids.MODEL_DIR_BASE
    before = {cid: dict(f) for cid, f in files.items()}

    if final_config is not None:
        entries = {ids._number(e['id'], 'ids'): e for e in final_config.get('ids') or []
                   if isinstance(e, dict) and e.get('id') is not None}
        for cid in [c for c in files if c >= ids.FIRST_NEW]:
            if cid not in entries or _model_of(entries[cid]) is None:
                del files[cid]                       # leaves the roster, or loads its template's files again
        for cid, entry in entries.items():
            model = _model_of(entry)
            if model is None or model.get('routes'):
                continue                             # no own directory, or the copies it has are kept
            directory = ids._number(model['from'], 'model.from') + ids.MODEL_DIR_BASE
            files[cid] = {i: vanilla_length(directory, i) for i in FILES}
            source_dir[cid] = directory
            en = (entry.get('name') or {}).get('en') if isinstance(entry.get('name'), dict) else None
            if cid not in names and en:          # a new ID only the config has (a roster pack's)
                names[cid] = f'{en} ({_hex(cid)})'

    def slot(cid):
        if cid not in files:                         # a slot the read had no files for (or a dirless new ID)
            files[cid] = current_files(chars[cid]) if cid in chars else {}
        return files[cid]

    for raw in commands:
        args = tuple(raw)
        if not args or args[0] not in SLOT_COMMANDS or '--validate-only' in args:
            continue
        target = _option(args, '--target-id')
        cid = ids._number(target, '--target-id') if target is not None else None
        if args[0] == '--patch':
            target_file = _option(args, '--target-file')
            for path in _paths(args):
                model = read_model(path)
                index = model.get('FileIndex')
                if cid is None:                      # in place, the model's own route
                    chunk = model.get('ChunkNumber')
                    if chunk is None or index is None:
                        continue
                    owner = chunk - ids.MODEL_DIR_BASE
                    if not 0 <= owner < ids.STOCK_IDS:
                        continue
                    slot(owner)[index] = ({BUILD: os.path.abspath(path)} if model.get('UseHammerspace')
                                          else vanilla_length(chunk, index))
                    continue
                if target_file is not None and index in gear.ROLES:
                    index = int(target_file)
                if index is None:
                    continue
                slot(cid)[index] = {BUILD: os.path.abspath(path)}
                if '--as-low' in args and index == 0:
                    slot(cid)[1] = {BUILD: os.path.abspath(path)}
        elif args[0] == '--unpatch' and cid is not None:
            directory = source_dir.get(cid, cid + ids.MODEL_DIR_BASE if cid < ids.FIRST_NEW else None)
            if directory is None:
                continue
            target_file = _option(args, '--target-file')
            indexes = (0, 1) if target_file is None else (int(target_file),)
            for index in indexes:
                if index in FILES:
                    slot(cid)[index] = vanilla_length(directory, index)
        elif args[0] == '--write-slot-blocks':
            cid = ids._number(args[1], '--write-slot-blocks')
            for index, path in ((0, args[2]), (1, args[3])):
                if path != '-':
                    slot(cid)[index] = _size(path)
        elif args[0] == '--write-slot-equipment':
            cid = ids._number(args[1], '--write-slot-equipment')
            slot(cid)[int(args[2], 0)] = _size(args[3])

    out = {}
    for cid, after in sorted(files.items()):
        if after == before.get(cid):
            continue
        out[_hex(cid)] = {'name': names.get(cid) or _hex(cid),
                          'before': {str(i): v for i, v in sorted((before.get(cid) or {}).items())},
                          'after': {str(i): v for i, v in sorted(after.items())}}
    return out


def display_names(st: dict) -> dict[int, str]:
    """``{cid: "Mario (0x00)"}`` from the read state's English names, for the check's messages."""
    out = {}
    for c in st.get('characters') or []:
        en = (c.get('name') or {}).get('en')
        out[c['id']] = f'{en} ({_hex(c["id"])})' if en else _hex(c['id'])
    return out
