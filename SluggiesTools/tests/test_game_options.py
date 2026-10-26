"""Game options (menu [8]): CPU vs CPU hooks in main.dol, their runner, and survival of roster runs."""

import json
import os
import struct
import tempfile
import unittest
from unittest import mock

from SluggiesTools import gecko_compat
from SluggiesTools.Dol import dolfile, inventory, ppc
from SluggiesTools.GameOptions import game_options as go
from SluggiesTools.GameOptions import runner as options_runner
from SluggiesTools.Roster import dol_hammerspace as dhs
from SluggiesTools.Roster import runner as roster_runner
from SluggiesTools.tests.test_roster_dev import ARENA, BSS, BSS_SIZE, CODE, CODE_SIZE

HOOK_SITES = {h.site: h.stock for o in go.OPTIONS for h in o.hooks} | \
    {p.site: p.stock for o in go.OPTIONS for p in o.patches}
CPU_SITES = {h.site: h.stock for key in ('cpu_vs_cpu', 'cpu_management') for h in go.BY_KEY[key].hooks}


def synthetic_dol(extra_words: dict[int, int] | None = None, blobs=()) -> bytes:
    """test_roster_dev's DOL (T0 with OSInit's arena pairs) plus small sections holding the hook sites (data
    slots 8 and up: a DOL has only 7 text slots, and the hammerspace needs one; the sites only need to be mapped).
    ``extra_words`` ({address: word}) are mapped like the sites, ``blobs`` ((address, bytes)) as sections of their
    own."""
    sections = []
    words = [0x60000000] * (CODE_SIZE // 4)
    for lis_site, (reg, value) in ARENA.items():
        words[(lis_site - CODE) // 4] = ppc.one(lis_site, lambda a: a.lis(reg, ppc.ha(value)))
        words[(lis_site + 4 - CODE) // 4] = ppc.one(lis_site + 4, lambda a: a.addi(reg, reg, ppc.lo(value)))
    sections.append((0, CODE, words))
    sites = HOOK_SITES | (extra_words or {})
    groups = []     # hook sites close together share one section (the data slots run out otherwise)
    for site in sorted(sites):
        if groups and site - groups[-1][-1] < 0x80000:
            groups[-1].append(site)
        else:
            groups.append([site])
    for slot, group in enumerate(groups, start=8):
        base = group[0] - 0x10
        block = [0x60000000] * (-(-(group[-1] + 0x10 - base) // 0x20) * 8)    # 32-byte multiples, like real sections
        for site in group:
            block[(site - base) // 4] = sites[site]
        sections.append((slot, base, block))
    for slot, (address, blob) in enumerate(blobs, start=8 + len(groups)):
        blob += bytes(-len(blob) % 0x20)                                         # 32-byte multiples too
        sections.append((slot, address, list(struct.unpack(f'>{len(blob) // 4}I', blob))))
    assert sections[-1][0] < 17, 'synthetic_dol: out of data slots (the hammerspace needs one)'
    header = bytearray(dolfile.HEADER_SIZE)
    body = b''
    for slot, address, block in sections:
        blob = struct.pack(f'>{len(block)}I', *block)
        struct.pack_into('>I', header, slot * 4, dolfile.HEADER_SIZE + len(body))
        struct.pack_into('>I', header, 0x48 + slot * 4, address)
        struct.pack_into('>I', header, 0x90 + slot * 4, len(blob))
        body += blob
    data = b'\x22' * 0x60
    struct.pack_into('>I', header, 7 * 4, dolfile.HEADER_SIZE + len(body))
    struct.pack_into('>I', header, 0x48 + 7 * 4, 0x80631000)
    struct.pack_into('>I', header, 0x90 + 7 * 4, len(data))
    struct.pack_into('>III', header, 0xD8, BSS, BSS_SIZE, CODE)
    return bytes(header) + body + data


def stub_of(image: dolfile.DolImage, site: int) -> int:
    return ppc.branch_target(image.u32(site), site)


WINDUP = inventory.table('pitchwindup')
LOW_CHARGES = {0x08: 7.0, 0x0F: 5.0}            # the rows below the precharge floor; the others 9.0
MOVED_WINDUP = 0x807D0000                       # where a roster run might have moved the table


def windup_rows(rows: int = 0x66) -> bytes:
    return b''.join(struct.pack('>3f', LOW_CHARGES.get(cid, 9.0), 1.0, 2.0) for cid in range(rows))


def precharge_dol(table: int = WINDUP.address) -> bytes:
    """synthetic_dol plus the pitchwindup table (at its stock address, and at ``table`` if it is moved) and the
    first lis/addi pair that points the code at ``table``."""
    lis_site, low_site = WINDUP.all_pairs[0]
    pair = {lis_site: ppc.one(lis_site, lambda a: a.lis(3, ppc.ha(table))),
            low_site: ppc.one(low_site, lambda a: a.addi(3, 3, ppc.lo(table)))}
    blobs = [(WINDUP.address, windup_rows())] + ([(table, windup_rows())] if table != WINDUP.address else [])
    return synthetic_dol(pair, blobs)


CHARFLOATS = inventory.table('charfloats')
UNUSED_HEIGHTS = {0x49: 1.1, 0x4A: 1.3, 0x4B: 0.15, 0x4C: 1.0}     # stock +0x1C of Toad, Pianta, Kritter, Koopa


def charfloats_rows(rows: int) -> bytes:
    out = bytearray()
    for cid in range(rows):
        row = bytearray(CHARFLOATS.row_size)
        struct.pack_into('>f', row, 0x1C, UNUSED_HEIGHTS.get(cid, 0.9))
        out += row
    return bytes(out)


def strike_zone_dol(table: int = CHARFLOATS.address) -> bytes:
    """synthetic_dol plus the charfloats table (stock, and at ``table`` with 0x100 rows if it is moved) and the first
    lis/addi pair that points the code at ``table``."""
    lis_site, low_site = CHARFLOATS.all_pairs[0]
    pair = {lis_site: ppc.one(lis_site, lambda a: a.lis(3, ppc.ha(table))),
            low_site: ppc.one(low_site, lambda a: a.addi(3, 3, ppc.lo(table)))}
    blobs = [(CHARFLOATS.address, charfloats_rows(0x66))]
    if table != CHARFLOATS.address:
        blobs.append((table, charfloats_rows(0x100)))
    return synthetic_dol(pair, blobs)


def height(image: dolfile.DolImage, cid: int, table: int = CHARFLOATS.address) -> bytes:
    return image.read(table + CHARFLOATS.row_size * cid + 0x1C, 4)


def charge(image: dolfile.DolImage, cid: int, table: int = WINDUP.address) -> float:
    return struct.unpack('>f', image.read(table + WINDUP.row_size * cid, 4))[0]


class GameOptionTests(unittest.TestCase):
    def setUp(self):
        self.image = dolfile.DolImage(synthetic_dol())

    def test_apply_detect_remove(self):
        self.assertEqual(go.detect(self.image), [])
        go.apply(self.image, ['cpu_vs_cpu', 'cpu_management'])
        self.assertEqual(go.detect(self.image), ['cpu_vs_cpu', 'cpu_management'])
        for site in CPU_SITES:
            self.assertTrue(dhs.TEXT_BASE <= stub_of(self.image, site) < dhs.TEXT_LIMIT)
        go.remove(self.image, ['cpu_management'])
        self.assertEqual(go.detect(self.image), ['cpu_vs_cpu'])
        for h in go.BY_KEY['cpu_management'].hooks:
            self.assertEqual(self.image.u32(h.site), h.stock)

    def test_autopicker_patches_in_place_without_sections(self):
        stock = self.image.to_bytes()
        log = go.apply(self.image, ['autopicker'])
        self.assertEqual(go.detect(self.image), ['autopicker'])
        self.assertIn('0x8006AED8', log[-1])
        for p in go.AUTOPICKER_PATCHES:
            self.assertEqual(self.image.u32(p.site), p.new)
        self.assertIsNone(dhs.DolHammerspace.open(self.image))
        self.assertTrue(gecko_compat.status(self.image.to_bytes()).compatible)
        self.assertEqual(go.apply(self.image, ['autopicker']), [go.BY_KEY['autopicker'].title + ': already on'])
        go.remove(self.image, ['autopicker'])
        self.assertEqual(self.image.to_bytes(), stock)

    def test_autopicker_branch_goes_to_the_reset_exit(self):
        p = go.AUTOPICKER_PATCHES[2]
        self.assertEqual(ppc.branch_target(p.new, p.site), 0x8006BA58)

    def test_autopicker_survives_turning_hooked_options_off(self):
        go.apply(self.image, ['autopicker', 'cpu_vs_cpu'])
        go.remove(self.image, ['cpu_vs_cpu'])     # no roster: the sections go, the patches stay
        self.assertEqual(go.detect(self.image), ['autopicker'])
        self.assertIsNone(dhs.DolHammerspace.open(self.image))

    def test_autopicker_refuses_a_foreign_word(self):
        self.image.write_word(0x800802B4, 0x12345678)
        with self.assertRaises(go.GameOptionError):
            go.apply(self.image, ['autopicker'])

    def test_stubs_replay_the_stock_word_and_return(self):
        go.apply(self.image, ['cpu_vs_cpu', 'cpu_management'])
        for site, stock in CPU_SITES.items():
            stub = stub_of(self.image, site)
            words = [self.image.u32(stub + i) for i in range(0, 0x40, 4)]
            back = next(i for i, w in enumerate(words) if ppc.branch_target(w, stub + i * 4) == site + 4)
            self.assertEqual(words[back - 1], stock, hex(site))

    def test_management_writes_through_r13_globals(self):
        go.apply(self.image, ['cpu_management'])
        flip = stub_of(self.image, go.FLIP_SITE)
        words = [self.image.u32(flip + i) for i in range(0, 0x2C, 4)]
        self.assertIn(ppc.one(0, lambda a: a.lwz(12, go.TEAM_MAP, 13)), words)
        self.assertIn(ppc.one(0, lambda a: a.xori(11, 6, 1)), words)
        self.assertIn(ppc.one(0, lambda a: a.stb(11, 4, 12)), words)

    def test_player_heap_stub_sets_the_size_and_returns(self):
        go.apply(self.image, ['player_heap_32'])
        stub = stub_of(self.image, go.HEAP_SITE)
        words = [self.image.u32(stub + i) for i in range(0, 12, 4)]
        self.assertEqual(words[:2], [ppc.one(0, lambda a: a.lis(5, 0xE)), ppc.one(0, lambda a: a.addi(5, 5, -0x3800))])
        self.assertEqual(ppc.branch_target(words[2], stub + 8), go.HEAP_SITE + 4)
        self.assertEqual(0xE0000 - 0x3800, go.PLAYER_HEAP_STOCK + 32 * 1024)

    def test_player_heap_levels_replace_each_other(self):
        go.apply(self.image, ['cpu_vs_cpu', 'player_heap_32'])
        self.assertEqual(go.detect(self.image), ['cpu_vs_cpu', 'player_heap_32'])
        log = go.apply(self.image, ['player_heap_48'])
        self.assertIn('off (replaced)', log[0])
        self.assertEqual(go.detect(self.image), ['cpu_vs_cpu', 'player_heap_48'])
        self.assertIn('already off', go.remove(self.image, ['player_heap_32'])[0])   # another level: untouched
        self.assertEqual(go.detect(self.image), ['cpu_vs_cpu', 'player_heap_48'])
        go.remove(self.image, [o.key for o in go.OPTIONS if o.group == 'player_heap'])
        self.assertEqual(go.detect(self.image), ['cpu_vs_cpu'])
        self.assertEqual(self.image.u32(go.HEAP_SITE), go.HEAP_STOCK_WORD)

    def test_player_heap_levels_cannot_both_be_turned_on(self):
        with self.assertRaises(go.GameOptionError):
            go.apply(self.image, ['player_heap_32', 'player_heap_big_1024'])

    def test_big_heap_hooks_all_three_sites_and_falls_back(self):
        go.apply(self.image, ['player_heap_big_2048'])
        self.assertEqual(go.detect(self.image), ['player_heap_big_2048'])
        heap = stub_of(self.image, go.HEAP_SITE)
        words = [self.image.u32(heap + i) for i in range(0, 4 * 10, 4)]
        safe = go.PLAYER_HEAP_STOCK + go.PLAYER_HEAP_SAFE_KB * 1024
        big = go.PLAYER_HEAP_STOCK + 2048 * 1024
        self.assertEqual(words[:2], [ppc.one(0, lambda a: a.lis(5, ppc.ha(safe))),
                                     ppc.one(0, lambda a: a.addi(5, 5, ppc.lo(safe)))])     # set first: the fallback
        self.assertIn(ppc.one(0, lambda a: a.lwz(12, ppc.lo(go.MEM2_SIZE_GLOBAL), 12)), words)
        self.assertIn(ppc.one(0, lambda a: a.lis(5, ppc.ha(big))), words)
        root = stub_of(self.image, go.ROOT_SIZE_SITE)
        self.assertEqual(self.image.u32(root), go.ROOT_SIZE_STOCK_WORD)                 # stock size first
        table = stub_of(self.image, go.TABLE_READ_SITE)
        self.assertEqual(self.image.u32(table), go.TABLE_READ_STOCK_WORD)
        for hook in go.BY_KEY['player_heap_big_2048'].hooks:
            site, stub = hook.site, stub_of(self.image, hook.site)
            tail = [self.image.u32(stub + i) for i in range(0, len(hook.stub(stub, site)), 4)]
            self.assertTrue(any(ppc.branch_target(w, stub + 4 * i) == site + 4 for i, w in enumerate(tail)))

    def test_switching_from_big_to_stock_memory_level_restores_the_heap_sites(self):
        go.apply(self.image, ['player_heap_big_512'])
        go.apply(self.image, ['player_heap_48'])
        self.assertEqual(go.detect(self.image), ['player_heap_48'])
        self.assertEqual(self.image.u32(go.ROOT_SIZE_SITE), go.ROOT_SIZE_STOCK_WORD)
        self.assertEqual(self.image.u32(go.TABLE_READ_SITE), go.TABLE_READ_STOCK_WORD)

    def test_player_heap_off_restores_the_stock_dol(self):
        original = self.image.to_bytes()
        go.apply(self.image, ['player_heap_48'])
        go.remove(self.image, ['player_heap_48'])
        self.assertEqual(self.image.to_bytes(), original)

    def test_off_without_roster_restores_the_stock_dol(self):
        original = self.image.to_bytes()
        go.apply(self.image, ['cpu_vs_cpu', 'cpu_management'])
        log = go.remove(self.image, ['cpu_vs_cpu', 'cpu_management'])
        self.assertIn('sections are removed', log[-1])
        self.assertEqual(self.image.to_bytes(), original)
        self.assertIsNone(dhs.DolHammerspace.open(self.image))

    def test_off_without_roster_rebuilds_the_options_still_on(self):
        go.apply(self.image, ['cpu_vs_cpu'])
        only_cpu = self.image.to_bytes()
        go.apply(self.image, ['cpu_management'])
        go.remove(self.image, ['cpu_management'])
        self.assertEqual(go.detect(self.image), ['cpu_vs_cpu'])
        self.assertEqual(self.image.to_bytes(), only_cpu)        # no unused stubs left behind

    def test_off_with_roster_data_keeps_the_sections(self):
        go.apply(self.image, ['cpu_vs_cpu'])
        hs = dhs.DolHammerspace.open(self.image)
        hs.data.put(b'\x01' * 0x20)                              # roster tables
        hs.commit()
        go.remove(self.image, ['cpu_vs_cpu'])
        self.assertEqual(go.detect(self.image), [])
        self.assertTrue(dhs.DolHammerspace.open(self.image).has_data)
        with self.assertRaisesRegex(dhs.HammerspaceError, 'roster data'):
            dhs.remove_sections(self.image)

    def test_apply_twice_is_a_no_op(self):
        go.apply(self.image, ['cpu_vs_cpu'])
        once = self.image.to_bytes()
        self.assertIn('already on', go.apply(self.image, ['cpu_vs_cpu'])[0])
        self.assertEqual(self.image.to_bytes(), once)

    def test_cpu_stub_masks_the_held_buttons(self):
        # The held word carries stick direction bits (0x000F); an exact compare missed A + Minus
        # whenever the axis was off-center (2026-10-08).
        go.apply(self.image, ['cpu_vs_cpu'])
        stub = stub_of(self.image, go.CPU_SITE)
        words = [self.image.u32(stub + i) for i in range(0, 0x24, 4)]
        mask = ppc.one(0, lambda a: a.andi_(5, 5, go.BUTTONS))
        compare = ppc.one(0, lambda a: a.cmpwi(5, go.BUTTONS))
        self.assertIn(mask, words)
        self.assertEqual(words.index(compare), words.index(mask) + 1)

    def test_outdated_stub_is_replaced(self):
        go.apply(self.image, ['cpu_vs_cpu'])
        stub = stub_of(self.image, go.CPU_SITE)
        self.image.write_word(stub + 0xC, ppc.one(stub + 0xC, lambda a: a.cmpwi(5, go.BUTTONS)))  # old exact check
        log = go.apply(self.image, ['cpu_vs_cpu'])
        self.assertIn('outdated stub replaced', log[0])
        new_stub = stub_of(self.image, go.CPU_SITE)
        self.assertNotEqual(new_stub, stub)
        self.assertTrue(go._stub_current(self.image, go.BY_KEY['cpu_vs_cpu'].hooks[0]))
        self.assertIn('already on', go.apply(self.image, ['cpu_vs_cpu'])[0])

    def test_foreign_patch_is_refused(self):
        self.image.write_word(go.CPU_SITE, 0x12345678)
        with self.assertRaisesRegex(go.GameOptionError, 'another patch'):
            go.apply(self.image, ['cpu_vs_cpu'])

    def test_unknown_option(self):
        with self.assertRaisesRegex(go.GameOptionError, 'unknown game option'):
            go.apply(self.image, ['nope'])


class CpuVsCpuExtrasTests(unittest.TestCase):
    """Ports of the community CPU vs CPU Gecko codes: the close play stub and the in-place fixes."""
    FIXES = ['triple_take', 'strikeout_camera', 'outfield_dives', 'no_precharge', 'star_pitch_buff']

    def setUp(self):
        self.image = dolfile.DolImage(precharge_dol())

    def test_mftb(self):
        self.assertEqual(ppc.one(0, lambda a: a.mftb(3)), 0x7C6C42E6)

    def test_close_play_stub_reads_no_heap_address(self):
        go.apply(self.image, ['close_play_30'])
        self.assertEqual(go.detect(self.image), ['close_play_30'])
        site = go.CLOSE_PLAY_SITE
        stub = stub_of(self.image, site)
        words = [self.image.u32(stub + i) for i in range(0, len(go.BY_KEY['close_play_30'].hooks[0].stub(stub, site)),
                                                        4)]
        one = lambda build: ppc.one(0, build)                                   # noqa: E731
        self.assertEqual(words[0], one(lambda a: a.lwz(12, go.SETTINGS, 13)))   # CPU vs CPU through r13
        self.assertIn(one(lambda a: a.lbz(11, go.CLOSE_PLAY_ABILITY, 31)), words)
        self.assertIn(one(lambda a: a.mftb(11)), words)
        self.assertIn(one(lambda a: a.cmplwi(11, 30)), words)
        self.assertIn(one(lambda a: a.li(12, go.CLOSE_PLAY_STOCK_VALUE)), words)   # human matches: stock entry
        self.assertIn(one(lambda a: a.sth(12, ppc.lo(go.CLOSE_PLAY_ENTRY), 11)), words)
        self.assertEqual(words[-2], go.CLOSE_PLAY_STOCK)
        self.assertEqual(ppc.branch_target(words[-1], stub + 4 * (len(words) - 1)), site + 4)
        lis = [w & 0xFFFF for w in words if w >> 26 == 15]
        self.assertEqual(lis, [ppc.ha(go.CLOSE_PLAY_ENTRY)])    # no stock heap address (0x811F..., 0x900D...)

    def test_close_play_levels_replace_each_other(self):
        go.apply(self.image, ['cpu_vs_cpu', 'close_play_30'])
        log = go.apply(self.image, ['close_play_70'])
        self.assertIn('off (replaced)', log[0])
        self.assertEqual(go.detect(self.image), ['cpu_vs_cpu', 'close_play_70'])
        with self.assertRaises(go.GameOptionError):
            go.apply(self.image, ['close_play_10', 'close_play_20'])

    def test_fixes_patch_in_place_without_sections(self):
        stock = self.image.to_bytes()
        go.apply(self.image, self.FIXES)
        self.assertEqual(go.detect(self.image), self.FIXES)
        self.assertIsNone(dhs.DolHammerspace.open(self.image))
        self.assertTrue(gecko_compat.status(self.image.to_bytes()).compatible)
        self.assertIn('already on', go.apply(self.image, ['triple_take'])[0])
        go.remove(self.image, self.FIXES, dolfile.DolImage(stock))
        self.assertEqual(self.image.to_bytes(), stock)

    def test_fix_words(self):
        one = lambda build: ppc.one(0, build)                                   # noqa: E731
        for p in go.TRIPLE_TAKE_PATCHES:        # the `beq` the Gecko code's r0 = 1 never let branch
            self.assertEqual(p.stock >> 16, 0x4182)
            self.assertEqual(p.new, one(lambda a: a.nop()))
        self.assertEqual(go.STRIKEOUT_CAMERA_PATCHES[0].new, one(lambda a: a.li(0, 0)))
        self.assertEqual(go.OUTFIELD_DIVE_PATCHES[0].new, one(lambda a: a.cmplwi(0, 6)))
        # the Gecko code's three 02 lines (address: halfword); the other halfword of each word stays stock
        gecko = {0x80624956: 0xF703, 0x8062495E: 0xF806, 0x8062493E: 0xF904}
        for p in go.STAR_PITCH_PATCHES:
            self.assertEqual(p.new >> 16, p.stock >> 16)
            self.assertEqual(p.new & 0xFFFF, gecko[p.site + 2])

    def test_precharge_raises_only_rows_below_the_floor(self):
        log = go.apply(self.image, ['no_precharge'])
        self.assertIn('rows raised to 8: 0x08, 0x0F', log[-1])
        self.assertEqual([charge(self.image, cid) for cid in (0x00, 0x08, 0x0F, 0x64)], [9.0, 8.0, 8.0, 9.0])
        self.assertEqual(self.image.read(WINDUP.address + WINDUP.row_size * 8 + 4, 8), struct.pack('>2f', 1.0, 2.0))

    def test_precharge_off_puts_the_stock_values_back(self):
        stock = dolfile.DolImage(self.image.to_bytes())
        go.apply(self.image, ['no_precharge'])
        with self.assertRaisesRegex(go.GameOptionError, '1_Input'):
            go.remove(self.image, ['no_precharge'])            # the stock values come from 1_Input
        self.assertIn('off', go.remove(self.image, ['no_precharge'], stock)[0])
        self.assertEqual(self.image.to_bytes(), stock.to_bytes())

    def test_precharge_follows_a_moved_table(self):
        image = dolfile.DolImage(precharge_dol(MOVED_WINDUP))
        go.apply(image, ['no_precharge'])
        self.assertEqual(go.detect(image), ['no_precharge'])
        self.assertEqual(charge(image, 0x08, MOVED_WINDUP), 8.0)
        self.assertEqual(charge(image, 0x08), 7.0)                   # the stale stock copy: untouched

    def test_precharge_without_its_table_is_off_and_refused(self):
        image = dolfile.DolImage(synthetic_dol())                   # no lis pair: the table cannot be found
        self.assertEqual(go.detect(image), [])
        with self.assertRaisesRegex(go.GameOptionError, 'pitchwindup'):
            go.apply(image, ['no_precharge'])


class StrikeZoneTests(unittest.TestCase):
    """The "Fix Unused Character Strikezone Heights" Gecko code as a game option that follows the roster."""
    KEY = 'unused_strike_zones'
    GECKO = {0x80629C38: 0x3F4CCCCD, 0x80629C5C: 0x3F733333, 0x80629C80: 0x3F933333, 0x80629CA4: 0x3F666666}

    def test_stock_layout_gets_the_gecko_words(self):
        image = dolfile.DolImage(strike_zone_dol())
        stock = image.to_bytes()
        self.assertNotIn(self.KEY, go.detect(image))
        go.apply(image, [self.KEY])
        self.assertIn(self.KEY, go.detect(image))
        self.assertEqual({a: image.u32(a) for a in self.GECKO}, self.GECKO)
        self.assertEqual(height(image, 0x48), struct.pack('>f', 0.9))               # others untouched
        self.assertIsNone(dhs.DolHammerspace.open(image))                            # no code area
        with self.assertRaisesRegex(go.GameOptionError, '1_Input'):
            go.remove(image, [self.KEY])
        go.remove(image, [self.KEY], dolfile.DolImage(stock))
        self.assertEqual(image.to_bytes(), stock)

    def test_follows_a_moved_table_and_the_rows_copied_from_the_unused_characters(self):
        from SluggiesTools.Roster import ids
        from SluggiesTools.StatEditor import bridge
        moved = MOVED_WINDUP
        image = dolfile.DolImage(strike_zone_dol(moved))
        vanilla = dolfile.DolImage(strike_zone_dol())
        # 0x66 plays with Toad's stats; Pianta adopts Mario's (0x00), so its row is Mario's and stays
        roster = bridge.Roster({}, [ids.NewId(0x66, 0x49, 0, 0, None, 0x49)], {0x4A: 0x00})
        with mock.patch.object(go, '_roster', return_value=roster):
            log = go.apply(image, [self.KEY])
            self.assertIn('0x49, 0x4B, 0x4C, 0x66', log[-1])
            self.assertEqual([height(image, cid, moved) for cid in (0x49, 0x66, 0x4A)],
                             [struct.pack('>f', 0.8), struct.pack('>f', 0.8), struct.pack('>f', 1.3)])  # 0x4A: untouched
            self.assertEqual(height(image, 0x49), struct.pack('>f', 1.1))           # the stale stock copy
            self.assertIn(self.KEY, go.detect(image))
            go.remove(image, [self.KEY], vanilla)
            self.assertEqual(height(image, 0x66, moved), struct.pack('>f', 1.1))    # Toad's own value again


STATS = inventory.table('stats')


def stats_rows(rows: int) -> bytes:
    """A stats table (header + rows): bytes 0-1 the row's own ID, the stock captains' "captain" byte set, chemistry 1."""
    out = bytearray(STATS.header)
    for cid in range(rows):
        row = bytearray(STATS.row_size)
        struct.pack_into('>H', row, 0, cid)
        row[0x07] = 1 if cid in go.CAPTAIN_IDS else 0
        row[0x28:0x28 + 0x65] = bytes([1]) * 0x65
        out += row
    return bytes(out)


def stats_dol(table: int = STATS.address) -> bytes:
    """synthetic_dol plus the stats table (stock, and at ``table`` with 0x100 rows if it is moved) and the first
    lis/addi pair that points the code at ``table``."""
    lis_site, low_site = STATS.all_pairs[0]
    pair = {lis_site: ppc.one(lis_site, lambda a: a.lis(3, ppc.ha(table))),
            low_site: ppc.one(low_site, lambda a: a.addi(3, 3, ppc.lo(table)))}
    blobs = [(STATS.address, stats_rows(0x66))]
    if table != STATS.address:
        blobs.append((table, stats_rows(0x100)))
    return synthetic_dol(pair, blobs)


def stats_byte(image: dolfile.DolImage, cid: int, offset: int, table: int = STATS.address) -> int:
    return image.read(table + STATS.header + STATS.row_size * cid + offset, 1)[0]


class CharacterExtrasTests(unittest.TestCase):
    """Ports of the "Characters and Chemistry" Gecko codes (Gameplay tab, Extras)."""

    def setUp(self):
        self.image = dolfile.DolImage(stats_dol())
        self.stock = self.image.to_bytes()

    def test_gecko_addresses_are_the_stock_stats_rows(self):
        rows = STATS.address + STATS.header
        self.assertEqual(rows + 0x07, 0x806CE9AF)                                        # the 08 line's start
        self.assertEqual([rows + STATS.row_size * c + 0x28 + c for c in (0x00, 0x13)], [0x806CE9D0, 0x806CF46D])

    def test_duplicate_characters_patch_in_place(self):
        go.apply(self.image, ['duplicate_characters'])
        self.assertEqual(go.detect(self.image), ['duplicate_characters'])
        self.assertIsNone(dhs.DolHammerspace.open(self.image))
        self.assertEqual(self.image.u32(0x80071B10), 0x38600000)
        go.remove(self.image, ['duplicate_characters'])
        self.assertEqual(self.image.to_bytes(), self.stock)

    def test_duplicate_captains_stub_copies_slot_zero(self):
        go.apply(self.image, ['duplicate_captains', 'duplicate_characters'])
        self.assertEqual(go.detect(self.image), ['duplicate_characters', 'duplicate_captains'])
        site = go.DUPLICATE_CAPTAIN_SITE
        stub = stub_of(self.image, site)
        words = [self.image.u32(stub + 4 * i) for i in range(7)]
        self.assertEqual(words[:6], [0x7C60EA14, 0x2C1B0000, 0x41820010, 0x7C050378, 0xA8A50000, 0xB0A30000])
        self.assertEqual(ppc.branch_target(words[6], stub + 24), site + 4)
        go.remove(self.image, ['duplicate_captains', 'duplicate_characters'])
        self.assertEqual(self.image.to_bytes(), self.stock)

    def test_all_captains_sets_every_row(self):
        go.apply(self.image, ['all_captains'])
        self.assertEqual(go.detect(self.image), ['all_captains'])
        self.assertEqual({stats_byte(self.image, cid, 0x07) for cid in range(0x65)}, {1})
        with self.assertRaisesRegex(go.GameOptionError, '1_Input'):
            go.remove(self.image, ['all_captains'])
        go.remove(self.image, ['all_captains'], dolfile.DolImage(self.stock))
        self.assertEqual(self.image.to_bytes(), self.stock)

    def test_captain_self_chemistry_only_on_the_diagonal(self):
        go.apply(self.image, ['captain_self_chemistry'])
        self.assertEqual(go.detect(self.image), ['captain_self_chemistry'])
        self.assertEqual([stats_byte(self.image, c, 0x28 + c) for c in (0x00, 0x13, 0x07)], [2, 2, 1])
        self.assertEqual(stats_byte(self.image, 0x00, 0x28 + 0x01), 1)
        go.remove(self.image, ['captain_self_chemistry'], dolfile.DolImage(self.stock))
        self.assertEqual(self.image.to_bytes(), self.stock)

    def test_follow_a_moved_table_and_new_ids(self):
        from SluggiesTools.Roster import ids
        from SluggiesTools.StatEditor import bridge
        moved = MOVED_WINDUP
        image = dolfile.DolImage(stats_dol(moved))
        vanilla = dolfile.DolImage(stats_dol())
        roster = bridge.Roster({}, [ids.NewId(0x66, 0x07, 0, 0, None, 0x07)], {})
        with mock.patch.object(go, '_roster', return_value=roster):
            go.apply(image, ['all_captains', 'captain_self_chemistry'])
            self.assertEqual(go.detect(image), ['all_captains', 'captain_self_chemistry'])
            self.assertEqual([stats_byte(image, c, 0x07, moved) for c in (0x07, 0x66)], [1, 1])
            self.assertEqual(stats_byte(image, 0x07, 0x07), 0)                       # the stale stock copy
            self.assertEqual(stats_byte(image, 0x00, 0x28, moved), 2)
            go.remove(image, ['all_captains', 'captain_self_chemistry'], vanilla)
            self.assertEqual([stats_byte(image, c, 0x07, moved) for c in (0x00, 0x07, 0x66)], [1, 0, 0])
            self.assertEqual(stats_byte(image, 0x00, 0x28, moved), 1)
            self.assertEqual(go.detect(image), [])


class NinetyNineZeroTests(unittest.TestCase):
    """Ports of the "99-0 Tools" Gecko codes (Gameplay tab, Extras)."""

    def setUp(self):
        self.image = dolfile.DolImage(synthetic_dol())
        self.stock = self.image.to_bytes()

    def test_plain_patches_in_place(self):
        go.apply(self.image, ['always_away', 'extra_innings'])
        self.assertEqual(go.detect(self.image), ['always_away', 'extra_innings'])
        self.assertIsNone(dhs.DolHammerspace.open(self.image))
        self.assertEqual([self.image.u32(a) for a in (0x802CEAA4, 0x8017F694)], [0x38000000, 0x38000063])
        go.remove(self.image, ['always_away', 'extra_innings'])
        self.assertEqual(self.image.to_bytes(), self.stock)

    def test_score_999_stub_is_the_gecko_function(self):
        go.apply(self.image, ['score_999'])
        self.assertEqual(go.detect(self.image), ['score_999'])
        site = go.SCORE_999_SITE
        stub = stub_of(self.image, site)
        words = [self.image.u32(stub + 4 * i) for i in range(21)]
        # the Gecko code's inner function (0x8048A01C), with r0/r4/r5 for r9/r10 and no stack frame
        self.assertEqual(words[:3], [0x2C1C0001, 0x40820030, 0xA083FFEA])      # cmpwi r28,1; bne; lhz r4,-0x16(r3)
        self.assertEqual(words[19], go.SCORE_999_STOCK)
        self.assertEqual(ppc.branch_target(words[20], stub + 80), site + 4)
        self.assertEqual([self.image.u32(p.site) for p in go.SCORE_999_PATCHES],
                         [0x2C1B0000, 0x7C000378, 0x38000005, 0x38C00004])
        go.remove(self.image, ['score_999'])
        self.assertEqual(self.image.to_bytes(), self.stock)


class MiscellaneousTests(unittest.TestCase):
    """Ports of the "Miscellaneous" Gecko codes (Gameplay tab, Extras)."""

    def setUp(self):
        self.image = dolfile.DolImage(synthetic_dol())
        self.stock = self.image.to_bytes()

    @staticmethod
    def one(build) -> int:
        return ppc.one(0, build)

    def stub_words(self, site: int) -> list[int]:
        stub = stub_of(self.image, site)
        words = []
        while not words or ppc.branch_target(words[-1], stub + 4 * (len(words) - 1)) != site + 4:
            words.append(self.image.u32(stub + 4 * len(words)))
        return words

    def test_mercy_rule_levels_replace_each_other_in_place(self):
        self.assertNotIn(go.mercy_rule_key(go.MERCY_RULE_STOCK_RUNS), go.BY_KEY)     # the stock lead is "off"
        go.apply(self.image, ['mercy_rule_15'])
        self.assertEqual(go.detect(self.image), ['mercy_rule_15'])
        self.assertEqual(self.image.u32(go.MERCY_RULE_SITE), 0x3800000F)            # li r0,15
        self.assertIsNone(dhs.DolHammerspace.open(self.image))
        log = go.apply(self.image, ['mercy_rule_3'])
        self.assertIn('off (replaced)', ' '.join(log))
        self.assertEqual(go.detect(self.image), ['mercy_rule_3'])
        with self.assertRaisesRegex(go.GameOptionError, 'both be on'):
            go.apply(self.image, ['mercy_rule_20', 'mercy_rule_30'])
        go.remove(self.image, ['mercy_rule_3'])
        self.assertEqual(self.image.to_bytes(), self.stock)

    def test_night_music_stubs_pick_the_night_tracks(self):
        go.apply(self.image, ['night_music'])
        self.assertEqual(go.detect(self.image), ['night_music'])
        for site, stock in go.NIGHT_MUSIC_HOOKS:
            words = self.stub_words(site)
            self.assertEqual(words[0], stock)                                       # the table read first
            self.assertIn(self.one(lambda a: a.lbz(12, go.SETTINGS_NIGHT, 12)), words)
            self.assertIn(self.one(lambda a: a.cmpwi(12, 0, cr=1)), words)               # cr0 is the site's
            self.assertNotIn(self.one(lambda a: a.cmpwi(12, 0)), words)
            for stadium, track in go.NIGHT_MUSIC:
                self.assertIn(self.one(lambda a: a.cmpwi(0, stadium * 4, cr=1)), words)
                self.assertIn(self.one(lambda a: a.li(5, track)), words)
        go.remove(self.image, ['night_music'])
        self.assertEqual(self.image.to_bytes(), self.stock)

    def test_challenge_coins_stub_reads_no_heap_address(self):
        go.apply(self.image, ['challenge_coins'])
        self.assertEqual(go.detect(self.image), ['challenge_coins'])
        words = self.stub_words(go.COINS_SITE)
        self.assertEqual(words[-2], go.COINS_STOCK)                                 # the epilogue's addi r11
        self.assertFalse([w for w in words if w >> 26 == 15])                       # no lis: r13 globals only
        for build in (lambda a: a.lwz(12, go.PAD_MANAGER, 13), lambda a: a.lwz(12, go.CHALLENGE_MANAGER, 13),
                      lambda a: a.lwz(12, go.CHALLENGE_STRUCT, 13), lambda a: a.andi_(10, 10, 0x0500),
                      lambda a: a.li(10, 999), lambda a: a.sth(10, go.CHALLENGE_COINS, 12)):
            self.assertIn(self.one(build), words)
        self.assertFalse([w for w in words if (w >> 21) & 31 == 11 and w != go.COINS_STOCK])    # r11 kept
        go.remove(self.image, ['challenge_coins'])
        self.assertEqual(self.image.to_bytes(), self.stock)


class CheatTests(unittest.TestCase):
    """Ports of the [TNTkryzt] codes of Dolphin's code list (Options tab, Cheats)."""

    # The functions the 06 codes write to the MEM1 tail, at their Gecko addresses.
    GECKO_FUNCTIONS = {
        'double_home_runs': (0x817FED64, 'A81C006C817C0A942C0B0001408200081C0000024A981020'),
        'reset_outs': (0x817FED14, '817C00342C0B00014082001CA17E00482C0B060040820010'
                                   '38000000981E00094A933500881E00094A9334F8'),
        'fast_movement': (0x817FED40, '2C050000418200188165FD602C0B00014182000C3D603EF0916300ECC3E300EC4A91C8D4'),
    }

    def setUp(self):
        self.image = dolfile.DolImage(synthetic_dol())
        self.stock = self.image.to_bytes()

    def test_hook_stubs_are_the_gecko_functions(self):
        for key, (address, gecko) in self.GECKO_FUNCTIONS.items():
            hook, = go.BY_KEY[key].hooks
            self.assertEqual(hook.stub(address, hook.site).assemble().hex().upper(), gecko, key)

    def test_toy_field_coins_patches_in_place(self):
        go.apply(self.image, ['toy_field_coins'])
        self.assertEqual(go.detect(self.image), ['toy_field_coins'])
        self.assertIsNone(dhs.DolHammerspace.open(self.image))
        self.assertEqual(self.image.u32(0x804CA690), 0x90180020)
        go.remove(self.image, ['toy_field_coins'])
        self.assertEqual(self.image.to_bytes(), self.stock)

    def test_save_data_stubs_read_no_heap_address(self):
        keys = ['all_stars', 'all_items', 'challenge_coins']        # the three per-frame hooks share the epilogue
        go.apply(self.image, keys)
        self.assertEqual(go.detect(self.image), ['challenge_coins', 'all_stars', 'all_items'])
        one = lambda build: ppc.one(0, build)      # noqa: E731
        stars = MiscellaneousTests.stub_words(self, go.STARS_SITE)
        self.assertEqual(stars[0], one(lambda a: a.lwz(12, go.PROGRESS, 13)))
        self.assertEqual(stars[-2], go.STARS_STOCK)
        self.assertIn(one(lambda a: a.stb(11, 0x197, 12)), stars)
        self.assertEqual([w for w in stars if w >> 26 == 36], [one(lambda a, o=o: a.stw(11, o, 12))
                                                              for o in range(0x198, 0x1C0, 4)])
        items = MiscellaneousTests.stub_words(self, go.ITEMS_SITE)
        self.assertEqual(items[:2], [go.ITEMS_STOCK, one(lambda a: a.lwz(12, go.CHALLENGE_STRUCT, 13))])
        self.assertEqual(items[4:6], [one(lambda a: a.lis(11, 0x6363)), one(lambda a: a.addi(11, 11, 0x6363))])
        self.assertEqual(items[6:9], [one(lambda a, o=o: a.stw(11, o, 12)) for o in (0x2D0, 0x2D4, 0x2D8)])
        for words in (stars, items):         # the only lis is the fill value's: r13 globals, no heap address
            self.assertEqual(len([w for w in words if w >> 26 == 15]), 1)
        go.remove(self.image, keys)
        self.assertEqual(self.image.to_bytes(), self.stock)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.dict(os.environ, {gecko_compat.ACCEPT_ENV: '1'}))   # options add DOL sections
        self.input_dir = os.path.join(self.tmp, '1_Input')
        os.makedirs(self.input_dir)
        self.dol_path = os.path.join(self.tmp, 'main.dol')
        self.original = synthetic_dol()
        for path in (self.dol_path, os.path.join(self.input_dir, 'main.dol')):
            with open(path, 'wb') as f:
                f.write(self.original)
        self.config = os.path.join(self.tmp, 'roster.json')
        with open(self.config, 'w') as f:
            json.dump({'version': 1}, f)

    def image(self) -> dolfile.DolImage:
        with open(self.dol_path, 'rb') as f:
            return dolfile.DolImage(f.read())

    def test_on_off_and_dry_run(self):
        options_runner.run(self.tmp, on=['cpu_vs_cpu'], dry_run=True)
        self.assertEqual(go.detect(self.image()), [])
        log = options_runner.run(self.tmp, on=['cpu_vs_cpu'])
        self.assertEqual(go.detect(self.image()), ['cpu_vs_cpu'])
        self.assertIn('cpu_management: off', ' '.join(log))
        options_runner.run(self.tmp, off=['cpu_vs_cpu'])
        self.assertEqual(go.detect(self.image()), [])

    def test_refusals(self):
        with self.assertRaisesRegex(options_runner.GameOptionsRunError, 'both on and off'):
            options_runner.run(self.tmp, on=['cpu_vs_cpu'], off=['cpu_vs_cpu'])
        os.remove(self.dol_path)
        with self.assertRaisesRegex(options_runner.GameOptionsRunError, 'missing'):
            options_runner.run(self.tmp)

    def test_gecko_break_asks_first(self):
        del os.environ[gecko_compat.ACCEPT_ENV]
        with mock.patch.object(gecko_compat.slogger, 'ask', return_value='n') as ask:
            options_runner.run(self.tmp, on=['cpu_vs_cpu'], dry_run=True)
            ask.assert_not_called()                      # a dry run never asks
            with self.assertRaisesRegex(options_runner.GameOptionsRunError, 'Gecko'):
                options_runner.run(self.tmp, on=['cpu_vs_cpu'])
            self.assertEqual(ask.call_args.kwargs['kind'], 'danger')
        with open(self.dol_path, 'rb') as f:
            self.assertEqual(f.read(), self.original)    # cancelled: nothing written
        with mock.patch.object(gecko_compat.slogger, 'ask', return_value='y'):
            options_runner.run(self.tmp, on=['cpu_vs_cpu'])
        self.assertEqual(go.detect(self.image()), ['cpu_vs_cpu'])
        with mock.patch.object(gecko_compat.slogger, 'ask') as ask:
            options_runner.run(self.tmp, on=['cpu_management'])   # incompatible already: nothing left to break
            ask.assert_not_called()

    def test_stock_roster_run_does_not_ask(self):
        del os.environ[gecko_compat.ACCEPT_ENV]
        with mock.patch.object(gecko_compat.slogger, 'ask') as ask:
            report = roster_runner.run(self.tmp, config_path=self.config, input_dir=self.input_dir)
            ask.assert_not_called()
        self.assertFalse(report['gecko_break'])

    def test_options_survive_roster_runs(self):
        options_runner.run(self.tmp, on=['cpu_vs_cpu', 'cpu_management'])
        roster_runner.run(self.tmp, config_path=self.config, input_dir=self.input_dir)
        self.assertEqual(go.detect(self.image()), ['cpu_vs_cpu', 'cpu_management'])
        roster_runner.run(self.tmp, remove_only=True, input_dir=self.input_dir)
        self.assertEqual(go.detect(self.image()), ['cpu_vs_cpu', 'cpu_management'])
        options_runner.run(self.tmp, off=['cpu_vs_cpu', 'cpu_management'])
        roster_runner.run(self.tmp, remove_only=True, input_dir=self.input_dir)
        with open(self.dol_path, 'rb') as f:
            self.assertEqual(f.read(), self.original)

    def test_a_group_logs_one_line(self):
        log = options_runner.run(self.tmp, on=['cpu_vs_cpu'])
        self.assertIn('close_play: off (every level)', log)
        self.assertFalse([line for line in log if line.startswith('close_play_')])
        log = options_runner.run(self.tmp, on=['close_play_50'])
        self.assertIn('close_play_50: on', ' '.join(log))
        self.assertNotIn('close_play_40', ' '.join(log))

    def test_cpu_extras_survive_roster_runs(self):
        self.original = precharge_dol()
        for path in (self.dol_path, os.path.join(self.input_dir, 'main.dol')):
            with open(path, 'wb') as f:
                f.write(self.original)
        keys = ['cpu_vs_cpu', 'triple_take', 'strikeout_camera', 'outfield_dives', 'no_precharge', 'close_play_80']
        options_runner.run(self.tmp, on=keys)
        roster_runner.run(self.tmp, config_path=self.config, input_dir=self.input_dir)
        self.assertEqual(go.detect(self.image()), keys)
        options_runner.run(self.tmp, off=keys, input_dir=self.input_dir)    # the precharge's stock values
        roster_runner.run(self.tmp, remove_only=True, input_dir=self.input_dir)
        with open(self.dol_path, 'rb') as f:
            self.assertEqual(f.read(), self.original)


if __name__ == '__main__':
    unittest.main()
