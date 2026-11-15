"""``InplacePatcher/patch_inplace.py`` run as the child process it is, on a hand-built DAT.

The script resolves ``1_Input`` and ``3_Output_Dat`` from its own location, so the test runs a copy of the tool
sources from a temporary project root. Covered: the output DAT is a whole copy or refused (a shorter file is
an interrupted copy), every write stays inside the model's block, and a patch that fails midway is undone.

``dat_writes`` (the guarded handle those rules live in) is tested directly at the end.
"""

import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
for path in (TOOLS_DIR, TOOLS_DIR / 'InplacePatcher'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import dat_writes  # noqa: E402
from binfmt import dump_sluggie, encode_field  # noqa: E402

BLOCK, BLOCK_LENGTH = 0x100, 0x100            # the model's block
NEIGHBOUR = BLOCK + BLOCK_LENGTH              # the next model starts here
VERTS = BLOCK + 0x20
OLD = bytes(range(1, 13))                     # one s16 xyz vertex... twice
NEW = bytes(range(101, 113))


def _dat() -> bytes:
    raw = bytearray(b'\xAA' * 0x400)
    raw[VERTS:VERTS + len(OLD)] = OLD
    return bytes(raw)


def _model(**vertex_buffer) -> dict:
    buffer = {
        'VertexBufferOffset': hex(VERTS), 'VertexBufferLength': len(OLD), 'VertexBufferCompCount': 3,
        'VertexBufferQuantizeInfo': 0x30,
        'VertexBufferData': encode_field(OLD, False), 'VertexBufferDataEdited': encode_field(NEW, False),
    }
    buffer.update(vertex_buffer)
    return {'SluggiesModel': {
        'ChunkNumber': 18, 'FileIndex': 0, 'ModelOffset': hex(BLOCK), 'ModelLength': BLOCK_LENGTH,
        'Submeshes': [{'VertexBuffer': buffer, 'UVChannels': [], 'DisplayStates': []}],
    }}


def _sk1_outside_the_block() -> dict:
    """A resized skin whose struct fields lie in the next model: the write phase fails after the vertex buffer
    was written."""
    source = bytes(4 * 24)
    return {
        'SKNOffset': hex(BLOCK + 0x40), 'QuantizeInfo': 0x40,
        'SK1s': [{
            'BoneIndex': 0, 'VertexCnt': 4, 'VertexCntEdited': 3, 'VertexOffset': 0,
            'BindPoseData': encode_field(source, False), 'BindPoseDataEdited': encode_field(source[:72], False),
            'VertexArrFieldOffset': hex(NEIGHBOUR + 0x10), 'VertexArrAbsolutePtr': hex(BLOCK + 0x80),
        }],
    }


class PatchInplaceRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls._tmp.name)
        shutil.copytree(
            TOOLS_DIR, cls.root / 'SluggiesTools',
            ignore=lambda folder, names: [
                n for n in names
                if n in ('tests', 'Debug', '_build', '__pycache__', 'Icons', 'Sound', 'Roster', 'Dol')
                or (os.path.isfile(os.path.join(folder, n)) and not n.endswith('.py'))])
        cls.script = cls.root / 'SluggiesTools' / 'InplacePatcher' / 'patch_inplace.py'
        (cls.root / '1_Input').mkdir()
        (cls.root / '1_Input' / 'dt_na.dat').write_bytes(_dat())
        cls.output = cls.root / '3_Output_Dat' / 'dt_na.dat'

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        shutil.rmtree(self.root / '3_Output_Dat', ignore_errors=True)

    def _run(self, document, *args):
        path = self.root / 'model.sluggie'
        dump_sluggie(document, str(path))
        return subprocess.run(
            [sys.executable, str(self.script), str(path), *args],
            input='\n', capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)

    def test_patch_and_unpatch(self):
        result = self._run(_model())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        expected = bytearray(_dat())
        expected[VERTS:VERTS + len(NEW)] = NEW
        self.assertEqual(self.output.read_bytes(), bytes(expected))
        self.assertEqual(os.listdir(self.output.parent).count('dt_na.dat.copying'), 0)
        result = self._run(_model(), '--unpatch')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.output.read_bytes(), _dat())

    def test_a_shorter_output_is_refused(self):
        """An interrupted first copy must not be patched into (and grown with zeros) forever after."""
        self.output.parent.mkdir()
        self.output.write_bytes(_dat()[:0x180])
        for args in ((), ('--unpatch',), ('--validate-only',)):
            result = self._run(_model(), *args)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn('shorter than the input', result.stdout + result.stderr)
            self.assertEqual(self.output.read_bytes(), _dat()[:0x180])

    def test_a_write_outside_the_block_is_refused(self):
        document = _model(VertexBufferOffset=hex(NEIGHBOUR + 0x20))
        for args in ((), ('--validate-only',)):
            result = self._run(document, *args)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("outside the model's block", result.stdout + result.stderr)
        self.assertEqual(self.output.read_bytes(), _dat())

    def test_a_block_past_the_input_dat_is_refused(self):
        document = _model()
        document['SluggiesModel']['ModelOffset'] = hex(0x380)
        result = self._run(document)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('past the input dt_na.dat', result.stdout + result.stderr)

    def test_an_overhanging_buffer_is_cut_at_the_block_end(self):
        """A vanilla buffer can be declared longer than the block holds: the next model's bytes stay."""
        offset = NEIGHBOUR - 8
        old = _dat()[offset:offset + 12]
        result = self._run(_model(VertexBufferOffset=hex(offset), VertexBufferData=encode_field(old, False)))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('cut to 8 bytes', result.stdout + result.stderr)
        expected = bytearray(_dat())
        expected[offset:NEIGHBOUR] = NEW[:8]
        self.assertEqual(self.output.read_bytes(), bytes(expected))

    def test_a_failure_in_the_write_phase_undoes_the_earlier_writes(self):
        document = _model()
        document['SluggiesModel']['SkinData'] = _sk1_outside_the_block()
        result = self._run(document)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('every write was undone', result.stdout + result.stderr)
        self.assertIn('Submesh 0 vertex: wrote', result.stdout + result.stderr)     # it had been written
        self.assertEqual(self.output.read_bytes(), _dat())


class GuardedDatTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, 'dt_na.dat')
        with open(self.path, 'wb') as f:
            f.write(bytes(range(64)))

    def _raw(self):
        with open(self.path, 'rb') as f:
            return f.read()

    def test_writes_inside_the_block_stay(self):
        with dat_writes.GuardedDat(self.path, 16, 16) as dat:
            dat.seek(16)
            dat.write(b'ab')
            dat.seek(30)
            dat.write(struct.pack('>H', 7))
            self.assertEqual(dat.bytes_written, 4)
        self.assertEqual(self._raw(), bytes(range(16)) + b'ab' + bytes(range(18, 30)) + b'\x00\x07'
                         + bytes(range(32, 64)))

    def test_a_write_leaving_the_block_raises_and_everything_is_undone(self):
        for offset, data in ((15, b'x'), (31, b'xy'), (32, b'x'), (0, b'x')):
            with self.assertRaises(dat_writes.OutOfBlockWrite):
                with dat_writes.GuardedDat(self.path, 16, 16) as dat:
                    dat.seek(20)
                    dat.write(b'kept until the end')[:0]
            with self.assertRaises(dat_writes.OutOfBlockWrite):
                with dat_writes.GuardedDat(self.path, 16, 16) as dat:
                    dat.seek(20)
                    dat.write(b'abcd')
                    dat.seek(20)
                    dat.write(b'efgh')                 # the same bytes twice: undone newest first
                    dat.seek(offset)
                    dat.write(data)
            self.assertEqual(self._raw(), bytes(range(64)))

    def test_a_block_past_the_end_of_the_file_is_not_grown(self):
        with self.assertRaises(dat_writes.OutOfBlockWrite):
            with dat_writes.GuardedDat(self.path, 48, 64) as dat:
                dat.seek(60)
                dat.write(b'12345678')
        self.assertEqual(self._raw(), bytes(range(64)))

    def test_any_exception_undoes_the_writes(self):
        for error in (RuntimeError('x'), SystemExit(1), KeyboardInterrupt()):
            with self.assertRaises(type(error)):
                with dat_writes.GuardedDat(self.path, 0, 64) as dat:
                    dat.seek(8)
                    dat.write(b'zzzz')
                    raise error
            self.assertEqual(self._raw(), bytes(range(64)))

    def test_fit_to_block(self):
        kept, cut, outside = dat_writes.fit_to_block(
            [('in', 16, b'abcd'), ('empty', 99, b''), ('over', 30, b'abcd'), ('before', 15, b'ab'),
             ('after', 32, b'a')], 16, 16)
        self.assertEqual(kept, [('in', 16, b'abcd'), ('over', 30, b'ab')])
        self.assertEqual(len(cut), 1)
        self.assertIn('over', cut[0])
        self.assertEqual([text.split(':')[0] for text in outside], ['before', 'after'])

    def test_block_problem(self):
        self.assertIsNone(dat_writes.block_problem(0x100, 0x40, 0x1000))
        self.assertIsNone(dat_writes.block_problem(0x100, 0x40, 0x1000, (0x80, 0x100)))      # an archive member
        self.assertIn('past the input', dat_writes.block_problem(0xFE0, 0x40, 0x1000))
        self.assertIn('directory record', dat_writes.block_problem(0x100, 0x40, 0x1000, (0x120, 0x40)))
        self.assertIn('not a valid range', dat_writes.block_problem(0x100, 0, 0x1000))


if __name__ == '__main__':
    unittest.main()
