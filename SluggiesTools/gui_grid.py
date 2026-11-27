"""Character grid tab: everything that needs no running Dear PyGui (testable on its own).

This module is the import name only. The code lives in ``grid_common`` (file locations, edit ``op`` names,
``SlotDialog``), ``grid_images`` (Pillow pictures), ``grid_game_options`` (option toggles), ``grid_state``
(loader, names, portraits, ``GridNav``), ``grid_commands`` (``start.py`` commands and their result files),
``grid_pending`` (``PendingEdits``, tiles, staged files) and ``grid_dialogs`` (the confirm dialogs), which
import each other in that order only. Every name of theirs is imported here (``__all__``); a test that
replaces a function patches it on the module that calls it, not here.

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

try:
    import gecko_compat
except ImportError:                    # imported as SluggiesTools.gui_grid (tests)
    from SluggiesTools import gecko_compat

if __package__:
    from .grid_common import (
        ANIM, ANIM_FILTERS, CHEMISTRY_PREFIX, CHEMISTRY_STAGING_REL, CLOSE_PLAY_GECKO, CLOSE_PLAY_LEVELS, COPY, COPY_LABEL, COPY_MULTIPLE,
        CPU_VS_CPU_OPTIONS, EDITS_REL, EQUIP_OPS, ERROR, EXPORT_LABEL, EXPORT_PACKAGE_LABEL, GAMEPLAY,
        GAME_OPS, GAME_OPTION, GAME_OPTION_GROUPS, GAME_OPTION_TITLES, GAME_WIDE, ITEM, LUIGI,
        MERCY_RULE_LEVELS, MERCY_RULE_STOCK, MODEL_OPS, OK, PACKAGE, PACKAGE_EXPORT_REL, PACKAGE_EXTENSION,
        PACKAGE_FILTERS, PACKAGE_STAT_ORIGIN, PACK_DIR_REL, PACK_EXTENSION, PACK_PLAN_REL, PASTE_LABEL,
        SLGMODEL, SLGMODEL_EXTENSION, SLGMODEL_FILTERS, SLOT_EXPORT_REL, SLOT_PLAN_REL, STADIUM,
        STAGED_CLIPS_REL, STAGED_ICONS_REL, STATE_REL, STAT_EDITS, STAT_RESET, SWITCH_PLAN_REL, SlotDialog,
        TEXT, UNNAMED, WARN, WRITING_FLAGS, _BUILD_LINE, _mb, chain_writes, hex_id,
    )
    from .grid_images import (
        IconPreview, SOURCE_BOX, _icon_import, banana_icon, flag_icon, source_thumbnail,
    )
    from .grid_game_options import (
        AUTOPICKER_DISABLE, AUTOPICKER_ENABLE, AUTOPICKER_KEY, CHEATS, CHEATS_NOTE, CLOSE_PLAY_DISABLE,
        CLOSE_PLAY_STAGE, CPU_FIXES, CPU_MANAGEMENT_DISABLE, CPU_MANAGEMENT_ENABLE, CPU_VS_CPU_DISABLE,
        CPU_VS_CPU_ENABLE, EXTRAS, EXTRAS_99_0, EXTRAS_MISC, EXTRA_COMPANIONS, EXTRA_TOGGLES,
        MERCY_RULE_RESET, MERCY_RULE_STAGE, PLAYER_HEAP_LEVELS, PLAYER_HEAP_SAFE_KB, PLAYER_HEAP_STOCK,
        PLAYER_HEAP_STOCK_LABEL, STRIKE_ZONE_DISABLE, STRIKE_ZONE_ENABLE, STRIKE_ZONE_KEY, _cheat_label,
        _close_play_of, _cpu_fix_label, _effective_options, _game_options, _kb_text, _mercy_rule_of,
        _pending_suffix, autopicker_button, autopicker_command, autopicker_status, cheat_button,
        cheat_command, cheat_status, close_play_buttons, close_play_edit, close_play_key, close_play_level,
        close_play_snap, close_play_status, cpu_fix_button, cpu_fix_edits, cpu_fix_status, cpu_gecko_warning,
        cpu_management_button, cpu_management_edit, cpu_management_status, cpu_vs_cpu_button,
        cpu_vs_cpu_edits, cpu_vs_cpu_status, extras_gecko_warning, game_option_edit, mercy_rule_buttons,
        mercy_rule_clamp, mercy_rule_edit, mercy_rule_key, mercy_rule_level, mercy_rule_status,
        player_heap_choice, player_heap_choices, player_heap_command, player_heap_is_big, player_heap_keys,
        player_heap_level, player_heap_status, staged_game_options, strike_zone_button, strike_zone_command,
        strike_zone_status,
    )
    from .grid_state import (
        CHAR_VOICE, CHAR_VOICE_DEFAULT, FIT_CHOICES, FRONT, GRID, GridNav, HAS_ALT_TEXT, IMAGE_EXTENSIONS,
        IMAGE_FILTERS, IMAGE_PATTERNS, MII_START, NEW_START, PLATE_FONT_SIZE, PLATE_FONT_WEIGHT,
        PLATE_TEXT_WIDTH, PLAYER_END, SET_ALT_TEXT, SIDE, SIDE_ALT, SLOT, SPECIES_CLIP, SPECIES_CLIPS,
        SQUARE, SQUARE_PACK, STATE_SOURCES, STATE_VERSION, StateLoader, VIEW_LABELS, _CHAR_INDEX,
        _STATE_KEYS, _labelled, _pack_label, animations_text, can_replace_portrait, char_voice_choices,
        char_voice_current, char_voice_edit, char_voice_text, characters, choice_index, error_message,
        has_alt_icon, icon_file, icon_note, icon_ref, is_fallback, known_name, name_of, name_problem,
        portrait_tip, rename_prefill, slot_count, species_clip_edit, square_label, square_pack_choices,
        square_pack_edits, square_tooltip, state_problem, state_signature, stats_choices, stats_text,
        stock_luigi_note, view_label, voice_choices, voice_reach, voice_text,
    )
    from .grid_commands import (
        DEPLOY_ARGS, DEPLOY_LABEL, DEPLOY_SETTING, DIFF_TEXT, EQUIP_FIELDS, GAMEPLAY_DIR_REL,
        GAMEPLAY_DIR_SETTING, GAMEPLAY_EXTENSION, GAMEPLAY_REPORT_REL, GAMEPLAY_RESET, GAMEPLAY_STAGING_REL,
        GAME_DIR_FILE_REL, GAME_SUBDIRS, PACK_FIELDS, PATCH_DEPLOY_LABEL, PATCH_LABEL, SWITCH_FIELDS,
        _GAME_DIR_ENCODING, _write_whole, accept_gecko_args, accept_memory_args, apply_command, build_sizes,
        clean_game_dir, export_command, export_result, export_toast, game_dir_problem, gameplay_edit,
        gameplay_item_label, gameplay_report_command, gameplay_save_command, gameplay_save_path,
        load_command, load_gameplay_report, load_pack_plan, load_plan, pack_fingerprints,
        package_export_command, package_export_result, package_name, patch_label, preview_command,
        read_game_dir, save_command, staged_gameplay_items, switch_command, switch_plan, with_extension,
        write_edits, write_game_dir,
    )
    from .grid_pending import (
        EMPTY, EQUIP_LABELS, EQUIP_TILES, EXTRA_FILE, EXTRA_HINT, ITEM_FILTERS, ITEM_LABEL, ITEM_PATCH_NOTE,
        ITEM_TIP, MODEL_FILE_TILES, MODIFIED, ModelFileTile, ORIGINAL, PENDING_STATE, PendingEdits,
        SIZE_FIELDS, SIZE_FILE_PREFIX, SIZE_MAX, SIZE_MIN, SIZE_ORIGIN, STADIUM_LABEL, STADIUM_PATCH_NOTE,
        STADIUM_TIP, STATS_REPLACED, STATS_RESET, STAT_STAGING_REL, _PACKAGE_FILES, _SIZE_FILES, _STAT_IDS,
        _edit_title, _same_file, context_menu, copy_edit, edit_who, equipment_state, equipment_tiles,
        icon_label, item_edit, item_tooltip, package_stat_file, parse_size, pending_size, prune_staged_clips,
        prune_staged_icons, size_doc, size_factor, size_file, size_of, size_text, slot_details,
        staged_clip_files, staged_icon_files, staged_item_files, staged_stat_files, stat_edit,
        chemistry_edit, is_chemistry_item, pending_pastes, staged_chemistry_files, staged_stat_items,
        stat_edit_risk, stat_edits_of, stat_edits_text, stat_file_ids, stat_reset_edit, write_size_file,
    )
    from .grid_dialogs import (
        Reference, SKIP_CONFIRM_LABEL, SKIP_CONFIRM_SETTING, VALUE_KINDS, _find_section, _pack_name,
        _partner_lines, _refused_lines, _same_item, _same_path, _section_lines, anyway_label, chemistry_dialog,
        dialog_skippable, export_failed_dialog, export_pending_dialog, game_option_dialog, gameplay_dialog,
        gameplay_load_dialog, gameplay_risk, item_dialog, items_dialog, load_dialog, new_targets,
        package_dialog, package_export_failed_dialog, save_pending_dialog, slot_dialog, staged_package_files,
        stat_edits_dialog, summary_dialog, switch_dialog, with_gecko_warning, with_memory_warning,
    )
else:
    from grid_common import (
        ANIM, ANIM_FILTERS, CHEMISTRY_PREFIX, CHEMISTRY_STAGING_REL, CLOSE_PLAY_GECKO, CLOSE_PLAY_LEVELS, COPY, COPY_LABEL, COPY_MULTIPLE,
        CPU_VS_CPU_OPTIONS, EDITS_REL, EQUIP_OPS, ERROR, EXPORT_LABEL, EXPORT_PACKAGE_LABEL, GAMEPLAY,
        GAME_OPS, GAME_OPTION, GAME_OPTION_GROUPS, GAME_OPTION_TITLES, GAME_WIDE, ITEM, LUIGI,
        MERCY_RULE_LEVELS, MERCY_RULE_STOCK, MODEL_OPS, OK, PACKAGE, PACKAGE_EXPORT_REL, PACKAGE_EXTENSION,
        PACKAGE_FILTERS, PACKAGE_STAT_ORIGIN, PACK_DIR_REL, PACK_EXTENSION, PACK_PLAN_REL, PASTE_LABEL,
        SLGMODEL, SLGMODEL_EXTENSION, SLGMODEL_FILTERS, SLOT_EXPORT_REL, SLOT_PLAN_REL, STADIUM,
        STAGED_CLIPS_REL, STAGED_ICONS_REL, STATE_REL, STAT_EDITS, STAT_RESET, SWITCH_PLAN_REL, SlotDialog,
        TEXT, UNNAMED, WARN, WRITING_FLAGS, _BUILD_LINE, _mb, chain_writes, hex_id,
    )
    from grid_images import (
        IconPreview, SOURCE_BOX, _icon_import, banana_icon, flag_icon, source_thumbnail,
    )
    from grid_game_options import (
        AUTOPICKER_DISABLE, AUTOPICKER_ENABLE, AUTOPICKER_KEY, CHEATS, CHEATS_NOTE, CLOSE_PLAY_DISABLE,
        CLOSE_PLAY_STAGE, CPU_FIXES, CPU_MANAGEMENT_DISABLE, CPU_MANAGEMENT_ENABLE, CPU_VS_CPU_DISABLE,
        CPU_VS_CPU_ENABLE, EXTRAS, EXTRAS_99_0, EXTRAS_MISC, EXTRA_COMPANIONS, EXTRA_TOGGLES,
        MERCY_RULE_RESET, MERCY_RULE_STAGE, PLAYER_HEAP_LEVELS, PLAYER_HEAP_SAFE_KB, PLAYER_HEAP_STOCK,
        PLAYER_HEAP_STOCK_LABEL, STRIKE_ZONE_DISABLE, STRIKE_ZONE_ENABLE, STRIKE_ZONE_KEY, _cheat_label,
        _close_play_of, _cpu_fix_label, _effective_options, _game_options, _kb_text, _mercy_rule_of,
        _pending_suffix, autopicker_button, autopicker_command, autopicker_status, cheat_button,
        cheat_command, cheat_status, close_play_buttons, close_play_edit, close_play_key, close_play_level,
        close_play_snap, close_play_status, cpu_fix_button, cpu_fix_edits, cpu_fix_status, cpu_gecko_warning,
        cpu_management_button, cpu_management_edit, cpu_management_status, cpu_vs_cpu_button,
        cpu_vs_cpu_edits, cpu_vs_cpu_status, extras_gecko_warning, game_option_edit, mercy_rule_buttons,
        mercy_rule_clamp, mercy_rule_edit, mercy_rule_key, mercy_rule_level, mercy_rule_status,
        player_heap_choice, player_heap_choices, player_heap_command, player_heap_is_big, player_heap_keys,
        player_heap_level, player_heap_status, staged_game_options, strike_zone_button, strike_zone_command,
        strike_zone_status,
    )
    from grid_state import (
        CHAR_VOICE, CHAR_VOICE_DEFAULT, FIT_CHOICES, FRONT, GRID, GridNav, HAS_ALT_TEXT, IMAGE_EXTENSIONS,
        IMAGE_FILTERS, IMAGE_PATTERNS, MII_START, NEW_START, PLATE_FONT_SIZE, PLATE_FONT_WEIGHT,
        PLATE_TEXT_WIDTH, PLAYER_END, SET_ALT_TEXT, SIDE, SIDE_ALT, SLOT, SPECIES_CLIP, SPECIES_CLIPS,
        SQUARE, SQUARE_PACK, STATE_SOURCES, STATE_VERSION, StateLoader, VIEW_LABELS, _CHAR_INDEX,
        _STATE_KEYS, _labelled, _pack_label, animations_text, can_replace_portrait, char_voice_choices,
        char_voice_current, char_voice_edit, char_voice_text, characters, choice_index, error_message,
        has_alt_icon, icon_file, icon_note, icon_ref, is_fallback, known_name, name_of, name_problem,
        portrait_tip, rename_prefill, slot_count, species_clip_edit, square_label, square_pack_choices,
        square_pack_edits, square_tooltip, state_problem, state_signature, stats_choices, stats_text,
        stock_luigi_note, view_label, voice_choices, voice_reach, voice_text,
    )
    from grid_commands import (
        DEPLOY_ARGS, DEPLOY_LABEL, DEPLOY_SETTING, DIFF_TEXT, EQUIP_FIELDS, GAMEPLAY_DIR_REL,
        GAMEPLAY_DIR_SETTING, GAMEPLAY_EXTENSION, GAMEPLAY_REPORT_REL, GAMEPLAY_RESET, GAMEPLAY_STAGING_REL,
        GAME_DIR_FILE_REL, GAME_SUBDIRS, PACK_FIELDS, PATCH_DEPLOY_LABEL, PATCH_LABEL, SWITCH_FIELDS,
        _GAME_DIR_ENCODING, _write_whole, accept_gecko_args, accept_memory_args, apply_command, build_sizes,
        clean_game_dir, export_command, export_result, export_toast, game_dir_problem, gameplay_edit,
        gameplay_item_label, gameplay_report_command, gameplay_save_command, gameplay_save_path,
        load_command, load_gameplay_report, load_pack_plan, load_plan, pack_fingerprints,
        package_export_command, package_export_result, package_name, patch_label, preview_command,
        read_game_dir, save_command, staged_gameplay_items, switch_command, switch_plan, with_extension,
        write_edits, write_game_dir,
    )
    from grid_pending import (
        EMPTY, EQUIP_LABELS, EQUIP_TILES, EXTRA_FILE, EXTRA_HINT, ITEM_FILTERS, ITEM_LABEL, ITEM_PATCH_NOTE,
        ITEM_TIP, MODEL_FILE_TILES, MODIFIED, ModelFileTile, ORIGINAL, PENDING_STATE, PendingEdits,
        SIZE_FIELDS, SIZE_FILE_PREFIX, SIZE_MAX, SIZE_MIN, SIZE_ORIGIN, STADIUM_LABEL, STADIUM_PATCH_NOTE,
        STADIUM_TIP, STATS_REPLACED, STATS_RESET, STAT_STAGING_REL, _PACKAGE_FILES, _SIZE_FILES, _STAT_IDS,
        _edit_title, _same_file, context_menu, copy_edit, edit_who, equipment_state, equipment_tiles,
        icon_label, item_edit, item_tooltip, package_stat_file, parse_size, pending_size, prune_staged_clips,
        prune_staged_icons, size_doc, size_factor, size_file, size_of, size_text, slot_details,
        staged_clip_files, staged_icon_files, staged_item_files, staged_stat_files, stat_edit,
        chemistry_edit, is_chemistry_item, pending_pastes, staged_chemistry_files, staged_stat_items,
        stat_edit_risk, stat_edits_of, stat_edits_text, stat_file_ids, stat_reset_edit, write_size_file,
    )
    from grid_dialogs import (
        Reference, SKIP_CONFIRM_LABEL, SKIP_CONFIRM_SETTING, VALUE_KINDS, _find_section, _pack_name,
        _partner_lines, _refused_lines, _same_item, _same_path, _section_lines, anyway_label, chemistry_dialog,
        dialog_skippable, export_failed_dialog, export_pending_dialog, game_option_dialog, gameplay_dialog,
        gameplay_load_dialog, gameplay_risk, item_dialog, items_dialog, load_dialog, new_targets,
        package_dialog, package_export_failed_dialog, save_pending_dialog, slot_dialog, staged_package_files,
        stat_edits_dialog, summary_dialog, switch_dialog, with_gecko_warning, with_memory_warning,
    )


# Names of the modules above that are reached through this one.
__all__ = [
    'gecko_compat', 'ANIM', 'ANIM_FILTERS', 'AUTOPICKER_DISABLE', 'AUTOPICKER_ENABLE', 'AUTOPICKER_KEY',
    'CHAR_VOICE', 'CHAR_VOICE_DEFAULT', 'CHEATS', 'CHEATS_NOTE', 'CLOSE_PLAY_DISABLE', 'CLOSE_PLAY_GECKO',
    'CLOSE_PLAY_LEVELS', 'CLOSE_PLAY_STAGE', 'COPY', 'COPY_LABEL', 'COPY_MULTIPLE', 'CPU_FIXES',
    'CPU_MANAGEMENT_DISABLE', 'CPU_MANAGEMENT_ENABLE', 'CPU_VS_CPU_DISABLE', 'CPU_VS_CPU_ENABLE',
    'CPU_VS_CPU_OPTIONS', 'DEPLOY_ARGS', 'DEPLOY_LABEL', 'DEPLOY_SETTING', 'DIFF_TEXT', 'EDITS_REL', 'EMPTY',
    'EQUIP_FIELDS', 'EQUIP_LABELS', 'EQUIP_OPS', 'EQUIP_TILES', 'ERROR', 'EXPORT_LABEL',
    'EXPORT_PACKAGE_LABEL', 'EXTRAS', 'EXTRAS_99_0', 'EXTRAS_MISC', 'EXTRA_COMPANIONS', 'EXTRA_FILE',
    'EXTRA_HINT', 'EXTRA_TOGGLES', 'FIT_CHOICES', 'FRONT', 'GAMEPLAY', 'GAMEPLAY_DIR_REL',
    'GAMEPLAY_DIR_SETTING', 'GAMEPLAY_EXTENSION', 'GAMEPLAY_REPORT_REL', 'GAMEPLAY_RESET',
    'GAMEPLAY_STAGING_REL', 'GAME_DIR_FILE_REL', 'GAME_OPS', 'GAME_OPTION', 'GAME_OPTION_GROUPS',
    'GAME_OPTION_TITLES', 'GAME_SUBDIRS', 'GAME_WIDE', 'GRID', 'GridNav', 'HAS_ALT_TEXT', 'IMAGE_EXTENSIONS',
    'IMAGE_FILTERS', 'IMAGE_PATTERNS', 'ITEM', 'ITEM_FILTERS', 'ITEM_LABEL', 'ITEM_PATCH_NOTE', 'ITEM_TIP',
    'IconPreview', 'LUIGI', 'MERCY_RULE_LEVELS', 'MERCY_RULE_RESET', 'MERCY_RULE_STAGE', 'MERCY_RULE_STOCK',
    'MII_START', 'MODEL_FILE_TILES', 'MODEL_OPS', 'MODIFIED', 'ModelFileTile', 'NEW_START', 'OK', 'ORIGINAL',
    'PACKAGE', 'PACKAGE_EXPORT_REL', 'PACKAGE_EXTENSION', 'PACKAGE_FILTERS', 'PACKAGE_STAT_ORIGIN',
    'PACK_DIR_REL', 'PACK_EXTENSION', 'PACK_FIELDS', 'PACK_PLAN_REL', 'PASTE_LABEL', 'PATCH_DEPLOY_LABEL',
    'PATCH_LABEL', 'PENDING_STATE', 'PLATE_FONT_SIZE', 'PLATE_FONT_WEIGHT', 'PLATE_TEXT_WIDTH', 'PLAYER_END',
    'PLAYER_HEAP_LEVELS', 'PLAYER_HEAP_SAFE_KB', 'PLAYER_HEAP_STOCK', 'PLAYER_HEAP_STOCK_LABEL',
    'PendingEdits', 'Reference', 'SET_ALT_TEXT', 'SIDE', 'SIDE_ALT', 'SIZE_FIELDS', 'SIZE_FILE_PREFIX',
    'SIZE_MAX', 'SIZE_MIN', 'SIZE_ORIGIN', 'SKIP_CONFIRM_LABEL', 'SKIP_CONFIRM_SETTING', 'SLGMODEL',
    'SLGMODEL_EXTENSION', 'SLGMODEL_FILTERS', 'SLOT', 'SLOT_EXPORT_REL', 'SLOT_PLAN_REL', 'SOURCE_BOX',
    'SPECIES_CLIP', 'SPECIES_CLIPS', 'SQUARE', 'SQUARE_PACK', 'STADIUM', 'STADIUM_LABEL', 'STADIUM_PATCH_NOTE',
    'STADIUM_TIP', 'STAGED_CLIPS_REL', 'STAGED_ICONS_REL', 'STATE_REL', 'STATE_SOURCES', 'STATE_VERSION',
    'STATS_REPLACED', 'STATS_RESET', 'STAT_EDITS', 'STAT_RESET', 'STAT_STAGING_REL', 'STRIKE_ZONE_DISABLE',
    'STRIKE_ZONE_ENABLE', 'STRIKE_ZONE_KEY', 'SWITCH_FIELDS', 'SWITCH_PLAN_REL', 'SlotDialog', 'StateLoader',
    'TEXT', 'UNNAMED', 'VALUE_KINDS', 'VIEW_LABELS', 'WARN', 'WRITING_FLAGS', '_BUILD_LINE', '_CHAR_INDEX',
    '_GAME_DIR_ENCODING', '_PACKAGE_FILES', '_SIZE_FILES', '_STATE_KEYS', '_STAT_IDS', '_cheat_label',
    '_close_play_of', '_cpu_fix_label', '_edit_title', '_effective_options', '_find_section', '_game_options',
    '_icon_import', '_kb_text', '_labelled', '_mb', '_mercy_rule_of', '_pack_label', '_pack_name',
    '_partner_lines', '_pending_suffix', '_refused_lines', '_same_file', '_same_item', '_same_path',
    '_section_lines', '_write_whole', 'accept_gecko_args', 'accept_memory_args', 'animations_text',
    'anyway_label', 'apply_command', 'autopicker_button', 'autopicker_command', 'autopicker_status',
    'banana_icon', 'build_sizes', 'can_replace_portrait', 'chain_writes', 'char_voice_choices',
    'char_voice_current', 'char_voice_edit', 'char_voice_text', 'characters', 'cheat_button', 'cheat_command',
    'cheat_status', 'choice_index', 'clean_game_dir', 'close_play_buttons', 'close_play_edit',
    'close_play_key', 'close_play_level', 'close_play_snap', 'close_play_status', 'context_menu', 'copy_edit',
    'cpu_fix_button', 'cpu_fix_edits', 'cpu_fix_status', 'cpu_gecko_warning', 'cpu_management_button',
    'cpu_management_edit', 'cpu_management_status', 'cpu_vs_cpu_button', 'cpu_vs_cpu_edits',
    'cpu_vs_cpu_status', 'dialog_skippable', 'edit_who', 'equipment_state', 'equipment_tiles', 'error_message',
    'export_command', 'export_failed_dialog', 'export_pending_dialog', 'export_result', 'export_toast',
    'extras_gecko_warning', 'flag_icon', 'game_dir_problem', 'game_option_dialog', 'game_option_edit',
    'gameplay_dialog', 'gameplay_edit', 'gameplay_item_label', 'gameplay_load_dialog',
    'gameplay_report_command', 'gameplay_risk', 'gameplay_save_command', 'gameplay_save_path', 'has_alt_icon',
    'hex_id', 'icon_file', 'icon_label', 'icon_note', 'icon_ref', 'is_fallback', 'item_dialog', 'item_edit',
    'item_tooltip', 'items_dialog', 'known_name', 'load_command', 'load_dialog', 'load_gameplay_report',
    'load_pack_plan', 'load_plan', 'mercy_rule_buttons', 'mercy_rule_clamp', 'mercy_rule_edit',
    'mercy_rule_key', 'mercy_rule_level', 'mercy_rule_status', 'name_of', 'name_problem', 'new_targets',
    'pack_fingerprints', 'package_dialog', 'package_export_command', 'package_export_failed_dialog',
    'package_export_result', 'package_name', 'package_stat_file', 'parse_size', 'patch_label', 'pending_size',
    'player_heap_choice', 'player_heap_choices', 'player_heap_command', 'player_heap_is_big',
    'player_heap_keys', 'player_heap_level', 'player_heap_status', 'portrait_tip', 'preview_command',
    'prune_staged_clips', 'prune_staged_icons', 'read_game_dir', 'rename_prefill', 'save_command',
    'save_pending_dialog', 'size_doc', 'size_factor', 'size_file', 'size_of', 'size_text', 'slot_count',
    'slot_details', 'slot_dialog', 'source_thumbnail', 'species_clip_edit', 'square_label',
    'square_pack_choices', 'square_pack_edits', 'square_tooltip', 'staged_clip_files', 'staged_game_options',
    'staged_gameplay_items', 'staged_icon_files', 'staged_item_files', 'staged_package_files',
    'staged_stat_files', 'stat_edit', 'stat_edit_risk', 'stat_edits_dialog', 'stat_edits_of',
    'stat_edits_text', 'stat_file_ids', 'stat_reset_edit', 'state_problem', 'state_signature', 'stats_choices',
    'stats_text', 'stock_luigi_note', 'strike_zone_button', 'strike_zone_command', 'strike_zone_status',
    'summary_dialog', 'switch_command', 'switch_dialog', 'switch_plan', 'view_label', 'voice_choices',
    'voice_reach', 'voice_text', 'with_extension', 'with_gecko_warning', 'with_memory_warning', 'write_edits',
    'write_game_dir', 'write_size_file', 'CHEMISTRY_PREFIX', 'CHEMISTRY_STAGING_REL', 'chemistry_edit',
    'is_chemistry_item', 'pending_pastes', 'staged_chemistry_files', 'staged_stat_items', 'chemistry_dialog',
]
