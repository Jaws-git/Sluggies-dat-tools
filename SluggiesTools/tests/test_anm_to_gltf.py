"""``anm_to_gltf.parse_anm`` on a hand-built ANM bank."""

import os
import struct
import sys
import unittest

TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import anm_to_gltf  # noqa: E402


def _bank(setting_pointers):
    """One sequence with one translation track (s16, no shift); a key per entry of ``setting_pointers``
    (None: the key's setting at its own place in the bank, 0: a null pointer)."""
    header, sequence, track = 0x10, 12, 16
    seq_at = header
    track_at = seq_at + sequence
    keys_at = track_at + track
    bank_at = keys_at + 12 * len(setting_pointers)
    out = bytearray(struct.pack('>IIHHHH', sorted(anm_to_gltf.ANM_MAGICS)[0], seq_at, 0, 1, 1, len(setting_pointers)))
    out += struct.pack('>IIHH', 0, track_at, 1, 0)
    out += struct.pack('>fIHH', 10.0, keys_at, len(setting_pointers), 7) + struct.pack('>BBBB', 0x30, 0x01, 0, 0)
    bank = bytearray()
    for index, pointer in enumerate(setting_pointers):
        if pointer is None:
            pointer = bank_at + len(bank)
            bank += struct.pack('>3h', index + 1, 0, 0)
        out += struct.pack('>fII', float(index), pointer, 0)
    return bytes(out + bank)


class ParseAnmTests(unittest.TestCase):
    def test_keys_are_read_from_their_setting(self):
        track = anm_to_gltf.parse_anm(_bank([None, None]))[0]['tracks'][0]
        self.assertEqual(track['track_id'], 7)
        self.assertEqual([(k['time'], k['settings'][0]) for k in track['keyframes']],
                         [(0.0, [1.0, 0.0, 0.0]), (1.0, [2.0, 0.0, 0.0])])

    def test_a_key_with_a_null_setting_pointer_is_skipped(self):
        track = anm_to_gltf.parse_anm(_bank([None, 0, None]))[0]['tracks'][0]
        self.assertEqual([(k['time'], k['settings'][0]) for k in track['keyframes']],
                         [(0.0, [1.0, 0.0, 0.0]), (2.0, [3.0, 0.0, 0.0])])


if __name__ == '__main__':
    unittest.main()
