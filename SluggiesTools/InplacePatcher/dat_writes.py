"""Guarded writes into the output ``dt_na.dat`` for the in-place patcher.

An in-place patch writes at offsets taken from the ``.sluggie``. :class:`GuardedDat` is the file handle those
writes go through: it refuses a write that leaves the model's own block (or the end of the file), and it keeps
the bytes every write replaces, so a patch that fails midway is undone (:meth:`GuardedDat.rollback`, run by the
``with`` block on any exception) and the DAT is as it was before the run.

Importing this module has no side effects.
"""

import os


class OutOfBlockWrite(ValueError):
    """A write would leave the model's block or the file."""


def block_problem(start: int, length: int, dat_size: int, record: tuple[int, int] | None = None) -> str | None:
    """Why ``[start, start + length)`` cannot be the block an in-place patch writes into, or None.

    ``dat_size``: size of the *input* ``dt_na.dat`` (a block past it is a hammerspace block, whose place is not
    fixed). ``record``: ``(offset, length)`` of the model's directory record in the input ``main.dol``, when it
    could be read; the block has to lie inside it (an archive member is a part of its record)."""
    if length <= 0 or start < 0:
        return f"the model block 0x{start:X}+{length:,} is not a valid range"
    if start + length > dat_size:
        return (f"the model block 0x{start:X}+{length:,} lies past the input dt_na.dat ({dat_size:,} bytes): the "
                "file was exported from a patched game (a hammerspace block), which only the Hammerspace builder "
                "can patch")
    if record is not None:
        record_offset, record_length = record
        if not (record_offset <= start and start + length <= record_offset + record_length):
            return (f"the model block 0x{start:X}+{length:,} is not inside the model's directory record "
                    f"(0x{record_offset:X}+{record_length:,} in the input main.dol): the .sluggie does not "
                    "belong to these game files")
    return None


class RollbackFailed(RuntimeError):
    """A failed patch could not be undone: the DAT holds a part of it."""


def fit_to_block(writes, start: int, length: int):
    """Sort ``(label, offset, data)`` writes by how they sit in the block ``[start, start + length)``.

    Returns ``(kept, cut, outside)``: ``kept`` are the writes to make; ``cut`` describes those that began inside
    the block and ran past its end, kept in ``kept`` without the overhang; ``outside`` describes those that
    begin outside the block (not in ``kept``). Empty writes are dropped.

    An overhang is cut, not refused, because a vanilla export can carry one: the exporter reads a normal buffer
    by its declared count, and for one stock model (``ice_block_koopa``, checked over 1,588 exports on
    2026-11-15) that runs 6.9 KB into the next model. Those bytes are the neighbour's and stay untouched.
    """
    end = start + length
    kept, cut, outside = [], [], []
    for label, offset, data in writes:
        if not data:
            continue
        if not start <= offset < end:
            outside.append(f"{label}: 0x{offset:X}+{len(data):,}")
        elif offset + len(data) > end:
            cut.append(f"{label}: 0x{offset:X}+{len(data):,} cut to {end - offset:,} bytes")
            kept.append((label, offset, data[:end - offset]))
        else:
            kept.append((label, offset, data))
    return kept, cut, outside


class GuardedDat:
    """``r+b`` handle on a DAT that only writes inside ``[start, start + length)`` and can undo its writes.

    Offers the ``seek`` / ``read`` / ``write`` / ``tell`` subset the patchers use. Use as a context manager: an
    exception inside the block (``SystemExit`` included) rolls every write back before the file is closed.
    """

    def __init__(self, path: str, start: int, length: int):
        self.path = path
        self.start = start
        self.end = start + length
        self._file = open(path, 'r+b')
        self._size = os.path.getsize(path)
        self._journal: list[tuple[int, bytes]] = []      # (offset, the bytes a write replaced), in write order
        self.rolled_back = False

    # -- the file subset -------------------------------------------------------------------------------------
    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        return self._file.seek(offset, whence)

    def tell(self) -> int:
        return self._file.tell()

    def read(self, size: int = -1) -> bytes:
        return self._file.read(size)

    def write(self, data) -> int:
        data = bytes(data)
        if not data:
            return 0
        offset = self._file.tell()
        if not (self.start <= offset and offset + len(data) <= self.end):
            raise OutOfBlockWrite(
                f"write of {len(data):,} bytes at 0x{offset:X} is outside the model block "
                f"0x{self.start:X}..0x{self.end:X}")
        if offset + len(data) > self._size:
            raise OutOfBlockWrite(
                f"write of {len(data):,} bytes at 0x{offset:X} passes the end of the file ({self._size:,} bytes)")
        before = self._file.read(len(data))
        if len(before) != len(data):
            raise OutOfBlockWrite(f"short read at 0x{offset:X}: the file ends inside the model block")
        self._journal.append((offset, before))
        self._file.seek(offset)
        return self._file.write(data)

    # -- undo -------------------------------------------------------------------------------------------------
    @property
    def bytes_written(self) -> int:
        return sum(len(before) for _offset, before in self._journal)

    def rollback(self) -> None:
        """Put back what every write replaced (newest first). Raises ``OSError`` when that fails."""
        while self._journal:
            offset, before = self._journal[-1]
            self._file.seek(offset)
            self._file.write(before)
            self._journal.pop()
        self._file.flush()
        self.rolled_back = True

    def close(self) -> None:
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None and self._journal:
                try:
                    self.rollback()
                except OSError as rollback_error:
                    raise RollbackFailed(
                        f"{exc}; undoing the writes failed too ({rollback_error}), "
                        f"{len(self._journal)} write(s) are still in the file") from exc
        finally:
            self.close()
        return False
