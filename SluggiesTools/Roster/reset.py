"""Reset the roster to vanilla: take any roster injection out of the output files, using ``1_Input`` as reference.

Works like the model unpatcher: the original bytes come from the input
files, not from a record of what an earlier run changed, so it needs no
report and also cleans files injected by an older version.

``main.dol`` is rebuilt from ``1_Input/main.dol``: everything the roster
changes there (code sites, per-ID tables, the arena start, the two added
sections) goes back to the input bytes. Kept from the output are the
directory records, which are the only DOL bytes the other tools write
(model patches, the untangler, the icon reimport) — except the three
records the roster owns (the select layout, the icon bank and the name
table), which go back to the input too.

``dt_na.dat``: the roster writes only past the stock end (``BASE_SIZE``), into
the copies those three records point at and the copies of own model
directories (``model_dirs.py``, whose records live in the removed DOL data
section). They are zeroed, so the space is free again; the file keeps its size
(and ``fst.bin`` its entry). Copies the next run keeps (``keep``: the derived
config's ``model.routes``) are left as they are.
"""

import struct

try:
    from ..Dol import dolfile
    from . import dat_hammerspace as dhs
    from . import dol_hammerspace, icons, layout_file, model_dirs, names
except ImportError:
    from Dol import dolfile
    import dat_hammerspace as dhs
    import dol_hammerspace
    import icons
    import layout_file
    import model_dirs
    import names


class ResetError(RuntimeError):
    pass


def _has_directory(image: dolfile.DolImage) -> bool:
    return image.is_mapped(dhs.dol_base_address(dhs.hh._DIRS_START), 4 * dhs.hh._DIRS_COUNT)


def directory_records(image: dolfile.DolImage) -> list[int]:
    """Addresses of every dt_na.dat directory record, once each."""
    if not _has_directory(image):
        return []
    return sorted({record for _c, _i, record, _w in dhs.iter_records(image)})


def roster_records(image: dolfile.DolImage) -> list[int]:
    """The records the roster expansion repoints."""
    out = [layout_file.RECORD, icons.ICON_RECORD]
    if _has_directory(image):
        out.append(names.name_record(image))
    return out


def _slots(image: dolfile.DolImage, record: int) -> list[tuple[int, int]]:
    if not image.is_mapped(record, dhs.RECORD_SIZE):
        return []
    words = struct.unpack('>12I', image.read(record, dhs.RECORD_SIZE))
    if words[0::4] != (dhs.hh._DAT_FNAME_PTR,) * 3:
        return []
    return [dhs.slot(list(words), lang)[:2] for lang in dhs.LANGS]


def _without_roster_sections(image: dolfile.DolImage) -> dolfile.DolImage:
    image = dolfile.DolImage(image.to_bytes())
    for base in (dol_hammerspace.DATA_BASE, dol_hammerspace.TEXT_BASE):
        slot = image.section_at(base)
        if slot is not None:
            image.remove_section(slot)
    return image


def reset_dol(current: bytes, vanilla: bytes) -> tuple[bytes, list[str]]:
    """``vanilla`` with the output's directory records, except the roster's own."""
    stock = dolfile.DolImage(vanilla)
    if dol_hammerspace.DolHammerspace.open(stock) is not None:
        raise ResetError('1_Input/main.dol already holds a roster injection; put the original game file there')
    out = dolfile.DolImage(current)
    had_sections = out.section_at(dol_hammerspace.TEXT_BASE) is not None
    bare = _without_roster_sections(out)
    shape = [(s.address, s.size) for s in bare.header.used_slots]
    if shape != [(s.address, s.size) for s in stock.header.used_slots] or len(bare.to_bytes()) != len(vanilla):
        raise ResetError('3_Output_Dat/main.dol is not built from 1_Input/main.dol (its sections differ); '
                         'run the normal pipeline again first')
    result = dolfile.DolImage(vanilla)
    owned = set(roster_records(stock))
    kept = 0
    for record in directory_records(stock):
        if record in owned:
            continue
        words = out.read(record, dhs.RECORD_SIZE)
        if words != stock.read(record, dhs.RECORD_SIZE):
            result.write(record, words)
            kept += 1
    data = result.to_bytes()
    if data == current:
        return data, ['main.dol: no roster changes']
    return data, ['main.dol: back to 1_Input' + (', roster sections removed' if had_sections else '')
                  + f'; {kept} changed directory records kept (model patches, untangled routes)']


def _stock_directory_records(image: dolfile.DolImage) -> list[int]:
    """The records of the stock directories (own model directories' records are not among them)."""
    if not _has_directory(image):
        return []
    return sorted({record for chunk, _i, record, _w in dhs.iter_records(image) if chunk < dhs.hh._DIRS_COUNT})


def free_roster_copies(current: bytes, dat, keep=()) -> list[str]:
    """Zero the hammerspace copies the output's roster records and own model directories point at (none that
    another record uses, none in ``keep``)."""
    image = dolfile.DolImage(current)
    owned = roster_records(image)
    others = []
    for record in _stock_directory_records(image):
        if record not in owned:
            others += [(o, o + n) for o, n in _slots(image, record) if o >= dhs.BASE_SIZE and n]
    others += [(o, o + n) for o, n in keep]
    copies = {(o, n) for record in owned for o, n in _slots(image, record) if o >= dhs.BASE_SIZE and n}
    copies = sorted(copies | set(model_dirs.own_routes(image)))
    freed = 0
    for offset, length in copies:
        if any(lo < offset + length and offset < hi for lo, hi in others):
            continue
        end = min(offset + length, dat.size)
        if end > offset:
            dat.write(offset, bytes(end - offset))
            freed += end - offset
    if not copies:
        return []
    return [f'dt_na.dat: {len(copies)} roster copies in hammerspace zeroed (0x{freed:X} bytes)']


def reset(current: bytes, vanilla: bytes, dat, keep=()) -> tuple[bytes, list[str]]:
    """The reset ``main.dol`` bytes and log lines; DAT changes go to ``dat`` (a ``datfile.DatFile``, or None).
    ``keep``: DAT ranges left as they are (own model directories the next run re-uses)."""
    log = free_roster_copies(current, dat, keep) if dat is not None else []
    data, dol_log = reset_dol(current, vanilla)
    return data, dol_log + log
