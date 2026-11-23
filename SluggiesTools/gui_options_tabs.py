"""The CPU vs CPU, Extras and Options tabs: built and handled by the character grid tab, because their
toggles are staged like grid edits or run as commands of the grid (``grid_game_options`` has the labels,
status lines, edits and commands).

A mixin of ``gui_character_grid.CharacterGridTab``: it has no state of its own and uses the tab's
(``self.app``, ``self.pending``, ``self.loader``, ...).
"""

import dearpygui.dearpygui as dpg

import gui_grid
from gui_grid_layout import (
    _DIM, _ERROR, _OK, _PENDING, _WARN,
)


class OptionsTabsMixin:
    """``CharacterGridTab``'s part for the three option tabs: ``build_cpu_vs_cpu``, ``build_extras``,
    ``build_options``, their status refresh (``_show_cpu_vs_cpu``) and the handlers of their buttons."""

    def build_cpu_vs_cpu(self):
        """The Gameplay tab's "CPU vs CPU" inner tab: the buttons stage game option edits ("Patch Game" writes
        them). Its state comes from the grid's read, so it is built here (inside the Gameplay tab's tab bar)."""
        with dpg.tab(label='CPU vs CPU', tag='cpu_vs_cpu_tab'):
            dpg.add_text('The buttons stage the change; "Patch Game" writes every pending edit.', color=_DIM)
            dpg.add_button(label='Discard pending CPU vs CPU edits', tag='cpu_vs_cpu_discard', enabled=False,
                           callback=lambda: self._on_cpu_vs_cpu_discard())
            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                dpg.add_button(label=gui_grid.CPU_VS_CPU_ENABLE, tag='grid_cpu_vs_cpu',
                               callback=lambda: self._on_cpu_vs_cpu())
                with dpg.tooltip('grid_cpu_vs_cpu'):
                    dpg.add_text('Stage CPU vs CPU on (hold Minus on controller starting a match). Turning it off '
                                 'also turns off its management and the close play odds.', wrap=420)
                dpg.add_text('', tag='grid_cpu_vs_cpu_status')
            with dpg.group(horizontal=True):
                dpg.add_button(label=gui_grid.CPU_MANAGEMENT_ENABLE, tag='grid_cpu_management',
                               callback=lambda: self._on_cpu_management())
                with dpg.tooltip('grid_cpu_management'):
                    dpg.add_text('In a CPU vs CPU match, controller 1 manages the fielding team (pause to make '
                                 'defensive changes). Needs CPU vs CPU on (or staged on) first.', wrap=420)
                dpg.add_text('', tag='grid_cpu_management_status')
            dpg.add_spacer(height=4)
            dpg.add_text('Close play odds (CPU vs CPU)')
            with dpg.group(horizontal=True):
                dpg.add_slider_int(tag='grid_close_play_odds', width=160, min_value=0, max_value=100,
                                   default_value=gui_grid.CLOSE_PLAY_GECKO, format='%d%%',
                                   callback=lambda _s, value: self._on_close_play_slider(value))
                with dpg.tooltip('grid_close_play_odds'):
                    dpg.add_text('Port of "CPU Closeplay Overhaul" [C^2]. In CPU vs CPU matches the close plays get '
                                 'these odds (steps of 10%; 50% is the Gecko code); a player with the baserunning '
                                 'ability always gets 100%. Human matches keep the stock close plays.', wrap=420)
                    dpg.add_text('Needs CPU vs CPU on (or staged on) first. It adds code to main.dol, like CPU vs '
                                 'CPU itself.', wrap=420)
                dpg.add_button(label=gui_grid.CLOSE_PLAY_STAGE, tag='grid_close_play_stage',
                               callback=lambda: self._on_close_play(True))
                dpg.add_button(label=gui_grid.CLOSE_PLAY_DISABLE, tag='grid_close_play_disable',
                               callback=lambda: self._on_close_play(False))
                dpg.add_text('', tag='grid_close_play_status')
            dpg.add_spacer(height=4)
            dpg.add_text('CPU fixes (every match; no added code, so Gecko codes keep working)')
            for key, _label, tip in gui_grid.CPU_FIXES:
                with dpg.group(horizontal=True):
                    dpg.add_button(label=gui_grid.cpu_fix_button(None, None, key)[0], tag=f'grid_fix_{key}',
                                   width=260, callback=lambda _s, _a, u: self._on_cpu_fix(u), user_data=key)
                    with dpg.tooltip(f'grid_fix_{key}'):
                        dpg.add_text(tip, wrap=420)
                    dpg.add_text('', tag=f'grid_fix_{key}_status')
            # the tool's CPU vs CPU patch and Dolphin's CPU vs CPU Gecko codes must not both be active
            dpg.add_text('Disable all CPU vs CPU Gecko codes in Dolphin now!', tag='grid_cpu_gecko_warning',
                         color=_WARN, show=False)
            with dpg.group(horizontal=True):
                dpg.add_loading_indicator(tag='cpu_vs_cpu_spinner', style=1, radius=1.6, show=False,
                                          color=(90, 200, 120, 255), secondary_color=(60, 120, 80, 255))
                dpg.add_text('', tag='cpu_vs_cpu_status')

    def build_extras(self):
        """The Gameplay tab's "Extras" inner tab: community Gecko codes as toggles that stage game option edits, like
        the CPU vs CPU tab's CPU fixes ("Patch Game" writes them)."""
        with dpg.tab(label='Extras', tag='extras_tab'):
            dpg.add_text('The buttons stage the change; "Patch Game" writes every pending edit.', color=_DIM)
            dpg.add_spacer(height=4)
            for title, extras in (('Characters and chemistry', gui_grid.EXTRAS), ('99-0 tools', gui_grid.EXTRAS_99_0),
                                  ('Miscellaneous', gui_grid.EXTRAS_MISC)):
                if extras is not gui_grid.EXTRAS:
                    dpg.add_spacer(height=4)
                dpg.add_text(title)
                for key, _label, tip in extras:
                    with dpg.group(horizontal=True):
                        dpg.add_button(label=gui_grid.cpu_fix_button(None, None, key)[0], tag=f'grid_fix_{key}',
                                       width=300, callback=lambda _s, _a, u: self._on_cpu_fix(u), user_data=key)
                        with dpg.tooltip(f'grid_fix_{key}'):
                            dpg.add_text(tip, wrap=420)
                        dpg.add_text('', tag=f'grid_fix_{key}_status')
            with dpg.group(horizontal=True):
                dpg.add_input_int(tag='grid_mercy_rule_runs', width=120, min_value=1, max_value=99,
                                  min_clamped=True, max_clamped=True, default_value=gui_grid.MERCY_RULE_STOCK,
                                  callback=lambda: self._show_cpu_vs_cpu())
                with dpg.tooltip('grid_mercy_rule_runs'):
                    dpg.add_text('Port of "Mercy Rule Modifier" [ZPL]: with the mercy rule on in the match settings, '
                                 'a match ends once a team leads by this many runs (1-99; stock 10). Plain patch: no '
                                 'added code, so Gecko codes keep working.', wrap=420)
                dpg.add_button(label=gui_grid.MERCY_RULE_STAGE, tag='grid_mercy_rule_stage',
                               callback=lambda: self._on_mercy_rule(True))
                dpg.add_button(label=gui_grid.MERCY_RULE_RESET, tag='grid_mercy_rule_reset',
                               callback=lambda: self._on_mercy_rule(False))
                dpg.add_text('', tag='grid_mercy_rule_status')
            dpg.add_text('Disable the matching Gecko codes in Dolphin now!', tag='grid_extras_gecko_warning',
                         color=_WARN, show=False)
            with dpg.group(horizontal=True):
                dpg.add_loading_indicator(tag='extras_spinner', style=1, radius=1.6, show=False,
                                          color=(90, 200, 120, 255), secondary_color=(60, 120, 80, 255))
                dpg.add_text('', tag='extras_status')

    def build_options(self):
        """The Options tab: game options written into main.dol right away (player memory). Its state comes from the
        grid's read, so the tab lives here."""
        with dpg.tab(label='Options', tag='options_tab'):
            dpg.add_text('Pending player changes. Please commit or discard them before changing the game options.',
                         tag='options_pending', color=_ERROR, wrap=600, show=False)
            dpg.add_text('Player memory')
            dpg.add_checkbox(label='I have manually increased the MEM2 limit in Dolphin to 128 MB',
                             tag='grid_big_memory', callback=lambda _s, value: self._on_big_memory(value))
            with dpg.tooltip('grid_big_memory'):
                dpg.add_text('Unlocks player memory levels of +512 KB to +4 MB. They need Dolphin\'s "Emulated '
                             'Memory Size Override" (Config > Advanced) with MEM2 at 128 MB: the game heap then '
                             'grows by 64 MB.', wrap=420)
                dpg.add_text('The game checks the memory size when it starts. Without the override (or on a real '
                             'Wii) it keeps the stock heaps and uses +48 KB player memory, so models bigger than '
                             'that will crash there.', wrap=420)
            with dpg.group(horizontal=True):
                dpg.add_combo(gui_grid.player_heap_choices(), tag='grid_player_heap', width=260,
                              default_value=gui_grid.PLAYER_HEAP_STOCK_LABEL,
                              callback=lambda _s, value: self._on_player_heap(value))
                with dpg.tooltip('grid_player_heap'):
                    dpg.add_text('Each player on the field gets a fixed memory area for its High model, Low model '
                                 'and bat or glove (850 KB in the stock game). A player whose files do not fit '
                                 'crashes the game when it is loaded onto the field. Raising it lets bigger custom '
                                 'models play.', wrap=420)
                    dpg.add_text('The extra memory comes from the game heap that the stadium, effects and '
                                 'animations share (13 players x the increase). +48 KB fits a cautious estimate of '
                                 'the stock console memory, +64 KB goes slightly past it, +128 KB well past it: '
                                 'that may crash in a heavy scene, for example when a run is scored.', wrap=420)
                    dpg.add_text('Levels marked (experimental) have only been tried at Wario City; they may '
                                 'cause problems elsewhere.', wrap=420)
                dpg.add_text('', tag='grid_player_heap_status')
            dpg.add_spacer(height=4)
            dpg.add_separator()
            dpg.add_spacer(height=4)
            dpg.add_text('Team autopicker')
            with dpg.group(horizontal=True):
                dpg.add_button(label=gui_grid.AUTOPICKER_ENABLE, tag='options_autopicker', width=260,
                               callback=lambda: self._on_autopicker())
                with dpg.tooltip('options_autopicker'):
                    dpg.add_text('Writes the Gecko code that the MSS-AutoTeam autopicker asks for straight into '
                                 'main.dol, so it no longer has to be enabled in Dolphin. It keeps the game from '
                                 'resetting the lineup the autopicker writes; without the autopicker the lineup '
                                 'screens keep their stock defaults.', wrap=420)
                    dpg.add_text('Three instructions are changed in place: it adds no code area, so Gecko codes '
                                 'made for the stock game keep working.', wrap=420)
                dpg.add_text('', tag='options_autopicker_status')
            dpg.add_spacer(height=4)
            dpg.add_separator()
            dpg.add_spacer(height=4)
            dpg.add_text('Unused character strike zones')
            with dpg.group(horizontal=True):
                dpg.add_button(label=gui_grid.STRIKE_ZONE_ENABLE, tag='options_strike_zone', width=260,
                               callback=lambda: self._on_strike_zone())
                with dpg.tooltip('options_strike_zone'):
                    dpg.add_text('Writes the Gecko code "Fix Unused Character Strikezone Heights" into main.dol: the '
                                 'strike zone heights of the unused Toad (0.8), Pianta (0.95), Kritter (1.15) and '
                                 'Koopa (0.9).', wrap=420)
                    dpg.add_text('It follows the roster: new characters that play with one of their stats get the '
                                 'fixed height too, and it stays on through roster changes. It adds no code area, '
                                 'so Gecko codes made for the stock game keep working.', wrap=420)
                dpg.add_text('', tag='options_strike_zone_status')
            dpg.add_spacer(height=4)
            dpg.add_separator()
            dpg.add_spacer(height=4)
            dpg.add_text('Cheats')
            dpg.add_text(gui_grid.CHEATS_NOTE, color=_DIM, wrap=600)
            for key, _label, tip in gui_grid.CHEATS:
                with dpg.group(horizontal=True):
                    dpg.add_button(label=gui_grid.cheat_button(None, key)[0], tag=f'options_cheat_{key}', width=260,
                                   callback=lambda _s, _a, k: self._on_cheat(k), user_data=key)
                    with dpg.tooltip(f'options_cheat_{key}'):
                        dpg.add_text(tip, wrap=420)
                    dpg.add_text('', tag=f'options_cheat_{key}_status')
            dpg.add_spacer(height=4)
            dpg.add_separator()
            dpg.add_spacer(height=4)
            dpg.add_button(label='Copy to Dolphin now', tag='options_copy_now', callback=lambda: self._on_copy_now())
            with dpg.tooltip('options_copy_now'):
                dpg.add_text('Copy output files back to the game folder and overwrite.', wrap=420)
            with dpg.group(horizontal=True):
                dpg.add_loading_indicator(tag='options_spinner', style=1, radius=1.6, show=False,
                                          color=(90, 200, 120, 255), secondary_color=(60, 120, 80, 255))
                dpg.add_text('', tag='options_status')
            dpg.add_spacer(height=4)
            dpg.add_separator()
            dpg.add_spacer(height=4)
            dpg.add_text('UI Options')
            dpg.add_checkbox(label=gui_grid.SKIP_CONFIRM_LABEL, tag='options_skip_confirm',
                             default_value=self.app.skip_confirmations(),
                             callback=lambda _s, value: self._on_skip_confirm(value))
            with dpg.tooltip('options_skip_confirm'):
                dpg.add_text('Do not show the confirmation popups (after staging checks and dry runs, before '
                             'writing, discarding or closing): each one goes on as if OK was clicked. Popups that '
                             'report a refusal or an error still show; skipped ones leave their title and warnings '
                             'in the log.', wrap=420)

    def _on_skip_confirm(self, value):
        try:
            self.app.settings.set(gui_grid.SKIP_CONFIRM_SETTING, bool(value))
        except OSError as exc:
            self.app.log_line(f'[options] could not save the setting: {exc}', _WARN)

    def _options_locked(self):
        """The Options tab writes main.dol right away, so it waits until pending player edits are committed or
        discarded (Patch Game would otherwise mix them)."""
        return self._locked() or bool(len(self.pending))

    def _cpu_vs_cpu_locked(self):
        """The CPU vs CPU buttons stage edits: not while the tab is busy or a roster pack load is pending."""
        return self._locked() or self.confirm is not None or self.pending.pack is not None

    def _show_cpu_vs_cpu(self):
        """The CPU vs CPU tab's labels and buttons: the game's state with the pending game option edits on top
        (the management button is usable only while CPU vs CPU is on or staged on)."""
        if not dpg.does_item_exist('grid_cpu_vs_cpu'):
            return
        state, pending = self.loader.state, self.pending
        staged = gui_grid.staged_game_options(pending)
        for tag, key, status in (('grid_cpu_vs_cpu', 'cpu_vs_cpu', gui_grid.cpu_vs_cpu_status),
                                 ('grid_cpu_management', 'cpu_management', gui_grid.cpu_management_status)):
            text, enabled = status(state, pending)
            dpg.set_value(f'{tag}_status', text)
            dpg.configure_item(f'{tag}_status', color=_PENDING if key in staged else _OK if enabled
                               else _WARN if enabled is False else _DIM)
        locked = self._cpu_vs_cpu_locked()
        dpg.configure_item('grid_cpu_vs_cpu', label=gui_grid.cpu_vs_cpu_button(state, pending)[0], enabled=not locked)
        label, _enable, usable = gui_grid.cpu_management_button(state, pending)
        dpg.configure_item('grid_cpu_management', label=label, enabled=usable and not locked)
        level = gui_grid.close_play_level(state, pending)
        if level is not None and level != self.close_play_shown:    # follow the game / staged level, not each redraw
            dpg.set_value('grid_close_play_odds', level)
        self.close_play_shown = level
        text, enabled = gui_grid.close_play_status(state, pending)
        dpg.set_value('grid_close_play_status', text)
        dpg.configure_item('grid_close_play_status', color=_PENDING if '(pending' in text else _OK if enabled
                           else _WARN if enabled is False else _DIM)
        stage, disable = gui_grid.close_play_buttons(state, pending,
                                                     gui_grid.close_play_snap(dpg.get_value('grid_close_play_odds')))
        dpg.configure_item('grid_close_play_stage', enabled=stage and not locked)
        dpg.configure_item('grid_close_play_disable', enabled=disable and not locked)
        for key, _label, _tip in gui_grid.CPU_FIXES + gui_grid.EXTRA_TOGGLES:
            if not dpg.does_item_exist(f'grid_fix_{key}'):
                continue
            dpg.configure_item(f'grid_fix_{key}', label=gui_grid.cpu_fix_button(state, pending, key)[0],
                               enabled=not locked)
            text, enabled = gui_grid.cpu_fix_status(state, pending, key)
            dpg.set_value(f'grid_fix_{key}_status', text)
            dpg.configure_item(f'grid_fix_{key}_status', color=_PENDING if key in staged else _OK if enabled
                               else _DIM)
        dpg.configure_item('cpu_vs_cpu_discard', enabled=bool(staged) and not locked)
        dpg.configure_item('grid_cpu_gecko_warning', show=gui_grid.cpu_gecko_warning(state, pending))
        if dpg.does_item_exist('grid_extras_gecko_warning'):
            dpg.configure_item('grid_extras_gecko_warning', show=gui_grid.extras_gecko_warning(state, pending))
        if dpg.does_item_exist('grid_mercy_rule_runs'):
            level = gui_grid.mercy_rule_level(state, pending)
            if level != self.mercy_rule_shown:      # follow the game / staged lead, not each redraw
                dpg.set_value('grid_mercy_rule_runs', level)
            self.mercy_rule_shown = level
            text, enabled = gui_grid.mercy_rule_status(state, pending)
            dpg.set_value('grid_mercy_rule_status', text)
            dpg.configure_item('grid_mercy_rule_status', color=_PENDING if '(pending' in text else _OK if enabled
                               else _DIM)
            stage, reset = gui_grid.mercy_rule_buttons(
                state, pending, gui_grid.mercy_rule_clamp(dpg.get_value('grid_mercy_rule_runs')))
            dpg.configure_item('grid_mercy_rule_stage', enabled=stage and not locked)
            dpg.configure_item('grid_mercy_rule_reset', enabled=reset and not locked)

    # ------------------------------------------------------------------ deploy
    def _deploying(self):
        return dpg.does_item_exist('grid_deploy') and bool(dpg.get_value('grid_deploy'))

    def _on_deploy_toggle(self, value):
        """The checkbox is remembered in the GUI settings; the Patch Game label follows it."""
        try:
            self.app.settings.set(gui_grid.DEPLOY_SETTING, bool(value))
        except OSError as exc:
            self.app.log_line(f'[character grid] could not save the setting: {exc}', _WARN)
        self.set_busy(self.app.busy)

    def _on_copy_now(self):
        """Options tab: copy the current 3_Output_Dat game files to the game directory without patching first."""
        if not self._options_locked():
            self._deploy()

    def _on_cpu_vs_cpu(self):
        """Stage CPU vs CPU on or off ("Patch Game" writes it); turning it off stages management off too."""
        if self._cpu_vs_cpu_locked():
            return
        enable = gui_grid.cpu_vs_cpu_button(self.loader.state, self.pending)[1]
        self.offer_game_options(gui_grid.cpu_vs_cpu_edits(enable, self.loader.state, self.pending),
                                'CPU vs CPU ' + ('on' if enable else 'off'))

    def _on_cpu_management(self):
        """Stage CPU vs CPU management on or off, like _on_cpu_vs_cpu; only while CPU vs CPU is on or staged on."""
        if self._cpu_vs_cpu_locked():
            return
        _label, enable, usable = gui_grid.cpu_management_button(self.loader.state, self.pending)
        if usable:
            self.offer_game_options([gui_grid.cpu_management_edit(enable)],
                                    'CPU vs CPU management ' + ('on' if enable else 'off'))

    def _on_close_play_slider(self, value):
        """The slider snaps to the levels (steps of 10%); nothing is staged until Stage is clicked."""
        dpg.set_value('grid_close_play_odds', gui_grid.close_play_snap(value))
        self._show_cpu_vs_cpu()

    def _on_close_play(self, enable):
        """Stage the slider's close play odds, or turn them off; only while CPU vs CPU is on or staged on."""
        if self._cpu_vs_cpu_locked():
            return
        percent = gui_grid.close_play_snap(dpg.get_value('grid_close_play_odds'))
        stage, disable = gui_grid.close_play_buttons(self.loader.state, self.pending, percent)
        edit = gui_grid.close_play_edit(self.loader.state, self.pending, percent if enable else None)
        if edit is not None and (stage if enable else disable):
            self.offer_game_options([edit], f'close play odds {percent}%' if enable else 'close play odds off')

    def _on_mercy_rule(self, enable):
        """Stage the input's mercy rule lead, or the stock one (10 runs)."""
        if self._cpu_vs_cpu_locked():
            return
        runs = gui_grid.mercy_rule_clamp(dpg.get_value('grid_mercy_rule_runs'))
        stage, reset = gui_grid.mercy_rule_buttons(self.loader.state, self.pending, runs)
        edit = gui_grid.mercy_rule_edit(self.loader.state, self.pending, runs if enable else None)
        if edit is not None and (stage if enable else reset):
            self.offer_game_options([edit], f'mercy rule {runs} runs' if enable else 'stock mercy rule')

    def _on_cpu_fix(self, key):
        """Stage a CPU fix (or an extra) on or off, like _on_cpu_vs_cpu; on with its companion (999 runs)."""
        if self._cpu_vs_cpu_locked():
            return
        label, _enable = gui_grid.cpu_fix_button(self.loader.state, self.pending, key)
        self.offer_game_options(gui_grid.cpu_fix_edits(self.loader.state, self.pending, key), label.lower())

    def _on_cpu_vs_cpu_discard(self):
        if self._cpu_vs_cpu_locked():
            return
        self.pending.discard_op(gui_grid.GAME_OPTION)
        self._pending_changed()
        self.app.log_line('[character grid] the pending CPU vs CPU edits were discarded.', _DIM)

    def offer_game_options(self, edits, what):
        """The staging check of game option edits (the CPU vs CPU tab): the pending edits plus these. Staged
        straight away when the check passes (an edit back to what the game has drops the pending one of that
        option); else its dialog says why."""
        if self._cpu_vs_cpu_locked():
            return False
        try:
            gui_grid.write_edits(self.edits_path, self.pending.staging(edits))
        except OSError as exc:
            self.app.log_line(f'[character grid] could not write {self.edits_path}: {exc}', _WARN)
            return False
        self.action = 'game_option'

        def show(code, output):
            state = self.nav.state or self.loader.state
            plan = gui_grid.load_plan(self.plan_path)
            dialog = gui_grid.game_option_dialog(state, plan, code, output)
            if dialog.can_apply:
                self._stage(plan)
                self.app.log_line(f'[character grid] staged: {what}; "Patch Game" writes it.', _PENDING)
                return
            self._dialog(dialog, self._end_action)
        if not self._run([gui_grid.preview_command(self.edits_path)], 'Checking the game options...', show):
            self._end_action()
            return False
        return True

    def _player_heap_items(self):
        dpg.configure_item('grid_player_heap', items=gui_grid.player_heap_choices(dpg.get_value('grid_big_memory')))

    def _on_big_memory(self, checked):
        """The 128 MB checkbox only unlocks the combo's big levels; with one of them on it stays ticked."""
        if self._options_locked():
            dpg.set_value('grid_big_memory', not checked)
            return
        if not checked and gui_grid.player_heap_is_big(gui_grid.player_heap_level(self.loader.state)):
            dpg.set_value('grid_big_memory', True)
            self.app.log_line('[options] a 128 MB player memory level is on: choose a stock-memory level first.',
                              _WARN)
        self._player_heap_items()

    def _on_player_heap(self, choice):
        """Set the player memory level in main.dol right away, like the CPU vs CPU buttons."""
        current = gui_grid.player_heap_choice(gui_grid.player_heap_level(self.loader.state))
        command = gui_grid.player_heap_command(choice)
        if self._options_locked() or command is None or choice == current:
            dpg.set_value('grid_player_heap', current)
            return
        stock = choice == gui_grid.PLAYER_HEAP_STOCK_LABEL
        self._run_game_option(command, not stock, 'raised player memory' if stock else f'player memory {choice}')

    def _on_autopicker(self):
        """Turn the autopicker patch on or off in main.dol right away, like the player memory combo."""
        if self._options_locked() or self.loader.state is None:
            return
        _label, enable = gui_grid.autopicker_button(self.loader.state)
        self._run_game_option(gui_grid.autopicker_command(enable), enable, 'the autopicker patch')

    def _on_strike_zone(self):
        """Turn the unused characters' strike zone fix on or off in main.dol right away, like the autopicker patch."""
        if self._options_locked() or self.loader.state is None:
            return
        _label, enable = gui_grid.strike_zone_button(self.loader.state)
        self._run_game_option(gui_grid.strike_zone_command(enable), enable, 'the strike zone fix')

    def _on_cheat(self, key):
        """Turn a cheat on or off in main.dol right away, like the autopicker patch."""
        if self._options_locked() or self.loader.state is None:
            return
        label, enable = gui_grid.cheat_button(self.loader.state, key)
        self._run_game_option(gui_grid.cheat_command(key, enable), enable, label.split(' ', 1)[1])

    def _run_game_option(self, command, enable, name):
        verb = 'enabling' if enable else 'disabling'

        def done(code, _output):
            if code != 0:
                self.app.log_line(f'[character grid] {verb} {name} failed (see the log above).', _WARN)
            self.set_busy(self.app.busy)
        if not self._run_or_say([command], f'{verb.capitalize()} {name}...', done):
            self._show_status()              # the controls go back to what the game holds
