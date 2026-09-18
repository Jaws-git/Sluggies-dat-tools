import pathlib
import struct
import sys
import unittest

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
for candidate in (TOOLS_DIR, TOOLS_DIR / 'Hammerspace'):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from ArchiveContainer import (
    ARCHIVE_ALIGN,
    ARCHIVE_MAX_SLOTS,
    parse_archive_container,
    rebuild_archive_container,
)


def _archive(offsets, members, total=None):
    """Build an archive from ``{slot_offset: payload}`` member bytes."""
    header = struct.pack(f'>{len(offsets) + 1}I', len(offsets), *offsets)
    body = bytearray(header)
    for offset, payload in sorted(members.items()):
        if len(body) < offset:
            body += bytes(offset - len(body))
        body[offset:offset + len(payload)] = payload
    if total is not None and len(body) < total:
        body += bytes(total - len(body))
    return bytes(body)


class ParseArchiveContainerTests(unittest.TestCase):
    def test_parses_count_offsets_and_member_extents(self):
        container = _archive(
            [0x20, 0, 0x60],
            {0x20: b'A' * 0x40, 0x60: b'B' * 0x40},
            total=0xA0,
        )

        layout = parse_archive_container(container)

        self.assertEqual(layout.file_count, 3)
        self.assertEqual(layout.offsets, (0x20, 0, 0x60))
        self.assertEqual(layout.header_size, 4 + 4 * 3)
        self.assertEqual(
            [(m.slot, m.offset, m.length) for m in layout.members],
            [(0, 0x20, 0x40), (2, 0x60, 0x40)],
        )

    def test_last_member_runs_to_the_end_of_the_container(self):
        container = _archive([0x20], {0x20: b'A' * 0x40}, total=0x100)

        layout = parse_archive_container(container)

        self.assertEqual(layout.members[-1].length, 0x100 - 0x20)

    def test_member_at_finds_by_offset_and_returns_none_otherwise(self):
        container = _archive([0x20, 0x60], {}, total=0xA0)

        layout = parse_archive_container(container)

        self.assertEqual(layout.member_at(0x60).slot, 1)
        self.assertIsNone(layout.member_at(0x40))

    def test_model_block_is_not_an_archive(self):
        # A model block opens with a zero word, so the count is 0.
        self.assertIsNone(parse_archive_container(bytes(0x80)))

    def test_anm_magic_is_not_an_archive(self):
        container = struct.pack('>I', 0x01321AFD) + bytes(0x40)

        self.assertIsNone(parse_archive_container(container))

    def test_rejects_implausible_file_counts(self):
        too_many = struct.pack('>I', ARCHIVE_MAX_SLOTS + 1) + bytes(0x4000)

        self.assertIsNone(parse_archive_container(too_many))

    def test_rejects_table_larger_than_the_container(self):
        self.assertIsNone(parse_archive_container(struct.pack('>I', 64) + bytes(0x20)))

    def test_rejects_offsets_inside_the_table(self):
        container = _archive([0x04, 0x60], {}, total=0xA0)

        self.assertIsNone(parse_archive_container(container))

    def test_rejects_offsets_past_the_container(self):
        container = _archive([0x20, 0x400], {}, total=0xA0)

        self.assertIsNone(parse_archive_container(container))

    def test_rejects_descending_offsets(self):
        container = _archive([0x60, 0x20], {}, total=0xA0)

        self.assertIsNone(parse_archive_container(container))

    def test_rejects_an_all_empty_table(self):
        container = _archive([0, 0], {}, total=0xA0)

        self.assertIsNone(parse_archive_container(container))

    def test_rejects_a_container_too_short_for_a_header(self):
        self.assertIsNone(parse_archive_container(b'\x00\x00\x00\x01'))


class RebuildArchiveContainerTests(unittest.TestCase):
    def setUp(self):
        self.container = _archive(
            [0x20, 0x60, 0xA0],
            {0x20: b'A' * 0x40, 0x60: b'B' * 0x40, 0xA0: b'C' * 0x40},
            total=0xE0,
        )

    def test_growing_a_middle_member_shifts_only_the_slots_after_it(self):
        new_member = b'N' * 0x80

        block, report = rebuild_archive_container(self.container, 0x60, new_member)

        layout = parse_archive_container(block)
        self.assertEqual(layout.offsets, (0x20, 0x60, 0xA0 + 0x40))
        self.assertEqual(report['archive_size_delta'], 0x40)
        self.assertEqual(report['archive_slots_shifted'], 1)
        self.assertEqual(report['archive_member_slot'], 1)
        self.assertEqual(report['archive_member_original_size'], 0x40)
        self.assertEqual(report['archive_member_new_size'], 0x80)
        self.assertEqual(report['archive_new_size'], len(block))

    def test_untouched_members_are_copied_byte_for_byte(self):
        block, _ = rebuild_archive_container(self.container, 0x60, b'N' * 0x80)

        before = parse_archive_container(self.container)
        after = parse_archive_container(block)
        for old, new in zip(before.members, after.members):
            if old.slot == 1:
                continue
            self.assertEqual(
                self.container[old.offset:old.end],
                block[new.offset:new.end],
                f'slot {old.slot} changed',
            )

    def test_new_member_lands_at_its_original_offset(self):
        new_member = b'N' * 0x80

        block, _ = rebuild_archive_container(self.container, 0x60, new_member)

        self.assertEqual(block[0x60:0x60 + len(new_member)], new_member)

    def test_size_delta_is_rounded_up_to_the_alignment(self):
        # One byte over the old size still costs a whole cache line, so every
        # later member keeps its 32-byte boundary.
        block, report = rebuild_archive_container(self.container, 0x60, b'N' * 0x41)

        self.assertEqual(report['archive_size_delta'], ARCHIVE_ALIGN)
        self.assertEqual(report['archive_member_padding'], ARCHIVE_ALIGN - 1)
        layout = parse_archive_container(block)
        for member in layout.members:
            self.assertEqual(member.offset % ARCHIVE_ALIGN, 0)

    def test_a_shrunken_member_moves_the_later_slots_down(self):
        block, report = rebuild_archive_container(self.container, 0x60, b'N' * 0x20)

        self.assertEqual(report['archive_size_delta'], -0x20)
        self.assertEqual(parse_archive_container(block).offsets, (0x20, 0x60, 0x80))

    def test_a_same_size_member_leaves_the_table_untouched(self):
        block, report = rebuild_archive_container(self.container, 0x60, b'N' * 0x40)

        self.assertEqual(report['archive_size_delta'], 0)
        self.assertEqual(len(block), len(self.container))
        self.assertEqual(parse_archive_container(block).offsets, (0x20, 0x60, 0xA0))

    def test_rebuilding_the_last_member_keeps_every_earlier_slot(self):
        block, report = rebuild_archive_container(self.container, 0xA0, b'N' * 0x100)

        self.assertEqual(report['archive_slots_shifted'], 0)
        self.assertEqual(report['archive_suffix_size'], 0)
        self.assertEqual(parse_archive_container(block).offsets, (0x20, 0x60, 0xA0))

    def test_empty_slots_stay_empty(self):
        container = _archive(
            [0x20, 0, 0x60],
            {0x20: b'A' * 0x40, 0x60: b'B' * 0x40},
            total=0xA0,
        )

        block, _ = rebuild_archive_container(container, 0x20, b'N' * 0x80)

        self.assertEqual(parse_archive_container(block).offsets, (0x20, 0, 0xA0))

    def test_non_archive_input_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'not an archive container'):
            rebuild_archive_container(bytes(0x80), 0x20, b'N')

    def test_offset_that_is_not_a_slot_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'not the start of a populated archive slot'):
            rebuild_archive_container(self.container, 0x70, b'N' * 0x40)

    def test_layout_from_a_different_container_is_rejected(self):
        layout = parse_archive_container(self.container + bytes(0x20))

        with self.assertRaisesRegex(ValueError, 'but container is'):
            rebuild_archive_container(self.container, 0x60, b'N', layout=layout)


if __name__ == '__main__':
    unittest.main()
