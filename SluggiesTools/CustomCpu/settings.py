"""Custom CPU level settings: the list, the checks, ``.slgcpu`` files and the bytes a custom match swaps in.

The settings of the Slugout 0.8 patcher as data (files go both ways), reduced to one level: the
custom level is that tool's **level 4 column** (the clean game's level 4 tables, every behaviour off, everything
opt-in), so its files load here and ours load there. Three kinds of setting:

* ``Table``: bytes of the game's own CPU tables (static data in ``main.dol``). The default is the clean game's
  level 4 value, decoded from ``1_Input/main.dol``. A custom match swaps the bytes in at its start and any
  other match puts the saved ones back (``engine.py``), so levels 1-4 never see them.
* ``Switch``: the four table groups (``cpu-perfect-*``: on = the group's tuned bytes are the base that the
  group's table values edit) and the behaviour switches of injected code.
* ``Const``: a constant of injected behaviour code; acts only while its switch is on.

Every behaviour switch has its code in ``behaviours.py``. Keys of our own (no counterpart in the external tool)
start with ``sluggies_``; they are the CPU tables that used to be on the Gameplay tab.

Edits are ``{key: value}`` and hold only what differs from the default. A table value given while its group is
on stays explicit even when it equals the clean game's value (the group's tuned byte is the base then).

Files (``.slgcpu``, the external tool's format): ``{"format": "sluggers-cpu", "version": 1, "name",
"settings": {key: {"4": value}}}``. Loading reads one level column: 4 exactly; 5 or 6 by turning that tool's
defaults of the level (groups and behaviours on, its level 5 / 6 constants) into explicit values.
"""

import json
import math
import os
import struct
from dataclasses import dataclass

EXTENSION, FORMAT, VERSION = '.slgcpu', 'sluggers-cpu', 1
LEVELS = (4, 5, 6)                       # the external tool's columns; ours is 4
OWN_PREFIX = 'sluggies_'
ROLL_LIMIT = 127                         # star usage + modifier: a byte the game sign-extends (0x800C5934)

BAT, PITCH, FIELD, RUN = 'cpu-perfect-batting', 'cpu-perfect-pitching', 'cpu-perfect-fielding', 'cpu-perfect-running'
GRID_DIVE_GATE, FLY_DIVE_GATE = 0x800E2750, 0x800E25B8       # the fielding group's two code words
OUTFIELD_DIVE_WORD = bytes.fromhex('28000006')               # the "outfield dive" game option's word at the first
STAR_SWINGS = 'cpu-star-swings'


class SettingsError(ValueError):
    pass


def _hz50(v: int) -> int:
    """A 60 Hz frame count's 50 Hz row, as the game's own rows (10 -> 8, 20 -> 16, 12 -> 10, 6 -> 5, 2 -> 1)."""
    return int(v * 5 // 6)


SIZES = {'u8': 1, 's8': 1, 'frames': 1, 'u16': 2, 'frames16': 2, 'f32': 4, 'pair': 2, 'fastball': 5}


@dataclass(frozen=True)
class Table:
    """Byte sites in the game's CPU tables. ``enc``: ``u8`` / ``s8`` (each site a byte), ``u16`` (each a
    big-endian u16), ``f32``, ``pair`` (s8 -v, +v: a +-frames window), ``frames`` / ``frames16`` (u8 / u16:
    sites (60 Hz, 50 Hz), the 50 Hz one ``_hz50``), ``fastball`` (the 5 charge weights; the value is the
    perfect charge's share)."""
    key: str
    group: str | None                    # its ``cpu-perfect-*`` switch; None: no group (our own tables)
    section: str
    label: str
    unit: str
    lo: float
    hi: float
    enc: str
    sites: tuple[int, ...]
    stock: float                         # the clean game's value (the default without a DOL to decode)
    tip: str
    step: float = 1
    integer: bool = True
    choices: tuple[str, ...] | None = None
    kind = 'table'
    boolean = False

    @property
    def feature(self):
        return self.group

    @property
    def size(self) -> int:
        return SIZES[self.enc]

    def encode(self, v, stock_get) -> dict[int, bytes]:
        """``{address: bytes}`` for value ``v`` (``stock_get(address, n)``: the clean game, for the fastball row)."""
        e = self.enc
        if e == 'u8':
            return {a: bytes([int(v)]) for a in self.sites}
        if e == 's8':
            return {a: struct.pack('>b', int(v)) for a in self.sites}
        if e == 'u16':
            return {a: struct.pack('>H', int(v)) for a in self.sites}
        if e == 'f32':
            return {a: struct.pack('>f', float(v)) for a in self.sites}
        if e == 'pair':
            return {a: struct.pack('>bb', -int(v), int(v)) for a in self.sites}
        if e in ('frames', 'frames16'):
            fmt = '>B' if e == 'frames' else '>H'
            a60, a50 = self.sites
            return {a60: struct.pack(fmt, int(v)), a50: struct.pack(fmt, _hz50(int(v)))}
        if e == 'fastball':                  # the other four weights keep the game's mix, scaled to the rest
            a = self.sites[0]
            base = list(stock_get(a, 5)[:4])
            rest, total = 100 - int(v), sum(base)
            w = [rest * b // total for b in base]
            w[w.index(max(w))] += rest - sum(w)
            return {a: bytes(w + [int(v)])}
        raise SettingsError(f'unknown encoding {e!r}')

    def decode(self, get):
        """The value in the bytes ``get(address, n)`` (the first site; the others follow it)."""
        a, e = self.sites[0], self.enc
        if e in ('u8', 'frames'):
            return get(a, 1)[0]
        if e == 's8':
            return struct.unpack('>b', get(a, 1))[0]
        if e in ('u16', 'frames16'):
            return struct.unpack('>H', get(a, 2))[0]
        if e == 'f32':
            return round(struct.unpack('>f', get(a, 4))[0], 4)
        if e == 'pair':
            return struct.unpack('>bb', get(a, 2))[1]
        if e == 'fastball':
            w = get(a, 5)
            return round(100 * w[4] / sum(w))
        raise SettingsError(f'unknown encoding {e!r}')


@dataclass(frozen=True)
class Switch:
    """A CPU switch. ``gated``: it gates injected behaviour code (else a table group)."""
    key: str
    label: str
    tip: str
    gated: bool = True
    kind = 'switch'
    section = 'Switches'
    unit = ''
    lo, hi, step, integer, boolean, choices = 0, 1, 1, True, True, None
    split = False
    levels = LEVELS

    @property
    def feature(self):
        return self.key


@dataclass(frozen=True)
class Const:
    """A constant of injected behaviour code. ``defaults``: the external tool's values at levels 4 / 5 / 6
    (None: the level has none)."""
    key: str
    feature: str                         # the switch it needs
    module: str                          # the module of this package that holds its code
    section: str
    label: str
    unit: str
    lo: float
    hi: float
    defaults: tuple
    tip: str
    step: float = 1
    integer: bool = True
    boolean: bool = False
    levels: tuple[int, ...] = LEVELS
    split: bool = False                  # the external tool reads a bare number as levels 5 and 6
    kind = 'constant'
    choices = None


SWITCHES = [
    Switch(BAT, 'Batters read your pitches',
           'Batters read the pitch type, track the ball and lay off balls (the Batting settings).', gated=False),
    Switch(PITCH, 'Pitchers charge, curve and move the ball',
           'Perfect-charge fastballs, curves that break late, rarely the same spot twice (the Pitching settings).',
           gated=False),
    Switch(FIELD, 'Fielders dive, jump and react',
           'Fielders go for dives and jump catches, clear obstacles and react to teammates (the Fielding settings).',
           gated=False),
    Switch(RUN, 'Runners tap and press fast',
           'Runners land their sprint taps and press quickly in close plays (the Running settings).', gated=False),
    Switch('cpu-pitching', 'Pitchers learn each batter',
           'Pitchers work the edges of the zone and learn during the game what gets each batter out. Each '
           'pitcher\'s starting pitch mix is worked out from the characters\' stats when you deploy (and at every '
           'roster run): after changing stats with the stat editor (Bridge), deploy again.'),
    Switch('cpu-charge-timing', 'Batters swing fully charged',
           'Batters start charging at once and are at full charge when they swing.'),
    Switch('cpu-batter-track', 'Batters see where the pitch really crosses',
           'Batters judge ball or strike where the pitch crosses the plate, so breaking balls don\'t fool them.'),
    Switch('cpu-no-bunts', 'Never bunts', 'The CPU never bunts.'),
    Switch('cpu-cursed-ball', 'Charged pitches weaken your hits',
           'The CPU\'s charged pitches are cursed balls, like yours: they cut the batter\'s hit power.'),
    Switch('cpu-star-swings', 'Star swings by hitter strength',
           'Weak hitters always star-swing with 2 or more stars; strong hitters rarely or never (the Star swings '
           'settings; the star swing odds tables are not used then).'),
    Switch('cpu-no-star-pitches', 'Never throws star pitches', 'The CPU pitcher never throws a star pitch.'),
    Switch('cpu-rundown', 'Smart rundown throws',
           'In a rundown it throws ahead at once and times throws behind the runner to get the out.'),
    Switch('cpu-items', 'Aims batting items at fielders',
           'Uses its batting item, aimed and timed to hit the fielder about to get the ball (the Items settings '
           'add error to that).'),
    Switch('cpu-item-defense', 'Fielders handle items',
           'Fielders jump POWs, pass when a teammate is near, run around items and ignore Boo.'),
    Switch('cpu-subs', 'Brings in its best relief pitchers',
           'When it changes pitchers it picks from its best, then moves fielders for better chemistry.'),
]

CLASSES = (('Balance', 0), ('Power', 1), ('Speed', 2), ('Technique', 3))
PITCH_TYPES = ('Curveball', 'Fastball', 'Changeup')
STAR_USAGE, STAR_MODIFIERS, PREFERRED_PITCH = 0x80624778, 0x80797210, 0x807971C0
STAR_SITUATIONS = (('normal_0_1', 'normal, 0-1 strikes'), ('normal_2', 'normal, 2 strikes'),
                   ('special_0_1', 'special, 0-1 strikes'), ('special_2', 'special, 2 strikes'), ('dire', 'dire'))
STAR_USAGE_STOCK = bytes.fromhex('0505144B5F0F0F1E505F01010A1E3C19191E325F')
PREFERRED_PITCH_STOCK = bytes.fromhex('0001010100000002' '464655555550323C')     # 8 pitch types, then 8 chances
TOP_TIER = 3                             # the custom level plays the hardest tier: row 3 of the per-level tables

TABLES = [
    Table('pitch_read', BAT, 'Batting', 'Pitch read', '%', 0, 100, 'u8', (0x80797227,), 85,
          'Chance the batter reads the pitch type right. A wrong read widens its timing error.'),
    Table('star_pitch_read', BAT, 'Batting', 'Star-pitch read', '%', 0, 100, 'u8', (0x80797223,), 45,
          'The same, against star pitches.'),
    Table('track', BAT, 'Batting', 'Tracks the ball', '%', 0, 100, 'u8', (0x80797233,), 85,
          'Chance the bat follows the ball. When it doesn\'t, the batter swings where it guessed the pitch would '
          'go, usually a whiff. This is the main sweet-spot rate.'),
    Table('aim_error', BAT, 'Batting', 'Aim error', 'plate widths', 0, 0.3, 'f32', (0x806248F4,), 0.05,
          'How far off the bat\'s aim can be while tracking.', step=0.01, integer=False),
    Table('timing_right', BAT, 'Batting', 'Timing error, right read', '± frames', 0, 10, 'pair', (0x8062490E,), 2,
          'Random early or late swing after a right read.'),
    Table('timing_wrong', BAT, 'Batting', 'Timing error, wrong read', '± frames', 0, 20, 'pair', (0x80624916,), 4,
          'Random early or late swing after a wrong read.'),
    Table('takes_first', BAT, 'Batting', 'Takes the first pitch', '%', 0, 100, 'u8', (0x80624984,), 40,
          'Chance of taking an at-bat\'s first pitch, wherever it is.'),
    Table('lays_off', BAT, 'Batting', 'Lays off balls', '%', 0, 100, 'u8', (0x80624985,), 95,
          'Chance of taking a pitch outside the zone.'),
    Table('takes_strike', BAT, 'Batting', 'Takes a strike (0-1 strikes)', '%', 0, 100, 'u8', (0x80624986,), 5,
          'Chance of taking a hittable strike with 0 or 1 strikes.'),
    Table('takes_strike2', BAT, 'Batting', 'Takes a strike (2 strikes)', '%', 0, 100, 'u8', (0x80624987,), 2,
          'Chance of taking a hittable strike with 2 strikes.'),
    Table('decoy', BAT, 'Batting', 'Fooled by the Wario decoy', '%', 0, 100, 'u8', (0x80797253,), 30,
          'Chance a decoy star pitch makes the batter track the fake ball.'),
    *[Table(f'charge_{n.lower()}{k}', BAT, 'Batting', f'Charge swings: {n}, {"0-1" if not k else "2"} strikes', '%',
            0, 100, 'u8', (0x80797218 + c * 2 + k,), s,
            f'Chance a {n} hitter charges the swing with {"0 or 1 strikes" if not k else "2 strikes"} (rolled only '
            'when the CPU does not star-swing).')
      for n, c in CLASSES for k, s in ((0, (40, 90, 30, 40)[c]), (1, (10, 40, 10, 10)[c]))],
    Table('perfect_fastball', PITCH, 'Pitching', 'Perfect-charge fastball', '%', 0, 100, 'fastball', (0x8062475F,),
          10, 'Chance a charged fastball gets a perfect charge (the game\'s own perfect charge). The rest keep the '
          'game\'s mix of 50 / 70 / 100% charges.'),
    Table('repeat_location', PITCH, 'Pitching', 'Repeats a location', '%', 0, 100, 'u8', (0x807971E7,), 10,
          'Chance of throwing to the same spot as the last pitch.'),
    Table('curve', PITCH, 'Pitching', 'Curves a plain pitch', '%', 0, 100, 'u8', (0x807971EB,), 85,
          'Chance a plain pitch curves.'),
    Table('late_break', PITCH, 'Pitching', 'Late break', '%', 0, 100, 'u8', (0x807971EF,), 70,
          'Chance a curve breaks late.'),
    Table('dive_try', FIELD, 'Fielding', 'Tries a dive or jump', '%', 0, 100, 'u8',
          tuple(0x80624A14 + 5 * c for c in range(4)), 95,
          'Chance a fielder goes for a dive or jump catch (the game\'s is 95-99% by class; one value for all).'),
    Table('obstacle', FIELD, 'Fielding', 'Clears obstacles', '%', 0, 100, 'u8',
          tuple(0x80624A28 + 5 * c for c in range(4)), 90,
          'Chance a fielder jumps over or dodges an obstacle in its path.'),
    Table('chem_far', FIELD, 'Fielding', 'Chemistry reaction (far)', '%', 0, 100, 'u8', (0x80624A68,), 60,
          'Chance a fielder reacts to a good-chemistry teammate.'),
    Table('chem_near', FIELD, 'Fielding', 'Chemistry reaction (near)', '%', 0, 100, 'u8', (0x80624A6D,), 80,
          'The same, with the teammate close.'),
    Table('reaction_delay', FIELD, 'Fielding', 'Reaction delay', 'frames', 0, 30, 'frames',
          (0x806249E8, 0x806249ED), 10, 'Extra frames before a fielder starts moving after contact.'),
    Table('throw_hold_in', FIELD, 'Fielding', 'Throw hold (infield)', 'frames', 0, 10, 'frames',
          (0x806249F4, 0x806249FE), 2, 'Frames an infielder holds the ball before throwing.'),
    Table('throw_hold_out', FIELD, 'Fielding', 'Throw hold (outfield)', 'frames', 0, 10, 'frames',
          (0x806249F9, 0x80624A03), 2, 'Frames an outfielder holds the ball before throwing.'),
    Table('replan', FIELD, 'Fielding', 'Recover from a deflection', 'frames', 1, 30, 'frames',
          (0x80624A08, 0x80624A0D), 6,
          'Frames before a fielder re-plans its chase after the ball bounces off someone (at least 1: 0 never '
          'fires).'),
    Table('hard_throw', FIELD, 'Fielding', 'Hard throw on close plays', '%', 0, 100, 'u8', (0x80797258,), 90,
          'Chance of the CPU-only harder throw on a close play.'),
    Table('steal_jump', RUN, 'Running', 'Good steal jump', '%', 0, 100, 'u8', (0x8079727F,), 30,
          'Chance a stealing runner gets a good jump.'),
    Table('sprint_taps', RUN, 'Running', 'Sprint taps land', '%', 0, 100, 'u16', (0x80624A7E, 0x80624AA6), 90,
          'Chance the runner\'s sprint taps count.'),
    Table('press_delay', RUN, 'Running', 'Close-play press delay', 'frames', 0, 40, 'frames16',
          (0x80624B1E, 0x80624B26), 20, 'Frames before the runner presses in a close-play contest.'),
    Table('always_presses', RUN, 'Running', 'Presses in a close play', '%', 0, 100, 'u8',
          tuple(0x80797280 + i for i in range(4)), 99, 'Chance the runner presses at all in a close play.'),
    # -- our own: the CPU tables that used to be on the Gameplay tab --------------------------------------
    *[Table(f'{OWN_PREFIX}star_usage_{n.lower()}_{key}', None, 'Star usage', f'Star usage: {n}, {text}', '%',
            0, ROLL_LIMIT, 'u8', (STAR_USAGE + 5 * c + j,), STAR_USAGE_STOCK[5 * c + j],
            f'Chance a {n} character star-swings or throws a star pitch ({text}). Special: runners on 2nd/3rd '
            '(not 1st) or bases loaded. Dire: 2 strikes, 2 outs, bottom of the final inning.')
      for n, c in CLASSES for j, (key, text) in enumerate(STAR_SITUATIONS)],
    Table(f'{OWN_PREFIX}star_modifier_normal', None, 'Star usage', 'Star swing modifier: normal', '', -128, 127,
          's8', (STAR_MODIFIERS + 2 * TOP_TIER,), -20,
          'Added to the star usage odds in a normal situation. Odds plus modifier may not pass 127.'),
    Table(f'{OWN_PREFIX}star_modifier_special', None, 'Star usage', 'Star swing modifier: special', '', -128, 127,
          's8', (STAR_MODIFIERS + 2 * TOP_TIER + 1,), 5,
          'Added to the star usage odds in a special situation. Odds plus modifier may not pass 127.'),
    *[t for n, c in CLASSES for k, strikes in enumerate(('0-1 strikes', '2 strikes')) for t in (
        Table(f'{OWN_PREFIX}pitch_{n.lower()}{k}', None, 'Preferred pitch', f'Preferred pitch: {n}, {strikes}', '',
              0, 2, 'u8', (PREFERRED_PITCH + 2 * c + k,), PREFERRED_PITCH_STOCK[2 * c + k],
              f'The pitch a {n} pitcher prefers with {strikes}.', choices=PITCH_TYPES),
        Table(f'{OWN_PREFIX}pitch_chance_{n.lower()}{k}', None, 'Preferred pitch',
              f'Preferred pitch chance: {n}, {strikes}', '%', 0, 100, 'u8', (PREFERRED_PITCH + 8 + 2 * c + k,),
              PREFERRED_PITCH_STOCK[8 + 2 * c + k],
              'Chance of throwing the preferred pitch. When it fails, the CPU throws a random pitch.'))],
]
# Tables a custom match always holds at the clean game's values outside the settings above (rows of other tiers).
WHOLE_TABLES = ((STAR_MODIFIERS, 8),)
# The tables above that were global Gameplay tab settings before (whole tables; the charge odds follow the
# modifiers): star usage, modifiers + charge odds, preferred pitch + chances.
FORMER_GAMEPLAY_RANGES = ((STAR_USAGE, 20), (STAR_MODIFIERS, 16), (PREFERRED_PITCH, 16))


def _c(key, feature, module, section, label, unit, lo, hi, defaults, tip, **more) -> Const:
    if not isinstance(defaults, tuple):
        defaults = (defaults,) * 3
    return Const(key, feature, module, section, label, unit, lo, hi, defaults, tip, **more)


CONSTANTS = [
    _c('model_learning', 'cpu-pitching', 'pitching', 'Pitch model', 'Learning speed', '', 0.0, 0.5,
       (0.04, 0.04, 0.08), 'How fast the pitcher learns what beats this batter (0: never learns).', step=0.01,
       integer=False),
    _c('model_randomness', 'cpu-pitching', 'pitching', 'Pitch model', 'Randomness: pitch choice', '', 0.05, 5.0,
       1.0, 'How random its pitch type, location and break are (higher: more random, harder to read).', step=0.05,
       integer=False),
    _c('model_randomness_bs', 'cpu-pitching', 'pitching', 'Pitch model', 'Randomness: ball or strike', '', 0.05,
       5.0, 0.25, 'How random its ball-or-strike choice is.', step=0.05, integer=False),
    _c('model_patience', 'cpu-pitching', 'pitching', 'Pitch model', 'Reads the batter over', 'pitches', 1.0,
       50.0, 4.0, 'How many pitches its read of the batter\'s habits (chasing, taking) is averaged over: higher '
       'changes its mind slower.', integer=False),
    _c('model_floor', 'cpu-pitching', 'pitching', 'Pitch model', 'Balls at 0-1 balls, at least', '%', 0, 50, 5,
       'A ball this often first, before the model picks, with 0 or 1 balls.'),
    _c('never_walk', 'cpu-pitching', 'pitching', 'Pitch model', 'Never walk the batter', '', 0, 1, 0,
       'On: with 3 balls every pitch is a strike, so it never walks a batter (the 3-ball rates below are held at '
       '0).', boolean=True),
    _c('cap3_0', 'cpu-pitching', 'pitching', 'Pitch model', 'Balls at 3-0', '%', 0, 100, 5,
       'With 3 balls and 0 strikes: the ball rate.'),
    _c('cap3_1', 'cpu-pitching', 'pitching', 'Pitch model', 'Balls at 3-1', '%', 0, 100, 5,
       'With 3 balls and 1 strike.'),
    _c('cap3_2', 'cpu-pitching', 'pitching', 'Pitch model', 'Balls at 3-2', '%', 0, 100, 5,
       'With 3 balls and 2 strikes.'),
    _c('far_ball', 'cpu-pitching', 'pitching', 'Pitch model',
       'How far outside and inside the strike zone it throws', 'plate widths', 0.76, 1.2, (0.94, 0.94, 0.9),
       'The outside edge of the band its balls land in (the band starts at 0.75, past the sweet spot).', step=0.01,
       integer=False),
    _c('fastball_speed', 'cpu-pitching', 'pitching', 'Pitch mix', 'Fast pitcher charge pitch threshold', '', 0,
       300, 150, 'Pitchers with at least this charge pitch speed start out throwing fastballs more.', split=True),
    _c('fastball_top', 'cpu-pitching', 'pitching', 'Pitch mix', 'Fastballs (fast pitchers)', '%', 0, 70, 35,
       'Starting fastball share for those pitchers (at most 70: changeups can take up to 30).', split=True),
    _c('fastball_low', 'cpu-pitching', 'pitching', 'Pitch mix', 'Fastballs (the others)', '%', 0, 70, 3,
       'Starting fastball share for everyone else (at most 70, as above).', split=True),
    _c('plain_curves', 'cpu-pitching', 'pitching', 'Pitch mix', 'Plain curves', '%', 0, 100, 15,
       'Starting share of plain curves; the rest after fastballs and changeups are charged curves it steers onto '
       'the edge.', split=True),
    _c('zone_first', 'cpu-pitching', 'pitching', 'Pitch mix', 'First pitch in the zone', '%', 0, 100, 40,
       'Before it learns: chance an at-bat\'s first pitch goes in the zone.', split=True),
    _c('zone_after_take', 'cpu-pitching', 'pitching', 'Pitch mix', 'In the zone after a taken strike', '%', 0,
       100, 75, 'Before it learns: chance of a strike after the batter took one.', split=True),
    _c('zone_after_chase', 'cpu-pitching', 'pitching', 'Pitch mix', 'In the zone after a chase', '%', 0, 100,
       20, 'Before it learns: chance of a strike after the batter chased a ball.', split=True),
    _c('hbp_margin', 'cpu-pitching', 'pitching', 'Pitch mix', 'Hit-by-pitch margin', 'm', 0.0, 0.5, 0.15,
       'How far clear of the batter\'s hit box a pitch on his side must stay.', split=True, step=0.01,
       integer=False),
    _c('star_always', 'cpu-star-swings', 'behaviours', 'Star swings', 'Always star-swing below charge power', '', 0,
       150, 60, 'A batter with charge power under this always star-swings when the team has 2 or more stars.',
       split=True),
    _c('star_never', 'cpu-star-swings', 'behaviours', 'Star swings', 'Never at charge power', '', 0, 150, 60,
       'Charge power at or above this never star-swings.', split=True),
    _c('star_rare', 'cpu-star-swings', 'behaviours', 'Star swings', 'Rarely from charge power', '', 0, 150, 60,
       'From this charge power up to the "never" line, only at the rare rate.', split=True),
    _c('star_rare_pct', 'cpu-star-swings', 'behaviours', 'Star swings', 'The rare rate', '%', 0, 100, 2,
       'The chance in that range.', split=True),
    _c('star_top', 'cpu-star-swings', 'behaviours', 'Star swings', 'Below that: this minus charge power', '', 0, 200,
       80, 'Below the rare line the chance is this number minus charge power (80: charge power 60 gets 20%).',
       split=True),
    _c('rundown_first', 'cpu-rundown', 'rundown', 'Rundowns', 'Early throw, first throw', '%', 0, 100, 90,
       'Chance the first throw to the base behind a runner heading back goes early.', split=True),
    _c('rundown_early', 'cpu-rundown', 'rundown', 'Rundowns', 'Early throw, other throws', '%', 0, 100, 70,
       'Chance a later throw to the base behind a runner heading back goes early.', split=True),
    _c('rundown_lead', 'cpu-rundown', 'rundown', 'Rundowns', 'Early throw lead', 'frames', 0, 60, 20,
       'How many frames before the deadline (the last moment the throw still gets him out) an early throw goes.',
       split=True),
    _c('rundown_buffer', 'cpu-rundown', 'rundown', 'Rundowns', 'Throw deadline margin', 'frames', 0, 60, 10,
       'The ball must reach the base this many frames ahead of the runner\'s best time.', split=True),
    _c('rundown_rolled', 'cpu-rundown', 'rundown', 'Rundowns', 'Max early throws', '', 0, 50, 10,
       'How many throws of one rundown may go early; after that each goes at the deadline.', split=True),
    _c('items_error_free', 'cpu-items', 'items', 'Items', 'Error-free item at-bats', '%', 0, 100,
       (75, 75, None), 'Share of at-bats with no added item error; the others roll the error below once for the '
       'whole at-bat.', levels=(4, 5)),
    _c('items_timing_error', 'cpu-items', 'items', 'Items', 'Item timing error', '± frames', 0, 30,
       (30, 30, None), 'Frames early or late, in an at-bat with added error.', levels=(4, 5)),
    _c('items_aim_error', 'cpu-items', 'items', 'Items', 'Item aim error', '± m', 0.0, 10.0, (2.5, 2.5, None),
       'Metres off target on each axis, in an at-bat with added error (steps of 0.25 m).', levels=(4, 5),
       step=0.25, integer=False),
    _c('items_lead', 'cpu-items', 'items', 'Items', 'Hit before the catch', 'frames', 0, 60, 15,
       'The item lands this many frames before the fielder reaches the ball.', split=True),
    _c('items_cursor', 'cpu-items', 'items', 'Items', 'Cursor speed', 'm / frame', 0.5, 5.0, 2.0,
       'How fast the item cursor moves (the game\'s level 4: 0.7).', split=True, step=0.1, integer=False),
    _c('items_runner_pad', 'cpu-items', 'items', 'Items', 'Keep clear of own runners', 'm', 0.0, 10.0, 1.5,
       'How far outside a blast its own runners must stay.', split=True, step=0.25, integer=False),
    _c('items_pass', 'cpu-item-defense', 'item_defense', 'Items', 'Pass distance vs items', 'm', 0.0, 20.0,
       20.0, 'A fielder facing an item passes to a teammate within this many metres (the game\'s pass flight tops '
       'out at 20).', split=True, step=0.5, integer=False),
    _c('boo_error_rate', 'cpu-item-defense', 'item_defense', 'Items', 'Boo Error Rate', '%', 0, 100, 40,
       'While a Boo hides the ball, each fielder misjudges where it is by a random amount; this share of fielders '
       'are off by more than their catch reach and aren\'t likely to make the play.'),
]

SETTINGS = SWITCHES + TABLES + CONSTANTS
BY_KEY = {s.key: s for s in SETTINGS}
assert len(BY_KEY) == len(SETTINGS), 'a setting key is used twice'
SECTIONS = list(dict.fromkeys(s.section for s in SETTINGS))
LOCKS = {'never_walk': (('cap3_0', 'cap3_1', 'cap3_2'), 0)}      # while the key is on, these are held at the value
# While the switch is on its code makes the value the tables with these key prefixes would give.
REPLACES = {STAR_SWINGS: (f'{OWN_PREFIX}star_usage_', f'{OWN_PREFIX}star_modifier_')}
RETIRED = {'force3': '3 balls: throw that rate exactly (replaced by Never walk the batter)'}

# -- the table groups' tuned bytes: (address, clean bytes, tuned bytes) -----------------------------------------
_F0 = struct.pack('>f', 0.0)
GROUPS = {
    BAT: [
        (0x80797227, b'\x55', b'\x64'), (0x80797223, b'\x2d', b'\x64'), (0x80797233, b'\x55', bytes([79])),
        (0x806248F4, struct.pack('>f', 0.05), _F0), (0x80624984, b'\x28\x5f\x05\x02', b'\x00\x64\x00\x00'),
        (0x8062490E, b'\xfe\x02', b'\x00\x00'), (0x80624916, b'\xfc\x04', b'\x00\x00'),
        (0x80797253, b'\x1e', b'\x00'),
        (0x80797218, bytes([40, 10, 90, 40, 30, 10, 40, 10]), bytes([100, 90, 100, 100, 100, 80, 100, 80])),
    ],
    PITCH: [
        (0x8062475F, b'\x00\x14\x1e\x28\x0a', b'\x00\x00\x00\x00\x64'), (0x807971E7, b'\x0a', b'\x00'),
        (0x807971EB, b'\x55', b'\x64'), (0x807971EF, b'\x46', b'\x64'),
    ],
    # The fielding and running groups are the external tool's level 6 bytes (it folds its level 6 overrides into
    # them: no faster reactions or throws than the clean game, never the harder close-play throw).
    FIELD: [
        *[(0x80624A14 + 5 * c, s, b'\x64') for c, s in enumerate((b'\x5f', b'\x62', b'\x5f', b'\x63'))],
        *[(0x80624A28 + 5 * c, b'\x5a', b'\x64') for c in range(4)],
        (0x80624A68, b'\x3c', b'\x64'), (0x80624A6D, b'\x50', b'\x64'), (0x80797258, b'\x5a', b'\x00'),
        (GRID_DIVE_GATE, bytes.fromhex('28000003'), OUTFIELD_DIVE_WORD),      # dives / jumps for grounders, liners
        (FLY_DIVE_GATE, bytes.fromhex('2C040014'), bytes.fromhex('2C040000')),     # dives for fly balls
    ],
    RUN: [
        (0x80624A7E, b'\x00\x5a', b'\x00\x64'), (0x80624AA6, b'\x00\x5a', b'\x00\x64'),
        (0x80624B1E, b'\x00\x14', b'\x00\x0c'), (0x80624B26, b'\x00\x10', b'\x00\x0a'),
        (0x80797280, b'\x63' * 4, b'\x64' * 4),
    ],
}
# The external tool's level 6 over its level 5 bytes, where the groups above don't hold them already.
LEVEL6 = {BAT: [(0x80797233, b'\x64')]}
# Its level 5 column of the two folded groups plays level 6's bytes too (it has no separate level 5 there).


def setting(key: str):
    try:
        return BY_KEY[key]
    except KeyError:
        raise SettingsError(f'{key}: not a CPU setting') from None


def is_own(key: str) -> bool:
    return key.startswith(OWN_PREFIX)


# -- bytes ---------------------------------------------------------------------------------------------------------

def _declared_stock() -> dict[int, int]:
    """``{address: byte}`` of the clean game at every site, from the data above (no DOL needed)."""
    out = {}
    for records in GROUPS.values():
        for at, clean, _tuned in records:
            out.update({at + i: b for i, b in enumerate(clean)})
    out.update({STAR_USAGE + i: b for i, b in enumerate(STAR_USAGE_STOCK)})
    out.update({PREFERRED_PITCH + i: b for i, b in enumerate(PREFERRED_PITCH_STOCK)})
    out.update({STAR_MODIFIERS + i: b for i, b in enumerate(bytes.fromhex('00000000F600EC05'))})
    out.update({0x806249E8: 10, 0x806249ED: 8, 0x806249F4: 2, 0x806249F9: 2, 0x806249FE: 1, 0x80624A03: 1,
                0x80624A08: 6, 0x80624A0D: 5, 0x8079727F: 0x1E})
    return out


DECLARED_STOCK = _declared_stock()


def declared_get(address: int, size: int) -> bytes:
    """The clean game's bytes from the module's own data (``stock_get`` without a DOL)."""
    try:
        return bytes(DECLARED_STOCK[address + i] for i in range(size))
    except KeyError:
        raise SettingsError(f'0x{address:08X}: not a custom CPU site') from None


def image_get(image):
    """``stock_get`` reading a ``DolImage`` (the clean ``1_Input/main.dol``)."""
    return lambda address, size: image.read(address, size)


def site_addresses() -> list[int]:
    """Every byte address a custom match writes, sorted: the table settings' sites, the groups' bytes and the
    whole tables. Fixed, whatever the settings are."""
    out = set()
    for t in TABLES:
        for a in t.sites:
            out.update(range(a, a + t.size))
    for records in GROUPS.values():
        for at, clean, _tuned in records:
            out.update(range(at, at + len(clean)))
    for at, size in WHOLE_TABLES:
        out.update(range(at, at + size))
    return sorted(out)


def runs() -> list[tuple[int, int]]:
    """``(address, length)`` of every run of adjacent sites."""
    out: list[list[int]] = []
    for a in site_addresses():
        if out and a == out[-1][0] + out[-1][1]:
            out[-1][1] += 1
        else:
            out.append([a, 1])
    return [(a, n) for a, n in out]


def group_on(edits: dict, group: str | None) -> bool:
    return bool(group) and bool((edits or {}).get(group, False))


def base_bytes(edits: dict, stock_get, groups=None) -> dict[int, int]:
    """``{address: byte}`` at every site without any table value: the clean game, with the tuned bytes of the
    groups that are on (``groups``: these instead of the edits' switches)."""
    on = [g for g in GROUPS if (g in groups if groups is not None else group_on(edits, g))]
    out = {a: stock_get(a, 1)[0] for a in site_addresses()}
    for g in on:
        for at, _clean, tuned in GROUPS[g]:
            out.update({at + i: b for i, b in enumerate(tuned)})
    return out


def custom_bytes(edits: dict, stock_get, outfield_dive: bool = False) -> dict[int, int]:
    """``{address: byte}`` a custom match holds at every site: ``base_bytes`` with the table values on top.
    ``outfield_dive``: the global game option is on, so its word stays on at the custom level too."""
    out = base_bytes(edits, stock_get)
    for key, value in (edits or {}).items():
        s = BY_KEY.get(key)
        if s is not None and s.kind == 'table':
            for at, raw in s.encode(value, stock_get).items():
                out.update({at + i: b for i, b in enumerate(raw)})
    if outfield_dive:
        out.update({GRID_DIVE_GATE + i: b for i, b in enumerate(OUTFIELD_DIVE_WORD)})
    return out


def swap_records(edits: dict, stock_get, outfield_dive: bool = False) -> list[tuple[int, bytes]]:
    """``(address, custom bytes)`` per run (``runs``), for the engine's swap table."""
    held = custom_bytes(edits, stock_get, outfield_dive)
    return [(a, bytes(held[a + i] for i in range(n))) for a, n in runs()]


# -- values --------------------------------------------------------------------------------------------------------

def default(s, stock_get=None):
    """The setting's value without an edit and with every switch off."""
    if s.kind == 'switch':
        return False
    if s.kind == 'table':
        return s.decode(stock_get) if stock_get else s.stock
    return s.defaults[0]


def base_value(s, edits: dict, stock_get=None):
    """What the setting holds without its own edit: ``default``, or the group's tuned value while its group
    is on."""
    if s.kind != 'table' or not group_on(edits, s.group):
        return default(s, stock_get)
    held = base_bytes(edits, stock_get or declared_get)
    return s.decode(lambda a, n: bytes(held[a + i] for i in range(n)))


def value(s, edits: dict, stock_get=None):
    return edits[s.key] if s.key in (edits or {}) else base_value(s, edits, stock_get)


def same(s, a, b) -> bool:
    if s.kind == 'switch' or s.boolean:
        return bool(a) == bool(b)
    return math.isclose(float(a), float(b), rel_tol=0, abs_tol=1e-6)


def set_value(edits: dict, key: str, new, stock_get=None) -> None:
    """Store ``new`` in ``edits``, or drop the edit when it is what the setting holds without one."""
    s = setting(key)
    edits.pop(key, None)
    if not same(s, new, base_value(s, edits, stock_get)):
        edits[key] = bool(new) if s.kind == 'switch' else new


def locked(edits: dict, key: str):
    """The value another setting holds ``key`` at (``LOCKS``), or None."""
    for lock, (keys, held) in LOCKS.items():
        if key in keys and (edits or {}).get(lock):
            return held
    return None


def replaced_by(edits: dict, key: str) -> str | None:
    """The switch whose code replaces the table ``key`` now (``REPLACES``), or None."""
    for switch, prefixes in REPLACES.items():
        if (edits or {}).get(switch) and key.startswith(prefixes):
            return switch
    return None


def _value_problem(s, x) -> str | None:
    if s.kind == 'switch' or s.boolean:
        return None if isinstance(x, bool) or x in (0, 1) else f'{s.label}: {x!r} isn\'t on or off'
    if isinstance(x, bool) or not isinstance(x, (int, float)) or (isinstance(x, float) and math.isnan(x)):
        return f'{s.label}: {x!r} isn\'t a number'
    if not s.lo <= x <= s.hi:
        return f'{s.label}: {x} is outside {s.lo}..{s.hi}'
    if s.integer and x != int(x):
        return f'{s.label}: {x} must be a whole number'
    return None


def check(edits: dict, stock_get=None) -> list[str]:
    """Plain-words problems with ``{key: value}``, [] if none."""
    if not isinstance(edits, dict):
        return ['the CPU settings must be {setting: value}']
    found = []
    for key, x in edits.items():
        s = BY_KEY.get(key)
        if s is None:
            found.append(f'{key}: not a CPU setting (renamed or removed?)')
            continue
        problem = _value_problem(s, x)
        if problem:
            found.append(problem)
    if found:
        return found
    rare, never = (value(BY_KEY[k], edits) for k in ('star_rare', 'star_never'))
    if rare > never:
        found.append('Star swings: "Rarely from" must not be above "Never at"')
    top = max(value(BY_KEY[f'{OWN_PREFIX}star_modifier_{k}'], edits, stock_get) for k in ('normal', 'special'))
    for t in TABLES:
        if t.key.startswith(f'{OWN_PREFIX}star_usage_'):
            odds = value(t, edits, stock_get)
            if odds + top > ROLL_LIMIT:
                found.append(f'{t.label}: {odds} + star swing modifier {top} = {odds + top}; at most {ROLL_LIMIT}, '
                             'or the CPU never star-swings')
    return found


def _text(s, x) -> str:
    if s.kind == 'switch' or s.boolean:
        return 'on' if x else 'off'
    if s.choices:
        return s.choices[int(x)]
    unit = '%' if s.unit == '%' else f' {s.unit}' if s.unit else ''
    return f'{x:g}{unit}'


def describe(edits: dict) -> list[str]:
    """One log line per changed value (``Pitch read 95%``)."""
    out = []
    for key, x in (edits or {}).items():
        s = BY_KEY.get(key)
        if s is not None:
            out.append(f'{s.label} {_text(s, x)}')
    return out


# -- files ---------------------------------------------------------------------------------------------------------

@dataclass
class Loaded:
    name: str
    edits: dict                          # {key: value}: the column, as our settings
    extra: dict                          # keys this version does not know, as the file holds them (kept)
    notes: list[str]                     # plain words: unknown keys, retired keys, the column read
    level: int = 4


def document(edits: dict, name: str = '', extra: dict | None = None) -> dict:
    """The file for ``edits``: every value under level "4"; ``extra`` (unknown keys of a loaded file) as it was."""
    out = dict(extra or {})
    for key, x in edits.items():
        out[key] = {'4': x}
    return {'format': FORMAT, 'version': VERSION, 'name': name, 'settings': out}


def save(path: str, edits: dict, name: str = '', extra: dict | None = None) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(document(edits, name, extra), f, ensure_ascii=False, indent=2)


def read_document(path: str) -> dict:
    label = os.path.basename(path)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            doc = json.load(f)
    except (OSError, ValueError) as exc:
        raise SettingsError(f'{label} isn\'t a CPU settings file ({exc})') from exc
    if not isinstance(doc, dict) or doc.get('format') != FORMAT or not isinstance(doc.get('settings'), dict):
        raise SettingsError(f'{label} isn\'t a CPU settings file')
    return doc


def _by_level(s, v) -> dict:
    """A file value -> ``{level: value}`` (the external tool's rules: a bare number is levels 5 and 6 of a
    ``split`` constant, level 5 of a level 4 / 5 one)."""
    if not isinstance(v, dict):
        if getattr(s, 'levels', LEVELS) == (4, 5):
            return {5: v}
        if getattr(s, 'split', False):
            return {5: v, 6: v}
        raise SettingsError(f'{s.label}: give it per level, {{"4": ...}}')
    out = {}
    for k, x in v.items():
        if str(k) not in {str(n) for n in LEVELS}:
            raise SettingsError(f'{s.label}: level {k} isn\'t one of {", ".join(map(str, LEVELS))}')
        out[int(k)] = x
    return out


def levels_in(doc: dict) -> list[int]:
    """The level columns the file gives values for (unknown keys aside), sorted."""
    found = set()
    for key, v in doc['settings'].items():
        s = BY_KEY.get(key)
        if s is not None:
            try:
                found.update(_by_level(s, v))
            except SettingsError:
                pass
    return sorted(found)


def _column(doc: dict, level: int) -> tuple[dict, dict, list[str]]:
    """``({setting: value given at level}, extra, notes)``."""
    given, extra, notes = {}, {}, []
    for key, v in doc['settings'].items():
        if key in RETIRED:
            notes.append(f'{key}: no longer a setting, dropped ({RETIRED[key]})')
            continue
        s = BY_KEY.get(key)
        if s is None:
            extra[key] = v
            continue
        by = _by_level(s, v)
        if level in by:
            problem = _value_problem(s, by[level])
            if problem:
                raise SettingsError(problem)
            given[s] = by[level]
    if extra:
        notes.append(f'{len(extra)} setting{"s" if len(extra) != 1 else ""} this version does not know (kept in '
                     f'the file data, not applied): {", ".join(sorted(extra))}')
    return given, extra, notes


def from_document(doc: dict, level: int = 4, stock_get=None, label: str = 'the file') -> Loaded:
    """One level column of a parsed file as our settings. Level 5 / 6: the external tool's defaults of that level
    (every switch on, its level's constants, the groups' bytes with its level 6 overrides) become explicit."""
    if level not in LEVELS:
        raise SettingsError(f'level {level} isn\'t one of {", ".join(map(str, LEVELS))}')
    try:
        given, extra, notes = _column(doc, level)
    except SettingsError as exc:
        raise SettingsError(f'{label}: {exc}') from None
    name = doc.get('name') if isinstance(doc.get('name'), str) else ''
    edits: dict = {}
    if level == 4:
        for s, x in given.items():
            edits[s.key] = bool(x) if s.kind == 'switch' else x
    else:
        get = stock_get or declared_get
        for s in SWITCHES:
            if bool(given.get(s, True)):
                edits[s.key] = True
        # That tool's bytes at the level: tuned groups that are on, level 6 overrides, then the table values
        # of groups that are on (it skips a table value whose group is off at level 5 / 6).
        held = base_bytes(edits, get)
        if level == 6:
            for group, records in LEVEL6.items():
                if edits.get(group):
                    for at, raw in records:
                        held.update({at + i: b for i, b in enumerate(raw)})
        for s, x in given.items():
            if s.kind == 'table' and (s.group is None or edits.get(s.group)):
                for at, raw in s.encode(x, get).items():
                    held.update({at + i: b for i, b in enumerate(raw)})
        ours = base_bytes(edits, get)
        for t in TABLES:
            spots = [a + i for a in t.sites for i in range(t.size)]
            if any(held[a] != ours[a] for a in spots):
                edits[t.key] = t.decode(lambda a, n: bytes(held[a + i] for i in range(n)))
        for c in CONSTANTS:
            x = given.get(c, c.defaults[level - 4])
            if x is not None and not same(c, x, c.defaults[0]):
                edits[c.key] = x
        notes.append(f'read as level {level}: that level\'s defaults of the other tool are now explicit values')
    problems = check(edits, stock_get)
    if problems:
        raise SettingsError(f'{label}: ' + '; '.join(problems))
    return Loaded(name, edits, extra, notes, level)


def load(path: str, level: int | None = None, stock_get=None) -> Loaded:
    """A ``.slgcpu`` file. ``level`` None: the file's only column, else 4."""
    doc = read_document(path)
    if level is None:
        found = levels_in(doc)
        level = found[0] if len(found) == 1 else 4
    return from_document(doc, level, stock_get, os.path.basename(path))
