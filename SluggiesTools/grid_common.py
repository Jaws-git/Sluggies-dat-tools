"""Character grid, shared base (no Dear PyGui): the ``_gui`` file locations, the edit ``op`` names and their
groups, the menu labels, the line kinds of a dialog (``TEXT`` / ``WARN`` / ``ERROR`` / ``OK``) and
``SlotDialog``, the plain result every dialog builder returns.

Every other ``grid_*`` module imports from here and none is imported here. ``gui_grid`` re-exports all of
it; the GUI and the tests reach these names through ``gui_grid``.
"""

import os
import re
from dataclasses import dataclass, field


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
                           '--write-voice-pack', '--game-options', '--custom-cpu', '--custom-cpu-off'})
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


STAGED_ICONS_REL = os.path.join('_gui', 'slot', 'staged_icons')


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


# --------------------------------------------------------------------------
# Slot actions: the confirm dialogs
# --------------------------------------------------------------------------

TEXT, WARN, ERROR, OK = 'text', 'warn', 'error', 'ok'
_BUILD_LINE = re.compile(r'Slot build check passed \| Model: (?P<name>.+?) \| Size: [\d.]+ MB \((?P<bytes>\d+) bytes\)')


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
