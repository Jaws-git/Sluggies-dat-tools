"""Character grid dialogs: the modal windows of the tab. ``_dialog`` draws a ``grid_dialogs.SlotDialog``
(coloured lines, the OK / Cancel row) and ``_end_action`` closes it; the others are the dialogs with inputs
of their own (icon, rename, Stats / Voice value, size) and the ones that show a finished check (confirm,
Patch Game summary, roster pack load, roster switch, discard).

A mixin of ``gui_character_grid.CharacterGridTab``.
"""

import os

import dearpygui.dearpygui as dpg
from PIL import Image

import gui_grid
import memory_guard
from gui_grid_layout import (
    BUTTON_H, CONFIRM_W, DIALOG_LINES, FONT_REL, GAP, PORTRAIT, _DIM, _ERROR, _LINE_COLORS, _OK, _PENDING,
    _WARN, _rgba, _source_thumbnail,
)


class GridDialogsMixin:
    """``CharacterGridTab``'s modal dialogs."""

    def _icon_dialog(self, cid, view, path):
        """The source image, the 48x51 result (3x, nearest) beside the slot's other view, fit and trim; each change
        redraws the result in-process. OK stages the edit (after the staging check)."""
        state = self.nav.state or self.loader.state
        preview = gui_grid.IconPreview(path)
        other = gui_grid.SIDE if view != gui_grid.SIDE else gui_grid.FRONT
        label = gui_grid.view_label(view)
        vw, vh = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        win = self.confirm = dpg.add_window(
            label=f'{label.capitalize()} portrait of {gui_grid.name_of(state, cid)} ({gui_grid.hex_id(cid)})',
            modal=True, no_collapse=True, no_saved_settings=True, autosize=True,
            pos=(max(0, (vw - CONFIRM_W) // 2), max(0, vh // 8)), on_close=lambda *_: self._end_action())
        result_tex = dpg.add_dynamic_texture(PORTRAIT[0], PORTRAIT[1], [0.0] * (PORTRAIT[0] * PORTRAIT[1] * 4),
                                             parent='grid_dialog_textures')
        with dpg.group(horizontal=True, horizontal_spacing=2 * GAP, parent=win):
            with dpg.group():
                source = _source_thumbnail(preview)
                if source is not None:
                    tex = dpg.add_static_texture(source.width, source.height, _rgba(source),
                                                 parent='grid_dialog_textures')
                    dpg.add_image(tex, width=source.width, height=source.height, border_color=(90, 90, 96, 255))
                else:
                    with dpg.drawlist(width=PORTRAIT[0], height=PORTRAIT[1]):
                        dpg.draw_rectangle((1, 1), (PORTRAIT[0] - 1, PORTRAIT[1] - 1), color=(120, 120, 120, 255))
                dpg.add_text('Your image', color=_DIM)
            with dpg.group():
                dpg.add_image(result_tex, width=PORTRAIT[0], height=PORTRAIT[1], border_color=_PENDING)
                dpg.add_text(f'New {label} (48x51)', color=_PENDING)
            with dpg.group():
                other_path = self.pending.portrait(cid, other) or gui_grid.icon_file(
                    state, self.loader.state_path, cid, other)
                other_tex = self._dialog_texture(other_path)
                if other_tex is not None:
                    dpg.add_image(other_tex, width=PORTRAIT[0], height=PORTRAIT[1], border_color=(90, 90, 96, 255))
                dpg.add_text(f'{other.capitalize()} (kept)', color=_DIM)
        dpg.add_spacer(height=GAP, parent=win)
        labels = [label for _mode, label in gui_grid.FIT_CHOICES]
        dpg.add_radio_button(labels, parent=win, horizontal=True, default_value=labels[0],
                             callback=lambda _s, label: change(fit=dict((l, m) for m, l in gui_grid.FIT_CHOICES)[label]))
        dpg.add_checkbox(label='Trim transparent border', parent=win, default_value=True,
                         callback=lambda _s, ticked: change(trim=ticked))
        messages = dpg.add_group(parent=win)
        dpg.add_spacer(height=GAP, parent=win)
        with dpg.group(horizontal=True, parent=win):
            ok = dpg.add_button(label='OK', width=110, height=BUTTON_H, callback=lambda: accept())
            dpg.bind_item_theme(ok, 'primary_theme')
            dpg.add_button(label='Cancel', width=110, height=BUTTON_H, callback=lambda: self._end_action())

        def change(fit=None, trim=None):
            if fit is not None:
                preview.fit = fit
            if trim is not None:
                preview.trim = trim
            image = preview.result()[0]
            if image is not None:
                big = image.resize(PORTRAIT, Image.Resampling.NEAREST)
            else:
                big = Image.new('RGBA', PORTRAIT, (0, 0, 0, 0))
            dpg.set_value(result_tex, _rgba(big))
            dpg.delete_item(messages, children_only=True)
            for text, kind in preview.lines():
                dpg.add_text(text, parent=messages, wrap=CONFIRM_W, color=_LINE_COLORS[kind])
            dpg.configure_item(ok, enabled=preview.ok)

        def accept():
            if not preview.ok:
                return
            folder = os.path.join(self.app.root_dir, gui_grid.STAGED_ICONS_REL)
            try:
                staged = preview.save(folder, cid, view)
            except OSError as exc:
                self.app.log_line(f'[character grid] could not write the portrait into {folder}: {exc}', _WARN)
                return
            self._end_action()
            self._start_preview(cid, None, icon=preview.edit(cid, view, staged))
        change()
        self.confirm_default = accept
        self.set_busy(self.app.busy)

    def _on_rename(self, cid):
        """The rename dialog: a text box with a live "fits / too long" line; OK runs the staging check."""
        if self._locked() or self.pending.pack is not None:      # the name's click handler has no disabled state
            return
        state = self.nav.state or self.loader.state
        font = os.path.join(self.app.root_dir, FONT_REL)
        vw, vh = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        self.action = (cid, 'rename')
        win = self.confirm = dpg.add_window(label=f'Rename {gui_grid.name_of(state, cid)} ({gui_grid.hex_id(cid)})',
                                            modal=True, no_collapse=True, no_saved_settings=True, autosize=True,
                                            pos=(max(0, (vw - CONFIRM_W) // 2), max(0, vh // 5)),
                                            on_close=lambda *_: self._end_action())
        dpg.add_text('One name is used for English, French and Spanish. It must fit the name plate '
                     '(no shrinking). Leave it blank to reset the name.', parent=win, wrap=CONFIRM_W)
        status, ok = 'grid_rename_status', 'grid_rename_ok'

        def changed(_s=None, text=None):
            text = (text if text is not None else dpg.get_value('grid_rename_text')).strip()
            problem = gui_grid.name_problem(text, font) if text else None
            dpg.set_value(status, problem or ('Resets the name' if not text else 'Fits the name plate'))
            dpg.configure_item(status, color=_ERROR if problem else _OK if text else _DIM)
            dpg.configure_item(ok, enabled=problem is None)

        dpg.add_input_text(tag='grid_rename_text', parent=win, width=CONFIRM_W - 20, hint='New name',
                           default_value=gui_grid.rename_prefill(state, cid), callback=changed)
        dpg.add_text('', tag=status, parent=win)
        dpg.add_spacer(height=GAP, parent=win)
        with dpg.group(horizontal=True, parent=win):
            dpg.add_button(label='OK', tag=ok, width=110, height=BUTTON_H,
                           callback=lambda: self._on_rename_ok(cid))
            dpg.bind_item_theme(ok, 'primary_theme')
            dpg.add_button(label='Cancel', width=110, height=BUTTON_H, callback=lambda: self._end_action())
        changed()
        self.confirm_default = lambda: dpg.get_item_configuration(ok).get('enabled') and self._on_rename_ok(cid)
        dpg.focus_item('grid_rename_text')
        self.set_busy(self.app.busy)

    def _on_value(self, op, key):
        """The Stats / Voice dialog: the current assignment and a list to pick from; OK runs the staging check.
        ``key``: the slot's ID (stats) or the square's index (voice; the edit goes on the square's head)."""
        if self._locked():
            return
        state = self.nav.state or self.loader.state
        packs = []
        if op == 'voice':
            cid = state['squares'][key]['head']
            packs = self._voice_packs()
            choices = gui_grid.voice_choices(state, key) + gui_grid.square_pack_choices(state, packs)
            title = f'Voice of the square of {gui_grid.name_of(state, cid)}'
            lines = [gui_grid.voice_text(state, key), gui_grid.voice_reach(state, key)]
            if packs:
                lines.append('A voice pack (Voices tab) is staged as the own voice of every member of the square '
                             '(each member\'s Voice... shows it); the square\'s voice stays as it is.')
        elif op == gui_grid.CHAR_VOICE:
            cid = key
            packs = self._voice_packs()
            choices = [(label, (source, pack)) for label, source, pack in gui_grid.char_voice_choices(state, cid, packs)]
            title = f'Own voice of {gui_grid.name_of(state, cid)} ({gui_grid.hex_id(cid)})'
            lines = [gui_grid.char_voice_text(state, cid),
                     'The slot speaks with the picked voice as its own, on the select screen and on the field, whatever '
                     'its square speaks with; the other members of its square keep theirs. A voice pack (Voices tab) '
                     'puts its custom clips on top of a stock voice.']
        else:
            cid = key
            choices = gui_grid.stats_choices(state, cid)
            title = f'Stats of {gui_grid.name_of(state, cid)} ({gui_grid.hex_id(cid)})'
            lines = [gui_grid.stats_text(state, cid),
                     'The slot plays with the picked stock player\'s stats: stats, pitching, fielding and chemistry. '
                     'Its model, size, voice and name stay.']
        pending = self.pending.value_edit(cid, op)
        if pending is not None:
            lines.append(gui_grid._edit_title(pending))
        if op == gui_grid.CHAR_VOICE:
            current = gui_grid.char_voice_current(state, cid, self.pending)
        else:
            current = pending.get('source') if pending is not None else None
        vw, vh = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        self.action = (cid, op)
        win = self.confirm = dpg.add_window(label=title, modal=True, no_collapse=True, no_saved_settings=True,
                                            autosize=True, pos=(max(0, (vw - CONFIRM_W) // 2), max(0, vh // 5)),
                                            on_close=lambda *_: self._end_action())
        for line in lines:
            dpg.add_text(line, parent=win, wrap=CONFIRM_W, color=_PENDING if line.startswith('Pending') else _DIM)
        labels = [label for label, _value in choices]
        dpg.add_combo(labels, tag='grid_value_choice', parent=win, width=CONFIRM_W - 20,
                      default_value=labels[gui_grid.choice_index(choices, current)])
        dpg.add_spacer(height=GAP, parent=win)

        def ok():
            label = dpg.get_value('grid_value_choice')
            source = next((value for text, value in choices if text == label), None)
            self._end_action()
            if op == 'voice' and isinstance(source, tuple) and source[0] == gui_grid.SQUARE_PACK:
                if not self._locked():
                    self._check_and_confirm(cid, gui_grid.square_pack_edits(state, key, source[1]), gui_grid.CHAR_VOICE)
            elif op == gui_grid.CHAR_VOICE:
                source, pack = source if source is not None else (None, None)
                self._start_preview(cid, None, value=(op, source, pack))
            else:
                self._start_preview(cid, None, value=(op, source))
        with dpg.group(horizontal=True, parent=win):
            button = dpg.add_button(label='OK', width=110, height=BUTTON_H, callback=lambda: ok())
            dpg.bind_item_theme(button, 'primary_theme')
            dpg.add_button(label='Cancel', width=110, height=BUTTON_H, callback=lambda: self._end_action())
        self.confirm_default = ok
        self.set_busy(self.app.busy)

    def _on_rename_ok(self, cid):
        text = dpg.get_value('grid_rename_text').strip()
        self._end_action()
        self._start_preview(cid, None, rename=text)

    def _on_size(self, cid):
        """The Set size... dialog: a factor (1 = the character's normal size; a refused one gets a red line); OK writes a one-character stat edit file (``gui_grid.size_doc``) and runs its staging check."""
        state = self.nav.state or self.loader.state
        sizes = gui_grid.size_of(state, cid)
        current = gui_grid.size_factor(sizes)
        if self._locked() or current is None:        # no sizes, or no base size to take a factor of
            return
        pending = gui_grid.pending_size(self.pending, cid)
        vw, vh = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        self.action = (cid, 'size')
        win = self.confirm = dpg.add_window(label=f'Size of {gui_grid.name_of(state, cid)} ({gui_grid.hex_id(cid)})',
                                            modal=True, no_collapse=True, no_saved_settings=True, autosize=True,
                                            pos=(max(0, (vw - CONFIRM_W) // 2), max(0, vh // 5)),
                                            on_close=lambda *_: self._end_action())
        dpg.add_text('One factor for the whole model (skeleton and mesh), in game and on the select screens: 1 is '
                     'the character\'s normal size, 1.2 makes it 20% bigger, 0.8 20% smaller. Its hitbox and '
                     'fielding reach stay as they are.', parent=win, wrap=CONFIRM_W)
        dpg.add_text(gui_grid.size_text(state, cid), parent=win, color=_DIM)
        if pending is not None:
            dpg.add_text(f'Pending: {pending:g}x', parent=win, color=_PENDING)
        status, ok = 'grid_size_status', 'grid_size_ok'

        def changed(_s=None, text=None):
            text = text if text is not None else dpg.get_value('grid_size_text')
            _value, problem = gui_grid.parse_size(text)
            dpg.set_value(status, problem or '')         # only a refusal shows
            dpg.configure_item(status, color=_ERROR)
            dpg.configure_item(ok, enabled=problem is None)

        shown = pending if pending is not None else current
        dpg.add_input_text(tag='grid_size_text', parent=win, width=160, hint='Factor, e.g. 1.2',
                           default_value=f'{shown:.4g}', callback=changed)
        dpg.add_text('', tag=status, parent=win)
        dpg.add_spacer(height=GAP, parent=win)
        with dpg.group(horizontal=True, parent=win):
            dpg.add_button(label='OK', tag=ok, width=110, height=BUTTON_H, callback=lambda: self._on_size_ok(cid))
            dpg.bind_item_theme(ok, 'primary_theme')
            dpg.add_button(label='Normal size', width=110, height=BUTTON_H,
                           callback=lambda: (dpg.set_value('grid_size_text', '1'), changed(text='1')))
            dpg.add_button(label='Cancel', width=110, height=BUTTON_H, callback=lambda: self._end_action())
        changed()
        self.confirm_default = lambda: dpg.get_item_configuration(ok).get('enabled') and self._on_size_ok(cid)
        dpg.focus_item('grid_size_text')
        self.set_busy(self.app.busy)

    def _on_size_ok(self, cid):
        value, problem = gui_grid.parse_size(dpg.get_value('grid_size_text'))
        state = self.nav.state or self.loader.state
        self._end_action()
        if problem is not None:
            return
        try:
            path = gui_grid.write_size_file(self.app.root_dir, gui_grid.size_doc(state, cid, value))
        except OSError as exc:
            self.app.log_line(f'[character grid] could not write the size edit file: {exc}', _WARN)
            return
        self.offer_stat_edits(path)

    def _voice_packs(self):
        """``[(name, base voice, clip count)]`` of the custom voice packs in _gui/Sound_Packs (for the Voice dialog)."""
        try:
            from Sound import sound_packs
            packs, _problems = sound_packs.list_packs(self.app.root_dir)
        except Exception as exc:                 # a damaged pack folder must not break the dialog
            self.app.log_line(f'[character grid] voice packs could not be listed: {exc}', _WARN)
            return []
        return [(p.name, p.voice, p.count) for p in packs]

    def _show_confirm(self, cid, sluggie, code, output, kind='patch', view=None, gear_file=None, moment=None,
                      anm=None):
        state = self.nav.state or self.loader.state
        plan = gui_grid.load_plan(self.plan_path)
        if kind == gui_grid.PACKAGE:
            path, staged = self._package
            dialog = gui_grid.with_memory_warning(
                gui_grid.package_dialog(state, cid, path, plan, code, output, staged.skipped),
                memory_guard.read_details(self.app.root_dir), only=gui_grid.new_targets(plan))
            self._dialog(dialog, lambda: self._stage_package(plan), ok_label='Stage')
            return
        dialog = gui_grid.with_memory_warning(
            gui_grid.slot_dialog(state, cid, sluggie is not None, plan, code, output, self.pending, kind=kind,
                                 view=view, gear_file=gear_file, moment=moment, anm=anm),
            memory_guard.read_details(self.app.root_dir), only=gui_grid.new_targets(plan))
        if (kind in gui_grid.VALUE_KINDS + ('equip_clear',) and dialog.can_apply and code == 0 and plan is not None
                and any(int(s['target'], 16) == cid for s in plan['edits']) and not dialog.offer_stat_reset
                and not dialog.memory_over):
            self._stage(plan)                  # a valid rename / stats / voice pick needs no second confirmation
            return
        extra = (('Stage + clear stat edits', lambda: self._stage_with_stat_reset(cid, plan))
                 if dialog.offer_stat_reset else None)
        self._dialog(dialog, lambda: self._stage(plan), ok_label='Stage', extra=extra)

    def _show_switch(self, path, code, output):
        plan = gui_grid.switch_plan(self.switch_plan_path)
        dialog = gui_grid.with_gecko_warning(gui_grid.switch_dialog(plan, code, output, path), self._gecko_break())
        accept = dialog.gecko_break

        def save():
            self._end_action()
            self._pick_pack(save=True, after=lambda: self._switch(path, accept))
        self._dialog(dialog, lambda: self._switch(path, accept), ok_label='Inject', extra=('Save Configuration', save))

    def confirm_discard(self, then, title='Discard the pending edits?', reason=''):
        """Run ``then``; with pending edits ask first (Discard drops them). Presets, closing, Discard all."""
        if not len(self.pending):
            then()
            return
        if self.confirm is not None:
            return                                   # another dialog is up
        count = len(self.pending)
        lines = ([(reason, gui_grid.TEXT)] if reason else []) + [
            (f'{count} pending edit{"s" if count != 1 else ""} (nothing written for them yet):', gui_grid.TEXT)]
        state = self.loader.state
        for cid, text in self.pending.titles():
            who = '' if cid is None else f'{gui_grid.edit_who(state, int(cid, 16))}: '
            lines.append((f'  {who}{text}', gui_grid.WARN))
        lines.append(('Discard drops them.', gui_grid.TEXT))

        def discard():
            self._end_action()
            self.pending.clear()
            self._pending_changed()
            then()
        self.action = 'ask'
        self._dialog(gui_grid.SlotDialog(title, lines, True), discard, ok_label='Discard')

    def _show_summary(self, code, output):
        state = self.nav.state or self.loader.state
        dialog = gui_grid.with_gecko_warning(gui_grid.summary_dialog(state, gui_grid.load_plan(self.plan_path), code,
                                                                     output), self._gecko_break())
        dialog = gui_grid.with_memory_warning(dialog, memory_guard.read_details(self.app.root_dir))
        accept, accept_memory = dialog.gecko_break, dialog.memory_over
        self._dialog(dialog, lambda: self._patch_game(accept, accept_memory), ok_label=self._patch_ok_label())

    def _show_load(self, path, code, output, writing=False):
        """The load dialog: Stage adds the load to the pending list; ``writing`` (Patch Game's summary after a
        fresh dry run): Patch Game writes it."""
        state = self.loader.state
        plan = gui_grid.load_pack_plan(self.pack_plan_path)
        if writing and code == 0 and plan is not None and plan.get('ok') and not plan.get('commands'):
            self.pending.clear()                 # the game holds the pack's roster already: nothing stays pending
            self._pending_changed()
        breaks = writing and self._gecko_break()        # staging writes nothing: Patch Game's summary warns
        memory = memory_guard.read_details(self.app.root_dir)

        def build(only=True):
            return gui_grid.with_memory_warning(gui_grid.with_gecko_warning(
                gui_grid.load_dialog(state, plan, code, output, path, only, writing), breaks), memory)
        dialog = build()
        self._dialog(dialog, (lambda: self._load_pack(path, plan, dialog.gecko_break, dialog.memory_over)) if writing
                     else (lambda: self._stage_pack(path, plan)),
                     ok_label=self._patch_ok_label() if writing else 'Stage', rebuild=build,
                     toggle='Only differing slots')

    def _dialog(self, dialog, on_ok, ok_label='OK', rebuild=None, toggle=None, extra=None):
        """A modal dialog: its lines, then ``ok_label`` / Cancel (``dialog.can_apply``) or Close. ``toggle``: a
        checkbox label (ticked at first); changing it redraws the lines from ``rebuild(ticked)``. Long line lists
        scroll. ``extra``: (label, callback) for a middle button. With "Skip confirmation popups" on
        (``gui_grid.dialog_skippable``) it is not shown: OK runs (Close for an info dialog), the log gets the title
        and its warnings. A Gecko code compatibility break (``dialog.gecko_break``) is always shown, OK as a red
        Confirm that Enter does not press; a player memory warning (``dialog.memory_over``) likewise, OK as a red
        "... anyway" (``gui_grid.anyway_label``)."""
        if self.app.skip_confirmations() and gui_grid.dialog_skippable(dialog):
            self.app.log_line(f'[character grid] {dialog.title} ({"OK" if dialog.can_apply else "popup"} skipped)',
                              _DIM)
            for text, kind in dialog.lines:
                if kind in (gui_grid.WARN, gui_grid.ERROR):
                    self.app.log_line(f'[character grid]   {text.strip()}', _LINE_COLORS[kind])
            (on_ok if dialog.can_apply else self._end_action)()
            return
        vw, vh = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        self.confirm = dpg.add_window(label=dialog.title, modal=True, no_collapse=True, no_saved_settings=True,
                                      autosize=True, pos=(max(0, (vw - CONFIRM_W) // 2), max(0, vh // 8)),
                                      on_close=lambda *_: self._end_action())
        if toggle:
            dpg.add_checkbox(label=toggle, default_value=True, parent=self.confirm,
                             callback=lambda _s, ticked: fill(rebuild(ticked)))
        if toggle or len(dialog.lines) > DIALOG_LINES:
            body = dpg.add_child_window(parent=self.confirm, width=CONFIRM_W + 24, height=min(vh // 2, 420))
        else:
            body = dpg.add_group(parent=self.confirm)

        def fill(content):
            dpg.delete_item(body, children_only=True)
            for text, kind in content.lines:
                dpg.add_text(text, parent=body, wrap=CONFIRM_W, color=_LINE_COLORS[kind])
        fill(dialog)
        dpg.add_spacer(height=GAP, parent=self.confirm)
        with dpg.group(horizontal=True, parent=self.confirm):
            if dialog.can_apply:
                label = ('Confirm' if dialog.gecko_break else gui_grid.anyway_label(ok_label) if dialog.memory_over
                         else ok_label)
                ok = dpg.add_button(label=label, width=110 if not dialog.memory_over else 130, height=BUTTON_H,
                                    callback=lambda: on_ok())
                dpg.bind_item_theme(ok, 'danger_theme' if dialog.danger else 'primary_theme')
                if extra:
                    dpg.add_button(label=extra[0], width=170, height=BUTTON_H, callback=lambda: extra[1]())
                dpg.add_button(label='Cancel', width=110, height=BUTTON_H, callback=lambda: self._end_action())
            else:
                dpg.add_button(label='Close', width=110, height=BUTTON_H, callback=lambda: self._end_action())
        self.confirm_default = on_ok if dialog.can_apply and not dialog.danger else self._end_action
        self.set_busy(self.app.busy)

    def _end_action(self):
        """Close the file dialog / the open dialog; the levels respond to clicks again."""
        if self.confirm is not None and dpg.does_item_exist(self.confirm):
            dpg.delete_item(self.confirm)
        if dpg.does_item_exist('grid_dialog_textures'):
            dpg.delete_item('grid_dialog_textures', children_only=True)
        self.confirm, self.confirm_default, self.action = None, None, None
        self.stat_offer = None
        if self._package is not None:          # a package closed without Stage: the voice pack it made goes again
            from Roster import slot_zip
            slot_zip.remove_pack(self.app.root_dir, self._package[1].created_pack)
            self._package = None
        self.set_busy(self.app.busy)
