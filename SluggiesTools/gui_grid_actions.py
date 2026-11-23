"""Character grid actions that start from a click or a dropped file: picking ``.sluggie``, ``.slgmodel``,
animation, equipment and portrait files, the right-click menu (copy, paste, export, character packages)
and the copied character.

Each ends in an edit handed to the staging check (``gui_grid_staging``). A mixin of
``gui_character_grid.CharacterGridTab``.
"""

import os

import dearpygui.dearpygui as dpg

import gui_grid
from gui_grid_layout import (
    ANIM_DIR_SETTING, FONT_REL, IMAGE_DIR_SETTING, PACKAGE_DIR_SETTING, SLGMODEL_DIR_SETTING,
    SLUGGIE_DIR_SETTING, _COPIED, _OK, _WARN,
)


class GridActionsMixin:
    """``CharacterGridTab``'s file picks, drops and context menu."""

    def _on_select(self, cid, low=False):
        """Select .sluggie... / Select lowpoly (L_) .sluggie... (``low``: a Low model pick)."""
        if self._locked():
            return
        self.action = (cid, 'low' if low else None)
        self.set_busy(False)
        self._pick_sluggie('Select a lowpoly (L_) .sluggie for this slot' if low else 'Select a .sluggie for this slot')

    def _pick_sluggie(self, title):
        """Open the .sluggie picker in the folder the last pick came from (else the models folder)."""
        if not self.app.pick_files('grid_sluggie_dialog', title,
                                   [('Sluggie files', '*.sluggie'), ('Character files', '*.slgmodel')],
                                   self._on_sluggie_paths, on_cancel=self._end_action, remember=SLUGGIE_DIR_SETTING):
            self._end_action()

    def _on_sluggie_paths(self, paths):
        cid = self.action[0] if self.action else None
        equip = self.action[2] if self.action and len(self.action) == 3 and self.action[1] == 'equip' else None
        path = next((p for p in paths if p and p.lower().endswith(('.sluggie', gui_grid.SLGMODEL_EXTENSION))
                     and os.path.isfile(p)), None)
        if cid is None or path is None:
            self.app.log_line(f'[character grid] no .sluggie file chosen: {paths[0]}', _WARN)
            self._end_action()
            return
        low = self.action[1:] == ('low',)
        self.action = None
        if path.lower().endswith(gui_grid.SLGMODEL_EXTENSION) and equip is None:
            self._start_preview(cid, None, slgmodel=path)
            return
        self._start_preview(cid, path, equip=None if equip is None else (equip, path), low=low)

    # ------------------------------------------------------------------ character files (.slgmodel)
    def _on_import_slgmodel(self, cid):
        """Import .slgmodel...: a Sluggers Characters patcher character file for the slot."""
        if self._locked() or self.pending.pack is not None:
            return
        self.action = (cid, gui_grid.SLGMODEL)
        self.set_busy(False)
        if not self.app.pick_files('grid_slgmodel_dialog', 'Select a character file (.slgmodel) for this slot',
                                   gui_grid.SLGMODEL_FILTERS, self._on_slgmodel_paths, on_cancel=self._end_action,
                                   remember=SLGMODEL_DIR_SETTING):
            self._end_action()

    def _on_slgmodel_paths(self, paths):
        cid = self.action[0] if isinstance(self.action, tuple) and self.action[1:] == (gui_grid.SLGMODEL,) else None
        path = next((p for p in paths if p and p.lower().endswith(gui_grid.SLGMODEL_EXTENSION) and os.path.isfile(p)),
                    None)
        if cid is None or path is None:
            self.app.log_line(f'[character grid] no .slgmodel file chosen: {paths[0]}', _WARN)
            self._end_action()
            return
        self.action = None
        self._start_preview(cid, None, slgmodel=path)

    def on_files_dropped(self, paths, _point):
        """Files dropped onto the window (``file_drop``) while a slot is open on this tab: a ``.slgmodel`` or a
        ``.sluggie`` is staged for that slot as its button would. False when the drop is not for this tab."""
        tab_bar = dpg.get_value('main_tab_bar')
        if tab_bar not in ('grid_tab', dpg.get_alias_id('grid_tab')):
            return False
        cid = self.nav.slot
        if cid is None or self.nav.level != gui_grid.SLOT:
            self.app.log_line('[character grid] open a slot first, then drop the file onto the window', _WARN)
            return True
        if self._locked() or self.pending.pack is not None:
            return True
        path = next((p for p in paths if p.lower().endswith((gui_grid.SLGMODEL_EXTENSION, '.sluggie'))
                     and os.path.isfile(p)), None)
        if path is None:
            self.app.log_line(f'[character grid] {os.path.basename(paths[0])} is not a .slgmodel or .sluggie file',
                              _WARN)
            return True
        if len(paths) > 1:
            self.app.log_line(f'[character grid] {len(paths)} files dropped; staging {os.path.basename(path)} only.')
        self.action = None
        if path.lower().endswith(gui_grid.SLGMODEL_EXTENSION):
            self._start_preview(cid, None, slgmodel=path)
        else:
            self._start_preview(cid, path)
        return True

    # ------------------------------------------------------------------ animations
    def _on_anim(self, cid):
        """Replace animation...: pick one or more .anm banks; one staging check finds each one's bank (6-14) and
        checks it, and one confirm dialog stages them all."""
        if self._locked() or self.pending.pack is not None:
            return
        self.action = (cid, gui_grid.ANIM)
        self.set_busy(False)
        if not self.app.pick_files('grid_anim_dialog', 'Select animation banks (.anm) for this slot',
                                   gui_grid.ANIM_FILTERS, self._on_anim_paths, on_cancel=self._end_action,
                                   multi=True, remember=ANIM_DIR_SETTING):
            self._end_action()

    def _on_anim_paths(self, paths):
        cid = self.action[0] if isinstance(self.action, tuple) and self.action[1:] == (gui_grid.ANIM,) else None
        chosen = list(dict.fromkeys(os.path.abspath(p) for p in paths if p and os.path.isfile(p)))
        if cid is None or not chosen:
            self.app.log_line(f'[character grid] no animation file chosen: {paths[0]}', _WARN)
            self._end_action()
            return
        self.action = None
        self._start_preview(cid, None, anim=chosen)

    # ------------------------------------------------------------------ equipment
    def _on_equip_select(self, cid, file):
        """Select... on an equipment tile: pick a bat / glove .sluggie for slot file ``file``."""
        if self._locked() or self.pending.pack is not None:
            return
        self.action = (cid, 'equip', file)
        self.set_busy(False)
        label = gui_grid.EQUIP_LABELS[file].lower()
        self._pick_sluggie(f'Select a {label} .sluggie for this slot')

    def _on_equip_reset(self, cid, file):
        """Reset on an equipment tile: drop its pending edit, or stage the return to the baseline."""
        if self._locked() or self.pending.pack is not None:
            return
        if self.pending.equip_edit(cid, file) is not None:
            self.pending.drop_equip(cid, file)
            self._pending_changed()
            return
        self._start_preview(cid, None, equip=(file, None))

    # ------------------------------------------------------------------ portraits
    def _on_portrait(self, cid, view):
        """A click on a slot-level portrait: pick an image, then the icon dialog."""
        if self._locked() or not gui_grid.can_replace_portrait(cid) or self.pending.pack is not None:
            return
        self.action = (cid, 'icon', view)
        self.set_busy(False)
        if not self.app.pick_files('grid_image_dialog', f'Select an image for the {gui_grid.view_label(view)} portrait',
                                   gui_grid.IMAGE_FILTERS, self._on_image_paths, on_cancel=self._end_action,
                                   remember=IMAGE_DIR_SETTING):
            self._end_action()

    def _on_image_paths(self, paths):
        action = self.action if isinstance(self.action, tuple) and len(self.action) == 3 else None
        path = next((p for p in paths if p), None)
        if action is None or path is None:
            self._end_action()
            return
        cid, _kind, view = action
        self._icon_dialog(cid, view, path)

    # ------------------------------------------------------------------ copy / paste
    def _add_menu(self, button, members):
        """Give a portrait button the right-click menu (``_on_context``) for ``members`` (its square's, or one)."""
        if len(self._menu_targets) >= self._menu_prune_at:
            # Deleted buttons are dropped now and then, not on every button added (item ids are never reused).
            self._menu_targets = {b: m for b, m in self._menu_targets.items() if dpg.does_item_exist(b)}
            self._menu_prune_at = 2 * len(self._menu_targets) + 128
        self._menu_targets[button] = list(members)
        dpg.bind_item_handler_registry(button, 'grid_context_handlers')

    def _on_context(self, _sender, app_data):
        """A right click on a portrait button: fill the shared menu window and open it at the mouse."""
        button = app_data[1] if isinstance(app_data, (list, tuple)) else app_data
        members = self._menu_targets.get(button)
        state = self.loader.state
        if members is None or state is None or self.confirm is not None:
            return
        dpg.delete_item('grid_context', children_only=True)
        actions = {gui_grid.COPY_LABEL: lambda: self._on_copy(members[0]),
                   gui_grid.PASTE_LABEL: lambda: self._on_paste(members[0]),
                   gui_grid.EXPORT_LABEL: lambda: self._on_export(members[0]),
                   gui_grid.EXPORT_PACKAGE_LABEL: lambda: self._on_export_package(members[0])}
        entries = gui_grid.context_menu(state, members, self.copied, self.pending.pack is not None, self._locked())
        sizes = [dpg.get_text_size(label) for label, _enabled, _why in entries]
        width = max((s[0] for s in sizes if s), default=0)
        width = int(width) + 20 if width else 0              # + FramePadding x2: one width for every button
        for label, enabled, why in entries:
            item = dpg.add_button(label=label, parent='grid_context', enabled=enabled, width=width,
                                  callback=(lambda _s, _a, act: self._menu_choice(act)),
                                  user_data=actions.get(label))
            if why:
                with dpg.tooltip(item):
                    dpg.add_text(why, wrap=360)
        dpg.configure_item('grid_context', show=True, pos=dpg.get_mouse_pos(local=False))

    def _menu_choice(self, action):
        dpg.configure_item('grid_context', show=False)
        if action is not None:
            action()

    def _on_copy(self, cid):
        if self._locked():
            return
        self.copied = cid
        self.app.log_line(f'[character grid] copied {gui_grid.name_of(self.loader.state, cid)} ({gui_grid.hex_id(cid)}): '
                          'right-click another slot and choose Paste.', _COPIED)
        self._show_status()
        self._pending_changed()

    def _on_paste(self, cid):
        if self._locked() or self.copied is None or self.copied == cid or self.pending.pack is not None:
            return
        self._start_preview(cid, None, copy_from=self.copied)

    def _on_export(self, cid):
        """Export as .sluggie: with pending edits on the slot ask first (they are not in the game yet)."""
        if self._locked():
            return
        if self.pending.has(cid):
            self.action = 'ask'
            self._dialog(gui_grid.export_pending_dialog(self.loader.state, cid, self.pending),
                         lambda: (self._end_action(), self._export(cid)), ok_label='Export without them')
            return
        self._export(cid)

    def _export(self, cid):
        who = f'{gui_grid.name_of(self.loader.state, cid)} ({gui_grid.hex_id(cid)})'

        def done(code, _output):
            result = gui_grid.export_result(self.app.root_dir)
            toast = gui_grid.export_toast(code, result)
            if toast is None:
                self.app.log_line(f'[character grid] {who} was not exported: see the log above.', _WARN)
                self.action = 'ask'          # holds the levels: a click on its Close must not close one too
                self._dialog(gui_grid.export_failed_dialog(self.loader.state, cid, result), self._end_action)
                return
            self.app.log_line(f'[character grid] {who} exported to {result["folder"]}', _OK)
            self._end_action()
            self.app.toast.ok(toast)
        self._run_or_say([gui_grid.export_command(cid)], f'Exporting {who}...', done)

    # ------------------------------------------------------------------ character packages (.sluggiezip)
    def _on_export_package(self, cid):
        """Export to .sluggiezip: with pending edits on the slot ask first (they are not in the game yet), then the
        Save As dialog."""
        if self._locked():
            return
        if self.pending.has(cid):
            self.action = 'ask'
            self._dialog(gui_grid.export_pending_dialog(self.loader.state, cid, self.pending),
                         lambda: (self._end_action(), self._pick_package_save(cid)), ok_label='Export without them')
            return
        self._pick_package_save(cid)

    def _pick_package_save(self, cid):
        self.action = (cid, gui_grid.PACKAGE)
        self.set_busy(self.app.busy)
        if not self.app.pick_files('grid_package_save_dialog', 'Export the character as',
                                   gui_grid.PACKAGE_FILTERS, lambda paths: self._on_package_save_paths(cid, paths),
                                   on_cancel=self._end_action, save=True,
                                   default_ext=gui_grid.PACKAGE_EXTENSION.lstrip('.'), remember=PACKAGE_DIR_SETTING,
                                   default_name=gui_grid.package_name(self.loader.state, cid)):
            self._end_action()

    def _on_package_save_paths(self, cid, paths):
        path = paths[0] if paths else None
        self._end_action()
        if not path:
            return
        if not path.lower().endswith(gui_grid.PACKAGE_EXTENSION):
            path += gui_grid.PACKAGE_EXTENSION
        who = f'{gui_grid.name_of(self.loader.state, cid)} ({gui_grid.hex_id(cid)})'

        def done(code, _output):
            result = gui_grid.package_export_result(self.app.root_dir)
            written = (result or {}).get('path') if code == 0 else None
            if not written or not os.path.isfile(written):
                self.app.log_line(f'[character grid] {who} was not exported: see the log above.', _WARN)
                self.action = 'ask'
                self._dialog(gui_grid.package_export_failed_dialog(self.loader.state, cid, result), self._end_action)
                return
            self.app.log_line(f'[character grid] {who} exported to {written}', _OK)
            self.app.toast.ok(f'{os.path.basename(written)} has been created')
        self._run_or_say([gui_grid.package_export_command(cid, path)], f'Exporting {who}...', done)

    def _on_load_package(self, cid):
        """Import .sluggiezip package...: pick a package; every aspect it holds is staged for the slot at once."""
        if self._locked() or self.pending.pack is not None:
            return
        self.action = (cid, gui_grid.PACKAGE)
        self.set_busy(False)
        if not self.app.pick_files('grid_package_load_dialog', 'Select a character package (.sluggiezip) for this slot',
                                   gui_grid.PACKAGE_FILTERS, self._on_package_paths, on_cancel=self._end_action,
                                   remember=PACKAGE_DIR_SETTING):
            self._end_action()

    def _on_package_paths(self, paths):
        cid = self.action[0] if isinstance(self.action, tuple) and self.action[1:] == (gui_grid.PACKAGE,) else None
        path = next((p for p in paths if p and os.path.isfile(p)), None)
        if cid is None or path is None:
            self.app.log_line(f'[character grid] no character package chosen: {(paths or [None])[0] or "(none)"}', _WARN)
            self._end_action()
            return
        self.action = None
        from Roster import slot_zip
        root = self.app.root_dir
        slot_zip.prune(os.path.join(root, slot_zip.STAGED_REL), gui_grid.staged_package_files(self.pending))
        font = os.path.join(root, FONT_REL)
        try:
            staged = slot_zip.stage(path, cid, self.nav.state or self.loader.state, root,
                                    name_problem=lambda text: gui_grid.name_problem(text, font),
                                    can_portrait=gui_grid.can_replace_portrait)
        except Exception as exc:  # noqa: BLE001 - a package from someone else: whatever is wrong with it is said
            if not isinstance(exc, (slot_zip.PackageError, OSError)):
                exc = slot_zip.PackageError(f'the package is damaged ({type(exc).__name__}: {exc})')
            self.app.log_line(f'[character grid] {os.path.basename(path)} was not loaded: {exc}', _WARN)
            self.action = 'ask'
            self._dialog(gui_grid.SlotDialog('Package not loaded', [(str(exc), gui_grid.ERROR),
                                                                    ('Nothing was staged.', gui_grid.TEXT)]),
                         self._end_action)
            return
        self._package = (path, staged)
        if not staged.edits:
            self.action = 'ask'
            self._dialog(gui_grid.SlotDialog(f'{os.path.basename(path)}: nothing to stage',
                                             [(f'Skipped: {text}', gui_grid.WARN) for text in staged.skipped]),
                         self._end_action)
            return
        self._check_and_confirm(cid, staged.edits, gui_grid.PACKAGE)

    def package_stat_files(self) -> list[str]:
        """The stat edit file of the package being checked (the Stat Editor tab keeps it until it is staged)."""
        if self._package is None:
            return []
        return [e['file'] for e in self._package[1].edits if e['op'] == gui_grid.STAT_EDITS]

    def forget_copy(self):
        """Drop the copied character (a tab switch does this)."""
        if self.copied is None:
            return
        self.copied = None
        if self._locked():
            # A staging check or its dialog is up: only redraw. _pending_changed prunes the staged files down to
            # the pending list, and the edit being checked (its portrait, its clip) is not in that list yet.
            self._refresh_markers()
            return
        self._show_status()
        self._pending_changed()
