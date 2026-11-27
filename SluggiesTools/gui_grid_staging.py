"""Character grid staging: from an edit to the game files. The staging check
(``start.py --apply-slots edits.json --dry-run``) and its confirm dialog, the pending list's upkeep, the
edits other tabs hand in (``stage_edit``, ``offer_stat_edits``, ``offer_gameplay``), item and stadium
models, roster packs and the roster switch, Patch Game with its summary, and the copy to the game
directory. ``_run`` / ``_run_or_say`` start the tab's own command chains.

A mixin of ``gui_character_grid.CharacterGridTab``.
"""

import os

import gecko_compat
import gui_grid
import memory_guard
from gui_grid_layout import (
    MODEL_FILE_DIRS, PACK_DIR_SETTING, _OK, _PENDING, _WARN, _draws,
)


class GridStagingMixin:
    """``CharacterGridTab``'s staging checks, pending edits, Patch Game, roster packs and deploy."""

    @_draws
    def _pending_changed(self):
        """Redraw what shows the pending list: the grid's markers, the open levels, the buttons."""
        self._prune_staged_icons()
        self._prune_staged_clips()
        self._draw_grid()
        self._draw_popups()
        self.set_busy(self.app.busy)
        for callback in self.on_pending_changed:
            callback()

    def _prune_staged_clips(self):
        """Delete staged clips (``_gui/slot/staged_clips``) that no pending edit uses any more."""
        gui_grid.prune_staged_clips(os.path.join(self.app.root_dir, gui_grid.STAGED_CLIPS_REL),
                                    gui_grid.staged_clip_files(self.pending))

    def _start_preview(self, cid, sluggie, rename=None, value=None, icon=None, equip=None, copy_from=None,
                       anim=None, low=False, slgmodel=None):
        """The staging check (the pending edits plus this one; this one's build check); the confirm dialog
        opens when it is done. ``low``: ``sluggie`` is a Low model pick (``slot_plan`` refuses a High model and joins
        it to the slot's pending High pick); ``rename``: the new name text (blank resets), a rename edit instead of patch/clear;
        ``value``: ``(op, source)``, a stats or voice edit (source None: back to the default); ``icon``: a
        finished icon edit (``IconPreview.edit``); ``equip``: ``(slot file, .sluggie or None)``, an equipment edit
        (None: reset that file); ``copy_from``: the copied character, a paste (the slot becomes its clone); ``anim``:
        a list of ``.anm`` files, one animation bank edit each (staged together); ``slgmodel``: a character file of
        the Sluggers Characters patcher (``.slgmodel``), an import edit."""
        if self._locked():
            return
        kind = (gui_grid.SLGMODEL if slgmodel is not None
                else gui_grid.COPY if copy_from is not None else gui_grid.ANIM if anim is not None
                else 'equip' if equip is not None and equip[1] else 'equip_clear' if equip is not None
                else 'icon' if icon is not None else 'rename' if rename is not None else value[0] if value
                else 'patch' if sluggie else 'clear')
        view = icon['view'] if icon is not None else None
        gear_file = equip[0] if equip is not None else None
        if slgmodel is not None:
            edit = {'op': gui_grid.SLGMODEL, 'id': gui_grid.hex_id(cid), 'file': os.path.abspath(slgmodel)}
        elif copy_from is not None:
            edit = gui_grid.copy_edit(cid, copy_from)
        elif anim is not None:
            edit = [{'op': gui_grid.ANIM, 'id': gui_grid.hex_id(cid), 'anm': path} for path in anim]
        elif equip is not None:
            edit = ({'op': 'equip', 'id': gui_grid.hex_id(cid), 'file': equip[0], 'sluggie': equip[1],
                     'origin': 'user'} if equip[1] else
                    {'op': 'equip_clear', 'id': gui_grid.hex_id(cid), 'file': equip[0]})
        elif icon is not None:
            edit = icon
        elif rename is not None:
            edit = {'op': 'rename', 'id': gui_grid.hex_id(cid), 'text': rename}
        elif value is not None and value[0] == gui_grid.CHAR_VOICE:
            edit = gui_grid.char_voice_edit(cid, value[1], value[2])
        elif value is not None:
            edit = {'op': value[0], 'id': gui_grid.hex_id(cid), 'source': value[1]}
        elif sluggie:
            edit = {'op': 'patch', 'id': gui_grid.hex_id(cid), ('low' if low else 'file'): sluggie}
        else:
            edit = {'op': 'clear', 'id': gui_grid.hex_id(cid)}
        self._check_and_confirm(cid, edit, kind, sluggie, view, gear_file, anm=anim)

    def stage_edit(self, edit, kind, moment=None):
        """Another tab stages an edit (the Voices tab's clip swaps): the staging check, then the confirm dialog, like
        the slot buttons. False when the tab is busy or a roster pack load is pending."""
        if self._locked() or self.pending.pack is not None:
            return False
        self._check_and_confirm(int(edit['id'], 16), edit, kind, None, None, None, moment)
        return True

    def _check_and_confirm(self, cid, edit, kind, sluggie=None, view=None, gear_file=None, moment=None, anm=None):
        try:
            gui_grid.write_edits(self.edits_path, self.pending.staging(edit))
        except OSError as exc:
            self.app.log_line(f'[character grid] could not write {self.edits_path}: {exc}', _WARN)
            return
        self.action = (cid, sluggie)
        if not self._run([gui_grid.preview_command(self.edits_path)], 'Checking the edit...',
                         lambda code, output: self._show_confirm(cid, sluggie, code, output, kind, view,
                                                                 gear_file, moment, anm)):
            self._end_action()

    def _stage_package(self, plan):
        """Stage on a package's confirm dialog: its voice pack (if it made one) stays."""
        path = self._package[0] if self._package else None
        self._package = None
        self._stage(plan)
        if path:
            self.app.log_line(f'[character grid] {os.path.basename(path)} staged; "Patch Game" writes it.', _PENDING)

    def _stage_with_stat_reset(self, cid, plan):
        """Stage + clear stat edits: one more staging check with the slot's stat edits cleared behind the edit
        just checked (its build check passed, so it is not run again); staged straight away when it passes."""
        self._end_action()
        edits = {'edits': [dict(e, checked=True) for e in plan.get('merged') or []] + [gui_grid.stat_reset_edit(cid)]}
        try:
            gui_grid.write_edits(self.edits_path, edits)
        except OSError as exc:
            self.app.log_line(f'[character grid] could not write {self.edits_path}: {exc}', _WARN)
            return
        self.action = (cid, None)

        def done(code, output):
            state = self.nav.state or self.loader.state
            checked = gui_grid.load_plan(self.plan_path)
            if code == 0 and checked is not None and not checked['refused']:
                self._stage(checked)
                return
            self._dialog(gui_grid.slot_dialog(state, cid, False, checked, code, output, self.pending,
                                              kind=gui_grid.STAT_RESET), self._end_action)
        if not self._run([gui_grid.preview_command(self.edits_path)], 'Checking the edit...', done):
            self._end_action()

    def offer_stat_edits(self, path, quiet_refusal=False):
        """The staging check of a stat edit file the Stat Editor sent (the pending edits plus this one), then its
        confirm dialog; Stage adds it to the pending list. ``quiet_refusal`` (a file left from an earlier session):
        a refused or empty file only gets a log line. False when the tab is busy (the caller tries again later)."""
        if self._locked() or self.confirm is not None:
            return False
        if self.pending.pack is not None:
            self.action = 'ask'
            self._dialog(gui_grid.SlotDialog('Stat edits: refused', [
                ('A roster pack load is staged: it replaces the whole roster, so stat values cannot be staged on top '
                 'of it.', gui_grid.ERROR),
                ('Patch Game or discard the load first, then open the Stat Editor again. Nothing was staged.',
                 gui_grid.TEXT)]), self._end_action)
            return True
        try:
            gui_grid.write_edits(self.edits_path, self.pending.staging(gui_grid.stat_edit(path)))
        except OSError as exc:
            self.app.log_line(f'[stat editor] could not write {self.edits_path}: {exc}', _WARN)
            return True
        self.action = 'stat_edits'
        self.stat_offer = path                 # the Stat Editor tab keeps it while it is offered

        def show(code, output):
            state = self.nav.state or self.loader.state
            plan = gui_grid.load_plan(self.plan_path)
            dialog = gui_grid.stat_edits_dialog(state, plan, code, output, path)
            if quiet_refusal and not dialog.can_apply:
                self._end_action()
                why = dialog.lines[0][0] if dialog.lines else dialog.title.removeprefix('Stat edits: ')
                self.app.log_line(f'[stat editor] the edit file left from an earlier session was dropped: {why}',
                                  _WARN)
                return
            self._dialog(dialog, lambda: self._stage(plan), ok_label='Stage')
        if not self._run([gui_grid.preview_command(self.edits_path)], 'Checking the stat edits...', show):
            self._end_action()
            return False
        return True

    def offer_gameplay(self, edit, replace=False, on_staged=None, confirm=False):
        """The staging check of a gameplay item (the Gameplay tab: its staged values, a loaded settings file, a
        reset): the pending edits plus this one (``replace``: without the pending gameplay edits, which a load
        replaces). It is staged straight away when the check passes without warnings, unless ``confirm``; else its
        dialog asks. ``on_staged`` runs once it is staged. False when the tab is busy or a roster pack load is
        pending."""
        if self._locked() or self.confirm is not None or self.pending.pack is not None:
            return False
        try:
            gui_grid.write_edits(self.edits_path, self.pending.staging(
                edit, drop_op=gui_grid.GAMEPLAY if replace else None))
        except OSError as exc:
            self.app.log_line(f'[gameplay] could not write {self.edits_path}: {exc}', _WARN)
            return False
        self.action = 'gameplay'

        def stage(plan):
            self._stage(plan)
            if on_staged is not None:
                on_staged()

        def show(code, output):
            state = self.nav.state or self.loader.state
            plan = gui_grid.load_plan(self.plan_path)
            dialog = gui_grid.gameplay_dialog(state, plan, code, output, edit['file'])
            if dialog.can_apply and not confirm and not any(k == gui_grid.WARN for _t, k in dialog.lines):
                stage(plan)
                return
            self._dialog(dialog, lambda: stage(plan), ok_label='Stage')
        if not self._run([gui_grid.preview_command(self.edits_path)], 'Checking the gameplay settings...', show):
            self._end_action()
            return False
        return True

    def offer_chemistry(self, path, on_staged=None):
        """The staging check of a chemistry file the Chemistry tab wrote (the pending edits plus this one, as a
        new item at the end of the list). It is staged straight away when the check passes without warnings; else
        its dialog asks (warnings) or tells (a refusal). ``on_staged`` runs once it is staged. False when the tab is
        busy or a roster pack load is pending."""
        if self._locked() or self.confirm is not None or self.pending.pack is not None:
            return False
        edit = gui_grid.chemistry_edit(path)
        try:
            gui_grid.write_edits(self.edits_path, self.pending.staging(edit))
        except OSError as exc:
            self.app.log_line(f'[chemistry] could not write {self.edits_path}: {exc}', _WARN)
            return False
        self.action = 'chemistry'

        def stage(plan):
            self._stage(plan)
            if on_staged is not None:
                on_staged()

        def show(code, output):
            state = self.nav.state or self.loader.state
            plan = gui_grid.load_plan(self.plan_path)
            dialog = gui_grid.chemistry_dialog(state, plan, code, output, edit['file'])
            for text, kind in dialog.lines:
                if kind == gui_grid.WARN:
                    self.app.log_line(f'[chemistry] {text}', _WARN)
            if dialog.can_apply and not any(k == gui_grid.WARN for _t, k in dialog.lines):
                stage(plan)
                return
            self._dialog(dialog, lambda: stage(plan), ok_label='Stage')
        if not self._run([gui_grid.preview_command(self.edits_path)], 'Checking the chemistry changes...', show):
            self._end_action()
            return False
        return True

    # ------------------------------------------------------------------ item and stadium models
    def _on_items(self, op):
        """The item / stadium tile (``op``): with pending models of the tile a dialog lists them (Add..., Discard);
        else the picker."""
        if self._locked():
            return
        tile = gui_grid.MODEL_FILE_TILES[op]
        if self.pending.pack is not None:
            self.action = 'ask'
            self._dialog(gui_grid.SlotDialog(f'{tile.title}: refused', [
                (f'A roster pack load is staged: Patch Game or discard it first, then stage the {tile.noun}s.',
                 gui_grid.ERROR)]), self._end_action)
            return
        if not gui_grid.staged_item_files(self.pending, op):
            self._pick_items(op)
            return
        self.action = 'ask'
        self._dialog(gui_grid.items_dialog(self.pending, op), lambda: (self._end_action(), self._pick_items(op)),
                     ok_label='Add...', extra=(f'Discard {tile.label.lower()}', lambda: self._discard_items(op)))

    def _pick_items(self, op):
        self.action = 'items'
        self.set_busy(False)
        if not self.app.pick_files('grid_sluggie_dialog', f'Select {gui_grid.MODEL_FILE_TILES[op].noun} .sluggie '
                                   'files', gui_grid.ITEM_FILTERS, lambda paths: self._on_item_paths(paths, op),
                                   on_cancel=self._end_action, multi=True, remember=MODEL_FILE_DIRS[op]):
            self._end_action()

    def _on_item_paths(self, paths, op):
        chosen = list(dict.fromkeys(os.path.abspath(p) for p in paths
                                    if p and p.lower().endswith('.sluggie') and os.path.isfile(p)))
        if self.action != 'items' or not chosen:
            self.app.log_line(f'[character grid] no .sluggie file chosen: {paths[0]}', _WARN)
            self._end_action()
            return
        self.action = None
        self._offer_items(chosen, op)

    def _offer_items(self, paths, op):
        """The staging check of picked item / stadium models (the pending edits plus these), then their confirm
        dialog."""
        try:
            gui_grid.write_edits(self.edits_path, self.pending.staging([gui_grid.item_edit(p, op) for p in paths]))
        except OSError as exc:
            self.app.log_line(f'[character grid] could not write {self.edits_path}: {exc}', _WARN)
            return
        self.action = 'items'

        def show(code, output):
            plan = gui_grid.load_plan(self.plan_path)
            dialog = gui_grid.item_dialog(self.nav.state or self.loader.state, plan, code, output, paths, op)
            self._dialog(dialog, lambda: self._stage(plan), ok_label='Stage')
        if not self._run([gui_grid.preview_command(self.edits_path)],
                         f'Checking the {gui_grid.MODEL_FILE_TILES[op].noun}s...', show):
            self._end_action()

    def _discard_items(self, op):
        self._end_action()
        self.pending.discard_op(op)
        self._pending_changed()

    def _stage(self, plan):
        self._end_action()
        self.pending.accept(plan, gecko_compat.read_details(self.app.root_dir),     # the staging check's results
                            memory_guard.read_details(self.app.root_dir))
        self.app.log_line(f'[character grid] {len(self.pending)} pending edit(s); "Patch Game" writes them.', _PENDING)
        self._pending_changed()

    def _on_discard(self, cid):
        if self._locked():
            return
        self.pending.discard(cid)
        self._pending_changed()

    def config_risks(self, stats_lost=False, stats_reason=gui_grid.STATS_REPLACED):
        """Why the current configuration would be lost by an export / roster injection (empty: vanilla, nothing
        pending, or the grid not read yet). ``stats_lost``: every stat edit goes (``stats_reason``: the untangle
        export replaces main.dol, a reset clears them); else only stat edits of new IDs are at risk
        (``gui_grid.stat_edit_risk``)."""
        risks = []
        state = self.loader.state
        if state is not None and state.get('kind') != 'stock':
            risks.append('The game files hold a custom roster configuration.')
        if len(self.pending):
            risks.append(f'{len(self.pending)} pending grid edit{"s" if len(self.pending) != 1 else ""} '
                         '(not written to the game yet).')
        stat_risk = gui_grid.stat_edit_risk(state, stats_lost, stats_reason)
        if stat_risk:
            risks.append(stat_risk)
        gameplay_risk = gui_grid.gameplay_risk(state) if stats_lost and stats_reason == gui_grid.STATS_REPLACED \
            else None                                # a roster run (a reset included) carries them
        if gameplay_risk:
            risks.append(gameplay_risk)
        return risks

    def config_guard(self, then, title, action, stats_lost=False, stats_reason=gui_grid.STATS_REPLACED):
        """Run ``then``; when the configuration is not vanilla, has pending grid edits or stat edits at risk ask
        first: OK (go on, pending edits are dropped) / Save Configuration (roster pack, then go on) / Cancel.
        ``stats_lost``, ``stats_reason``: ``config_risks``."""
        risks = self.config_risks(stats_lost, stats_reason)
        if not risks:
            then()
            return
        if self.confirm is not None:
            return                                   # another dialog is up
        lines = [(f'{action} replaces the current configuration, which would be lost:', gui_grid.TEXT)]
        lines += [(f'  {risk}', gui_grid.WARN) for risk in risks]
        lines.append(('"Save Configuration" stores the game as it is in a roster pack first (pending edits and '
                      'stat values are not part of it).', gui_grid.TEXT))

        def proceed():
            self._end_action()
            self.pending.clear()
            self._pending_changed()
            then()

        def save():
            self._end_action()
            self._pick_pack(save=True, after=proceed)
        self.action = 'ask'
        self._dialog(gui_grid.SlotDialog(title, lines, True), proceed, ok_label='OK',
                     extra=('Save Configuration', save))

    def switch_roster(self, path):
        """Roster Size tab, "Inject roster": pending edits are discarded first (asks), then the switch is planned
        and built in memory (``--roster --config FILE --dry-run``) and its dialog lists what the slots keep and
        lose; Inject runs the chain (``Roster/migrate.py``)."""
        if self._locked():
            return

        def check():
            self.action = 'switch'
            if not self._run([gui_grid.switch_command(path, dry_run=True)], 'Planning the roster switch...',
                             lambda code, output: self._show_switch(path, code, output)):
                self._end_action()
        self.confirm_discard(check, 'Inject the roster and discard the pending edits?',
                             'Injecting a roster rebuilds the grid, so the pending edits would no longer fit.')

    def _gecko_break(self):
        """Whether the dry run that just ended recorded a Gecko code compatibility break (``gecko_compat``)."""
        return gecko_compat.read_check(self.app.root_dir)

    def _switch(self, path, accept_gecko=False):
        self._end_action()
        self.pending.clear()
        self._pending_changed()

        def done(code, _output):
            if code == 0:
                self.app.log_line(f'[character grid] roster {os.path.basename(path)} injected; slots on both grids '
                                  'kept their customisations.', _OK)
            else:
                self.app.log_line('[character grid] the roster switch stopped at a failed step: see the log above; '
                                  'the re-read shows what landed.', _WARN)
            self.set_busy(self.app.busy)
        self._run_or_say([gui_grid.switch_command(path, accept_gecko=accept_gecko)], 'Injecting the roster...', done)

    def _on_discard_all(self):
        if self._locked() or not len(self.pending):
            return
        self.confirm_discard(lambda: None, 'Discard all pending edits?')

    def _on_patch_game(self):
        """The full dry run (fresh read, every build check), then the summary."""
        if self._locked() or not len(self.pending):
            return
        if self.pending.pack is not None:
            self._patch_game_pack()
            return
        try:
            gui_grid.write_edits(self.edits_path, self.pending.to_file())
        except OSError as exc:
            self.app.log_line(f'[character grid] could not write {self.edits_path}: {exc}', _WARN)
            return
        self.action = 'patch_game'
        if not self._run([gui_grid.preview_command(self.edits_path)], 'Checking every pending edit...',
                         self._show_summary):
            self._end_action()

    def _patch_game(self, accept_gecko=False, accept_memory=False):
        self._end_action()
        deploy = self._deploying()

        def done(code, _output):
            if code == 0:
                self.pending.clear()
                self._pending_changed()      # prunes the staged files and tells the other tabs
                self.app.log_line('[character grid] Patch Game done: every pending edit is written.', _OK)
                if deploy:
                    self._deploy(patched=True)          # its toast says how the copy went
                else:
                    self.app.toast.ok('Game patched: every pending edit is written.')
            else:
                self.app.log_line('[character grid] Patch Game stopped at a failed step: the pending edits are '
                                  'kept; the re-read shows what landed. Fix the cause and run Patch Game again.',
                                  _WARN)
                self.app.toast.error('Patch Game failed: the pending edits are kept.')
            self.set_busy(self.app.busy)
        self._run_or_say([gui_grid.apply_command(self.edits_path, accept_gecko, accept_memory)],
                         'Patching the game...', done)

    # ------------------------------------------------------------------ roster packs
    def _on_save_pack(self):
        """Save roster...: with pending edits ask first (they are not in the game yet), then the file dialog."""
        if self._locked():
            return
        if len(self.pending):
            self.action = 'ask'
            self._dialog(gui_grid.save_pending_dialog(self.loader.state, self.pending),
                         lambda: (self._end_action(), self._pick_pack(save=True)), ok_label='Save without them')
            return
        self._pick_pack(save=True)

    def _on_load_pack(self):
        """Load roster...: pending edits are discarded first (asks), then the file dialog."""
        if self._locked():
            return
        self.confirm_discard(lambda: self._pick_pack(save=False), 'Load a roster pack and discard the pending edits?',
                             'Loading a roster pack replaces the whole roster, so the pending edits would no '
                             'longer fit.')

    def _pick_pack(self, save, after=None):
        self.action = 'pack'
        self._after_save = after
        self.set_busy(self.app.busy)
        if save:
            try:
                os.makedirs(self.pack_dir, exist_ok=True)
            except OSError as exc:           # the dialog then starts elsewhere; the tab must not stay locked
                self.app.log_line(f'[character grid] could not create {self.pack_dir}: {exc}', _WARN)
        filters = [('Roster packs', '*' + gui_grid.PACK_EXTENSION), ('All files', '*.*')]
        on_paths = self._on_pack_save_paths if save else self._on_pack_load_paths
        if not self.app.pick_files('grid_pack_save_dialog' if save else 'grid_pack_load_dialog',
                                   'Save the roster as' if save else 'Load a roster pack', filters, on_paths,
                                   on_cancel=self._end_action, initial_dir=self.pack_dir, save=save,
                                   default_ext=gui_grid.PACK_EXTENSION.lstrip('.'), remember=PACK_DIR_SETTING):
            self._end_action()

    def _on_pack_save_paths(self, paths):
        path = paths[0] if paths else None
        after, self._after_save = self._after_save, None
        self._end_action()
        if not path:
            return
        path = gui_grid.with_extension(path)

        def done(code, _output):
            fingerprints = gui_grid.pack_fingerprints(path) if code == 0 else None
            if fingerprints is None:
                self.app.log_line('[character grid] the roster pack was not saved: see the log above.', _WARN)
                return
            self.reference = gui_grid.Reference(f'saved ({os.path.basename(path)})', fingerprints)
            self.app.log_line(f'[character grid] roster saved to {path}', _OK)
            self._refresh_markers()
            if after is not None:
                after()
        self._run_or_say([gui_grid.save_command(path)], 'Saving the roster pack...', done)

    def _on_pack_load_paths(self, paths):
        path = next((p for p in paths if p and os.path.isfile(p)), None)
        self._end_action()
        if path is None:
            self.app.log_line(f'[character grid] no roster pack chosen: {(paths or [None])[0] or "(none)"}', _WARN)
            return
        self.action = 'pack'
        if not self._run([gui_grid.load_command(path, dry_run=True)], 'Comparing the pack with the game...',
                         lambda code, output: self._show_load(path, code, output)):
            self._end_action()

    def _stage_pack(self, path, plan):
        self._end_action()
        self.pending.stage_pack(path, plan or {})
        self.app.log_line(f'[character grid] roster pack {os.path.basename(path)} staged; "Patch Game" loads it.',
                          _PENDING)
        self._pending_changed()

    def _patch_game_pack(self):
        """Patch Game with a staged pack load: check the pack against the game again, then the summary."""
        path = self.pending.pack['path']
        if not os.path.isfile(path):
            self.app.log_line(f'[character grid] the staged roster pack is gone: {path}', _WARN)
            return
        self.action = 'patch_game'
        if not self._run([gui_grid.load_command(path, dry_run=True)], 'Checking the roster pack...',
                         lambda code, output: self._show_load(path, code, output, writing=True)):
            self._end_action()

    def _load_pack(self, path, plan, accept_gecko=False, accept_memory=False):
        self._end_action()
        deploy = self._deploying()

        def done(code, _output):
            if code == 0:
                self.pending.clear()
                self.reference = gui_grid.Reference(f'loaded ({os.path.basename(path)})',
                                                    (plan or {}).get('pack_fingerprints') or {})
                self._show_status()          # the reference line
                self._pending_changed()      # redraws the markers; the other tabs show the pending list too
                self.app.log_line(f'[character grid] Patch Game done: roster pack {os.path.basename(path)} '
                                  'loaded.', _OK)
                if deploy:
                    self._deploy(patched=True)
                else:
                    self.app.toast.ok(f'Game patched: roster pack {os.path.basename(path)} loaded.')
            else:
                self.app.log_line('[character grid] loading the roster pack stopped at a failed step: the load stays '
                                  'pending; the re-read shows what landed. Run Patch Game again once the cause is '
                                  'fixed.', _WARN)
                self.app.toast.error('Patch Game failed: the roster pack load stays pending.')
                self._refresh_markers()
            self.set_busy(self.app.busy)
        self._run_or_say([gui_grid.load_command(path, accept_gecko=accept_gecko, accept_memory=accept_memory)],
                         'Loading the roster pack...', done)

    def _patch_ok_label(self):
        return gui_grid.PATCH_DEPLOY_LABEL if self._deploying() else gui_grid.PATCH_LABEL

    def _deploy(self, patched=False):
        """After a successful Patch Game with "Deploy patched files to game directory" ticked (``patched``), or
        the Options tab's copy: copy the game files (``start.py --deploy-game-files``, deploy_files.py). The
        game directory it reads (3_Output_Dat/sluggiespath) is asked for here when it is missing or no game
        folder; Cancel or a blank / wrong answer stops before the copy. Every outcome ends in a toast."""
        not_copied = 'Game patched, but the files were not copied' if patched else 'Files not copied'
        path_file = os.path.join(self.app.root_dir, gui_grid.GAME_DIR_FILE_REL)
        stored = gui_grid.read_game_dir(path_file)
        if gui_grid.game_dir_problem(stored) is None:
            self._copy_files(stored, patched)
            return

        def answered(text):
            game_dir = gui_grid.clean_game_dir(text)
            problem = gui_grid.game_dir_problem(game_dir)
            if problem is not None:
                self.app.log_line(f'[character grid] files not copied: {problem}', _WARN)
                self.app.toast.error(f'{not_copied}: {problem}', hint=None)
                return
            try:
                gui_grid.write_game_dir(path_file, game_dir)
            except (OSError, UnicodeError) as exc:
                self.app.log_line(f'[character grid] files not copied: could not write {path_file}: {exc}', _WARN)
                self.app.toast.error(f'{not_copied}: the game directory could not be stored.')
                return
            self._copy_files(game_dir, patched)

        def cancelled():
            self.app.log_line('[character grid] files not copied: the game directory prompt was cancelled', _WARN)
            self.app.toast.warn(f'{not_copied}: the game directory prompt was cancelled.')
        files_dir, sys_dir = gui_grid.GAME_SUBDIRS
        lines = ['Enter the path to your unpacked copy of Mario Super Sluggers (US): its main folder, the one '
                 f'holding {files_dir} and {sys_dir}. It is kept in {gui_grid.GAME_DIR_FILE_REL} for the next '
                 'time.']
        if stored:
            lines.append(f'The stored one cannot be used: {gui_grid.game_dir_problem(stored)}')
        self.app.ask_text('Game directory', '\n\n'.join(lines), answered, cancelled, initial=stored)

    def _copy_files(self, game_dir, patched=False):
        def done(code, _output):
            if code == 0:
                self.app.log_line(f'[character grid] game files copied to {game_dir}.', _OK)
                self.app.toast.ok('Game patched and the files copied to the game directory.' if patched
                                  else 'Game files copied to the game directory.')
            else:
                self.app.log_line('[character grid] copying the files to the game directory failed: see the log '
                                  'above.', _WARN)
                self.app.toast.error('Game patched, but copying the files to the game directory failed.' if patched
                                     else 'Copying the files to the game directory failed.')
            self.set_busy(self.app.busy)
        self._run_or_say([gui_grid.DEPLOY_ARGS], 'Copying the files to the game directory...', done)

    def _run(self, steps, work, on_done):
        """Run a chain of the tab's own with the spinner and ``work`` as the status line until it is done (the
        re-read after a writing chain keeps the spinner going). False when another command runs."""
        def done(code, output):
            self.work = None
            self._show_status()
            on_done(code, output)
        self.work = work
        if not self.app.run_chain(steps, on_done=done):
            self.work = None
            return False
        self._show_status()
        return True

    def _run_or_say(self, steps, work, on_done):
        """``_run`` for the callers that have nothing to undo when the command cannot start: the refusal goes to
        the log and a toast (the console is hidden by default, so a click would seem to do nothing)."""
        if self._run(steps, work, on_done):
            return True
        text = (f'Not started ({work.rstrip(".")}): another command or a sound write is running. Try again when it '
                'is done.')
        self.app.log_line(f'[character grid] {text}', _WARN)
        self.app.toast.warn(text)
        self.set_busy(self.app.busy)
        return False

    def _prune_staged_icons(self):
        """Delete normalised portraits (``_gui/slot/staged_icons``) that no pending edit uses any more."""
        gui_grid.prune_staged_icons(os.path.join(self.app.root_dir, gui_grid.STAGED_ICONS_REL),
                                    gui_grid.staged_icon_files(self.pending))
