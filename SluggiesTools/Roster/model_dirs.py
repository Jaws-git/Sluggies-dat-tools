"""Own model directories for new IDs (GUI character grid, Phase 4b; port of the external tool's step "2b").

A new ID plays with its template's model directory (``dirmap[id]`` = template
+ 0x12), so a model patched into it would change the template too. An
``ids`` entry with ``"model"`` gets a directory of its own:

    {"id": "0x66", "template": "0x00", "model": {"from": "0x0D"}}

* ``from``: the stock player ID whose files the directory holds. Every file of
  that character's directory (``1_Input``) is copied into DAT hammerspace,
  not only the models: no two playable characters share a file offset in the
  stock game, and the loaders relocate some files in place, so sharing the
  copies made same-template characters invisible (external tool).
* ``routes`` (the derived config, read -> rebuild): the directory's existing
  DAT copies, one ``[offset, length]`` per file (or one per language). They
  are kept byte for byte, so model patches made into the directory survive a
  rebuild. The runner keeps them out of the reset and reserved for the run.

The directory records go to the roster's DOL data section, behind the stock
ones in a moved directory table (``Dol/dirtable.py``); ``dirmap[id]`` points
at the new directory. ``FUN_8036629c`` also treats a directory as a model ID
(> 0x5E selects the Mii format, ``dir - 0x12`` indexes per-model prop rows),
so two hooks map a new directory to its source's first (``revmap``).

Own directories cost DAT space, not RAM: only the models a match loads count
against the memory budget.
"""

import struct

try:
    from ..Dol import dirtable, dolfile, inventory, relocate
    from ..Dol.ppc import one
    from . import dat_hammerspace as dhs
    from . import dol_hammerspace, ids, steps
except ImportError:
    from Dol import dirtable, dolfile, inventory, relocate
    from Dol.ppc import one
    import dat_hammerspace as dhs
    import dol_hammerspace
    import ids
    import steps

REVMAP_GROUP = 'model_dir_revmap'
REVMAP_FORMAT_SITE = 0x80366340     # cmplwi r27,0x5F
REVMAP_ROW_SITE = 0x803663A8        # subi r31,r27,0x12
STATE_KEY = 'model_dirs'            # ctx.state: {id: (directory, source)}
RESERVED_KEY = 'reserved_routes'    # ctx.state: DAT ranges a later step must not allocate (kept routes)


class ModelDirError(ValueError):
    pass


def _hex(cid: int) -> str:
    return f'0x{cid:02X}'


def directory_records(image: dolfile.DolImage, directory: int) -> list[list[int]]:
    """The 12-word records of one directory (bounded as ``dhs.iter_records`` bounds it)."""
    return [list(words) for chunk, _i, _r, words in dhs.iter_records(image) if chunk == directory]


def own_routes(image: dolfile.DolImage) -> list[tuple[int, int]]:
    """Every DAT range (past ``BASE_SIZE``) the image's own model directories route to (the old copies a reset
    leaves behind)."""
    stock_count = inventory.table(dirtable.NAME).rows
    out = set()
    try:
        records = list(dhs.iter_records(image))
    except dirtable.DirTableError:
        return []
    for chunk, _i, _r, words in records:
        if chunk < stock_count:
            continue
        for lang in dhs.LANGS:
            offset, length, _alloc = dhs.slot(words, lang)
            if offset >= dhs.BASE_SIZE and length:
                out.add((offset, length))
    return sorted(out)


def config_routes(config: dict) -> list[tuple[int, int]]:
    """The ``routes`` the config's ``ids[].model`` entries keep (before any step runs)."""
    out = set()
    for c in ids.parse_ids(config) if 'ids' in config else []:
        if c.model and c.model.routes:
            out.update(route for file_routes in c.model.routes for route in file_routes if route[1])
    return sorted(out)


def _check_routes(c: ids.NewId, routes: tuple, count: int, dat, taken: list[tuple[int, int]]) -> None:
    where = f'{_hex(c.id)}: model routes'
    if len(routes) != count:
        raise ModelDirError(f'{where}: {len(routes)} files, but {_hex(c.model.source)}\'s directory has {count}')
    for index, file_routes in enumerate(routes):
        for offset, length in set(file_routes):
            if not length:
                continue
            if offset < dhs.BASE_SIZE or offset % dhs.ALIGN or offset + length > dat.size:
                raise ModelDirError(f'{where}: file {index} at 0x{offset:X}+0x{length:X} is not a hammerspace copy')
            if any(lo < offset + length and offset < lo + n for lo, n in taken):
                raise ModelDirError(f'{where}: file {index} at 0x{offset:X} overlaps a range another directory '
                                    'record routes to')


def _copy_files(c: ids.NewId, source_records: list[list[int]], ctx: steps.RosterContext,
                reserved: list[tuple[int, int]]) -> tuple:
    """Copies of the source's files (input DAT) in DAT hammerspace: routes per file, as ``ModelSpec.routes``."""
    if ctx.input_dat is None:
        raise ModelDirError(f'{_hex(c.id)}: copying a model directory needs 1_Input/dt_na.dat')
    routes = []
    done: dict[tuple[int, int], tuple[int, int]] = {}
    for words in source_records:
        file_routes = []
        for lang in dhs.LANGS:
            offset, length, _alloc = dhs.slot(words, lang)
            if not length:
                file_routes.append((offset, 0))
                continue
            if (offset, length) not in done:
                blob = ctx.input_dat.read(offset, length)
                at = dhs.allocate(ctx.dat, length, reserved)
                ctx.dat.write(at, blob)
                reserved.append((at, length))
                done[(offset, length)] = (at, length)
            file_routes.append(done[(offset, length)])
        routes.append(tuple(file_routes))
    return tuple(routes)


def _revmap_hooks(image: dolfile.DolImage, hs: dol_hammerspace.DolHammerspace, revmap_at: int) -> None:
    hooks = ids.HookBuilder(image, hs)
    stock = {s.address: s.stock for s in inventory.group(REVMAP_GROUP)}
    a = hooks.new_stub()
    a.load_addr('r12', revmap_at).slwi('r0', 'r27', 1).lhzx('r12', 'r12', 'r0')     # (r0 is dead here)
    a.cmplwi('r12', 0x5F).b(REVMAP_FORMAT_SITE + 4)
    image.patch_word(REVMAP_FORMAT_SITE, stock[REVMAP_FORMAT_SITE],
                     one(REVMAP_FORMAT_SITE, lambda b, t=hooks.stub(a): b.b(t)))
    a = hooks.new_stub()
    a.load_addr('r12', revmap_at).slwi('r31', 'r27', 1).lhzx('r31', 'r12', 'r31')   # (r0 is live: and. next)
    a.addi('r31', 'r31', -ids.MODEL_DIR_BASE).b(REVMAP_ROW_SITE + 4)
    image.patch_word(REVMAP_ROW_SITE, stock[REVMAP_ROW_SITE],
                     one(REVMAP_ROW_SITE, lambda b, t=hooks.stub(a): b.b(t)))


def apply_model_dirs(ctx: steps.RosterContext, new: list[ids.NewId]) -> list[str]:
    own = [c for c in new if c.model]
    if not own:
        return ['no new ID has "model": every new ID keeps its template\'s model directory']
    image, hs = ctx.dol, dol_hammerspace.get(ctx)
    if ctx.dat is None:
        raise ModelDirError('own model directories need dt_na.dat')
    vanilla = ctx.input_dol or image
    pointers = dirtable.pointers(image)
    if len(pointers) != inventory.table(dirtable.NAME).rows:
        raise ModelDirError('the directory table is already moved')
    reserved = dhs.routed_ranges(image) + list(ctx.state.get(RESERVED_KEY, []))
    stock_routes = [(o, n) for _c, _i, _r, words in dhs.iter_records(image)
                    for o, n, _a in (dhs.slot(words, lang) for lang in dhs.LANGS) if n]
    dirmap_at = ctx.state['dirmap']
    revmap = list(range(len(pointers)))
    log, made = [], {}
    taken = list(stock_routes)
    for c in own:
        source_dir = c.model.source + ids.MODEL_DIR_BASE
        source_records = directory_records(vanilla, source_dir)
        if not source_records:
            raise ModelDirError(f'{_hex(c.id)}: {_hex(c.model.source)} has no model directory (dir {source_dir})')
        if c.model.routes is not None:
            _check_routes(c, c.model.routes, len(source_records), ctx.dat, taken)
            routes, how = c.model.routes, 'kept'
        else:
            routes, how = _copy_files(c, source_records, ctx, reserved), 'copied'
        taken += [route for file_routes in routes for route in set(file_routes) if route[1]]
        records = bytearray()
        for words, file_routes in zip(source_records, routes):
            words = list(words)
            for lang, (offset, length) in zip(dhs.LANGS, file_routes):
                dhs.set_slot(words, lang, offset, length)
            records += struct.pack('>12I', *words)
        directory = len(pointers)
        pointers.append(hs.data.put(bytes(records), 4))
        revmap.append(source_dir)
        struct.pack_into('>H', hs.data.blob, dirmap_at - hs.data.base + 2 * c.id, directory)
        made[c.id] = (directory, c.model.source)
        size = sum(n for file_routes in routes for _o, n in set(file_routes))
        log.append(f'{_hex(c.id)}: dir {directory}, {len(routes)} files of {_hex(c.model.source)} '
                   f'(dir {source_dir}) {how}, 0x{size:X} bytes')
    at = dirtable.move(image, hs.data.put, pointers, relocate.scan_refs(image))
    revmap_at = hs.data.put(struct.pack(f'>{len(revmap)}H', *revmap), 4)
    _revmap_hooks(image, hs, revmap_at)
    hs.commit()
    ctx.state[STATE_KEY] = made
    log.append(f'directory table moved to 0x{at:08X} ({len(pointers)} directories); revmap at 0x{revmap_at:08X}')
    return log


@steps.register('model_dirs')
def apply(ctx: steps.RosterContext) -> list[str]:
    if 'ids' not in ctx.config:
        return []
    return apply_model_dirs(ctx, ctx.state.get('new_ids') or [])
