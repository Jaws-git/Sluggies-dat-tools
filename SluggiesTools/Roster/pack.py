"""Roster packs: save the whole roster to one file, load it into any output.

A pack (``*.sluggiesroster``) is a zip:

* ``pack.json``: format version, what the pack holds (``blocks``: the block
  files per slot), counts;
* ``state.json``: the derived config of the saved game (``derive.py``: grid,
  names, square voices, stats sources, own model directories, portraits),
  with each own directory's DAT routes dropped: a pack fits any output, so an
  own directory is a fresh copy of its source's files (``ids[].model.from``,
  from ``1_Input``) unless the game it is loaded into holds the same slot
  already;
* ``icons/``: the derived config's portraits, each the bank's own 48x51 cell
  (PNG) plus its CMPR blocks (``.cmpr``), under the derived names, so cells
  that two entries share stay shared and unchanged portraits keep their bytes;
* ``models/0xNN_hp.bin`` / ``0xNN_l.bin``: the finished High / Low blocks of
  every slot that owns its model files (a stock ID, or a new ID with an own
  model directory) whose block differs from the vanilla one it started from,
  as they sit in the game (blocks only use relative pointers, so they move
  between outputs unchanged);
* ``models/0xNN_bat.bin`` / ``_glove_l`` / ``_glove_r`` / ``_extra``
  (format 2): the equipment blocks (files 2-5) a slot changed. The
  read decides what "changed" is (``state_cli.add_vanilla_flags``: not on its
  vanilla route, or an own directory's copy differing from its source's), not
  the bytes, because an untangle export rewrites texture bytes in place;
* ``fingerprints.json``: per slot (``fingerprints``) the SHA-1 of the High and
  Low block (None: the slot shows another slot's files) and of the front and
  side portrait pixels, plus the own directory's source, name, stats source,
  square voice and square head. Equipment (format 2): the SHA-1 of each
  changed file, None while it is at its baseline; a format 1 pack has no
  such keys and counts as "equipment unchanged".

Loading (``plan_load``) compares the pack's fingerprints with the game's
(``diff``), checks every pack block (``LoadEnv.slot_problems``: validator,
the slot's skeleton, the High/Low pair rules), then gives the chain: one
roster rebuild with the pack's config when it differs from the game's
(``--roster --state``, or ``--roster --remove`` for a stock pack), the stock
slots whose models differ back to vanilla (``--unpatch --target-id``) where
the pack keeps a vanilla file, the pack blocks written as they are
(``--write-slot-blocks``), one ``--roster-state``. An unchanged game gives
no commands. Any refused block refuses the whole load; nothing is written.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import posixpath
import zipfile
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

try:
    from . import derive, icons, ids
except ImportError:
    import derive
    import icons
    import ids

EXTENSION = '.sluggiesroster'
FORMAT = 2
READABLE_FORMATS = (1, 2)                       # format 1: no equipment
META_FILE = 'pack.json'
STATE_FILE = 'state.json'
FINGERPRINTS_FILE = 'fingerprints.json'
ICON_DIR = 'icons'
MODEL_DIR = 'models'
ROLES = {'high': ('hp', 0), 'low': ('l', 1)}          # role: (file name suffix, file index)
EQUIP_ROLES = {'bat': ('bat', 2), 'glove_l': ('glove_l', 3), 'glove_r': ('glove_r', 4), 'extra': ('extra', 5)}
BLOCK_ROLES = {**ROLES, **EQUIP_ROLES}
FIELDS = ('high', 'low', *EQUIP_ROLES, 'model', 'front', 'side', 'name', 'stats', 'voice', 'square')
FIELD_LABELS = {'high': 'High model', 'low': 'Low model', 'bat': 'bat', 'glove_l': 'left glove',
                'glove_r': 'right glove', 'extra': 'extra bat', 'model': 'model directory',
                'front': 'front portrait', 'side': 'side portrait', 'name': 'name', 'stats': 'stats',
                'voice': 'square voice', 'square': 'square'}
SAME, DIFFERS, GAME_ONLY, PACK_ONLY = 'same', 'differs', 'game', 'pack'
ROSTER_KEYS_IGNORED = ('version', 'comment')


class PackError(ValueError):
    pass


def _hex(cid: int) -> str:
    return f'0x{cid:02X}'


def _cid(key: str) -> int:
    return int(key, 16)


# --------------------------------------------------------------------------
# Fingerprints
# --------------------------------------------------------------------------

def pixel_sha1(rgba) -> str:
    return hashlib.sha1(np.ascontiguousarray(np.asarray(rgba, dtype=np.uint8)).tobytes()).hexdigest()


def owns_models(char: dict) -> bool:
    """Whether a slot loads model files of its own: a stock ID, or a new ID with an own model directory (other new
    IDs load their template's)."""
    return char['id'] < ids.FIRST_NEW or bool(char.get('own_model_dir'))


def fingerprint(st: dict, char: dict, crop) -> dict:
    """One slot's fingerprint (module docstring). ``crop(ref)``: a portrait reference's RGBA pixels, or None."""
    blocks = char.get('blocks') or {}
    owns = owns_models(char)
    square = st['squares'][char['square']]
    out = {role: (blocks.get(role) or {}).get('sha1') if owns else None for role in ROLES}
    equipment = char.get('equipment') or {}
    for role in EQUIP_ROLES:           # only a changed file has a fingerprint: the baseline differs per game
        entry = equipment.get(role) or {}
        out[role] = entry.get('sha1') if owns and entry.get('vanilla') is False else None
    out['model'] = _hex(char['model_source']) if char.get('own_model_dir') else None
    for view in ('front', 'side'):
        ref = (char.get('icon') or {}).get(view)
        pixels = crop(ref) if ref else None
        out[view] = pixel_sha1(pixels) if pixels is not None else None
    out['name'] = char.get('name')
    out['stats'] = _hex(char['stats']) if char.get('stats') is not None else None
    out['voice'] = _hex(square['voice'])
    out['square'] = _hex(square['head'])
    return out


def crops_from_dir(icon_dir: str):
    """``crop`` for ``fingerprint``: the crop PNGs ``state_icons.write_crops`` wrote (lossless: the page's pixels)."""
    def crop(ref):
        if not ref.get('file'):
            return None
        try:
            with Image.open(os.path.join(icon_dir, ref['file'])) as img:
                return np.asarray(img.convert('RGBA'))
        except OSError:
            return None
    return crop


def add_fingerprints(st: dict, crop) -> dict[str, dict]:
    """Put ``fingerprint`` on every character of a read state; returns ``{'0xNN': fingerprint}``."""
    out = {}
    for char in st['characters']:
        char['fingerprint'] = fingerprint(st, char, crop)
        out[_hex(char['id'])] = char['fingerprint']
    return out


@dataclass
class SlotDiff:
    cid: int
    status: str                      # SAME / DIFFERS / GAME_ONLY / PACK_ONLY
    fields: list = field(default_factory=list)

    def to_json(self) -> dict:
        return {'id': _hex(self.cid), 'status': self.status, 'fields': list(self.fields)}


def diff(pack_fp: dict, game_fp: dict) -> list[SlotDiff]:
    """Per slot (both sides' IDs, in ID order): same, differs (which fields), only in the game, only in the pack."""
    out = []
    for key in sorted(set(pack_fp) | set(game_fp), key=_cid):
        p, g = pack_fp.get(key), game_fp.get(key)
        if p is None:
            out.append(SlotDiff(_cid(key), GAME_ONLY))
        elif g is None:
            out.append(SlotDiff(_cid(key), PACK_ONLY))
        else:
            fields = [f for f in FIELDS if p.get(f) != g.get(f) and not (f in EQUIP_ROLES and f not in p)]
            out.append(SlotDiff(_cid(key), DIFFERS if fields else SAME, fields))
    return out


# --------------------------------------------------------------------------
# Save
# --------------------------------------------------------------------------

def portable_config(config: dict) -> dict:
    """The derived config without the output's own-directory DAT routes (a fresh copy of ``from`` anywhere else)."""
    out = copy.deepcopy(config)
    for entry in out.get('ids') or []:
        if isinstance(entry.get('model'), dict):
            entry['model'] = {'from': entry['model']['from']}
    return out


def block_name(cid: int, role: str) -> str:
    return f'{MODEL_DIR}/{_hex(cid)}_{BLOCK_ROLES[role][0]}.bin'


def model_directory(cid: int, model_from: int | None) -> int:
    """The directory whose vanilla files a slot started from: its own (stock), or its own directory's source's."""
    return (cid if model_from is None else model_from) + ids.MODEL_DIR_BASE


def pack_files(st: dict, derived: derive.Derived, current_block, vanilla_block) -> dict[str, bytes]:
    """Every file of the pack of a read state (with ``fingerprint`` on its characters) and its derived config.
    ``current_block(char, role)``: the block the slot loads now; ``vanilla_block(directory, file)``: the input's."""
    files: dict[str, bytes] = {}
    blocks: dict[str, dict] = {}
    for char in st['characters']:
        if not owns_models(char) or not (char.get('blocks') or char.get('equipment')):
            continue
        model_from = char['model_source'] if char.get('own_model_dir') else None
        directory = model_directory(char['id'], model_from)
        for role, (_suffix, file_index) in ROLES.items():
            if role not in (char.get('blocks') or {}):
                continue
            block = current_block(char, role)
            if block is None or block == vanilla_block(directory, file_index):
                continue
            name = block_name(char['id'], role)
            files[name] = block
            blocks.setdefault(_hex(char['id']), {})[role] = name
        for role in EQUIP_ROLES:
            if (char.get('equipment') or {}).get(role, {}).get('vanilla') is not False:
                continue
            block = current_block(char, role)
            if block is None:
                continue
            name = block_name(char['id'], role)
            files[name] = block
            blocks.setdefault(_hex(char['id']), {})[role] = name
    for name, (art, cell) in derived.portraits.items():
        buf = io.BytesIO()
        art.save(buf, 'PNG')
        files[f'{ICON_DIR}/{name}'] = buf.getvalue()
        if cell is not None:
            files[f'{ICON_DIR}/{name[:-4]}{icons.KEPT_BLOCKS_EXT}'] = bytes(cell)
    fingerprints = {_hex(c['id']): c['fingerprint'] for c in st['characters']}
    meta = {'format': FORMAT, 'kind': st['kind'], 'shape': st['shape'], 'slots': len(fingerprints),
            'blocks': blocks, 'portraits': len(derived.portraits)}
    files[META_FILE] = _json(meta)
    files[STATE_FILE] = _json(portable_config(derived.config))
    files[FINGERPRINTS_FILE] = _json(fingerprints)
    return files


def _json(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=1).encode('utf-8')


def write_pack(path: str, files: dict[str, bytes]) -> None:
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = path + '.tmp'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files, key=lambda n: (n != META_FILE, n)):
            zf.writestr(name, files[name])
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------

@dataclass
class Pack:
    path: str
    meta: dict
    config: dict
    fingerprints: dict
    icons: dict                      # {file name: bytes} (PNGs and their .cmpr blocks)
    blocks: dict                     # {(id, role): bytes}

    def slot_blocks(self, cid: int) -> dict:
        """Every block the pack holds for the slot (models and equipment)."""
        return {role: self.blocks[(cid, role)] for role in BLOCK_ROLES if (cid, role) in self.blocks}

    def slot_models(self, cid: int) -> dict:
        return {role: self.blocks[(cid, role)] for role in ROLES if (cid, role) in self.blocks}

    def slot_equipment(self, cid: int) -> dict:
        return {role: self.blocks[(cid, role)] for role in EQUIP_ROLES if (cid, role) in self.blocks}


def _member(zf: zipfile.ZipFile, name: str) -> bytes:
    try:
        return zf.read(name)
    except KeyError as exc:
        raise PackError(f'{name} is missing from the pack') from exc


def _load_json(zf: zipfile.ZipFile, name: str):
    try:
        return json.loads(_member(zf, name).decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PackError(f'{name} in the pack is not valid JSON: {exc}') from exc


def read_pack(path: str) -> Pack:
    """Open and check a pack: its format, its JSON files, file names that stay inside the pack, and every block
    against its fingerprint (a damaged block is refused here, before anything is planned)."""
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise PackError(f'{path} is not a roster pack: {exc}') from exc
    with zf:
        meta = _load_json(zf, META_FILE)
        if not isinstance(meta, dict) or meta.get('format') not in READABLE_FORMATS:
            raise PackError(f'{os.path.basename(path)} is a roster pack of format {meta.get("format")!r}; this tool '
                            f'reads format {" and ".join(map(str, READABLE_FORMATS))}')
        config = _load_json(zf, STATE_FILE)
        fingerprints = _load_json(zf, FINGERPRINTS_FILE)
        if not isinstance(config, dict) or not isinstance(fingerprints, dict):
            raise PackError('the pack\'s state or fingerprints are not JSON objects')
        icons_ = {}
        for name in zf.namelist():
            if name.startswith(ICON_DIR + '/') and not name.endswith('/'):
                base = posixpath.basename(name)
                if posixpath.dirname(name) != ICON_DIR or not base.endswith(('.png', icons.KEPT_BLOCKS_EXT)):
                    raise PackError(f'unexpected file in the pack: {name}')
                icons_[base] = zf.read(name)
        blocks = {}
        for key, roles in (meta.get('blocks') or {}).items():
            try:
                cid = _cid(key)
            except (TypeError, ValueError) as exc:
                raise PackError(f'pack.json names a block of {key!r}, not an ID') from exc
            for role, name in roles.items():
                if role not in BLOCK_ROLES or name != block_name(cid, role):
                    raise PackError(f'pack.json names an unexpected block for {key}: {role} {name!r}')
                data = _member(zf, name)
                expected = (fingerprints.get(key) or {}).get(role)
                if hashlib.sha1(data).hexdigest() != expected:
                    raise PackError(f'the {role} block of {key} does not match its fingerprint: the pack is damaged')
                blocks[(cid, role)] = data
    for entry in _icon_entries(config):
        for view in ('side', 'front'):
            name = entry['icon'].get(view)
            if isinstance(name, str) and name not in icons_:
                raise PackError(f'{entry.get("id")}: portrait {name} is missing from the pack')
    return Pack(path, meta, config, fingerprints, icons_, blocks)


def _icon_entries(config: dict):
    for key in ('ids', 'wheels', icons.STOCK_KEY):
        for entry in config.get(key) or []:
            if isinstance(entry, dict) and isinstance(entry.get('icon'), dict):
                yield entry


# --------------------------------------------------------------------------
# Load
# --------------------------------------------------------------------------

class LoadEnv:
    """The block checks a load needs (the default reads the game files; tests pass their own)."""

    def slot_problems(self, directory: int, high: bytes | None, low: bytes | None) -> tuple[list[str], list[str]]:
        """``(errors, warnings)`` for a slot whose files started from ``directory``'s vanilla ones taking these
        blocks (None: the vanilla file stays)."""
        raise NotImplementedError

    def equipment_problems(self, directory: int, file_index: int, block: bytes) -> tuple[list[str], list[str]]:
        """``(errors, warnings)`` for a slot whose file ``file_index`` (2-5, started from ``directory``'s vanilla
        one) takes this finished equipment block."""
        return [], []


@dataclass
class LoadPlan:
    diff: list
    config: dict | None = None       # the config the rebuild uses, or None: no rebuild
    remove: bool = False             # a stock pack: the rebuild is a roster reset
    commands: list = field(default_factory=list)
    writes: list = field(default_factory=list)       # [(id, {'high': name, 'low': name})] of pack blocks written
    equipment_writes: list = field(default_factory=list)   # [(id, role)]: equipment blocks written
    equipment_clears: list = field(default_factory=list)   # [(id, file)]: equipment files back to their baseline
    clears: list = field(default_factory=list)       # stock IDs whose models go back to vanilla first
    kept_dirs: list = field(default_factory=list)    # new IDs whose own directory is kept as it is
    notes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    refused: list = field(default_factory=list)      # [(id, problem)]

    @property
    def ok(self) -> bool:
        return not self.refused

    def to_json(self) -> dict:
        return {'action': 'load_pack', 'ok': self.ok, 'rebuild': self.config is not None or self.remove,
                'remove': self.remove, 'commands': [list(c) for c in self.commands],
                'diff': [d.to_json() for d in self.diff],
                'writes': [{'id': _hex(c), 'blocks': sorted(r)} for c, r in self.writes],
                'equipment_writes': [{'id': _hex(c), 'role': r} for c, r in self.equipment_writes],
                'equipment_clears': [{'id': _hex(c), 'file': f} for c, f in self.equipment_clears],
                'clears': [_hex(c) for c in self.clears], 'kept_dirs': [_hex(c) for c in self.kept_dirs],
                'notes': self.notes, 'warnings': self.warnings,
                'refused': [{'id': _hex(c), 'error': e} for c, e in self.refused]}


def roster_keys(config: dict) -> set:
    return set(config) - set(ROSTER_KEYS_IGNORED)


def _normal(config: dict, portrait_key) -> dict:
    """``config`` with each portrait file name replaced by its content (``portrait_key(name)``), for comparing two
    configs whose portraits are stored under different names."""
    out = copy.deepcopy(config)
    for entry in _icon_entries(out):
        for view in ('side', 'front'):
            if isinstance(entry['icon'].get(view), str):
                entry['icon'][view] = portrait_key(entry['icon'][view])
    return out


def _pack_portrait_key(pack: Pack):
    def key(name):
        try:
            with Image.open(io.BytesIO(pack.icons[name])) as img:
                pixels = np.asarray(img.convert('RGBA'))
        except (KeyError, OSError):
            return name
        cell = pack.icons.get(name[:-4] + icons.KEPT_BLOCKS_EXT, b'')
        return pixel_sha1(pixels) + hashlib.sha1(cell).hexdigest()
    return key


def _derived_portrait_key(derived: derive.Derived):
    def key(name):
        if name not in derived.portraits:
            return name
        art, cell = derived.portraits[name]
        return pixel_sha1(np.asarray(art.convert('RGBA'))) + hashlib.sha1(bytes(cell or b'')).hexdigest()
    return key


def plan_load(pack: Pack, game_st: dict, game_fp: dict, game_derived: derive.Derived, env: LoadEnv,
              state_file: str, block_path) -> LoadPlan:
    """The chain that makes the game hold the pack's roster (module docstring). ``block_path(name)``: where the
    extracted pack block ``name`` lies for the chain's commands."""
    plan = LoadPlan(diff(pack.fingerprints, game_fp))
    config = copy.deepcopy(pack.config)
    game_chars = {c['id']: c for c in game_st['characters']}
    game_entries = {ids._number(e['id'], 'ids'): e for e in game_derived.config.get('ids') or []}

    # own model directories: kept when the game holds the same one, else a fresh copy plus the pack's blocks
    own_from = {}
    for entry in config.get('ids') or []:
        if not isinstance(entry.get('model'), dict):
            continue
        cid, source = ids._number(entry['id'], 'ids'), ids._number(entry['model']['from'], 'model.from')
        own_from[cid] = source
        game, key = game_chars.get(cid), _hex(cid)
        same_blocks = all((pack.fingerprints.get(key) or {}).get(r) == (game_fp.get(key) or {}).get(r)
                          for r in ROLES)
        same_equipment = all(r not in (pack.fingerprints.get(key) or {})
                             or (pack.fingerprints.get(key) or {}).get(r) == (game_fp.get(key) or {}).get(r)
                             for r in EQUIP_ROLES)
        game_model = (game_entries.get(cid) or {}).get('model')
        if (game is not None and game.get('own_model_dir') and game.get('model_source') == source and same_blocks
                and same_equipment and isinstance(game_model, dict) and game_model.get('routes')):
            entry['model'] = copy.deepcopy(game_model)
            plan.kept_dirs.append(cid)
        else:                  # a fresh copy of the source's files: the pack's blocks go on top
            if pack.slot_models(cid):
                plan.writes.append((cid, pack.slot_models(cid)))
            plan.equipment_writes += [(cid, role) for role in pack.slot_equipment(cid)]

    # stock slots: back to vanilla where the pack keeps a vanilla file, then the pack's blocks
    stock_writes = []
    for key, p in sorted(pack.fingerprints.items(), key=lambda kv: _cid(kv[0])):
        cid = _cid(key)
        if cid >= ids.FIRST_NEW:
            continue
        g = game_fp.get(key)
        for role, (_suffix, file_index) in EQUIP_ROLES.items():
            if role not in p or (g is not None and p.get(role) == g.get(role)):
                continue                      # a format 1 pack has no equipment keys: left as it is
            if (cid, role) in pack.blocks:
                plan.equipment_writes.append((cid, role))
            elif g is not None and g.get(role) is not None:
                plan.equipment_clears.append((cid, file_index))
        if g is not None and all(p.get(r) == g.get(r) for r in ROLES):
            continue
        blocks = pack.slot_models(cid)
        if len(blocks) < len(ROLES):
            plan.clears.append(cid)
        if blocks:
            stock_writes.append((cid, blocks))
    plan.writes = stock_writes + plan.writes

    # every pack block is checked, written or not: a damaged pack is refused as a whole
    for cid in sorted({c for c, _r in pack.blocks}):
        if cid >= ids.FIRST_NEW and cid not in own_from:
            plan.refused.append((cid, 'the pack holds model blocks for this new ID, but it has no own model '
                                      'directory in the pack\'s roster'))
            continue
        blocks = pack.slot_models(cid)
        directory = model_directory(cid, own_from.get(cid))
        if blocks:
            errors, warnings = env.slot_problems(directory, blocks.get('high'), blocks.get('low'))
            plan.refused += [(cid, e) for e in errors]
            plan.warnings += [f'{_hex(cid)}: {w}' for w in warnings]
        for role, block in pack.slot_equipment(cid).items():
            errors, warnings = env.equipment_problems(directory, EQUIP_ROLES[role][1], block)
            plan.refused += [(cid, f'the {FIELD_LABELS[role]}: {e}') for e in errors]
            plan.warnings += [f'{_hex(cid)}: the {FIELD_LABELS[role]}: {w}' for w in warnings]
    if plan.refused:
        plan.writes, plan.clears, plan.kept_dirs = [], [], []
        plan.equipment_writes, plan.equipment_clears = [], []
        return plan

    # one rebuild when the roster differs
    pack_stock, game_stock = not roster_keys(config), not roster_keys(game_derived.config)
    if pack_stock:
        plan.remove = not game_stock
    elif (_normal(config, _pack_portrait_key(pack))
          != _normal(game_derived.config, _derived_portrait_key(game_derived))):
        plan.config = config
    if plan.remove:
        plan.commands.append(('--roster', '--remove'))
    elif plan.config is not None:
        plan.commands.append(('--roster', '--state', state_file))
    for cid in plan.clears:
        plan.commands.append(('--unpatch', '--target-id', _hex(cid)))
    for cid, file_index in plan.equipment_clears:
        plan.commands.append(('--unpatch', '--target-id', _hex(cid), '--target-file', str(file_index)))
    for cid, blocks in plan.writes:
        plan.commands.append(('--write-slot-blocks', _hex(cid),
                              *(block_path(block_name(cid, r)) if r in blocks else '-' for r in ROLES)))
    for cid, role in plan.equipment_writes:
        plan.commands.append(('--write-slot-equipment', _hex(cid), str(EQUIP_ROLES[role][1]),
                              block_path(block_name(cid, role))))
    if plan.commands:
        plan.commands.append(('--roster-state',))
    elif plan.config is None:
        plan.notes.append('the game holds this roster already: nothing to load')

    game_only = [d.cid for d in plan.diff if d.status == GAME_ONLY]
    if game_only:
        plan.notes.append('slots only in the game leave the grid; models patched into stock slots among them stay '
                          'in their directories: ' + ', '.join(_hex(c) for c in game_only))
    if plan.kept_dirs and plan.commands:
        plan.notes.append('own model directories kept as they are (the game holds the same blocks): '
                          + ', '.join(_hex(c) for c in plan.kept_dirs))
    return plan


def extract(pack: Pack, plan: LoadPlan, folder: str) -> str:
    """Write what the chain reads into ``folder``: the rebuild's config (``derive.CONFIG_FILE``) with the pack's
    portraits beside it, and the blocks it writes. Returns the config's path."""
    icon_dir = os.path.join(folder, derive.ICON_DIR)
    model_dir = os.path.join(folder, MODEL_DIR)
    for sub in (icon_dir, model_dir):
        os.makedirs(sub, exist_ok=True)
        for name in os.listdir(sub):
            os.remove(os.path.join(sub, name))
    for name, data in pack.icons.items():
        with open(os.path.join(icon_dir, name), 'wb') as f:
            f.write(data)
    for (cid, role), data in pack.blocks.items():
        with open(os.path.join(folder, *block_name(cid, role).split('/')), 'wb') as f:
            f.write(data)
    path = os.path.join(folder, derive.CONFIG_FILE)
    with open(path, 'wb') as f:
        f.write(_json(plan.config if plan.config is not None else pack.config))
    return path
