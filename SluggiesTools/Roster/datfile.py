"""Buffered writes to ``dt_na.dat`` for the roster steps.

The DAT is about 700 MB, so a run never loads it: ``DatFile`` reads the file
on demand, keeps every write in memory (later writes win), and touches the
disk only in ``flush``. Steps of one run therefore see each other's writes,
and a dry run writes nothing.
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
