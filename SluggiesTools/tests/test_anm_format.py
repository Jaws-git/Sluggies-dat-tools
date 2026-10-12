"""The Blender add-on's bpy-free ANM reader/writer (BlenderAddonSrc/AnmFormat.py):
setting-bank channel order, write/parse round trips, the rebuilt tracks the
.anm export writes, keyframe reduction and the file signature."""

import glob
import math
import pathlib
import struct
import sys
import unittest

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
ROOT = TOOLS_DIR.parent
ADDON_DIR = ROOT / 'BlenderAddonSrc'
if str(ADDON_DIR) not in sys.path:
    sys.path.insert(0, str(ADDON_DIR))

import AnmFormat as A  # noqa: E402


def _track(track_id, ttype, quant, keys, interp=A.REBUILT_INTERP, length=10.0):
    return {'track_id': track_id, 'length': length, 'quant': quant, 'type': ttype,
            'interp': interp, 'replace': 1, 'keys': keys}


def _key(time, settings, ttype, quant):
    return {'time': time, 'set_blob': A.encode_settings(settings, ttype, quant), 'interp_blob': b''}


class SettingBankTests(unittest.TestCase):
    def test_bank_order_is_scale_rotation_translation(self):
        ttype = (1 << A.CH_TRANSLATION) | (1 << A.CH_SCALE) | (1 << A.CH_QUATERNION)
        blob = A.encode_settings({A.CH_SCALE: [1.0, 1.5, 0.5],
                                  A.CH_QUATERNION: [-1.0, 0.0, 0.0, 0.0],
                                  A.CH_TRANSLATION: [0.25, -0.5, 0.75]}, ttype, 0x3E)
        values = struct.unpack('>10h', blob)
        self.assertEqual(values[:3], (16384, 24576, 8192))
        self.assertEqual(values[3:7], (0, 0, 0, 16384))     # x, y, z, w
        self.assertEqual(values[7:], (4096, -8192, 12288))

    def test_decode_inverts_encode(self):
        ttype = (1 << A.CH_TRANSLATION) | (1 << A.CH_QUATERNION)
        settings = {A.CH_QUATERNION: [-0.5, 0.5, 0.5, 0.5], A.CH_TRANSLATION: [12.0, -3.5, 100.0]}
        quant = A.vector_quant(settings[A.CH_TRANSLATION])
        self.assertEqual(A.decode_settings(A.encode_settings(settings, ttype, quant), ttype, quant), settings)

    def test_vector_quant_picks_most_fraction_bits(self):
        self.assertEqual(A.vector_quant([1.0]), 0x3E)
        self.assertEqual(A.vector_quant([100.0]), 0x38)       # 100 * 2^8 = 25600
        self.assertEqual(A.vector_quant([30000.0]), 0x30)
        with self.assertRaises(ValueError):
            A.vector_quant([40000.0])


class WriteParseTests(unittest.TestCase):
    def test_round_trip_shares_identical_data(self):
        ttype = 1 << A.CH_QUATERNION
        q = [-1.0, 0.0, 0.0, 0.0]
        keys = [_key(0.0, {A.CH_QUATERNION: q}, ttype, 0x3E), _key(10.0, {A.CH_QUATERNION: q}, ttype, 0x3E)]
        opaque = {'time': 3.0, 'set_blob': None, 'set_word': 0x40000000,
                  'interp_blob': None, 'interp_word': 0}
        seqs = [[_track(5, ttype, 0x3E, keys), _track(0x4001, 0x60, 0x3E, [opaque], interp=2)],
                [_track(5, ttype, 0x3E, keys)]]
        data = A.write(0x01321AFD, 0, seqs)
        anm = A.parse(data)
        self.assertEqual(struct.unpack_from('>HH', data, 12), (3, 5))     # track and key totals
        self.assertEqual([[t['track_id'] for t in s] for s in anm['sequences']], [[5, 0x4001], [5]])
        self.assertEqual(anm['sequences'][0][0]['keys'][1]['settings'][A.CH_QUATERNION], q)
        self.assertEqual(anm['sequences'][0][1]['keys'][0]['set_word'], 0x40000000)
        # one keyframe array and one setting bank for all three copies
        self.assertEqual(len(data), 0x18 + 2 * 12 + 3 * 16 + 3 * 12 + 8)

    def test_signature(self):
        ttype = 1 << A.CH_TRANSLATION
        k = [_key(0.0, {A.CH_TRANSLATION: [0, 0, 0]}, ttype, 0x30)]
        data = A.write(0x013240DB, 0, [[_track(1, ttype, 0x30, k), _track(2, ttype, 0x30, k)], []])
        self.assertEqual(A.signature(data), (0x013240DB, [{1, 2}, set()]))
        with self.assertRaises(ValueError):
            A.signature(b'\0' * 0x18)

    def test_records_keep_unrebuilt_tracks(self):
        ttype = 1 << A.CH_SCALE
        k = [_key(0.0, {A.CH_SCALE: [1, 1, 1]}, ttype, 0x3E)]
        seq = A.parse(A.write(0x01321AFD, 0, [[_track(1, ttype, 0x3E, k), _track(2, ttype, 0x3E, k)]]))['sequences'][0]
        records = A.loads_records(A.dumps_records(A.track_records(seq, {1})))
        self.assertNotIn('keys', records[0])
        self.assertEqual(A.passthrough_track(records[1])['keys'][0]['set_blob'], k[0]['set_blob'])


class RebuiltTrackTests(unittest.TestCase):
    def _samples(self, n=31):
        out = []
        for f in range(n):
            angle = math.radians(3 * f)
            out.append((float(f), [1.0, 1.0, 1.0],
                        [math.cos(angle / 2), 0.0, math.sin(angle / 2), 0.0], [0.5 * f, 0.0, 2.0]))
        return out

    def test_linear_motion_reduces_to_end_keys(self):
        track = A.rebuilt_track(7, 30, self._samples(), {A.CH_QUATERNION, A.CH_TRANSLATION})
        self.assertEqual([k['time'] for k in track['keys']], [0.0, 30.0])
        self.assertEqual(track['type'], (1 << A.CH_QUATERNION) | (1 << A.CH_TRANSLATION))
        self.assertEqual(track['interp'], A.REBUILT_INTERP)

    def test_kink_is_kept_and_flags_survive(self):
        samples = self._samples()
        samples = [(t, s, q, [abs(15 - t), 0.0, 2.0]) for t, s, q, _tr in samples]
        track = A.rebuilt_track(7, 30, samples, {A.CH_TRANSLATION}, flags=0x29)
        self.assertEqual([k['time'] for k in track['keys']], [0.0, 15.0, 30.0])
        self.assertEqual(track['type'], 0x21)            # flag bit 5 kept, channels replaced
        seq = A.parse(A.write(0x01321AFD, 0, [[track]]))['sequences'][0][0]
        self.assertEqual(seq['keys'][1]['settings'][A.CH_TRANSLATION], [0.0, 0.0, 2.0])

    def test_hemisphere_flip_is_removed(self):
        samples = self._samples(3)
        t, s, q, tr = samples[1]
        samples[1] = (t, s, [-v for v in q], tr)
        track = A.rebuilt_track(7, 2, samples, {A.CH_QUATERNION})
        self.assertEqual(len(track['keys']), 2)


STOCK = sorted(glob.glob(str(ROOT / '2_Output_Assets' / '*' / 'anm' / '*.anm')))


@unittest.skipUnless(STOCK, "no exported .anm files in 2_Output_Assets")
class StockRoundTripTests(unittest.TestCase):
    def test_passthrough_round_trip(self):
        for path in STOCK[:40]:
            with self.subTest(path):
                with open(path, 'rb') as f:
                    raw = f.read()
                anm = A.parse(raw)
                seqs = [[A.passthrough_track(r) for r in A.loads_records(A.dumps_records(A.track_records(s, set())))]
                        for s in anm['sequences']]
                out = A.parse(A.write(anm['magic'], anm['bank_id'], seqs))
                self.assertEqual(A.signature(raw), A.signature(A.write(anm['magic'], anm['bank_id'], seqs)))
                for sa, sb in zip(anm['sequences'], out['sequences']):
                    for ta, tb in zip(sa, sb):
                        self.assertEqual([(k['time'], k['settings'], k['interp_blob']) for k in ta['keys']],
                                         [(k['time'], k['settings'], k['interp_blob']) for k in tb['keys']])


if __name__ == '__main__':
    unittest.main()
