"""Roster expansion Phase 4: colour wheels on the synthetic inventory DOL and a synthetic layout bank."""

import struct
import unittest
from unittest import mock

from SluggiesTools.Dol import dolfile, inventory, relocate
from SluggiesTools.Icons import layout2d
from SluggiesTools.Icons.tests.test_layout2d import make_bank, make_element
from SluggiesTools.Roster import dol_hammerspace as dhs
from SluggiesTools.Roster import ids, layout_file, steps, wheels
from SluggiesTools.tests.test_roster_ids import fresh_image

SELECTOR = inventory.table('selector').address
HASMODEL = inventory.table('hasmodel').address


class FakeLayout:
    """Stands in for layout_file.LayoutFiles: records the transforms applied."""

    def __init__(self):
        self.applied = []

    def update(self, transform):
        self.applied.append(transform)
        return ['layout updated']


def context(config: dict, image=None) -> steps.RosterContext:
    ctx = steps.RosterContext(dol=image or fresh_image(), dat=None, config=config)
    ctx.state[layout_file.STATE_KEY] = FakeLayout()
    return ctx


def row(image, cid: int, address: int = SELECTOR) -> bytes:
    return image.read(address + 8 * cid, 8)


def cap_values(image) -> list[int]:
    return [image.u32(a) & 0xFFFF for a in wheels.CAP_SITES]


class SpareRowTests(unittest.TestCase):
    def test_default_preset_rows(self):
        ctx = context({'wheels': [{'id': '0x47'}, {'id': '0x49', 'swatch': 'white'}]})
        wheels.apply(ctx)
        self.assertEqual(row(ctx.dol, 0x47), bytes.fromhex('0b06060000010105'))   # next slot (synthetic slots are all 0)
        self.assertEqual(row(ctx.dol, 0x49)[6:], b'\x01\x09')
        self.assertEqual(row(ctx.dol, 0x48), wheels.STOCK_SPARE_ROWS[0x48])          # unlisted: stock
        self.assertEqual(ctx.dol.read(HASMODEL + 0x47, 6), bytes([1, 0, 1, 0, 0, 0]))
        self.assertEqual(cap_values(ctx.dol), [6, 6])

    def test_without_wheels_key_rows_stay(self):
        image = fresh_image()
        before = image.read(SELECTOR, 8 * 101)
        wheels.apply(context({}, image))
        self.assertEqual(image.read(SELECTOR, 8 * 101), before)

    def test_host_without_wheel_gets_a_group(self):
        ctx = context({'wheels': [{'id': '0x4C', 'wheel': '0x00', 'swatch': 1}]})
        log = wheels.apply(ctx)
        group = row(ctx.dol, 0x00)[0]
        self.assertEqual(group, 0x0C)                    # synthetic max group is 0x0B (spare rows reset to stock)
        self.assertEqual(row(ctx.dol, 0x4C)[:3], bytes([group, 0x00, 0x00]))
        self.assertIn('wheel group 0x0C', ' '.join(log))

    def test_config_errors(self):
        for config, message in (({'wheels': [{'id': '0x66'}]}, 'not a spare row'),
                                ({'wheels': [{'id': '0x47'}, {'id': 71}]}, 'twice'),
                                ({'wheels': [{'id': '0x47', 'wheel': '0x48'}]}, 'not a stock player'),
                                ({'wheels': [{'id': '0x47', 'swatch': 11}]}, 'outside')):
            with self.assertRaisesRegex(wheels.WheelConfigError, message):
                wheels.apply(context(config))


class LimitTests(unittest.TestCase):
    def test_seven_members_raise_the_caps(self):
        image = fresh_image()
        for cid in (0x44, 0x45):                         # Yoshi wheel: 0x06, 0x42-0x45 = 5
            image.write(SELECTOR + 8 * cid, bytes([0x0B, 6, 6, 0, 0, 0, 1, 0]))
        ctx = context({'wheels': [{'id': '0x47'}, {'id': '0x48'}]}, image)
        wheels.apply(ctx)
        self.assertEqual(cap_values(image), [7, 7])
        self.assertEqual(ctx.state[layout_file.STATE_KEY].applied, [])

    def test_caps_already_seven_count_as_stock(self):
        image = fresh_image()
        wheels.set_caps(image, 7)
        wheels.apply(context({'wheels': []}, image))
        self.assertEqual(cap_values(image), [7, 7])      # a wheel of 6 or fewer leaves them alone
        wheels.set_caps(image, 6)

    def test_ten_members_with_new_ids(self):
        image = fresh_image()
        hs = dhs.DolHammerspace.create(image)
        state = {}
        config = {'ids': [{'template': '0x06'} for _ in range(5)],
                  'wheels': [{'id': '0x47'}, {'id': '0x48'}]}            # 3 + 5 + 2 = 10
        with mock.patch.object(relocate, 'scan_refs', return_value=[]):
            ids.apply_ids(image, hs, ids.parse_ids(config), state)
        hs.commit()
        ctx = context(config, image)
        ctx.state.update(state)
        log = wheels.apply(ctx)
        selector, rows = ctx.state['tables']['selector']
        self.assertEqual(rows, ids.ROWS)
        self.assertEqual(row(image, 0x47, selector)[6], 1)
        self.assertEqual(row(image, 0x47, SELECTOR)[6], 0)               # the old table is left alone
        self.assertEqual(image.read(ctx.state['tables']['hasmodel'][0] + 0x47, 2), b'\x01\x01')
        self.assertEqual(cap_values(image), [10, 10])
        for s in inventory.group('wheel7'):
            if s.tool is not None:
                self.assertEqual(image.u32(s.address), s.tool, f'0x{s.address:08X}')
            elif s.address in wheels.NODE_SITES:
                self.assertEqual(image.u32(s.address) >> 26, 18)          # b to the remap stub
        self.assertEqual(len(ctx.state[layout_file.STATE_KEY].applied), 1)
        self.assertIn('species 0x06 (10)', log[-1])

    def test_more_than_ten_refused(self):
        image = fresh_image()
        for cid in range(0x20, 0x28):                    # 8 more Yoshis: 3 + 8 + 2 spares = 13
            image.write(SELECTOR + 8 * cid, bytes([0x0B, 6, 6, 0, 0, 0, 1, 0]))
        with self.assertRaisesRegex(wheels.WheelConfigError, 'more than 10'):
            wheels.apply(context({'wheels': [{'id': '0x47'}, {'id': '0x48'}]}, image))

    def test_orange_swatch_requested_by_a_member(self):
        ctx = context({'wheels': [{'id': '0x49', 'swatch': 'orange'}]})
        log = wheels.apply(ctx)
        self.assertEqual(len(ctx.state[layout_file.STATE_KEY].applied), 1)
        self.assertIn('orange', log[-1])


def branch_target(image, at: int) -> int:
    word = image.u32(at)
    assert word >> 26 == 18, f'0x{at:08X}: {word:08X} is not a branch'
    return (at + ((word & 0x03FFFFFC) ^ 0x02000000) - 0x02000000) & 0xFFFFFFFF


class OrderTests(unittest.TestCase):
    STOCK_MR = 0x7FE3FB78        # mr r3,r31

    def tail(self, image, stub) -> int:
        """Address of the stub's ``mr r3,r31`` that is followed by the branch back behind the hook site."""
        at = stub
        while image.is_mapped(at + 4, 4):
            if image.u32(at) == self.STOCK_MR and image.u32(at + 4) >> 26 == 18 and                     branch_target(image, at + 4) == wheels.ROSTER_SITE + 4:
                return at
            at += 4
        self.fail('no tail')

    def test_stock_site(self):
        image = fresh_image()
        dhs.DolHammerspace.create(image)
        log = wheels.apply(context({'wheels': [{'id': '0x47'}], 'wheel_order': [['0x47', '0x06']]}, image))
        self.tail(image, branch_target(image, wheels.ROSTER_SITE))
        self.assertIn('species 0x06: 0x47 0x06', log[-1])

    def test_chains_after_the_new_id_hook(self):
        image = fresh_image()
        hs = dhs.DolHammerspace.create(image)
        state = {}
        config = {'ids': [{'id': '0x66', 'template': '0x06'}], 'wheel_order': [['0x66']]}
        with mock.patch.object(relocate, 'scan_refs', return_value=[]):
            ids.apply_ids(image, hs, ids.parse_ids(config), state)
        hs.commit()
        ids_stub = branch_target(image, wheels.ROSTER_SITE)
        ids_tail = self.tail(image, ids_stub)
        ctx = context(config, image)
        ctx.state.update(state)
        log = wheels.apply(ctx)
        self.assertIn('new-ID roster hook', log[-1])
        self.assertEqual(branch_target(image, wheels.ROSTER_SITE), ids_stub)      # the new-ID hook runs first
        reorder = branch_target(image, ids_tail)                                   # ... then the reorder
        self.assertGreater(reorder, ids_tail)
        self.tail(image, reorder)

    def test_order_errors(self):
        for order, message in (([['0x47']], 'not a selectable'), ([['0x06', '0x00']], 'different wheels'),
                               ([['0x06'], ['0x42']], 'twice')):
            with self.assertRaisesRegex(wheels.WheelConfigError, message):
                wheels.apply(context({'wheels': [], 'wheel_order': order}))

    def test_order_table(self):
        self.assertEqual(wheels.order_table([(6, [0x47, 6]), (0, [0x66])]), bytes([6, 0x47, 6, 0xFF, 0, 0x66, 0xFF, 0xFF]))


def element_key(time: int, x: int, size: int = 0x3C) -> bytes:
    key = bytearray(size)
    key[1] = size // 4
    struct.pack_into('>H', key, 2, time)
    key[4] = 3 if size == 0x3C else 4
    for offset in (0x08, 0x18, 0x1C):
        struct.pack_into('>h', key, offset, x)
    if size == 0x3C:
        struct.pack_into('>H', key, 0x28, wheels.STOCK_LAST)
    else:
        for offset, value in ((0x28, -40.0), (0x2C, -8.0), (0x30, 40.0), (0x34, 8.0)):
            struct.pack_into('>f', key, offset, value)
    return bytes(key)


def node(keys: list[bytes]) -> bytes:
    keys = [bytes([0 if n == 0 else 1]) + k[1:] for n, k in enumerate(keys)]
    return struct.pack('>HH', len(keys), len(keys[0])) + b''.join(keys)


def element(nodes: list[bytes], last: int = wheels.STOCK_LAST) -> bytes:
    info = struct.pack('>HHHH', 1, 0, last, 1) + bytes(8)
    head = 0x0C + 4 * (1 + len(nodes))
    subs, cursor = [head], head + len(info)
    for blob in nodes:
        subs.append(cursor)
        cursor += len(blob)
    return struct.pack(f'>HHII{len(subs)}I', 1, 0, len(subs), cursor, *subs) + info + b''.join(nodes)


def popup_bank() -> bytes:
    """A bank whose elements 0xAD/0xAE hold swatch keys (times 0-10) and 0xB3 is a stock-like popup."""
    elements = [make_element([(0, 0, 0xFF)]) for _ in range(wheels.POPUP + 1)]
    swatch = node([element_key(t, 0, 0x50) for t in range(10, -1, -1)])
    elements[0xAD] = element([swatch])
    elements[0xAE] = element([swatch])
    popup = [node([element_key(t, -5 * (t + 1) + 10 * i) for t in range(4, -1, -1)]) for i in range(6)]
    popup.append(node([element_key(t, 5 * (t + 1) + 15) for t in range(4, -1, -1)]))
    popup.append(node([element_key(t, -5 * (t + 1) - 15) for t in range(4, -1, -1)]))
    popup.append(node([element_key(t, 0, 0x58) for t in range(4, -1, -1)]))
    popup[5] = node([element_key(4, 25)])                 # the 6th swatch shows only at 6 members
    elements[wheels.POPUP] = element(popup)
    return make_bank(elements)


class LayoutTests(unittest.TestCase):
    def test_popup_frames_for_ten(self):
        out = wheels.popup_layout(popup_bank())
        lay = layout2d.Layout(out)
        self.assertEqual(struct.unpack_from('>H', lay.info_block(wheels.POPUP), 4)[0], 8)
        nodes = lay.node_blobs(wheels.POPUP)
        self.assertEqual(len(nodes), 13)
        records = [r for _o, r in layout2d.node_records(nodes[0])]
        self.assertEqual([struct.unpack_from('>H', r, 2)[0] for r in records], [8, 7, 6, 5, 4, 3, 2, 1, 0])
        self.assertEqual([r[0] for r in records], [0] + [1] * 8)
        self.assertEqual(struct.unpack_from('>h', records[0], 8)[0], wheels.swatch_x(10, 0))
        self.assertEqual(struct.unpack_from('>H', records[0], 0x28)[0], 8)
        cap = [r for _o, r in layout2d.node_records(nodes[6])][0]
        self.assertEqual(struct.unpack_from('>h', cap, 8)[0], wheels.edge(10))
        pane = [r for _o, r in layout2d.node_records(nodes[8])][0]
        self.assertEqual(struct.unpack_from('>ff', pane, 0x28), (-60.0, -8.0))
        self.assertEqual([len(layout2d.node_records(n)) for n in nodes[9:]], [4, 3, 2, 1])
        last = layout2d.node_records(nodes[12])[0][1]                # member 9 shows only at 10
        self.assertEqual((struct.unpack_from('>H', last, 2)[0], struct.unpack_from('>h', last, 8)[0]),
                         (8, wheels.swatch_x(10, 9)))

    def test_popup_refuses_a_patched_element(self):
        with self.assertRaises(layout2d.Layout2dError):
            wheels.popup_layout(wheels.popup_layout(popup_bank()))

    def test_orange_swatch(self):
        out = wheels.orange_swatch(popup_bank())
        lay = layout2d.Layout(out)
        for element_index in wheels.SWATCH_ELEMENTS:
            records = [r for _o, r in layout2d.node_records(lay.node_blobs(element_index)[0])]
            ten = [r for r in records if struct.unpack_from('>H', r, 2)[0] == 10][0]
            self.assertEqual(ten[0x40:0x50], wheels.ORANGE * 4)
            nine = [r for r in records if struct.unpack_from('>H', r, 2)[0] == 9][0]
            self.assertEqual(nine[0x40:0x50], bytes(16))
        self.assertEqual(len(out), len(popup_bank()))


if __name__ == '__main__':
    unittest.main()
