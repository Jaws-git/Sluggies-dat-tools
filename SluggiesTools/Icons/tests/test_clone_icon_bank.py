import os
import struct
import sys
import tempfile
import unittest


ICONS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
if ICONS_DIR not in sys.path:
    sys.path.insert(0, ICONS_DIR)

import clone_icon_bank as cib


FILENAME_POINTERS = (0x8067F658, 0x8067F658, 0x8067F658)


def write_dol(path, offset=cib.STOCK_BANK_OFFSET, length=cib.STOCK_BANK_LENGTH):
    words = []
    for filename_pointer in FILENAME_POINTERS:
        words.extend((filename_pointer, length, offset, length))
    with open(path, 'wb') as dol:
        dol.seek(cib.ICON_ENTRY_DOL_OFFSET)
        dol.write(struct.pack('>12I', *words))


def make_stock_bank():
    bank = bytearray(cib.STOCK_BANK_LENGTH)
    struct.pack_into('>II', bank, 0, cib.STOCK_TEXTURE_SECTION, cib.STOCK_ICON_TABLE)
    struct.pack_into('>H', bank, 0x20, cib.STOCK_TEXTURE_COUNT)
    bank[0x100:0x108] = b'SLUGGIES'
    bank[-8:] = b'BANK-END'
    return bytes(bank)


class CloneIconBankTests(unittest.TestCase):
    def test_read_and_patch_direct_entry_updates_all_languages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dol_path = os.path.join(temp_dir, 'main.dol')
            write_dol(dol_path)

            entry = cib.read_icon_entry(dol_path)
            self.assertEqual(entry.offset, cib.STOCK_BANK_OFFSET)
            self.assertEqual(entry.length, cib.STOCK_BANK_LENGTH)
            self.assertEqual(entry.filename_pointers, FILENAME_POINTERS)

            destination = 0x2AC39500
            cib.patch_direct_icon_entry(dol_path, destination, cib.EXPANDED_BANK_LENGTH)
            with open(dol_path, 'rb') as dol:
                dol.seek(cib.ICON_ENTRY_DOL_OFFSET)
                words = struct.unpack('>12I', dol.read(cib.ICON_ENTRY_SIZE))

            for language_index in range(3):
                base = language_index * 4
                self.assertEqual(words[base], FILENAME_POINTERS[language_index])
                self.assertEqual(words[base + 1], cib.EXPANDED_BANK_LENGTH)
                self.assertEqual(words[base + 2], destination)
                self.assertEqual(words[base + 3], cib.EXPANDED_BANK_LENGTH)

    def test_build_expanded_clone_preserves_stock_and_zero_fills_tail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dat_path = os.path.join(temp_dir, 'dt_na.dat')
            stock = make_stock_bank()
            with open(dat_path, 'wb') as dat:
                dat.seek(cib.STOCK_BANK_OFFSET)
                dat.write(stock)

            entry = cib.IconEntry(
                FILENAME_POINTERS,
                cib.STOCK_BANK_OFFSET,
                cib.STOCK_BANK_LENGTH,
                cib.STOCK_BANK_LENGTH,
            )
            expanded = cib.build_expanded_clone(dat_path, entry)

            self.assertEqual(len(expanded), cib.EXPANDED_BANK_LENGTH)
            self.assertEqual(expanded[:cib.STOCK_BANK_LENGTH], stock)
            self.assertEqual(
                expanded[cib.STOCK_BANK_LENGTH:],
                bytes(cib.EXPANDED_BANK_LENGTH - cib.STOCK_BANK_LENGTH),
            )

    def test_read_rejects_language_slot_disagreement(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dol_path = os.path.join(temp_dir, 'main.dol')
            write_dol(dol_path)
            with open(dol_path, 'r+b') as dol:
                dol.seek(cib.ICON_ENTRY_DOL_OFFSET + 20)
                dol.write(struct.pack('>I', cib.STOCK_BANK_LENGTH + 1))

            with self.assertRaisesRegex(cib.IconBankCloneError, 'EN/SP/FR fields disagree'):
                cib.read_icon_entry(dol_path)

    def test_patch_rejects_unaligned_destination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dol_path = os.path.join(temp_dir, 'main.dol')
            write_dol(dol_path)

            with self.assertRaisesRegex(cib.IconBankCloneError, 'not 32-byte aligned'):
                cib.patch_direct_icon_entry(
                    dol_path,
                    0x2AC39501,
                    cib.EXPANDED_BANK_LENGTH,
                )


if __name__ == '__main__':
    unittest.main()