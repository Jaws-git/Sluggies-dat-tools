"""Shared DOL tooling (SluggiesTools/Dol): image and sections, assembler, relocation, frames, inventory."""

import struct
import unittest

from SluggiesTools.Dol import dolfile, frames, inventory, ppc, relocate

TEXT = 0x80004000
DATA = 0x80010000
BSS = 0x80020000
BSS_SIZE = 0x1000


def make_dol(text_words, data=b'\x11' * 0x40) -> bytes:
    text = b''.join(struct.pack('>I', w) for w in text_words)
    header = bytearray(dolfile.HEADER_SIZE)
    text_offset = dolfile.HEADER_SIZE
    data_offset = text_offset + len(text)
    struct.pack_into('>III', header, 0x00, text_offset, 0, 0)
    struct.pack_into('>I', header, 0x48, TEXT)
    struct.pack_into('>I', header, 0x90, len(text))
    struct.pack_into('>I', header, 7 * 4, data_offset)
    struct.pack_into('>I', header, 0x48 + 7 * 4, DATA)
    struct.pack_into('>I', header, 0x90 + 7 * 4, len(data))
    struct.pack_into('>III', header, 0xD8, BSS, BSS_SIZE, TEXT)
    return bytes(header) + text + data


def word(build, at=TEXT) -> int:
    return ppc.one(at, build)


class PpcEncodingTests(unittest.TestCase):
    """Encodings checked against words in the stock US main.dol (site inventory notes)."""

    def test_known_words(self):
        self.assertEqual(word(lambda a: a.mr('r3', 'r31')), 0x7FE3FB78)
        self.assertEqual(word(lambda a: a.cmpwi('r28', 6, cr=1)), 0x2C9C0006)
        self.assertEqual(word(lambda a: a.cmpwi('r4', 0x65, cr=1)), 0x2C840065)
        self.assertEqual(word(lambda a: a.addi('r4', 'r26', 0x12)), 0x389A0012)
        self.assertEqual(word(lambda a: a.li('r3', 1)), 0x38600001)
        self.assertEqual(word(lambda a: a.lis('r0', 0x8063)), 0x3C008063)
        self.assertEqual(word(lambda a: a.add('r3', 'r0', 'r6')), 0x7C603214)
        self.assertEqual(word(lambda a: a.stb('r27', 0x47, 'r4')), 0x9B640047)
        self.assertEqual(word(lambda a: a.blr()), 0x4E800020)
        self.assertEqual(word(lambda a: a.lbzx('r0', 'r3', 'r0')), 0x7C0300AE)

    def test_conditional_branch_offsets(self):
        self.assertEqual(word(lambda a: a.bge(TEXT + 8, cr=1)), 0x40840008)
        self.assertEqual(word(lambda a: a.blt(TEXT + 0xC, cr=1)), 0x4184000C)
        self.assertEqual(word(lambda a: a.bge(TEXT + 0x1C, cr=1)), 0x4084001C)

    def test_labels_resolve_forward_and_backward(self):
        a = ppc.Asm(0x807B7000)
        a.label('top').cmpwi('r3', 0).beq('out').b('top').label('out').blr()
        words = struct.unpack('>4I', a.assemble())
        self.assertEqual(words[1], 0x41820008)        # beq +8
        self.assertEqual(words[2], 0x4BFFFFF8)        # b -8
        self.assertEqual(ppc.branch_target(words[2], 0x807B7008), 0x807B7000)

    def test_branch_range_and_undefined_label(self):
        with self.assertRaises(ppc.PpcError):
            ppc.branch_word(0x80000000, 0x82000000)
        with self.assertRaises(ppc.PpcError):
            ppc.Asm(0x80000000).b('nowhere').assemble()

    def test_load_addr_carries_the_sign(self):
        a = ppc.Asm(TEXT).load_addr('r12', 0x807BC000)
        lis, addi = struct.unpack('>2I', a.assemble())
        self.assertEqual(lis & 0xFFFF, 0x807C)
        self.assertEqual(((lis & 0xFFFF) << 16) + ppc.signed16(addi), 0x807BC000)


class DolImageTests(unittest.TestCase):
    def test_patch_word_checks_the_stock_word(self):
        image = dolfile.DolImage(make_dol([0x60000000, 0x4E800020]))
        image.patch_word(TEXT, 0x60000000, 0x38600001)
        self.assertEqual(image.u32(TEXT), 0x38600001)
        with self.assertRaisesRegex(dolfile.DolError, 'expected stock'):
            image.patch_word(TEXT + 4, 0x60000000, 0)

    def test_add_section_appends_aligned_and_uses_free_slots(self):
        dol = make_dol([0x4E800020])
        image = dolfile.DolImage(dol)
        text = image.add_section('text', 0x807B7000, b'\x4E\x80\x00\x20')
        data = image.add_section('data', 0x807B7020, b'abc')
        self.assertEqual((text.name, data.name), ('T1', 'D1'))
        self.assertEqual(text.file_offset % 32, 0)
        self.assertEqual(data.size, 0x20)
        self.assertEqual(image.read(0x807B7020, 3), b'abc')
        self.assertEqual(dolfile.parse_header(image.to_bytes()).slots[data.index].address, 0x807B7020)

    def test_section_overlap_bss_and_alignment_refused(self):
        image = dolfile.DolImage(make_dol([0x4E800020]))
        with self.assertRaisesRegex(dolfile.DolError, 'overlaps T0'):
            image.add_section('data', TEXT, b'x')
        with self.assertRaisesRegex(dolfile.DolError, 'bss'):
            image.add_section('data', BSS + 0x20, b'x')
        with self.assertRaisesRegex(dolfile.DolError, 'aligned'):
            image.add_section('data', 0x807B7004, b'x')

    def test_remove_section_restores_the_file(self):
        dol = make_dol([0x4E800020])
        image = dolfile.DolImage(dol)
        slot = image.add_section('data', 0x807B7000, b'x' * 0x40)
        image.remove_section(slot)
        self.assertEqual(image.to_bytes(), dol + b'\0' * (-len(dol) % 32))

    def test_replace_section_keeps_slot_and_address(self):
        image = dolfile.DolImage(make_dol([0x4E800020]))
        image.add_section('data', 0x807B6000, b'a')
        slot = image.add_section('data', 0x807B7000, b'b' * 0x20)
        grown = image.replace_section(slot, b'c' * 0x60)
        self.assertEqual((grown.index, grown.address, grown.size), (slot.index, 0x807B7000, 0x60))
        self.assertEqual(image.read(0x807B7040, 4), b'cccc')
        self.assertEqual(image.read(0x807B6000, 1), b'a')

    def test_merge_sections(self):
        image = dolfile.DolImage(make_dol([0x4E800020]))
        a = image.add_section('text', 0x807B7000, b'\x11' * 0x20)
        b = image.add_section('text', 0x807B7040, b'\x22' * 0x20)
        merged = image.merge_sections([a, b])
        self.assertEqual(merged.size, 0x60)
        self.assertEqual(image.read(0x807B7020, 0x20), bytes(0x20))
        self.assertEqual(image.read(0x807B7040, 1), b'\x22')
        self.assertEqual(len([s for s in image.header.used_slots if s.is_text]), 2)

    def test_space_allocator(self):
        space = dolfile.Space(0x807B7000, 0x807B7010)
        self.assertEqual(space.put(b'ab', align=1), 0x807B7000)
        self.assertEqual(space.put(b'cd', align=4), 0x807B7004)
        space.write(0x807B7004, b'xy')
        self.assertEqual(bytes(space.blob), b'ab\0\0xy')
        with self.assertRaises(dolfile.DolError):
            space.put(b'z' * 0x10)


def lis(reg, value): return ppc.one(TEXT, lambda a: a.lis(reg, value))
def addi(rt, ra, value): return ppc.one(TEXT, lambda a: a.addi(rt, ra, value))
def ori(ra, rs, value): return ppc.one(TEXT, lambda a: a.ori(ra, rs, value))
def lwz(rt, d, ra): return ppc.one(TEXT, lambda a: a.lwz(rt, d, ra))


class RelocateTests(unittest.TestCase):
    OLD, NEW = 0x80631550, 0x807BC000

    def image(self):
        words = [lis(3, 0x8063), addi(3, 3, 0x1550),        # base
                 lis(4, 0x8063), lwz(0, 0x1556, 4),          # interior byte
                 lis(5, 0x8063), ori(5, 5, 0x1560),          # ori form
                 lis(6, 0x8063), addi(6, 6, 0x1878)]         # next table: not ours
        return dolfile.DolImage(make_dol(words))

    def test_pairs_keep_their_offset(self):
        image = self.image()
        pairs = [(TEXT, TEXT + 4), (TEXT + 8, TEXT + 12), (TEXT + 16, TEXT + 20)]
        changes = relocate.relocate_table(image, pairs, self.OLD, 0x328, self.NEW)
        self.assertEqual(len(changes), 6)
        self.assertEqual(relocate.pair_address(image, TEXT, TEXT + 4), self.NEW)
        self.assertEqual(relocate.pair_address(image, TEXT + 8, TEXT + 12), self.NEW + 6)
        self.assertEqual(relocate.pair_address(image, TEXT + 16, TEXT + 20), self.NEW + 0x10)
        self.assertEqual(relocate.pair_address(image, TEXT + 24, TEXT + 28), 0x80631878)

    def test_pair_outside_the_table_is_refused(self):
        with self.assertRaisesRegex(relocate.RelocationError, 'not an address'):
            relocate.relocate_table(self.image(), [(TEXT + 24, TEXT + 28)], self.OLD, 0x328, self.NEW)

    def test_shared_lis_is_refused(self):
        other = [(TEXT, TEXT + 4, self.OLD), (TEXT, TEXT + 28, 0x80631878)]
        with self.assertRaisesRegex(relocate.RelocationError, 'also builds'):
            relocate.relocate_table(self.image(), [(TEXT, TEXT + 4)], self.OLD, 0x328, self.NEW, other)

    def test_identity_relocation_twice_round_trips(self):
        image = self.image()
        before = image.to_bytes()
        pairs = [(TEXT, TEXT + 4), (TEXT + 8, TEXT + 12)]
        relocate.relocate_table(image, pairs, self.OLD, 0x328, self.NEW)
        relocate.relocate_table(image, pairs, self.NEW, 0x328, self.OLD)
        self.assertEqual(image.to_bytes(), before)


class GrowFrameTests(unittest.TestCase):
    def test_offsets_at_or_above_the_threshold_move(self):
        words = [
            0x9421FFD0,                       # stwu r1,-0x30(r1)
            ppc.one(TEXT, lambda a: a.mflr('r0')),
            0x90010034,                       # stw r0,0x34(r1)
            0xBF610014,                       # stmw r27,0x14(r1)
            ppc.one(TEXT, lambda a: a.addi('r7', 'r1', 8)),   # the buffer: stays
            0xBB610014,                       # lmw r27,0x14(r1)
            0x80010034,                       # lwz r0,0x34(r1)
            0x38210030,                       # addi r1,r1,0x30
            0x4E800020,
        ]
        image = dolfile.DolImage(make_dol(words))
        changes = frames.grow_frame(image, TEXT, TEXT + 4 * 8, 0x14)
        self.assertEqual(image.u32(TEXT), 0x9421FFC0)
        self.assertEqual(image.u32(TEXT + 8), 0x90010044)
        self.assertEqual(image.u32(TEXT + 12), 0xBF610024)
        self.assertEqual(image.u32(TEXT + 16), ppc.one(TEXT, lambda a: a.addi('r7', 'r1', 8)))
        self.assertEqual(image.u32(TEXT + 28), 0x38210040)
        self.assertEqual(len(changes), 6)

    def test_x_form_r1_and_bad_prologue_refused(self):
        image = dolfile.DolImage(make_dol([0x9421FFF0, ppc.one(TEXT, lambda a: a.mr('r31', 'r1')), 0x4E800020]))
        with self.assertRaises(frames.FrameError):
            frames.grow_frame(image, TEXT, TEXT + 8, 0x8)
        with self.assertRaises(frames.FrameError):
            frames.grow_frame(dolfile.DolImage(make_dol([0x4E800020])), TEXT, TEXT, 0x8)


class InventoryTests(unittest.TestCase):
    """The checked-in inventory (generated from the clean DOL by probe_site_inventory.py)."""

    def test_selector_table_and_pairs(self):
        selector = inventory.table('selector')
        self.assertEqual((selector.address, selector.row_size, selector.rows), (0x80631550, 8, 0x65))
        self.assertEqual(len(selector.pairs), 111)
        self.assertEqual(inventory.table('model_handles').extra_pairs, ((0x80152E4C, 0x80152EA4),))

    def test_every_table_pair_is_unique_to_its_table(self):
        seen = {}
        for lis_site, low_site, address in inventory.all_pairs():
            self.assertNotIn((lis_site, low_site), seen)
            seen[(lis_site, low_site)] = address
        by_lis = {}
        for (lis_site, _low), address in seen.items():
            by_lis.setdefault(lis_site, set()).add(address)
        self.assertEqual([l for l, a in by_lis.items() if len(a) > 1], [])

    def test_groups_and_stock_words(self):
        arena = inventory.group('arena_lo')
        self.assertEqual(len(arena), 8)
        self.assertEqual(inventory.site('roster_hook', 0x8006BD58).stock, 0x7FE3FB78)
        self.assertEqual(inventory.site('wheel7', 0x80071ECC).tool & 0xFFFF, 10)
        for name in ('wheel7', 'id_limits', 'select_chemistry', 'char_names', 'charge_scale', 'gridcells_11x4'):
            self.assertTrue(inventory.group(name), name)

    def test_stock_mismatches(self):
        image = dolfile.DolImage(make_dol([0x7FE3FB78]))
        good = inventory.Site(TEXT, 0x7FE3FB78)
        bad = inventory.Site(TEXT, 0)
        self.assertEqual(inventory.stock_mismatches(image, [good]), [])
        self.assertEqual(len(inventory.stock_mismatches(image, [good, bad])), 1)


if __name__ == '__main__':
    unittest.main()
