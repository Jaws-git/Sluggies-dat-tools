"""The grid's confirm dialogs as plain data (no Dear PyGui): each builder turns a staging check's plan and
output into a ``SlotDialog`` (title, coloured lines, whether it can be applied). ``slot_dialog`` (one new
edit), ``summary_dialog`` (Patch Game), the export, package, stat edit, roster switch, roster pack
(``load_dialog`` with its ``Reference``, ``save_pending_dialog``), gameplay, game option and item model
dialogs, and the Gecko and player memory warnings added to them.

``gui_grid_dialogs`` draws them. Reached through ``gui_grid``.
"""

import os
from dataclasses import dataclass

try:
    import gecko_compat
    import memory_guard
except ImportError:                    # imported as SluggiesTools.grid_dialogs (tests)
    from SluggiesTools import gecko_compat, memory_guard

if __package__:
    from .grid_common import (
        ANIM, COPY, EQUIP_OPS, ERROR, GAMEPLAY, GAME_OPTION, GAME_WIDE, ITEM, OK, SLGMODEL, STAT_EDITS,
        STAT_RESET, SlotDialog, TEXT, WARN, _mb, hex_id,
    )
    from .grid_state import (
        CHAR_VOICE, SPECIES_CLIP, SPECIES_CLIPS, characters, error_message, known_name, name_of, view_label,
    )
    from .grid_commands import (
        DIFF_TEXT, EQUIP_FIELDS, GAMEPLAY_RESET, PACK_FIELDS, SWITCH_FIELDS, build_sizes,
        gameplay_item_label,
    )
    from .grid_pending import (
        EQUIP_LABELS, MODEL_FILE_TILES, PendingEdits, STATS_REPLACED, _edit_title, _same_file, edit_who,
        size_file, staged_item_files,
    )
else:
    from grid_common import (
        ANIM, COPY, EQUIP_OPS, ERROR, GAMEPLAY, GAME_OPTION, GAME_WIDE, ITEM, OK, SLGMODEL, STAT_EDITS,
        STAT_RESET, SlotDialog, TEXT, WARN, _mb, hex_id,
    )
    from grid_state import (
        CHAR_VOICE, SPECIES_CLIP, SPECIES_CLIPS, characters, error_message, known_name, name_of, view_label,
    )
    from grid_commands import (
        DIFF_TEXT, EQUIP_FIELDS, GAMEPLAY_RESET, PACK_FIELDS, SWITCH_FIELDS, build_sizes,
        gameplay_item_label,
    )
    from grid_pending import (
        EQUIP_LABELS, MODEL_FILE_TILES, PendingEdits, STATS_REPLACED, _edit_title, _same_file, edit_who,
        size_file, staged_item_files,
    )


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
                           (CHAR_VOICE, 'Own voice'), ('portrait_note', 'Portraits'), (STAT_EDITS, 'Stat values')):
            if effects.get(key):
                lines.append((f'{label}: {effects[key]}', TEXT))
        for _file, text in sorted((effects.get('equipment') or {}).items()):
            lines.append((f'Equipment: {text} ({_mb(info.get("bat") or 0)})', TEXT))
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
