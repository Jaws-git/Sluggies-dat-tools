"""A resized in-place skin patch moves the flush-index array; patching again
(or unpatching) must still use the donor flush bytes, not whatever now sits at
the array's old offset in the output DAT."""
import os
import pathlib
import struct
import sys
import tempfile
import unittest

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
for path in (TOOLS_DIR, TOOLS_DIR / 'InplacePatcher'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import patch_skn_inplace as _skn  # noqa: E402
from binfmt import encode_field  # noqa: E402

SKN = 0x100
SK1_STRUCT = SKN + 0x24
DATA_REL = 0x24 + 0x40                     # one SK1 struct
VS = 24                                    # float xyz + nxnynz
FLUSH = struct.pack('>4H', 1, 2, 3, 4)
SRC = bytes(range(1, 2 * VS + 1))
FLUSH_REL = DATA_REL + len(SRC)


def _dat():
    raw = bytearray(0x400)
    struct.pack_into('>I', raw, SKN + 0x1C, FLUSH_REL)
    struct.pack_into('>I', raw, SK1_STRUCT, DATA_REL)
    struct.pack_into('>HH', raw, SK1_STRUCT + 0x08, 5, 2)
    raw[SKN + DATA_REL:SKN + DATA_REL + len(SRC)] = SRC
    raw[SKN + FLUSH_REL:SKN + FLUSH_REL + len(FLUSH)] = FLUSH
    return bytes(raw)


def _skin(with_flush_data):
    skin = {
        'SKNOffset': hex(SKN), 'QuantizeInfo': 0x40,
        'FlushIndSize': 4, 'FlushIndAbsolutePtr': hex(SKN + FLUSH_REL),
        'FlushIndArrFieldOffset': hex(SKN + 0x1C),
        'SK1s': [{
            'BoneIndex': 5, 'VertexCnt': 2, 'VertexOffset': 0,
            'BindPoseData': encode_field(SRC, False),
            'BindPoseDataEdited': encode_field(SRC[:VS], False), 'VertexCntEdited': 1,
            'VertexArrFieldOffset': hex(SK1_STRUCT), 'VertexArrAbsolutePtr': hex(SKN + DATA_REL),
        }],
    }
    if with_flush_data:
        skin['FlushIndData'] = encode_field(FLUSH, False)
    return skin


class FlushRepatchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.input = os.path.join(tmp.name, 'in.dat')
        self.output = os.path.join(tmp.name, 'out.dat')
        for path in (self.input, self.output):
            with open(path, 'wb') as f:
                f.write(_dat())
        for name, value in (('INPUT_DAT', self.input), ('OUTPUT_DAT', self.output)):
            old = getattr(_skn, name)
            setattr(_skn, name, value)
            self.addCleanup(setattr, _skn, name, old)

    def _out(self):
        with open(self.output, 'rb') as f:
            return f.read()

    def _flush_at_pointer(self):
        raw = self._out()
        rel = struct.unpack_from('>I', raw, SKN + 0x1C)[0]
        return raw[SKN + rel:SKN + rel + len(FLUSH)]

    def _patch_twice(self, skin):
        for _ in range(2):
            self.assertGreaterEqual(_skn.patchSKNInPlaceResized(skin), 0)
            self.assertEqual(self._flush_at_pointer(), FLUSH)

    def test_second_patch_reads_flush_from_the_sluggie(self):
        os.remove(self.input)
        self._patch_twice(_skin(True))

    def test_second_patch_reads_flush_from_the_input_dat(self):
        self._patch_twice(_skin(False))

    def test_unpatch_puts_the_flush_bytes_back(self):
        skin = _skin(True)
        self._patch_twice(skin)
        self.assertTrue(_skn.restoreSKNBlockInPlace(skin))
        raw = self._out()
        self.assertEqual(struct.unpack_from('>I', raw, SKN + 0x1C)[0], FLUSH_REL)
        self.assertEqual(raw, _dat())

    def test_no_flush_source_refuses(self):
        os.remove(self.input)
        before = self._out()
        self.assertEqual(_skn.patchSKNInPlaceResized(_skin(False)), -1)
        self.assertEqual(self._out(), before)


if __name__ == '__main__':
    unittest.main()
