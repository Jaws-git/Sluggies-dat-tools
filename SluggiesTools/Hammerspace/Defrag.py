"""Hammerspace defragmentation: pack the live blocks of the output ``dt_na.dat`` past ``BASE_SIZE`` and truncate.

Re-patching leaves zeroed holes behind (the allocators reuse them only when a new block fits), and the untangle
clone step grows the file in large steps. This module rewrites the hammerspace tail:

- **What is live.** Every hammerspace block is reached through a DOL directory record (stock directories and
  the own model directories of new IDs), each with three language slots of ``(length, offset, alloc)``. Every
  slot routed past ``BASE_SIZE`` is a live range; overlapping ranges (sharers, an archive container and a
  member, languages of different lengths) merge into one **extent**, which moves as a unit so offsets inside
  it stay valid. Nothing else stores DAT offsets of hammerspace blocks (roster manifest: none; ``.sluggie``
  files exported from hammerspace are rebased onto their vanilla donor when patched; slot exports carry their
  own donor bytes).
- **Layout.** Extents keep their order and are packed from ``BASE_SIZE`` up, each at the first offset with
  its old alignment modulo ``HS_ALIGN_BYTES`` (so a block never moves up, and 32-aligned blocks stay
  32-aligned). Adjacent blocks touch, as the allocators place them; the file ends ``HS_BUFFER_BYTES`` after
  the last block. Unrouted non-zero bytes (leftovers) are dropped.
- **Pointers.** Each slot's offset shifts by its extent's move (length and alloc stay); the FST gets the new
  file size.
- **Safety.** Before writing, the output ``main.dol``, ``fst.bin`` and the DAT tail from the first changed byte
  are copied into ``3_Output_Dat/_defrag_backup`` (its ``defrag.json`` is written last). Every route is hashed
  before and checked after; any failure restores the backup. A backup left by an interrupted run is restored
  by the next run, which then stops.

Run as a script (``start.py --defrag-hammerspace [--dry-run]``) or use :func:`analyze` / :func:`run`.
"""

import bisect
import hashlib
import json
import os
import shutil
import struct
import sys
from dataclasses import dataclass, field

import numpy as np

try:
    import HammerspaceHelper as hh
except ImportError:
    from . import HammerspaceHelper as hh

import slogger as _slogger                     # HammerspaceHelper put SluggiesTools on sys.path

SOURCE = 'hammerspace.defrag'
SLOTS = ((1, 2), (5, 6), (9, 10))             # (length word, offset word) of the en, fr, sp slots
BACKUP_FOLDER = '_defrag_backup'
_BACKUP_INFO = 'defrag.json'
_BACKUP_TAIL = 'dt_na.tail'
_BACKUP_DOL = 'main.dol'
_BACKUP_FST = 'fst.bin'
_SCAN_CHUNK = 1 << 20
# Log texts the GUI's Maintenance tab looks for in a run's output.
COMPACT_TEXT = 'Hammerspace is already compact'
RESTORED_TEXT = 'restored from the backup of an interrupted defragmentation'


class DefragError(RuntimeError):
    pass


@dataclass(frozen=True)
class Route:
    chunk: int
    index: int
    record: int                                # file offset of the 48-byte record in the output main.dol
    slot: int                                  # index into SLOTS
    offset: int
    length: int

    @property
    def end(self):
        return self.offset + self.length


@dataclass
class Extent:
    start: int
    end: int
    new_start: int = 0

    @property
    def length(self):
        return self.end - self.start

    @property
    def delta(self):
        return self.new_start - self.start


@dataclass
class Layout:
    dat_size: int
    new_size: int
    extents: list
    routes: list
    leftover_bytes: int = 0

    @property
    def moved(self):
        return [e for e in self.extents if e.delta]

    @property
    def live_bytes(self):
        return sum(e.length for e in self.extents)

    @property
    def hammerspace_bytes(self):
        return max(0, self.dat_size - hh.BASE_SIZE)

    @property
    def saved(self):
        return self.dat_size - self.new_size

    @property
    def compact(self):
        """Nothing to gain: no block moves and the file is no longer than the packed layout."""
        return not self.moved and self.saved <= 0

    def first_change(self):
        """The lowest DAT offset this layout writes (a moved extent's new place, else the truncation point)."""
        moved = self.moved
        return moved[0].new_start if moved else self.new_size


@dataclass
class Report:
    size_before: int
    size_after: int
    moved: int
    extents: int
    leftover_bytes: int
    dry_run: bool = False
    restored: bool = False                     # an interrupted run's backup was restored instead
    warnings: list = field(default_factory=list)


def _backup_dir():
    return os.path.join(os.path.dirname(hh.OUTPUT_DAT), BACKUP_FOLDER)


def backup_pending():
    """Whether an interrupted run left a complete backup (the next run restores it)."""
    return os.path.isfile(os.path.join(_backup_dir(), _BACKUP_INFO))


def _mb(n):
    return f'{n / (1024 * 1024):,.2f} MB'


# ------------------------------------------------------------------------------------------------ analysis

def collect_routes():
    """Every language slot of the output main.dol routed into hammerspace."""
    routes = []
    for chunk, index, record, words in hh._iterDirRecords(hh.OUTPUT_DOL):
        for slot, (length_word, offset_word) in enumerate(SLOTS):
            offset, length = words[offset_word], words[length_word]
            if length <= 0:
                continue
            if offset >= hh.BASE_SIZE:
                routes.append(Route(chunk, index, record, slot, offset, length))
            elif offset + length > hh.BASE_SIZE:
                raise DefragError(f'route ({chunk},{index}) 0x{offset:08X}+{length:,} starts below BASE_SIZE and '
                                  'ends in hammerspace; refusing to move anything')
    return routes


def merge_extents(routes):
    """Sorted extents: overlapping routes merged (touching ones stay separate)."""
    extents = []
    for route in sorted(routes, key=lambda r: (r.offset, r.end)):
        if extents and route.offset < extents[-1].end:
            extents[-1].end = max(extents[-1].end, route.end)
        else:
            extents.append(Extent(route.offset, route.end))
    return extents


def plan_layout(extents):
    """Give each extent its packed place; returns the new file size."""
    align = hh.HS_ALIGN_BYTES
    cursor = hh.BASE_SIZE
    for extent in extents:
        # the first offset >= cursor with the extent's own alignment: never above its old place
        extent.new_start = cursor + (extent.start - cursor) % align
        cursor = extent.new_start + extent.length
    if not extents:
        return hh.BASE_SIZE
    return cursor + hh.HS_BUFFER_BYTES


def _leftover_bytes(extents, dat_size):
    """Non-zero bytes in hammerspace that no route covers, counted in whole ``HS_ALIGN_BYTES`` blocks."""
    align = hh.HS_ALIGN_BYTES
    start = -(-hh.BASE_SIZE // align) * align
    if dat_size <= start:
        return 0
    busy_total = 0
    spans = [(e.start, e.end) for e in extents]
    with open(hh.OUTPUT_DAT, 'rb') as dat:
        pos = start
        dat.seek(pos)
        while pos < dat_size:
            chunk = dat.read(min(_SCAN_CHUNK, dat_size - pos))
            if not chunk:
                break
            padded = chunk + bytes(-len(chunk) % align)
            busy = np.frombuffer(padded, dtype=np.uint8).reshape(-1, align).any(axis=1)
            first = pos // align
            for lo, hi in spans:
                if hi <= pos or lo >= pos + len(chunk):
                    continue
                busy[max(lo, pos) // align - first:-(-min(hi, pos + len(chunk)) // align) - first] = False
            busy_total += int(busy.sum())
            pos += len(chunk)
    return busy_total * align


def analyze(count_leftovers=True):
    """The packed layout for the current output files (reads only)."""
    for path in (hh.OUTPUT_DAT, hh.OUTPUT_DOL, hh.INPUT_DOL):
        if not os.path.isfile(path):
            raise DefragError(f'missing {path}')
    dat_size = os.path.getsize(hh.OUTPUT_DAT)
    routes = collect_routes()
    extents = merge_extents(routes)
    for extent in extents:
        if extent.end > dat_size:
            raise DefragError(f'a route ends at 0x{extent.end:08X}, past the end of dt_na.dat ({dat_size:,} bytes)')
    new_size = plan_layout(extents)
    leftovers = _leftover_bytes(extents, dat_size) if count_leftovers else 0
    return Layout(dat_size, new_size, extents, routes, leftovers)


def unknown_pointers(layout, dol_bytes):
    """File offsets of aligned DOL words outside the directory records' offset words that equal the start of a
    moving extent (a pointer this module does not know about would break)."""
    starts = np.array(sorted({e.start for e in layout.moved}), dtype='>u4')
    if not starts.size:
        return []
    usable = len(dol_bytes) - len(dol_bytes) % 4
    words = np.frombuffer(bytes(dol_bytes[:usable]), dtype='>u4')
    hits = np.flatnonzero(np.isin(words, starts)) * 4
    known = {route.record + 4 * SLOTS[slot][1] for route in layout.routes for slot in range(len(SLOTS))}
    return [int(pos) for pos in hits if int(pos) not in known]


# ------------------------------------------------------------------------------------------------ backup

def _write_backup(layout):
    folder = _backup_dir()
    if os.path.exists(folder):
        shutil.rmtree(folder)
    os.makedirs(folder)
    tail_offset = layout.first_change()
    with open(hh.OUTPUT_DAT, 'rb') as dat, open(os.path.join(folder, _BACKUP_TAIL), 'wb') as out:
        dat.seek(tail_offset)
        shutil.copyfileobj(dat, out, _SCAN_CHUNK)
    shutil.copy2(hh.OUTPUT_DOL, os.path.join(folder, _BACKUP_DOL))
    has_fst = os.path.isfile(hh._FST_OUTPUT)
    if has_fst:
        shutil.copy2(hh._FST_OUTPUT, os.path.join(folder, _BACKUP_FST))
    info = {'tail_offset': tail_offset, 'dat_size': layout.dat_size, 'fst': has_fst}
    with open(os.path.join(folder, _BACKUP_INFO), 'w', encoding='utf-8') as f:     # last: the backup is complete
        json.dump(info, f)


def _drop_backup():
    folder = _backup_dir()
    info = os.path.join(folder, _BACKUP_INFO)
    if os.path.exists(info):
        os.remove(info)                        # first: a half-deleted backup is never restored
    shutil.rmtree(folder, ignore_errors=True)


def restore_backup():
    """Put the files back as the backup holds them. False when there is no complete backup."""
    folder = _backup_dir()
    info_path = os.path.join(folder, _BACKUP_INFO)
    if not os.path.isfile(info_path):
        return False
    with open(info_path, encoding='utf-8') as f:
        info = json.load(f)
    tail_offset, dat_size = int(info['tail_offset']), int(info['dat_size'])
    with open(hh.OUTPUT_DAT, 'r+b') as dat, open(os.path.join(folder, _BACKUP_TAIL), 'rb') as tail:
        dat.seek(tail_offset)
        shutil.copyfileobj(tail, dat, _SCAN_CHUNK)
        dat.truncate(dat_size)
    if os.path.getsize(hh.OUTPUT_DAT) != dat_size:
        raise DefragError('restoring the backup did not give back the old dt_na.dat size')
    shutil.copy2(os.path.join(folder, _BACKUP_DOL), hh.OUTPUT_DOL)
    if info.get('fst'):
        shutil.copy2(os.path.join(folder, _BACKUP_FST), hh._FST_OUTPUT)
    _drop_backup()
    return True


# ------------------------------------------------------------------------------------------------ the run

def _hash_routes(routes):
    """``{(record, slot): sha1}`` of every route's bytes (each range read once)."""
    by_range = {}
    with open(hh.OUTPUT_DAT, 'rb') as dat:
        for route in routes:
            key = (route.offset, route.length)
            if key not in by_range:
                dat.seek(route.offset)
                by_range[key] = hashlib.sha1(dat.read(route.length)).hexdigest()
    return {(r.record, r.slot): by_range[(r.offset, r.length)] for r in routes}


def _move_extents(layout):
    """Write every moved extent at its new place (ascending: a target never reaches an unread source), zero
    the alignment gaps between the packed extents and truncate."""
    with open(hh.OUTPUT_DAT, 'r+b') as dat:
        for extent in layout.extents:
            if not extent.delta:
                continue
            dat.seek(extent.start)
            data = dat.read(extent.length)
            if len(data) != extent.length:
                raise DefragError(f'short read at 0x{extent.start:08X}')
            dat.seek(extent.new_start)
            dat.write(data)
        cursor = hh.BASE_SIZE
        for extent in layout.extents + [Extent(layout.new_size, layout.new_size, layout.new_size)]:
            if extent.new_start > cursor:
                dat.seek(cursor)
                dat.write(bytes(extent.new_start - cursor))
            cursor = extent.new_start + extent.length
        dat.truncate(layout.new_size)


def _extent_lookup(layout):
    """``route -> its extent`` (extents are sorted and disjoint; every route lies in one)."""
    starts = [e.start for e in layout.extents]
    return lambda route: layout.extents[bisect.bisect_right(starts, route.offset) - 1]


def _patch_dol(layout):
    """Shift every route's offset in all language slots by its extent's move; one write of the whole DOL."""
    with open(hh.OUTPUT_DOL, 'rb') as f:
        dol = bytearray(f.read())
    extent_of = _extent_lookup(layout)
    for route in layout.routes:
        delta = extent_of(route).delta
        if not delta:
            continue
        position = route.record + 4 * SLOTS[route.slot][1]
        if struct.unpack_from('>I', dol, position)[0] != route.offset:
            raise DefragError(f'the DOL changed under the run (route ({route.chunk},{route.index}))')
        struct.pack_into('>I', dol, position, route.offset + delta)
    temp = hh.OUTPUT_DOL + '.defrag'
    with open(temp, 'wb') as f:
        f.write(dol)
    os.replace(temp, hh.OUTPUT_DOL)


def _verify(layout, before):
    extent_of = _extent_lookup(layout)
    expected = {(r.record, r.slot): (r.offset + extent_of(r).delta, r.length) for r in layout.routes}
    after = collect_routes()
    actual = {(r.record, r.slot): (r.offset, r.length) for r in after}
    if actual != expected:
        raise DefragError('the patched DOL routes do not match the planned layout')
    hashes = _hash_routes(after)
    bad = [key for key, digest in hashes.items() if before.get(key) != digest]
    if bad:
        raise DefragError(f'{len(bad)} route(s) read different bytes after the move')
    if os.path.getsize(hh.OUTPUT_DAT) != layout.new_size:
        raise DefragError('dt_na.dat does not have the planned size')


def run(dry_run=False):
    """Defragment the output hammerspace (or, with ``dry_run``, only report what would happen)."""
    if backup_pending():
        if dry_run:
            raise DefragError(f'an interrupted defragmentation left a backup in {_backup_dir()}; run without '
                              '--dry-run to restore it')
        restore_backup()
        size = os.path.getsize(hh.OUTPUT_DAT)
        _slogger.warning(f'dt_na.dat, main.dol and fst.bin were {RESTORED_TEXT}. Run it again to defragment.',
                         source=SOURCE)
        return Report(size, size, 0, 0, 0, restored=True)
    if os.path.exists(_backup_dir()):
        shutil.rmtree(_backup_dir(), ignore_errors=True)     # incomplete backup: nothing was written yet

    layout = analyze()
    report = Report(layout.dat_size, layout.new_size, len(layout.moved), len(layout.extents),
                    layout.leftover_bytes, dry_run=dry_run)
    _slogger.info(
        f'Hammerspace: {len(layout.extents)} block(s), {_mb(layout.live_bytes)} live of '
        f'{_mb(layout.hammerspace_bytes)}; {_mb(layout.leftover_bytes)} unrouted leftovers', source=SOURCE)
    if layout.compact:
        report.size_after = layout.dat_size
        report.moved = 0
        _slogger.info(f'{COMPACT_TEXT}; nothing to do.', source=SOURCE)
        return report

    with open(hh.OUTPUT_DOL, 'rb') as f:
        strays = unknown_pointers(layout, f.read())
    for pos in strays:
        report.warnings.append(f'main.dol word at file offset 0x{pos:X} equals a moving block offset but is not a '
                               'directory record; it is left as it is')
        _slogger.warning(report.warnings[-1], source=SOURCE)

    for extent in layout.moved:
        _slogger.info(f'{"Would move" if dry_run else "Move"} 0x{extent.start:08X} -> 0x{extent.new_start:08X} '
                      f'({extent.length:,} bytes)', source=SOURCE)
    if dry_run:
        _summary(report)
        return report

    before = _hash_routes(layout.routes)
    _write_backup(layout)
    try:
        _move_extents(layout)
        _patch_dol(layout)
        if not hh.patchFstFileSize(layout.new_size):
            raise DefragError('fst.bin could not be updated')
        _verify(layout, before)
    except BaseException:
        _slogger.error('Defragmentation failed; restoring dt_na.dat, main.dol and fst.bin from the backup.',
                       source=SOURCE)
        restore_backup()
        raise
    _drop_backup()
    _summary(report)
    return report


def _summary(report):
    verb = 'would shrink' if report.dry_run else 'shrank'
    _slogger.info(
        f'Hammerspace defrag: {"would move" if report.dry_run else "moved"} {report.moved} block(s); dt_na.dat '
        f'{verb} from {report.size_before:,} to {report.size_after:,} bytes '
        f'({_mb(report.size_before - report.size_after)} saved)', source=SOURCE)
    if not report.dry_run:
        _slogger.info('Copy dt_na.dat, main.dol and fst.bin to the game together (CopyFilesToGameDir.bat does).',
                      source=SOURCE)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Defragment the output dt_na.dat hammerspace.')
    parser.add_argument('--dry-run', action='store_true', help='report what would move, write nothing')
    args = parser.parse_args(argv)
    try:
        run(dry_run=args.dry_run)
    except DefragError as exc:
        _slogger.error(f'Hammerspace defrag refused: {exc}', source=SOURCE)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
