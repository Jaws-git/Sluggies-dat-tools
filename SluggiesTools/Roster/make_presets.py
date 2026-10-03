"""Hand-run: write the shipped roster presets into ``1_Input/_RosterConfigurations`` from the stock wheels of
``1_Input/main.dol``. Menu [10] lists every ``.json`` file in that folder (the user's own too, alphabetically);
this script only (re)writes these four:

01_Stock_Roster.json                 the stock roster (no expansion content)
02_Stock_and_Unused.json             + the six unused characters on their family wheels, with their icons
03_Unuseds_and_10_slot_colors.json   + every wheel filled to 10 with open slots
04_all_in_one_12x5_grid.json         + a 12x5 grid whose 19 new squares are open slots

An open slot is a new ID with no content of its own yet: it plays as a template character (the wheel's host on a
wheel, Peach on a new square, the game's own fallback character), shows the built-in "empty slot" icon and the name
"Empty slot". The presets are defaults to reassign, not content (the user's own ``1_Input/roster.json`` is the place
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

WHEEL_SIZE = 10
SQUARE_TEMPLATE = 0x04           # Peach: the character the game substitutes for an ID without own data (0x80367060)
SLOT_NAME = {'en': 'Empty slot', 'fr': 'Emplacement vide', 'sp': 'Espacio vacío'}
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
    entry.update({'icon': 'placeholder', 'name': dict(SLOT_NAME)})
    return entry


def wheel_slots(image: dolfile.DolImage, first: int) -> list[dict]:
    """Open slots that fill every stock wheel (with the unused characters on it) to WHEEL_SIZE."""
    table = inventory.table('selector')
    rows = [bytearray(image.read(table.address + 8 * i, 8)) for i in range(ids.PLAYER_END)]
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
        for swatch in free[:WHEEL_SIZE - len(members[species])]:
            out.append(open_slot(cid, host, host, swatch))
            cid += 1
    return out


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
    slots = wheel_slots(image, ids.FIRST_NEW)
    write('03_Unuseds_and_10_slot_colors.json', preset(
        f'Preset 3: preset 2 plus every wheel filled to {WHEEL_SIZE} with open slots ({len(slots)} new IDs, '
        f'0x{ids.FIRST_NEW:02X}-0x{ids.FIRST_NEW + len(slots) - 1:02X}): each plays as its wheel\'s host, shows the '
        '"empty slot" icon and the name "Empty slot" until you assign it something else.',
        ids=slots, wheels=unused_wheels()))
    first_square = ids.FIRST_NEW + len(slots)
    cols, rows = 12, 5
    count = cols * rows - grid.SQUARE_HEADS
    squares = [open_slot(first_square + k, SQUARE_TEMPLATE, None, None) for k in range(count)]
    write('04_all_in_one_12x5_grid.json', preset(
        f'Preset 4: preset 3 on a {cols}x{rows} grid (Luigi on his own square) whose {count} new squares are open slots '
        f'(0x{first_square:02X}-0x{first_square + count - 1:02X}), each playing as Peach (the game\'s fallback '
        'character) until you assign it something else.',
        ids=slots + squares, wheels=unused_wheels(),
        grid={'shape': [cols, rows], 'squares': [[s['id']] for s in squares]}))
    print(f'presets written to {OUT}: {len(slots)} wheel slots, {count} square slots')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
