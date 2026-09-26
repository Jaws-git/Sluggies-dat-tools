import os
import struct
import tempfile
import unittest

from SluggiesTools.Icons import layout2d


def make_sprite_record(first, key, x, y, alpha=0xFF, resource=0xA2):
    record = bytearray(layout2d.SPRITE_RECORD_SIZE)
    flags = 0x000F if first else 0x000F | layout2d.FOLLOWING_RECORD_FLAG
    struct.pack_into('>HHHHhh', record, 0, flags, key, 0x0300, resource, x, y)
    struct.pack_into('>hh', record, 0x18, x, y)
    struct.pack_into('>hh', record, 0x1C, x, y)
    struct.pack_into('>I', record, 0x2C, 0xFFFFFF00 | alpha)
    struct.pack_into('>ff', record, 0x34, 1.0, 1.0)
    return bytes(record)


def make_hold_record(key):
    return struct.pack('>BBHI', 1, 2, key, 0)


def make_track(records):
    return struct.pack('>HH', len(records), len(records[0])) + b''.join(records)


def make_element(squares, flags=1, extra_tracks=()):
    """Build an element with one 3-keyframe sprite track per ``(x, y, alpha)``."""
    info = struct.pack('>HHHH', 1, 0, 0x0A, 1) + bytes(8)
    tracks = [
        make_track([
            make_sprite_record(index == 0, key, x, y, alpha)
            for index, key in enumerate((10, 7, 0))
        ])
        for x, y, alpha in squares
    ]
    tracks.extend(extra_tracks)
    sub_count = 1 + len(tracks)
    header_size = 0x0C + 4 * sub_count
    offsets = [header_size]
    for block in (info, *tracks[:-1]):
        offsets.append(offsets[-1] + len(block))
    body = info + b''.join(tracks)
    length = header_size + len(body)
    return struct.pack(f'>HHII{sub_count}I', flags, 0, sub_count, length, *offsets) + body


def make_bank(elements, resource_rows=((0x45, (0.5, 0.25, 0.75, 1.0)),)):
    container = 0x40
    descriptor_size = 4 + 4 * (len(elements) + 1)
    element_start = container + layout2d.DESCRIPTOR_OFFSET + descriptor_size
    element_offsets = []
    blob = b''
    for element in elements:
        element_offsets.append(element_start + len(blob))
        blob += element
    resource_offset = element_start + len(blob)
    rows = b''.join(
        struct.pack('>HH4f', page, 0, *uv) for page, uv in resource_rows
    )
    resource = struct.pack('>II', len(resource_rows), 8 + len(rows)) + rows
    endpoint = resource_offset + len(resource)

    bank = bytearray(container)
    struct.pack_into('>II', bank, 0, layout2d.BANK_MAGIC, container)
    struct.pack_into('>H', bank, 0x20, 3)
    descriptor = container + layout2d.DESCRIPTOR_OFFSET
    bank += struct.pack(
        '>HHHHIII',
        0x280, 0x1C0, 0x3C24, 2,
        layout2d.DESCRIPTOR_OFFSET,
        resource_offset - container,
        endpoint - container,
    )
    bank += struct.pack(
        f'>I{len(elements) + 1}i',
        len(elements),
        resource_offset - descriptor,
        *(offset - descriptor for offset in element_offsets),
    )
    bank += blob + resource + bytes(8)
    return bytes(bank)


def stock_grid_squares():
    squares = [
        (43 + 49 * column, 135 + 52 * row, 0xFF)
        for row in range(4)
        for column in range(10)
    ]
    squares += [(533, 291, 0x00), (582, 291, 0x00)]
    return squares


class ParseBankTests(unittest.TestCase):
    def test_header_and_element_table(self):
        bank_bytes = make_bank([make_element([(1, 2, 0xFF)]), make_element([(3, 4, 0xFF)])])
        bank = layout2d.parse_bank(bank_bytes)
        self.assertEqual((bank.width, bank.height), (640, 448))
        self.assertEqual(bank.texture_count, 3)
        self.assertEqual(len(bank.element_offsets), 2)
        self.assertEqual(bank.endpoint + 8, len(bank_bytes))

    def test_rejects_non_bank(self):
        with self.assertRaises(layout2d.Layout2dError):
            layout2d.parse_bank(bytes(0x40))

    def test_rejects_resource_pointer_mismatch(self):
        bank_bytes = bytearray(make_bank([make_element([(1, 2, 0xFF)])]))
        struct.pack_into('>I', bank_bytes, 0x40 + 0x0C, 0x1234)
        with self.assertRaises(layout2d.Layout2dError):
            layout2d.parse_bank(bytes(bank_bytes))

    def test_resource_rows(self):
        bank_bytes = make_bank([make_element([(1, 2, 0xFF)])])
        rows = layout2d.resource_rows(bank_bytes, layout2d.parse_bank(bank_bytes))
        self.assertEqual(rows, [(0x45, (0.5, 0.25, 0.75, 1.0))])


class ParseElementTests(unittest.TestCase):
    def test_tracks_and_keyframes(self):
        bank_bytes = make_bank([make_element([(10, 20, 0xFF), (30, 40, 0x00)])])
        bank = layout2d.parse_bank(bank_bytes)
        element = layout2d.parse_element(bank_bytes, bank, 0)
        self.assertEqual(element.flags, 1)
        self.assertEqual(len(element.info), 0x10)
        self.assertEqual(len(element.tracks), 2)
        keys = [layout2d.parse_sprite_key(record) for record in element.tracks[1].records]
        self.assertEqual([key.key for key in keys], [10, 7, 0])
        self.assertEqual(keys[0].flags & layout2d.FOLLOWING_RECORD_FLAG, 0)
        self.assertTrue(all(key.flags & layout2d.FOLLOWING_RECORD_FLAG for key in keys[1:]))
        self.assertEqual((keys[0].x, keys[0].y, keys[0].visible), (30, 40, False))

    def test_variable_size_records(self):
        mixed = make_track([
            struct.pack('>BBHI', 0, 2, 14, 0),
            make_sprite_record(False, 13, 5, 6),
            make_hold_record(7),
        ])
        bank_bytes = make_bank([make_element([(10, 20, 0xFF)], extra_tracks=[mixed])])
        element = layout2d.parse_element(bank_bytes, layout2d.parse_bank(bank_bytes), 0)
        self.assertEqual([len(record) for record in element.tracks[1].records], [8, 0x3C, 8])
        self.assertEqual(layout2d.parse_sprite_key(element.tracks[1].records[1]).key, 13)

    def test_rejects_missing_continuation_flag(self):
        broken = make_track([make_sprite_record(True, 1, 0, 0), make_sprite_record(True, 0, 0, 0)])
        bank_bytes = make_bank([make_element([], extra_tracks=[broken])])
        with self.assertRaises(layout2d.Layout2dError):
            layout2d.parse_element(bank_bytes, layout2d.parse_bank(bank_bytes), 0)

    def test_rejects_track_with_inconsistent_span(self):
        element = bytearray(make_element([(10, 20, 0xFF)]))
        track_offset = struct.unpack_from('>I', element, 0x10)[0]
        struct.pack_into('>H', element, track_offset, 4)
        bank_bytes = make_bank([bytes(element)])
        with self.assertRaises(layout2d.Layout2dError):
            layout2d.parse_element(bank_bytes, layout2d.parse_bank(bank_bytes), 0)

    def test_rejects_missing_element(self):
        bank_bytes = make_bank([make_element([(10, 20, 0xFF)])])
        with self.assertRaises(layout2d.Layout2dError):
            layout2d.parse_element(bank_bytes, layout2d.parse_bank(bank_bytes), 1)


class GridSquareTests(unittest.TestCase):
    def test_stock_shaped_grid(self):
        filler = make_element([(0, 0, 0xFF)])
        elements = [filler] * layout2d.CSS_GRID_ELEMENT + [make_element(stock_grid_squares())]
        squares = layout2d.grid_squares(make_bank(elements))
        self.assertEqual(len(squares), 42)
        self.assertEqual(sum(square.visible for square in squares), 40)
        self.assertEqual((squares[0].x, squares[0].y), (43, 135))
        self.assertEqual((squares[39].x, squares[39].y), (484, 291))
        self.assertEqual([(s.x, s.y, s.visible) for s in squares[40:]], [(533, 291, False), (582, 291, False)])


class DolRouteTests(unittest.TestCase):
    def test_reads_all_three_language_slots(self):
        dol = bytearray(layout2d.CSS_LAYOUT_DOL_RECORD + 48)
        for slot, offset in enumerate(layout2d.CSS_LAYOUT_OFFSETS.values()):
            struct.pack_into(
                '>4I', dol, layout2d.CSS_LAYOUT_DOL_RECORD + slot * 16,
                layout2d.DAT_FNAME_PTR, layout2d.CSS_LAYOUT_LENGTH, offset, layout2d.CSS_LAYOUT_LENGTH,
            )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'main.dol')
            with open(path, 'wb') as handle:
                handle.write(dol)
            route = layout2d.read_css_layout_route(path)
        self.assertEqual(
            route,
            {lang: (offset, layout2d.CSS_LAYOUT_LENGTH) for lang, offset in layout2d.CSS_LAYOUT_OFFSETS.items()},
        )

    def test_rejects_non_dat_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'main.dol')
            with open(path, 'wb') as handle:
                handle.write(bytes(layout2d.CSS_LAYOUT_DOL_RECORD + 48))
            with self.assertRaises(layout2d.Layout2dError):
                layout2d.read_css_layout_route(path)


if __name__ == '__main__':
    unittest.main()
