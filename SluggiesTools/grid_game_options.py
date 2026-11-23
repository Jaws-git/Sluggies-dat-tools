"""Game option toggles of the CPU vs CPU, Extras and Options tabs (no Dear PyGui): for each option its key,
button label, status line, the staged ``game_option`` edit or the ``--game-options`` command. Covers
CPU vs CPU and CPU management, close plays, the CPU fixes, the extras, the mercy rule, the autopicker, the
strike zone, the cheats and the player memory levels.

The tabs themselves are drawn by ``gui_options_tabs``. Reached through ``gui_grid``.
"""

from typing import TYPE_CHECKING

if __package__:
    from .grid_common import (
        CLOSE_PLAY_GECKO, CLOSE_PLAY_LEVELS, CPU_VS_CPU_OPTIONS, GAME_OPTION, GAME_OPTION_GROUPS, GAME_WIDE,
        MERCY_RULE_LEVELS, MERCY_RULE_STOCK, hex_id,
    )
else:
    from grid_common import (
        CLOSE_PLAY_GECKO, CLOSE_PLAY_LEVELS, CPU_VS_CPU_OPTIONS, GAME_OPTION, GAME_OPTION_GROUPS, GAME_WIDE,
        MERCY_RULE_LEVELS, MERCY_RULE_STOCK, hex_id,
    )

if TYPE_CHECKING:                      # annotations only: grid_pending imports this module
    from grid_pending import PendingEdits


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
