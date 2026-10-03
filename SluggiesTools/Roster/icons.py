"""Icons for spare rows and new IDs (plan Phase 5): the roster step owns the icon bank.

Config: an optional ``icon`` on a ``wheels`` entry (spare rows 0x47-0x4C) or an
``ids`` entry (new IDs 0x66 and up)::

    {"id": "0x47", "wheel": "0x06", "icon": {"side": "black_yoshi_side.png",
                                             "front": "black_yoshi_front.png"}}

The PNGs live in ``1_Input/_Icons`` and are fitted into 48x51 as by the icon
pipeline (``fit``: contain, cover or strict; default contain). ``like``
(optional) names the character whose source records the new keys copy; it
decides the record flags, e.g. byte +0x26 of a side record (0x82 for most
characters, 0x02 for Luigi and a few others, probably a mirror flag). The
default is the icon pipeline's donor for a spare row (so existing art looks
the same) and the template for a new ID (as the external tool does).

What the step builds, when at least one entry has an ``icon`` (else it
leaves the icon bank and the DOL alone):

* a fresh icon bank from the stock one (dir 0 file 1574): the expanded
  layout of the icon pipeline (private CMPR pages 0x92/0x93, the container
  at 0x113C80, the three source tables back to back from 0x87520), with one
  64-px atlas slot per entry (16 a row, 4 rows: at most 64), one side and one
  front resource row per entry after the 152 stock rows, and keys pointing
  at them: side and front for every entry, normal_a too for new IDs (it shows
  the front row, as in the external tool). Each table's last frame (header
  +0x18) covers the highest key. For the icon pipeline's six characters, in
  its order and with its donors, the bank equals the pipeline's (stage f).
* the bank in DAT hammerspace, with the icon record pointing at it;
* the three runtime icon hooks of the icon pipeline back to their stock
  words (plan D5: keys alone draw the icons; Dolphin 2026-10-03); their
  stubs in the low-memory caves stay as dead code;
* for new IDs with art: ``portrait_of[id] = id`` (the ids step aliases the
  portrait to the template otherwise) and the portrait renderer's branch at
  0x80395E1C sends IDs >= 0x66 down the normal track path instead of the Mii
  path.
"""

import os
import struct
import tempfile
from dataclasses import dataclass

try:
    from ..Dol import dolfile
    from ..Dol.ppc import Asm, one
    from ..Icons import install_runtime_hooks as hooks
    from . import dat_hammerspace as dhs
    from . import dol_hammerspace, ids, steps
except ImportError:
    from Dol import dolfile
    from Dol.ppc import Asm, one
    from Icons import install_runtime_hooks as hooks
    import dat_hammerspace as dhs
    import dol_hammerspace
    import ids
    import steps

resources = hooks.resources
sources = resources.sources
pages = sources.pages
cib = pages.cib
artwork = resources.artwork

ICON_RECORD = dhs.dol_base_address(cib.ICON_ENTRY_DOL_OFFSET)
ICON_DIR = os.path.join(cib.ROOT, '1_Input', '_Icons')
SLOT = artwork.SLOT_X_STRIDE                 # 64-px slots
SLOTS_PER_ROW = artwork.ATLAS_WIDTH // SLOT  # 16
SLOT_ROWS = artwork.ATLAS_HEIGHT // SLOT     # 4
MAX_ICONS = SLOTS_PER_ROW * SLOT_ROWS
RECORD_KIND = 0x0400
BANK_TAIL = 8                                # zero bytes after the resource table, as the pipeline's bank
# The icon pipeline's donors (icon_characters.json): the records the spare rows' keys copy by default.
SPARE_DONORS = {0x47: 0x04, 0x48: 0x00, 0x49: 0x01, 0x4A: 0x02, 0x4B: 0x03, 0x4C: 0x05}
RESOLVER_SITE, RESOLVER_STOCK = 0x80395E1C, 0x4080008C   # bge 0x80395EA8 (the Mii block) after cmpwi r24,0x4D
RESOLVER_NORMAL, RESOLVER_MII = 0x80395E20, 0x80395EA8
HOOKS = ((hooks.LOWER_HOOK, hooks.LOWER_STOCK_WORD, hooks.LOWER_STUB),
         (hooks.KEY_HOOK, hooks.KEY_STOCK_WORD, hooks.KEY_STUB),
         (hooks.ROW_HOOK, hooks.ROW_STOCK_WORD, hooks.ROW_STUB))


class IconConfigError(ValueError):
    pass


class IconBankError(RuntimeError):
    pass


@dataclass(frozen=True)
class IconEntry:
    char_id: int
    side_path: str
    front_path: str
    like: int
    fit: str = artwork.DEFAULT_FIT_MODE

    @property
    def new_id(self) -> bool:
        return self.char_id >= ids.FIRST_NEW


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def parse_icons(config: dict, icon_dir: str | None = None, check_files: bool = True) -> list[IconEntry]:
    """Icon entries in config order: ``wheels`` entries first, then ``ids`` entries."""
    icon_dir = ICON_DIR if icon_dir is None else icon_dir
    out = []
    new_ids = {c.id: c for c in ids.parse_ids(config)} if config.get('ids') else {}
    listed = []
    for key in ('wheels', 'ids'):
        for n, entry in enumerate(config.get(key) or []):
            if entry.get('icon') is not None:
                listed.append((f'{key}[{n}]', key, entry))
    for where, key, entry in listed:
        icon = entry['icon']
        if not isinstance(icon, dict):
            raise IconConfigError(f'{where}.icon must be an object with "side" and "front"')
        if key == 'wheels':
            cid = ids._number(entry.get('id'), f'{where}.id')
            if cid not in SPARE_DONORS:
                raise IconConfigError(f'{where}: 0x{cid:02X} is not a spare row')
            default_like = SPARE_DONORS[cid]
        else:
            if entry.get('id') is None:
                raise IconConfigError(f'{where}: an entry with an icon needs an explicit "id"')
            cid = ids._number(entry['id'], f'{where}.id')
            default_like = new_ids[cid].template
        like = ids._number(icon['like'], f'{where}.icon.like') if icon.get('like') is not None else default_like
        if not 0 <= like < ids.PLAYER_END:
            raise IconConfigError(f'{where}.icon.like: 0x{like:02X} is not a stock player ID')
        fit = icon.get('fit', artwork.DEFAULT_FIT_MODE)
        if fit not in artwork.FIT_MODES:
            raise IconConfigError(f'{where}.icon.fit: {fit!r} is not one of {", ".join(artwork.FIT_MODES)}')
        paths = []
        for view in ('side', 'front'):
            name = icon.get(view)
            if not isinstance(name, str) or not name or os.path.basename(name) != name:
                raise IconConfigError(f'{where}.icon.{view} must be a plain PNG file name (in 1_Input/_Icons)')
            path = os.path.join(icon_dir, name)
            if check_files and not os.path.isfile(path):
                raise IconConfigError(f'{where}.icon.{view}: {path} not found')
            paths.append(path)
        out.append(IconEntry(cid, paths[0], paths[1], like, fit))
    seen = [e.char_id for e in out]
    if len(set(seen)) != len(seen):
        raise IconConfigError('an ID has two icon entries')
    if len(out) > MAX_ICONS:
        raise IconConfigError(f'{len(out)} icons; the two private icon pages hold {MAX_ICONS}')
    return out


# --------------------------------------------------------------------------
# Bank
# --------------------------------------------------------------------------

def slot_position(index: int) -> tuple[int, int]:
    return (index % SLOTS_PER_ROW) * SLOT, (index // SLOTS_PER_ROW) * SLOT


def artwork_entries(entries: list[IconEntry]) -> list:
    return [artwork.ArtworkEntry(f'0x{e.char_id:02X}', e.char_id, e.side_path, e.front_path, *slot_position(i))
            for i, e in enumerate(entries)]


def encode_atlases(entries: list[IconEntry]) -> tuple[bytes, bytes]:
    """Side and front CMPR payloads (wimgt), each entry fitted with its own fit mode."""
    side = front = None
    for fit in sorted({e.fit for e in entries}):
        group = [a for a, e in zip(artwork_entries(entries), entries) if e.fit == fit]
        s, f = artwork.compose_atlases(group, fit)
        if side is None:
            side, front = s, f
        else:
            side.alpha_composite(s)
            front.alpha_composite(f)
    with tempfile.TemporaryDirectory(prefix='sluggies_roster_icons_') as work:
        return artwork.encode_atlas_cmpr(side, work, 'roster_side'), artwork.encode_atlas_cmpr(front, work, 'roster_front')


def _table(bank: bytes, offset: int) -> list[bytearray]:
    _length, count, stride = sources._source_table_info(bank, offset)
    start = offset + sources.SOURCE_HEADER_SIZE
    return [bytearray(bank[start + i * stride:start + (i + 1) * stride]) for i in range(count)]


def _key(record: bytes) -> int:
    return struct.unpack_from('>H', record, 2)[0]


def extend_table(bank: bytes, offset: int, keys: dict[int, tuple[int, int]]) -> bytes:
    """The source table at ``offset`` with ``keys`` = {id: (like, resource row)} added; returns the table bytes."""
    header = bytearray(bank[offset:offset + sources.SOURCE_HEADER_SIZE])
    records = _table(bank, offset)
    by_id = {_key(r): r for r in records}
    for cid, (like, row) in keys.items():
        if cid in by_id:
            raise IconBankError(f'0x{cid:02X} already has a key in the source table at 0x{offset:X}')
        donor = by_id.get(like) or by_id[max(k for k in by_id if k <= like)]   # the key in force at ``like``
        record = bytearray(donor)
        struct.pack_into('>HHH', record, 0x02, cid, RECORD_KIND, row)
        records.append(record)
    records.sort(key=_key, reverse=True)
    for i, record in enumerate(records):
        flags = struct.unpack_from('>H', record, 0)[0]
        flags = flags & ~sources.SOURCE_FIRST_RECORD_FLAG if i == 0 else flags | sources.SOURCE_FIRST_RECORD_FLAG
        struct.pack_into('>H', record, 0, flags)
    struct.pack_into('>I', header, 0x08, sources.SOURCE_HEADER_SIZE + len(records) * sources.SOURCE_RECORD_SIZE)
    struct.pack_into('>H', header, 0x24, len(records))
    if keys:
        last = struct.unpack_from('>H', header, 0x18)[0]
        struct.pack_into('>H', header, 0x18, max(last, max(keys)))
    return bytes(header) + b''.join(bytes(r) for r in records)


def build_bank(stock_bank: bytes, entries: list[IconEntry], side_payload: bytes, front_payload: bytes) -> bytes:
    """The expanded icon bank with keys, rows and art for ``entries`` (see the module docstring)."""
    if len(stock_bank) != cib.STOCK_BANK_LENGTH:
        raise IconBankError(f'stock icon bank is 0x{len(stock_bank):X} bytes, expected 0x{cib.STOCK_BANK_LENGTH:X}')
    header = struct.unpack_from('>II', stock_bank, 0) + (struct.unpack_from('>H', stock_bank, 0x20)[0],)
    if header != (cib.STOCK_TEXTURE_SECTION, cib.STOCK_ICON_TABLE, cib.STOCK_TEXTURE_COUNT):
        raise IconBankError('the stock icon bank range does not hold the stock icon bank')
    bank = pages.add_private_texture_pages(stock_bank + bytes(cib.EXPANDED_BANK_LENGTH - len(stock_bank)))
    bank = artwork.apply_cmpr_payloads(bank, side_payload, front_payload)
    bank = bytearray(sources.relocate_icon_source_tables(bank))
    n = len(entries)

    # resource rows: the stock rows, then one side row per entry, then one front row per entry
    res = sources.RESOURCE_TABLE_OFFSET
    count, length = struct.unpack_from('>II', bank, res)
    if count != sources.STOCK_RESOURCE_COUNT:
        raise IconBankError(f'resource table has 0x{count:X} rows, expected 0x{sources.STOCK_RESOURCE_COUNT:X}')
    template_row = bytes(bank[res + 8:res + 8 + resources.RESOURCE_ROW_SIZE])
    art = artwork_entries(entries)
    rows = [resources._custom_row(template_row, pages.SIDE_PAGE, a) for a in art]
    rows += [resources._custom_row(template_row, pages.FRONT_PAGE, a) for a in art]
    table = bytes(bank[res:res + length]) + b''.join(rows)
    table = struct.pack('>II', count + 2 * n, len(table)) + table[8:]
    del bank[res:]
    bank += table + bytes(BANK_TAIL)
    struct.pack_into('>I', bank, pages.RELOCATED_ICON_TABLE + resources.ICON_TABLE_END_FIELD,
                     resources.STOCK_ICON_TABLE_END + 2 * n * resources.RESOURCE_ROW_SIZE)
    side_row = {e.char_id: count + i for i, e in enumerate(entries)}
    front_row = {e.char_id: count + n + i for i, e in enumerate(entries)}

    # source tables: rebuilt back to back from NORMAL_A_OFFSET
    fields = (sources.NORMAL_A_POINTER_FIELD, sources.SIDE_POINTER_FIELD, sources.FRONT_POINTER_FIELD)
    keys = {
        sources.NORMAL_A_POINTER_FIELD: {e.char_id: (e.like, front_row[e.char_id]) for e in entries if e.new_id},
        sources.SIDE_POINTER_FIELD: {e.char_id: (e.like, side_row[e.char_id]) for e in entries},
        sources.FRONT_POINTER_FIELD: {e.char_id: (e.like, front_row[e.char_id]) for e in entries},
    }
    snapshot = bytes(bank)
    starts = [sources._signed_pointer(snapshot, f) for f in fields]
    if starts[0] != sources.NORMAL_A_OFFSET:
        raise IconBankError('the source tables are not where the relocation put them')
    old_end = starts[2] + sources._source_table_info(snapshot, starts[2])[0]
    run = b''
    for field, start in zip(fields, starts):
        sources._write_signed_pointer(bank, field, sources.NORMAL_A_OFFSET + len(run))
        run += extend_table(snapshot, start, keys[field])
    end = sources.NORMAL_A_OFFSET + len(run)
    if end > pages.SIDE_IMAGE_OFFSET:
        raise IconBankError(f'the source tables would end at 0x{end:X}, inside the private side image')
    bank[sources.NORMAL_A_OFFSET:max(end, old_end)] = run + bytes(max(0, old_end - end))
    return bytes(bank)


# --------------------------------------------------------------------------
# DOL
# --------------------------------------------------------------------------

def retire_hooks(image: dolfile.DolImage) -> list[str]:
    """Put the icon pipeline's three hook sites back to their stock words."""
    log = []
    for site, stock, stub in HOOKS:
        word = image.u32(site)
        if word == stock:
            continue
        if word != one(site, lambda a: a.b(stub)):
            raise dolfile.DolError(f'icon hook site 0x{site:08X} is {word:08X}: neither stock nor the pipeline hook')
        image.write_word(site, stock)
        log.append(f'icon hook 0x{site:08X} back to stock (stub 0x{stub:08X} left as dead code)')
    return log


def resolver_branch(image: dolfile.DolImage, hs: dol_hammerspace.DolHammerspace) -> str:
    """IDs >= 0x66 take the normal track path (they reach the renderer only with own art: the others are
    aliased to their template at its entry)."""
    if image.u32(RESOLVER_SITE) != RESOLVER_STOCK:
        raise dolfile.DolError(f'0x{RESOLVER_SITE:08X} is {image.u32(RESOLVER_SITE):08X}, expected the stock bge')
    hs.code.put(b'', 4)
    a = Asm(hs.code.here)
    a.blt('normal')
    a.cmpwi('r24', ids.FIRST_NEW).bge('normal')
    a.b(RESOLVER_MII)
    a.label('normal')
    a.b(RESOLVER_NORMAL)
    at = hs.code.put(a.assemble(), 4)
    image.patch_word(RESOLVER_SITE, RESOLVER_STOCK, one(RESOLVER_SITE, lambda b: b.b(at)))
    return f'portrait resolver 0x{RESOLVER_SITE:08X}: IDs 0x{ids.FIRST_NEW:02X}+ take the normal track path'


# --------------------------------------------------------------------------
# Step
# --------------------------------------------------------------------------

def read_record(image) -> list[int]:
    words = dhs.read_record(image, ICON_RECORD)
    slots = {dhs.slot(words, lang)[:2] for lang in dhs.LANGS}
    if len(slots) != 1:
        raise IconBankError('the icon record\'s language slots disagree')
    return words


@steps.register('icons')
def apply(ctx: steps.RosterContext, encode=encode_atlases) -> list[str]:
    entries = parse_icons(ctx.config)
    if not entries:
        bare = [ids._number(w.get('id'), 'wheels.id') for w in ctx.config.get('wheels') or [] if w.get('id')]
        note = ['no "icon" in the roster config: the icon bank stays as it is']
        if bare:
            note.append('note: ' + ', '.join(f'0x{c:02X}' for c in bare) + ' have no icon here; unless the icon '
                        'pipeline gave them one, they show the held stock key (Pink Yoshi)')
        return note
    if ctx.dat is None:
        raise IconBankError('dt_na.dat is missing in the output folder')
    stock = ctx.dat.read(cib.STOCK_BANK_OFFSET, cib.STOCK_BANK_LENGTH)
    bank = build_bank(stock, entries, *encode(entries))
    words = read_record(ctx.dol)
    before = dhs.slot(words, 'en')[:2]
    at = dhs.allocate(ctx.dat, len(bank), dhs.routed_ranges(ctx.dol))
    ctx.dat.write(at, bank)
    for lang in dhs.LANGS:
        dhs.set_slot(words, lang, at, len(bank))
    dhs.write_record(ctx.dol, ICON_RECORD, words)
    log = [f'icon bank 0x{before[0]:08X}+0x{before[1]:X} -> 0x{at:08X}+0x{len(bank):X}: '
           f'{len(entries)} icons (' + ', '.join(f'0x{e.char_id:02X}' for e in entries) + ')']
    log += retire_hooks(ctx.dol)
    own = [e for e in entries if e.new_id]
    # Development switch (bisecting a Dolphin issue): "icon_debug": {"dol_side": false} keeps the new IDs' keys
    # in the bank but leaves the portrait alias and the resolver branch stock.
    if own and not (ctx.config.get('icon_debug') or {}).get('dol_side', True):
        log.append('icon_debug: new-ID portrait alias and resolver branch left stock (keys stay in the bank)')
        own = []
    if own:
        portrait_of = ctx.state.get('portrait_of')
        if portrait_of is None:
            raise IconBankError('new IDs with icons need the ids step (its portrait alias table)')
        hs = dol_hammerspace.get(ctx)
        for e in own:
            hs.write(portrait_of + e.char_id, bytes([e.char_id]))
        log.append(resolver_branch(ctx.dol, hs))
        hs.commit()
        log.append('own portraits: ' + ', '.join(f'0x{e.char_id:02X}' for e in own))
    growth = len(bank) - cib.STOCK_BANK_LENGTH
    log.append(f'icon bank game heap {growth:+,} bytes against stock (resident in MEM2 during a match)')
    return log
