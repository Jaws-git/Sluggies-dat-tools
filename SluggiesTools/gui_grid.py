"""Character grid tab: everything that needs no running Dear PyGui (testable on its own).

* ``StateLoader``: the background ``start.py --roster-state`` read and its
  result (idle / running / done / failed).
* ``GridNav``: the pop-out levels (grid -> square -> slot) and what a click
  or ``Esc`` does.
* Label and tooltip text for squares and slots, and where each portrait crop
  is (``icon_file``) and whether it is the slot's own (``icon_note``).
* ``PendingEdits``: the staged slot edits and the overlay text
  and portrait previews they give.
* ``slot_dialog``: the confirm dialog of "Select .sluggie..." / "Clear slot",
  from the staging check's batch plan (``--apply-slots FILE --dry-run``: the
  pending edits plus the new one) and its output; ``summary_dialog``: Patch
  Game's summary of the full dry run.
* Equipment: ``equipment_tiles`` (the slot level's Bat / Left glove / Right glove / Extra bat tiles:
  state from the read, overlay from the pending edits).
* Animations: ``animations_text`` (the slot summary's stock / custom line) and the ``anim`` edits of
  "Replace animation..." (one ``.anm`` bank each; several picked at once are staged together).
* Item and stadium models: ``item_edit`` / ``item_dialog`` (the grid's item tile: ``.sluggie`` files of models
  outside the character and stadium directories; the stadium tile: stadium models, ``op`` ``STADIUM``; staged
  game-wide and patched as a plain ``--patch FILE``), ``banana_icon`` and ``flag_icon``.
* Roster packs: ``load_dialog`` (the per-slot diff of
  ``--load-roster FILE --dry-run``), ``save_pending_dialog``, and the
  "changed since the pack was saved / loaded" marker (``Reference``).

The roster state is the dict ``Roster/state.py`` writes
(``_gui/roster_state.json``).
"""

import json
import os
import re
import time
from dataclasses import dataclass, field

try:
    import gecko_compat
    import memory_guard
except ImportError:                    # imported as SluggiesTools.gui_grid (tests)
    from SluggiesTools import gecko_compat, memory_guard

STATE_REL = os.path.join('_gui', 'roster_state.json')
SLOT_PLAN_REL = os.path.join('_gui', 'slot', 'plan.json')
EDITS_REL = os.path.join('_gui', 'slot', 'edits.json')
PACK_PLAN_REL = os.path.join('_gui', 'pack', 'plan.json')
SWITCH_PLAN_REL = os.path.join('_gui', 'switch', 'plan.json')
SLOT_EXPORT_REL = os.path.join('_gui', 'slot_export.json')    # Roster/slot_export.RESULT_FILE
PACKAGE_EXPORT_REL = os.path.join('_gui', 'sluggiezip_export.json')   # Roster/slot_zip.RESULT_FILE
PACK_DIR_REL = os.path.join('_gui', 'Roster_Packs')   # where the pack dialogs start
PACK_EXTENSION = '.sluggiesroster'
# start.py modes whose commands can change what the grid shows: the tab re-reads after them
WRITING_FLAGS = frozenset({'--export', '--roster', '--patch', '--unpatch', '--resplit-unused',
                           '--patch-slot', '--clear-slot', '--copy-slot', '--rename-slot', '--set-voice',
                           '--set-char-voice', '--set-species-clip', '--set-stats',
                           '--set-icon', '--apply-slots', '--load-roster',
                           '--write-slot-blocks', '--write-slot-equipment', '--write-slot-animation',
                           '--write-voice-pack', '--game-options'})
STAGED_CLIPS_REL = os.path.join('_gui', 'slot', 'staged_clips')   # encoded clips of pending species_clip edits
UNNAMED = '-'
CPU_VS_CPU_OPTIONS = ('cpu_vs_cpu', 'cpu_management')   # what the CPU vs CPU tab's button turns off (+ close plays)
CLOSE_PLAY_LEVELS = tuple(range(0, 101, 10))             # GameOptions/game_options.CLOSE_PLAY_LEVELS (test-pinned)
CLOSE_PLAY_GECKO = 50                                    # the community Gecko code's odds
MERCY_RULE_STOCK = 10                                    # game_options.MERCY_RULE_STOCK_RUNS (test-pinned)
MERCY_RULE_LEVELS = tuple(r for r in range(1, 100) if r != MERCY_RULE_STOCK)    # game_options (test-pinned)
LUIGI = 0x01


def hex_id(cid: int) -> str:
    return f'0x{cid:02X}'


def chain_writes(steps) -> bool:
    """Whether a command chain can change the game files the grid is read from (dry runs and build checks do
    not)."""
    return any(arg in WRITING_FLAGS for step in steps for arg in step
               if '--dry-run' not in step and '--validate-only' not in step)


CPU_VS_CPU_ENABLE = 'Enable CPU vs CPU'
CPU_VS_CPU_DISABLE = 'Disable CPU vs CPU'
CPU_MANAGEMENT_ENABLE = 'Enable CPU vs CPU management'
CPU_MANAGEMENT_DISABLE = 'Disable CPU vs CPU management'


def _game_options(state):
    """The game options the read found on (None: not known, e.g. no state yet or a state written before the
    reader listed game options)."""
    return (state or {}).get('game_options')


def game_option_edit(key: str, on: bool) -> dict:
    """The pending edit that turns the game option ``key`` on or off (Roster/slot_plan ``game_option``)."""
    return {'op': GAME_OPTION, 'id': hex_id(GAME_WIDE), 'file': f'{"on" if on else "off"}:{key}'}


def staged_game_options(pending: 'PendingEdits | None') -> dict[str, bool]:
    """{key: on} of the pending game option edits (the last one per option wins)."""
    out = {}
    for e in (pending.edits if pending is not None else ()):
        if e['op'] == GAME_OPTION:
            state, _colon, key = e.get('file', '').partition(':')
            out[key] = state == 'on'
    return out


def _effective_options(state, pending) -> set[str]:
    """The game options that are on once the pending game option edits are written (an option staged on replaces
    the other options of its group, ``GAME_OPTION_GROUPS``)."""
    options = set(_game_options(state) or ())
    for key, on in staged_game_options(pending).items():
        if on and key in GAME_OPTION_GROUPS:
            options -= {k for k in options if GAME_OPTION_GROUPS.get(k) == GAME_OPTION_GROUPS[key]}
        (options.add if on else options.discard)(key)
    return options


def cpu_vs_cpu_edits(enable: bool = True, state=None, pending=None) -> list[dict]:
    """The CPU vs CPU tab's button edits. Turning it off also turns management and the close play odds off: they
    do nothing without it, and their buttons are locked then, so they could not be turned off afterwards."""
    if enable:
        return [game_option_edit('cpu_vs_cpu', True)]
    close_play = close_play_edit(state, pending, None)
    return [game_option_edit(key, False) for key in CPU_VS_CPU_OPTIONS] + ([close_play] if close_play else [])


def cpu_management_edit(enable: bool = True) -> dict:
    """The CPU vs CPU tab's management button edit."""
    return game_option_edit('cpu_management', enable)


def cpu_vs_cpu_button(state, pending=None) -> tuple[str, bool]:
    """The CPU vs CPU button's label and whether it enables (the pending edits counted)."""
    on = 'cpu_vs_cpu' in _effective_options(state, pending)
    return (CPU_VS_CPU_DISABLE, False) if on else (CPU_VS_CPU_ENABLE, True)


def cpu_management_button(state, pending=None) -> tuple[str, bool, bool]:
    """The management button's label, whether it enables, and whether it is usable (only while CPU vs CPU is on,
    or staged on)."""
    options = _effective_options(state, pending)
    usable = 'cpu_vs_cpu' in options
    if 'cpu_management' in options:
        return CPU_MANAGEMENT_DISABLE, False, usable
    return CPU_MANAGEMENT_ENABLE, True, usable


def _pending_suffix(pending, key: str) -> str:
    staged = staged_game_options(pending).get(key)
    return '' if staged is None else f' (pending: {"enabled" if staged else "disabled"})'


def cpu_vs_cpu_status(state, pending=None) -> tuple[str, bool | None]:
    """The status label beside the CPU vs CPU button and whether the game has it on (None: not known); a pending
    edit is named after it."""
    options = _game_options(state)
    suffix = _pending_suffix(pending, 'cpu_vs_cpu')
    if options is None:
        return 'Cpu vs Cpu: unknown' + suffix, None
    return (('Cpu vs Cpu: enabled' + suffix, True) if 'cpu_vs_cpu' in options
            else ('Cpu vs Cpu: disabled' + suffix, False))


def cpu_management_status(state, pending=None) -> tuple[str, bool | None]:
    """The status label beside the management button and whether it is on (None: not known); a pending edit is
    named after it."""
    options = _game_options(state)
    suffix = _pending_suffix(pending, 'cpu_management')
    if options is None:
        return 'Management: unknown' + suffix, None
    if 'cpu_management' not in options:
        return 'Management: disabled' + suffix, False
    if 'cpu_vs_cpu' not in options:
        return 'Management: enabled (no effect without CPU vs CPU)' + suffix, False
    return 'Management: enabled' + suffix, True


# Close play odds (GameOptions/game_options close_play_<percent>): one level at a time, only in CPU vs CPU.
CLOSE_PLAY_STAGE = 'Stage odds'
CLOSE_PLAY_DISABLE = 'Disable close play odds'


def close_play_key(percent: int) -> str:
    return f'close_play_{percent}'


def close_play_snap(value) -> int:
    """The slider's value as a level: the nearest one (0-100 in steps of 10)."""
    try:
        value = int(round(float(value) / 10.0)) * 10
    except (TypeError, ValueError):
        return CLOSE_PLAY_GECKO
    return min(max(value, CLOSE_PLAY_LEVELS[0]), CLOSE_PLAY_LEVELS[-1])


def _close_play_of(options) -> int | None:
    return next((p for p in CLOSE_PLAY_LEVELS if close_play_key(p) in options), None)


def close_play_level(state, pending=None) -> int | None:
    """The close play odds once the pending edits are written (None: off)."""
    return _close_play_of(_effective_options(state, pending))


def close_play_buttons(state, pending, percent: int) -> tuple[bool, bool]:
    """Whether Stage (the slider's ``percent``) and Disable are usable: only while CPU vs CPU is on or staged on;
    Stage when ``percent`` is not the level already, Disable while a level is on or staged."""
    options = _effective_options(state, pending)
    level = _close_play_of(options)
    usable = 'cpu_vs_cpu' in options
    return usable and level != percent, usable and level is not None


def close_play_edit(state, pending, percent: int | None) -> dict | None:
    """The edit that stages the odds ``percent``, or turns them off (None). Off names the level the game has, so the
    planner drops a pending level too; with none in the game, the pending one (the planner then drops it: nothing to
    change). None when no level is on or staged."""
    if percent is not None:
        return game_option_edit(close_play_key(percent), True)
    level = _close_play_of(_game_options(state) or ())
    level = close_play_level(state, pending) if level is None else level
    return game_option_edit(close_play_key(level), False) if level is not None else None


def close_play_status(state, pending=None) -> tuple[str, bool | None]:
    """The status label beside the close play buttons and whether the odds are on (None: not known); pending edits
    are named after it."""
    options = _game_options(state)
    suffix = ''
    if any(GAME_OPTION_GROUPS.get(k) == 'close_play' for k in staged_game_options(pending)):
        level = close_play_level(state, pending)
        suffix = f' (pending: {"disabled" if level is None else f"{level}%"})'
    if options is None:
        return 'Close plays: unknown' + suffix, None
    level = _close_play_of(options)
    if level is None:
        return 'Close plays: stock' + suffix, False
    if 'cpu_vs_cpu' not in options:
        return f'Close plays: {level}% (no effect without CPU vs CPU)' + suffix, False
    return f'Close plays: {level}%' + suffix, True


# CPU fixes: plain patches (no DOL hammerspace), usable with or without CPU vs CPU: (option key, label, tooltip).
CPU_FIXES = (
    ('triple_take', 'Triple take home runs',
     'Port of "CPU Restore Triple Take Homeruns" [C^2]: CPU home runs get the triple take replay that CPU vs CPU '
     'skips.'),
    ('strikeout_camera', 'Dynamic strikeout camera',
     'Port of "CPU Restore Dynamic Strikeout Camera" [C^2]: strikeouts get the dynamic camera again in CPU vs CPU.'),
    ('outfield_dives', 'Outfield dives and jumps',
     'Port of "CPUs Can Dive and Jump in Outfield" [dementatino]: CPU fielders may dive and jump in the outfield, '
     'in every match.'),
    ('no_precharge', 'Precharge removal',
     'Port of "CPU Remove Precharge" [philenarion and The Fun Guy]: every character\'s pitching "charge" value below '
     '8.0 is raised to 8.0 (stock: six characters, at 5.0 or 7.0), in every match; the new IDs of a roster expansion '
     'too. The stat editor shows the raised values as edits; disabling puts the stock values back.'),
    ('star_pitch_buff', 'Star pitch buff',
     'Port of "Buff Waluigi/Yoshi/Peach Star Pitches": changes three values of a star pitch table for Waluigi\'s, '
     'Yoshi\'s and Peach\'s star pitches, in every match (CPUs and humans alike).'),
)


# Extras (Gameplay tab): the community Gecko codes of the "Characters and Chemistry" section, toggled like the CPU
# fixes (GameOptions/game_options.py): (option key, label, tooltip).
EXTRAS = (
    ('duplicate_characters', 'Duplicate characters',
     'Port of "Duplicate Characters" [LlamaTrauma]: a character may be on a team more than once (replace the captains '
     'with any players you like). Plain patches: no added code, so Gecko codes keep working.'),
    ('duplicate_captains', 'Duplicate captains',
     'Port of "Duplicate Captains" [LittleCoaks]: both teams are nine of their captain; other characters cannot be '
     'picked. Turn on "every character a captain" too to make a team of any character. It adds code to main.dol, '
     'like CPU vs CPU.'),
    ('all_captains', 'Every character a captain',
     'Port of "Requires all characters are captains" [philenarion]: every character\'s "captain" stat is set, the new '
     'IDs of a roster expansion too. The stat editor shows the changed values as edits; disabling puts the stock '
     'values back.'),
    ('captain_self_chemistry', 'Captain chemistry with themselves',
     'Port of "Captains Have Chemistry With Themselves" [LittleCoaks]: the twelve stock captains get good chemistry '
     '(2) with themselves, for teams of duplicates. The stat editor shows the changed values as edits; disabling puts '
     'the stock values back.'),
)
# The "99-0 Tools" section, below them.
EXTRAS_99_0 = (
    ('always_away', 'Always away team',
     'Port of "Always away team" [LlamaTrauma]: no coin flip for who bats first; the first team (yours) always bats '
     'first. Plain patch: no added code, so Gecko codes keep working.'),
    ('score_999', '999 runs',
     'Port of "999 runs" [LlamaTrauma]: the scoreboard shows the first team\'s score in three digits (up to 999) and '
     'the other team\'s in one (up to 9). Enabling it turns on "always away team" too, as the Gecko code does, so the '
     'three digits are yours. It adds code to main.dol, like CPU vs CPU.'),
    ('extra_innings', 'Unlimited extra innings',
     'Port of "Unlimited extra innings" [LittleCoaks]: a tied match goes on up to the 99th inning (stock: 3 extra '
     'innings). Experimental: the game keeps the line score for 12 innings only, so from the 13th inning on the runs '
     'may land in the wrong score or break the match. Plain patch: no added code.'),
)
# The "Miscellaneous" section, below them (and the mercy rule lead, an input: MERCY_RULE_*).
EXTRAS_MISC = (
    ('night_music', 'Day/night music themes',
     'Port of "Day/Night Music Themes" [C^2]: night matches at Mario Stadium, Wario City, Yoshi Park, Peach Ice '
     'Garden and DK Jungle play other music; day matches keep the stock music. Unlike the Gecko code it also works '
     'for matches the autopicker sets up. It adds code to main.dol, like CPU vs CPU.'),
    ('challenge_coins', 'Challenge mode 999 coins',
     'Port of "Challenge Mode - Press B+2 to get 999 Coins" [Master Kirby]: in challenge mode, hold B + 2 on '
     'controller 1 to set your coins to 999. Unlike the Gecko code it finds the coins when a roster expansion moves '
     'them. It adds code to main.dol, like CPU vs CPU.'),
)
# Enabling one of these also turns on the other (the Gecko code has its line); disabling leaves it alone.
EXTRA_COMPANIONS = {'score_999': 'always_away'}
EXTRA_TOGGLES = EXTRAS + EXTRAS_99_0 + EXTRAS_MISC


def _cpu_fix_label(key: str) -> str:
    return next(label for k, label, _tip in CPU_FIXES + EXTRA_TOGGLES if k == key)


def cpu_fix_button(state, pending, key: str) -> tuple[str, bool]:
    """A CPU fix's (or an extra's) button label and whether it enables (the pending edits counted)."""
    label = _cpu_fix_label(key).lower()
    return (f'Disable {label}', False) if key in _effective_options(state, pending) else (f'Enable {label}', True)


def cpu_fix_edits(state, pending, key: str) -> list[dict]:
    """The edits of a CPU fix's (or an extra's) button: on or off, and on with its companion (``EXTRA_COMPANIONS``)
    unless that is on already."""
    enable = cpu_fix_button(state, pending, key)[1]
    edits = [game_option_edit(key, enable)]
    companion = EXTRA_COMPANIONS.get(key)
    if enable and companion and companion not in _effective_options(state, pending):
        edits.append(game_option_edit(companion, True))
    return edits


def cpu_fix_status(state, pending, key: str) -> tuple[str, bool | None]:
    """The status label beside a CPU fix (or extra) button and whether the game has it on (None: not known); a pending edit is
    named after it."""
    options = _game_options(state)
    text = f'{_cpu_fix_label(key)}: ' + ('unknown' if options is None else 'enabled' if key in options else 'disabled')
    return text + _pending_suffix(pending, key), None if options is None else key in options


def cpu_gecko_warning(state, pending=None) -> bool:
    """Whether to warn about the CPU vs CPU Gecko codes: an option is on (or staged on) that hooks one of their sites
    (CPU vs CPU, its management, the close play odds); the CPU fixes write the same as theirs in place."""
    options = _effective_options(state, pending)
    return bool({'cpu_vs_cpu', 'cpu_management'} & options) or _close_play_of(options) is not None


def extras_gecko_warning(state, pending=None) -> bool:
    """Whether to warn about the Gecko codes the Extras replace: one of them is on (or staged on)."""
    options = _effective_options(state, pending)
    return bool({key for key, _label, _tip in EXTRA_TOGGLES} & options) or _mercy_rule_of(options) is not None


# Mercy rule lead (GameOptions/game_options mercy_rule_<runs>): one level at a time; the stock 10 has no option.
MERCY_RULE_STAGE = 'Stage lead'
MERCY_RULE_RESET = 'Stock mercy rule'


def mercy_rule_key(runs: int) -> str:
    return f'mercy_rule_{runs}'


def mercy_rule_clamp(value) -> int:
    """The input's value as a lead: 1-99 runs (not a number: the stock 10)."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return MERCY_RULE_STOCK
    return min(max(value, 1), 99)


def _mercy_rule_of(options) -> int | None:
    return next((r for r in MERCY_RULE_LEVELS if mercy_rule_key(r) in options), None)


def mercy_rule_level(state, pending=None) -> int:
    """The lead once the pending edits are written (the stock 10 without a level)."""
    level = _mercy_rule_of(_effective_options(state, pending))
    return MERCY_RULE_STOCK if level is None else level


def mercy_rule_buttons(state, pending, runs: int) -> tuple[bool, bool]:
    """Whether Stage (the input's ``runs``) and the stock button are usable: Stage when ``runs`` is not the lead
    already, the stock button while a level is on or staged."""
    level = _mercy_rule_of(_effective_options(state, pending))
    return mercy_rule_level(state, pending) != runs, level is not None


def mercy_rule_edit(state, pending, runs: int | None) -> dict | None:
    """The edit that stages the lead ``runs``, or the stock lead (None or 10). The stock one turns the game's level
    off, so the planner drops a pending level too; with none in the game, the pending one. None when no level is on
    or staged."""
    if runs is not None and runs != MERCY_RULE_STOCK:
        return game_option_edit(mercy_rule_key(runs), True)
    level = _mercy_rule_of(_game_options(state) or ())
    level = _mercy_rule_of(_effective_options(state, pending)) if level is None else level
    return game_option_edit(mercy_rule_key(level), False) if level is not None else None


def mercy_rule_status(state, pending=None) -> tuple[str, bool | None]:
    """The status label beside the mercy rule buttons and whether a lead is on (None: not known); pending edits are
    named after it."""
    options = _game_options(state)
    suffix = ''
    if any(GAME_OPTION_GROUPS.get(k) == 'mercy_rule' for k in staged_game_options(pending)):
        suffix = f' (pending: {mercy_rule_level(state, pending)} runs)'
    if options is None:
        return 'Mercy rule: unknown' + suffix, None
    level = _mercy_rule_of(options)
    if level is None:
        return f'Mercy rule: stock ({MERCY_RULE_STOCK} runs)' + suffix, False
    return f'Mercy rule: {level} runs' + suffix, True


# Team autopicker: the MSS-AutoTeam Gecko code written into main.dol (GameOptions/game_options.AUTOPICKER_PATCHES).
AUTOPICKER_KEY = 'autopicker'
AUTOPICKER_ENABLE = 'Enable autopicker patch'
AUTOPICKER_DISABLE = 'Disable autopicker patch'


def autopicker_button(state) -> tuple[str, bool]:
    """The autopicker button's label and whether it enables."""
    on = AUTOPICKER_KEY in (_game_options(state) or [])
    return (AUTOPICKER_DISABLE, False) if on else (AUTOPICKER_ENABLE, True)


def autopicker_command(enable: bool) -> tuple:
    return ('--game-options', '--on' if enable else '--off', AUTOPICKER_KEY)


def autopicker_status(state) -> tuple[str, bool | None]:
    """The status label beside the button and whether the patch is on (None: not known)."""
    options = _game_options(state)
    if options is None:
        return 'Autopicker patch: unknown', None
    if AUTOPICKER_KEY in options:
        return 'Autopicker patch: on (disable the "team autopicker" Gecko code in Dolphin)', True
    return 'Autopicker patch: off', False


# Unused character strike zones: the "Fix Unused Character Strikezone Heights" Gecko code, in main.dol
# (GameOptions/game_options.STRIKE_ZONE_FIX); written right away like the autopicker patch.
STRIKE_ZONE_KEY = 'unused_strike_zones'
STRIKE_ZONE_ENABLE = 'Enable strike zone fix'
STRIKE_ZONE_DISABLE = 'Disable strike zone fix'


def strike_zone_button(state) -> tuple[str, bool]:
    """The strike zone button's label and whether it enables."""
    on = STRIKE_ZONE_KEY in (_game_options(state) or [])
    return (STRIKE_ZONE_DISABLE, False) if on else (STRIKE_ZONE_ENABLE, True)


def strike_zone_command(enable: bool) -> tuple:
    return ('--game-options', '--on' if enable else '--off', STRIKE_ZONE_KEY)


def strike_zone_status(state) -> tuple[str, bool | None]:
    """The status label beside the button and whether the fix is on (None: not known)."""
    options = _game_options(state)
    if options is None:
        return 'Strike zone fix: unknown', None
    if STRIKE_ZONE_KEY in options:
        return 'Strike zone fix: on (disable the Gecko code in Dolphin)', True
    return 'Strike zone fix: off', False


# Cheats (Options tab): the [TNTkryzt] codes of Dolphin's code list, in main.dol (GameOptions/game_options.py,
# "cheats"); untested. Written right away like the autopicker patch: (option key, label after Enable / Disable,
# tooltip).
CHEATS = (
    ('toy_field_coins', 'Toy Field 9999 coins',
     'Port of "Toy Field 9999 Coins" [TNTkryzt]: your Toy Field coins go to the maximum. Plain patch: no added code, '
     'so Gecko codes keep working.'),
    ('double_home_runs', 'double home runs',
     'Port of "Homeruns Worth Double" [TNTkryzt]: the runs of a home run count twice. It adds code to main.dol, like '
     'CPU vs CPU.'),
    ('reset_outs', 'reset outs (B + 1)',
     'Port of "Reset Out Count B+1" [TNTkryzt]: hold B + 1 during a match to set the outs back to 0. It adds code to '
     'main.dol, like CPU vs CPU.'),
    ('fast_movement', 'extremely fast movement (P1)',
     'Port of "Extremely Fast Movement P1" [TNTkryzt]: player 1 runs much faster. It adds code to main.dol, like CPU '
     'vs CPU.'),
    ('all_stars', 'all star players / MVPs',
     'Port of "All Star Players" / "All MVP\'s" [TNTkryzt] (the list has the same code under both names): sets the '
     '41 star / MVP flags of the loaded save slot. The game saves them, so they stay after disabling. Unlike the Gecko '
     'code it finds the save data when a roster expansion moves it. It adds code to main.dol, like CPU vs CPU.'),
    ('all_items', 'all items',
     'Port of "Unlock All Items" [TNTkryzt]: the challenge mode items of the loaded save slot go to 99 each. The game '
     'saves them, so they stay after disabling. Unlike the Gecko code it finds them when a roster expansion moves them. '
     'It adds code to main.dol, like CPU vs CPU.'),
)
CHEATS_NOTE = ('Codes found in Dolphin\'s code list, written into main.dol. Untested: they may not work. Disable the '
               'matching Gecko codes in Dolphin.')


def _cheat_label(key: str) -> str:
    return next(label for k, label, _tip in CHEATS if k == key)


def cheat_button(state, key: str) -> tuple[str, bool]:
    """A cheat's button label and whether it enables."""
    label = _cheat_label(key)
    return (f'Disable {label}', False) if key in (_game_options(state) or []) else (f'Enable {label}', True)


def cheat_command(key: str, enable: bool) -> tuple:
    return ('--game-options', '--on' if enable else '--off', key)


def cheat_status(state, key: str) -> tuple[str, bool | None]:
    """The status label beside a cheat's button and whether the game has it on (None: not known)."""
    options = _game_options(state)
    if options is None:
        return 'unknown', None
    return ('on', True) if key in options else ('off', False)


# Player memory: each on-field player's heap (High + Low + bat or glove) is 870,400 bytes in the stock game.
# The game options player_heap_<KB> raise it on stock memory; player_heap_big_<KB> need Dolphin's MEM2 override
# at 128 MB and fall back to +48 KB without it (GameOptions/game_options.py, which these mirror).
PLAYER_HEAP_STOCK = 870_400
PLAYER_HEAP_SAFE_KB = 48
PLAYER_HEAP_STOCK_LABEL = 'Stock (850 KB)'
# (option key, combo label, extra KB, big), stock-memory levels first; experimental = Wario City only.
PLAYER_HEAP_LEVELS = (
    ('player_heap_32', '+32 KB (882 KB)', 32, False),
    ('player_heap_64', '+64 KB (914 KB)', 64, False),
    ('player_heap_128', '+128 KB (978 KB)', 128, False),
    ('player_heap_200', '+200 KB (1050 KB) (experimental)', 200, False),
    ('player_heap_big_512', '+512 KB', 512, True),
    ('player_heap_big_1024', '+1 MB', 1024, True),
    ('player_heap_big_2048', '+2 MB', 2048, True),
    ('player_heap_big_4096', '+4 MB', 4096, True),
    ('player_heap_big_5120', '+5 MB (experimental)', 5120, True),
)


def _kb_text(kb: int) -> str:
    return f'{kb // 1024} MB' if kb >= 1024 and kb % 1024 == 0 else f'{kb} KB'


def player_heap_keys() -> list[str]:
    return [key for key, _label, _kb, _big in PLAYER_HEAP_LEVELS]


def player_heap_choices(big_memory: bool = False) -> list[str]:
    """The Options tab's player memory combo items, stock first; the 128 MB levels only when ``big_memory``."""
    return [PLAYER_HEAP_STOCK_LABEL] + [label for _key, label, _kb, big in PLAYER_HEAP_LEVELS
                                        if big_memory or not big]


def player_heap_level(state) -> str | None:
    """The option key of the level the read found on ('': stock; None: not known)."""
    options = _game_options(state)
    if options is None:
        return None
    return next((key for key in player_heap_keys() if key in options), '')


def player_heap_is_big(level: str | None) -> bool:
    return any(key == level and big for key, _label, _kb, big in PLAYER_HEAP_LEVELS)


def player_heap_choice(level: str | None) -> str:
    """The combo item for a level (unknown shows stock)."""
    return next((label for key, label, _kb, _big in PLAYER_HEAP_LEVELS if key == level), PLAYER_HEAP_STOCK_LABEL)


def player_heap_command(choice: str) -> tuple | None:
    """The step that sets the chosen level: one option on (it replaces another level), or all of them off."""
    if choice == PLAYER_HEAP_STOCK_LABEL:
        return ('--game-options', '--off', *player_heap_keys())
    key = next((key for key, label, _kb, _big in PLAYER_HEAP_LEVELS if label == choice), None)
    return None if key is None else ('--game-options', '--on', key)


def player_heap_status(state) -> tuple[str, bool | None]:
    """The status label beside the combo and whether a raised heap is on (None: not known)."""
    level = player_heap_level(state)
    if level is None:
        return 'Player memory: unknown', None
    if not level:
        return f'Player memory: stock ({PLAYER_HEAP_STOCK:,} bytes per player)', False
    kb, big = next((kb, big) for key, _label, kb, big in PLAYER_HEAP_LEVELS if key == level)
    text = f'Player memory: +{_kb_text(kb)} ({PLAYER_HEAP_STOCK + kb * 1024:,} bytes per player)'
    if big:
        text += f'; needs MEM2 at 128 MB in Dolphin, else +{PLAYER_HEAP_SAFE_KB} KB'
    return text, True


# --------------------------------------------------------------------------
# Loader
# --------------------------------------------------------------------------

class StateLoader:
    """One background read at a time; a refresh asked for while one runs is queued (run once afterwards)."""

    IDLE, RUNNING, DONE, FAILED = 'idle', 'running', 'done', 'failed'

    def __init__(self, state_path: str):
        self.state_path = state_path
        self.status = self.IDLE
        self.state = None
        self.error = ''
        self.queued = False

    def start(self) -> bool:
        """True when the caller should launch a read now; False when one runs (the refresh is queued)."""
        if self.status == self.RUNNING:
            self.queued = True
            return False
        self.status = self.RUNNING
        self.error = ''
        return True

    def finish(self, code: int, output: str = '') -> bool:
        """Take a read's exit code and output. Returns True when a queued refresh should start next."""
        if code == 0:
            try:
                with open(self.state_path, encoding='utf-8') as f:
                    self.state = json.load(f)
                self.status = self.DONE
            except (OSError, ValueError) as exc:
                self.status, self.error = self.FAILED, f'could not load {self.state_path}: {exc}'
        else:
            self.status, self.error = self.FAILED, error_message(output) or f'exit code {code}'
        queued, self.queued = self.queued, False
        return queued

    @property
    def message(self) -> str:
        if self.status == self.RUNNING:
            return 'Reading the grid from 3_Output_Dat...'
        if self.status == self.FAILED:
            return f'Could not read the grid: {self.error}'
        if self.status == self.DONE and self.state is not None:
            cols, rows = self.state['shape']
            kind = 'Stock' if self.state['kind'] == 'stock' else 'Roster'
            note = '' if self.state.get('names_read', True) else ' (no names: dt_na.dat missing)'
            if self.state.get('names_read', True) and not self.state.get('icons_read', True):
                note += ' (no portraits)'
            return f'{kind} grid, {cols}x{rows}, {len(self.state["squares"])} squares{note}'
        return ''


def error_message(output: str) -> str:
    """The last ``[Error]`` log line's message in a child's output."""
    for line in reversed(output.splitlines()):
        if '[Error]' in line:
            return line.rsplit('] ', 1)[-1].strip()
    return ''


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------

def characters(state: dict) -> dict[int, dict]:
    return {c['id']: c for c in state['characters']}


def name_of(state: dict, cid: int) -> str:
    c = characters(state).get(cid)
    if (c or {}).get('default_name'):
        return c['default_name']
    name = ((c or {}).get('name') or {}).get('en')
    if not name or name == UNNAMED:
        return f'(unnamed {hex_id(cid)})'
    return name


def known_name(state: dict, cid: int) -> str:
    """The name of a grid character, else just its ID (fallback keys can be characters not on the grid)."""
    return name_of(state, cid) if cid in characters(state) else hex_id(cid)


# The name plate (Roster/names.py: PLATE_CELL, FONT_SIZE, FONT_WEIGHT; test-pinned to ``names.fit_problem``)
PLATE_TEXT_WIDTH = 113               # the 115 px plate, 2 px margin
PLATE_FONT_SIZE, PLATE_FONT_WEIGHT = 14, 800


def name_problem(text: str, font_path: str) -> str | None:
    """Why ``text`` cannot be a slot name (None: it can): what ``Roster.names.fit_problem`` says, measured with the
    plate font at ``font_path`` (the GUI's rename dialog shows it live; the planner checks again)."""
    if not text or not text.strip():
        return 'a name cannot be empty'
    if text != text.strip():
        return 'a name cannot start or end with a space'
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return 'a name cannot hold control characters or line breaks'
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype(font_path, PLATE_FONT_SIZE)
        font.set_variation_by_axes([PLATE_FONT_WEIGHT, 100])
    except (OSError, ValueError, AttributeError):
        return None                      # no font to measure with: the planner decides
    width = ImageDraw.Draw(Image.new('RGBA', (115, 16))).textbbox((0, 0), text, font=font)[2]
    if width > PLATE_TEXT_WIDTH:
        return f'too long for the name plate ({width} px, at most {PLATE_TEXT_WIDTH})'
    return None


def rename_prefill(state: dict, cid: int) -> str:
    """The text the rename dialog starts with: the slot's English name, nothing for an unnamed / open one."""
    c = characters(state).get(cid) or {}
    if c.get('default_name'):
        return c['default_name']
    name = (c.get('name') or {}).get('en') or ''
    return '' if name == UNNAMED else name


# --------------------------------------------------------------------------
# Voice and stats
# --------------------------------------------------------------------------

PLAYER_END = 0x4D                    # stock player IDs: the stats sources (Roster/ids.py PLAYER_END)


def _labelled(state: dict, cid: int) -> str:
    return f'{known_name(state, cid)} ({hex_id(cid)})'


def stats_choices(state: dict, cid: int) -> list[tuple[str, str | None]]:
    """``(label, source)`` for the Stats dialog: the default first (``None``: a stock ID's own stats, a new ID's
    template's), then every stock player on the grid."""
    c = characters(state).get(cid) or {}
    default = c.get('template') if c.get('template') is not None else cid
    first = (f'Default: its template {_labelled(state, default)}' if c.get('template') is not None
             else f'Default: its own ({known_name(state, cid)})')
    sources = sorted(i for i in characters(state) if i < PLAYER_END)
    return [(first, None)] + [(_labelled(state, i), hex_id(i)) for i in sources if i != default]


def voice_choices(state: dict, index: int) -> list[tuple[str, str | None]]:
    """``(label, source)`` for the Voice dialog: the default first (a stock square's own voice; a new square:
    none set), then every stock square's head (one voice per species)."""
    sq = state['squares'][index]
    heads = sorted({s['head'] for s in state['squares'] if s['kind'] == 'stock'})
    if sq['kind'] == 'stock':
        first = f'Default: its own ({known_name(state, sq["head"])})'
        heads = [h for h in heads if h != sq['head']]
    else:
        first = 'Default: none set (each member speaks with its own species\' voice)'
    return [(first, None)] + [(_labelled(state, h), hex_id(h)) for h in heads]


def stats_text(state: dict, cid: int) -> str:
    c = characters(state).get(cid) or {}
    if c.get('stats') is None:
        return 'Stats: not read'
    return f'Stats now: {_labelled(state, c["stats"])}'


def voice_text(state: dict, index: int) -> str:
    sq = state['squares'][index]
    shown = f'Voice now: {_labelled(state, sq["voice"])}'
    return shown + (' (set)' if sq.get('voice_set') is not None else '')


# Character voices (Roster/char_voices.py): a slot's own voice, a stock voice and/or a custom voice pack
CHAR_VOICE = 'char_voice'                     # the edit op (Roster/slot_plan.CHAR_VOICE, test-pinned)
SPECIES_CLIP = 'species_clip'                 # one moment of a stock voice (Roster/slot_plan.SPECIES_CLIP)
SPECIES_CLIPS = 'species_clips'               # its effects key
CHAR_VOICE_DEFAULT = 'Default: its square\'s voice'
MOMENT_KEYS = ('v1', 'v3', 'v4', 'v5', 'v6', 'v7', 'v8', 'v10', 'v11', 'v12', 'v13', 'v14')   # Sound/sound_packs, pinned


def char_voice_choices(state: dict, cid: int, packs=()) -> list[tuple[str, str | None, str | None]]:
    """``(label, source, pack)`` for the slot's Voice dialog: the default first (its square's voice), every stock
    square's head (one voice per species), then every custom voice pack (``packs``: ``[(name, base voice ID or None,
    clip count)]``) as "pack NAME (N custom clips on VOICE's voice)"."""
    heads = sorted({s['head'] for s in state['squares'] if s['kind'] == 'stock'})
    out = [(CHAR_VOICE_DEFAULT, None, None)]
    out += [(_labelled(state, h), hex_id(h), None) for h in heads]
    out += [(_pack_label(state, name, base, count), None, name) for name, base, count in packs]
    return out


def _pack_label(state: dict, name: str, base: int | None, count: int) -> str:
    rest = f'{known_name(state, base)}\'s voice' if base is not None else 'its square\'s voice'
    return f'Pack {name} ({count} custom clip{"s" if count != 1 else ""}, the rest {rest})'


SQUARE_PACK = 'square_pack'                   # a square Voice dialog value: ``(SQUARE_PACK, pack name)``


def square_pack_choices(state: dict, packs=()) -> list[tuple[str, tuple[str, str]]]:
    """``(label, (SQUARE_PACK, name))`` for the square's Voice dialog, after ``voice_choices``: a pack is staged as
    every member's own voice (``square_pack_edits``); the square voice itself holds a stock voice only."""
    return [(_pack_label(state, name, base, count) + ', as every member\'s own voice', (SQUARE_PACK, name))
            for name, base, count in packs]


def square_pack_edits(state: dict, index: int, pack: str) -> list[dict]:
    """One ``char_voice`` edit per member of square ``index`` that gives it the voice pack ``pack``."""
    return [char_voice_edit(cid, None, pack) for cid in state['squares'][index]['members']]


def char_voice_text(state: dict, cid: int) -> str:
    """The slot level's effective-voice line: ``Voice: Bowser (own)``, ``Voice: Mario (square)`` or
    ``Voice: pack Rosalina (own, 5 custom clips on Bowser's voice)``."""
    c = characters(state).get(cid) or {}
    if 'voice' not in c:
        return 'Voice: not read'
    clips = c.get('char_clips') or c.get('char_clip_ids') or {}
    if c.get('char_voice') is None and not clips:
        return f'Voice: {known_name(state, c["voice"])} (square)'
    if not clips:
        return f'Voice: {known_name(state, c["voice"])} (own)'
    n = len(clips)
    base = (f'{known_name(state, c["char_voice"])}\'s voice' if c.get('char_voice') is not None
            else 'its square\'s voice')
    label = f'pack {c["char_pack"]}' if c.get('char_pack') else 'custom clips'
    return f'Voice: {label} (own, {n} custom clip{"s" if n != 1 else ""} on {base})'


def char_voice_edit(cid: int, source: str | None, pack: str | None) -> dict:
    out = {'op': CHAR_VOICE, 'id': hex_id(cid), 'source': source}
    if pack:
        out['pack'] = pack
    return out


def species_clip_edit(head: int, moment: str, file: str | None, origin: str | None = None) -> dict:
    """The pending edit that replaces ``moment`` of ``head``'s species voice (``file``: the staged clip, None: the
    stock clip back; ``origin``: the user's WAV, for the texts)."""
    out = {'op': SPECIES_CLIP, 'id': hex_id(head), 'moment': moment, 'file': file}
    if origin:
        out['origin'] = origin
    return out


def char_voice_current(state: dict, cid: int, pending: 'PendingEdits | None') -> tuple[str | None, str | None]:
    """``(source, pack)`` the Voice dialog starts on: the pending edit's, else what the game holds."""
    edit = pending.value_edit(cid, CHAR_VOICE) if pending is not None else None
    if edit is not None:
        return edit.get('source'), edit.get('pack')
    c = characters(state).get(cid) or {}
    if c.get('char_pack'):
        return None, c['char_pack']
    return (hex_id(c['char_voice']) if c.get('char_voice') is not None else None), None


def staged_clip_files(pending: 'PendingEdits') -> set[str]:
    return {e['file'] for e in pending.edits if e['op'] == SPECIES_CLIP and e.get('file')}


def prune_staged_clips(folder: str, keep: set[str]) -> None:
    """Delete staged clips no pending edit uses any more."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    keep = {os.path.normcase(os.path.abspath(p)) for p in keep}
    for name in names:
        path = os.path.join(folder, name)
        if name.endswith('.rwav') and os.path.normcase(os.path.abspath(path)) not in keep:
            try:
                os.remove(path)
            except OSError:
                pass


def voice_reach(state: dict, index: int) -> str:
    """Who a square's voice reaches (``Roster/voices.py``, the grid step's square voices)."""
    if state['squares'][index]['kind'] == 'stock':
        return ('A stock square speaks as one species: the voice changes for every member of its wheel (spare '
                'rows and new IDs on it included), on the select screen and on the field. Nothing else changes.')
    return ('A new square\'s voice reaches its members without a colour wheel; members on a wheel keep their '
            'wheel\'s voice. It plays on the select screen and on the field.')


def choice_index(choices: list, source: str | None) -> int:
    return next((i for i, (_label, value) in enumerate(choices) if value == source), 0)


# --------------------------------------------------------------------------
# Portraits
# --------------------------------------------------------------------------

FRONT, SIDE = 'front', 'side'
SIDE_ALT = 'side_alt'                    # the alternate side view (Roster/icons.ALT_VIEW): facing the other way
VIEW_LABELS = {FRONT: 'front', SIDE: 'side', SIDE_ALT: 'alternate side'}
HAS_ALT_TEXT = 'Has alternate side icon'
SET_ALT_TEXT = '[Set Side Alt Icon]'


def view_label(view: str) -> str:
    return VIEW_LABELS.get(view, view)


def has_alt_icon(state: dict, cid: int) -> bool:
    """Whether the slot shows an alternate side portrait of its own (a cell distinct from the side and front
    ones; most characters mirror their side cell instead)."""
    try:
        from Roster import state_icons
    except ImportError:
        from SluggiesTools.Roster import state_icons
    return state_icons.has_alternate((characters(state).get(cid) or {}).get('icon'))


def icon_ref(state: dict, cid: int, view: str) -> dict | None:
    """Where the game takes ``cid``'s ``view`` portrait from (``Roster/state_icons.resolve``), or None."""
    return ((characters(state).get(cid) or {}).get('icon') or {}).get(view)


def icon_file(state: dict, state_path: str, cid: int, view: str) -> str | None:
    """The crop PNG of a portrait (``_gui/icons/...``), or None when there is none on disk."""
    ref = icon_ref(state, cid, view)
    if not ref or not ref.get('file'):
        return None
    path = os.path.join(os.path.dirname(state_path), state.get('icon_dir') or 'icons', ref['file'])
    return path if os.path.isfile(path) else None


def is_fallback(state: dict, cid: int, view: str) -> bool:
    ref = icon_ref(state, cid, view)
    return bool(ref) and ref['source'] != 'own'


def icon_note(state: dict, cid: int, view: str) -> str:
    """Where a portrait comes from, for the GUI's fallback marks."""
    ref = icon_ref(state, cid, view)
    if not ref:
        return 'no portrait read'
    source = ref['source']
    if source == 'own':
        return 'own portrait'
    if source == 'neighbour':
        return f'no own portrait: shows {known_name(state, ref["key"])} ({hex_id(ref["key"])}), the next lower key'
    if source == 'template':
        return f'no own portrait: shows its template {known_name(state, ref["alias"])} ({hex_id(ref["alias"])})'
    if source == 'mii':
        return 'Mii icon'
    return 'shows the "?" icon'


def square_label(state: dict, index: int) -> str:
    return name_of(state, state['squares'][index]['head'])


def slot_count(state: dict, index: int) -> int:
    """How many slots (colour swatches) a square has: the number the grid shows on it."""
    return len(state['squares'][index]['members'])


def square_tooltip(state: dict, index: int) -> list[str]:
    sq = state['squares'][index]
    lines = [f'{"Stock" if sq["kind"] == "stock" else "New"} square: {name_of(state, sq["head"])}',
             f'Voice: {name_of(state, sq["voice"])} ({hex_id(sq["voice"])})',
             'Members:'] + [f'  {hex_id(m)}  {name_of(state, m)}' for m in sq['members']]
    if is_fallback(state, sq['head'], FRONT):
        lines.append(f'Portrait: {icon_note(state, sq["head"], FRONT)}')
    if sq['head'] == LUIGI and not state.get('luigi_own_square', True):
        lines.append('(not on the stock grid: the game gives Luigi a captain\'s square at runtime; '
                     'shown beside the grid so his slots stay reachable)')
    return lines


def stock_luigi_note(state: dict) -> str:
    if state.get('luigi_own_square', True):
        return ''
    return ('Luigi has no square on the stock grid (the game hands him a captain\'s square at runtime); '
            'he is shown to the right of the grid.')


def slot_details(state: dict, cid: int) -> list[str]:
    c = characters(state).get(cid, {'id': cid})
    lines = [f'ID: {hex_id(cid)}']
    if c.get('own_model_dir'):
        source = known_name(state, c['model_source'])
        if source != hex_id(c['model_source']):
            source += f' ({hex_id(c["model_source"])})'
        lines.append(f'Model: own directory {c["model_dir"]} (files of {source})')
    elif c.get('template') is not None:
        lines.append(f'Model: template {name_of(state, c["template"])} ({hex_id(c["template"])}), '
                     f'directory {c["model_dir"]}')
    elif 'model_dir' in c:
        lines.append(f'Model: own, directory {c["model_dir"]}')
    blocks = c.get('blocks') or {}
    if blocks:
        lines.append('Blocks: ' + ', '.join(
            f'{label} {blocks[role]["length"] / (1024 * 1024):.2f} MB [{blocks[role]["sha1"][:8]}]'
            for role, label in (('high', 'High'), ('low', 'Low')) if role in blocks))
    if 'stats' in c:
        lines.append(f'Stats: {name_of(state, c["stats"])}' + ('' if c['stats'] == cid else f' ({hex_id(c["stats"])})'))
    if 'voice' in c:
        lines.append(char_voice_text(state, cid))
    edits = stat_edits_of(state, cid)
    if edits:
        lines.append(f'Stat edits: {stat_edits_text(edits)}')
    size = size_text(state, cid)
    if size:
        lines.append(size)
    animations = animations_text(c)
    if animations:
        lines.append(animations)
    if c.get('default_name'):
        lines.append(f'Name: the game text is still {c["name"]["en"]!r}; shown with its usual name')
    if has_alt_icon(state, cid):
        lines.append(HAS_ALT_TEXT)
    return lines


def animations_text(char: dict) -> str | None:
    """The slot summary's animations line: stock, or which banks (files 6-14) are custom; None without a read."""
    banks = char.get('animations')
    if not banks:
        return None
    custom = [e['file'] for e in banks if e.get('vanilla') is False]
    if custom:
        return f'Animations: custom (bank{"s" if len(custom) != 1 else ""} {", ".join(map(str, custom))})'
    if any(e.get('vanilla') is None for e in banks):
        return 'Animations: unknown (1_Input could not be read)'
    return 'Animations: stock'


# --------------------------------------------------------------------------
# Portrait replacement
# --------------------------------------------------------------------------

MII_START, NEW_START = 0x4D, 0x66    # Mii IDs have no portrait records (Roster/slot_plan.has_portrait_records)
STAGED_ICONS_REL = os.path.join('_gui', 'slot', 'staged_icons')
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tga', '.webp')   # the picker's filter only
IMAGE_PATTERNS = ';'.join('*' + ext for ext in IMAGE_EXTENSIONS)
IMAGE_FILTERS = [('Images', IMAGE_PATTERNS), ('All files', '*.*')]
FIT_CHOICES = (('contain', 'Fit inside'), ('cover', 'Fill and crop'), ('strict', 'Exact 48x51'))
ICON_SIZE = (48, 51)


def can_replace_portrait(cid: int) -> bool:
    return cid < MII_START or cid >= NEW_START


def portrait_tip(cid: int, view: str) -> str:
    label = view_label(view)
    if not can_replace_portrait(cid):
        return f'{label.capitalize()} portrait (Miis show the Mii icon; it cannot be replaced)'
    if view == SIDE_ALT:
        return ('Click to pick an alternate side portrait: the side view facing the other way. Without one the '
                'game mirrors the side portrait; characters with asymmetric art (Luigi\'s "L", Waluigi\'s "Γ") '
                'carry a second drawing instead. "Clear slot" drops it again where the character had none.')
    return f'Click to replace the {label} portrait'


def _icon_import():
    try:
        from Roster import icon_import
    except ImportError:
        from SluggiesTools.Roster import icon_import
    return icon_import


class IconPreview:
    """The icon dialog's state: the picked image (read and checked once, ``Roster/icon_import``), the fit mode and
    the trim switch; ``result()`` redraws the 48x51 portrait in-process (Pillow only)."""

    def __init__(self, path: str):
        self.path = path
        self.fit, self.trim = 'contain', True
        self.source, self.error = None, None
        self._cache = {}
        icon_import = _icon_import()
        try:
            self.source = icon_import.load_user_image(path)
        except icon_import.IconImportError as exc:
            self.error = str(exc)

    def result(self):
        """``(48x51 RGBA portrait or None, warnings, refusal or None)`` for the current fit and trim."""
        if self.source is None:
            return None, [], self.error
        key = (self.fit, self.trim)
        if key not in self._cache:
            icon_import = _icon_import()
            try:
                image, warns = icon_import.fit_user_image(self.source.image, self.fit, self.trim)
                self._cache[key] = (image, self.source.warnings + warns, None)
            except icon_import.IconImportError as exc:
                self._cache[key] = (None, list(self.source.warnings), str(exc))
        return self._cache[key]

    def lines(self) -> list:
        """The dialog's message lines: the source's facts, notes, warnings and a refusal."""
        if self.source is None:
            return [(f'Refused: {self.error}', ERROR)]
        w, h = self.source.size
        lines = [(f'{os.path.basename(self.path)}: {self.source.format}, {w}x{h}', TEXT)]
        lines += [(note, TEXT) for note in self.source.notes]
        _image, warns, error = self.result()
        lines += [(f'Warning: {w_}', WARN) for w_ in warns]
        if error:
            lines.append((f'Refused: {error}', ERROR))
        return lines

    @property
    def ok(self) -> bool:
        return self.result()[0] is not None

    def save(self, folder: str, cid: int, view: str) -> str:
        """Write the portrait into ``folder`` (named by its pixels, so a preview texture never shows a stale file);
        returns its path."""
        import hashlib
        image = self.result()[0]
        digest = hashlib.sha1(image.tobytes()).hexdigest()[:12]
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f'{cid:02X}_{view}_{digest}.png')
        image.save(path, 'PNG')
        return path

    def edit(self, cid: int, view: str, staged: str) -> dict:
        """The staged icon edit: the normalised copy, taken as it is (strict, no trim)."""
        return {'op': 'icon', 'id': hex_id(cid), 'view': view, 'file': staged, 'fit': 'strict', 'trim': False,
                'origin': self.path}


SOURCE_BOX = (240, 240)


def source_thumbnail(image, box=SOURCE_BOX):
    """The icon dialog's view of the picked image: scaled to fit ``box`` (up by whole factors, nearest; down
    smoothly)."""
    from PIL import Image
    w, h = image.size
    factor = min(box[0] / w, box[1] / h)
    if factor >= 1:
        whole = int(factor)
        return image.resize((w * whole, h * whole), Image.Resampling.NEAREST) if whole > 1 else image.copy()
    return image.resize((max(1, round(w * factor)), max(1, round(h * factor))), Image.Resampling.LANCZOS)


def staged_icon_files(pending: 'PendingEdits') -> set[str]:
    return {e['file'] for e in pending.edits if e['op'] == 'icon' and e.get('file')}


def prune_staged_icons(folder: str, keep: set[str]) -> None:
    """Delete normalised portraits no pending edit uses any more."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    keep = {os.path.normcase(os.path.abspath(p)) for p in keep}
    for name in names:
        path = os.path.join(folder, name)
        if name.endswith('.png') and os.path.normcase(os.path.abspath(path)) not in keep:
            try:
                os.remove(path)
            except OSError:
                pass


# --------------------------------------------------------------------------
# Navigation
# --------------------------------------------------------------------------

GRID, SQUARE, SLOT = 'grid', 'square', 'slot'


class GridNav:
    """The open pop-out levels. A square is remembered by its head character, so a refresh reopens it even when
    the square moved; a one-member square opens straight at the slot level (``skipped``)."""

    def __init__(self, state: dict | None = None):
        self.state = state
        self.square_head = None        # head character of the open square, or None
        self.slot = None               # ID of the open slot, or None
        self.skipped = False           # the slot was opened straight from the grid (one-member square)

    # -- queries
    @property
    def level(self) -> str:
        if self.slot is not None:
            return SLOT
        if self.square_head is not None:
            return SQUARE
        return GRID

    def square_index(self) -> int | None:
        if self.state is None or self.square_head is None:
            return None
        return next((i for i, sq in enumerate(self.state['squares']) if sq['head'] == self.square_head), None)

    def levels(self) -> list[str]:
        """The open levels above the grid, bottom first (what is drawn)."""
        if self.level == GRID:
            return []
        if self.level == SQUARE:
            return [SQUARE]
        return [SLOT] if self.skipped else [SQUARE, SLOT]

    # -- actions
    def open_square(self, index: int) -> None:
        sq = self.state['squares'][index]
        self.square_head = sq['head']
        if len(sq['members']) == 1:
            self.slot, self.skipped = sq['members'][0], True
        else:
            self.slot, self.skipped = None, False

    def open_slot(self, cid: int) -> None:
        if self.level != SQUARE:
            raise ValueError('a slot opens from the square level')
        self.slot, self.skipped = cid, False

    def back(self) -> bool:
        """One level back (outside click, Esc). False when already at the grid."""
        if self.level == SLOT and not self.skipped:
            self.slot = None
        elif self.level in (SLOT, SQUARE):
            self.square_head = self.slot = None
            self.skipped = False
        else:
            return False
        return True

    def click(self, inside_top_box: bool) -> bool:
        """A mouse click anywhere: outside the top level's box goes back one level."""
        return False if inside_top_box else self.back()

    def refresh(self, state: dict) -> None:
        """New state: reopen the same square / ID, one level lower for what no longer exists."""
        self.state = state
        index = self.square_index()
        if index is None:
            self.square_head = self.slot = None
            self.skipped = False
            return
        members = state['squares'][index]['members']
        if self.slot is not None and self.slot not in members:
            self.slot = None
            if self.skipped:
                self.square_head, self.skipped = None, False
        if self.slot is None and self.square_head is not None and len(members) == 1:
            self.open_square(index)           # now a one-member square: straight to its slot
        elif self.skipped and len(members) > 1:
            self.skipped = False              # the square grew: the slot now sits above its square


# --------------------------------------------------------------------------
# Staged edits: the pending list and its overlay
# --------------------------------------------------------------------------

class PendingEdits:
    """The pending slot edits, in GUI memory only. ``edits`` is the merged list in staging order
    (``plan['merged']`` of the last staging check), ``sections`` the planner's per-edit sections (notes,
    effects) that the overlay shows. Both survive a re-read; Patch Game re-plans everything on a fresh one.

    ``pack``: a staged roster pack load (``stage_pack``), or None. It replaces the whole roster, so it is the only
    pending edit while it is staged: staging it drops the slot edits, and slot edits cannot be staged on top of it
    (they would be planned against the game before the load).

    ``gecko``: the slots whose pending edits break Gecko code compatibility (``note_gecko``, session memory).
    ``memory``: the slots the pending edits leave over the player memory (``note_memory``: {id: the check's line})."""

    def __init__(self):
        self.edits: list[dict] = []
        self.sections: list[dict] = []
        self.pack: dict | None = None      # {'path', 'name', 'diff': {id: fields or status}}
        self.gecko: set[int] = set()
        self.memory: dict[int, str] = {}

    def __len__(self) -> int:
        return len(self.edits) + (1 if self.pack else 0)

    def stage_pack(self, path: str, plan: dict) -> None:
        """Stage the load of the roster pack at ``path`` (its dry-run ``plan``): the slot edits are dropped."""
        diff = {int(d['id'], 16): (d['fields'] if d['status'] == 'differs' else d['status'])
                for d in plan.get('diff') or [] if d['status'] != 'same'}
        self.clear()
        self.pack = {'path': path, 'name': os.path.basename(path), 'diff': diff}

    def _pack_lines(self, cid: int) -> list[str]:
        if not self.pack or cid not in self.pack['diff']:
            return []
        what = self.pack['diff'][cid]
        what = ('changes ' + ', '.join(PACK_FIELDS.get(f, f) for f in what) if isinstance(what, list)
                else DIFF_TEXT.get(what, what))
        return [f'Pending: load {self.pack["name"]}: {what}']

    def staging(self, edit: dict | list[dict], drop_op: str | None = None) -> dict:
        """The edits file of a staging check: the pending edits (build checks passed when they were staged)
        plus the new one (or several, staged together). ``drop_op``: leave out the pending edits of that op (a
        gameplay settings load replaces the pending gameplay edits)."""
        new = edit if isinstance(edit, list) else [edit]
        return {'edits': [dict(e, checked=True) for e in self.edits if e['op'] != drop_op] + [dict(e) for e in new]}

    def to_file(self) -> dict:
        """The edits file Patch Game writes: every build check runs again."""
        return {'edits': [dict(e) for e in self.edits]}

    def accept(self, plan: dict, gecko: dict | None = None, memory: dict | None = None) -> None:
        """Take the staging check's merged list (the new edit staged, earlier ones replaced or joined), its
        Gecko code compatibility result (``note_gecko``) and its player memory result (``note_memory``)."""
        self.edits = [dict(e) for e in plan.get('merged') or []]
        self.sections = [dict(s) for s in plan.get('edits') or []]
        self.note_gecko(plan, gecko or {})
        self.note_memory(memory or {})

    def note_memory(self, check: dict) -> None:
        """Which pending slots will not fit their player memory (``check``: ``memory_guard.read_details`` of a
        staging check, which projects every pending edit, so it replaces the earlier result)."""
        self.memory = {int(p['id'], 16): p['text'] for p in check.get('problems') or []
                       if p.get('id') and self.has(int(p['id'], 16))}

    def over_memory(self, cid: int) -> bool:
        return cid in self.memory and self.has(cid)

    def square_memory(self, state: dict, index: int) -> bool:
        return any(self.over_memory(m) for m in state['squares'][index]['members'])

    def memory_line(self, cid: int) -> str | None:
        """The red line of a slot over the player memory (tooltips, the slot level), or None."""
        return f'Player memory: {self.memory[cid]}' if self.over_memory(cid) else None

    def note_gecko(self, plan: dict, check: dict) -> None:
        """Which slots break Gecko code compatibility, after a staging check (``check``:
        ``gecko_compat.read_details``, ``{}`` when the files are incompatible already or nothing breaks it).
        The new edits' slots (sections not ``checked``; stat edits only write in place) are marked when they break
        it on their own (``new_breaks``), or when no other pending slot is marked: then they complete the break.
        With another slot marked and no answer of their own (``new_breaks`` None), they stay as they were."""
        self.gecko &= {int(e['id'], 16) for e in self.edits}
        if not check:
            self.gecko.clear()
            return
        new = {int(s['target'], 16) for s in plan.get('edits') or []
               if not s.get('checked') and s.get('action') not in (STAT_EDITS, STAT_RESET) + GAME_OPS}
        if check.get('new_breaks') or not self.gecko - new:
            self.gecko |= new

    def breaks_gecko(self, cid: int) -> bool:
        return cid in self.gecko and self.has(cid)

    def square_gecko(self, state: dict, index: int) -> bool:
        return any(self.breaks_gecko(m) for m in state['squares'][index]['members'])

    def discard(self, cid: int) -> None:
        """Drop the slot's pending edits; a pending pack load touching the slot is dropped as a whole."""
        self.edits = [e for e in self.edits if int(e['id'], 16) != cid]
        self.sections = [s for s in self.sections if int(s['target'], 16) != cid]
        self.gecko.discard(cid)
        self.memory.pop(cid, None)
        if self.pack and cid in self.pack['diff']:
            self.pack = None

    def discard_op(self, op: str) -> None:
        """Drop the pending edits of a game-wide ``op`` (``GAMEPLAY``, ``STAT_EDITS``)."""
        self.edits = [e for e in self.edits if e['op'] != op]
        self.sections = [s for s in self.sections if s['action'] != op]

    def clear(self) -> None:
        self.edits, self.sections, self.pack = [], [], None
        self.gecko = set()
        self.memory = {}

    def has(self, cid: int) -> bool:
        return any(int(e['id'], 16) == cid for e in self.edits) or bool(self._pack_lines(cid))

    def model_edit(self, cid: int) -> dict | None:
        return next((e for e in self.edits if int(e['id'], 16) == cid and e['op'] in MODEL_OPS), None)

    def value_edit(self, cid: int, op: str) -> dict | None:
        """The slot's pending ``op`` edit ('rename', 'stats', 'char_voice', or 'voice': staged on the square's head)."""
        return next((e for e in self.edits if int(e['id'], 16) == cid and e['op'] == op), None)

    def species_clip_edit(self, head: int, moment: str) -> dict | None:
        """The pending ``species_clip`` edit of ``moment`` on the species whose square head is ``head``."""
        return next((e for e in self.edits if int(e['id'], 16) == head and e['op'] == SPECIES_CLIP
                     and e.get('moment') == moment), None)

    def species_clip_edits(self, head: int) -> list[dict]:
        return [e for e in self.edits if int(e['id'], 16) == head and e['op'] == SPECIES_CLIP]

    def drop_species_clip(self, head: int, moment: str) -> None:
        self.edits = [e for e in self.edits if not (int(e['id'], 16) == head and e['op'] == SPECIES_CLIP
                                                    and e.get('moment') == moment)]
        self.sections = [s for s in self.sections if not (int(s['target'], 16) == head and s['action'] == SPECIES_CLIP
                                                          and (s.get('edit') or {}).get('moment') == moment)]

    def char_voice_users(self, pack: str) -> list[int]:
        """The slots whose pending voice edit assigns the pack ``pack``."""
        return [int(e['id'], 16) for e in self.edits if e['op'] == CHAR_VOICE and e.get('pack') == pack]

    def icon_edit(self, cid: int, view: str) -> dict | None:
        """The slot's pending portrait edit of ``view``."""
        return next((e for e in self.edits if int(e['id'], 16) == cid and e['op'] == 'icon' and e.get('view') == view),
                    None)

    def equip_edit(self, cid: int, file: int) -> dict | None:
        """The slot's pending equipment edit (``equip`` / ``equip_clear``) for slot file ``file`` (2-5)."""
        return next((e for e in self.edits if int(e['id'], 16) == cid and e['op'] in EQUIP_OPS
                     and e.get('file') == file), None)

    def sections_for(self, cid: int) -> list[dict]:
        return [s for s in self.sections if int(s['target'], 16) == cid]

    def square_pending(self, state: dict, index: int) -> bool:
        return any(self.has(m) for m in state['squares'][index]['members'])

    def stat_edits_for(self, cid: int) -> list[dict]:
        """The pending stat edit files (bridged in from the Stat Editor, staged game-wide) that name slot ``cid``."""
        return [e for e in self.edits if e['op'] == STAT_EDITS and cid in stat_file_ids(e.get('file'))]

    def has_stats(self, cid: int) -> bool:
        return bool(self.stat_edits_for(cid))

    def square_stats(self, state: dict, index: int) -> bool:
        return any(self.has_stats(m) for m in state['squares'][index]['members'])

    def stat_summary(self, cid: int) -> list[str]:
        """One line per pending stat edit file that names the slot (tooltips, the slot level)."""
        return [_edit_title(e) for e in self.stat_edits_for(cid)]

    def summary(self, cid: int) -> list[str]:
        """One line per pending edit of the slot (tooltips)."""
        return [_edit_title(e) for e in self.edits if int(e['id'], 16) == cid] + self._pack_lines(cid)

    def lines(self, cid: int) -> list[str]:
        """The slot level's pending lines: each edit, then what the slot shows once it is written."""
        out = []
        for section in self.sections_for(cid):
            effects = section.get('effects') or {}
            if section['action'] == 'rename':                 # the title names the result: no "Name:" line
                shown = effects.get('name')
                reset = not section['edit'].get('text') and shown
                out.append(f'Pending: reset the name to {shown if shown.startswith("its ") else repr(shown)}'
                           if reset else _edit_title(section['edit']))
                continue
            out.append(_edit_title(section['edit']))
            for key, label in (('model', 'Model'), ('name', 'Name'), ('stats', 'Stats'), ('voice', 'Voice'),
                               (CHAR_VOICE, 'Own voice'), (SPECIES_CLIPS, 'Voice pack'),
                               ('portrait_note', 'Portraits'), (STAT_EDITS, 'Stat edits')):
                if effects.get(key) and not (key == 'portrait_note' and section['action'] == 'icon'):
                    out.append(f'  {label}: {effects[key]}')
            for key, label in (('equipment', 'Equipment'), ('animations', 'Animations')):
                value = effects.get(key)
                if isinstance(value, dict):
                    out += [f'  {label}: {text}' for _file, text in sorted(value.items())]
                elif value:
                    out.append(f'  {label}: {value}')
            if effects.get('portraits') and section['action'] == COPY:
                out.append('  Portraits: copied (previewed, marked "pending")')
            if effects.get('portraits') and section['action'] == SLGMODEL:
                out.append('  Portraits: from the character file (previewed, marked "pending")')
            if effects.get('portraits') and section['action'] == 'patch':
                out.append('  Portraits: ' + os.path.basename(os.path.dirname(os.path.dirname(
                    effects['portraits']['front']))) + ' (previewed, marked "pending")')
        return out + self._pack_lines(cid)

    def titles(self) -> list[tuple[str | None, str]]:
        """``(slot id or None, text)`` per pending edit, for the discard / save questions."""
        out = [(None if e['op'] in GAME_OPS else e['id'], _edit_title(e).removeprefix('Pending: '))
               for e in self.edits]
        if self.pack:
            n = len(self.pack['diff'])
            out.append((None, f'load the roster pack {self.pack["name"]} ({n} slot{"s" if n != 1 else ""} change)'))
        return out

    def portrait(self, cid: int, view: str) -> str | None:
        """A pending portrait to preview (the model folder's ``icon/*.png``), or None."""
        for section in reversed(self.sections_for(cid)):
            path = ((section.get('effects') or {}).get('portraits') or {}).get(view)
            if path and os.path.isfile(path):
                return path
        return None


def _edit_title(edit: dict) -> str:
    if edit['op'] == STAT_EDITS:
        size = size_file(edit.get('file'))
        package = package_stat_file(edit.get('file')) if not size else None
        return (f'Pending: size {size[1]:g}x' if size else f'Pending: stat values from {package}' if package
                else 'Pending: stat editor value changes')
    if edit['op'] == GAMEPLAY:
        return f'Pending: gameplay settings: {gameplay_item_label(edit.get("file"))}'
    if edit['op'] == GAME_OPTION:
        state, _colon, key = (edit.get('file') or '').partition(':')
        return f'Pending: turn {state} {GAME_OPTION_TITLES.get(key, key)}'
    if edit['op'] in MODEL_FILE_TILES:
        return f'Pending: {MODEL_FILE_TILES[edit["op"]].noun} {os.path.basename(edit.get("file") or "?")}'
    if edit['op'] == 'equip':
        what = EQUIP_LABELS.get(edit.get('file'), 'equipment').lower()
        where = f' (file {edit["file"]})' if edit.get('file') in (5,) else ''
        bundled = ' (bundled with the model)' if edit.get('origin') == 'bundled' else ''
        return f'Pending: {what}{where} from {os.path.basename(edit.get("sluggie") or "?")}{bundled}'
    if edit['op'] == 'equip_clear':
        return f'Pending: reset the {EQUIP_LABELS.get(edit.get("file"), "equipment").lower()}'
    if edit['op'] == ANIM:
        bank = f'animation bank {edit["file"]}' if edit.get('file') is not None else 'an animation bank'
        return f'Pending: {bank} from {os.path.basename(edit.get("anm") or "?")}'
    if edit['op'] == 'patch':
        files = [os.path.basename(edit['file'])] + ([os.path.basename(edit['low'])] if edit.get('low') else [])
        return 'Pending: put ' + ' + '.join(files) + ' into this slot'
    if edit['op'] == 'clear':
        return 'Pending: clear this slot'
    if edit['op'] == COPY:
        return f'Pending: paste a clone of {edit.get("source")}'
    if edit['op'] == SLGMODEL:
        return f'Pending: import the character file {os.path.basename(edit.get("file") or "?")} into this slot'
    if edit['op'] == 'rename':
        return f'Pending: rename to {edit["text"]!r}' if edit.get('text') else 'Pending: reset the name'
    if edit['op'] == 'stats':
        return f'Pending: play with {edit["source"]}\'s stats' if edit.get('source') else 'Pending: default stats'
    if edit['op'] == 'voice':
        return (f'Pending: the square speaks with {edit["source"]}\'s voice' if edit.get('source')
                else 'Pending: the square\'s default voice')
    if edit['op'] == CHAR_VOICE:
        if edit.get('pack'):
            on = f' on {edit["source"]}\'s voice' if edit.get('source') else ''
            return f'Pending: speaks with the voice pack {edit["pack"]!r}{on} as its own'
        return (f'Pending: speaks with {edit["source"]}\'s voice as its own' if edit.get('source')
                else 'Pending: speaks with its square\'s voice again')
    if edit['op'] == SPECIES_CLIP:
        moment = edit.get('moment', '?')
        if edit.get('file'):
            return f'Pending: voice pack clip {moment} from {os.path.basename(edit.get("origin") or edit["file"])}'
        return f'Pending: voice pack clip {moment} back to the stock clip'
    if edit['op'] == 'icon':
        return f'Pending: {view_label(edit.get("view"))} portrait from {icon_label(edit)}'
    if edit['op'] == STAT_RESET:
        return 'Pending: clear its stat edits'
    return f'Pending: {edit["op"]}'


def icon_label(edit: dict) -> str:
    """The user's file name of an icon edit (``origin``: the picked file, ``file``: its normalised copy)."""
    return os.path.basename(edit.get('origin') or edit.get('file') or '?')


# --------------------------------------------------------------------------
# Equipment tiles
# --------------------------------------------------------------------------

EQUIP_OPS = ('equip', 'equip_clear')
ANIM = 'anim'                                 # one animation bank from an .anm file (Roster/slot_plan, test-pinned)
ANIM_FILTERS = [('Animation banks', '*.anm'), ('All files', '*.*')]
COPY = 'copy'                                 # paste: the slot becomes a clone (Roster/slot_plan, test-pinned)
SLGMODEL = 'slgmodel'                         # a character file of the Sluggers Characters patcher (test-pinned)
SLGMODEL_EXTENSION = '.slgmodel'
SLGMODEL_FILTERS = [('Character files', '*.slgmodel'), ('All files', '*.*')]
MODEL_OPS = ('patch', 'clear', COPY, SLGMODEL)
# A stat edit (the stat editor's edit file) belongs to no slot: its "id" is GAME_WIDE (Roster/slot_plan, test-pinned)
STAT_EDITS, GAME_WIDE = 'stat_edits', 0xFF
GAMEPLAY = 'gameplay'                  # gameplay settings (Gameplay tab): game-wide too
ITEM = 'item'                          # an item model .sluggie (the grid's item tile): game-wide too (test-pinned)
STADIUM = 'stadium'                    # a stadium model .sluggie (the grid's stadium tile): game-wide too (test-pinned)
GAME_OPTION = 'game_option'            # a game option on / off (Gameplay tab, CPU vs CPU): game-wide (test-pinned)
GAME_OPTION_TITLES = {'cpu_vs_cpu': 'CPU vs CPU', 'cpu_management': 'CPU vs CPU management',
                      **{f'close_play_{p}': f'CPU vs CPU close play odds {p}%' for p in CLOSE_PLAY_LEVELS},
                      'triple_take': 'CPU triple take replays', 'strikeout_camera': 'CPU strikeout camera',
                      'outfield_dives': 'CPU outfield dives and jumps', 'no_precharge': 'No CPU precharge',
                      'star_pitch_buff': 'Star pitch buff (Waluigi, Yoshi, Peach)',
                      'duplicate_characters': 'Duplicate characters', 'duplicate_captains': 'Duplicate captains',
                      'all_captains': 'Every character a captain',
                      'captain_self_chemistry': 'Captain chemistry with themselves',
                      'always_away': 'Always away team', 'score_999': '999 runs',
                      'extra_innings': 'Unlimited extra innings',
                      **{f'mercy_rule_{r}': f'Mercy rule at {r} runs' for r in MERCY_RULE_LEVELS},
                      'night_music': 'Day/night music themes', 'challenge_coins': 'Challenge mode 999 coins'}
GAME_OPTION_GROUPS = {**{f'close_play_{p}': 'close_play' for p in CLOSE_PLAY_LEVELS},    # Roster/slot_plan
                      **{f'mercy_rule_{r}': 'mercy_rule' for r in MERCY_RULE_LEVELS}}    # (test-pinned)
GAME_OPS = (STAT_EDITS, GAMEPLAY, ITEM, STADIUM, GAME_OPTION)
STAT_RESET = 'stat_reset'                     # clears one slot's stat edits (Roster/slot_plan, test-pinned)


COPY_MULTIPLE = 'Cannot copy multiple characters at once'
COPY_LABEL, PASTE_LABEL = 'Copy', 'Paste'
EXPORT_LABEL = 'Export as .sluggie'
EXPORT_PACKAGE_LABEL = 'Export to .sluggiezip'
PACKAGE = 'package'                           # the confirm dialog of a whole character package (Roster/slot_zip)
PACKAGE_EXTENSION = '.sluggiezip'             # Roster/slot_zip.EXTENSION
PACKAGE_FILTERS = [('Character packages', '*.sluggiezip'), ('All files', '*.*')]
PACKAGE_STAT_ORIGIN = 'sluggiezip'            # Roster/slot_zip.STAT_ORIGIN: a stat edit file made from a package


def copy_edit(target: int, source: int) -> dict:
    """The pending edit that makes slot ``target`` a clone of ``source`` (paste)."""
    return {'op': COPY, 'id': hex_id(target), 'source': hex_id(source)}


def context_menu(state: dict, members: list, copied: int | None, pack_pending: bool,
                 locked: bool) -> list[tuple[str, bool, str | None]]:
    """The right-click menu of a grid square or a colour-wheel swatch: ``(label, enabled, why disabled)``. A square
    with several characters gets only a disabled line; one character gets Copy and Paste (Paste needs a copied
    character other than this one), then Export as .sluggie and Export to .sluggiezip."""
    if len(members) != 1:
        return [(COPY_MULTIPLE, False, 'Open the square and right-click one of its characters.')]
    cid = members[0]
    busy = 'A command is running or a dialog is open.' if locked else None
    pack = 'A roster pack load is pending: press "Patch Game" (or Discard it) first.' if pack_pending else None
    copy_why = busy
    if copied is None:
        paste_why = 'Copy a character first (right-click it, Copy).'
    elif copied == cid:
        paste_why = 'This is the copied character itself.'
    elif copied not in characters(state):
        paste_why = 'The copied character is no longer on the grid.'
    else:
        paste_why = busy or pack
    return [(COPY_LABEL, copy_why is None, copy_why), (PASTE_LABEL, paste_why is None, paste_why),
            (EXPORT_LABEL, busy is None, busy), (EXPORT_PACKAGE_LABEL, busy is None, busy)]


def export_command(cid: int) -> tuple:
    """Export as .sluggie: the slot as the game holds it into ``2_Output_Assets/Custom <name> NN``."""
    return ('--export-slot', hex_id(cid))


def export_result(root_dir: str) -> dict | None:
    """The last slot export's result (``Roster/slot_export``): ``folder`` + ``name`` when it wrote one, ``error``
    when it aborted; None when there is none (it crashed before writing it)."""
    try:
        with open(os.path.join(root_dir, SLOT_EXPORT_REL), 'r', encoding='utf-8') as f:
            result = json.load(f)
    except (OSError, ValueError):
        return None
    return result if isinstance(result, dict) else None


def export_toast(code: int, result: dict | None) -> str | None:
    """The note shown when "Export as .sluggie" wrote its folder; None when it did not (``export_failed_dialog``)."""
    folder = (result or {}).get('folder') if code == 0 else None
    if not folder or not os.path.isdir(folder):
        return None
    return f'{os.path.basename(os.path.normpath(folder))} has been created in 2_Output_Assets'


def export_failed_dialog(state: dict | None, cid: int, result: dict | None) -> 'SlotDialog':
    """Shown when "Export as .sluggie" wrote nothing: why it aborted."""
    who = f'{name_of(state, cid) if state else hex_id(cid)} ({hex_id(cid)})'
    error = (result or {}).get('error')
    return SlotDialog('Export aborted', [
        (f'{who} was not exported; nothing was written.', ERROR),
        (f'Reason: {error}' if error else 'Reason: see the log for details.', WARN if error else TEXT)])


def export_pending_dialog(state: dict | None, cid: int, pending: PendingEdits) -> 'SlotDialog':
    """Asked before "Export as .sluggie" while the slot has pending edits: the export takes the game as it is."""
    who = f'{name_of(state, cid) if state else hex_id(cid)} ({hex_id(cid)})'
    lines = [(f'{who} has pending edits (not written to the game yet):', TEXT)]
    lines += [(f'  {line}', WARN) for line in pending.lines(cid)]
    lines.append(('The export takes the slot as the game holds it now, so they are not in it. To include them, '
                  'Cancel and run "Patch Game" first.', TEXT))
    return SlotDialog(f'Export {who} without its pending edits?', lines, True)


def package_name(state: dict | None, cid: int) -> str:
    """The file name "Export to .sluggiezip" offers: the slot's name as the grid shows it, safe for Windows."""
    name = ' '.join(re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', name_of(state, cid) if state else '').split()).rstrip(' .')
    return (name or f'Slot {hex_id(cid)}') + PACKAGE_EXTENSION


def package_export_command(cid: int, path: str) -> tuple:
    """Export to .sluggiezip: the slot as the game holds it into one character package."""
    return ('--export-sluggiezip', hex_id(cid), path)


def package_export_result(root_dir: str) -> dict | None:
    """The last package export's result (``Roster/slot_zip``): ``path`` when it wrote one, ``error`` when it
    aborted; None when there is none (it crashed before writing it)."""
    try:
        with open(os.path.join(root_dir, PACKAGE_EXPORT_REL), 'r', encoding='utf-8') as f:
            result = json.load(f)
    except (OSError, ValueError):
        return None
    return result if isinstance(result, dict) else None


def package_export_failed_dialog(state: dict | None, cid: int, result: dict | None) -> 'SlotDialog':
    """Shown when "Export to .sluggiezip" wrote nothing: why it aborted."""
    who = f'{name_of(state, cid) if state else hex_id(cid)} ({hex_id(cid)})'
    error = (result or {}).get('error')
    return SlotDialog('Export aborted', [
        (f'{who} was not exported; nothing was written.', ERROR),
        (f'Reason: {error}' if error else 'Reason: see the log for details.', WARN if error else TEXT)])


def staged_package_files(pending: 'PendingEdits') -> set[str]:
    """Every file the pending edits name (an extracted package stays while any of them points into it)."""
    return {e[key] for e in pending.edits for key in ('file', 'low', 'sluggie', 'anm')
            if isinstance(e.get(key), str)}


def package_dialog(state: dict, cid: int, path: str, plan: dict | None, code: int, output: str,
                   skipped: list[str]) -> 'SlotDialog':
    """The confirm dialog of a character package (``Roster/slot_zip.stage``), after its staging check: each staged
    aspect's lines, the aspects left out (``skipped``: not in the package, or the slot cannot take them, plus what
    the slot holds already) and the verdict. ``can_apply`` only when the planner and the build checks passed."""
    target = f'{name_of(state, cid)} ({hex_id(cid)})'
    dialog = SlotDialog(f'Load {os.path.basename(path)} into {target}?')
    lines = dialog.lines
    lines.append((f'Package: {os.path.basename(path)} -> {target}', TEXT))
    error = error_message(output)
    if plan is None or plan['refused']:
        dialog.title = f'{target}: refused'
        if plan is None:
            error = error.removeprefix('refused, nothing written: ')
            lines.append((f'Refused: {error or f"the planner failed (exit code {code})"}', ERROR))
        else:
            lines += _refused_lines(state, plan, cid)
        lines.append(('Nothing was staged: one refused aspect refuses the whole package.', TEXT))
        return dialog
    sizes = build_sizes(output)
    new = [s for s in plan['edits'] if not s.get('checked')]
    for section in new:
        title = _edit_title(section['edit']).removeprefix('Pending: ')
        lines.append((title[:1].upper() + title[1:], OK))
        lines += [('    ' + text, kind) for text, kind in _section_lines(state, section, sizes)]
    same = [s for s in plan['skipped'] if not s.get('checked')]
    for section in same:
        lines += [(f'Unchanged: {note}', TEXT) for note in section.get('notes') or []]
    lines += [(f'Skipped: {text}', WARN) for text in skipped]
    prefix = f'{hex_id(cid)}: '
    lines += [(f'- {note.removeprefix(prefix)}', TEXT) for note in plan['notes'] if note.startswith(prefix)]
    if code != 0:
        lines.append((f'Build check failed: {error or f"exit code {code}"}. Nothing was staged.', ERROR))
        return dialog
    if not new:
        dialog.title = f'{target}: nothing to change'
        lines.append(('The slot holds everything in the package already.', TEXT))
        return dialog
    lines.append((f'Checks passed: slot rules, and every model built and validated. Nothing written yet: Stage adds '
                  f'the {len(new)} edit{"s" if len(new) != 1 else ""} to the pending list, "Patch Game" writes them.',
                  OK))
    dialog.can_apply = True
    return dialog


def stat_reset_edit(cid: int) -> dict:
    """The pending edit that clears slot ``cid``'s stat edits."""
    return {'op': STAT_RESET, 'id': hex_id(cid)}


def stat_edits_of(state: dict | None, cid: int) -> dict | None:
    """The stat edits the game holds for ``cid`` (``{'fields': [...], 'chemistry': n}``, from the state read), or
    None."""
    return (((state or {}).get('stat_edits') or {}).get('characters') or {}).get(hex_id(cid))


def stat_edits_text(edits: dict) -> str:
    """``3 fields (stamina, slap size, charge pitch speed), 2 chemistry values`` (``StatEditor/carry``'s wording)."""
    parts = []
    names = [f.split('.', 1)[-1] for f in edits.get('fields') or []]
    if names:
        shown = ', '.join(names[:4]) + (f', +{len(names) - 4} more' if len(names) > 4 else '')
        parts.append(f'{len(names)} field{"s" if len(names) != 1 else ""} ({shown})')
    if edits.get('chemistry'):
        n = edits['chemistry']
        parts.append(f'{n} chemistry value{"s" if n != 1 else ""}')
    return ', '.join(parts)


STATS_REPLACED = 'this step replaces main.dol, so they are lost'
STATS_RESET = 'a reset to vanilla clears them'


def stat_edit_risk(state: dict | None, lost: bool, reason: str = STATS_REPLACED) -> str | None:
    """What an export / roster injection does to the game's stat edits (``config_risks``). ``lost``: every stat
    edit goes (``reason``: the untangle export replaces main.dol, a reset to vanilla clears them); else the roster
    run carries them over by character ID and only new IDs the new roster lacks lose theirs (None when no new ID
    has any)."""
    summary = (state or {}).get('stat_edits') or {}
    edited = [int(k, 16) for k in summary.get('characters') or {}]
    globals_ = summary.get('globals') or 0
    if lost:
        if not edited and not globals_:
            return None
        what = [f'{len(edited)} character{"s" if len(edited) != 1 else ""}'] if edited else []
        what += [f'{globals_} global value{"s" if globals_ != 1 else ""}'] if globals_ else []
        return f'Stat edits on {" and ".join(what)}: {reason}.'
    new = sorted(c for c in edited if c >= NEW_START)
    if not new:
        return None
    return (f'Stat edits on {len(new)} new ID{"s" if len(new) != 1 else ""}: a roster without '
            f'{"that ID" if len(new) == 1 else "those IDs"} drops them (the other stat edits are carried over by '
            'character ID).')


def edit_who(state: dict | None, cid: int, action: str | None = None) -> str:
    """Who a pending or planned edit is for: ``Mario (0x00)``, or ``Stat edits`` / ``Gameplay settings`` /
    ``Item models`` / ``Stadium models`` for a game-wide edit (``action``: its op)."""
    if cid == GAME_WIDE:
        if action in MODEL_FILE_TILES:
            return MODEL_FILE_TILES[action].title
        return {GAMEPLAY: 'Gameplay settings', GAME_OPTION: 'Game options'}.get(action, 'Stat edits')
    return f'{name_of(state, cid) if state else hex_id(cid)} ({hex_id(cid)})'
# state key -> (slot file, label); the file numbers are Roster/gear.py's (test-pinned)
EQUIP_TILES = (('bat', 2, 'Bat'), ('glove_l', 3, 'Left glove'), ('glove_r', 4, 'Right glove'),
               ('extra', 5, 'Extra bat'))
EQUIP_LABELS = {file: label for _role, file, label in EQUIP_TILES}
EXTRA_FILE = 5
EXTRA_HINT = ('File 5 is an extra slot that only Peach (a second bat) and Wario fill; what the game does with it on '
              'other characters is not confirmed yet.')
ORIGINAL, MODIFIED, EMPTY, PENDING_STATE = 'Original', 'Modified', 'Empty', 'Pending'


def equipment_state(state: dict, cid: int, role: str) -> str:
    """``Empty`` (a placeholder), ``Modified`` (not the vanilla block of its directory) or ``Original`` (also when
    the read cannot tell: the input files are unreadable)."""
    entry = ((characters(state).get(cid) or {}).get('equipment') or {}).get(role) or {}
    if entry.get('placeholder'):
        return EMPTY
    return MODIFIED if entry.get('vanilla') is False else ORIGINAL


def equipment_tiles(state: dict, cid: int, pending: 'PendingEdits | None' = None) -> list[dict]:
    """One dict per equipment file for the slot level: ``file``, ``label``, ``state`` (Original / Modified / Empty,
    or Pending with a pending edit), ``text`` (the line under the label), ``tip`` (tooltip lines) and ``can_reset``
    (the tile's Reset does something: modified, or a pending edit to drop). ``[]`` without equipment in the read."""
    char = characters(state).get(cid) or {}
    equipment = char.get('equipment')
    if not equipment:
        return []
    tiles = []
    for role, file, label in EQUIP_TILES:
        entry = equipment.get(role)
        if entry is None:
            continue
        now = equipment_state(state, cid, role)
        edit = pending.equip_edit(cid, file) if pending is not None else None
        tip = [f'{label} = file {file} of the slot\'s model directory']
        if entry.get('placeholder'):
            tip.append('empty: the shared placeholder block most characters ship in this file')
        else:
            tip.append(f'{_mb(entry["length"])} [{entry["sha1"][:8]}]')
        if entry.get('shared_with') and char.get('id', cid) < NEW_START:
            tip.append(f'also loaded by {entry["shared_with"]} other slot{"s" if entry["shared_with"] != 1 else ""}; '
                       'a replacement only changes this slot')
        if file == EXTRA_FILE:
            tip.append(EXTRA_HINT)
        if edit is not None:
            text = _edit_title(edit).removeprefix('Pending: ')
            tiles.append({'file': file, 'label': label, 'state': PENDING_STATE, 'text': text, 'tip': tip,
                          'can_reset': True})
            continue
        tiles.append({'file': file, 'label': label, 'state': now, 'text': now, 'tip': tip,
                      'can_reset': now == MODIFIED})
    return tiles


# --------------------------------------------------------------------------
# Slot actions: the confirm dialogs
# --------------------------------------------------------------------------

TEXT, WARN, ERROR, OK = 'text', 'warn', 'error', 'ok'
_BUILD_LINE = re.compile(r'Slot build check passed \| Model: (?P<name>.+?) \| Size: [\d.]+ MB \((?P<bytes>\d+) bytes\)')


def preview_command(edits_path: str) -> tuple:
    """The dry run behind a confirm dialog: plan the edits file + run its build checks, nothing written."""
    return ('--apply-slots', edits_path, '--dry-run')


def apply_command(edits_path: str, accept_gecko: bool = False, accept_memory: bool = False) -> tuple:
    return ('--apply-slots', edits_path) + accept_gecko_args(accept_gecko) + accept_memory_args(accept_memory)


def accept_gecko_args(accept: bool) -> tuple:
    """After a summary that showed the Gecko warning was confirmed: the command must not ask again."""
    return ('--accept-gecko-break',) if accept else ()


def accept_memory_args(accept: bool) -> tuple:
    """After a summary that showed the player memory warning was confirmed ("Patch anyway"): no second question."""
    return ('--accept-memory-over',) if accept else ()


PATCH_LABEL = 'Patch Game'
PATCH_DEPLOY_LABEL = 'Patch Game & copy files'
DEPLOY_LABEL = 'Deploy patched files to game directory'
DEPLOY_SETTING = 'deploy_to_game_dir'           # the GUI settings key (``gui_settings``) of that checkbox
DEPLOY_SCRIPT_REL = os.path.join('3_Output_Dat', 'CopyFilesToGameDir.bat')
GAME_DIR_FILE_REL = os.path.join('3_Output_Dat', 'sluggiespath')    # the script's game directory, one line
# where CopyFilesToGameDir.bat copies dt_na.dat and sound_NA/ (DATA/files) and main.dol / fst.bin (DATA/sys)
GAME_SUBDIRS = (os.path.join('DATA', 'files'), os.path.join('DATA', 'sys'))
_GAME_DIR_ENCODING = 'oem' if os.name == 'nt' else 'utf-8'           # what cmd's set /p and echo use


class ShellStep(tuple):
    """A command chain step run as it is, not as start.py arguments (``SluggiesGui.run_chain``)."""


def patch_label(count: int, deploy: bool) -> str:
    return f'{PATCH_DEPLOY_LABEL if deploy else PATCH_LABEL} ({count})'


def deploy_command(script: str, comspec: str | None = None) -> ShellStep:
    """Run CopyFilesToGameDir.bat (through cmd, so it also runs from a path with spaces)."""
    return ShellStep((comspec or os.environ.get('ComSpec', 'cmd.exe'), '/c', script))


def read_game_dir(path: str) -> str:
    """The game directory stored in the sluggiespath file (its first line), '' when there is none."""
    try:
        with open(path, encoding=_GAME_DIR_ENCODING, errors='replace') as f:
            return clean_game_dir(f.readline())
    except OSError:
        return ''


def clean_game_dir(text: str) -> str:
    """A typed or pasted directory: no surrounding blanks or quotes, no trailing separator."""
    text = (text or '').strip().strip('"').strip()
    return text.rstrip('\\/') if len(text) > 3 else text     # keep a drive root such as C:\


def game_dir_problem(game_dir: str) -> str | None:
    """Why ``game_dir`` is no unpacked Mario Super Sluggers main folder to copy into, or None when it is one."""
    if not game_dir:
        return 'no game directory given'
    if not os.path.isdir(game_dir):
        return f'the game directory does not exist: {game_dir}'
    missing = [sub for sub in GAME_SUBDIRS if not os.path.isdir(os.path.join(game_dir, sub))]
    if missing:
        return f'{game_dir} has no {" or ".join(missing)} folder (not the unpacked game\'s main folder?)'
    return None


def write_game_dir(path: str, game_dir: str) -> None:
    """Store the game directory for CopyFilesToGameDir.bat (it reads the first line with ``set /p``)."""
    with open(path, 'w', encoding=_GAME_DIR_ENCODING) as f:
        f.write(game_dir + '\n')


def write_edits(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def build_sizes(output: str) -> dict[str, int]:
    """Block size per model file name, from the build check's log lines."""
    return {m.group('name'): int(m.group('bytes')) for m in _BUILD_LINE.finditer(output)}


def load_plan(path: str) -> dict | None:
    """The dry run's batch plan; None when there is none (the planner failed before planning)."""
    try:
        with open(path, encoding='utf-8') as f:
            plan = json.load(f)
    except (OSError, ValueError):
        return None
    return plan if plan.get('action') == 'batch' else None


def _mb(size: int) -> str:
    return f'{size / (1024 * 1024):.2f} MB'


@dataclass
class SlotDialog:
    title: str
    lines: list = field(default_factory=list)   # [(text, TEXT/WARN/ERROR/OK)]
    can_apply: bool = False
    offer_stat_reset: bool = False              # the slot keeps stat edits: offer "Stage + clear stat edits"
    gecko_break: bool = False                   # applying breaks Gecko code compatibility: red Confirm, never skipped
    memory_over: bool = False                   # a character will not fit its player memory: red "... anyway", never skipped
    menu: bool = False                          # a choice, not a confirmation (the item tile's list): never skipped

    @property
    def danger(self) -> bool:
        return self.gecko_break or self.memory_over


SKIP_CONFIRM_LABEL = 'Skip confirmation popups'
SKIP_CONFIRM_SETTING = 'skip_confirmation_popups'   # the GUI settings key (``gui_settings``) of that checkbox


def dialog_skippable(dialog: SlotDialog) -> bool:
    """With "Skip confirmation popups" on: a dialog that can apply counts as OK'd; one that only informs (Close)
    is skipped too unless it reports a warning or an error (a refusal must still be seen). A Gecko code
    compatibility warning and a player memory warning are never skipped, nor a menu (``SlotDialog.menu``)."""
    if dialog.danger or dialog.menu:
        return False
    return dialog.can_apply or not any(kind in (WARN, ERROR) for _text, kind in dialog.lines)


def with_gecko_warning(dialog: SlotDialog, breaks: bool) -> SlotDialog:
    """A summary whose dry run recorded a Gecko code compatibility break (``gecko_compat.read_check``): the
    warning in red above the verdict, and the flag (red Confirm, never skipped). Dialogs that cannot apply stay
    as they are."""
    if breaks and dialog.can_apply:
        dialog.lines.insert(len(dialog.lines) - 1, (gecko_compat.WARNING, ERROR))
        dialog.gecko_break = True
    return dialog


def with_memory_warning(dialog: SlotDialog, check: dict, only: set | None = None) -> SlotDialog:
    """A dialog whose dry run recorded characters over the player memory (``memory_guard.read_details``): their
    lines in red above the verdict, and the flag (red "... anyway", never skipped). ``only``: the slots (``"0xNN"``)
    to report (a staging check: the new edits' slots, not the pending ones it re-checked). Dialogs that cannot apply
    stay as they are."""
    problems = [p for p in (check or {}).get('problems') or [] if only is None or p.get('id') in only]
    if problems and dialog.can_apply:
        at = len(dialog.lines) - 1 if dialog.lines else 0
        dialog.lines[at:at] = [(memory_guard.WARNING, ERROR)] + [(p['text'], ERROR) for p in problems]
        dialog.memory_over = True
    return dialog


def new_targets(plan: dict | None) -> set:
    """The slots (``"0xNN"``) of a staging check's new edits (the sections not marked ``checked``)."""
    return {s['target'] for s in (plan or {}).get('edits') or [] if not s.get('checked') and s.get('target')}


def anyway_label(ok_label: str) -> str:
    """The red OK of a dialog with a player memory warning: ``Patch anyway``, ``Stage anyway``."""
    word = ok_label.split(' (')[0].split(' & ')[0]
    return 'Patch anyway' if word.startswith('Patch') else f'{word} anyway'


def _partner_lines(files: dict, sizes: dict, joined: bool = False) -> list:
    def label(path, role):
        name = os.path.basename(path)
        size = sizes.get(name)
        return f'{name} ({role}' + (f', {_mb(size)})' if size else ')')

    high, low = files.get('high'), files.get('low')
    if high and low:
        how = 'the Low pick joins the pending High pick' if joined else 'found side by side'
        return [(f'Models: {label(high, "High")} + {label(low, "Low")}, {how}', TEXT)]
    if high:
        size = sizes.get(os.path.basename(high))
        twice = f': {_mb(2 * size)} together' if size else ''
        return [(f'Model: {label(high, "High")}, no Low partner beside it', WARN),
                (f'The High model is used as the Low model too, so the slot loads it twice on the field{twice}',
                 WARN)]
    return [(f'Model: {label(low, "Low only")}, no High partner beside it', WARN),
            ("It goes under the slot's current High model, which must be its own partner (checked)", TEXT)]


def _section_lines(state: dict, section: dict, sizes: dict, warnings: bool = True) -> list:
    """A planned edit's lines (the confirm dialog's content): source and models with their sizes, notes, warnings."""
    lines = []
    cid = int(section['target'], 16)
    patch = section['action'] == 'patch'
    equip = section['action'] == 'equip'
    animation = section['action'] == ANIM
    if section['action'] == COPY:
        source = int(section['source'], 16)
        lines.append((f'Clone of {known_name(state, source)} ({hex_id(source)}) -> {name_of(state, cid)} '
                      f'({hex_id(cid)}), as the game holds it now (its pending edits are not copied)', TEXT))
        effects = section.get('effects') or {}
        for key, label in (('model', 'Models'), ('name', 'Name'), ('stats', 'Stats'), ('voice', 'Voice'),
                           (CHAR_VOICE, 'Own voice'), ('portrait_note', 'Portraits'), (STAT_EDITS, 'Stat values')):
            if effects.get(key):
                lines.append((f'{label}: {effects[key]}', TEXT))
        for _file, text in sorted((effects.get('equipment') or {}).items()):
            lines.append((f'Equipment: {text}', TEXT))
        lines += [(f'- {note}', TEXT) for note in section['notes'][1:]]
        if warnings:
            lines += [(f'Warning: {warning}', WARN) for warning in section['warnings']]
        return lines
    if section['action'] == SLGMODEL:
        info = section.get('slgmodel') or {}
        base = int(info['base'], 16) if info.get('base') else None
        base_text = f'{known_name(state, base)} ({hex_id(base)})' if base is not None else info.get('base_name', '?')
        lines.append((f'Character file: {os.path.basename(info.get("file") or "?")}: {info.get("name", "?")}, built '
                      f'on {base_text} -> {name_of(state, cid)} ({hex_id(cid)})', TEXT))
        sizes_ = info.get('sizes') or {}
        if info.get('high_only'):
            lines.append((f'Models: High {_mb(sizes_.get("high") or 0)}, no Low model in the file: the High model is '
                          f'used as the Low model too ({_mb(2 * (sizes_.get("high") or 0))} together)', WARN))
        else:
            lines.append((f'Models: High {_mb(sizes_.get("high") or 0)} + Low {_mb(sizes_.get("low") or 0)}, '
                          'finished blocks written as they are', TEXT))
        if info.get('glb_edited'):
            lines.append(('Models: its model.glb was edited after export; those edits are not included', WARN))
        by = ', '.join(f'{label} {info[key]}' for key, label in (('author', 'by'), ('from', 'from')) if info.get(key))
        if by:
            lines.append((f'File: {by}', TEXT))
        if info.get('notes'):
            lines.append((f'Notes: {info["notes"]}', TEXT))
        effects = section.get('effects') or {}
        for key, label in (('model', 'Models'), ('name', 'Name'), ('stats', 'Stats'), ('voice', 'Voice'),
                           (CHAR_VOICE, 'Own voice'), ('portrait_note', 'Portraits')):
            if effects.get(key):
                lines.append((f'{label}: {effects[key]}', TEXT))
        lines += [(f'- {note}', TEXT) for note in section['notes'][1:]]
        if warnings:
            lines += [(f'Warning: {warning}', WARN) for warning in section['warnings']]
        return lines
    if patch:
        source = int(section['source'], 16)
        lines.append((f'Source: {known_name(state, source)} ({hex_id(source)}) -> {name_of(state, cid)} '
                      f'({hex_id(cid)})', TEXT))
        lines += _partner_lines(section.get('files') or {}, sizes, joined='low' in (section.get('edit') or {}))
    if equip:
        gear = section.get('gear') or {}
        name = os.path.basename(gear.get('path') or '?')
        size = sizes.get(name)
        bundled = ' (bundled with the model)' if gear.get('origin') == 'bundled' else ''
        lines.append((f'{gear.get("label", "Equipment")}: {name}{bundled}' + (f', {_mb(size)}' if size else ''), TEXT))
    if animation:
        bank = section.get('anim') or {}
        stock = f' (stock {_mb(bank["stock_size"])})' if bank.get('stock_size') else ''
        lines.append((f'{bank.get("label", "Animation bank")}: {os.path.basename(bank.get("path") or "?")}, '
                      f'{bank.get("count", "?")} animations, all replaced at once, {_mb(bank.get("size") or 0)}{stock}',
                      TEXT))
    # the first note is the line above
    notes = section['notes'][1:] if patch or equip or animation else section['notes']
    lines += [(f'- {note}', TEXT) for note in notes]
    if warnings:
        lines += [(f'Warning: {warning}', WARN) for warning in section['warnings']]
    return lines


def _refused_lines(state: dict, plan: dict, cid: int | None = None) -> list:
    lines = []
    for refused in plan['refused']:
        rid = int(refused['target'], 16)
        action = (refused.get('edit') or {}).get('op')
        where = ('' if rid == cid else f'{edit_who(state, rid, action)}: ' if rid == GAME_WIDE
                 else f'{known_name(state, rid)} ({hex_id(rid)}): ')
        lines.append((f'Refused: {where}{refused["error"]}', ERROR))
    return lines


VALUE_KINDS = ('rename', 'stats', 'voice', 'icon', CHAR_VOICE, SPECIES_CLIP)   # edits that change one value, no model


def _find_section(plan: dict, cid: int, kind: str, view: str | None, gear_file: int | None,
                  moment: str | None = None, anm: str | None = None) -> dict | None:
    """The planned section of the edit just staged (the plan also lists the pending edits' sections)."""
    for section in plan['edits']:
        if int(section['target'], 16) != cid or section['action'] != kind:
            continue
        edit = section.get('edit') or {}
        if kind == 'icon' and view is not None and edit.get('view') != view:
            continue
        if kind in EQUIP_OPS and gear_file is not None and edit.get('file') != gear_file:
            continue
        if kind == SPECIES_CLIP and moment is not None and edit.get('moment') != moment:
            continue
        if kind == ANIM and anm is not None and not _same_path(edit.get('anm'), anm):
            continue
        return section
    return None


def _same_path(a: str | None, b: str | None) -> bool:
    return bool(a and b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def slot_dialog(state: dict, cid: int, patch: bool, plan: dict | None, code: int, output: str,
                pending: PendingEdits | None = None, rename: bool = False, kind: str | None = None,
                view: str | None = None, gear_file: int | None = None, moment: str | None = None,
                anm: str | list[str] | None = None) -> SlotDialog:
    """The confirm dialog of a staged edit, after its staging check (the pending edits plus this one, exit
    ``code``, log ``output``): what the edit does, its warnings and the verdict. ``can_apply`` (Stage stages it)
    only when the planner and the build check passed. ``kind``: 'patch', 'clear', 'rename', 'stats', 'voice',
    'char_voice', 'species_clip' (``moment``: its voice moment) or 'icon' (``view``: its portrait view), 'equip' or
    'equip_clear' (``gear_file``: the slot file), 'anim' (``anm``: the picked .anm, or a list of them staged
    together) (default from ``patch`` / ``rename``; a voice edit's ``cid`` is its square's head)."""
    kind = kind or ('rename' if rename else 'patch' if patch else 'clear')
    rename = kind in VALUE_KINDS
    anms = [anm] if anm is None or isinstance(anm, str) else list(anm)
    target = f'{name_of(state, cid)} ({hex_id(cid)})'
    gear_label = EQUIP_LABELS.get(gear_file, 'equipment').lower()
    dialog = SlotDialog({'rename': f'Rename {target}?', 'patch': f'Put a model into {target}?',
                         'equip': f'Put a {gear_label} into {target}?',
                         'equip_clear': f'Reset the {gear_label} of {target}?',
                         ANIM: (f'Replace {len(anms)} animation banks of {target}?' if len(anms) > 1
                                else f'Replace an animation bank of {target}?'),
                         'stats': f'Other stats for {target}?',
                         'icon': f'New {view_label(view)} portrait for {target}?',
                         'voice': f'Another voice for the square of {target}?',
                         CHAR_VOICE: f'An own voice for {target}?',
                         SPECIES_CLIP: f'Replace clip {moment} of the voice of {target}?',
                         COPY: f'Paste onto {target}?',
                         SLGMODEL: f'Import a character file into {target}?'}.get(kind, f'Clear {target}?'))
    error = error_message(output)
    lines = dialog.lines
    if plan is None or plan['refused']:
        dialog.title = f'{target}: refused'
        if plan is None:
            error = error.removeprefix('refused, nothing written: ')        # the planner's wording; said below
            lines.append((f'Refused: {error or f"the planner failed (exit code {code})"}', ERROR))
        else:
            lines += _refused_lines(state, plan, cid)
            if any(int(r['target'], 16) != cid for r in plan['refused']):
                lines.append(('A pending edit no longer fits the game files: discard it on its slot.', TEXT))
        lines.append(('Nothing was staged.', TEXT))
        return dialog
    sections = []                             # one per picked .anm (a bank picked twice: the later one stays)
    for path in anms:
        found = _find_section(plan, cid, kind, view, gear_file, moment, path)
        if found is not None and not any(found is s for s in sections):
            sections.append(found)
    section = sections[0] if sections else None
    if section is None:                     # a clear of a slot at its baseline, or a rename that changes nothing
        skipped = next((s for s in plan['skipped'] if int(s['target'], 16) == cid and s['action'] == kind
                        and (kind != SPECIES_CLIP or (s.get('edit') or {}).get('moment') == moment)), None)
        lines += [(f'- {note}', TEXT) for note in (skipped or {}).get('notes', [])]
        earlier = None
        if pending is not None:
            earlier = (pending.icon_edit(cid, view) if kind == 'icon'
                       else pending.species_clip_edit(cid, moment) if kind == SPECIES_CLIP
                       else pending.value_edit(cid, kind) if rename
                       else pending.equip_edit(cid, gear_file) if kind in EQUIP_OPS
                       else None if kind == ANIM else pending.model_edit(cid))
        if earlier is not None:
            lines.append((f'Stage drops the slot\'s pending {earlier["op"]}, so the slot stays as it is.', OK))
            dialog.can_apply = True
        else:
            dialog.title = {'rename': f'{target}: nothing to rename', 'clear': f'{target}: nothing to clear',
                            'equip_clear': f'{target}: nothing to reset'}.get(kind, f'{target}: nothing to change')
        return dialog
    sizes = build_sizes(output)
    for each in sections:
        lines += _section_lines(state, each, sizes)
    prefix = f'{hex_id(cid)}: '
    lines += [(f'- {note.removeprefix(prefix)}', TEXT) for note in plan['notes'] if note.startswith(prefix)]
    if not any(each['rebuild'] for each in sections):
        lines.append(('- no roster rebuild needed', TEXT))
    if code != 0:
        lines.append((f'Build check failed: {error or f"exit code {code}"}. Nothing was staged.', ERROR))
        return dialog
    checks = ('slot rules, and every copied block validated' if kind == COPY
              else 'slot rules, and the file\'s blocks validated against the slot\'s skeleton' if kind == SLGMODEL
              else 'slot rules, and the bank checked against the slot\'s skeleton' if kind == ANIM
              else 'slot rules, and the model built and validated' if kind == 'equip'
              else 'slot rules, and every model built and validated' if patch and not rename else 'slot rules')
    if section.get('stat_edits_kept'):
        if any(e['op'] == STAT_RESET and int(e['id'], 16) == cid for e in plan.get('merged') or []):
            lines.append(('Its stat edits are cleared too (a pending edit).', TEXT))
        else:
            lines.append(('"Stage + clear stat edits" also clears them, so the slot plays with exactly the new '
                          'stats.', TEXT))
            dialog.offer_stat_reset = True
    lines.append((f'Checks passed: {checks}. Nothing written yet: Stage adds the edit to the pending list, '
                  '"Patch Game" writes it.', OK))
    dialog.can_apply = True
    return dialog


def summary_dialog(state: dict, plan: dict | None, code: int, output: str) -> SlotDialog:
    """Patch Game's summary after the full dry run (every pending edit planned on a fresh read, every build
    check run): per-slot lines, all warnings, the verdict. ``can_apply`` (Patch Game runs the chain) only when every
    edit passed."""
    count = len((plan or {}).get('edits') or [])
    dialog = SlotDialog(f'Patch Game: write {count} pending edit{"s" if count != 1 else ""}?')
    lines = dialog.lines
    error = error_message(output)
    if plan is None or plan['refused']:
        dialog.title = 'Patch Game: refused'
        if plan is None:
            error = error.removeprefix('refused, nothing written: ')
            lines.append((f'Refused: {error or f"the planner failed (exit code {code})"}', ERROR))
        else:
            lines += _refused_lines(state, plan)
            lines.append(('Discard or replace the refused edits on their slots, then try again.', TEXT))
        lines.append(('Nothing was written.', TEXT))
        return dialog
    sizes = build_sizes(output)
    for section in plan['edits']:
        cid = int(section['target'], 16)
        what = {'patch': 'put a model in', 'rename': 'rename'}.get(section['action'], 'clear')
        if section['action'] in EQUIP_OPS:
            gear = section.get('gear') or {}
            label = str(gear.get('label') or 'equipment').lower()
            what = (f'put a {label} in' if section['action'] == 'equip' else f'reset the {label}')
            if gear.get('origin') == 'bundled':
                what += ' (bundled with the model)'
        elif section['action'] == ANIM:
            label = str((section.get('anim') or {}).get('label') or 'animation bank').lower()
            effect = next(iter(((section.get('effects') or {}).get('animations') or {}).values()), '')
            what = f'{label} {effect}' if effect.startswith('back to') else f'replace {label}'
        elif section['action'] == 'rename':
            text = (section.get('edit') or {}).get('text')
            what = f'rename to {text!r}' if text else 'reset the name'
        elif section['action'] in ('stats', 'voice'):
            effect = ((section.get('effects') or {}).get(section['action']) or 'default').removesuffix(' (square voice)')
            what = f'stats of {effect}' if section['action'] == 'stats' else f'square voice of {effect}'
        elif section['action'] == CHAR_VOICE:
            what = f'own voice: {(section.get("effects") or {}).get(CHAR_VOICE) or "changed"}'
        elif section['action'] == SPECIES_CLIP:
            what = f'voice pack clip {(section.get("effects") or {}).get(SPECIES_CLIPS) or "changed"}'
        elif section['action'] == 'icon':
            what = (section.get('effects') or {}).get('portrait_note') or 'new portrait'
        elif section['action'] == STAT_EDITS:
            what = (section.get('effects') or {}).get(STAT_EDITS) or 'changed values'
        elif section['action'] == GAMEPLAY:
            what = (f'{gameplay_item_label((section.get("edit") or {}).get("file"))}: '
                    + ((section.get('effects') or {}).get(GAMEPLAY) or 'changed values'))
        elif section['action'] == GAME_OPTION:
            what = (section.get('effects') or {}).get(GAME_OPTION) or 'changed'
        elif section['action'] in MODEL_FILE_TILES:
            what = ('patch ' + ((section.get('effects') or {}).get(section['action'])
                                or f'the {MODEL_FILE_TILES[section["action"]].noun}'))
        elif section['action'] == STAT_RESET:
            what = 'stat edits ' + ((section.get('effects') or {}).get(STAT_EDITS) or 'cleared')
        lines.append((f'{edit_who(state, cid, section["action"])}: {what}', OK))
        lines += [('    ' + text, kind) for text, kind in _section_lines(state, section, sizes, warnings=False)]
    for section in plan['skipped']:
        cid = int(section['target'], 16)
        why = {'rename': 'nothing to rename (named that already)', 'clear': 'nothing to clear (at its baseline '
               'already)', STAT_RESET: 'no stat edits to clear'}.get(section['action'], 'nothing to change')
        lines.append((f'{edit_who(state, cid, section["action"])}: {why}', TEXT))
    lines += [(f'- {note}', TEXT) for note in plan['notes']]
    lines += [(f'Warning: {warning}', WARN) for warning in plan['warnings']]
    lines.append(('- one roster rebuild' if plan['rebuild'] else '- no roster rebuild needed', TEXT))
    if code != 0:
        lines.append((f'Build check failed: {error or f"exit code {code}"}. Nothing was written.', ERROR))
        return dialog
    if not plan['edits']:
        lines.append(('Nothing to write.', TEXT))
        return dialog
    lines.append(('Structural checks passed: slot rules, every model built and validated'
                  + (', every stat edit file fits the game' if any(s['action'] == STAT_EDITS for s in plan['edits'])
                     else '') + '. Patch Game writes them now.', OK))
    for op, tile in MODEL_FILE_TILES.items():
        if any(s['action'] == op for s in plan['edits']):
            lines.append((tile.patch_note, TEXT))
    dialog.can_apply = True
    return dialog


# --------------------------------------------------------------------------
# Stat edits (the Stat Editor tab, StatEditor/gui_tab.py)
# --------------------------------------------------------------------------

def stat_edit(path: str) -> dict:
    """The pending edit of a stat edit file the Stat Editor sent (``Roster/slot_plan``'s ``stat_edits`` op)."""
    return {'op': STAT_EDITS, 'id': hex_id(GAME_WIDE), 'file': path}


# The slot level's Set size...: one factor on the character's size scale (``sizescale``, the stat editor's size
# "Gameplay" and "Select Screens"), 1 = the size the roster gives it without stat edits. It is staged as a stat edit
# file of one character, written into the Stat Editor tab's staging folder under its received-file prefix
# (``StatEditor/editor_install.RECEIVED_PREFIX``), so the tab's pruning deletes it once no pending edit uses it.
SIZE_ORIGIN = 'set_size'                         # the file's "origin": a size edit, not the Stat Editor's
SIZE_MIN, SIZE_MAX = 0.1, 5.0
SIZE_FIELDS = (('gameplay', 'Gameplay'), ('select', 'Select Screens'))
STAT_STAGING_REL = os.path.join('_gui', 'stat')
SIZE_FILE_PREFIX = 'stat_edits_size_'


def size_of(state: dict | None, cid: int) -> dict | None:
    """``{"gameplay", "select", "base_gameplay", "base_select"}`` of ``cid`` (``Roster/state_cli.read_sizes``), or
    None when the state has no sizes for it."""
    return (((state or {}).get('sizes') or {}).get('characters') or {}).get(hex_id(cid))


def size_factor(sizes: dict | None) -> float | None:
    """The size factor the game gives a character now: its gameplay size over the roster's own (None: unknown)."""
    if not sizes or not sizes.get('base_gameplay'):
        return None
    return sizes['gameplay'] / sizes['base_gameplay']


def size_text(state: dict | None, cid: int) -> str | None:
    """The slot summary's size line, or None without sizes."""
    sizes = size_of(state, cid)
    factor = size_factor(sizes)
    if factor is None:
        return None
    what = 'normal' if abs(factor - 1) < 1e-6 else f'{factor:.3g}x its normal size'
    return f'Size: {what} (scale {sizes["gameplay"]:g} in game, {sizes["select"]:g} on the select screens)'


def parse_size(text: str) -> tuple[float | None, str | None]:
    """The factor typed into Set size... and the reason it is refused (None when it is fine)."""
    try:
        value = float(text.strip().rstrip('xX').replace(',', '.'))
    except ValueError:
        return None, 'Type a number, e.g. 1.2 (20% bigger) or 0.8 (20% smaller)'
    if not SIZE_MIN <= value <= SIZE_MAX:
        return None, f'The factor must be between {SIZE_MIN:g} and {SIZE_MAX:g}'
    return value, None


def size_doc(state: dict, cid: int, factor: float) -> dict:
    """The stat edit file (``StatEditor/apply.py``) that gives ``cid`` ``factor`` times its normal size, in game and
    on the select screens. Valid only for the main.dol the state was read from."""
    sizes = size_of(state, cid)
    values = {name: float(f'{sizes["base_" + key] * factor:.6g}') for key, name in SIZE_FIELDS}
    return {'format': 'sluggies-stat-edits', 'version': 1, 'main_dol_sha1': state['sizes']['main_dol_sha1'],
            'origin': SIZE_ORIGIN, 'size_factor': factor, 'characters': {hex_id(cid): {'size': values}}}


def write_size_file(root_dir: str, doc: dict, now: float | None = None) -> str:
    """Write a size edit file into the Stat Editor tab's staging folder; returns its path."""
    folder = os.path.join(root_dir, STAT_STAGING_REL)
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(time.time() if now is None else now))
    path, n = os.path.join(folder, f'{SIZE_FILE_PREFIX}{stamp}.json'), 2
    while os.path.exists(path):
        path, n = os.path.join(folder, f'{SIZE_FILE_PREFIX}{stamp}-{n}.json'), n + 1
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=1)
    return path


_SIZE_FILES: dict[tuple[str, float], tuple[int, float] | None] = {}


def size_file(path: str | None) -> tuple[int, float] | None:
    """``(ID, factor)`` of a size edit file (``size_doc``), None for any other stat edit file. Cached per path and
    modification time, like ``stat_file_ids``."""
    try:
        key = (os.path.normcase(os.path.abspath(path)), os.path.getmtime(path))
    except (OSError, TypeError, ValueError):
        return None
    if key not in _SIZE_FILES:
        found = None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                doc = json.load(f)
            if isinstance(doc, dict) and doc.get('origin') == SIZE_ORIGIN:
                ids = list(doc.get('characters') or {})
                if len(ids) == 1 and isinstance(doc.get('size_factor'), (int, float)):
                    found = (int(ids[0], 16), float(doc['size_factor']))
        except (OSError, ValueError, AttributeError, TypeError):
            found = None
        _SIZE_FILES[key] = found
    return _SIZE_FILES[key]


def pending_size(pending: 'PendingEdits', cid: int) -> float | None:
    """The factor of ``cid``'s last pending size edit, or None."""
    found = None
    for path in staged_stat_files(pending):
        size = size_file(path)
        if size is not None and size[0] == cid:
            found = size[1]
    return found


def staged_stat_files(pending: 'PendingEdits') -> list[str]:
    """The pending stat edit files, in staging order."""
    return [e['file'] for e in pending.edits if e['op'] == STAT_EDITS and e.get('file')]


_STAT_IDS: dict[tuple[str, float], frozenset[int]] = {}


_PACKAGE_FILES: dict[tuple[str, float], str | None] = {}


def package_stat_file(path: str | None) -> str | None:
    """The package a stat edit file was made from (``Roster/slot_zip.stat_doc``), None for any other stat edit
    file. Cached per path and modification time, like ``size_file``."""
    try:
        key = (os.path.normcase(os.path.abspath(path)), os.path.getmtime(path))
    except (OSError, TypeError, ValueError):
        return None
    if key not in _PACKAGE_FILES:
        found = None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                doc = json.load(f)
            if isinstance(doc, dict) and doc.get('origin') == PACKAGE_STAT_ORIGIN:
                found = str(doc.get('package') or 'a package')
        except (OSError, ValueError, AttributeError, TypeError):
            found = None
        _PACKAGE_FILES[key] = found
    return _PACKAGE_FILES[key]


def stat_file_ids(path: str | None) -> frozenset[int]:
    """The character IDs a stat edit file names (its ``characters`` keys; chemistry partners not counted), empty
    when unreadable. Cached per path and modification time (the grid asks on every redraw)."""
    try:
        key = (os.path.normcase(os.path.abspath(path)), os.path.getmtime(path))
    except (OSError, TypeError, ValueError):
        return frozenset()
    if key not in _STAT_IDS:
        ids = set()
        try:
            with open(path, 'r', encoding='utf-8') as f:
                doc = json.load(f)
            for text in (doc.get('characters') or {}) if isinstance(doc, dict) else ():
                try:                           # StatEditor/apply._id's rules: "0xNN", 0x00-0xFF
                    cid = int(text, 16) if text.lower().startswith('0x') else -1
                except (AttributeError, ValueError):
                    cid = -1
                if 0 <= cid <= 0xFF:
                    ids.add(cid)
        except (OSError, ValueError, AttributeError):
            pass
        _STAT_IDS[key] = frozenset(ids)
    return _STAT_IDS[key]


def _same_file(a: str | None, b: str) -> bool:
    return bool(a) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def stat_edits_dialog(state: dict | None, plan: dict | None, code: int, output: str, path: str) -> SlotDialog:
    """The confirm dialog of a stat edit file after its staging check (the pending edits plus this one): what it
    changes per character and table, its warnings and the verdict. ``can_apply`` (Stage stages it) only when the
    whole check passed and the file changes something. A size edit file (``size_doc``) gets the size wording."""
    size = size_file(path)
    noun = 'Size' if size else 'Stat edits'
    dialog = SlotDialog(f'Give {name_of(state or {}, size[0])} {size[1]:g}x its normal size?' if size
                        else 'Stage the Stat Editor\'s values?')
    lines = dialog.lines
    error = error_message(output)
    if plan is None or plan['refused']:
        dialog.title = f'{noun}: refused'
        if plan is None:
            error = error.removeprefix('refused, nothing written: ')
            lines.append((f'Refused: {error or f"the planner failed (exit code {code})"}', ERROR))
        else:
            lines += _refused_lines(state or {}, plan, GAME_WIDE)
            if any(not _same_file((r.get('edit') or {}).get('file'), path) for r in plan['refused']):
                lines.append(('A pending edit no longer fits the game files: discard it first.', TEXT))
        lines.append(('Nothing was staged.', TEXT))
        return dialog
    section = next((s for s in plan['edits'] if s['action'] == STAT_EDITS
                    and _same_file((s.get('edit') or {}).get('file'), path)), None)
    if section is None:
        dialog.title = f'{noun}: nothing to change'
        skipped = next((s for s in plan['skipped'] if s['action'] == STAT_EDITS
                        and _same_file((s.get('edit') or {}).get('file'), path)), None)
        lines += [(f'- {note}', TEXT) for note in (skipped or {}).get('notes', [])]
        lines.append(('The game has that size already. Nothing was staged.' if size else
                      'The Stat Editor sent no value the game does not hold already. Nothing was staged.', TEXT))
        return dialog
    lines.append((f'Changes: {(section.get("effects") or {}).get(STAT_EDITS) or "changed values"}', OK))
    lines += [(f'- {note}', TEXT) for note in section['notes']]
    lines += [(f'Warning: {warning}', WARN) for warning in section['warnings']]
    if code != 0:
        lines.append((f'Check failed: {error or f"exit code {code}"}. Nothing was staged.', ERROR))
        return dialog
    lines.append(('Checks passed: made from this main.dol, known characters and fields, every value fits. Nothing '
                  'written yet: Stage adds the values to the pending list, "Patch Game" (Character grid tab) writes '
                  'them.', OK))
    dialog.can_apply = True
    return dialog


# --------------------------------------------------------------------------
# Roster packs
# --------------------------------------------------------------------------

# the fingerprint fields (Roster/pack.py FIELDS), as the dialogs and markers name them
PACK_FIELDS = {'high': 'High model', 'low': 'Low model', 'bat': 'bat', 'glove_l': 'left glove',
               'glove_r': 'right glove', 'extra': 'extra bat', 'model': 'model directory', 'front': 'front portrait',
               'side': 'side portrait', 'name': 'name', 'stats': 'stats', 'voice': 'square voice',
               'char_voice': 'own voice', 'clips': 'own voice clips', 'species_clips': 'voice pack clips',
               'square': 'square'}
EQUIP_FIELDS = ('bat', 'glove_l', 'glove_r', 'extra', 'clips', 'species_clips')   # absent from older packs' fingerprints
DIFF_TEXT = {'game': 'only in the game (leaves the grid)', 'pack': 'only in the pack (comes onto the grid)'}


def save_command(path: str) -> tuple:
    return ('--save-roster', path)


def load_command(path: str, dry_run: bool = False, accept_gecko: bool = False, accept_memory: bool = False) -> tuple:
    return (('--load-roster', path) + (('--dry-run',) if dry_run else ()) + accept_gecko_args(accept_gecko)
            + accept_memory_args(accept_memory))


def with_extension(path: str) -> str:
    return path if path.lower().endswith(PACK_EXTENSION) else path + PACK_EXTENSION


def pack_fingerprints(path: str) -> dict | None:
    """The per-slot fingerprints a roster pack holds (``fingerprints.json``), or None when it cannot be read."""
    import zipfile
    try:
        with zipfile.ZipFile(path) as zf:
            data = json.loads(zf.read('fingerprints.json').decode('utf-8'))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
    return data if isinstance(data, dict) else None


def load_pack_plan(path: str, action: str = 'load_pack') -> dict | None:
    """A planner's plan file (``action``: 'load_pack', or 'switch_roster' for ``switch_plan``), or None."""
    try:
        with open(path, encoding='utf-8') as f:
            plan = json.load(f)
    except (OSError, ValueError):
        return None
    return plan if isinstance(plan, dict) and plan.get('action') == action else None


# --------------------------------------------------------------------------
# Roster switch (Roster Size tab: inject a preset, keep the slots' customisations)
# --------------------------------------------------------------------------

# Roster/migrate.py's field keys (test-pinned to migrate.FIELD_LABELS)
SWITCH_FIELDS = {'model': 'models', 'equipment': 'equipment', 'name': 'name', 'icon': 'portraits',
                 'stats': 'stats source', 'voice': 'square voice', 'char_voice': 'own voice',
                 'species_clips': 'voice pack clips', 'stat_edits': 'stat edits'}


def switch_command(path: str, dry_run: bool = False, fresh: bool = False, accept_gecko: bool = False) -> tuple:
    """``start.py --roster --config``: the switch chain (``fresh``: no slot customisations kept)."""
    return (('--roster', '--config', path) + (('--fresh',) if fresh else ()) + (('--dry-run',) if dry_run else ())
            + accept_gecko_args(accept_gecko))


def switch_plan(path: str) -> dict | None:
    return load_pack_plan(path, 'switch_roster')


def switch_dialog(plan: dict | None, code: int, output: str, path: str) -> SlotDialog:
    """The dialog of "Inject roster", after ``--roster --config FILE --dry-run``: which slots keep their
    customisations, which are reset or dropped, the verdict. ``can_apply`` only when the plan and the in-memory
    build passed."""
    name = os.path.basename(path)
    dialog = SlotDialog(f'Inject the roster {name}?')
    lines = dialog.lines
    if plan is None or code != 0:
        dialog.title = f'{name}: refused'
        error = error_message(output).removeprefix('refused, nothing written: ')
        lines.append((f'Refused: {error or f"the check failed (exit code {code})"}', ERROR))
        lines.append(('Nothing was written.', TEXT))
        return dialog
    names = plan.get('names') or {}

    def rows(key, title, kind, empty_too=True):
        items = [r for r in plan.get(key) or [] if empty_too or r['fields']]
        if not items:
            return
        lines.append((title, TEXT))
        for r in items:
            who = f'{names[r["id"]]} ({r["id"]})' if names.get(r['id']) else r['id']
            what = ', '.join(SWITCH_FIELDS.get(f, f) for f in r['fields'])
            lines.append((f'  {who}' + (f': {what}' if what else ''), kind))
    rows('carried', 'Kept (on both grids; where they sit comes from the preset):', OK)
    rows('reset', 'Reset (leaving the grid, back to their baseline):', WARN)
    rows('dropped', 'Dropped (new IDs leaving the roster) with their customisations:', WARN, empty_too=False)
    lines += [(f'- {note}', TEXT) for note in plan.get('notes') or []]
    lines += [(f'Warning: {w}', WARN) for w in plan.get('warnings') or []]
    lines.append(('- Pending grid edits are dropped. Stat edits travel with their characters.', TEXT))
    lines.append(('Checks passed: the merged roster builds. Inject writes it into 3_Output_Dat.', OK))
    dialog.can_apply = True
    return dialog


@dataclass
class Reference:
    """The roster pack last saved or loaded in this session (GUI memory only): slots that differ from it get the
    "changed since" marker."""
    label: str                                  # 'saved X.sluggiesroster' / 'loaded X.sluggiesroster'
    fingerprints: dict

    def changed(self, state: dict, cid: int) -> list[str]:
        """What differs between the slot now and the pack (field names); ``[]`` when nothing does."""
        now = (characters(state).get(cid) or {}).get('fingerprint')
        then = self.fingerprints.get(hex_id(cid))
        if now is None:
            return []
        if then is None:
            return ['not in the pack']
        return [label for f, label in PACK_FIELDS.items()
                if now.get(f) != then.get(f) and not (f in EQUIP_FIELDS and f not in then)]

    def square_changed(self, state: dict, index: int) -> bool:
        return any(self.changed(state, m) for m in state['squares'][index]['members'])

    def count(self, state: dict) -> int:
        return sum(bool(self.changed(state, c['id'])) for c in state['characters'])

    def line(self, state: dict | None) -> str:
        if state is None:
            return f'Reference: the roster pack you {self.label}.'
        n = self.count(state)
        return (f'Reference: the roster pack you {self.label}; '
                + (f'{n} slot{"s" if n != 1 else ""} changed since (blue border).' if n else 'no slot changed since.'))


def _pack_name(state: dict | None, plan: dict, cid: int) -> str:
    if state is not None and cid in characters(state):
        return f'{name_of(state, cid)} ({hex_id(cid)})'
    name = ((plan.get('pack_fingerprints') or {}).get(hex_id(cid)) or {}).get('name') or {}
    return f'{name.get("en") or "?"} ({hex_id(cid)})'


def load_dialog(state: dict | None, plan: dict | None, code: int, output: str, path: str,
                differing_only: bool = True, writing: bool = False) -> SlotDialog:
    """The dialog of "Load roster...", after ``--load-roster FILE --dry-run``: the per-slot diff (only the
    differing slots with ``differing_only``), what the load does, the verdict. ``can_apply`` (Stage adds the load
    to the pending list; with ``writing``, Patch Game's summary, Patch Game writes it) only when the pack passed
    every check and there is something to load."""
    name = os.path.basename(path)
    dialog = SlotDialog(f'Patch Game: load the roster pack {name}?' if writing else f'Load the roster pack {name}?')
    lines = dialog.lines
    error = error_message(output)
    if plan is None or plan.get('refused') or code != 0:
        dialog.title = f'{name}: refused'
        if plan is None or not plan.get('refused'):
            error = error.removeprefix('refused, nothing written: ')
            lines.append((f'Refused: {error or f"the planner failed (exit code {code})"}', ERROR))
        else:
            for refused in plan['refused']:
                lines.append((f'Refused: {_pack_name(state, plan, int(refused["id"], 16))}: {refused["error"]}',
                              ERROR))
        lines.append(('Nothing was written.', TEXT))
        return dialog
    diff = plan.get('diff') or []
    counts = {k: sum(d['status'] == k for d in diff) for k in ('same', 'differs', 'game', 'pack')}
    meta = plan.get('pack_meta') or {}
    blocks = sum(len(r) for r in (meta.get('blocks') or {}).values())
    lines.append((f'Pack: {name} ({meta.get("slots", "?")} slots, {blocks} model blocks)', TEXT))
    lines.append((f'{counts["same"]} slots are the same, {counts["differs"]} differ, {counts["game"]} only in the '
                  f'game, {counts["pack"]} only in the pack.', TEXT))
    for d in diff:
        if d['status'] == 'same' and differing_only:
            continue
        if d['status'] == 'differs':
            what = 'differs: ' + ', '.join(PACK_FIELDS.get(f, f) for f in d['fields'])
        else:
            what = DIFF_TEXT.get(d['status'], 'the same')
        lines.append((f'  {_pack_name(state, plan, int(d["id"], 16))}: {what}',
                      TEXT if d['status'] == 'same' else WARN))
    lines += [(f'- {note}', TEXT) for note in plan.get('notes') or []]
    lines += [(f'Warning: {w}', WARN) for w in plan.get('warnings') or []]
    if not plan.get('commands'):
        dialog.title = f'{name}: nothing to load'
        lines.append(('The game holds this roster already.', OK))
        return dialog
    what = ['one roster rebuild' if plan.get('rebuild') else 'no roster rebuild']
    if plan.get('clears'):
        what.append(f'{len(plan["clears"])} stock slot(s) back to vanilla models first')
    if plan.get('writes'):
        what.append(f'{len(plan["writes"])} slot(s) get the pack\'s model blocks')
    lines.append(('- ' + '; '.join(what), TEXT))
    if writing:
        lines.append(('Checks passed: every model block of the pack. Patch Game now replaces the whole roster of '
                      '3_Output_Dat with the pack.', OK))
    else:
        lines.append(('Checks passed: every model block of the pack. Nothing written yet: Stage adds the load to '
                      'the pending list, "Patch Game" writes it (it replaces the whole roster).', OK))
    dialog.can_apply = True
    return dialog


def save_pending_dialog(state: dict | None, pending: PendingEdits) -> SlotDialog:
    """Asked before "Save roster..." while edits are pending: the pack saves the game as it is."""
    count = len(pending)
    lines = [(f'{count} pending edit{"s" if count != 1 else ""} (not written to the game yet):', TEXT)]
    for cid, text in pending.titles():
        who = '' if cid is None else f'{name_of(state, int(cid, 16)) if state else cid} ({cid}): '
        lines.append((f'  {who}{text}', WARN))
    lines.append(('A roster pack saves the game as it is, so they are not in it. To include them, Cancel and run '
                  '"Patch Game" first.', TEXT))
    return SlotDialog('Save the roster without the pending edits?', lines, True)


# --------------------------------------------------------------------------
# Gameplay settings (the Gameplay tab, gui_gameplay.py)
# --------------------------------------------------------------------------

GAMEPLAY_RESET = 'reset'                              # Gameplay/apply.RESET_ITEM (test-pinned)
GAMEPLAY_STAGING_REL = os.path.join('_gui', 'gameplay')        # the staged edit files and load copies
GAMEPLAY_DIR_REL = os.path.join('_gui', 'Gameplay_Settings')   # where Save / Load settings... start
GAMEPLAY_EXTENSION = '.sluggiesgameplay'                       # Gameplay/apply.EXTENSION (test-pinned)
GAMEPLAY_DIR_SETTING = 'gameplay_last_dir'
GAMEPLAY_REPORT_REL = os.path.join('_gui', 'gameplay', 'report.json')


def gameplay_edit(item: str) -> dict:
    """The pending edit of a gameplay item: a file (the tab's staged values, a loaded settings file) or ``reset``."""
    return {'op': GAMEPLAY, 'id': hex_id(GAME_WIDE), 'file': item}


def staged_gameplay_items(pending: 'PendingEdits') -> list[str]:
    """The pending gameplay items (files, ``reset``), in staging order."""
    return [e['file'] for e in pending.edits if e['op'] == GAMEPLAY and e.get('file')]


def gameplay_item_label(item: str | None) -> str:
    """``back to 1_Input`` for a reset, ``loaded from NAME`` for a loaded file, ``changed values`` for the tab's own
    staged values (``edit_*.json`` in the staging folder)."""
    if not item:
        return 'changed values'
    if item.strip().lower() == GAMEPLAY_RESET:
        return 'every value back to 1_Input'
    name = os.path.basename(item)
    if name.startswith('load_'):
        return f'loaded from {name.split("_", 2)[-1]}'
    return 'changed values'


def load_gameplay_report(path: str) -> dict | None:
    """The ``--apply-gameplay --report`` JSON (``Gameplay/apply.report``, or ``{"error"}``); None when unreadable."""
    try:
        with open(path, encoding='utf-8') as f:
            report = json.load(f)
    except (OSError, ValueError):
        return None
    return report if isinstance(report, dict) else None


def gameplay_save_path(path: str) -> str:
    return path if path.lower().endswith(GAMEPLAY_EXTENSION) else path + GAMEPLAY_EXTENSION


def gameplay_save_command(path: str) -> tuple:
    return ('--save-gameplay', path)


def gameplay_report_command(path: str, report: str) -> tuple:
    return ('--apply-gameplay', path, '--dry-run', '--report', report)


def gameplay_risk(state: dict | None) -> str | None:
    """What an export that replaces main.dol does to the game's gameplay settings (``config_risks``), or None when
    the game holds the 1_Input values (a roster run, a reset to vanilla included, carries them)."""
    block = (state or {}).get('gameplay') or {}
    current, vanilla = block.get('current'), block.get('vanilla')
    if not current or not vanilla:
        return None
    n = sum(1 for table, rows in current.items() for row, cols in rows.items() for col, value in cols.items()
            if ((vanilla.get(table) or {}).get(row) or {}).get(col) != value)
    if not n:
        return None
    return (f'Gameplay settings ({n} value{"s" if n != 1 else ""} differ from 1_Input): {STATS_REPLACED}; save them '
            'first on the Gameplay tab (Save settings...).')


def gameplay_dialog(state: dict | None, plan: dict | None, code: int, output: str, item: str) -> SlotDialog:
    """The confirm dialog of a gameplay item after its staging check (the pending edits plus this one): what the
    pending gameplay edits change together, the notes and warnings, the verdict. ``can_apply`` only when the whole
    check passed and the item is planned."""
    dialog = SlotDialog('Stage the gameplay settings?')
    lines = dialog.lines
    error = error_message(output)
    if plan is None or plan['refused']:
        dialog.title = 'Gameplay settings: refused'
        if plan is None:
            error = error.removeprefix('refused, nothing written: ')
            lines.append((f'Refused: {error or f"the planner failed (exit code {code})"}', ERROR))
        else:
            lines += _refused_lines(state or {}, plan, GAME_WIDE)
        lines.append(('Nothing was staged.', TEXT))
        return dialog
    section = next((s for s in reversed(plan['edits']) if s['action'] == GAMEPLAY
                    and _same_item((s.get('edit') or {}).get('file'), item)), None)
    if section is None:
        dialog.title = 'Gameplay settings: nothing to change'
        lines.append(('The game holds these values already. Nothing was staged.', TEXT))
        return dialog
    lines.append((f'Changes: {(section.get("effects") or {}).get(GAMEPLAY) or "changed values"}', OK))
    lines += [(f'- {note}', TEXT) for note in section['notes']]
    lines += [(f'Warning: {warning}', WARN) for warning in section['warnings']]
    lines += [(f'- {note}', TEXT) for note in plan.get('notes') or [] if note.startswith('gameplay:')]
    if code != 0:
        lines.append((f'Check failed: {error or f"exit code {code}"}. Nothing was staged.', ERROR))
        return dialog
    lines.append(('Checks passed: known fields, every value fits, the cross-field checks. Nothing written yet: '
                  '"Patch Game" writes the pending edits.', OK))
    dialog.can_apply = True
    return dialog


def game_option_dialog(state: dict | None, plan: dict | None, code: int, output: str) -> SlotDialog:
    """The game option staging check's dialog. ``can_apply`` when the whole check passed: the merged list is
    then staged as it is (an edit back to what the game has drops the earlier pending one of that option)."""
    dialog = SlotDialog('Stage the game options?')
    lines = dialog.lines
    error = error_message(output)
    if plan is None or plan['refused']:
        dialog.title = 'Game options: refused'
        if plan is None:
            error = error.removeprefix('refused, nothing written: ')
            lines.append((f'Refused: {error or f"the planner failed (exit code {code})"}', ERROR))
        else:
            lines += _refused_lines(state or {}, plan, GAME_WIDE)
        lines.append(('Nothing was staged.', TEXT))
        return dialog
    if code != 0:
        lines.append((f'Check failed: {error or f"exit code {code}"}. Nothing was staged.', ERROR))
        return dialog
    dialog.can_apply = True
    return dialog


def _same_item(a: str | None, b: str) -> bool:
    if not a:
        return False
    if b.strip().lower() == GAMEPLAY_RESET or a.strip().lower() == GAMEPLAY_RESET:
        return a.strip().lower() == b.strip().lower()
    return _same_file(a, b)


def gameplay_load_dialog(report: dict | None, name: str, labels: dict, pending: int, only_diff: bool = True,
                         legacy_title: str = 'AI Gameplay Tool file') -> SlotDialog:
    """Load settings...: what the file ``name`` sets against the game (``report``: ``--apply-gameplay --dry-run
    --report``), per inner tab (``labels``: ``{(table, row, column): (tab title, cell label)}``); ``only_diff``: only
    the values that differ from the game. ``pending``: pending gameplay edits the load replaces. ``can_apply`` (Stage)
    when the file was accepted."""
    dialog = SlotDialog(f'Load gameplay settings: {name}')
    lines = dialog.lines
    if report is None or report.get('error'):
        dialog.title = f'Gameplay settings: {name} refused'
        lines.append((f'Refused: {(report or {}).get("error") or "the check failed (see the log)"}', ERROR))
        lines.append(('Nothing was staged.', TEXT))
        return dialog
    if report.get('legacy'):
        dialog.title = f'Load gameplay settings: {name} ({legacy_title})'
        lines.append((f'An {legacy_title}: only the blocks its Gecko codes wrote are taken, as the bytes '
                      'they wrote.', TEXT))
    changes = report.get('changes') or []
    lines.append((f'{len(changes)} value{"s" if len(changes) != 1 else ""} differ from the game'
                  + (f', {report.get("same", 0)} the same' if report.get('same') else '') + '.',
                  OK if changes else TEXT))
    rows = report.get('changes') if only_diff else report.get('values')
    current_tab = None
    for v in rows or []:
        tab, cell = labels.get((v['table'], v['row'], v['column']), ('?', f'{v["table"]} / {v["row"]} / {v["column"]}'))
        if tab != current_tab:
            lines.append((f'{tab}:', TEXT))
            current_tab = tab
        same = v['before'] == v['after']
        lines.append((f'    {cell}: {v["before"]}' + ('' if same else f' -> {v["after"]}'), TEXT if same else OK))
    lines += [(f'Note: {note}', TEXT) for note in report.get('notes') or []]
    lines += [(f'Warning: {warning}', WARN) for warning in report.get('warnings') or []]
    if pending:
        lines.append((f'Stage replaces the {pending} pending gameplay edit{"s" if pending != 1 else ""}.', WARN))
    if not changes:
        lines.append(('The game holds these values already.' + (' Stage still drops the pending gameplay edits.'
                                                                  if pending else ''), TEXT))
        dialog.can_apply = bool(pending)
        return dialog
    lines.append(('Nothing written yet: Stage adds the values to the pending list, "Patch Game" writes them.', TEXT))
    dialog.can_apply = True
    return dialog


# --------------------------------------------------------------------------
# Item and stadium models (the grid's item and stadium tiles)
# --------------------------------------------------------------------------

ITEM_TIP = 'Patch item models'
ITEM_LABEL = 'Items'
ITEM_FILTERS = [('Sluggie files', '*.sluggie')]
ITEM_PATCH_NOTE = ('Item models are patched as "Patch .sluggie models" of the command line menu does (in place, or '
                   'into hammerspace when the file asks for it), after a dry run of that patch as their build check.')
STADIUM_TIP = 'Patch stadiums'
STADIUM_LABEL = 'Stadiums'
STADIUM_PATCH_NOTE = ITEM_PATCH_NOTE.replace('Item models', 'Stadium models')


@dataclass(frozen=True)
class ModelFileTile:
    """The text of a tile that stages model ``.sluggie`` files game-wide (``item_edit`` and the rest)."""
    label: str
    tip: str
    noun: str                                   # 'item model': pending lines, dialog titles
    hint: str                                   # the empty tile's tooltip
    checked: str                                # what the staging check made sure of
    patch_note: str

    @property
    def title(self) -> str:
        return self.noun[0].upper() + self.noun[1:] + 's'

    def plural(self, n: int) -> str:
        return self.noun + ('s' if n != 1 else '')


MODEL_FILE_TILES = {
    ITEM: ModelFileTile(ITEM_LABEL, ITEM_TIP, 'item model',
                        'Click to pick item .sluggie files (models outside the character and stadium directories: '
                        'items, map objects, props). "Patch Game" patches them as the command line menu does.',
                        'each file is an exported model outside the character and stadium directories, and its '
                        'patch built and passed its checks (a dry run)',
                        ITEM_PATCH_NOTE),
    STADIUM: ModelFileTile(STADIUM_LABEL, STADIUM_TIP, 'stadium model',
                           'Click to pick stadium .sluggie files (models of the stadium directories). "Patch Game" '
                           'patches them as the command line menu does.',
                           'each file is an exported model of a stadium directory, and its patch built and passed '
                           'its checks (a dry run)', STADIUM_PATCH_NOTE),
}


def item_edit(path: str, op: str = ITEM) -> dict:
    """The pending edit of an item (``op`` ``STADIUM``: stadium) model ``.sluggie`` (``Roster/slot_plan``'s op)."""
    return {'op': op, 'id': hex_id(GAME_WIDE), 'file': path}


def staged_item_files(pending: 'PendingEdits', op: str = ITEM) -> list[str]:
    """The pending item (stadium) model files, in staging order."""
    return [e['file'] for e in pending.edits if e['op'] == op and e.get('file')]


def item_tooltip(pending: 'PendingEdits', op: str = ITEM) -> list[str]:
    """The tile's tooltip below its ``tip``: the pending item (stadium) models, or how to stage one."""
    tile = MODEL_FILE_TILES[op]
    files = staged_item_files(pending, op)
    if not files:
        return [tile.hint]
    return [f'{len(files)} pending {tile.plural(len(files))}:'] + [
        f'  {os.path.basename(f)}' for f in files]


def items_dialog(pending: 'PendingEdits', op: str = ITEM) -> 'SlotDialog':
    """The tile with pending item (stadium) models: their list; OK picks more files (Add), the extra button drops
    them."""
    tile = MODEL_FILE_TILES[op]
    files = staged_item_files(pending, op)
    dialog = SlotDialog(tile.title, [(f'{len(files)} pending {tile.plural(len(files))} '
                                      '(nothing written yet):', TEXT)])
    dialog.lines += [(f'  {os.path.basename(f)}', WARN) for f in files]
    dialog.lines.append(('Add picks more .sluggie files; a file of a model that is pending already replaces it.',
                         TEXT))
    dialog.can_apply = dialog.menu = True
    return dialog


def item_dialog(state: dict | None, plan: dict | None, code: int, output: str, paths: list[str],
                op: str = ITEM) -> 'SlotDialog':
    """The confirm dialog of picked item (stadium) models after their staging check (the pending edits plus these):
    each file, the notes and warnings, the verdict. ``can_apply`` only when the check passed and every file is
    planned."""
    tile = MODEL_FILE_TILES[op]
    count = len(paths)
    dialog = SlotDialog(f'Stage {count} {tile.plural(count)}?')
    lines = dialog.lines
    error = error_message(output)
    if plan is None or plan['refused']:
        dialog.title = f'{tile.title}: refused'
        if plan is None:
            error = error.removeprefix('refused, nothing written: ')
            lines.append((f'Refused: {error or f"the planner failed (exit code {code})"}', ERROR))
        else:
            lines += _refused_lines(state or {}, plan, GAME_WIDE)
        lines.append(('Nothing was staged.', TEXT))
        return dialog
    sections = [s for s in plan['edits'] if s['action'] == op
                and any(_same_file((s.get('edit') or {}).get('file'), p) for p in paths)]
    for section in sections:
        lines.append((f'{tile.title[:-1]}: {(section.get("effects") or {}).get(op) or "?"}', OK))
        lines += [(f'- {note}', TEXT) for note in section['notes']]
        lines += [(f'Warning: {warning}', WARN) for warning in section['warnings']]
    lines += [(f'- {note}', TEXT) for note in plan.get('notes') or [] if note.startswith(f'{op}:')]
    if code != 0:
        lines.append((f'Check failed: {error or f"exit code {code}"}. Nothing was staged.', ERROR))
        return dialog
    if len(sections) != count:
        lines.append(('Not every picked file was planned. Nothing was staged.', ERROR))
        return dialog
    lines.append((f'Checks passed: {tile.checked}. Nothing written yet: Stage adds them to the pending list, '
                  '"Patch Game" writes them.', OK))
    dialog.can_apply = True
    return dialog


def banana_icon(size: tuple[int, int]):
    """The item tile's picture: a banana on a transparent background, fitted into ``size`` (an RGBA PIL image)."""
    import math
    from PIL import Image, ImageDraw
    w, h = size
    k = 4                                                    # drawn 4x larger, then scaled down (smooth edges)

    def band(t, offset, scale):
        """A point across the banana at ``t`` (0-1 along it): ``offset`` -1 (inner edge) to 1 (outer edge)."""
        angle = math.radians(190 + 150 * t)                  # a curve hanging down, tips up
        half = 0.5 * (0.10 + 0.90 * math.sin(math.pi * t) ** 0.7) * scale
        radius = 1.0 + offset * half
        return math.cos(angle) * radius, -math.sin(angle) * radius

    steps = [i / 40 for i in range(41)]
    outline = [band(t, 1, 0.62) for t in steps] + [band(t, -1, 0.62) for t in reversed(steps)]
    xs, ys = [p[0] for p in outline], [p[1] for p in outline]
    fit = min(w * k * 0.84 / (max(xs) - min(xs)), h * k * 0.84 / (max(ys) - min(ys) + 0.25))
    ox = w * k / 2 - fit * (max(xs) + min(xs)) / 2
    oy = h * k / 2 - fit * (max(ys) + min(ys)) / 2 + fit * 0.08

    def px(points):
        return [(ox + x * fit, oy + y * fit) for x, y in points]

    image = Image.new('RGBA', (w * k, h * k), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    tip = px([band(1, 0, 0.62)])[0]                          # the stem, at the right-hand tip
    draw.line((tip, (tip[0] + 0.06 * fit, tip[1] - 0.22 * fit)), fill=(110, 80, 20, 255), width=int(0.12 * fit))
    draw.polygon(px(outline), fill=(250, 210, 55, 255), outline=(120, 85, 20, 255), width=2 * k)
    middle = steps[6:35]
    draw.polygon(px([band(t, 0.15, 0.62) for t in middle] + [band(t, -0.55, 0.62) for t in reversed(middle)]),
                 fill=(255, 238, 130, 255))                  # highlight along the inner curve
    end = px([band(0, 0, 0.62)])[0]                          # the dark blossom end
    draw.ellipse((end[0] - 0.05 * fit, end[1] - 0.05 * fit, end[0] + 0.05 * fit, end[1] + 0.05 * fit),
                 fill=(90, 60, 15, 255))
    return image.resize((w, h), Image.Resampling.LANCZOS)


def flag_icon(size: tuple[int, int]):
    """The stadium tile's picture: a flag pole with a triangular flag, on a transparent background (an RGBA PIL
    image of ``size``)."""
    from PIL import Image, ImageDraw
    w, h = size
    k = 4                                                    # drawn 4x larger, then scaled down (smooth edges)
    s = min(w, h) * k                                        # a square drawing area, centred
    ox, oy = (w * k - s) / 2, (h * k - s) / 2

    def px(x, y):
        return ox + x * s, oy + y * s

    image = Image.new('RGBA', (w * k, h * k), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    outline = (60, 40, 30, 255)
    draw.polygon([px(0.37, 0.16), px(0.86, 0.29), px(0.37, 0.42)],      # the flag, right of the pole's top
                 fill=(220, 50, 45, 255), outline=outline, width=2 * k)
    draw.rectangle((*px(0.30, 0.12), *px(0.37, 0.92)), fill=(190, 190, 200, 255), outline=outline, width=k)
    draw.ellipse((*px(0.275, 0.06), *px(0.395, 0.18)), fill=(245, 200, 60, 255), outline=outline, width=k)
    draw.rectangle((*px(0.20, 0.88), *px(0.47, 0.94)), fill=(120, 120, 130, 255), outline=outline, width=k)
    return image.resize((w, h), Image.Resampling.LANCZOS)
