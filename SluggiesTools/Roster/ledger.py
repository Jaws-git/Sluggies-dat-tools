"""Undo records for roster-expansion steps.

Every step's changes are recorded so the development injector (menu [10])
can take them back out before it runs again, newest step first:

* ``diff_bytes`` / ``undo_diff``: a byte-level diff of the whole ``main.dol``
  before and after one step (the DOL is about 7 MB, so this is cheap), with
  any appended tail checked by hash;
* ``DatFile``: buffered writes to ``dt_na.dat`` that remember the bytes they
  replace (the DAT is far too large to diff).

Undo refuses bytes that changed since the step wrote them (another tool
wrote over them), and reports a record whose bytes are all back to the old
values as already undone (the file was regenerated, e.g. by menu [1]).
"""

import hashlib
import os

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


class DatFile:
    """Buffered, recorded writes to a large file. Nothing touches the disk before ``flush``."""

    def __init__(self, path: str):
        self.path = path
        self.disk_size = os.path.getsize(path)
        self.size = self.disk_size                   # grows with ``grow``; the new bytes read as zeros
        self.pending: list[tuple[int, bytes]] = []   # in write order; later writes win
        self.records: list[list] = []                # [offset, old hex, new hex] in write order

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
        merged = [r for r in self.records if r[0] < hi and lo < r[0] + len(r[1]) // 2]
        for r in merged:
            lo, hi = min(lo, r[0]), max(hi, r[0] + len(r[1]) // 2)
        old = bytearray(self.read(lo, hi - lo))
        for r in merged:                       # back to the bytes before this step
            old[r[0] - lo:r[0] - lo + len(r[1]) // 2] = bytes.fromhex(r[1])
        self.pending.append((offset, data))
        new = self.read(lo, hi - lo)
        self.records = [r for r in self.records if r not in merged] + [[lo, old.hex(), new.hex()]]

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

    def take_records(self) -> list[list]:
        records, self.records = self.records, []
        return records

    def undo(self, records: list[list]) -> str:
        """Take back one step's ``records``, newest write first. Undo writes are not recorded."""
        if not records:
            return 'undone'
        # Bytes past the end read as zeros: a regenerated (shorter) file holds none of our appended bytes.
        if all(self.read_padded(o, len(old) // 2) == bytes.fromhex(old) for o, old, _n in records) and \
                not all(self.read_padded(o, len(n) // 2) == bytes.fromhex(n) for o, _old, n in records):
            return 'already undone'
        for offset, old, new in reversed(records):
            if self.read_padded(offset, len(new) // 2) != bytes.fromhex(new):
                raise LedgerError(f'{self.path}: 0x{offset:X} changed since the roster step wrote it')
            self.pending.append((offset, bytes.fromhex(old)))
        return 'undone'
