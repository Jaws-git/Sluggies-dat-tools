import json
import os
import struct
import tempfile
import unittest

from SluggiesTools.Icons import add_private_texture_pages as pages
from SluggiesTools.Icons import update_icon_source_tables as sources
from SluggiesTools.Icons.tests.test_clone_icon_bank import make_stock_bank


def make_source_table(count, resource_base):
    table = bytearray(sources.SOURCE_HEADER_SIZE + count * sources.SOURCE_RECORD_SIZE)
    struct.pack_into('>I', table, 0x08, len(table))
    struct.pack_into('>HH', table, 0x24, count, sources.SOURCE_RECORD_SIZE)
    donor_resources = dict(zip(range(6), resource_base))
    character_ids = (
        [0x4D, *range(0x46, -1, -1)]
        if count == sources.STOCK_FRONT_COUNT
        else list(range(count - 1, -1, -1))
    )
    for index, char_id in enumerate(character_ids):
        resource_id = donor_resources.get(char_id, index)
        start = sources.SOURCE_HEADER_SIZE + index * sources.SOURCE_RECORD_SIZE
        record_flags = 0x0014 if index == 0 else 0x0114
        struct.pack_into('>HHHH', table, start, record_flags, char_id, 0x0400, resource_id)
        table[start + 8:start + sources.SOURCE_RECORD_SIZE] = bytes([index & 0xFF]) * (
            sources.SOURCE_RECORD_SIZE - 8
        )
    return bytes(table)


def make_plain_source_bank():
    stock = bytearray(make_stock_bank())
    root = pages.cib.STOCK_ICON_TABLE
    descriptor = root + 0x14
    normal = make_source_table(71, [0x00, 0x02, 0x03, 0x04, 0x05, 0x06])
    side = make_source_table(71, [0x00, 0x02, 0x03, 0x04, 0x05, 0x06])
    front = make_source_table(72, [0x4F, 0x50, 0x51, 0x52, 0x53, 0x54])
    normal_offset = descriptor + 0x14
    side_offset = normal_offset + len(normal)
    front_offset = side_offset + len(side)
    resource_offset = front_offset + len(front)
    struct.pack_into('>I', stock, root + 0x10, resource_offset + 8 + sources.STOCK_RESOURCE_COUNT * 0x14 - root)
    struct.pack_into('>I', stock, descriptor, 3)
    for field, target in ((4, resource_offset), (8, normal_offset), (12, side_offset), (16, front_offset)):
        struct.pack_into('>i', stock, descriptor + field, target - descriptor)
    stock[normal_offset:normal_offset + len(normal)] = normal
    stock[side_offset:side_offset + len(side)] = side
    stock[front_offset:front_offset + len(front)] = front
    struct.pack_into('>II', stock, resource_offset, sources.STOCK_RESOURCE_COUNT, 8 + sources.STOCK_RESOURCE_COUNT * 0x14)
    return bytes(stock) + bytes(pages.cib.EXPANDED_BANK_LENGTH - len(stock))


def make_configured_bank():
    return pages.add_private_texture_pages(make_plain_source_bank())


ROUTES = [
    sources.CharacterRoute('A', 0x48, 0x00, 0x00, 0x4F, 0x98, 0x9E),
    sources.CharacterRoute('B', 0x49, 0x01, 0x02, 0x50, 0x99, 0x9F),
    sources.CharacterRoute('C', 0x4A, 0x02, 0x03, 0x51, 0x9A, 0xA0),
    sources.CharacterRoute('D', 0x4B, 0x03, 0x04, 0x52, 0x9B, 0xA1),
    sources.CharacterRoute('E', 0x47, 0x04, 0x05, 0x53, 0x9C, 0xA2),
    sources.CharacterRoute('F', 0x4C, 0x05, 0x06, 0x54, 0x9D, 0xA3),
]


class UpdateIconSourceTableTests(unittest.TestCase):
    def test_shipped_routes_use_first_six_unique_donors(self):
        routes = sources.load_character_routes()

        self.assertEqual([route.donor_id for route in routes], list(range(6)))
        self.assertEqual(
            [(route.donor_side_resource, route.donor_front_resource) for route in routes],
            [(0x00, 0x4F), (0x02, 0x50), (0x03, 0x51),
             (0x04, 0x52), (0x05, 0x53), (0x06, 0x54)],
        )

    def test_rejects_duplicate_donor_ids(self):
        characters = [
            {
                'name': f'Character {index}',
                'char_id': hex(0x47 + index),
                'donor_id': '0x00',
                'donor_side_resource': hex(index),
                'donor_front_resource': hex(0x4F + index),
            }
            for index in range(sources.INITIAL_CHARACTER_COUNT)
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, 'characters.json')
            with open(path, 'w', encoding='utf-8') as description_file:
                json.dump({'characters': characters}, description_file)

            with self.assertRaisesRegex(sources.IconSourceTableError, 'duplicate donor ID'):
                sources.load_character_routes(path)

    def test_relocation_preserves_stock_tables_without_custom_records(self):
        configured = make_configured_bank()
        normal, side, front = sources._extract_source_tables(configured)

        relocated = sources.relocate_icon_source_tables(configured)

        sources.validate_relocated_source_tables(relocated)
        self.assertFalse(sources._is_updated(relocated))
        self.assertEqual(
            relocated[sources.NORMAL_A_OFFSET:sources.NORMAL_A_OFFSET + len(normal)],
            normal,
        )
        self.assertEqual(
            relocated[sources.SIDE_TABLE_OFFSET:sources.SIDE_TABLE_OFFSET + len(side)],
            side,
        )
        self.assertEqual(
            relocated[sources.FRONT_TABLE_OFFSET:sources.FRONT_TABLE_OFFSET + len(front)],
            front,
        )

    def test_relocates_and_expands_source_tables(self):
        configured = make_configured_bank()
        updated = sources.update_icon_source_tables(configured, ROUTES)

        composed = sources.append_custom_source_records(
            sources.relocate_icon_source_tables(configured), ROUTES
        )

        sources.validate_updated_source_tables(updated, ROUTES)
        self.assertTrue(sources._is_updated(updated))
        self.assertEqual(updated, composed)
        self.assertEqual(
            sources._signed_pointer(updated, sources.RESOURCE_POINTER_FIELD),
            sources.RESOURCE_TABLE_OFFSET,
        )
        self.assertEqual(
            updated[sources.RESOURCE_TABLE_OFFSET:],
            configured[sources.RESOURCE_TABLE_OFFSET:],
        )

    def test_custom_records_clone_donor_tail_and_replace_key_fields(self):
        updated = sources.update_icon_source_tables(make_configured_bank(), ROUTES)
        side_length, _, _ = sources._source_table_info(updated, sources.SIDE_TABLE_OFFSET)
        side = updated[sources.SIDE_TABLE_OFFSET:sources.SIDE_TABLE_OFFSET + side_length]
        donor = sources._record_for_character(side, 0x00)
        custom = sources._record_for_character(side, 0x48)

        self.assertEqual(struct.unpack_from('>HHH', custom, 2), (0x48, 0x0400, 0x98))
        self.assertEqual(custom[8:], donor[8:])

    def test_custom_records_preserve_descending_key_order_and_first_marker(self):
        updated = sources.update_icon_source_tables(make_configured_bank(), ROUTES)

        for offset in (sources.SIDE_TABLE_OFFSET, sources.FRONT_TABLE_OFFSET):
            _, count, stride = sources._source_table_info(updated, offset)
            records = [
                updated[
                    offset + sources.SOURCE_HEADER_SIZE + index * stride:
                    offset + sources.SOURCE_HEADER_SIZE + (index + 1) * stride
                ]
                for index in range(count)
            ]
            character_ids = [struct.unpack_from('>H', record, 0x02)[0] for record in records]
            record_flags = [struct.unpack_from('>H', record, 0x00)[0] for record in records]

            self.assertEqual(character_ids, sorted(character_ids, reverse=True))
            self.assertEqual(record_flags[0] & sources.SOURCE_FIRST_RECORD_FLAG, 0)
            self.assertTrue(all(
                flags & sources.SOURCE_FIRST_RECORD_FLAG for flags in record_flags[1:]
            ))

    def test_rejects_mismatched_donor_resource(self):
        wrong_routes = [
            sources.CharacterRoute('A', 0x48, 0x00, 0x01, 0x4F, 0x98, 0x9C),
        ]
        with self.assertRaisesRegex(sources.IconSourceTableError, 'description expects'):
            sources.update_icon_source_tables(make_configured_bank(), wrong_routes)

    def test_preserves_private_cmpr_payloads(self):
        configured = bytearray(make_configured_bank())
        side = bytes([0x11]) * pages.CMPR_IMAGE_LENGTH
        front = bytes([0x22]) * pages.CMPR_IMAGE_LENGTH
        configured[
            pages.SIDE_IMAGE_OFFSET:pages.SIDE_IMAGE_OFFSET + pages.CMPR_IMAGE_LENGTH
        ] = side
        configured[
            pages.FRONT_IMAGE_OFFSET:pages.FRONT_IMAGE_OFFSET + pages.CMPR_IMAGE_LENGTH
        ] = front

        updated = sources.update_icon_source_tables(bytes(configured), ROUTES)

        self.assertEqual(
            updated[pages.SIDE_IMAGE_OFFSET:pages.SIDE_IMAGE_OFFSET + pages.CMPR_IMAGE_LENGTH],
            side,
        )
        self.assertEqual(
            updated[pages.FRONT_IMAGE_OFFSET:pages.FRONT_IMAGE_OFFSET + pages.CMPR_IMAGE_LENGTH],
            front,
        )


if __name__ == '__main__':
    unittest.main()