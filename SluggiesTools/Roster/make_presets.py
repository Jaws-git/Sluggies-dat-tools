"""Hand-run: write the shipped roster presets into ``1_Input/_RosterConfigurations`` from the stock wheels of
``1_Input/main.dol``. Menu [7] lists every ``.json`` file in that folder (the user's own too, alphabetically);
this script only (re)writes these six:

01_Stock_Roster.json                 the stock roster (no expansion content)
02_Stock_and_Unused.json             + the six unused characters on their family wheels, with their icons
03_Unuseds_and_8_color_slots.json    + every wheel filled to 8 with open slots, and a wheel of 3-4 for every
                                       character without one
04_Unuseds_and_10_color_slots.json   + every wheel filled to 10 with open slots, and a wheel of 3 for every
                                       character without one
05_extra _columns_12x4_grid.json     8-wide wheels on a 12x4 grid: the rest of the IDs are spread evenly over the
                                       characters without a wheel and the 7 new squares
06_maximum_12x5_grid.json            04 + a 12x5 grid whose 19 new squares are open slots

It also draws the "empty slot" portrait into ``1_Input/_Icons/empty_slot_side.png`` / ``empty_slot_front.png``
(only when missing, so an edited one stays).

An open slot is a new ID with no content of its own yet: it plays as a template character (the wheel's host on a
wheel, Peach on a new square, the game's own fallback character), shows the "empty slot" icon and the name
"Empty slot". The 153 new IDs (0x66-0xFE) do not reach 10 for every character: 30 stock characters have no wheel,
so they get wheels of 3 (60 IDs) next to the 63 that fill the existing wheels, and preset 4's squares take 19 more. The presets are defaults to reassign, not content (the user's own ``1_Input/roster.json`` is the place
for that). Swatches: the colours (0-10) the wheel does not use yet, lowest first.
"""

import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
OUT = os.path.join(ROOT, '1_Input', '_RosterConfigurations')
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from SluggiesTools.Dol import dolfile, inventory  # noqa: E402
from SluggiesTools.Roster import grid, ids, wheels  # noqa: E402
from SluggiesTools.Roster.open_slot import (  # noqa: E402
    ICON_DIR, ICON_SIZE, SLOT_ICON, SLOT_NAME, slot_portrait, write_slot_icons)

WHEEL_SIZE = 10
NEW_WHEEL_SIZE = 3               # characters without a wheel (the ID budget, see above)
WHEEL_SIZE_8 = 8                 # presets 3 and 5: stock wheels hold up to 8
BIGGER_NEW_WHEELS = 22           # preset 3: the first 22 characters without a wheel (by ID) get 4, not 3
SQUARE_TEMPLATE = 0x04           # Peach: the character the game substitutes for an ID without own data (0x80367060)
SWATCH_COUNT = 11
UNUSED = [  # the six unused characters (spare rows) with the icon art shipped in 1_Input/_Icons
    ('0x47', '0x06', 'black', 'Black Yoshi', 'black_yoshi'),
    ('0x48', '0x06', 'white', 'White Yoshi', 'white_yoshi'),
    ('0x49', '0x0D', 'black', 'Black Toad', 'black_toad'),
    ('0x4A', '0x15', 'black', 'Black Pianta', 'black_pianta'),
    ('0x4B', '0x3A', 'black', 'Black Kritter', 'black_kritter'),
    ('0x4C', '0x0C', 'black', 'Black Koopa', 'black_koopa'),
]


def unused_wheels() -> list[dict]:
    return [{'id': cid, 'wheel': wheel, 'swatch': swatch, 'comment': comment,
             'icon': {'side': f'{art}_side.png', 'front': f'{art}_front.png'}}
            for cid, wheel, swatch, comment, art in UNUSED]


def open_slot(cid: int, template: int, wheel, swatch: int | None) -> dict:
    entry = {'id': f'0x{cid:02X}', 'template': f'0x{template:02X}', 'wheel': None if wheel is None else f'0x{wheel:02X}'}
    if swatch is not None:
        entry['swatch'] = swatch
    entry.update({'icon': dict(SLOT_ICON), 'name': dict(SLOT_NAME)})
    return entry


def selector_rows(image: dolfile.DolImage) -> list[bytearray]:
    table = inventory.table('selector')
    return [bytearray(image.read(table.address + 8 * i, 8)) for i in range(ids.PLAYER_END)]


def wheelless(rows) -> list[int]:
    """Stock characters without a wheel (each hosts a new one), by ID."""
    return [h for h, r in enumerate(rows[:wheels.SPARE_IDS.start]) if r[6] and not r[0]]


def wheel_slots(image: dolfile.DolImage, first: int, wheel_size: int = WHEEL_SIZE, new_sizes=None) -> list[dict]:
    """Open slots that fill every stock wheel (with the unused characters on it) to WHEEL_SIZE, then a wheel of
    NEW_WHEEL_SIZE for every character without one (it hosts the new wheel)."""
    rows = selector_rows(image)
    spares = {int(cid, 16): (int(wheel, 16), ids.SWATCHES[swatch]) for cid, wheel, swatch, _c, _a in UNUSED}
    members: dict[int, list[tuple[int, int]]] = {}          # species -> [(id, swatch)]
    for cid, row in enumerate(rows):
        if row[6] and row[0]:
            members.setdefault(row[2], []).append((cid, row[7]))
    for cid, (wheel, swatch) in spares.items():
        members.setdefault(rows[wheel][2], []).append((cid, swatch))
    heads = image.read(grid.HEAD_LIST, grid.SQUARE_HEADS)
    out, cid = [], first
    for species in sorted(members):
        host = heads[species]
        used = {swatch for _c, swatch in members[species]}
        free = [s for s in range(SWATCH_COUNT) if s not in used]
        for swatch in free[:wheel_size - len(members[species])]:
            out.append(open_slot(cid, host, host, swatch))
            cid += 1
    for n, host in enumerate(wheelless(rows)):
        free = [s for s in range(SWATCH_COUNT) if s != rows[host][7]]
        size = NEW_WHEEL_SIZE if new_sizes is None else new_sizes[n]
        for swatch in free[:size - 1]:
            out.append(open_slot(cid, host, host, swatch))
            cid += 1
    return out


def even_grid_slots(image: dolfile.DolImage, first: int, cols: int, rows_: int):
    """Preset 5: stock wheels to 8, then every remaining ID spread evenly (by total members) over the characters
    without a wheel and the new squares. Returns (ids, squares, sizes)."""
    rows = selector_rows(image)
    hosts = wheelless(rows)
    base = wheel_slots(image, first, WHEEL_SIZE_8, [1] * len(hosts))
    sizes = [1] * len(hosts) + [0] * (cols * rows_ - grid.SQUARE_HEADS)
    for _ in range(ids.MAX_ID - first + 1 - len(base)):
        i = min(range(len(sizes)), key=lambda k: (sizes[k], k) if k < len(hosts) else (sizes[k], k - 1000))
        sizes[i] += 1
    out, cid = list(base), first + len(base)
    for host, size in zip(hosts, sizes):
        free = [x for x in range(SWATCH_COUNT) if x != rows[host][7]]
        for swatch in free[:size - 1]:
            out.append(open_slot(cid, host, host, swatch))
            cid += 1
    squares = []
    for size in sizes[len(hosts):]:
        members = []
        for _ in range(size):
            out.append(open_slot(cid, SQUARE_TEMPLATE, None, None))
            members.append(out[-1]['id'])
            cid += 1
        squares.append(members)
    return out, squares, sizes


def preset(comment: str, **keys) -> dict:
    out = {'version': 1, 'comment': comment}
    out.update(keys)
    return out


def write(name: str, data: dict) -> None:
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write('\n')


def main() -> int:
    with open(os.path.join(ROOT, '1_Input', 'main.dol'), 'rb') as f:
        image = dolfile.DolImage(f.read())
    write('01_Stock_Roster.json', preset('Preset 1: the stock roster. No expansion content; the DOL hammerspace sections are '
                                 'still added (empty).'))
    write('02_Stock_and_Unused.json', preset('Preset 2: the stock roster plus the six unused characters on their families\' '
                                  'wheels (Yoshi gets 8), with their icons from 1_Input/_Icons.',
                                  wheels=unused_wheels()))
    slot_text = ('each plays as its wheel\'s host, shows the "empty slot" icon and the name "Empty slot" until you '
                 'assign it something else.')
    hosts = len(wheelless(selector_rows(image)))
    slots8 = wheel_slots(image, ids.FIRST_NEW, WHEEL_SIZE_8,
                         [4 if n < BIGGER_NEW_WHEELS else 3 for n in range(hosts)])
    write('03_Unuseds_and_8_color_slots.json', preset(
        f'Preset 3: preset 2 plus every wheel filled to {WHEEL_SIZE_8}; the IDs that frees go to characters without a '
        f'wheel ({BIGGER_NEW_WHEELS} of the {hosts} get a wheel of 4, the first {BIGGER_NEW_WHEELS} by ID; the other '
        f'{hosts - BIGGER_NEW_WHEELS} get 3). {len(slots8)} new IDs, 0x{ids.FIRST_NEW:02X}-'
        f'0x{ids.FIRST_NEW + len(slots8) - 1:02X}: ' + slot_text,
        ids=slots8, wheels=unused_wheels()))
    slots = wheel_slots(image, ids.FIRST_NEW)
    write('04_Unuseds_and_10_color_slots.json', preset(
        f'Preset 4: preset 2 plus every wheel filled to {WHEEL_SIZE} and a wheel of {NEW_WHEEL_SIZE} for every '
        f'character without one ({len(slots)} new IDs, 0x{ids.FIRST_NEW:02X}-0x{ids.FIRST_NEW + len(slots) - 1:02X}): '
        + slot_text, ids=slots, wheels=unused_wheels()))
    cols, rows = 12, 4
    out, squares, sizes = even_grid_slots(image, ids.FIRST_NEW, cols, rows)
    write('05_extra _columns_12x4_grid.json', preset(
        f'Preset 5: {cols}x{rows} grid. Stock wheels (incl. the six unused characters) hold up to {WHEEL_SIZE_8} '
        f'members; the remaining IDs are spread evenly over the {hosts} characters without a wheel and the '
        f'{len(squares)} new squares ({min(sizes)}-{max(sizes)} members each). All {len(out)} new IDs '
        f'(0x{ids.FIRST_NEW:02X}-0x{ids.MAX_ID:02X}) are open slots: ' + slot_text,
        ids=out, wheels=unused_wheels(), grid={'shape': [cols, rows], 'squares': squares}))
    first_square = ids.FIRST_NEW + len(slots)
    cols, rows = 12, 5
    count = cols * rows - grid.SQUARE_HEADS
    sq = [open_slot(first_square + k, SQUARE_TEMPLATE, None, None) for k in range(count)]
    write('06_maximum_12x5_grid.json', preset(
        f'Preset 6: preset 4 on a {cols}x{rows} grid (Luigi on his own square) whose {count} new squares are open slots '
        f'(0x{first_square:02X}-0x{first_square + count - 1:02X}), each playing as Peach (the game\'s fallback '
        'character) until you assign it something else.',
        ids=slots + sq, wheels=unused_wheels(),
        grid={'shape': [cols, rows], 'squares': [[s['id']] for s in sq]}))
    icons = write_slot_icons()
    print(f'presets written to {OUT}: {len(slots)} wheel slots, {count} square slots'
          + (f'; {", ".join(icons)} drawn into {ICON_DIR}' if icons else ''))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
