"""One writer of the output sound archive at a time, inside one process.

``3_Output_Dat/sound_NA/MY2.brsar`` is rewritten whole by each change: the Sound FX worker
(``effects.sync_output``) and Set volume from three GUI tabs (``volumes.set_volume``) each read it, change it and
replace it. Two of them overlapping lost one change without a word, or failed on the temp file they shared.
``writing()`` puts the whole read-modify-write under one lock; ``busy()`` tells the GUI that a sound write is
running or waiting, so it does not start a command that rewrites the archive from another process (Patch Game,
a roster run) in between; ``temp_path`` is a temp name no other writer uses.
"""

import contextlib
import os
import threading

_LOCK = threading.RLock()            # re-entrant: set_volume makes the archive through sync_output first
_COUNT_LOCK = threading.Lock()
_writers = 0                         # writes running or waiting for the lock


@contextlib.contextmanager
def writing():
    """Hold the archive for one read-modify-write."""
    global _writers
    with _COUNT_LOCK:
        _writers += 1
    try:
        with _LOCK:
            yield
    finally:
        with _COUNT_LOCK:
            _writers -= 1


def busy() -> bool:
    """Whether a sound write of this process runs or waits."""
    return _writers > 0


def temp_path(path: str) -> str:
    """A temp file beside ``path`` for this process and thread only."""
    return f'{path}.{os.getpid()}.{threading.get_ident()}.tmp'
