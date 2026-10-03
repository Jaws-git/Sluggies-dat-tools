"""Undo records for the roster steps.

A run's changes are recorded so the roster runner (menu [9]) can take them
back out before it runs again:

* ``diff_bytes`` / ``undo_diff``: a byte-level diff of the whole ``main.dol``
  before and after one step (the DOL is about 7 MB, so this is cheap), with
  any appended tail checked by hash; undone newest step first;
* ``DatFile``: buffered writes to ``dt_na.dat`` that remember the bytes they
  replace (the DAT is far too large to diff); recorded and undone for the
  whole run (``run_record`` / ``undo_run``): the bytes the run started from,
  zlib-compressed (``pack``), and a hash of what it left.

Undo refuses bytes that changed since the run wrote them (another tool
wrote over them), and reports a run whose bytes are all back to the old
values as already undone (the file was regenerated, e.g. by menu [1]).
"""

import base64
import hashlib
import os
import zlib

# Changed bytes closer than this are kept in one range.
MERGE_GAP = 16


class LedgerError(RuntimeError):
    pass


def diff_bytes(old: bytes, new: bytes) -> dict:
    """Ranges where ``new`` differs from ``old`` inside the shorter length, plus any length change."""
    common = min(len(old), len(new))
    ranges = []
    block = 0x1000
    pos = 0
    start = None
    last = None
    while pos < common:
        end = min(pos + block, common)
        if old[pos:end] == new[pos:end]:
            pos = end
            continue
        for i in range(pos, end):
            if old[i] != new[i]:
                if start is not None and i - last <= MERGE_GAP:
                    last = i
                else:
                    if start is not None:
                        ranges.append((start, last + 1))
                    start = last = i
        pos = end
    if start is not None:
        ranges.append((start, last + 1))
    return {
        'length_before': len(old),
        'length_after': len(new),
        'ranges': [[a, old[a:b].hex(), new[a:b].hex()] for a, b in ranges],
        'tail_sha1': hashlib.sha1(new[len(old):]).hexdigest() if len(new) > len(old) else None,
    }


def diff_is_empty(diff: dict) -> bool:
    return not diff['ranges'] and diff['length_before'] == diff['length_after']


def undo_diff(data: bytearray, diff: dict, what: str = 'main.dol') -> str:
    """Restore ``data`` in place. Returns ``'undone'`` or ``'already undone'``; raises on foreign changes."""
    if diff_is_empty(diff):
        return 'nothing to undo'
    ranges = [(a, bytes.fromhex(o), bytes.fromhex(n)) for a, o, n in diff['ranges']]
    ours = all(data[a:a + len(n)] == n for a, _o, n in ranges)
    # A range past the end lay in a section an earlier step appended: a regenerated file lacks both.
    theirs = all(data[a:a + len(o)] == o or a >= len(data) for a, o, _n in ranges)
    tail_ok = len(data) == diff['length_after'] and (
        diff['tail_sha1'] is None or hashlib.sha1(data[diff['length_before']:]).hexdigest() == diff['tail_sha1'])
    if ours and tail_ok:
        for a, o, _n in ranges:
            data[a:a + len(o)] = o
        del data[diff['length_before']:]
        if len(data) < diff['length_before']:
            raise LedgerError(f'{what}: shorter than before the step')
        return 'undone'
    if theirs and len(data) <= diff['length_before']:
        return 'already undone'
    changed = [f'0x{a:X}' for a, _o, n in ranges if data[a:a + len(n)] != n][:8]
    raise LedgerError(f'{what} changed since the roster step wrote it (offsets {", ".join(changed) or "tail"}); '
                      'restore it from the normal pipeline before running the roster expansion again')


def pack(data: bytes) -> str:
    """Bytes for a report: ``z:`` + base64 of the zlib stream."""
    return 'z:' + base64.b64encode(zlib.compress(bytes(data), 6)).decode('ascii')


def unpack(text) -> bytes:
    """``pack``'s inverse; plain hex (DOL diffs, older reports) and raw bytes pass through."""
    if isinstance(text, (bytes, bytearray)):
        return bytes(text)
    if text.startswith('z:'):
        return zlib.decompress(base64.b64decode(text[2:]))
    return bytes.fromhex(text)


def pre_run_ranges(range_lists) -> list[tuple[int, bytes]]:
    """The bytes a whole run started from: ``range_lists`` are the steps' ``[offset, old hex, new hex]``
    lists in run order; where steps overlap, the first step's old bytes win. Returns disjoint ranges."""
    covered: list[tuple[int, int]] = []          # sorted, disjoint
    out = []
    for ranges in range_lists:
        for offset, old, _new in ranges:
            old = unpack(old)
            start, end = offset, offset + len(old)
            cursor = start
            for lo, hi in covered:
                if hi <= cursor or lo >= end:
                    continue
                if lo > cursor:
                    out.append((cursor, old[cursor - start:lo - start]))
                cursor = max(cursor, hi)
            if cursor < end:
                out.append((cursor, old[cursor - start:]))
            covered = sorted(covered + [(start, end)])
            merged = []
            for lo, hi in covered:
                if merged and lo <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
                else:
                    merged.append((lo, hi))
            covered = merged
    return sorted(out)


def run_already_undone(data: bytes, diffs: list[dict]) -> bool:
    """True when ``data`` (a whole DOL) is back to the bytes the run started from, e.g. regenerated by the
    normal pipeline. Checked over the whole run, because later steps overwrite bytes earlier steps wrote
    (the DOL header, the hammerspace sections), so their own old bytes are not the clean file's."""
    if not diffs or all(diff_is_empty(d) for d in diffs):
        return False
    if len(data) > diffs[0]['length_before']:
        return False
    for offset, old in pre_run_ranges([d['ranges'] for d in diffs]):
        have = data[offset:offset + len(old)]
        if have != old[:len(have)]:
            return False
    return True


class DatFile:
    """Buffered, recorded writes to a large file. Nothing touches the disk before ``flush``."""

    def __init__(self, path: str):
        self.path = path
        self.disk_size = os.path.getsize(path)
        self.size = self.disk_size                   # grows with ``grow``; the new bytes read as zeros
        self.pending: list[tuple[int, bytes]] = []   # in write order; later writes win
        self.records: list[list] = []                # [offset, old bytes, new bytes] in write order

    def read(self, offset: int, size: int) -> bytes:
        if offset < 0 or offset + size > self.size:
            raise LedgerError(f'{self.path}: read 0x{offset:X}+0x{size:X} past the end (0x{self.size:X})')
        return self.read_padded(offset, size)

    def read_padded(self, offset: int, size: int) -> bytes:
        """Like ``read``, but bytes past the end read as zeros."""
        data = bytearray(size)
        if offset < self.disk_size:
            with open(self.path, 'rb') as f:
                f.seek(offset)
                blob = f.read(min(size, self.disk_size - offset))
            data[:len(blob)] = blob
        for at, blob in self.pending:
            lo, hi = max(at, offset), min(at + len(blob), offset + size)
            if lo < hi:
                data[lo - offset:hi - offset] = blob[lo - at:hi - at]
        return bytes(data)

    def write(self, offset: int, data: bytes) -> None:
        """Write ``data``; this step's records stay disjoint (an overlapping write is merged in)."""
        data = bytes(data)
        if self.read(offset, len(data)) == data:
            return
        lo, hi = offset, offset + len(data)
        merged = [r for r in self.records if r[0] < hi and lo < r[0] + len(r[1])]
        for r in merged:
            lo, hi = min(lo, r[0]), max(hi, r[0] + len(r[1]))
        old = bytearray(self.read(lo, hi - lo))
        for r in merged:                       # back to the bytes before this step
            old[r[0] - lo:r[0] - lo + len(r[1])] = r[1]
        self.pending.append((offset, data))
        new = self.read(lo, hi - lo)
        self.records = [r for r in self.records if r not in merged] + [[lo, bytes(old), new]]

    def grow(self, size: int) -> None:
        """Make the file at least ``size`` bytes long (zero-filled at ``flush``). Growth is never undone."""
        self.size = max(self.size, size)

    @property
    def grown(self) -> bool:
        return self.size > self.disk_size

    def flush(self) -> None:
        if self.size > self.disk_size:
            with open(self.path, 'r+b') as f:
                f.seek(self.disk_size)
                left = self.size - self.disk_size
                while left:
                    step = min(left, 1 << 20)
                    f.write(bytes(step))
                    left -= step
            self.disk_size = self.size
        if not self.pending:
            return
        with open(self.path, 'r+b') as f:
            for offset, blob in self.pending:
                f.seek(offset)
                f.write(blob)
        self.pending.clear()

    def take_raw(self) -> list[list]:
        """This step's records as ``[offset, old bytes, new bytes]``; starts the next step's."""
        records, self.records = self.records, []
        return records

    def run_record(self, raw_lists: list[list]) -> list[list]:
        """One record for a whole run (``raw_lists``: each step's ``take_raw`` in run order):
        ``[offset, packed bytes the run started from, sha1 of the bytes it left, length]`` per disjoint range.

        Undo only ever takes a whole run out, so the report needs neither the intermediate states nor the
        written bytes themselves: a step that moves a grown file records the previous copy as its old
        bytes, and the select layout's three copies made that several MB per step."""
        out = []
        for offset, original in pre_run_ranges(raw_lists):
            final = self.read_padded(offset, len(original))
            out.append([offset, pack(original), hashlib.sha1(final).hexdigest(), len(original)])
        return out

    def undo_run(self, records: list[list]) -> str:
        """Take a whole run out (``run_record``'s records). Undo writes are not recorded."""
        if not records:
            return 'undone'
        records = [(offset, unpack(original), sha1, length) for offset, original, sha1, length in records]
        if all(self.read_padded(offset, length) == original for offset, original, _s, length in records):
            return 'already undone'
        for offset, _original, sha1, length in records:
            if hashlib.sha1(self.read_padded(offset, length)).hexdigest() != sha1:
                raise LedgerError(f'{self.path}: 0x{offset:X} changed since the roster expansion wrote it')
        self.pending += [(offset, original) for offset, original, _s, _n in records]
        return 'undone'
