"""The custom CPU level (SluggiesTools/CustomCpu): settings, files, the DOL block and its stubs, the popup files,
and its survival of option changes and roster runs."""

import functools
import json
import os
import struct
import tempfile
import unittest
from unittest import mock

from PIL import Image

from SluggiesTools import gecko_compat
from SluggiesTools.CustomCpu import behaviours, carry, engine, item_defense, menu_files, pitching, settings, view
from SluggiesTools.CustomCpu import runner as cpu_runner
from SluggiesTools.Dol import dolfile, inventory, ppc
from SluggiesTools.GameOptions import game_options as go
from SluggiesTools.Icons import layout2d
from SluggiesTools.Icons.tests.test_layout2d import make_bank, make_element
from SluggiesTools.Roster import dat_hammerspace as dhs
from SluggiesTools.Roster import datfile, names
from SluggiesTools.Roster import dol_hammerspace
from SluggiesTools.Roster import runner as roster_runner
from SluggiesTools.tests.test_roster_dev import synthetic_dol

BASE = 0x10000                          # stands in for BASE_SIZE
FNAME = dhs.hh._DAT_FNAME_PTR
SETTINGS_AT = 0x80630000                # the match settings object in the synthetic game
R13 = SETTINGS_AT + 0x2000
DIR_BASE = 0x80692000
RECORDS = {menu_files.MSG_DIR: 0x80692100, menu_files.ART_DIR: 0x80692400}
STOCK_AT = {(menu_files.MSG_DIR, 'en'): 0x0100, (menu_files.MSG_DIR, 'fr'): 0x2000,
            (menu_files.MSG_DIR, 'sp'): 0x4000, (menu_files.ART_DIR, 'en'): 0x6000,
            (menu_files.ART_DIR, 'fr'): 0x9000, (menu_files.ART_DIR, 'sp'): 0xC000}
CAPTIONS = {'en': 'All-Star Level', 'fr': 'Intense!  Niveau Étoile', 'sp': 'Difícil  Nivel experto'}


def _section(start: int, end: int, fill) -> tuple[int, bytes]:
    return start, bytes(fill(a) for a in range(start, end))


# Three characters of the synthetic game (id: charge pitch speed, curveball speed, curve, charge power, changeup
# speed factor and arc): a fast pitcher and strong hitter, a floating changeup and weak hitter, a slow thrower.
PITCHERS = {0: (160, 100, 60, 90, 1.0, 10.0), 1: (120, 100, 40, 30, 0.5, 12.0), 2: (90, 100, 0, 65, 1.0, 5.0)}
PITCH_TABLES = ('stats', 'changeup', 'traj', 'charfloats', 'throwfloats', 'selector')


def pitch_bytes() -> dict[int, int]:
    """What the pitch model reads of the game, as ``{address: byte}``: the per-character tables at their stock
    addresses with the game's lis / addi pairs to them, the flight and timing numbers."""
    out: dict[int, int] = {}

    def put(at: int, raw: bytes) -> None:
        out.update({at + i: b for i, b in enumerate(raw)})

    def pair(sites, target: int) -> None:
        put(sites[0], struct.pack('>I', ppc.one(sites[0], lambda a: a.lis(3, ppc.ha(target)))))
        put(sites[1], struct.pack('>I', ppc.one(sites[1], lambda a: a.addi(3, 3, ppc.lo(target)))))
    for name in PITCH_TABLES:
        pair(inventory.table(name).pairs[0], inventory.table(name).address)
    for ref, name in ((pitching.TF_REF, 'throwfloats'), (pitching.CHARFLOATS_REF, 'charfloats'),
                      (pitching.TRAJ_REF, 'traj'), (pitching.SELECTOR_REF, 'selector')):
        pair(ref, inventory.table(name).address)
    stats = inventory.table('stats').address + inventory.table('stats').header
    for cid, (charge, curveball, curve, power, mult, arc) in PITCHERS.items():
        row = stats + cid * pitching.STATS_ROW
        for offset, value in ((pitching.STAT_CHARGE_PITCH, charge), (pitching.STAT_CURVEBALL, curveball),
                              (pitching.STAT_CURVE, curve), (pitching.STAT_CHARGE_POWER, power),
                              (pitching.STAT_SLAP_POWER, 50)):
            put(row + offset, struct.pack('>H', value))
        put(inventory.table('changeup').address + 8 * cid, struct.pack('>ff', mult, arc))
        put(inventory.table('throwfloats').address + 0xC * cid + pitching.TF_STEER, struct.pack('>f', 1.0))
        put(inventory.table('charfloats').address + 0x24 * cid + 0x10, struct.pack('>f', -0.5))
    put(pitching.HBP_BOX + 1, bytes([20]))
    for at, value in ((pitching.OPEN, 8.0), (pitching.CLOSE, 15.0), (pitching.STEER_RANGE + 8, 0.0008),
                      (pitching.STEER_RANGE + 12, 0.002), (0x8079EDC0 + pitching.SPEED_DIV, 100.0),
                      (0x8079EDC0 - 0x7338, 0.995), (0x8079794C, 18.0), (0x80627BA8, 1.2)):
        put(at, struct.pack('>f', value))
    put(0x806277DC + 0xE, struct.pack('>7h', 0, 0, 0, 2, 50, 0, 0))          # the flight row: drag 0.002 from 50%
    put(pitching.STAT_RANGE, struct.pack('>HH', 0, 100))
    for row in (0, 0x1E):                                                    # every swing frame is active
        put(pitching.ACTIVE + row, struct.pack('>15h', *[1] * 15))
    for kind in range(3):                                                    # straight away on frame 7
        put(pitching.DIRS + kind * 0x3C, struct.pack('>30h', *[(f // 2 - 7) * 10 for f in range(30)]))
    return out


def item_bytes() -> dict[int, int]:
    """What the fielders' item code reads of the game when it is built: the two jump types' rows (both modes)."""
    out: dict[int, int] = {}
    for mode in range(2):
        for jtype, row in enumerate(((0.5, 0.05, 0.0, 4.0), (0.6, 0.05, 0.0, 6.0))):
            at = item_defense.JUMP_TABLE + mode * 0x40 + jtype * 0x10
            out.update({at + i: b for i, b in enumerate(struct.pack('>4f', *row))})
    return out


@functools.lru_cache(maxsize=None)
def game_dol(behaviour_sites: bool = False) -> bytes:
    """test_roster_dev's DOL plus every site the custom level reads or writes, holding the clean game's bytes.
    ``behaviour_sites``: the behaviour code's hook sites and what the pitch model reads (``pitch_bytes``) too."""
    blob = bytearray(synthetic_dol())
    struct.pack_into('>I', blob, 0xDC, 0x80000)          # a shorter bss: the real one leaves the D7 tables free
    image = dolfile.DolImage(bytes(blob))
    words: dict[int, int] = {engine.HOOK: engine.HOOK_STOCK, engine.OPEN_DIALOG[0]: engine.OPEN_DIALOG[1],
                             engine.OPEN_SITE: engine.OPEN_STOCK, engine.ANSWER_SITE: engine.ANSWER_STOCK,
                             engine.ANSWER_LOW: ppc.one(engine.ANSWER_LOW, lambda a: a.b(0x802CCCE0)),
                             go.CLOSE_PLAY_SITE: go.CLOSE_PLAY_STOCK}
    for site, value in engine.STOCK_STORES.items():
        for i, word in enumerate(struct.unpack('>3I', engine._store_words(value))):
            words[site + 4 * i] = word
    for i, value in enumerate(engine.ENTRY_STOCK):
        words[engine.ENTRY + 4 * i] = value & 0xFFFFFFFF
    for i, value in enumerate((3, 2, 1, 0, 0)):
        words[engine.TIER - 16 + 4 * i] = value
    close_play = go.CLOSE_PLAY_SITE & ~0x1F
    ranges = [(0x800C3660, 0x800C36A0), (0x800E25A0, 0x800E2760), (close_play, close_play + 0x20),
              (0x802CCC20, 0x802CCD00), (0x80624740, 0x80624B40), (0x8062FFE0, 0x80630040),
              (0x8063FB60, 0x8063FBA0), (0x806D3140, 0x806D31A0), (0x807971C0, 0x807972A0)]
    stock = dict(settings.DECLARED_STOCK)
    if behaviour_sites:
        ranges = [(0x800BA380, 0x800E2B00), (0x80113300, 0x80163640), (0x802CCC20, 0x802FECA0),
                  (0x80450300, 0x8046DC40), (0x80624740, 0x80631000), (0x80631060, 0x80631D00),
                  (0x8063FB60, 0x8063FBA0), (0x806CE9A0, 0x806D31A0), (0x807971C0, 0x80797AA0),
                  (0x807A1640, 0x807A1660)]
        for b in behaviours.BEHAVIOURS:
            words.update(dict(b.hooks + b.relies))
        stock.update(pitch_bytes())
        stock.update(item_bytes())
    for at, word in words.items():
        stock.update({at + i: b for i, b in enumerate(struct.pack('>I', word))})

    def fill(a):
        return stock.get(a, 0x60 if a < 0x80600000 else 0)
    for start, end in ranges:
        image.add_section('text' if start < 0x80600000 else 'data', *_section(start, end, fill))
    return image.to_bytes()


def setUpModule():
    """No test reads the machine's own button picture (``_gui/custom_cpu/button.png``)."""
    patcher = mock.patch.object(menu_files, 'BUTTON_PICTURE', '')
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


def message_table(lang: str) -> bytes:
    msgs = [f'm{n}'.encode('utf-16-be') for n in range(engine.MESSAGE)]
    msgs[menu_files.ALL_STAR] = b'\xF0\x00' + menu_files.encode_name(CAPTIONS[lang])
    return names.build_table(msgs)


def picture_bank(lang: str) -> bytes:
    rows = [(n % 7, (0.0, 0.0, 1.0, 1.0)) for n in range(engine.ART_ROW)]
    rows[menu_files.LEVEL4_ROW] = (50, (0.4667, 0.166, 0.547, 0.326))
    bank = bytearray(make_bank([make_element([(1, 2, 0xFF)])], resource_rows=rows))
    bank[0x30] = 0xA0 + dhs.LANGS.index(lang)           # the copies differ in their texture data
    return bytes(bank)


def stock_file(directory: int, lang: str) -> bytes:
    return message_table(lang) if directory == menu_files.MSG_DIR else picture_bank(lang)


def game_files(tmp: str) -> tuple[str, str]:
    """``game_dol`` with a directory table and the two popup files' records, and a DAT holding their copies."""
    image = dolfile.DolImage(game_dol())
    blob = bytearray(0xF000)
    table = dhs.dol_base_address(dhs.hh._DIRS_START)
    pointers = [DIR_BASE + 0x40] * dhs.hh._DIRS_COUNT
    for directory, at in RECORDS.items():
        pointers[directory] = at
    struct.pack_into(f'>{len(pointers)}I', blob, table - DIR_BASE, *pointers)
    dat = bytearray(BASE + 0x100)
    for directory, at in RECORDS.items():
        for file in range(6):
            slots = [(0x20, 0x10)] * 3
            if file == menu_files.MSG_FILE:             # both files are file 4 of their directory
                slots = [(STOCK_AT[directory, lang], len(stock_file(directory, lang))) for lang in dhs.LANGS]
            rec = b''.join(struct.pack('>4I', FNAME, length, offset, length) for offset, length in slots)
            blob[at - DIR_BASE + 0x30 * file:at - DIR_BASE + 0x30 * (file + 1)] = rec
        for lang in dhs.LANGS:
            data = stock_file(directory, lang)
            assert len(data) <= 0x1F00, 'the synthetic copies would overlap'
            dat[STOCK_AT[directory, lang]:STOCK_AT[directory, lang] + len(data)] = data
    image.add_section('data', DIR_BASE, bytes(blob))
    dol_path, dat_path = os.path.join(tmp, 'main.dol'), os.path.join(tmp, 'dt_na.dat')
    with open(dol_path, 'wb') as f:
        f.write(image.to_bytes())
    with open(dat_path, 'wb') as f:
        f.write(dat)
    with open(os.path.join(tmp, 'fst.bin'), 'wb') as f:
        f.write(bytes(0x18) + struct.pack('>I', len(dat)))
    return dol_path, dat_path


class Machine:
    """Runs a stub's instructions on a ``DolImage`` (the handful the custom level's stubs use)."""

    def __init__(self, image: dolfile.DolImage):
        self.image, self.r, self.ctr, self.cr0, self.flushed = image, [0] * 32, 0, 0, []

    def run(self, pc: int, limit: int = 20000) -> None:
        for _ in range(limit):
            w = self.image.u32(pc)
            if w == 0x4E800020:
                return
            pc = self.step(w, pc)
        raise AssertionError('the stub did not return')

    def step(self, w: int, pc: int) -> int:
        r, op = self.r, w >> 26
        d, a, imm = (w >> 21) & 31, (w >> 16) & 31, ppc.signed16(w)
        b = (w >> 11) & 31
        if op in (14, 15):
            r[d] = ((r[a] if a else 0) + (imm << 16 if op == 15 else imm)) & 0xFFFFFFFF
        elif op == 32:
            r[d] = self.image.u32((r[a] + imm) & 0xFFFFFFFF)
        elif op in (34, 35):
            at = (r[a] + imm) & 0xFFFFFFFF
            r[d] = self.image.read(at, 1)[0]
            if op == 35:
                r[a] = at
        elif op in (38, 39):
            at = (r[a] + imm) & 0xFFFFFFFF
            self.image.write(at, bytes([r[d] & 0xFF]))
            if op == 39:
                r[a] = at
        elif op == 11:
            self.cr0 = (ppc.signed16(r[a] >> 16) << 16 | r[a] & 0xFFFF) - imm
        elif op == 21:
            sh, mb, me = b, (w >> 6) & 31, (w >> 1) & 31
            value = ((r[d] << sh) | (r[d] >> (32 - sh))) & 0xFFFFFFFF if sh else r[d]
            mask = sum(1 << (31 - i) for i in range(mb, me + 1))
            r[a] = value & mask
        elif op == 18:
            return ppc.branch_target(w, pc)
        elif op == 16:
            bo, bi = d, a
            target = pc + ppc.signed16(w & 0xFFFC)
            if bo == 16:
                self.ctr -= 1
                return target if self.ctr else pc + 4
            bit = {0: self.cr0 < 0, 1: self.cr0 > 0, 2: self.cr0 == 0}[bi]
            return target if bit == (bo == 12) else pc + 4
        elif op == 31:
            xo = (w >> 1) & 0x3FF
            if xo == 0:
                self.cr0 = r[a] - r[b]
            elif xo == 266:
                r[d] = (r[a] + r[b]) & 0xFFFFFFFF
            elif xo == 444:
                r[a] = r[d] | r[b]
            elif xo == 467:
                self.ctr = r[d]
            elif xo in (54, 982):
                self.flushed.append((xo, r[b]))
            elif xo != 598:
                raise AssertionError(f'instruction {w:08X} at 0x{pc:08X}')
        elif w != 0x4C00012C:
            raise AssertionError(f'instruction {w:08X} at 0x{pc:08X}')
        return pc + 4


class SettingsTests(unittest.TestCase):
    def test_sites_are_fixed_and_cover_every_table(self):
        runs = settings.runs()
        self.assertEqual(len(runs), 41)
        self.assertEqual(sum(n for _a, n in runs), 116)
        self.assertIn((settings.STAR_MODIFIERS, 16), runs)       # the modifiers and the charge odds are one run
        self.assertLessEqual(max(n for _a, n in runs), engine.MAX_RUN)
        self.assertEqual((len(settings.SWITCHES), len(settings.CONSTANTS)), (15, 36))
        self.assertEqual(len([t for t in settings.TABLES if not settings.is_own(t.key)]), 36)

    def test_declared_defaults_decode_from_the_declared_bytes(self):
        for t in settings.TABLES:
            self.assertTrue(settings.same(t, t.decode(settings.declared_get), t.stock), t.key)

    def test_no_edits_holds_the_clean_game(self):
        held = settings.custom_bytes({}, settings.declared_get)
        self.assertEqual(held, {a: settings.DECLARED_STOCK[a] for a in settings.site_addresses()})

    def test_a_group_is_the_base_its_values_edit(self):
        edits = {settings.BAT: True, 'lays_off': 95}
        held = settings.custom_bytes(edits, settings.declared_get)
        self.assertEqual(held[0x80797227], 100)                   # tuned: pitch read
        self.assertEqual(held[0x80624985], 95)                    # the explicit value, though it is the clean one
        self.assertEqual(held[0x807971EB], 0x55)                  # another group: clean
        self.assertEqual(settings.value(settings.BY_KEY['pitch_read'], edits), 100)
        self.assertEqual(settings.value(settings.BY_KEY['lays_off'], edits), 95)

    def test_set_value_keeps_only_what_differs_from_the_base(self):
        edits = {}
        settings.set_value(edits, 'pitch_read', 85)
        self.assertEqual(edits, {})
        settings.set_value(edits, settings.BAT, True)
        settings.set_value(edits, 'pitch_read', 85)
        self.assertEqual(edits, {settings.BAT: True, 'pitch_read': 85})
        settings.set_value(edits, 'pitch_read', 100)
        self.assertEqual(edits, {settings.BAT: True})

    def test_encodings(self):
        get = settings.declared_get
        held = settings.custom_bytes({'reaction_delay': 12, 'press_delay': 12, 'timing_wrong': 3, 'dive_try': 50,
                                      'perfect_fastball': 100, 'sluggies_star_modifier_normal': -30}, get)
        self.assertEqual((held[0x806249E8], held[0x806249ED]), (12, 10))          # 60 Hz, 50 Hz
        self.assertEqual([held[0x80624B1E + i] for i in range(2)] + [held[0x80624B26 + i] for i in range(2)],
                         [0, 12, 0, 10])
        self.assertEqual((held[0x80624916], held[0x80624917]), (0xFD, 3))
        self.assertEqual([held[0x80624A14 + 5 * c] for c in range(4)], [50] * 4)
        self.assertEqual([held[0x8062475F + i] for i in range(5)], [0, 0, 0, 0, 100])
        self.assertEqual(held[settings.STAR_MODIFIERS + 6], 0xE2)
        self.assertEqual(held[settings.STAR_MODIFIERS + 4], 0xF6)                 # another tier's row: clean

    def test_fielding_group_writes_the_dive_gates_and_the_option_keeps_its_word(self):
        on = dict(settings.swap_records({settings.FIELD: True}, settings.declared_get))
        off = dict(settings.swap_records({}, settings.declared_get))
        self.assertEqual(on[settings.FLY_DIVE_GATE], bytes.fromhex('2C040000'))
        self.assertEqual(on[settings.GRID_DIVE_GATE], settings.OUTFIELD_DIVE_WORD)
        self.assertEqual(off[settings.GRID_DIVE_GATE], bytes.fromhex('28000003'))
        kept = dict(settings.swap_records({}, settings.declared_get, outfield_dive=True))
        self.assertEqual(kept[settings.GRID_DIVE_GATE], settings.OUTFIELD_DIVE_WORD)

    def test_checks(self):
        self.assertEqual(settings.check({}), [])
        self.assertIn('outside', settings.check({'pitch_read': 101})[0])
        self.assertIn('whole number', settings.check({'pitch_read': 1.5})[0])
        self.assertIn('not a CPU setting', settings.check({'nope': 1})[0])
        self.assertIn('Rarely from', settings.check({'star_rare': 80, 'star_never': 70})[0])
        usage = 'sluggies_star_usage_power_dire'
        found = settings.check({usage: 125, 'sluggies_star_modifier_special': 5})
        self.assertEqual(len(found), 1)
        self.assertIn('at most 127', found[0])

    def test_changed_values_are_described(self):
        edits = {'cpu-items': True, 'items_lead': 20, 'pitch_read': 90}
        self.assertEqual(settings.describe(edits), ['Aims batting items at fielders on', 'Hit before the catch 20 '
                                                    'frames', 'Pitch read 90%'])


class FileTests(unittest.TestCase):
    def test_document_is_the_other_tools_level_4_column(self):
        doc = settings.document({'pitch_read': 95, 'cpu-items': True}, 'mine', {'new_key': {'5': 1}})
        self.assertEqual(doc, {'format': 'sluggers-cpu', 'version': 1, 'name': 'mine', 'settings': {
            'new_key': {'5': 1}, 'pitch_read': {'4': 95}, 'cpu-items': {'4': True}}})
        loaded = settings.from_document(doc)
        self.assertEqual(loaded.edits, {'pitch_read': 95, 'cpu-items': True})
        self.assertEqual(loaded.extra, {'new_key': {'5': 1}})
        self.assertIn('new_key', loaded.notes[0])

    def test_level_4_column_ignores_the_other_levels(self):
        doc = {'format': 'sluggers-cpu', 'settings': {'pitch_read': {'4': 90, '5': 50}, 'curve': {'5': 10},
                                                       'fastball_speed': 120, 'force3': {'5': 1}}}
        loaded = settings.from_document(doc)
        self.assertEqual(loaded.edits, {'pitch_read': 90})
        self.assertEqual(settings.levels_in(doc), [4, 5, 6])
        self.assertTrue(any('force3' in note for note in loaded.notes))

    def test_level_5_column_turns_that_tools_defaults_into_values(self):
        loaded = settings.from_document({'format': 'sluggers-cpu', 'settings': {}}, 5)
        self.assertEqual(loaded.edits, {s.key: True for s in settings.SWITCHES})
        held = settings.custom_bytes(loaded.edits, settings.declared_get)
        self.assertEqual((held[0x80797227], held[0x80797233], held[0x80624B1F]), (100, 79, 12))

    def test_level_6_column(self):
        doc = {'format': 'sluggers-cpu', 'settings': {
            settings.PITCH: {'6': False}, 'curve': {'6': 20}, 'lays_off': {'5': 3, '6': 97}, 'fastball_top': 50}}
        loaded = settings.from_document(doc, 6)
        self.assertNotIn(settings.PITCH, loaded.edits)
        self.assertNotIn('curve', loaded.edits)                  # its group is off there: that tool skips it
        self.assertEqual(loaded.edits['lays_off'], 97)
        self.assertEqual(loaded.edits['track'], 100)             # its level 6 override of the batting group
        self.assertEqual(loaded.edits['model_learning'], 0.08)
        self.assertEqual(loaded.edits['fastball_top'], 50)       # a bare number: levels 5 and 6
        self.assertNotIn('items_error_free', loaded.edits)       # no level 6 value: the default

    def test_bad_files_are_refused_in_plain_words(self):
        with self.assertRaisesRegex(settings.SettingsError, 'outside'):
            settings.from_document({'format': 'sluggers-cpu', 'settings': {'pitch_read': {'4': 300}}})
        with self.assertRaisesRegex(settings.SettingsError, 'per level'):
            settings.from_document({'format': 'sluggers-cpu', 'settings': {'pitch_read': 90}})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'x.slgcpu')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'format': 'other'}, f)
            with self.assertRaisesRegex(settings.SettingsError, 'isn\'t a CPU settings file'):
                settings.load(path)
            settings.save(path, {'curve': 50}, 'n')
            self.assertEqual(settings.load(path).edits, {'curve': 50})


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.image = dolfile.DolImage(game_dol())
        self.record = engine.Record('Custom Level', {settings.FIELD: True, 'pitch_read': 95, 'press_delay': 8})

    def start_match(self, level: int) -> int:
        """The level the CPU manager gets for match settings +0x10 = ``level``."""
        self.image.write_word(SETTINGS_AT + engine.LEVEL_OFF, level)
        machine = Machine(self.image)
        machine.r[7] = SETTINGS_AT
        for reg in (0, 3, 4, 5):
            machine.r[reg] = 0x1000 + reg
        machine.run(ppc.branch_target(self.image.u32(engine.HOOK), engine.HOOK))
        self.assertEqual([machine.r[reg] for reg in (0, 3, 4, 5, 7)], [0x1000, 0x1003, 0x1004, 0x1005, SETTINGS_AT])
        self.flushed = machine.flushed
        return machine.r[6]

    def live(self) -> dict:
        return {a: self.image.read(a, 1)[0] for a in settings.site_addresses()}

    def test_record_round_trips_and_block_is_in_its_own_section(self):
        self.assertIsNone(engine.detect(self.image))
        log = engine.apply_dol(self.image, self.record)
        self.assertIn('its own DOL section', log[0])
        self.assertEqual(engine.detect(self.image), self.record)
        hs = dol_hammerspace.DolHammerspace.open(self.image)
        self.assertFalse(hs.has_data)                              # a DOL with only the custom level is no roster
        self.assertEqual(len(hs.code.blob), dol_hammerspace.MARKER_SIZE)     # nothing of it in the text section
        slot = dol_hammerspace.cpu_slot(self.image)
        self.assertEqual(slot.address, dol_hammerspace.DATA_BASE + dol_hammerspace.CPU_ALIGN)
        self.assertEqual(engine.block_address(self.image), slot.address + dol_hammerspace.MARKER_SIZE)
        self.assertEqual(dol_hammerspace.arena_values(self.image), [slot.end] * 4)
        self.assertEqual(slot.file_offset + slot.size, len(self.image.data))         # the file's tail
        self.assertEqual(self.image.u32(engine.TIER), engine.TIER_NEW)
        self.assertEqual(self.image.read(engine.FLAG, 2), b'\0\0')
        self.assertEqual(self.live(), {a: settings.DECLARED_STOCK[a] for a in settings.site_addresses()})
        entry = struct.unpack('>14i', self.image.read(engine.ENTRY, engine.DIALOG_SIZE))
        self.assertEqual(entry, engine.ENTRY_STOCK)                # the 5th slot is filled only while the popup opens

    def test_custom_match_swaps_in_and_the_next_match_restores(self):
        engine.apply_dol(self.image, self.record)
        self.image.write(0x80797219, b'\x33')                      # another tool's global edit (a charge odd)
        before = self.live()
        for level in (0, 3):
            self.assertEqual(self.start_match(level), level)
            self.assertEqual(self.live(), before)
            self.assertEqual(self.image.read(engine.FLAG, 2), b'\0\0')
        self.assertEqual(self.start_match(engine.CUSTOM_LEVEL), 0)     # level 4 underneath
        self.assertEqual(self.image.read(engine.FLAG, 2), b'\1\1')
        self.assertEqual(self.live(), settings.custom_bytes(self.record.edits, settings.declared_get))
        self.assertEqual(self.live()[0x80797219], 10)              # the custom level is its settings alone
        self.assertIn((982, settings.FLY_DIVE_GATE), self.flushed)     # the code words reach the instruction cache
        self.assertEqual(self.start_match(engine.CUSTOM_LEVEL), 0)     # again: the backup is not overwritten
        self.assertEqual(self.flushed, [])
        self.assertEqual(self.start_match(2), 2)
        self.assertEqual(self.live(), before)
        self.assertEqual(self.image.read(engine.FLAG, 2), b'\0\0')

    def test_popup_stubs(self):
        engine.apply_dol(self.image, self.record)
        open_at = ppc.branch_target(self.image.u32(engine.OPEN_SITE), engine.OPEN_SITE)
        self.assertEqual(self.image.u32(engine.OPEN_SITE) & 1, 1)                  # still a call
        words = [self.image.u32(open_at + 4 * i) for i in range(18)]
        hi, lo0, lo1 = engine._slot_halves()
        call = next(i for i, w in enumerate(words) if w >> 26 == 18)
        self.assertEqual(ppc.branch_target(words[call], open_at + 4 * call), engine.POPUP_OPEN)
        one = lambda build: ppc.one(0, build)                                      # noqa: E731
        fill = [one(lambda a: a.li(11, engine.CHOICE)), one(lambda a: a.stw(11, lo0, 12)),
                one(lambda a: a.li(11, engine.MESSAGE)), one(lambda a: a.stw(11, lo1, 12))]
        self.assertEqual(words[call - 4:call], fill)
        self.assertEqual(words[call + 1:call + 6], [one(lambda a: a.lis(12, hi)), one(lambda a: a.li(11, -1)),
                                                    fill[1], one(lambda a: a.li(11, 0)), fill[3]])
        self.assertEqual((hi << 16) + lo0, engine.SLOT5)
        answer = ppc.branch_target(self.image.u32(engine.ANSWER_SITE), engine.ANSWER_SITE)
        words = [self.image.u32(answer + 4 * i) for i in range(8)]
        self.assertEqual(words[1], one(lambda a: a.cmpwi(0, engine.CHOICE, cr=1)))
        self.assertEqual(words[3:6], list(struct.unpack('>3I', engine._store_words(engine.CUSTOM_LEVEL))))
        self.assertEqual(ppc.branch_target(words[6], answer + 24), engine.ANSWER_DONE)
        self.assertEqual(ppc.branch_target(words[7], answer + 28), engine.ANSWER_LOW)

    def test_a_block_in_the_text_section_is_read_and_moved(self):
        hs = dol_hammerspace.DolHammerspace.open_or_create(self.image)       # as an earlier version deployed it
        hs.code.put(b'\x60\0\0\0' * 4, 4)                                    # another feature's stub before it
        base = hs.code.here
        block = engine.build_block(base, self.record.encode(), settings.swap_records(
            self.record.edits, settings.declared_get))
        hs.code.put(block.blob, 4)
        hs.commit()
        self.image.write_word(engine.HOOK, ppc.one(engine.HOOK, lambda a: a.bl(block.manager)))
        self.assertEqual(engine.detect(self.image), self.record)
        engine.apply_dol(self.image, engine.Record('Moved', {'curve': 100}))
        self.assertGreaterEqual(engine.block_address(self.image), dol_hammerspace.DATA_BASE)
        self.assertEqual(engine.detect(self.image).button, 'Moved')
        hs = dol_hammerspace.DolHammerspace.open(self.image)
        self.assertEqual(len(hs.code.blob), dol_hammerspace.MARKER_SIZE + 16)        # the old block is cut off

    def test_the_section_follows_the_data_section(self):
        hs = dol_hammerspace.DolHammerspace.open_or_create(self.image)
        hs.data.put(bytes(0x1234), 4)                                        # a roster's tables
        hs.commit()
        engine.apply_dol(self.image, self.record)
        slot = dol_hammerspace.cpu_slot(self.image)
        self.assertEqual(slot.address, dol_hammerspace.DATA_BASE + 0x2000)
        hs = dol_hammerspace.DolHammerspace.open(self.image)
        hs.data.put(bytes(0x2000), 4)                                        # data that would run into it
        with self.assertRaises(dol_hammerspace.HammerspaceError):
            hs.commit()

    def test_redeploy_rewrites_the_section_where_it_is(self):
        engine.apply_dol(self.image, self.record)
        at, size = engine.block_address(self.image), len(self.image.data)
        engine.apply_dol(self.image, engine.Record('Hard', {'curve': 100}))
        self.assertEqual((engine.block_address(self.image), len(self.image.data)), (at, size))
        self.assertEqual(engine.detect(self.image).button, 'Hard')
        self.start_match(engine.CUSTOM_LEVEL)
        self.assertEqual(self.live()[0x807971EB], 100)

    def test_unhook_puts_the_stock_words_back(self):
        stock = {site: self.image.u32(site) for site in (engine.HOOK, engine.OPEN_SITE, engine.ANSWER_SITE,
                                                         engine.TIER)}
        engine.apply_dol(self.image, self.record)
        self.assertTrue(engine.unhook(self.image))
        self.assertEqual({site: self.image.u32(site) for site in stock}, stock)
        self.assertIsNone(engine.detect(self.image))
        self.assertFalse(engine.unhook(self.image))

    def test_changed_sites_and_bad_settings_are_refused(self):
        with self.assertRaisesRegex(engine.CustomCpuError, 'outside'):
            engine.apply_dol(self.image, engine.Record('x', {'pitch_read': 500}))
        self.image.write_word(engine.ANSWER_SITE, 0x60000000)
        with self.assertRaisesRegex(engine.CustomCpuError, 'another patch'):
            engine.apply_dol(self.image, self.record)


class GameOptionTests(unittest.TestCase):
    def setUp(self):
        self.image = dolfile.DolImage(game_dol())
        self.record = engine.Record('Custom Level', {'press_delay': 8})

    def test_turning_an_option_off_rebuilds_the_sections_with_the_custom_level(self):
        go.apply(self.image, ['close_play_50'])
        engine.apply_dol(self.image, self.record)
        log = go.remove(self.image, ['close_play_50'])
        self.assertIn('the custom CPU level rebuilt', log[-1])
        self.assertEqual(engine.detect(self.image), self.record)
        self.assertEqual(len(dol_hammerspace.DolHammerspace.open(self.image).code.blob),
                         dol_hammerspace.MARKER_SIZE)                # the option's stub is gone
        self.assertEqual(go.detect(self.image), [])

    def test_options_survive_the_custom_level_going_off(self):
        stock = self.image.to_bytes()
        engine.apply_dol(self.image, self.record)
        go.apply(self.image, ['close_play_50'])
        engine.unhook(self.image)
        go.remove(self.image, [])
        self.assertEqual(go.detect(self.image), ['close_play_50'])
        go.remove(self.image, ['close_play_50'])
        self.assertEqual(self.image.to_bytes(), stock)             # nothing left: sections dropped

    def test_outfield_dive_option_stays_on_at_the_custom_level(self):
        self.image.write(settings.GRID_DIVE_GATE, settings.OUTFIELD_DIVE_WORD)
        engine.apply_dol(self.image, self.record)
        table_at = engine.block_address(self.image)
        records = dict(settings.swap_records(self.record.edits, settings.declared_get, True))
        block = self.image.read(table_at, engine.CAPACITY)
        self.assertIn(struct.pack('>II', settings.GRID_DIVE_GATE, 4) + records[settings.GRID_DIVE_GATE], block)
        self.image.write(settings.GRID_DIVE_GATE, bytes.fromhex('28000003'))
        self.assertEqual(go.refresh_custom_cpu(self.image), ['custom CPU level: rebuilt for the outfield dive option'])
        block = self.image.read(table_at, engine.CAPACITY)
        self.assertIn(struct.pack('>II', settings.GRID_DIVE_GATE, 4) + bytes.fromhex('28000003'), block)


class MenuFileTests(unittest.TestCase):
    def test_names(self):
        self.assertIsNone(menu_files.name_problem('Custom Level'))
        self.assertIsNone(menu_files.name_problem('W' * 25))
        self.assertIn('at most 25', menu_files.name_problem('W' * 26))
        self.assertIn('empty', menu_files.name_problem('  '))
        self.assertIn('start or end', menu_files.name_problem(' a'))
        self.assertIn('accents', menu_files.name_problem('Lv★'))
        self.assertEqual(menu_files.unproven('Zed 89 Ñ é ~ Å ø Å'), 'Åø')
        self.assertEqual(menu_files.encode_name('A b!é'), bytes.fromhex('FF21 0020 FF42 FF01 00E9'))

    def test_caption_is_appended_with_the_all_star_prefix(self):
        stock = message_table('fr')
        table = menu_files.message_table(stock, 'Custom Level')
        msgs = names.messages(table)
        self.assertEqual(len(msgs), engine.MESSAGE + 1)
        self.assertEqual(msgs[:engine.MESSAGE], names.messages(stock))
        self.assertEqual(msgs[engine.MESSAGE][:2], b'\xF0\x00')
        self.assertEqual(menu_files.decode_text(msgs[engine.MESSAGE]), 'Custom Level')
        with self.assertRaisesRegex(menu_files.MenuFileError, 'messages'):
            menu_files.message_table(table, 'Again')              # a file that has our message is not stock

    def test_button_picture_is_the_level_4_row(self):
        stock = picture_bank('en')
        lay = layout2d.Layout(menu_files.picture_bank(stock))
        self.assertEqual(len(lay.rows), engine.ART_ROW + 1)
        self.assertEqual(lay.rows[engine.ART_ROW], lay.rows[menu_files.LEVEL4_ROW])
        self.assertEqual(len(lay.to_bytes()), len(stock) + layout2d.RESOURCE_ROW_SIZE)
        with self.assertRaisesRegex(menu_files.MenuFileError, 'rows'):
            menu_files.picture_bank(lay.to_bytes())

    def test_button_picture_gets_a_page_of_its_own(self):
        lay = layout2d.Layout(picture_bank('en'))
        prefix = bytearray(lay.prefix[:0x24]) + bytes(0x80 - 0x24)       # one 4x4 texture, image at 0x60
        struct.pack_into('>I', prefix, 4, 0x80)
        struct.pack_into('>H', prefix, 0x20, 1)
        struct.pack_into('>IIHH', prefix, 0x24, 0x60 - 0x20, 0, 4, 4)
        prefix[0x24 + 0x17] = 0x09
        lay.prefix = bytes(prefix)
        lay.rows[menu_files.LEVEL4_ROW] = struct.pack('>HH4f', 0, 0, 0.0, 0.0, 1.0, 1.0)
        stock = lay.to_bytes()
        path = os.path.join(self.enterContext(tempfile.TemporaryDirectory()), 'button.png')
        self.assertIsNone(menu_files.load_picture(path))
        Image.new('RGBA', (164, 164), (255, 255, 255, 255)).save(path)          # any size: drawn into the cell
        picture = menu_files.load_picture(path)
        self.assertEqual(picture.size, (menu_files.CELL, menu_files.CELL))
        out = menu_files.picture_bank(stock, picture)
        new = layout2d.Layout(out)
        self.assertEqual(struct.unpack_from('>H', out, 0x20)[0], 2)
        self.assertEqual(struct.unpack('>HH4f', new.rows[engine.ART_ROW]),
                         (1, 0, 0.0, 0.0, menu_files.CELL / menu_files.PAGE, menu_files.CELL / menu_files.PAGE))
        self.assertEqual(new.rows[:engine.ART_ROW], layout2d.Layout(stock).rows)
        descriptor = out[0x24 + 0x20:0x24 + 0x40]
        image, _pal, height, width = struct.unpack_from('>IIHH', descriptor)
        self.assertEqual((width, height, descriptor[0x17]), (menu_files.PAGE, menu_files.PAGE, names.GX_RGB5A3))
        texels = out[0x20 + image:0x20 + image + menu_files.PAGE * menu_files.PAGE * 2]
        self.assertEqual(texels[:2], b'\xFF\xFF')                                # the cell: opaque white
        self.assertEqual(texels[-2:], b'\x00\x00')                               # outside it: clear
        with open(path, 'wb') as f:
            f.write(b'not a picture')
        with self.assertRaisesRegex(menu_files.MenuFileError, 'cannot be read'):
            menu_files.load_picture(path)


@mock.patch.object(dhs, 'BASE_SIZE', BASE)
class DeployTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = os.path.join(self._tmp.name, '3_Output_Dat')
        self.inp = os.path.join(self._tmp.name, '1_Input')
        os.makedirs(self.out)
        os.makedirs(self.inp)
        self.dol_path, self.dat_path = game_files(self.out)
        with open(self.dol_path, 'rb') as src, open(os.path.join(self.inp, 'main.dol'), 'wb') as dst:
            dst.write(src.read())
        self.record = engine.Record('Custom Level', {'pitch_read': 95})
        patcher = mock.patch.object(gecko_compat, 'confirm', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def image(self) -> dolfile.DolImage:
        with open(self.dol_path, 'rb') as f:
            return dolfile.DolImage(f.read())

    def slots(self, directory: int) -> dict:
        image = self.image()
        words = dhs.read_record(image, RECORDS[directory] + menu_files.MSG_FILE * dhs.RECORD_SIZE)
        return {lang: dhs.slot(words, lang)[:2] for lang in dhs.LANGS}

    def read(self, offset: int, length: int) -> bytes:
        with open(self.dat_path, 'rb') as f:
            f.seek(offset)
            return f.read(length)

    def test_deploy_writes_both_files_and_is_stable(self):
        with open(self.dat_path, 'rb') as f:
            stock_dat = f.read()
        cpu_runner.run(self.out, self.record, input_dir=self.inp)
        self.assertEqual(engine.detect(self.image()), self.record)
        for directory in RECORDS:
            for lang, (offset, length) in self.slots(directory).items():
                self.assertGreaterEqual(offset, BASE)
                data = self.read(offset, length)
                if directory == menu_files.MSG_DIR:
                    self.assertEqual(menu_files.decode_text(names.messages(data)[engine.MESSAGE]), 'Custom Level')
                    self.assertEqual(names.messages(data)[menu_files.ALL_STAR], names.messages(
                        message_table(lang))[menu_files.ALL_STAR])
                else:
                    self.assertEqual(data, menu_files.picture_bank(picture_bank(lang)))
        self.assertEqual(self.read(0, BASE), stock_dat[:BASE])                # the stock copies stay
        with open(os.path.join(self.out, 'fst.bin'), 'rb') as f:
            self.assertEqual(struct.unpack('>I', f.read()[roster_runner.FST_DAT_SIZE_OFFSET:][:4])[0], os.path.getsize(self.dat_path))
        before = (self.image().to_bytes(), self.slots(menu_files.MSG_DIR), self.slots(menu_files.ART_DIR))
        _c, _d, log = cpu_runner.run(self.out, self.record, input_dir=self.inp)
        self.assertEqual((self.image().to_bytes(), self.slots(menu_files.MSG_DIR), self.slots(menu_files.ART_DIR)),
                         before)
        self.assertTrue(all('already in place' in line for line in log[-2:]))

    def test_a_new_name_replaces_only_the_captions(self):
        cpu_runner.run(self.out, self.record, input_dir=self.inp)
        old, art = self.slots(menu_files.MSG_DIR), self.slots(menu_files.ART_DIR)
        cpu_runner.run(self.out, engine.Record('Brutal', {}), input_dir=self.inp)
        self.assertEqual(self.slots(menu_files.ART_DIR), art)
        new = self.slots(menu_files.MSG_DIR)
        for lang, (offset, length) in new.items():
            self.assertEqual(menu_files.decode_text(names.messages(self.read(offset, length))[engine.MESSAGE]),
                             'Brutal')
        taken = sorted(new.values())
        self.assertTrue(all(a + n <= b for (a, n), (b, _m) in zip(taken, taken[1:])))
        self.assertEqual(len(set(new.values()) | set(old.values())) >= 3, True)

    def test_off_restores_the_stock_game(self):
        with open(self.dol_path, 'rb') as f:
            stock = f.read()
        cpu_runner.run(self.out, self.record, input_dir=self.inp)
        size = os.path.getsize(self.dat_path)
        _c, _d, log = cpu_runner.run(self.out, None, input_dir=self.inp)
        self.assertEqual(self.image().to_bytes(), stock)
        self.assertEqual(self.read(BASE, size - BASE), bytes(size - BASE))    # our copies are zeroed
        self.assertIn('custom CPU level: off', log)

    def test_deploy_clears_an_older_gameplay_tabs_cpu_tables(self):
        image = self.image()
        image.write(settings.STAR_USAGE + 3, b'\x10')
        image.write(0x80797219, b'\x33')
        with open(self.dol_path, 'wb') as f:
            f.write(image.to_bytes())
        _c, _d, log = cpu_runner.run(self.out, self.record, input_dir=self.inp)
        self.assertTrue(any('2 values of the star usage' in line for line in log), log)
        self.assertEqual(self.image().read(settings.STAR_USAGE + 3, 1)[0], settings.STAR_USAGE_STOCK[3])
        self.assertEqual(self.image().read(0x80797219, 1), b'\x0a')

    def test_dry_run_writes_nothing(self):
        with open(self.dol_path, 'rb') as f:
            stock = f.read()
        size = os.path.getsize(self.dat_path)
        current, data, _log = cpu_runner.run(self.out, self.record, dry_run=True, input_dir=self.inp)
        self.assertEqual(current, stock)
        self.assertNotEqual(data, stock)
        self.assertEqual((self.image().to_bytes(), os.path.getsize(self.dat_path)), (stock, size))

    def test_cancelled_gecko_break_writes_nothing(self):
        with open(self.dol_path, 'rb') as f:
            stock = f.read()
        with mock.patch.object(gecko_compat, 'confirm', return_value=False):
            with self.assertRaisesRegex(cpu_runner.CustomCpuRunError, 'cancelled'):
                cpu_runner.run(self.out, self.record, input_dir=self.inp)
        self.assertEqual(self.image().to_bytes(), stock)

    def test_carry_rebuilds_the_dol_part_after_a_reset(self):
        """A roster run's reset drops the DOL code and keeps the two records: ``carry.apply`` rebuilds from
        the record alone and leaves the copies where they are."""
        cpu_runner.run(self.out, self.record, input_dir=self.inp)
        deployed = self.image()
        record = carry.detect(deployed)
        with open(os.path.join(self.inp, 'main.dol'), 'rb') as f:
            vanilla = dolfile.DolImage(f.read())
        reset = dolfile.DolImage(vanilla.to_bytes())
        for directory, at in RECORDS.items():                      # what the reset copies over
            rec = at + menu_files.MSG_FILE * dhs.RECORD_SIZE
            reset.write(rec, deployed.read(rec, dhs.RECORD_SIZE))
        self.assertIsNone(carry.detect(reset))
        dat = datfile.DatFile(self.dat_path)
        log = carry.apply(reset, dat, record, vanilla)
        self.assertEqual(carry.detect(reset), self.record)
        self.assertEqual(dat.pending, [])
        self.assertTrue(all('already in place' in line for line in log[-2:]))

    def test_settings_file_and_button_sources(self):
        path = os.path.join(self._tmp.name, 'mine.slgcpu')
        cpu_runner.save(path, engine.Record('Brutal', {'curve': 40}, {'later': {'5': 2}}))
        record, _notes = cpu_runner.record_from_file(path)
        self.assertEqual(record, engine.Record('Brutal', {'curve': 40}, {'later': {'5': 2}}, 'mine'))
        self.assertEqual(cpu_runner.record_from_file(path, button='Other')[0].button, 'Other')
        with self.assertRaisesRegex(cpu_runner.CustomCpuRunError, 'at most 25'):
            cpu_runner.record_from_file(path, button='W' * 26)
        self.assertEqual(cpu_runner.main(['--apply', path, '--output-dir', self.out, '--input-dir', self.inp]), 0)
        self.assertEqual(engine.detect(self.image()).button, 'Brutal')
        self.assertEqual(cpu_runner.main(['--off', '--output-dir', self.out, '--input-dir', self.inp]), 0)
        self.assertIsNone(engine.detect(self.image()))

    def test_picture_mode_redeploys_the_deployed_level(self):
        args = ['--output-dir', self.out, '--input-dir', self.inp]
        self.assertEqual(cpu_runner.main(['--picture', *args]), 1)             # off: nothing to write it with
        cpu_runner.run(self.out, self.record, input_dir=self.inp)
        before = (self.image().to_bytes(), self.slots(menu_files.ART_DIR))
        self.assertEqual(cpu_runner.main(['--picture', *args]), 0)
        self.assertEqual((self.image().to_bytes(), self.slots(menu_files.ART_DIR)), before)
        self.assertEqual(engine.detect(self.image()), self.record)


class ViewTests(unittest.TestCase):
    """The GUI tab's model (``CustomCpu/view.py``)."""

    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.out, self.inp = os.path.join(self.tmp, 'out.dol'), os.path.join(self.tmp, 'in.dol')
        image = dolfile.DolImage(game_dol())
        with open(self.inp, 'wb') as f:
            f.write(image.to_bytes())
        self.record = engine.Record('Custom Level', {'pitch_read': 95, settings.RUN: True})
        engine.apply_dol(image, self.record)
        with open(self.out, 'wb') as f:
            f.write(image.to_bytes())
        self.snap = view.snapshot(self.out, self.inp)

    def test_snapshot_and_pending(self):
        self.assertFalse(self.snap.readonly)
        self.assertEqual(self.snap.deployed, self.record)
        draft = view.Draft.of(self.snap.deployed)
        self.assertFalse(view.pending(self.snap, draft))
        view.set_value(self.snap, draft, 'curve', 60)
        draft.button = 'Brutal'
        self.assertEqual(view.pending_text(self.snap, draft), 'Pending: 1 changed setting and a new button name.')
        draft.on = False
        self.assertEqual(view.pending_text(self.snap, draft), 'Pending: turn the custom CPU level off.')
        off = view.snapshot(self.inp, self.inp)
        self.assertIsNone(off.deployed)
        self.assertFalse(view.pending(off, view.Draft()))
        self.assertTrue(view.pending(off, view.Draft(on=True)))
        missing = view.snapshot(os.path.join(self.tmp, 'none.dol'), self.inp)
        self.assertTrue(missing.readonly)
        self.assertIn('missing', missing.source)

    def test_rows_and_marks(self):
        draft = view.Draft.of(self.snap.deployed)
        view.set_value(self.snap, draft, 'curve', 60)
        view.set_value(self.snap, draft, 'never_walk', True)
        rows = {r.setting.key: r for section in settings.SECTIONS for r in view.rows(self.snap, draft, section)}
        self.assertEqual(len(rows), len(settings.SETTINGS))
        self.assertEqual((rows['pitch_read'].value, rows['pitch_read'].stock, rows['pitch_read'].mark),
                         (95, 85, view.CHANGED))
        self.assertEqual((rows['curve'].value, rows['curve'].mark), (60, view.EDITED))
        self.assertEqual((rows['press_delay'].value, rows['press_delay'].stock, rows['press_delay'].base),
                         (12, 20, 12))                                     # its group is on: the tuned value
        self.assertIsNone(rows['lays_off'].mark)
        self.assertEqual((rows['cap3_1'].locked, rows['cap3_1'].value), (True, 0))
        self.assertEqual(rows['items_lead'].note, '')
        self.assertEqual(view.changed_sections(self.snap, draft), {'Switches', 'Batting', 'Pitching', 'Pitch model'})
        view.reset_section(draft, 'Pitching')
        self.assertNotIn('curve', draft.edits)

    def test_problems_and_warnings(self):
        draft = view.Draft(on=True, button=' x', edits={'star_rare': 90, 'star_never': 70})
        found = view.problems(self.snap, draft)
        self.assertEqual(len(found), 2)
        self.assertTrue(found[0].startswith('The button name cannot start'))
        self.assertEqual(view.problems(self.snap, view.Draft(on=False, button='')), [])     # off: nothing to check
        self.assertIn('Å', view.warnings(view.Draft(on=True, button='Zone Å'))[0])
        self.assertEqual(view.warnings(view.Draft(on=True, button='Zone 8')), [])

    def test_files_and_deploy_command(self):
        draft = view.Draft(True, 'Brutal', {'curve': 60}, {'later': {'5': 1}}, '')
        path = view.save_file(draft, os.path.join(self.tmp, 'mine'))
        self.assertTrue(path.endswith('mine.slgcpu'))
        loaded = view.Draft(on=True)
        view.load_file(self.snap, loaded, path)
        self.assertEqual((loaded.button, loaded.edits, loaded.extra, loaded.name),
                         ('Brutal', {'curve': 60}, {'later': {'5': 1}}, 'mine'))
        self.assertEqual(view.load_levels(path), [4])
        command = view.deploy_command(self.tmp, draft)
        self.assertEqual(command[0], '--custom-cpu')
        self.assertEqual(command[2:], ('--cpu-level', '4', '--cpu-button', 'Brutal'))
        record, _notes = cpu_runner.record_from_file(command[1], 4, 'Brutal')
        self.assertEqual(record, draft.record())                # what Deploy writes is what the tab shows
        self.assertEqual(view.deploy_command(self.tmp, view.Draft(on=False)), ('--custom-cpu-off',))


class RosterRunTests(unittest.TestCase):
    def test_the_roster_runner_carries_the_custom_level(self):
        with mock.patch.object(dhs, 'BASE_SIZE', BASE), tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(gecko_compat, 'confirm', return_value=True):
            out, inp = os.path.join(tmp, '3_Output_Dat'), os.path.join(tmp, '1_Input')
            os.makedirs(out)
            os.makedirs(inp)
            dol_path, _dat_path = game_files(out)
            with open(dol_path, 'rb') as src, open(os.path.join(inp, 'main.dol'), 'wb') as dst:
                dst.write(src.read())
            record = engine.Record('Custom Level', {'curve': 100})
            cpu_runner.run(out, record, input_dir=inp)
            with open(dol_path, 'rb') as f:
                deployed = f.read()
            report = roster_runner.run(out, remove_only=True, input_dir=inp)
            self.assertTrue(any(line.startswith('[custom CPU] custom CPU level: on') for line in report['log']))
            with open(dol_path, 'rb') as f:
                after = f.read()
            self.assertEqual(engine.detect(dolfile.DolImage(after)), record)
            self.assertEqual(after, deployed)


if __name__ == '__main__':
    unittest.main()
