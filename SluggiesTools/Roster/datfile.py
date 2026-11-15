"""Buffered writes to ``dt_na.dat`` for the roster steps.

The DAT is about 700 MB, so a run never loads it: ``DatFile`` reads the file
on demand, keeps every write in memory (later writes win), and touches the
disk only in ``flush``. Steps of one run therefore see each other's writes,
and a dry run writes nothing.

``flush(keep_undo=True)`` also keeps the bytes it replaces, so the caller can
take the flush back (``rollback``) when the file that belongs to it
(``main.dol``) cannot be written afterwards; a flush that fails midway takes
itself back.
"""

import os


class DatFileError(RuntimeError):
    pass


class DatFile:
    """Buffered writes to a large file. Nothing touches the disk before ``flush``."""

    def __init__(self, path: str):
        self.path = path
        self.disk_size = os.path.getsize(path)
        self.size = self.disk_size                   # grows with ``grow``; the new bytes read as zeros
        self.pending: list[tuple[int, bytes]] = []   # in write order; later writes win
        self._undo: tuple[int, list[tuple[int, bytes]]] | None = None   # of the last flush(keep_undo=True)
        self.version = 0                             # counts flushes and rollbacks: ``pending`` started over
        self.hs_busy_cache = None                    # dat_hammerspace's map of busy blocks (its own business)

    def read(self, offset: int, size: int) -> bytes:
        if offset < 0 or offset + size > self.size:
            raise DatFileError(f'{self.path}: read 0x{offset:X}+0x{size:X} past the end (0x{self.size:X})')
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
        data = bytes(data)
        if self.read(offset, len(data)) != data:
            self.pending.append((offset, data))

    def grow(self, size: int) -> None:
        """Make the file at least ``size`` bytes long (zero-filled at ``flush``). The file never shrinks."""
        self.size = max(self.size, size)

    @property
    def grown(self) -> bool:
        return self.size > self.disk_size

    def flush(self, keep_undo: bool = False) -> None:
        """Write the growth and every pending write to the disk.

        ``keep_undo``: remember what the flush replaces, for ``rollback``. A flush that fails then puts the
        file back itself before the error leaves (``DatFileError`` when even that fails); without it a failed
        flush leaves the file partly written."""
        old_size = self.disk_size
        undo: list[tuple[int, bytes]] = []
        try:
            if self.size > self.disk_size:
                with open(self.path, 'r+b') as f:
                    f.seek(self.disk_size)
                    left = self.size - self.disk_size
                    while left:
                        step = min(left, 1 << 20)
                        f.write(bytes(step))
                        left -= step
            if self.pending:
                with open(self.path, 'r+b') as f:
                    for offset, blob in self.pending:
                        if keep_undo:
                            f.seek(offset)
                            undo.append((offset, f.read(len(blob))))
                        f.seek(offset)
                        f.write(blob)
        except BaseException as exc:
            if keep_undo:
                self._restore(old_size, undo, exc)
            raise
        self.disk_size = self.size
        self.pending.clear()
        self.version += 1
        self._undo = (old_size, undo) if keep_undo else None

    def rollback(self) -> None:
        """Take the last ``flush(keep_undo=True)`` back: the replaced bytes and the old length."""
        if self._undo is None:
            raise DatFileError(f'{self.path}: no flush to take back')
        old_size, undo = self._undo
        self._undo = None
        self._restore(old_size, undo, None)
        self.disk_size = self.size = old_size
        self.version += 1

    def _restore(self, old_size: int, undo: list[tuple[int, bytes]], cause) -> None:
        try:
            with open(self.path, 'r+b') as f:
                for offset, before in reversed(undo):
                    f.seek(offset)
                    f.write(before)
                f.truncate(old_size)
        except OSError as exc:
            raise DatFileError(
                f'{self.path}: the write could not be taken back ({exc}); the file is partly written') from cause
