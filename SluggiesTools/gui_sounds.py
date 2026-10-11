"""The GUI's "Sounds" tab: the game's voice packs and the user's custom ones.

Left, the pack list: **Game voice packs** (one per stock voice: the 12 voice moments of a species as
``1_Input/main.dol`` and ``1_Input/sound_NA/MY2.brsar`` hold them, ``Sound/sound_packs.stock_packs``) and
**Custom packs** (the folders of ``Sound_Packs/``, ``sound_packs.list_packs``). Right, the opened pack: one row
per voice moment (role, the current clip and its length, **Play**, **Replace...**, **Reset**).

* A **game pack's** Replace stages a ``species_clip`` edit on the character grid (through
  ``CharacterGridTab.stage_edit``: the staging check and confirm dialog, then "Patch Game" writes it): the WAV is
  prepared and encoded here, on a worker thread (``Sound/clips``), into ``_gui/slot/staged_clips/<sha>.rwav``, so
  the planner only copies bytes. Reset drops the pending edit, or stages the stock clip back when the game holds a
  replaced one. Pending moments show orange; moments the game already replaced show blue.
* A **custom pack** is a library: Replace writes the encoded clip into the pack folder (``sound_packs.set_clip``),
  nothing is staged; the grid's slot-level **Voice...** assigns a pack to a slot. **Stage for its slots** re-stages
  the pack on every slot that speaks with it now (after a clip changed). New pack..., Rename..., Delete, Export...
  (one ``.sluggiesvoice`` file) and Import... manage the library.

Playback uses ``winsound`` (Windows); elsewhere the Play buttons are disabled. The archive is read once on a worker
thread; Refresh reads it again.
"""

import os
import threading

import dearpygui.dearpygui as dpg

import gui_grid

_DIM = (200, 200, 200, 255)
_FAINT = (140, 140, 140, 255)
_WARN = (255, 210, 90, 255)
_OK = (120, 220, 140, 255)
_ERROR = (255, 120, 110, 255)
_PENDING = (255, 170, 70, 255)
_CHANGED = (110, 170, 255, 255)
_WINDOWS = os.name == 'nt'
LIST_W = 340
BUTTON_H = 28
WAV_FILTERS = [('WAV files', '*.wav;*.wave'), ('Encoded clips', '*.rwav'), ('All files', '*.*')]
PACK_FILTERS = [('Voice packs', '*.sluggiesvoice'), ('All files', '*.*')]
CLIP_DIR_SETTING = 'sounds_last_clip_dir'      # the GUI settings key: the folder the last Replace... WAV came from


def _sound_modules():
    from Sound import brsar, clips, sound_packs
    return brsar, clips, sound_packs


class SoundsTab:
    def __init__(self, app):
        self.app = app                       # gui.SluggiesGui
        self.grid = app.grid_tab             # pending edits, state, staging
        self.loading = False
        self.archive = None                  # Sound.brsar.Brsar of 1_Input/sound_NA/MY2.brsar, once read
        self.stock = []                      # [sound_packs.StockPack]
        self.custom = []                     # [sound_packs.CustomPack]
        self.problems = []                   # unreadable pack folders
        self.error = None                    # why the archive could not be read
        self.output_size = None              # bytes of 3_Output_Dat/sound_NA/MY2.brsar, or None
        self.selected = None                 # ('stock', species) or ('custom', name), or None
        self.working = False                 # a clip is being encoded
        self._playing = None                 # the temp WAV file winsound plays from
        self.grid.on_pending_changed.append(self._pending_changed)
        self.grid.on_state_read.append(self._state_read)

    # ------------------------------------------------------------------ build
    def build(self):
        with dpg.tab(label='Sounds', tag='sounds_tab'):
            with dpg.group(horizontal=True):
                dpg.add_button(label='Refresh', tag='sounds_refresh', callback=lambda: self.refresh())
                dpg.add_button(label='New pack...', tag='sounds_new', callback=lambda: self._on_new_pack())
                dpg.add_button(label='Import pack...', tag='sounds_import', callback=lambda: self._on_import())
                dpg.add_spacer(width=12)
                dpg.add_loading_indicator(tag='sounds_spinner', style=1, radius=1.6, show=False,
                                          color=(90, 200, 120, 255), secondary_color=(60, 120, 80, 255))
                dpg.add_text('', tag='sounds_status')
            dpg.add_text('', tag='sounds_archive', color=_DIM, wrap=1200)
            dpg.add_separator()
            with dpg.group(horizontal=True):
                with dpg.child_window(tag='sounds_list', width=LIST_W, border=True):
                    pass
                with dpg.child_window(tag='sounds_detail', border=True):
                    dpg.add_text('Pick a voice pack on the left.', color=_FAINT)
        with dpg.theme(tag='sounds_selected_theme'):
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (60, 110, 80, 255))
        # the built-in file dialogs, used when the Windows one is off (gui.pick_files); their result goes to the
        # callback the last pick stored
        start = self.app.models_dir if os.path.isdir(self.app.models_dir) else self.app.root_dir
        for tag, extensions, save in (('sounds_wav_dialog', ('.wav', '.rwav'), False),
                                      ('sounds_pack_save_dialog', ('.sluggiesvoice',), True),
                                      ('sounds_pack_load_dialog', ('.sluggiesvoice',), False)):
            with dpg.file_dialog(directory_selector=False, show=False, modal=True, tag=tag, width=760, height=460,
                                 callback=self._on_fallback_chosen, cancel_callback=lambda *_: None,
                                 default_path=start, default_filename='pack' if save else ''):
                for ext in extensions:
                    dpg.add_file_extension(ext, color=(120, 180, 255, 255))
                dpg.add_file_extension('.*')
        self._fallback = None                # on_files of the pick the built-in dialog serves

    def _on_fallback_chosen(self, _sender, app_data):
        app_data = app_data or {}
        paths = list(app_data.get('selections', {}).values()) or [app_data.get('file_path_name')]
        callback, self._fallback = self._fallback, None
        if callback is not None:
            callback([p for p in paths if p])

    def _pick(self, tag, title, filters, on_files, **kwargs):
        self._fallback = on_files
        if not self.app.pick_files(tag, title, filters, on_files, on_cancel=lambda: None, **kwargs):
            self._fallback = None

    def on_open(self):
        """The tab was opened: read the archive the first time."""
        if self.archive is None and not self.loading and self.error is None:
            self.refresh()

    # ------------------------------------------------------------------ reading
    def refresh(self):
        if self.loading:
            return
        self.loading = True
        dpg.configure_item('sounds_refresh', enabled=False)
        dpg.configure_item('sounds_spinner', show=True)
        self._status('Reading the sound archive...', _DIM)
        root = self.app.root_dir

        def work():
            result = {'archive': None, 'stock': [], 'custom': [], 'problems': [], 'error': None, 'output_size': None}
            try:
                brsar, _clips, sound_packs = _sound_modules()
                result['custom'], result['problems'] = sound_packs.list_packs(root)
                archive_path = os.path.join(root, '1_Input', 'sound_NA', 'MY2.brsar')
                dol_path = os.path.join(root, '1_Input', 'main.dol')
                output_path = os.path.join(root, '3_Output_Dat', 'sound_NA', 'MY2.brsar')
                if os.path.isfile(output_path):
                    result['output_size'] = os.path.getsize(output_path)
                if not os.path.isfile(archive_path):
                    result['error'] = ('1_Input/sound_NA/MY2.brsar is missing: copy the game\'s files/sound_NA folder '
                                       '(MY2.brsar and stream/) into 1_Input/sound_NA.')
                elif not os.path.isfile(dol_path):
                    result['error'] = '1_Input/main.dol is missing: the voice tables are read from it.'
                else:
                    from Dol import dolfile
                    with open(dol_path, 'rb') as handle:
                        image = dolfile.DolImage(handle.read())
                    archive = brsar.Brsar.from_file(archive_path)
                    result['stock'] = sound_packs.stock_packs(image, archive)
                    result['archive'] = archive
            except Exception as exc:
                result['error'] = f'{type(exc).__name__}: {exc}'
            self.app.output_queue.put(('call', self._read_done, result))
        threading.Thread(target=work, daemon=True).start()

    def _read_done(self, result):
        self.loading = False
        dpg.configure_item('sounds_refresh', enabled=True)
        dpg.configure_item('sounds_spinner', show=False)
        self.archive, self.stock, self.custom = result['archive'], result['stock'], result['custom']
        self.problems, self.error, self.output_size = result['problems'], result['error'], result['output_size']
        if self.error:
            self._status(self.error, _ERROR)
        else:
            self._status(f'{len(self.stock)} game voice packs, {len(self.custom)} custom pack'
                         f'{"s" if len(self.custom) != 1 else ""}.', _OK)
        for problem in self.problems:
            self.app.log_line(f'[sounds] voice pack not readable: {problem}', _WARN)
        self._archive_line()
        if self.selected is not None and self.selected[0] == 'custom' and not any(
                p.name == self.selected[1] for p in self.custom):
            self.selected = None
        self._draw_list()
        self._draw_detail()

    def _archive_line(self):
        parts = []
        path = os.path.join(self.app.root_dir, '1_Input', 'sound_NA', 'MY2.brsar')
        if os.path.isfile(path):
            parts.append(f'Input archive: 1_Input/sound_NA/MY2.brsar ({os.path.getsize(path) / 2**20:.1f} MB)')
        if self.output_size is not None:
            parts.append(f'output archive: 3_Output_Dat/sound_NA/MY2.brsar ({self.output_size / 2**20:.1f} MB)')
        parts.append('A voice pack is one voice\'s 12 moments. Game packs: replacing a clip changes it for every '
                     'character who speaks with that voice (staged, "Patch Game" writes it). Custom packs: a library '
                     'of your own clips; the character grid\'s slot-level Voice... gives one to a character.')
        dpg.set_value('sounds_archive', '. '.join(parts) + ('.' if parts else ''))

    def _status(self, text, color):
        dpg.set_value('sounds_status', text)
        dpg.configure_item('sounds_status', color=color)

    def _state_read(self):
        self._draw_list()
        self._draw_detail()

    def _pending_changed(self):
        if dpg.does_item_exist('sounds_list'):
            self._draw_list()
            self._draw_detail()

    # ------------------------------------------------------------------ the list
    def _state(self):
        return self.grid.loader.state

    def _name(self, cid):
        state = self._state()
        return gui_grid.known_name(state, cid) if state else gui_grid.hex_id(cid)

    def _square_of_head(self, head):
        """The read state's square of the stock species whose base character is ``head`` (None: not read)."""
        state = self._state()
        if not state:
            return None
        return next((sq for sq in state['squares'] if sq['kind'] == 'stock' and sq['head'] == head), None)

    def _pack_users(self, name):
        """The slots that speak with custom pack ``name`` now (state) and the pending edits that assign it."""
        state = self._state()
        now = [c['id'] for c in (state or {}).get('characters') or [] if c.get('char_pack') == name]
        return now, self.grid.pending.char_voice_users(name)

    def _draw_list(self):
        if not dpg.does_item_exist('sounds_list'):
            return
        dpg.delete_item('sounds_list', children_only=True)
        parent = 'sounds_list'
        dpg.add_text('Game voice packs', parent=parent, color=_DIM)
        if not self.stock and self.error:
            dpg.add_text('(not read: see the status line)', parent=parent, color=_FAINT, wrap=LIST_W - 20)
        for pack in self.stock:
            square = self._square_of_head(pack.head)
            replaced = len((square or {}).get('clips') or {})
            pending = len(self.grid.pending.species_clip_edits(pack.head))
            label = f'{self._name(pack.head)}  ({pack.family_wave_bytes // 1024} KB)'
            marks = ([f'{replaced} replaced'] if replaced else []) + ([f'{pending} pending'] if pending else [])
            if marks:
                label += '  [' + ', '.join(marks) + ']'
            self._list_button(label, ('stock', pack.species), _PENDING if pending else _CHANGED if replaced else None)
        dpg.add_spacer(height=8, parent=parent)
        dpg.add_text('Custom packs (Sound_Packs/)', parent=parent, color=_DIM)
        if not self.custom:
            dpg.add_text('none yet: "New pack..." creates one', parent=parent, color=_FAINT, wrap=LIST_W - 20)
        for pack in self.custom:
            now, pending = self._pack_users(pack.name)
            label = f'{pack.name}  ({pack.count} of 12 clips)'
            marks = ([f'{len(now)} slot{"s" if len(now) != 1 else ""}'] if now else []) + (
                [f'{len(pending)} pending'] if pending else [])
            if marks:
                label += '  [' + ', '.join(marks) + ']'
            self._list_button(label, ('custom', pack.name), _PENDING if pending else None)

    def _list_button(self, label, key, color):
        button = dpg.add_button(label=label, width=LIST_W - 20, parent='sounds_list',
                                callback=lambda _s, _a, k: self._select(k), user_data=key)
        if key == self.selected:
            dpg.bind_item_theme(button, 'sounds_selected_theme')
        if color is not None:
            with dpg.theme() as theme:
                with dpg.theme_component(dpg.mvButton):
                    dpg.add_theme_color(dpg.mvThemeCol_Text, color)
            dpg.bind_item_theme(button, theme)

    def _select(self, key):
        self.selected = key
        self._draw_list()
        self._draw_detail()

    # ------------------------------------------------------------------ the detail
    def _stock_pack(self, species):
        return next((p for p in self.stock if p.species == species), None)

    def _custom_pack(self, name):
        return next((p for p in self.custom if p.name == name), None)

    def _draw_detail(self):
        if not dpg.does_item_exist('sounds_detail'):
            return
        dpg.delete_item('sounds_detail', children_only=True)
        parent = 'sounds_detail'
        if self.selected is None:
            dpg.add_text('Pick a voice pack on the left.', parent=parent, color=_FAINT)
            return
        kind, key = self.selected
        pack = self._stock_pack(key) if kind == 'stock' else self._custom_pack(key)
        if pack is None:
            dpg.add_text('This pack is gone; Refresh the list.', parent=parent, color=_WARN)
            return
        if kind == 'stock':
            self._draw_stock(pack, parent)
        else:
            self._draw_custom(pack, parent)

    def _row_header(self, parent):
        with dpg.table(parent=parent, header_row=True, borders_innerH=True, resizable=False,
                       policy=dpg.mvTable_SizingStretchProp) as table:
            dpg.add_table_column(label='Moment', init_width_or_weight=0.9)
            dpg.add_table_column(label='Role', init_width_or_weight=1.6)
            dpg.add_table_column(label='Clip', init_width_or_weight=2.4)
            dpg.add_table_column(label='', init_width_or_weight=1.9)
        return table

    def _draw_stock(self, pack, parent):
        _brsar, _clips, sound_packs = _sound_modules()
        head = pack.head
        square = self._square_of_head(head)
        locked = self.grid.pending.pack is not None or self.working
        dpg.add_text(f'{self._name(head)}\'s voice (game voice pack, species 0x{pack.species:02X}, sounds '
                     f'{pack.prefix}_v*)', parent=parent)
        who = ', '.join(self._name(m) for m in (square or {}).get('members') or []) if square else ''
        dpg.add_text(f'Voice bank: file {pack.family_file}, {pack.family_wave_bytes:,} bytes of wave data (vanilla). '
                     + (f'Spoken by: {who}.' if who else 'Open the Character grid tab to see who speaks with it.'),
                     parent=parent, color=_DIM, wrap=900)
        dpg.add_text('Replace... stages a new clip for everyone who speaks with this voice; a character\'s own voice '
                     '(slot-level Voice...) that copies this voice takes the replaced clips too.',
                     parent=parent, color=_FAINT, wrap=900)
        if locked and self.grid.pending.pack is not None:
            dpg.add_text('A roster pack load is pending: press "Patch Game" (or Discard it) first.', parent=parent,
                         color=_WARN, wrap=900)
        table = self._row_header(parent)
        replaced = (square or {}).get('clips') or {}
        for clip in pack.clips:
            moment = clip.moment
            edit = self.grid.pending.species_clip_edit(head, moment)
            with dpg.table_row(parent=table):
                dpg.add_text(moment)
                dpg.add_text(sound_packs.ROLE_OF[moment], color=_DIM)
                if edit is not None:
                    text = gui_grid._edit_title(edit).removeprefix('Pending: voice pack clip ')
                    dpg.add_text(f'Pending: {text}', color=_PENDING, wrap=0)
                elif moment in replaced:
                    dpg.add_text(f'replaced in the game (stock: {clip.name}, {clip.seconds:.2f} s)', color=_CHANGED)
                else:
                    dpg.add_text(f'{clip.name}  ({clip.seconds:.2f} s, {clip.wave_bytes:,} B)')
                with dpg.group(horizontal=True, horizontal_spacing=4):
                    play = dpg.add_button(label='Play', width=54, height=BUTTON_H, enabled=_WINDOWS and self.archive is not None,
                                          callback=lambda _s, _a, u: self._play_stock(u), user_data=clip.sound_id)
                    if edit is not None and edit.get('file'):
                        dpg.add_button(label='Play new', width=74, height=BUTTON_H, enabled=_WINDOWS,
                                       callback=lambda _s, _a, u: self._play_file(u), user_data=edit['file'])
                    replace = dpg.add_button(label='Replace...', width=84, height=BUTTON_H, enabled=not locked,
                                             callback=lambda _s, _a, u: self._on_replace_stock(*u), user_data=(head, moment))
                    can_reset = edit is not None or moment in replaced
                    dpg.add_button(label='Reset', width=54, height=BUTTON_H, enabled=not locked and can_reset,
                                   callback=lambda _s, _a, u: self._on_reset_stock(*u), user_data=(head, moment))
                    with dpg.tooltip(replace):
                        dpg.add_text('Pick a WAV (any format; made mono 32 kHz, cut at 2.5 s) or an encoded .rwav. '
                                     'It is staged on the character grid; "Patch Game" writes it.', wrap=360)
                    with dpg.tooltip(play):
                        dpg.add_text('Play the stock clip' if _WINDOWS else 'Playback needs Windows')

    def _draw_custom(self, pack, parent):
        _brsar, _clips, sound_packs = _sound_modules()
        now, pending = self._pack_users(pack.name)
        base = f'{self._name(pack.voice)}\'s voice' if pack.voice is not None else 'the slot\'s own species voice'
        dpg.add_text(f'Custom pack: {pack.name}', parent=parent)
        dpg.add_text(f'{pack.count} of 12 moments filled; the rest take {base}. Folder: '
                     f'{os.path.relpath(pack.folder, self.app.root_dir)}. Adds {sound_packs.pack_wave_bytes(pack):,} '
                     'bytes of wave data to the voice bank of every species it is used in.', parent=parent,
                     color=_DIM, wrap=900)
        users = ', '.join(self._name(c) for c in now) or 'no slot yet'
        line = f'Spoken by: {users}'
        if pending:
            line += f'; pending for: {", ".join(self._name(c) for c in pending)}'
        dpg.add_text(line + '. Assign it on the Character grid tab: open a slot, Voice..., pick the pack.',
                     parent=parent, color=_DIM, wrap=900)
        with dpg.group(horizontal=True, parent=parent):
            state = self._state()
            heads = sorted({sq['head'] for sq in (state or {}).get('squares') or [] if sq['kind'] == 'stock'})
            choices = ['(the slot\'s own species voice)'] + [self._name(h) for h in heads]
            current = choices[0] if pack.voice is None or pack.voice not in heads else self._name(pack.voice)
            dpg.add_text('Base voice:', color=_DIM)
            dpg.add_combo(choices, default_value=current, width=260, enabled=bool(heads),
                          callback=lambda _s, value: self._on_base_voice(pack, heads, choices, value))
            with dpg.tooltip(dpg.last_item()):
                dpg.add_text('The stock voice the pack\'s unfilled moments come from (needs the grid read).', wrap=360)
            dpg.add_spacer(width=12)
            dpg.add_button(label='Rename...', height=BUTTON_H, callback=lambda: self._on_rename(pack))
            dpg.add_button(label='Export...', height=BUTTON_H, callback=lambda: self._on_export(pack))
            dpg.add_button(label='Delete', height=BUTTON_H, callback=lambda: self._on_delete(pack))
            restage = dpg.add_button(label='Stage for its slots', height=BUTTON_H, enabled=bool(now) and not self.working
                                     and self.grid.pending.pack is None, callback=lambda: self._on_restage(pack, now))
            with dpg.tooltip(restage):
                dpg.add_text('After a clip changed: stage the pack again on every slot that speaks with it, so '
                             '"Patch Game" writes the new clips.', wrap=360)
        table = self._row_header(parent)
        for moment, role in sound_packs.MOMENTS:
            clip = pack.clips.get(moment)
            with dpg.table_row(parent=table):
                dpg.add_text(moment)
                dpg.add_text(role, color=_DIM)
                if clip is not None:
                    origin = os.path.basename(clip.origin) if clip.origin else clip.rwav
                    seconds = f'{clip.seconds:.2f} s' if clip.seconds is not None else ''
                    dpg.add_text(f'{origin}  ({seconds})', color=_OK)
                else:
                    dpg.add_text(f'(not filled: {base})', color=_FAINT)
                with dpg.group(horizontal=True, horizontal_spacing=4):
                    wav = pack.wav_path(moment) if clip is not None else None
                    dpg.add_button(label='Play', width=54, height=BUTTON_H,
                                   enabled=_WINDOWS and clip is not None,
                                   callback=lambda _s, _a, u: self._play_file(u),
                                   user_data=wav or (pack.rwav_path(moment) if clip is not None else None))
                    dpg.add_button(label='Replace...', width=84, height=BUTTON_H, enabled=not self.working,
                                   callback=lambda _s, _a, u: self._on_replace_custom(*u), user_data=(pack, moment))
                    dpg.add_button(label='Reset', width=54, height=BUTTON_H, enabled=clip is not None and not self.working,
                                   callback=lambda _s, _a, u: self._on_remove_custom(*u), user_data=(pack, moment))

    # ------------------------------------------------------------------ playback
    def _play_bytes(self, data):
        if not _WINDOWS:
            return
        import tempfile
        import winsound
        # winsound cannot play asynchronously from memory: the WAV goes through a temp file (one, reused)
        try:
            if self._playing is None:
                handle, self._playing = tempfile.mkstemp(prefix='sluggies_clip_', suffix='.wav')
                os.close(handle)
            winsound.PlaySound(None, winsound.SND_PURGE)              # stop what still plays: the file is rewritten
            with open(self._playing, 'wb') as f:
                f.write(data)
            winsound.PlaySound(self._playing, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except (RuntimeError, OSError) as exc:
            self.app.log_line(f'[sounds] could not play the clip: {exc}', _WARN)
            self._status(f'Could not play the clip: {exc}', _ERROR)

    def _play_stock(self, sound_id):
        if self.archive is None:
            return
        _brsar, _clips, sound_packs = _sound_modules()
        try:
            self._play_bytes(sound_packs.stock_clip_wav(self.archive, sound_id))
        except Exception as exc:
            self.app.log_line(f'[sounds] could not decode sound {sound_id}: {exc}', _WARN)

    def _play_file(self, path):
        if not path or not os.path.isfile(path):
            return
        _brsar, clips, _sound_packs = _sound_modules()
        try:
            with open(path, 'rb') as handle:
                data = handle.read()
            if clips.is_rwav_name(path):
                data = clips.rwav_wav_bytes(data)
            self._play_bytes(data)
        except Exception as exc:
            self.app.log_line(f'[sounds] could not play {os.path.basename(path)}: {exc}', _WARN)

    # ------------------------------------------------------------------ encoding
    def _encode(self, path, on_done):
        """Prepare and encode the picked clip on a worker thread; ``on_done(rwav, wav_bytes, notes, error)`` on
        the GUI thread."""
        self.working = True
        self._status(f'Encoding {os.path.basename(path)}...', _DIM)
        dpg.configure_item('sounds_spinner', show=True)
        archive_path = os.path.join(self.app.root_dir, '1_Input', 'sound_NA', 'MY2.brsar')

        def work():
            rwav = wav = None
            notes, error = [], None
            # the encoder is a tight numpy loop that holds the GIL: give the render thread more turns meanwhile
            import sys
            interval = sys.getswitchinterval()
            sys.setswitchinterval(0.0005)
            try:
                brsar, clips, _sound_packs = _sound_modules()
                if clips.is_rwav_name(path):
                    with open(path, 'rb') as handle:
                        rwav = handle.read()
                    clips.check_rwav(rwav, os.path.basename(path))
                    wav = clips.rwav_wav_bytes(rwav)
                else:
                    rate, x = clips.read_wav(path)
                    samples, notes = clips.prepare(rate, x)
                    archive = self.archive or brsar.Brsar.from_file(archive_path)
                    rwav = clips.encode(samples, clips.voice_templates(archive))
                    from Sound import wavfile
                    wav = wavfile.wav_bytes(clips.RATE, [samples])
            except Exception as exc:
                error = str(exc)
            finally:
                sys.setswitchinterval(interval)
            self.app.output_queue.put(('call', self._encoded, on_done, rwav, wav, notes, error))
        threading.Thread(target=work, daemon=True).start()

    def _encoded(self, on_done, rwav, wav, notes, error):
        self.working = False
        dpg.configure_item('sounds_spinner', show=False)
        if error:
            self._status(f'Refused: {error}', _ERROR)
            self.app.log_line(f'[sounds] clip refused: {error}', _WARN)
            self._draw_detail()
            return
        for note in notes:
            self.app.log_line(f'[sounds] {note}', _DIM)
        self._status('Clip encoded.' + (f' ({"; ".join(notes)})' if notes else ''), _OK)
        on_done(rwav, wav)

    def _pick_clip(self, title, on_path):
        """Pick one WAV, starting where the last one came from (remembered in the GUI settings), else in
        2_Output_Models/_SOUNDS."""
        if self.app.picking:
            return
        start = next((d for d in (self.app.settings.get(CLIP_DIR_SETTING),
                                  os.path.join(self.app.models_dir, '_SOUNDS')) if d and os.path.isdir(d)),
                     self.app.models_dir)
        if os.path.isdir(start):
            dpg.configure_item('sounds_wav_dialog', default_path=start)

        def picked(paths):
            path = paths[0] if paths else None
            if path:
                try:
                    self.app.settings.set(CLIP_DIR_SETTING, os.path.dirname(os.path.abspath(path)))
                except OSError:
                    pass                      # not remembered; the pick itself goes on
            on_path(path)
        self._pick('sounds_wav_dialog', title, WAV_FILTERS, picked, initial_dir=start)

    # ------------------------------------------------------------------ game packs
    def _on_replace_stock(self, head, moment):
        if self.working or self.grid.pending.pack is not None:
            return
        name = self._name(head)
        _brsar, _clips, sound_packs = _sound_modules()

        def chosen(path):
            if not path or not os.path.isfile(path):
                return

            def done(rwav, _wav):
                import hashlib
                folder = os.path.join(self.app.root_dir, gui_grid.STAGED_CLIPS_REL)
                os.makedirs(folder, exist_ok=True)
                staged = os.path.join(folder, hashlib.sha1(rwav).hexdigest()[:16] + '.rwav')
                with open(staged, 'wb') as handle:
                    handle.write(rwav)
                edit = gui_grid.species_clip_edit(head, moment, staged, origin=path)
                if not self.grid.stage_edit(edit, gui_grid.SPECIES_CLIP, moment):
                    self._status('The character grid is busy: try again in a moment.', _WARN)
            self._encode(path, done)
        self._pick_clip(f'Replace {moment} ({sound_packs.ROLE_OF[moment]}) of {name}\'s voice', chosen)

    def _on_reset_stock(self, head, moment):
        if self.working or self.grid.pending.pack is not None:
            return
        if self.grid.pending.species_clip_edit(head, moment) is not None:
            self.grid.pending.drop_species_clip(head, moment)
            self.grid._pending_changed()
            return
        square = self._square_of_head(head)
        if moment in ((square or {}).get('clips') or {}):
            edit = gui_grid.species_clip_edit(head, moment, None)
            if not self.grid.stage_edit(edit, gui_grid.SPECIES_CLIP, moment):
                self._status('The character grid is busy: try again in a moment.', _WARN)

    # ------------------------------------------------------------------ custom packs
    def _on_replace_custom(self, pack, moment):
        if self.working:
            return
        _brsar, _clips, sound_packs = _sound_modules()

        def chosen(path):
            if not path or not os.path.isfile(path):
                return

            def done(rwav, wav):
                try:
                    sound_packs.set_clip(pack, moment, rwav, wav, origin=path)
                except (sound_packs.PackError, OSError, ValueError) as exc:
                    self._status(f'Could not store the clip: {exc}', _ERROR)
                self._draw_list()
                self._draw_detail()
            self._encode(path, done)
        self._pick_clip(f'Replace {moment} ({sound_packs.ROLE_OF[moment]}) of the pack {pack.name}', chosen)

    def _on_remove_custom(self, pack, moment):
        _brsar, _clips, sound_packs = _sound_modules()
        try:
            sound_packs.remove_clip(pack, moment)
        except OSError as exc:
            self._status(f'Could not remove the clip: {exc}', _ERROR)
        self._draw_list()
        self._draw_detail()

    def _on_base_voice(self, pack, heads, choices, value):
        _brsar, _clips, sound_packs = _sound_modules()
        index = choices.index(value) if value in choices else 0
        voice = None if index == 0 else heads[index - 1]
        try:
            sound_packs.set_voice(pack, voice)
        except OSError as exc:
            self._status(f'Could not save the pack: {exc}', _ERROR)
        self._draw_detail()

    def _on_new_pack(self):
        _brsar, _clips, sound_packs = _sound_modules()

        def answer(text):
            try:
                pack = sound_packs.create_pack(self.app.root_dir, text.strip())
            except (sound_packs.PackError, OSError) as exc:
                self._status(f'Could not create the pack: {exc}', _ERROR)
                return
            self.custom.append(pack)
            self.custom.sort(key=lambda p: p.name.lower())
            self.selected = ('custom', pack.name)
            self._status(f'Pack {pack.name!r} created: fill its moments with Replace..., then give it to a slot on the '
                         'Character grid tab (Voice...).', _OK)
            self._draw_list()
            self._draw_detail()
        self.app.ask_text('New voice pack', 'Name of the new voice pack (a folder in Sound_Packs/):', answer,
                          lambda: None)

    def _on_rename(self, pack):
        _brsar, _clips, sound_packs = _sound_modules()

        def answer(text):
            try:
                sound_packs.rename_pack(self.app.root_dir, pack, text.strip())
            except (sound_packs.PackError, OSError) as exc:
                self._status(f'Could not rename the pack: {exc}', _ERROR)
                return
            self.selected = ('custom', pack.name)
            self._status(f'Renamed to {pack.name!r}. Slots that speak with the old name keep their clips; assign the '
                         'pack again to show the new name.', _OK)
            self._draw_list()
            self._draw_detail()
        self.app.ask_text('Rename the voice pack', f'New name for {pack.name!r}:', answer, lambda: None,
                          initial=pack.name)

    def _on_delete(self, pack):
        _brsar, _clips, sound_packs = _sound_modules()
        now, pending = self._pack_users(pack.name)

        def answer(text):
            if str(text).strip().lower() not in ('y', 'yes'):
                return
            try:
                sound_packs.delete_pack(pack)
            except OSError as exc:
                self._status(f'Could not delete the pack: {exc}', _ERROR)
                return
            self.custom = [p for p in self.custom if p.name != pack.name]
            self.selected = None
            self._status(f'Pack {pack.name!r} deleted.', _OK)
            self._draw_list()
            self._draw_detail()
        warn = (f' {len(now)} slot(s) speak with it in the game; they keep their clips until given another voice.'
                if now else '')
        self.app.ask_text('Delete the voice pack?', f'Delete the folder of {pack.name!r} with its clips?{warn}',
                          answer, lambda: None, kind='yesno')

    def _on_export(self, pack):
        _brsar, _clips, sound_packs = _sound_modules()
        if self.app.picking:
            return
        folder = sound_packs.packs_dir(self.app.root_dir)

        def chosen(paths):
            path = paths[0] if paths else None
            if not path:
                return
            try:
                written = sound_packs.export_pack(pack, path)
            except OSError as exc:
                self._status(f'Could not export the pack: {exc}', _ERROR)
                return
            self._status(f'Exported to {written}.', _OK)
        os.makedirs(folder, exist_ok=True)
        self._pick('sounds_pack_save_dialog', f'Export the voice pack {pack.name}', PACK_FILTERS, chosen,
                   initial_dir=folder, save=True, default_ext=sound_packs.PACK_EXTENSION.lstrip('.'))

    def _on_import(self):
        _brsar, _clips, sound_packs = _sound_modules()
        if self.app.picking:
            return

        def chosen(paths):
            path = paths[0] if paths else None
            if not path or not os.path.isfile(path):
                return
            try:
                pack = sound_packs.import_pack(self.app.root_dir, path)
            except (sound_packs.PackError, OSError, ValueError) as exc:
                self._status(f'Could not import the pack: {exc}', _ERROR)
                return
            self.custom.append(pack)
            self.custom.sort(key=lambda p: p.name.lower())
            self.selected = ('custom', pack.name)
            self._status(f'Imported {pack.name!r} ({pack.count} clips).', _OK)
            self._draw_list()
            self._draw_detail()
        self._pick('sounds_pack_load_dialog', 'Import a voice pack', PACK_FILTERS, chosen,
                   initial_dir=sound_packs.packs_dir(self.app.root_dir))

    def _on_restage(self, pack, users):
        """Stage the pack again on every slot that speaks with it (one edit at a time: each runs the staging
        check; the next starts when the dialog closes)."""
        if not users or self.grid.pending.pack is not None:
            return
        state = self._state()
        queue = list(users)

        def next_one():
            if not queue:
                return
            cid = queue.pop(0)
            char = gui_grid.characters(state).get(cid) or {}
            source = gui_grid.hex_id(char['char_voice']) if char.get('char_voice') is not None else None
            edit = gui_grid.char_voice_edit(cid, source, pack.name)
            if not self.grid.stage_edit(edit, gui_grid.CHAR_VOICE):
                self._status('The character grid is busy: try again in a moment.', _WARN)
                return
            self._after_action(next_one)
        next_one()

    def _after_action(self, callback):
        """Run ``callback`` once the grid tab's dialog closed (polled through the queue)."""
        def check():
            if self.grid.action is None and self.grid.confirm is None and not self.app.busy:
                callback()
            else:
                threading.Timer(0.3, lambda: self.app.output_queue.put(('call', check))).start()
        threading.Timer(0.3, lambda: self.app.output_queue.put(('call', check))).start()
