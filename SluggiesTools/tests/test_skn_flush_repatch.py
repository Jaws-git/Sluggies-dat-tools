"""A resized in-place skin patch keeps every SKN array at its original offset. Earlier versions repacked the
arrays and moved the flush-index array: patching (or unpatching) over that layout must put the donor flush
bytes back, from the .sluggie or the input DAT, never from the array's old offset in the output DAT."""
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

    def _legacy_patched(self):
        """The output DAT as the 4-byte repack of earlier versions left it: the shortened source array, the
        flush-index array moved right behind it (over the old second vertex) and its old place overwritten."""
        raw = bytearray(_dat())
        new_flush_rel = DATA_REL + VS
        raw[SKN + new_flush_rel:SKN + new_flush_rel + len(FLUSH)] = FLUSH
        raw[SKN + FLUSH_REL:SKN + FLUSH_REL + len(FLUSH)] = b'\xEE' * len(FLUSH)
        struct.pack_into('>I', raw, SKN + 0x1C, new_flush_rel)
        struct.pack_into('>H', raw, SK1_STRUCT + 0x0A, 1)
        with open(self.output, 'wb') as f:
            f.write(raw)

    def _expected_patched(self):
        raw = bytearray(_dat())
        raw[SKN + DATA_REL + VS:SKN + DATA_REL + 2 * VS] = bytes(VS)      # the dropped vertex, zeroed
        struct.pack_into('>H', raw, SK1_STRUCT + 0x0A, 1)
        return bytes(raw)

    def test_arrays_and_flush_keep_their_place(self):
        """A shortened array stays at its (32-aligned) vanilla offset; no pointer moves; patching twice is the
        same as once, and needs no donor flush bytes."""
        os.remove(self.input)
        for _ in range(2):
            self.assertEqual(_skn.patchSKNInPlaceResized(_skin(False)), 1)
            self.assertEqual(self._out(), self._expected_patched())
        self.assertEqual(self._flush_at_pointer(), FLUSH)

    def test_legacy_layout_is_put_back_from_the_sluggie(self):
        self._legacy_patched()
        os.remove(self.input)
        self.assertEqual(_skn.patchSKNInPlaceResized(_skin(True)), 1)
        self.assertEqual(self._out(), self._expected_patched())

    def test_legacy_layout_is_put_back_from_the_input_dat(self):
        self._legacy_patched()
        self.assertEqual(_skn.patchSKNInPlaceResized(_skin(False)), 1)
        self.assertEqual(self._out(), self._expected_patched())

    def test_legacy_layout_without_a_flush_source_refuses(self):
        self._legacy_patched()
        os.remove(self.input)
        before = self._out()
        self.assertEqual(_skn.patchSKNInPlaceResized(_skin(False)), -1)
        self.assertEqual(self._out(), before)

    def test_unpatch_restores_the_file(self):
        for prepare in (lambda: None, self._legacy_patched):
            prepare()
            skin = _skin(True)
            self.assertEqual(_skn.patchSKNInPlaceResized(skin), 1)
            self.assertTrue(_skn.restoreSKNBlockInPlace(skin))
            self.assertEqual(self._out(), _dat())

    def test_a_grown_array_is_refused(self):
        skin = _skin(True)
        skin['SK1s'][0]['BindPoseDataEdited'] = encode_field(SRC + bytes(VS), False)
        skin['SK1s'][0]['VertexCntEdited'] = 3
        before = self._out()
        self.assertEqual(_skn.patchSKNInPlaceResized(skin, dry_run=True), -1)
        self.assertEqual(_skn.patchSKNInPlaceResized(skin), -1)
        self.assertEqual(self._out(), before)

    def test_an_empty_edited_array_is_not_taken_for_a_missing_one(self):
        """SKAcc entry emptied by the edit: its weights are the (empty) edited array, not the donor's."""
        acc_struct, acc_src, acc_dest, acc_wt = 0x300, 0x320, 0x340, 0x360
        skin = _skin(True)
        skin['SKAccs'] = [{
            'VertexCnt': 1, 'VertexCntEdited': 0,
            'BindPoseData': encode_field(bytes(range(VS)), False), 'BindPoseDataEdited': encode_field(b'', False),
            'DestIndexData': encode_field(b'\x00\x07', False), 'DestIndexDataEdited': encode_field(b'', False),
            'WeightData': encode_field(b'\x80', False), 'WeightDataEdited': encode_field(b'', False),
            'VertexArrFieldOffset': hex(acc_struct), 'VertexArrAbsolutePtr': hex(acc_src),
            'DestArrFieldOffset': hex(acc_struct + 4), 'DestArrAbsolutePtr': hex(acc_dest),
            'WeightArrFieldOffset': hex(acc_struct + 8), 'WeightArrAbsolutePtr': hex(acc_wt),
        }]
        with open(self.output, 'r+b') as f:
            f.seek(acc_wt)
            f.write(b'\x80')
        self.assertEqual(_skn.patchSKNInPlaceResized(skin), 1)
        raw = self._out()
        self.assertEqual(raw[acc_wt], 0)
        self.assertEqual(struct.unpack_from('>H', raw, acc_struct + 0x12)[0], 0)

    def test_writes_go_through_the_given_handle(self):
        """With ``dat=`` the caller's guarded handle takes every write: a block that does not hold the SKN
        refuses the patch, and the handle undoes what was written."""
        import dat_writes
        before = self._out()
        with self.assertRaises(dat_writes.OutOfBlockWrite):
            with dat_writes.GuardedDat(self.output, SKN + DATA_REL, VS * 2) as dat:    # the array, not the structs
                _skn.patchSKNInPlaceResized(_skin(True), dat=dat)
        self.assertEqual(self._out(), before)
        with dat_writes.GuardedDat(self.output, SKN, 0x200) as dat:
            self.assertEqual(_skn.patchSKNInPlaceResized(_skin(True), dat=dat), 1)
        self.assertEqual(self._out(), self._expected_patched())


if __name__ == '__main__':
    unittest.main()
