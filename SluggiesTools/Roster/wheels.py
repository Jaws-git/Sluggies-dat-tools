"""Colour wheels (plan Phase 4): spare-row variants, wheels for single-variant hosts, up to 10 members.

Config (``wheels`` in the roster preset)::

    "wheels": [
      {"id": "0x47", "wheel": "0x06", "swatch": "black"}
    ]

Each entry makes one of the spare rows 0x47-0x4C (the unused characters,
with model data of their own in dirs 89-94) selectable on a colour wheel:

* ``id``: 0x47-0x4C.
* ``wheel``: the stock player ID whose wheel it joins (default: the row's
  stock host, byte 1). A host without a wheel gets a new wheel group (0x0E
  and up, plan 4b).
* ``swatch``: name or 0-10 (default: the row's stock byte 7).

Optional wheel order (plan 4c)::

    "wheel_order": [["0x06", "0x66", "0x47"]]

Each list names members of one wheel; they come first, in that order, and
the other members follow as before (stock order: stock IDs, then new IDs,
each by ID). A list may name any selectable member of a single species.

With a ``wheels`` key, the config owns all six spare rows: a listed row is
selectable (byte 6 = 1) and has its own character data (``hasmodel`` = 1,
else the game substitutes Peach on the field); an unlisted one gets its
stock row back (not selectable). Without the key, the rows stay as the icon
pipeline left them. New IDs (0x66 and up) join wheels through their own
``ids`` entry (plan Phase 3).

Whatever the config, the step then counts every wheel's members (selectable
rows by species: IDs below 0x4D, which the stock roster builder scans, plus
the new IDs the roster hook appends) and lifts the game's limits as needed:

* up to 6: nothing;
* 7: the two member-list caps (``0x80071ECC``, ``0x804303CC``) become 7;
* 8-10 (plan 4d, port of the external tool's ``wheel7``): both caps 10, 19
  stack buffers grown by 0x10, member index -> popup node remap at 4 sites,
  and popup element 0xB3 of the select layout gets the frames for 7-10
  members (needs the layout in DAT hammerspace, plan Phase 2);
* more than 10: refused (the roster struct holds 10 IDs per species).

A selectable member with swatch 10 also recolours the unused white key of
swatch elements 0xAD/0xAE to orange (plan 4e). The cap words may already be
7 (the icon pipeline's temporary Yoshi fix); 6 and 7 both count as stock.
"""

import struct

try:
    from ..Dol import dolfile, frames, inventory
    from ..Dol.ppc import Asm, one
    from ..Icons import layout2d
    from . import dol_hammerspace, ids, layout_file, steps
except ImportError:
    from Dol import dolfile, frames, inventory
    from Dol.ppc import Asm, one
    from Icons import layout2d
    import dol_hammerspace
    import ids
    import layout_file
    import steps

SPARE_IDS = range(0x47, 0x4D)
MEMBERS = 10                # the roster struct's species lists are 10 wide
STOCK_CAP = 6
CAP_SITES = (0x80071ECC, 0x804303CC)        # cmpwi cr1,r28 / r24,0x6 in the two member-list builders
GROW = 0x10
# (function start, last instruction, buffer start, buffer end = first offset that moves), from the
# external tool's wheel7.FRAMES; a function with two buffers is listed twice, the higher buffer first.
# The result is checked against the tool's words in the site inventory (group ``wheel7``).
FRAMES = ((0x8007569C, 0x80075788, 0x08, 0x20), (0x800721A0, 0x800722E8, 0x08, 0x20),
          (0x80073958, 0x80075068, 0x48, 0x68), (0x80073958, 0x80075068, 0x30, 0x48),
          (0x80431DF0, 0x80431FDC, 0x08, 0x20), (0x8042C8A4, 0x8042EFD0, 0x28, 0x40),
          (0x8006C044, 0x8006C39C, 0x08, 0x24), (0x8006C44C, 0x8006C56C, 0x10, 0x2C),
          (0x8006D3C0, 0x8006DA10, 0x10, 0x34), (0x8006E7D8, 0x8006E85C, 0x08, 0x28),
          (0x800751DC, 0x80075698, 0x08, 0x24), (0x8007578C, 0x80075C7C, 0x08, 0x2C),
          (0x80077054, 0x80077424, 0x20, 0x3C), (0x8042AF3C, 0x8042B404, 0x10, 0x2C),
          (0x8042BD2C, 0x8042BF98, 0x08, 0x28), (0x8042C048, 0x8042C168, 0x10, 0x2C),
          (0x8042C180, 0x8042C200, 0x08, 0x28), (0x8042FEEC, 0x80430180, 0x08, 0x24),
          (0x804305E8, 0x8043081C, 0x08, 0x2C))
NODE_SITES = (0x8006C4B4, 0x8006C51C, 0x8042C0B0, 0x8042C118)   # stb r27,off(r4): member index -> node
SEVENTH_NODE = 9            # nodes 6-8 are the end caps and the pane: members 6.. use nodes 9..
POPUP = 0xB3
POPUP_NODES = 9
STOCK_LAST = 4              # the popup element's last time (6 members); time = count - 2
SWATCH_ELEMENTS = (0xAD, 0xAE)
ORANGE_TIME = 10
ORANGE = bytes.fromhex('ff8000ff')


# Rows 0x47-0x4C of the stock selector table (US main.dol): group, host, species, 0, 0, slot,
# selectable 0, swatch. The table in the DOL may carry the icon pipeline's edits, so these are kept here.
STOCK_SPARE_ROWS = {
    0x47: bytes.fromhex('0b06060000060005'),   # Black Yoshi
    0x48: bytes.fromhex('0b06060000070009'),   # White Yoshi
    0x49: bytes.fromhex('020d0d0000050005'),   # Black Toad
    0x4A: bytes.fromhex('0515150000030005'),   # Black Pianta
    0x4B: bytes.fromhex('0a3a240000040005'),   # Black Kritter
    0x4C: bytes.fromhex('010c0c0000020005'),   # Black Koopa
}


class WheelConfigError(ValueError):
    pass


# --------------------------------------------------------------------------
# Config and selector rows
# --------------------------------------------------------------------------

def parse_wheels(config: dict) -> dict[int, tuple[int | None, int | None]] | None:
    """``{spare id: (wheel host or None, swatch or None)}``, or None without a ``wheels`` key."""
    if 'wheels' not in config:
        return None
    out = {}
    for n, entry in enumerate(config.get('wheels') or []):
        where = f'wheels[{n}]'
        if 'id' not in entry:
            raise WheelConfigError(f'{where}: no id')
        cid = ids._number(entry['id'], f'{where}.id')
        if cid not in SPARE_IDS:
            raise WheelConfigError(f'{where}: 0x{cid:02X} is not a spare row (0x47-0x4C); new IDs join a wheel '
                                   'through their "ids" entry')
        if cid in out:
            raise WheelConfigError(f'{where}: 0x{cid:02X} is listed twice')
        wheel = entry.get('wheel')
        if wheel is not None:
            wheel = ids._number(wheel, f'{where}.wheel')
            if not 0 <= wheel < ids.PLAYER_END or wheel in SPARE_IDS:
                raise WheelConfigError(f'{where}: wheel 0x{wheel:02X} is not a stock player ID')
        swatch = entry.get('swatch')
        if isinstance(swatch, str) and swatch.lower() in ids.SWATCHES:
            swatch = ids.SWATCHES[swatch.lower()]
        elif swatch is not None:
            swatch = ids._number(swatch, f'{where}.swatch')
            if not 0 <= swatch <= 10:
                raise WheelConfigError(f'{where}: swatch {swatch} is outside 0-10')
        out[cid] = (wheel, swatch)
    return out


def table_location(ctx: steps.RosterContext, name: str) -> tuple[int, int]:
    """``(address, rows)`` of a per-ID table: where the ids step put it, else its stock place."""
    moved = ctx.state.get('tables', {})
    if name in moved:
        return moved[name]
    table = inventory.table(name)
    return table.address + table.header, table.rows


def spare_rows(rows: list[bytearray], spares: dict, log: list[str]) -> list[int]:
    """Write the spare rows into ``rows`` (the whole selector table); returns the listed IDs."""
    stock = STOCK_SPARE_ROWS
    for cid in SPARE_IDS:
        rows[cid][:] = stock[cid]
    next_group = max(r[0] for r in rows) + 1
    for cid in SPARE_IDS:
        if cid not in spares:
            continue
        wheel, swatch = spares[cid]
        host = rows[stock[cid][1] if wheel is None else wheel]
        if host[0] == 0:
            host[0] = next_group
            log.append(f'0x{host[1]:02X} gets wheel group 0x{next_group:02X}')
            next_group += 1
        row = bytearray(stock[cid])
        row[0:3] = host[0:3]
        row[3] = 0
        shown = [r[5] for i, r in enumerate(rows) if i != cid and r[2] == row[2] and r[6] and
                 (i < ids.PLAYER_END or i >= ids.FIRST_NEW)]
        row[5] = 1 + max(shown or [-1])        # unread by the game (wheel order is ID order)
        row[6] = 1
        if swatch is not None:
            row[7] = swatch
        rows[cid][:] = row
    return sorted(spares)


def wheel_members(rows: list[bytes]) -> dict[int, list[int]]:
    """Members per species: selectable IDs the roster builder lists (below 0x4D) plus new IDs (0x66 and up)."""
    out: dict[int, list[int]] = {}
    for cid, row in enumerate(rows):
        if row[6] and (cid < ids.PLAYER_END or ids.FIRST_NEW <= cid <= ids.MAX_ID):
            out.setdefault(row[2], []).append(cid)
    return out


# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------

def set_caps(image: dolfile.DolImage, value: int) -> list[str]:
    log = []
    for address in CAP_SITES:
        stock = inventory.site('wheel7', address).stock
        word = image.u32(address)
        if word & 0xFFFF0000 != stock & 0xFFFF0000 or word & 0xFFFF not in (STOCK_CAP, STOCK_CAP + 1):
            raise dolfile.DolError(f'member-list cap 0x{address:08X} is {word:08X}, expected a cap of 6 or 7')
        image.write_word(address, (stock & 0xFFFF0000) | value)
        log.append(f'0x{address:08X}: cap {word & 0xFFFF} -> {value}')
    return log


def node_stub(address: int, stock: int, base: int) -> bytes:
    """The replaced ``stb r27,off(r4)``, with member index >= 6 stored as node 9 + (index - 6)."""
    a = Asm(base)
    a.mr('r0', 'r27').cmpwi('r27', STOCK_CAP, cr=1).blt('store', cr=1)
    a.addi('r0', 'r27', SEVENTH_NODE - STOCK_CAP)
    a.label('store')
    a.stb('r0', stock & 0xFFFF, 'r4')
    a.b(address + 4)
    return a.assemble()


def ten_member_code(image: dolfile.DolImage, hs: dol_hammerspace.DolHammerspace) -> list[str]:
    """Plan 4d in the DOL: caps 10, the stack buffers, the node remap."""
    sites = inventory.group('wheel7')
    others = [s for s in sites if s.address not in CAP_SITES]
    bad = inventory.stock_mismatches(image, others)
    if bad:
        raise dolfile.DolError('10-member wheels: code is not stock: ' + '; '.join(bad[:4]))
    log = set_caps(image, MEMBERS)
    grown = 0
    for start, last, buffer, threshold in FRAMES:
        if (threshold - buffer + GROW) // 4 < MEMBERS:
            raise dolfile.DolError(f'0x{start:08X}: buffer at 0x{buffer:X} would still be too small')
        grown += len(frames.grow_frame(image, start, last, threshold, GROW))
    for address in NODE_SITES:
        stock = inventory.site('wheel7', address).stock
        hs.code.put(b'', 4)
        at = hs.code.put(node_stub(address, stock, hs.code.here), 4)
        image.patch_word(address, stock, one(address, lambda a: a.b(at)))
    wrong = [f'0x{s.address:08X}' for s in sites if s.tool is not None and image.u32(s.address) != s.tool]
    if wrong:
        raise dolfile.DolError('10-member wheels: result differs from the external tool at ' + ', '.join(wrong[:8]))
    log.append(f'{len(FRAMES)} stack buffers +0x{GROW:X} ({grown} words); member index 6.. -> node '
               f'{SEVENTH_NODE}.. at {len(NODE_SITES)} sites')
    return log


# --------------------------------------------------------------------------
# Layout (element 0xB3: the wheel popup; 0xAD/0xAE: the swatch colours)
# --------------------------------------------------------------------------

def swatch_x(count: int, member: int) -> int:
    """x of a member's swatch with ``count`` members (stock spacing: 10 px, centred)."""
    return -5 * (count - 1) + 10 * member


def edge(count: int) -> int:
    """End caps' x and the pane's half-width (stock: 20 at 2 members .. 40 at 6)."""
    return 5 * (count - 1) + 15


def _keys(node: bytes) -> tuple[int, list[bytearray]]:
    count, size = struct.unpack_from('>HH', node, 0)
    keys = [bytearray(node[4 + j * size:4 + (j + 1) * size]) for j in range(count)]
    if 4 + count * size != len(node):
        raise layout2d.Layout2dError('popup node keys are not all the same size')
    return size, keys


def _set_x(key: bytearray, x: int) -> None:
    for offset in (0x08, 0x18, 0x1C):
        struct.pack_into('>h', key, offset, x)


def _time_key(model: bytearray, time: int) -> bytearray:
    key = bytearray(model)
    struct.pack_into('>H', key, 2, time)
    if len(key) == 0x3C:                     # an element key repeats its time at +0x28
        if struct.unpack_from('>H', key, 0x28)[0] != STOCK_LAST:
            raise layout2d.Layout2dError('unexpected popup element key')
        struct.pack_into('>H', key, 0x28, time)
    return key


def popup_nodes(nodes: list[bytes]) -> list[bytes]:
    """Popup nodes with keys for 7..10 members on every node, plus nodes 9.. (members 6..)."""
    if len(nodes) != POPUP_NODES:
        raise layout2d.Layout2dError(f'popup element has {len(nodes)} nodes, expected {POPUP_NODES}')
    new_times = list(range(MEMBERS - 2, STOCK_LAST, -1))        # highest first, like the stock keys
    out = []
    for index, node in enumerate(nodes):
        size, keys = _keys(node)
        if struct.unpack_from('>H', keys[0], 2)[0] != STOCK_LAST or keys[0][0] & 1:
            raise layout2d.Layout2dError(f'popup node {index}: unexpected first key')
        added = []
        for time in new_times:
            count = time + 2
            key = _time_key(keys[0], time)
            if index < 6:
                _set_x(key, swatch_x(count, index))
            elif index in (6, 7):
                _set_x(key, edge(count) if index == 6 else -edge(count))
            else:                                                  # the pane: its quad's half-width
                if size != 0x58:
                    raise layout2d.Layout2dError('popup pane key is not 0x58 bytes')
                for offset in range(0x28, 0x48, 4):
                    v = struct.unpack_from('>f', key, offset)[0]
                    if abs(v) == 40.0:
                        struct.pack_into('>f', key, offset, float(edge(count)) if v > 0 else -float(edge(count)))
            key[0] |= 1
            added.append(key)
        added[0][0] &= ~1
        keys[0][0] |= 1
        out.append(struct.pack('>HH', len(keys) + len(added), size) + b''.join(bytes(k) for k in added + keys))
    size, keys = _keys(nodes[5])
    model = keys[0]                                                 # node 5's only key (time 4)
    for member in range(STOCK_CAP, MEMBERS):                        # nodes 9..: members 6..
        added = []
        for time in range(MEMBERS - 2, member - 2, -1):             # counts member + 1 .. 10
            key = _time_key(model, time)
            _set_x(key, swatch_x(time + 2, member))
            key[0] |= 1
            added.append(key)
        added[0][0] &= ~1
        out.append(struct.pack('>HH', len(added), size) + b''.join(bytes(k) for k in added))
    return out


def popup_layout(data: bytes) -> bytes:
    lay = layout2d.Layout(data)
    info = bytearray(lay.info_block(POPUP))
    if struct.unpack_from('>H', info, 4)[0] != STOCK_LAST:
        raise layout2d.Layout2dError(f'popup element 0x{POPUP:X}: last time is not {STOCK_LAST}')
    struct.pack_into('>H', info, 4, MEMBERS - 2)
    lay.set_nodes(POPUP, popup_nodes(lay.node_blobs(POPUP)), bytes(info))
    return lay.to_bytes()


def orange_swatch(data: bytes) -> bytes:
    lay = layout2d.Layout(data)
    for element in SWATCH_ELEMENTS:
        nodes = lay.node_blobs(element)
        hits = [(n, offset, record) for n, node in enumerate(nodes) for offset, record in layout2d.node_records(node)
                if len(record) >= 0x50 and record[4] == 4 and struct.unpack_from('>H', record, 2)[0] == ORANGE_TIME]
        if len(hits) != 1:
            raise layout2d.Layout2dError(f'swatch element 0x{element:X}: {len(hits)} keys at time {ORANGE_TIME}')
        n, offset, _record = hits[0]
        node = bytearray(nodes[n])
        node[offset + 0x40:offset + 0x50] = ORANGE * 4
        nodes[n] = bytes(node)
        lay.set_nodes(element, nodes)
    return lay.to_bytes()


# --------------------------------------------------------------------------
# Wheel order (plan 4c)
# --------------------------------------------------------------------------

ROSTER_SITE = 0x8006BD58        # mr r3,r31 at the end of the roster builder FUN_8006ba6c (r31 = roster X)
ROSTER_COUNTS, ROSTER_LISTS = 0x4D, 0x76    # X + 0x4D + species: count; X + 0x76 + species * 10: IDs


def parse_order(config: dict, members: dict[int, list[int]]) -> list[tuple[int, list[int]]]:
    """``[(species, ids in order)]`` from ``wheel_order``; every ID must be a member of that one wheel."""
    species_of = {cid: s for s, ids_ in members.items() for cid in ids_}
    out, seen = [], set()
    for n, entry in enumerate(config.get('wheel_order') or []):
        where = f'wheel_order[{n}]'
        order = [ids._number(v, f'{where}') for v in entry]
        if not order:
            continue
        unknown = [f'0x{c:02X}' for c in order if c not in species_of]
        if unknown:
            raise WheelConfigError(f'{where}: not a selectable wheel member: {", ".join(unknown)}')
        species = {species_of[c] for c in order}
        if len(species) != 1:
            raise WheelConfigError(f'{where}: the IDs belong to different wheels')
        species = species.pop()
        if species in seen or len(set(order)) != len(order):
            raise WheelConfigError(f'{where}: a wheel or an ID is listed twice')
        seen.add(species)
        out.append((species, order))
    return out


def order_table(order: list[tuple[int, list[int]]]) -> bytes:
    """Per wheel: species byte, the IDs, 0xFF; then a final 0xFF."""
    return b''.join(bytes([s, *ids_, 0xFF]) for s, ids_ in order) + bytes([0xFF])


def emit_reorder(a: Asm, table: int, scratch: int) -> Asm:
    """Port of the external tool's ``grid_order.family_reorder``: each listed wheel's species list in the roster
    struct becomes the listed IDs in that order, then the rest as they were. Uses r0, r4-r11; r31 = X."""
    a.load_addr('r6', table).load_addr('r11', scratch)
    a.label('fam')
    a.lbz('r0', 0, 'r6').cmplwi('r0', 0xFF).beq('fams_done')
    a.add('r10', 'r31', 'r0').lbz('r4', ROSTER_COUNTS, 'r10')               # r4 = count
    a.mulli('r5', 'r0', 10).add('r5', 'r31', 'r5').addi('r5', 'r5', ROSTER_LISTS)
    a.li('r7', 0)
    a.label('copy')                                                         # scratch = the list
    a.cmpw('r7', 'r4').bge('copied')
    a.lbzx('r0', 'r5', 'r7').stbx('r0', 'r11', 'r7').addi('r7', 'r7', 1).b('copy')
    a.label('copied')
    a.li('r8', 0)                                                           # r8 = the next slot written
    a.label('want')
    a.addi('r6', 'r6', 1).lbz('r9', 0, 'r6').cmplwi('r9', 0xFF).beq('rest')
    a.li('r7', 0)
    a.label('find')
    a.cmpw('r7', 'r4').bge('want')
    a.lbzx('r0', 'r11', 'r7').cmpw('r0', 'r9').bne('find_next')
    a.stbx('r9', 'r5', 'r8').addi('r8', 'r8', 1)
    a.li('r0', ids.STOCK_IDS).stbx('r0', 'r11', 'r7').b('want')             # taken (0x65: no character)
    a.label('find_next')
    a.addi('r7', 'r7', 1).b('find')
    a.label('rest')                                                         # the rest, as they were
    a.li('r7', 0)
    a.label('rest_loop')
    a.cmpw('r7', 'r4').bge('fam_next')
    a.lbzx('r0', 'r11', 'r7').cmplwi('r0', ids.STOCK_IDS).beq('rest_next')
    a.stbx('r0', 'r5', 'r8').addi('r8', 'r8', 1)
    a.label('rest_next')
    a.addi('r7', 'r7', 1).b('rest_loop')
    a.label('fam_next')
    a.addi('r6', 'r6', 1).b('fam')
    a.label('fams_done')
    return a


def install_reorder(image: dolfile.DolImage, hs: dol_hammerspace.DolHammerspace,
                    order: list[tuple[int, list[int]]]) -> list[str]:
    """Run the reorder at the end of the roster builder, after the ids step's new-ID hook if it is there."""
    stock = inventory.site('roster_hook', ROSTER_SITE).stock
    table = hs.data.put(order_table(order), 4)
    scratch = hs.data.put(bytes(MEMBERS), 4)
    hs.code.put(b'', 4)
    a = emit_reorder(Asm(hs.code.here), table, scratch)
    a.mr('r3', 'r31').b(ROSTER_SITE + 4)
    stub = hs.code.put(a.assemble(), 4)
    word = image.u32(ROSTER_SITE)
    if word == stock:
        image.write_word(ROSTER_SITE, one(ROSTER_SITE, lambda b: b.b(stub)))
        where = f'0x{ROSTER_SITE:08X}'
    else:
        if word >> 26 != 18 or word & 3:
            raise dolfile.DolError(f'0x{ROSTER_SITE:08X} is {word:08X}: neither stock nor a hook branch')
        target = (ROSTER_SITE + ((word & 0x03FFFFFC) ^ 0x02000000) - 0x02000000) & 0xFFFFFFFF
        code = hs.code

        def u32(at):
            return int.from_bytes(code.blob[at - code.base:at - code.base + 4], 'big')
        tails = [at for at in range(target, min(target + 0x400, stub), 4)
                 if u32(at) == stock and u32(at + 4) == one(at + 4, lambda b: b.b(ROSTER_SITE + 4))]
        if len(tails) != 1:
            raise dolfile.DolError('the roster hook at 0x{:08X} has no unique end to chain to'.format(ROSTER_SITE))
        # inside our own text section: through the allocator, which ``commit`` writes back
        hs.code.write(tails[0], one(tails[0], lambda b: b.b(stub)).to_bytes(4, 'big'))
        where = f'the new-ID roster hook (0x{tails[0]:08X})'
    return [f'wheel order after {where}: ' + '; '.join(
        f'species 0x{s:02X}: ' + ' '.join(f'0x{c:02X}' for c in o) for s, o in order)]


# --------------------------------------------------------------------------
# Step
# --------------------------------------------------------------------------

def has_ten_members(image: dolfile.DolImage) -> bool:
    """Whether the 10-member code (plan 4d) is in: both member-list caps are 10."""
    return all(image.u32(address) & 0xFFFF == MEMBERS for address in CAP_SITES)


def lift_to_ten(ctx: steps.RosterContext) -> list[str]:
    """Plan 4d: the 10-member code in the DOL and the popup frames in the layout."""
    hs = dol_hammerspace.get(ctx)
    log = ten_member_code(ctx.dol, hs)
    hs.commit()
    return log + layout_file.get(ctx).update(lambda _lang, data: popup_layout(data))


@steps.register('wheels')
def apply(ctx: steps.RosterContext) -> list[str]:
    spares = parse_wheels(ctx.config)
    if spares is None and 'ids' not in ctx.config:
        return ['no "wheels" or "ids" key in the roster config: wheels stay as they are']
    address, count = table_location(ctx, 'selector')
    rows = [bytearray(ctx.dol.read(address + 8 * i, 8)) for i in range(count)]
    log: list[str] = []
    # The ids step may have moved the tables into our DOL data section: write through the hammerspace.
    hs = dol_hammerspace.DolHammerspace.open(ctx.dol) and dol_hammerspace.get(ctx)
    write = hs.write if hs else ctx.dol.write
    if spares is not None:
        listed = spare_rows(rows, spares, log)
        has_at, _n = table_location(ctx, 'hasmodel')
        for cid in SPARE_IDS:
            write(has_at + cid, bytes([1 if cid in spares else 0]))
        log.append('spare rows: ' + (', '.join(f'0x{c:02X} -> wheel 0x{rows[c][1]:02X} swatch {rows[c][7]}'
                                               for c in listed) or 'none selectable'))
    for cid, row in enumerate(rows):
        write(address + 8 * cid, bytes(row))

    members = wheel_members(rows)
    largest = max(len(m) for m in members.values())
    full = {s: m for s, m in members.items() if len(m) > MEMBERS}
    if full:
        raise WheelConfigError('more than 10 members on a wheel: ' + '; '.join(
            f'species 0x{s:02X}: ' + ' '.join(f'0x{c:02X}' for c in m) for s, m in full.items()))
    big = ', '.join(f'species 0x{s:02X} ({len(m)})' for s, m in sorted(members.items()) if len(m) > STOCK_CAP)
    if largest > STOCK_CAP + 1:
        log += lift_to_ten(ctx)
        log.append(f'wheels of up to {MEMBERS} members: {big}')
    elif largest == STOCK_CAP + 1:
        log += set_caps(ctx.dol, STOCK_CAP + 1)
        log.append(f'wheels of 7 members: {big}')
    else:
        log.append('every wheel has 6 members or fewer: caps unchanged')
    order = parse_order(ctx.config, members)
    if order:
        hs = dol_hammerspace.get(ctx)
        log += install_reorder(ctx.dol, hs, order)
        hs.commit()
    if any(row[6] and row[7] == ORANGE_TIME for cid, row in enumerate(rows)
           if cid < ids.PLAYER_END or cid >= ids.FIRST_NEW):
        log += layout_file.get(ctx).update(lambda _lang, data: orange_swatch(data))
        log.append(f'swatch {ORANGE_TIME} recoloured orange')
    return log

