"""Exhibition draft grid (plan Phase 6): 11 or 12 columns, 4 or 5 rows, new squares.

Port of the external tool's ``gridcells`` (plan section 2.2 D). Only the
exhibition team draft (screen A, layout element ``0xBA``) grows; the other
grid screen (Toy Field and friends, element ``0x97``) keeps its 41 squares,
although its objects grow too, because the grid widget code is shared.

Config (``grid`` in the roster preset; without it, or with ``null``, the grid stays stock;
``{}`` is the stock squares at 11x4 with Luigi on his own square)::

    "grid": {
      "squares": [["0x66", "0x67"], ["0x47"]],
      "shape": [12, 5],
      "order": ["0x06", "0x11", ...]
    }

* ``squares`` (optional): new squares, each a list of 1-10 character IDs
  (new IDs, spare rows or stock non-head IDs), the first one shown on the
  square, or ``{"members": [...], "voice": "0xNN"}``. On this screen a
  square's members leave their template's wheel and form the square's own
  wheel. More than 6 on a square needs the 10-member wheel code (plan 4d),
  which the step installs if the wheels step has not. ``voice``: a stock
  player ID (0x00-0x4C) whose voice the square's square-only new IDs speak
  with: their selector byte 2 (species) becomes that ID's
  (``apply_voices``). Other members keep their own species, which their
  wheels need.
* ``shape`` (optional): ``[columns, rows]``, one of 11x4, 12x4, 10x5,
  11x5, 12x5; default the smallest that holds the 41 stock squares (the 40
  stock ones plus Luigi's own) and the new ones. Leftover cells are empty:
  hidden, skipped by the D-pad and the pointer.
* ``order`` (optional): every cell in reading order, a flat list or one
  list per row: a stock square by its head's character ID (``0x00`` Mario,
  ``0x01`` Luigi, ...), a new square by its first member, ``null`` for an
  empty cell; cells after the list are empty. Default: the stock 10x4 block
  in columns 1-10 (0-9 at 10 columns), then the new squares and Luigi in the
  free cells: column 0 top to bottom, column 11 top to bottom, row 5 left to
  right.

What changes (letters as in the tool and plan section 2.2 D):

* a. the layout (each language's copy, via ``layout_file``): element 0xBA
  rebuilt with columns x rows nodes at the stock 49 px pitch, right edge at
  the stock column 9; 5 rows re-spaced with the team bars moved apart; past
  10 columns the team bars stretch, the roster slots spread and team 0's
  banner moves; the screen's elements shift right (20 px at 11 columns, 40
  at 12); empty cells' nodes move off screen;
* b. the square counts; c. the grid widget's per-square arrays (moved behind
  both screens' objects, which grow); d. the square -> head map and the
  per-head arrays (moved, filled from one table), the captain swap removed
  (Luigi has his own square); d2. a pick marks its square "decided" with
  flag 2; e. heads 43 and up for the new squares (the head list moves and
  grows; the member-list builder ``FUN_80071bb0`` lists a square's own
  members); f. 191 cursor-position constants; g. the D-pad's column divide,
  bounds and wraps; i. the random-team pools walk the new heads; j. empty
  cells.

The result for the stock squares at 11x4 matches the tool's words in the
site inventory (group ``gridcells_11x4``).
"""

import struct
from dataclasses import dataclass

try:
    from ..Dol import dolfile, inventory, relocate
    from ..Dol.ppc import Asm, ha, lo, one
    from ..Icons import layout2d
    from . import dol_hammerspace, ids, layout_file, steps, voices, wheels
except ImportError:
    from Dol import dolfile, inventory, relocate
    from Dol.ppc import Asm, ha, lo, one
    from Icons import layout2d
    import dol_hammerspace
    import ids
    import layout_file
    import steps
    import voices
    import wheels

GROUP = 'gridcells_11x4'
STOCK_SQUARES, STOCK_COLS, STOCK_ROWS = 40, 10, 4
STOCK_HEADS = 43                 # 41 squares' heads (species 0x00-0x28) + the two Mii groups
SQUARE_HEADS = 41
LUIGI_HEAD = 1
SHAPES = ((11, 4), (12, 4), (10, 5), (11, 5), (12, 5))   # smallest first
MAX_CELLS = 60
SQUARE_MAX = 10
SQUARE_STOCK_MAX = 6             # members per wheel without the 10-member code

# d. screen object: square -> head map (60), per-head flag and wheel index (64 heads each)
MAP, FLAGS, SEL = 0x67C, 0x6BC, 0x6FC
# c. grid widget in this / the other screen's object; its per-square arrays move to grid + GRID_ARRAYS
GRID_A, GRID_B = 0x338, 0x2A0
GRID_ARRAYS, CAP = 0x430, 72
G_FLAGS, G_W6C, G_W110, G_PAIRS = GRID_ARRAYS, GRID_ARRAYS + CAP, GRID_ARRAYS + 5 * CAP, GRID_ARRAYS + 9 * CAP
OBJ_SIZE = GRID_A + G_PAIRS + 8 * CAP        # 0xC30
OBJ_B_SIZE = GRID_B + G_PAIRS + 8 * CAP      # 0xB98
OBJ_ALLOC = 0x802C8CFC                       # li r3,0x67c before FUN_8006e9c4
OBJ_B_ALLOCS = (0x8038DF98, 0x80435E08, 0x804EA464)   # li r3,0x6cc before FUN_8042c424
STOCK_MAP = 0x80623458                       # 40 heads, copied by FUN_8006e9c4
HEAD_LIST = 0x80631878                       # head -> character ID (43)
MAP_COPY = (0x8006EB74, 0x8006EB80, 0x8006EB78)   # lis / addi of the map source, li r0,5 (8 bytes a pass)

MAP_SITES = (0x8006C240, 0x8006D6B0, 0x8006E7CC, 0x8006EB9C, 0x8006EBA8, 0x8006EBB0, 0x8006EBB8,
             0x8006EBC0, 0x8006EBC8, 0x8006EBD0, 0x8006EBDC, 0x8006EC38, 0x8006EC88, 0x80073C44,
             0x80073D7C, 0x800748F8, 0x800755A4, 0x80075BF8, 0x800771FC)
CAPTAIN_SWAP_STORE = 0x8006ECF4              # stb r0,0x238(r3): the square nearest the centre := Luigi
HEAD_SITES = (0x8006C260, 0x8006D6CC, 0x8006D704, 0x8006E820, 0x8006E884, 0x8006EAB0, 0x8006ED10,
              0x8006ED14, 0x8006ED18, 0x8006ED1C, 0x8006ED20, 0x8006ED24, 0x8006ED28, 0x8006ED2C,
              0x8006ED30, 0x8006ED34, 0x8006ED38, 0x8006ED3C, 0x8006ED40, 0x8006ED44, 0x8006ED48,
              0x8006ED4C, 0x8006ED5C, 0x8006ED6C, 0x8007215C, 0x80073DA4, 0x80073E88, 0x80073ED0,
              0x80074920, 0x80074978, 0x80075634, 0x8007566C, 0x80075678, 0x80075C14, 0x80075C64,
              0x80077218)
GRID_SITES = (0x800669F0, 0x80066A00, 0x80066A08, 0x80066A18, 0x80066A48, 0x80066A5C, 0x80066A64,
              0x80066A6C, 0x80066A7C, 0x80066A84, 0x80066B70, 0x80066C04, 0x80066CF0, 0x80066D3C,
              0x80066DF4, 0x80066EC0, 0x80066FFC, 0x800672A0, 0x800672B4, 0x800672C4, 0x800672F4,
              0x8006749C, 0x800674D0, 0x800675A8, 0x8006769C, 0x800676C8, 0x800678D8,
              0x80067C60, 0x80067C64, 0x80067C68, 0x80067C6C, 0x80067C70, 0x80067C74, 0x80067C78,
              0x80067C7C, 0x80067C80, 0x80067C84, 0x80067C88,
              0x80067D24, 0x80067D28, 0x80067D2C, 0x80067D30, 0x80067D34, 0x80067D38, 0x80067D3C,
              0x80067D40, 0x80067D44, 0x80067D48, 0x80067D4C, 0x80067D50, 0x80067D54, 0x80067D58,
              0x80067D5C, 0x80067D60, 0x80067D64, 0x80067D68, 0x80067D6C, 0x80067D70, 0x80067D74,
              0x80067D78, 0x80067D7C, 0x80067D80, 0x80067D84, 0x80067D88, 0x80067D8C, 0x80067D90,
              0x80067D94, 0x80067D98, 0x80067D9C, 0x80067DA0, 0x80067DA4, 0x80067DA8, 0x80067DAC,
              0x80067DB0, 0x80067DB4, 0x80067DB8, 0x80067DC0, 0x80067DC4,
              0x80067DDC, 0x80067DE8, 0x80067DF4, 0x80067E00, 0x80067E04,
              0x800688C0, 0x800688C8, 0x800688CC)
GRID_LOOPS = (0x80066990, 0x80066A28, 0x80067748, 0x80067B6C, 0x80067BE4)   # li / cmpwi 0x29
GRID_INIT_PASSES = 0x80067CDC                # li r0,5: 8 squares a pass, plus one after the loop

# b. FUN_80067ef4's square counts
COUNT_LOOP_A, COUNT_LOOP_B = 0x800680B0, 0x80068750
COUNT_DC = ((0x800681A0, 0x800681A8), (0x800682B4, 0x800682BC), (0x800683D8, 0x800683E0),
            (0x80068504, 0x8006850C), (0x80068624, 0x8006862C))

# f. cursor positions: 0-3 specials, 4-0x15 rosters, 0x16.. the grid, then 10 Mii rows and 2 buttons
POSITION_SITES = (
    0x8006C0C4, 0x8006C0CC, 0x8006C0E0, 0x8006C13C, 0x8006C150, 0x8006C158,
    0x8006D190, 0x8006D198, 0x8006D1AC, 0x8006D214, 0x8006D228, 0x8006D230,
    0x8006D480, 0x8006D488, 0x8006D49C, 0x8006D504, 0x8006D520, 0x8006D744, 0x8006D850, 0x8006D858,
    0x8006D86C, 0x8006D8D4, 0x8006D8F0,
    0x8006F48C, 0x8006F598, 0x8006F5A0, 0x8006F5B4, 0x8006FCD0, 0x8006FCE8, 0x80070554, 0x80070568,
    0x800705CC, 0x800705D4, 0x800705E8, 0x80070774, 0x80070798, 0x800707F8, 0x8007080C, 0x80070870,
    0x80070878, 0x8007088C, 0x8007095C, 0x80070964, 0x8007096C, 0x80070984, 0x8007098C, 0x80070998,
    0x80070A08, 0x80070A1C, 0x80070A80, 0x80070A88, 0x80070A9C,
    0x80073094, 0x8007309C, 0x800730B0, 0x8007310C, 0x80073120, 0x80073154, 0x80073190, 0x80073198,
    0x8007355C, 0x80073564, 0x8007356C, 0x80073584, 0x8007358C, 0x800735DC, 0x800735E4, 0x800735F8,
    0x80073654, 0x80073668, 0x80073670,
    0x800739AC, 0x800739B4, 0x800739C8, 0x80073A2C, 0x80073A48, 0x80073C08, 0x80073C24, 0x80073C70,
    0x8007404C, 0x80074134, 0x800744D8, 0x800744E0, 0x800744F4, 0x80074558, 0x80074574, 0x80074A8C,
    0x800752DC, 0x800752E4, 0x800752F8, 0x80075354, 0x80075368, 0x80075370, 0x8007540C, 0x80075414,
    0x80075428, 0x80075484, 0x80075498, 0x800754A0,
    0x800757EC, 0x800757F4, 0x80075808, 0x8007586C, 0x80075888, 0x80075AC0, 0x80075AC8, 0x80075ADC,
    0x80075B40, 0x80075B5C,
    0x80075CC0, 0x80075CD0, 0x80075CE0, 0x80075CF0, 0x80075D20,
    0x80075DF8, 0x80075E00, 0x80075E14, 0x80075E70, 0x80075E84, 0x8007605C, 0x800760F4, 0x8007610C,
    0x80076118, 0x80076120, 0x8007623C, 0x8007624C,
    0x800762D0, 0x800762D8, 0x800762EC, 0x80076348, 0x8007635C, 0x80076538, 0x800765D4, 0x800765EC,
    0x800765F8, 0x80076600, 0x80076724, 0x80076734,
    0x80076880, 0x80076888, 0x8007689C, 0x800768F8, 0x8007690C, 0x80076B00, 0x80076B3C, 0x80076B4C,
    0x80076B94, 0x80076BA4, 0x80076BB8, 0x80076BD4, 0x80076BE4, 0x80076C2C, 0x80076C3C, 0x80076C50,
    0x80076C68, 0x80076C74, 0x80076C8C, 0x80076C9C, 0x80076CB0, 0x80076CC0, 0x80076D1C, 0x80076D2C,
    0x80076D40, 0x80076D50, 0x80076D5C, 0x80076D98, 0x80076DA8, 0x80076DBC, 0x80076DCC, 0x80076DE0,
    0x80076DF0, 0x80076E04, 0x80076E14, 0x80076E60, 0x80076E70, 0x80076E98, 0x80076EBC, 0x80076ECC,
    0x80076EE0, 0x80076EF0, 0x80076F18, 0x80076F3C, 0x80076F4C, 0x80077014, 0x80077024,
    0x800770C4, 0x800770CC, 0x800770E0, 0x80077144, 0x80077160, 0x8007727C,
)
POSITION_IMMS = {0x27, 0x28} | set(range(0x3D, 0x4B))
GRID_FIRST_POSITION = 0x16

# g. D-pad: row = (pos - 0x16) / 10 (lis 0x6666; addi 0x6667; mulhw; srawi 2, twice per site)
DIVIDE = {10: (0x6666, 0x6667, 2), 11: (0x2E8C, -0x5D17, 1), 12: (0x2AAB, -0x5555, 1)}
DIV_SITES = ((0x80075F90, 0x80075F98, 0x80075FA0, 0x80075FA4),     # up
             (0x80076468, 0x80076470, 0x80076478, 0x8007647C),     # down
             (0x800769B8, 0x800769C0, 0x800769C8, 0x800769D0))     # left / right
TIMES_10 = (0x80075FB8, 0x8007603C, 0x80076490, 0x80076518, 0x800769D8, 0x80076ACC, 0x80076AE0,
            0x80075D54, 0x80076F88)                               # mulli rX,rY,0xa
COLUMN_BOUNDS = (0x800769F0, 0x80076AC0)                           # cmpwi r25,0xa
LAST_COLUMN = (0x80076AB8, 0x80076F68)                             # li 9: left wrap / from specials
UP_WRAP = 0x80076030                                               # addi r0,r26,0x34: row 3
ENTRY_BOTTOM_ROW = 0x80075D50       # FUN_80075c80: rlwinm r0,r0,0,30,31 (-(bottom) & 3 = row 3)
DOWN_ROWS = 0x80076498              # FUN_8007627c: cmpwi r0,4

# i. random team pools
RANDOM_HEAD_BOUND = 0x800722CC      # FUN_800721a0: cmpwi r22,0x29 (heads 0..40)
RANDOM_POOL_ALLOC = 0x80072310      # FUN_800722ec: li r3,0x52 (41 u16 pool entries)
POOL_HEAD_BOUNDS = ((0x80431F08, 'r21', 'r26'), (0x80431F94, 'r20', 'r16'))   # FUN_80431df0

# e. hooks
MEMBERS_FN = 0x80071BB0             # FUN_80071bb0(obj, player, id, keep_id, out): the wheel holding id
AVAILABLE = 0x80071ABC              # FUN_80071abc(obj, handle): not on a team, not held, unlocked
MEMBER_TAKE = 0x80071EAC            # cmpwi cr1,r29,0: add family member r26 to the wheel?
MEMBER_NEXT = 0x80071EE0
HAS_MEMBERS = 0x8006E868            # lbz r3,0x5a(r3): provider "head has members", r4 = head
DECIDE_FN, DECIDE_CALL = 0x800688B4, 0x80073DF4
CURSOR_FAMILY = 0x80073C34          # lbz r3,0x2(r3): family of id (r4 = id * 8)

# j. empty cells
HIT_CALL, HIT_FN = 0x8006F4A0, 0x8006887C
DPAD_CALLS = ((0x80070184, 0x80075DA4, None), (0x800701DC, 0x8007627C, None),
              (0x80070238, 0x80076828, -1), (0x80070294, 0x80076828, 1))
CURSOR = 0x2D8                      # obj + player * 4: the player's cursor position

# a. layout
GRID_ELEMENT = 0xBA
SQUARE_W = 49
CHILD_X = (0x08, 0x18, 0x1C)        # s16 x in a key record (three copies)
CHILD_Y = (0x0A, 0x1A, 0x1E)
ROW_Y0, ROW_PITCH = 105, 48         # 5 rows
TOP_GROUP, TOP_DY = (0x9A, 0xB9, 0xBD, 0xB5), -28
BOTTOM_GROUP, BOTTOM_DY = (0x9B, 0xB8, 0xBE, 0xB4), 3
HIDE_DX = -2000
BARS = ((0x9A, 0x9C, 0xB9), (0x9B, 0x9D, 0xB8))   # (position, three-part bar, roster slots): top, bottom
BAR_MIDDLE_PX = 60
SLOT_FIRST, SLOT_LAST = 47, 471
BANNER, BANNER_X, BANNER_SCALE = 0xA0, -26, 1.5
SCREEN_DX = 40                      # at 12 columns; half at 11
SCREEN_ELEMENTS = (GRID_ELEMENT, 0x9A, 0x9B, 0xB8, 0xB9, 0xA0, 0xA1, 0xBD, 0xBE,
                   0x1A, 0x69, 0xB4, 0xB5, 0xB6, 0xBB, 0xBC)


class GridConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Grid:
    """``cells``: per cell in reading order, ``('stock', head)``, ``('square', k)`` or None (empty)."""
    cols: int
    rows: int
    cells: tuple
    squares: tuple                  # tuple of member-ID tuples, k = index
    voices: tuple = ()              # per square: the voice's character ID or None (empty: no voices)

    def voice(self, k: int) -> int | None:
        return self.voices[k] if self.voices else None

    @property
    def size(self) -> int:
        return self.cols * self.rows

    @property
    def empty(self) -> list[int]:
        return [i for i, c in enumerate(self.cells) if c is None]

    def heads(self) -> bytes:
        """Square -> head map: stock heads, squares' heads 43.. (square order), empty cells' heads after."""
        out, empty_head = [], STOCK_HEADS + len(self.squares)
        for cell in self.cells:
            if cell is None:
                out.append(empty_head)
                empty_head += 1
            else:
                out.append(cell[1] if cell[0] == 'stock' else STOCK_HEADS + cell[1])
        return bytes(out)

    @property
    def first_empty_head(self) -> int:
        return STOCK_HEADS + len(self.squares)


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def _id(value, where: str) -> int:
    try:
        return ids._number(value, where)
    except ValueError as exc:
        raise GridConfigError(str(exc)) from exc


def default_cells(cols: int, rows: int, stock_map: bytes, squares: int) -> list:
    """The stock 10x4 block in columns 1-10 (0-9 at 10 columns), then the squares and Luigi in the free cells:
    column 0 top to bottom, the last column top to bottom, the bottom row left to right."""
    cells = [None] * (cols * rows)
    first = 1 if cols > STOCK_COLS else 0
    for r in range(STOCK_ROWS):
        for c in range(STOCK_COLS):
            cells[r * cols + first + c] = ('stock', stock_map[r * STOCK_COLS + c])

    def rank(i):
        row, col = divmod(i, cols)
        return (0, row, col) if col == 0 else (1, row, col) if col == cols - 1 else (2, row, col)
    free = sorted((i for i, c in enumerate(cells) if c is None), key=rank)
    for i, cell in zip(free, [('square', k) for k in range(squares)] + [('stock', LUIGI_HEAD)]):
        cells[i] = cell
    return cells


def parse_grid(config: dict, stock_heads: bytes, stock_map: bytes) -> Grid | None:
    """The grid from the ``grid`` key (None without one, or for null). ``stock_heads``: the 41 squares' head characters."""
    cfg = config.get('grid')
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise GridConfigError('"grid" must be an object')
    squares, voices = [], []
    for k, sq in enumerate(cfg.get('squares') or []):
        voice = None
        if isinstance(sq, dict):
            unknown = set(sq) - {'members', 'voice'}
            if unknown:
                raise GridConfigError(f'grid.squares[{k}]: unknown keys ' + ', '.join(sorted(unknown)))
            if sq.get('voice') is not None:
                voice = _id(sq['voice'], f'grid.squares[{k}].voice')
                if not 0 <= voice < ids.PLAYER_END:
                    raise GridConfigError(f'grid.squares[{k}].voice: 0x{voice:02X} is not a stock player ID '
                                          '(0x00-0x4C)')
            sq = sq.get('members')
        voices.append(voice)
        if not isinstance(sq, list) or not 1 <= len(sq) <= SQUARE_MAX:
            raise GridConfigError(f'grid.squares[{k}] must list 1-{SQUARE_MAX} character IDs')
        members = tuple(_id(m, f'grid.squares[{k}]') for m in sq)
        for cid in members:
            if cid in stock_heads:
                raise GridConfigError(f'grid.squares[{k}]: 0x{cid:02X} is a stock square\'s head character')
            if not (cid < ids.PLAYER_END or ids.FIRST_NEW <= cid <= ids.MAX_ID):
                raise GridConfigError(f'grid.squares[{k}]: 0x{cid:02X} is not a player ID')
        squares.append(members)
    flat = [c for sq in squares for c in sq]
    if len(set(flat)) != len(flat):
        raise GridConfigError('grid.squares: a character is on two squares')

    needed = SQUARE_HEADS + len(squares)
    if cfg.get('shape') is not None:
        shape = tuple(cfg['shape'])
        if shape not in SHAPES:
            raise GridConfigError(f'grid.shape {list(shape)} is not one of '
                                  + ', '.join(f'{c}x{r}' for c, r in SHAPES))
    else:
        shape = next((s for s in SHAPES if s[0] * s[1] >= needed), None)
    if shape is None or shape[0] * shape[1] < needed:
        raise GridConfigError(f'{needed} squares do not fit ' + (f'{shape[0]}x{shape[1]}' if shape else
                              f'the largest grid ({MAX_CELLS})'))
    cols, rows = shape

    order = cfg.get('order')
    if order is None:
        cells = default_cells(cols, rows, stock_map, len(squares))
    else:
        if order and all(isinstance(r, list) for r in order):
            order = [x for r in order for x in r]
        if len(order) > cols * rows:
            raise GridConfigError(f'grid.order has {len(order)} cells, the grid {cols * rows}')
        firsts = {sq[0]: k for k, sq in enumerate(squares)}
        cells = []
        for n, entry in enumerate(order):
            if entry is None:
                cells.append(None)
                continue
            cid = _id(entry, f'grid.order[{n}]')
            if cid in firsts:
                cells.append(('square', firsts[cid]))
            elif cid in stock_heads:
                cells.append(('stock', stock_heads.index(cid)))
            else:
                raise GridConfigError(f'grid.order[{n}]: 0x{cid:02X} is neither a stock square\'s head '
                                      'character nor a new square\'s first member')
        cells += [None] * (cols * rows - len(cells))
        listed = [c for c in cells if c is not None]
        if len(set(listed)) != len(listed):
            raise GridConfigError('grid.order: a square is listed twice')
        missing = ([f'0x{stock_heads[h]:02X}' for h in range(SQUARE_HEADS) if ('stock', h) not in listed]
                   + [f'square 0x{sq[0]:02X}' for k, sq in enumerate(squares) if ('square', k) not in listed])
        if missing:
            raise GridConfigError('grid.order leaves out ' + ', '.join(missing))
    return Grid(cols, rows, tuple(cells), tuple(squares), tuple(voices) if any(v is not None for v in voices) else ())


# --------------------------------------------------------------------------
# DOL
# --------------------------------------------------------------------------

# Sites the tool leaves alone at 4 rows (so not in the inventory's 11x4 trace): their stock words.
ROW_SITES_STOCK = {ENTRY_BOTTOM_ROW: 0x540007BE, DOWN_ROWS: 0x2C000004}


def _stock(address: int) -> int:
    if address in ROW_SITES_STOCK:
        return ROW_SITES_STOCK[address]
    return inventory.site(GROUP, address).stock


def _patch(image: dolfile.DolImage, address: int, new: int) -> None:
    image.patch_word(address, _stock(address), new)


def _imm(image: dolfile.DolImage, address: int, old: int, new: int) -> None:
    word = image.u32(address)
    if word != _stock(address) or word & 0xFFFF != old & 0xFFFF:
        raise dolfile.DolError(f'0x{address:08X}: {word:08X}, expected the stock word with immediate 0x{old & 0xFFFF:X}')
    image.write_word(address, (word & 0xFFFF0000) | (new & 0xFFFF))


def _remap(image: dolfile.DolImage, sites, ranges) -> None:
    """Each load / store's displacement d in [start, end) becomes base + (d - start)."""
    for address in sites:
        word = image.u32(address)
        if word != _stock(address):
            raise dolfile.DolError(f'0x{address:08X}: {word:08X} is not the stock word')
        d = word & 0xFFFF
        new = [base + d - start for start, end, base in ranges if start <= d < end]
        if len(new) != 1 or word >> 26 not in (32, 34, 36, 38):
            raise dolfile.DolError(f'0x{address:08X}: {word:08X} is not a remappable load / store')
        image.write_word(address, (word & 0xFFFF0000) | new[0])


def _hook(hs: dol_hammerspace.DolHammerspace, build) -> int:
    """Assemble ``build(asm)`` at the code section's next word; returns its address."""
    hs.code.put(b'', 4)
    a = Asm(hs.code.here)
    build(a)
    return hs.code.put(a.assemble(), 4)


def _branch_to(image: dolfile.DolImage, site: int, target: int, link: bool = False) -> None:
    image.patch_word(site, _stock(site), one(site, lambda a: a.b(target, link)))


def counts(image: dolfile.DolImage, size: int) -> None:
    """b. FUN_80067ef4 builds ``size`` squares on this screen (41 on the other) and gives the widgets that count."""
    extra = size - 0x29
    _patch(image, COUNT_LOOP_A, (7 << 26) | (3 << 21) | (22 << 16) | (-extra & 0xFFFF))   # mulli r3,r22,-extra
    _patch(image, COUNT_LOOP_B, 0x38000000 | size)                                       # li r0,size
    for rl, add in COUNT_DC:
        word = image.u32(rl)
        if word != _stock(rl) or word & 0xFFE0FFFF != 0x54000FFE:
            raise dolfile.DolError(f'0x{rl:08X}: {word:08X}')
        image.write_word(rl, (7 << 26) | (((word >> 16) & 0x1F) << 21) | extra)          # mulli rX,r0,extra
        _imm(image, add, 0x28, size)


def widget_arrays(image: dolfile.DolImage, size: int) -> None:
    """c. The grid widget's per-square arrays behind both screens' objects."""
    _remap(image, GRID_SITES, ((0x40, 0x6C, G_FLAGS), (0x6C, 0x110, G_W6C), (0x110, 0x1B4, G_W110),
                               (0x1B4, 0x2FC, G_PAIRS)))
    for address in GRID_LOOPS:
        _imm(image, address, 0x29, size)
    passes = (size - 1 + 7) // 8
    if not size <= 8 * passes + 1 <= CAP:
        raise dolfile.DolError(f'{size} squares do not fit the moved arrays')
    _patch(image, GRID_INIT_PASSES, 0x38000000 | passes)
    _patch(image, OBJ_ALLOC, 0x38600000 | OBJ_SIZE)
    for address in OBJ_B_ALLOCS:
        _patch(image, address, 0x38600000 | OBJ_B_SIZE)


def head_map(image: dolfile.DolImage, hs: dol_hammerspace.DolHammerspace, grid: Grid) -> int:
    """d. The square -> head map and the per-head arrays move into the object, filled from one table; the
    captain swap goes. Returns the table's address."""
    _remap(image, MAP_SITES, ((0x222, 0x223, MAP - GRID_FIRST_POSITION), (0x238, 0x240, MAP)))
    _remap(image, HEAD_SITES, ((0x260, 0x289, FLAGS), (0x289, 0x2B1, SEL)))
    heads = grid.heads()
    table = heads + b'\xFF' * (FLAGS - MAP - len(heads)) + bytes(SEL + 0x40 - FLAGS)
    at = hs.data.put(table, 4)
    lis, addi, passes = MAP_COPY
    _patch(image, lis, 0x3C600000 | ha(at))
    _patch(image, addi, 0x38630000 | lo(at))
    _patch(image, passes, 0x38000000 | len(table) // 8)
    _patch(image, CAPTAIN_SWAP_STORE, 0x60000000)
    return at


def decided_flag(image: dolfile.DolImage, hs: dol_hammerspace.DolHammerspace) -> None:
    """d2. This screen's pick sets its square's "decided" flag to 2 (stock: 1 forever)."""
    def build(a):
        a.add('r7', 'r3', 'r4').li('r0', 2).stb('r0', G_FLAGS, 'r7')
        a.slwi('r4', 'r4', 2).add('r3', 'r3', 'r4')
        a.stw('r5', G_W6C, 'r3').stw('r6', G_W110, 'r3').blr()
    _branch_to(image, DECIDE_CALL, _hook(hs, build), link=True)


def square_wheels(image: dolfile.DolImage, hs: dol_hammerspace.DolHammerspace, grid: Grid, cap: int,
                  refs) -> tuple[int, int]:
    """e. Heads 43..: the head list moves and grows; the member-list builder lists a square's own members; square
    characters leave their template's wheel; "head has members" for heads >= 43; the cursor finds a square
    character's own square. Returns (head list address, relocated words)."""
    table = inventory.table('head_list')
    stock_heads = image.read(HEAD_LIST, STOCK_HEADS)
    heads = stock_heads + bytes(sq[0] for sq in grid.squares) + stock_heads[:1] * len(grid.empty)
    head_list = hs.data.put(heads, 4)
    changes = relocate.relocate_table(image, table.all_pairs, HEAD_LIST, STOCK_HEADS, head_list, refs)
    square_ids = [c for sq in grid.squares for c in sq]

    def has_members(a):                                # heads >= 43 (squares, empty cells) have members
        a.cmpwi('r4', STOCK_HEADS).bge('single')
        a.lbz('r3', 0x5A, 'r3').b(HAS_MEMBERS + 4)
        a.label('single')
        a.li('r3', 1).b(HAS_MEMBERS + 4)
    _branch_to(image, HAS_MEMBERS, _hook(hs, has_members))
    if not grid.squares:
        return head_list, len(changes)
    lists = hs.data.put(b''.join(bytes(sq) + b'\xFF' * (cap + 2 - len(sq)) for sq in grid.squares), 4)

    def members(a):
        for k, sq in enumerate(grid.squares):          # r8 = the square's member list, or the stock path
            for cid in sq:
                a.cmpwi('r5', cid).beq(f'sq{k}')
        a.word(_stock(MEMBERS_FN)).b(MEMBERS_FN + 4)   # stwu r1,-0x80(r1): stock
        for k in range(len(grid.squares)):
            a.label(f'sq{k}')
            a.load_addr('r8', lists + k * (cap + 2)).b('wheel')
        a.label('wheel')
        a.stwu('r1', -0x40, 'r1').mflr('r0').stw('r0', 0x44, 'r1')
        for r in range(25, 32):
            a.stw(f'r{r}', 0x20 + 4 * (r - 25), 'r1')
        a.mr('r31', 'r3').mr('r30', 'r5').mr('r29', 'r6').mr('r28', 'r7').mr('r27', 'r8').li('r26', 0)
        a.label('member')
        a.lbz('r25', 0, 'r27').cmplwi('r25', 0xFF).beq('done')
        a.sth('r25', 8, 'r1').li('r0', 0).stw('r0', 0xC, 'r1').stw('r0', 0x10, 'r1')   # handle {id, 0, 0}
        a.mr('r3', 'r31').addi('r4', 'r1', 8).bl(AVAILABLE)
        a.cmpwi('r3', 0).bne('take')
        a.cmpw('r25', 'r30').bne('next').cmpwi('r29', 0).beq('next')
        a.label('take')
        a.cmpwi('r28', 0).beq('count').cmpwi('r26', cap).bge('count')
        a.slwi('r0', 'r26', 2).word((31 << 26) | (25 << 21) | (28 << 16) | (0 << 11) | (151 << 1))  # stwx r25,r28,r0
        a.label('count')
        a.addi('r26', 'r26', 1)
        a.label('next')
        a.addi('r27', 'r27', 1).b('member')
        a.label('done')
        a.mr('r3', 'r26')
        for r in range(25, 32):
            a.lwz(f'r{r}', 0x20 + 4 * (r - 25), 'r1')
        a.lwz('r0', 0x44, 'r1').mtlr('r0').addi('r1', 'r1', 0x40).blr()
    _branch_to(image, MEMBERS_FN, _hook(hs, members))

    def take(a):                                       # square characters are not on their template's wheel
        for cid in square_ids:
            a.cmpwi('r26', cid).beq('skip')
        a.cmpwi('r29', 0, cr=1).b(MEMBER_TAKE + 4)
        a.label('skip')
        a.b(MEMBER_NEXT)
    _branch_to(image, MEMBER_TAKE, _hook(hs, take))

    def cursor(a):                                     # the cursor goes to a square character's square
        a.lbz('r3', 2, 'r3')
        for k, sq in enumerate(grid.squares):
            for cid in sq:
                a.cmpwi('r4', cid * 8).bne(f'not_{cid}').li('r3', STOCK_HEADS + k)
                a.label(f'not_{cid}')
        a.b(CURSOR_FAMILY + 4)
    _branch_to(image, CURSOR_FAMILY, _hook(hs, cursor))
    return head_list, len(changes)


def positions(image: dolfile.DolImage, delta: int) -> None:
    """f. Every cursor position past the grid moves up by the grid's growth."""
    for address in POSITION_SITES:
        word = image.u32(address)
        if word != _stock(address):
            raise dolfile.DolError(f'0x{address:08X}: {word:08X} is not the stock word')
        imm = word & 0xFFFF
        imm = imm - 0x10000 if imm & 0x8000 else imm
        if abs(imm) not in POSITION_IMMS:
            raise dolfile.DolError(f'0x{address:08X}: {word:08X} has no cursor-position immediate')
        new = imm - delta if imm < 0 else imm + delta             # subi: raise the magnitude
        image.write_word(address, (word & 0xFFFF0000) | (new & 0xFFFF))


def dpad(image: dolfile.DolImage, cols: int, rows: int) -> None:
    """g. The D-pad's row / column arithmetic for ``cols`` x ``rows``."""
    hi, low, shift = DIVIDE[cols]
    for lis, addi, sr1, sr2 in DIV_SITES:
        _imm(image, lis, 0x6666, hi)
        _imm(image, addi, 0x6667, low)
        for sr in (sr1, sr2):
            word = image.u32(sr)
            if word != _stock(sr) or word & 0xFC0007FE != 0x7C000670 or (word >> 11) & 31 != 2:
                raise dolfile.DolError(f'0x{sr:08X}: {word:08X} is not srawi _,_,2')
            image.write_word(sr, (word & ~(31 << 11)) | (shift << 11))
    for address in TIMES_10 + COLUMN_BOUNDS:
        _imm(image, address, 10, cols)
    for address in LAST_COLUMN:
        _imm(image, address, 9, cols - 1)
    _imm(image, UP_WRAP, GRID_FIRST_POSITION + 30, GRID_FIRST_POSITION + (rows - 1) * cols)
    if rows != STOCK_ROWS:
        _patch(image, ENTRY_BOTTOM_ROW, (7 << 26) | (-(rows - 1) & 0xFFFF))   # mulli r0,r0,-(rows - 1)
    _imm(image, DOWN_ROWS, STOCK_ROWS, rows)


def random_pools(image: dolfile.DolImage, hs: dol_hammerspace.DolHammerspace, grid: Grid) -> None:
    """i. The random-team pools walk heads 43.. (the squares) after 0..40, skipping the Mii groups."""
    first_empty = grid.first_empty_head

    def walk(count, ptr, site):
        def build(a):
            a.cmpwi(count, SQUARE_HEADS).bne('bound')
            a.li(count, STOCK_HEADS).addi(ptr, ptr, STOCK_HEADS - SQUARE_HEADS)
            a.label('bound')
            a.cmpwi(count, first_empty).b(site + 4)      # then blt: next head
        return build
    _branch_to(image, RANDOM_HEAD_BOUND, _hook(hs, walk('r22', 'r29', RANDOM_HEAD_BOUND)))
    _patch(image, RANDOM_POOL_ALLOC, 0x38600000 | (2 * (SQUARE_HEADS + len(grid.squares)) + 7) & ~7)
    for site, count, ptr in POOL_HEAD_BOUNDS:
        _branch_to(image, site, _hook(hs, walk(count, ptr, site)))


def empty_cells(image: dolfile.DolImage, hs: dol_hammerspace.DolHammerspace, grid: Grid) -> None:
    """j. The pointer reports no square for an empty cell; a D-pad move onto one goes on in the same direction
    (at most 2 x (columns + rows) moves, else the cursor stays where it was)."""
    first_empty, size = grid.first_empty_head, grid.size
    steps_max = 2 * (grid.cols + grid.rows)

    def empty_square(a, pos, out):                      # branch to out unless cursor position pos is empty
        a.addi(pos, pos, -GRID_FIRST_POSITION).cmplwi(pos, size).bge(out)
        a.add(pos, pos, 'r30').lbz(pos, MAP, pos).cmplwi(pos, first_empty).blt(out)

    if image.u32(HIT_CALL - 8) != 0x387E0338:
        raise dolfile.DolError('pointer call: r3 is not r30 + 0x338')

    def hit(a):
        a.stwu('r1', -0x10, 'r1').mflr('r0').stw('r0', 0x14, 'r1')
        a.bl(HIT_FN)
        a.cmpwi('r3', 0).blt('out')
        a.addi('r12', 'r3', GRID_FIRST_POSITION)
        empty_square(a, 'r12', 'out')
        a.li('r3', -1)
        a.label('out')
        a.lwz('r0', 0x14, 'r1').mtlr('r0').addi('r1', 'r1', 0x10).blr()
    _branch_to(image, HIT_CALL, _hook(hs, hit), link=True)

    for call, fn, step in DPAD_CALLS:
        setup = (0x7FC3F378, 0x7EC4B378) + (() if step is None else (0x38A00000 | (step & 0xFFFF),))
        got = tuple(image.u32(call - 4 * len(setup) + 4 * i) for i in range(len(setup)))
        if got != setup:
            raise dolfile.DolError(f'0x{call:08X}: the D-pad call\'s arguments are not stock')

        def move(a, fn=fn, step=step):
            a.stwu('r1', -0x20, 'r1').mflr('r0').stw('r0', 0x24, 'r1').stw('r31', 0x1C, 'r1')
            a.slwi('r31', 'r22', 2).add('r31', 'r31', 'r30')               # r31 = obj + player * 4
            a.lwz('r0', CURSOR, 'r31').stw('r0', 8, 'r1')                  # where the cursor was
            a.li('r0', steps_max).stw('r0', 0xC, 'r1')
            a.label('move')
            a.mr('r3', 'r30').mr('r4', 'r22')
            if step is not None:
                a.li('r5', step)
            a.bl(fn)
            a.cmpwi('r3', 0).beq('out')
            a.lwz('r12', CURSOR, 'r31')
            empty_square(a, 'r12', 'out')
            a.lwz('r12', 0xC, 'r1').addi('r12', 'r12', -1).stw('r12', 0xC, 'r1')
            a.cmpwi('r12', 0).bgt('move')
            a.lwz('r0', 8, 'r1').stw('r0', CURSOR, 'r31')
            a.label('out')
            a.lwz('r31', 0x1C, 'r1').lwz('r0', 0x24, 'r1').mtlr('r0').addi('r1', 'r1', 0x20).blr()
        _branch_to(image, call, _hook(hs, move), link=True)


def grid_code(image: dolfile.DolImage, hs: dol_hammerspace.DolHammerspace, grid: Grid, cap: int,
              refs=None) -> list[str]:
    """Everything in the DOL (b-j). ``cap``: members a square's wheel may list (6, or 10 with plan 4d)."""
    bad = inventory.stock_mismatches(image, inventory.group(GROUP)) + [
        f'0x{a:08X}' for a, w in ROW_SITES_STOCK.items() if image.u32(a) != w]
    if bad:
        raise dolfile.DolError('grid: code is not stock (a grid was built before?): ' + '; '.join(bad[:4]))
    if any(len(sq) > cap for sq in grid.squares):
        raise dolfile.DolError(f'a square has more than {cap} members')
    refs = relocate.scan_refs(image) if refs is None else refs
    counts(image, grid.size)
    widget_arrays(image, grid.size)
    map_at = head_map(image, hs, grid)
    decided_flag(image, hs)
    head_list, moved = square_wheels(image, hs, grid, cap, refs)
    positions(image, grid.size - STOCK_SQUARES)
    dpad(image, grid.cols, grid.rows)
    random_pools(image, hs, grid)
    log = [f'grid {grid.cols}x{grid.rows}: {SQUARE_HEADS} stock squares (Luigi has his own; captain swap removed)'
           + (f' + {len(grid.squares)} new (heads {STOCK_HEADS}-{grid.first_empty_head - 1}: '
              + ' | '.join(','.join(f'0x{c:02X}' for c in sq) for sq in grid.squares) + ')'
              if grid.squares else ''),
           f'square -> head table 0x{map_at:08X}; head list ({moved} words) -> 0x{head_list:08X}; objects '
           f'0x{OBJ_SIZE:X} / 0x{OBJ_B_SIZE:X} bytes; {len(POSITION_SITES)} cursor positions +{grid.size - STOCK_SQUARES}']
    if grid.empty:
        empty_cells(image, hs, grid)
        log.append(f'{len(grid.empty)} empty cells (' + ', '.join(str(i) for i in grid.empty)
                   + '): hidden, skipped by the D-pad and the pointer')
    return log


# --------------------------------------------------------------------------
# Layout (a)
# --------------------------------------------------------------------------

def _shift_records(node: bytes, dx: int = 0, dy: int = 0, y: int | None = None) -> bytes:
    node = bytearray(node)
    for offset, _record in layout2d.node_records(bytes(node)):
        for o in CHILD_X:
            struct.pack_into('>h', node, offset + o, struct.unpack_from('>h', node, offset + o)[0] + dx)
        for o in CHILD_Y:
            value = y if y is not None else struct.unpack_from('>h', node, offset + o)[0] + dy
            struct.pack_into('>h', node, offset + o, value)
    return bytes(node)


def _move_element(lay: layout2d.Layout, element: int, dx: int = 0, dy: int = 0) -> None:
    lay.set_nodes(element, [_shift_records(n, dx, dy) for n in lay.node_blobs(element)])


def grid_nodes(stock: list[bytes], grid: Grid) -> list[bytes]:
    """Element 0xBA's nodes: one per cell, 49 px apart, right edge at the stock column 9; empty cells off
    screen. ``stock``: the 40 stock square nodes."""
    cols, rows = grid.cols, grid.rows
    first = (cols - 9) // 2                       # the stock columns' first (12: 1, 11: 1, 10: 0)
    out = []
    for row in range(rows):
        src = min(row, STOCK_ROWS - 1) * STOCK_COLS   # row 5 copies row 4
        for col in range(cols):
            k = min(max(col - first, 0), 9)
            dx = ((9 - k) - (cols - 1 - col)) * SQUARE_W
            node = _shift_records(stock[src + k], dx)
            if rows == 5:
                node = _shift_records(node, y=ROW_Y0 + row * ROW_PITCH)
            if grid.cells[row * cols + col] is None:
                node = _shift_records(node, HIDE_DX)
            out.append(node)
    return out


def _stretch_bars(lay: layout2d.Layout, grow: int) -> None:
    """Bars start ``grow`` px further left and keep their right end; the roster slots spread along them; team 0's
    banner moves between the screen edge and the grid."""
    for pos, bar, slots in BARS:
        nodes = []
        found = 0
        for node in lay.node_blobs(pos):
            node = bytearray(node)
            for offset, record in layout2d.node_records(bytes(node)):
                if record[4] == 3:
                    if struct.unpack_from('>H', record, 6)[0] != bar:
                        raise layout2d.Layout2dError(f'0x{pos:X} does not place 0x{bar:X}')
                    node[offset:offset + len(record)] = _shift_records(
                        struct.pack('>HH', 1, len(record)) + record, -grow)[4:]
                    found += 1
            nodes.append(bytes(node))
        if not found:
            raise layout2d.Layout2dError(f'0x{pos:X} does not place 0x{bar:X}')
        lay.set_nodes(pos, nodes)
        middle, right_cap, left_cap = lay.node_blobs(bar)
        middle = bytearray(middle)
        for offset, _record in layout2d.node_records(bytes(middle)):
            sx = struct.unpack_from('>f', middle, offset + 0x38)[0]
            struct.pack_into('>f', middle, offset + 0x38, sx + grow / BAR_MIDDLE_PX)
        lay.set_nodes(bar, [bytes(middle), _shift_records(right_cap, grow), left_cap])
        first = SLOT_FIRST - grow
        moved = []
        for i, node in enumerate(lay.node_blobs(slots)):
            x = round(first + i * (SLOT_LAST - first) / 8)
            moved.append(_shift_records(node, x - struct.unpack_from('>h', node, 4 + CHILD_X[0])[0]))
        lay.set_nodes(slots, moved)
    banner = []
    for node in lay.node_blobs(BANNER):
        node = bytearray(node)
        for offset, _record in layout2d.node_records(bytes(node)):
            dx = BANNER_X - struct.unpack_from('>h', node, offset + CHILD_X[0])[0]
            for o in CHILD_X:
                struct.pack_into('>h', node, offset + o, struct.unpack_from('>h', node, offset + o)[0] + dx)
            struct.pack_into('>ff', node, offset + 0x34, BANNER_SCALE, BANNER_SCALE)
        banner.append(bytes(node))
    lay.set_nodes(BANNER, banner)


def screen_dx(cols: int) -> int:
    return SCREEN_DX * (cols - STOCK_COLS) // 2


def grid_layout(data: bytes, grid: Grid) -> bytes:
    lay = layout2d.Layout(data)
    stock = lay.node_blobs(GRID_ELEMENT)
    if len(stock) < STOCK_SQUARES or any(struct.unpack_from('>HH', n, 0) != (3, 0x3C) for n in stock[:STOCK_SQUARES]):
        raise layout2d.Layout2dError(f'element 0x{GRID_ELEMENT:X} is not the stock grid')
    lay.set_nodes(GRID_ELEMENT, grid_nodes(stock[:STOCK_SQUARES], grid))
    if grid.cols > STOCK_COLS:
        _stretch_bars(lay, (grid.cols - STOCK_COLS) * SQUARE_W)
    if grid.rows == 5:
        for group, dy in ((TOP_GROUP, TOP_DY), (BOTTOM_GROUP, BOTTOM_DY)):
            for element in group:
                _move_element(lay, element, dy=dy)
    if screen_dx(grid.cols):
        for element in SCREEN_ELEMENTS:
            _move_element(lay, element, dx=screen_dx(grid.cols))
    return lay.to_bytes()


# --------------------------------------------------------------------------
# Square voices
# --------------------------------------------------------------------------

# Species the field code tests directly (RosterExpansion.md, "Stats, size and voice"): a voice from one of them, or
# a body of one of them with another voice, takes that species' branches with it.
SPECIES_BRANCHES = {0x16: 'Noki', 0x19: 'Magikoopa', 0x24: 'Kritter'}


def apply_voices(ctx: steps.RosterContext, grid: Grid, write) -> list[str]:
    """Square voices: each voiced square's square-only new IDs (wheel group 0) get the voice's species (selector
    byte 2), which picks the voice bank, the clips and the select voice. When stock squares swapped voices
    (``voices`` step), that is a species that still speaks with the voice's stock sounds. ``write(address,
    bytes)``."""
    if not grid.voices:
        return []
    selector, rows = wheels.table_location(ctx, 'selector')
    species = lambda cid: ctx.dol.read(selector + 8 * cid + 2, 1)[0]
    remap = ctx.state.get('voice_remap') or {}
    new = {c.id: c for c in ctx.state.get('new_ids') or []}
    log = []
    for k, sq in enumerate(grid.squares):
        voice = grid.voice(k)
        if voice is None:
            continue
        target = voices.species_for_voice(species(voice), remap)
        if target is None:
            raise GridConfigError(f'square 0x{sq[0]:02X}: the voice of 0x{voice:02X} is given away (stock_voices) '
                                  'and no stock square speaks with it any more: give the square another voice')
        voiced, kept = [], []
        for cid in sq:
            square_only = cid in new and new[cid].wheel is None
            (voiced if square_only else kept).append(cid)
        for cid in voiced:
            write(selector + 8 * cid + 2, bytes([target]))
            body = species(new[cid].model_source)
            special = {s for s in (target, body) if s in SPECIES_BRANCHES}
            if body != target and special:
                log.append(f'warning: 0x{cid:02X} has a {", ".join(SPECIES_BRANCHES[s] for s in sorted(special))} '
                           'voice or body: the gameplay branches of that species follow the voice (untested)')
        log.append(f'square 0x{sq[0]:02X}: voice of 0x{voice:02X} (species 0x{target:02X}) for '
                   + (', '.join(f'0x{c:02X}' for c in voiced) or 'no square-only member')
                   + (f'; ' + ', '.join(f'0x{c:02X}' for c in kept) + ' keep their own (on a wheel)' if kept else ''))
    return log


# --------------------------------------------------------------------------
# Step
# --------------------------------------------------------------------------

@steps.register('grid')
def apply(ctx: steps.RosterContext) -> list[str]:
    if ctx.config.get('grid') is None:
        return ['no "grid" in the roster config: the grid stays stock']
    stock_heads = ctx.dol.read(HEAD_LIST, SQUARE_HEADS)
    grid = parse_grid(ctx.config, stock_heads, ctx.dol.read(STOCK_MAP, STOCK_SQUARES))
    ctx.state['grid'] = grid
    selector, rows = wheels.table_location(ctx, 'selector')
    for sq in grid.squares:
        for cid in sq:
            if cid >= rows or not ctx.dol.read(selector + 8 * cid + 6, 1)[0]:
                raise GridConfigError(f'square member 0x{cid:02X} is not selectable (new IDs need an "ids" '
                                      'entry, spare rows a "wheels" entry)')
    log = []
    if any(len(sq) > SQUARE_STOCK_MAX for sq in grid.squares) and not wheels.has_ten_members(ctx.dol):
        log += wheels.lift_to_ten(ctx)
    cap = SQUARE_MAX if wheels.has_ten_members(ctx.dol) else SQUARE_STOCK_MAX
    hs = dol_hammerspace.get(ctx)
    log += grid_code(ctx.dol, hs, grid, cap)
    log += apply_voices(ctx, grid, hs.write)
    hs.commit()
    log += layout_file.get(ctx).update(lambda _lang, data: grid_layout(data, grid))
    return log
