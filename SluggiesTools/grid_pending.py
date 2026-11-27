"""The staged edits of the grid (no Dear PyGui): ``PendingEdits`` (the pending list, its overlay text and
portrait previews, the Gecko and memory marks), ``_edit_title``, the context menu and copy / paste edits,
the equipment tiles, the item and stadium model tiles, stat and size edits with their staged files, and
the staged-file lists with their prune helpers (icons, voice clips, packages, stat files, item models).
``slot_details`` is here too: the slot level's summary reads the pending stat and size edits.

Reached through ``gui_grid``.
"""

import json
import os
import time
from dataclasses import dataclass

if __package__:
    from .grid_common import (
        ANIM, CHEMISTRY_PREFIX, COPY, COPY_LABEL, COPY_MULTIPLE, EQUIP_OPS, EXPORT_LABEL, EXPORT_PACKAGE_LABEL,
        GAMEPLAY, GAME_OPS, GAME_OPTION, GAME_OPTION_TITLES, GAME_WIDE, ITEM, MODEL_OPS, PACKAGE_STAT_ORIGIN,
        PASTE_LABEL, SLGMODEL, STADIUM, STAT_EDITS, STAT_RESET, _mb, hex_id,
    )
    from .grid_state import (
        CHAR_VOICE, HAS_ALT_TEXT, NEW_START, SPECIES_CLIP, SPECIES_CLIPS, animations_text, char_voice_text,
        characters, has_alt_icon, known_name, name_of, view_label,
    )
    from .grid_commands import (
        DIFF_TEXT, PACK_FIELDS, _write_whole, gameplay_item_label,
    )
else:
    from grid_common import (
        ANIM, CHEMISTRY_PREFIX, COPY, COPY_LABEL, COPY_MULTIPLE, EQUIP_OPS, EXPORT_LABEL, EXPORT_PACKAGE_LABEL,
        GAMEPLAY, GAME_OPS, GAME_OPTION, GAME_OPTION_TITLES, GAME_WIDE, ITEM, MODEL_OPS, PACKAGE_STAT_ORIGIN,
        PASTE_LABEL, SLGMODEL, STADIUM, STAT_EDITS, STAT_RESET, _mb, hex_id,
    )
    from grid_state import (
        CHAR_VOICE, HAS_ALT_TEXT, NEW_START, SPECIES_CLIP, SPECIES_CLIPS, animations_text, char_voice_text,
        characters, has_alt_icon, known_name, name_of, view_label,
    )
    from grid_commands import (
        DIFF_TEXT, PACK_FIELDS, _write_whole, gameplay_item_label,
    )


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
        """Drop the pending edits of a game-wide ``op`` (``GAMEPLAY``)."""
        self.edits = [e for e in self.edits if e['op'] != op]
        self.sections = [s for s in self.sections if s['action'] != op]

    def discard_stat_items(self, chemistry: bool) -> None:
        """Drop the pending ``STAT_EDITS`` edits of one tool: the Chemistry tab's items (``chemistry``), or the
        stat edit files (the Stat Editor's, sizes, packages)."""
        self.edits = [e for e in self.edits
                      if not (e['op'] == STAT_EDITS and is_chemistry_item(e.get('file')) == chemistry)]
        self.sections = [s for s in self.sections
                         if not (s['action'] == STAT_EDITS
                                 and is_chemistry_item((s.get('edit') or {}).get('file')) == chemistry)]

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

    def drop_equip(self, cid: int, file: int) -> None:
        """Drop the slot's pending equipment edit for slot file ``file`` (the tile's Reset). The slot's red marks
        go with it: they came from a staging check that still had this edit (a bat or glove counts towards the
        player memory), so they may no longer hold. The next staging check and Patch Game's summary work them
        out again."""
        self.edits = [e for e in self.edits if not (int(e['id'], 16) == cid and e['op'] in EQUIP_OPS
                                                    and e.get('file') == file)]
        self.sections = [s for s in self.sections if not (int(s['target'], 16) == cid and s['action'] in EQUIP_OPS
                                                          and (s.get('edit') or {}).get('file') == file)]
        self.gecko.discard(cid)
        self.memory.pop(cid, None)

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
    if edit['op'] == STAT_EDITS and is_chemistry_item(edit.get('file')):
        return 'Pending: chemistry changes'
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
    _write_whole(path, json.dumps(doc, indent=1))
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
    """The pending stat edit files, in staging order (without the Chemistry tab's items)."""
    return [e['file'] for e in pending.edits
            if e['op'] == STAT_EDITS and e.get('file') and not is_chemistry_item(e['file'])]


def is_chemistry_item(item: str | None) -> bool:
    """A ``STAT_EDITS`` edit's ``file`` that is the Chemistry tab's item (``chemistry:<file>``)."""
    return isinstance(item, str) and item.lower().startswith(CHEMISTRY_PREFIX)


def chemistry_edit(path: str) -> dict:
    """The pending edit of a chemistry file the Chemistry tab staged: a ``stat_edits`` op like the Stat Editor's
    files (same chain step, same staging order), told apart by its item prefix."""
    return {'op': STAT_EDITS, 'id': hex_id(GAME_WIDE), 'file': CHEMISTRY_PREFIX + path}


def staged_chemistry_files(pending: 'PendingEdits') -> list[str]:
    """The pending chemistry files (paths, without the item prefix), in staging order."""
    return [e['file'][len(CHEMISTRY_PREFIX):] for e in pending.edits
            if e['op'] == STAT_EDITS and is_chemistry_item(e.get('file'))]


def staged_stat_items(pending: 'PendingEdits') -> list[str]:
    """Every pending ``--apply-stat-edits`` item in staging order, as Patch Game's first stat step gets them:
    stat edit files, chemistry items and ``reset:0xNN`` for the pending clears."""
    out = []
    for e in pending.edits:
        if e['op'] == STAT_EDITS and e.get('file'):
            out.append(e['file'])
        elif e['op'] == STAT_RESET:
            out.append(f'reset:{hex_id(int(e["id"], 16))}')
    return out


def pending_pastes(pending: 'PendingEdits') -> list[tuple[int, int]]:
    """``(target, source)`` of the pending pastes, in staging order. A paste's stat values (its source's
    chemistry included) are written after every other stat item."""
    out = []
    for e in pending.edits:
        if e['op'] == COPY and e.get('source') is not None:
            try:
                out.append((int(e['id'], 16), int(e['source'], 16) if isinstance(e['source'], str) else e['source']))
            except (TypeError, ValueError):
                pass
    return out


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
    when unreadable. For the Chemistry tab's item (``chemistry:<file>``): both characters of every pair. Cached per
    path and modification time (the grid asks on every redraw)."""
    chemistry = is_chemistry_item(path)
    if chemistry:
        path = path[len(CHEMISTRY_PREFIX):]
    try:
        key = (os.path.normcase(os.path.abspath(path)), os.path.getmtime(path))
    except (OSError, TypeError, ValueError):
        return frozenset()
    if key not in _STAT_IDS:
        ids = set()
        try:
            with open(path, 'r', encoding='utf-8') as f:
                doc = json.load(f)
            if chemistry:
                rows = doc.get('pairs') if isinstance(doc, dict) else None
                named = [text for row in rows or () if isinstance(row, list) for text in row[:2]]
            else:
                named = (doc.get('characters') or {}) if isinstance(doc, dict) else ()
            for text in named:
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
