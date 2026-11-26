"""The custom CPU level's two ``dt_na.dat`` files: the popup's caption and its button picture.

* **dir 121 file 4**, the popup message table (``01 01``, u16 count, u32 offsets in 2-byte units, UTF-16BE
  text; ``Roster/names.messages`` / ``build_table``). One copy per language, 292 messages each; 0xAE-0xB1 are
  the four stock level captions. Ours is appended as message 292 (``engine.MESSAGE``): the colour prefix of the
  copy's own All-Star caption, then the button name in the game's text encoding (printable ASCII as fullwidth
  forms U+FF01-FF5E, space 0x0020, Latin-1 letters as themselves; ``encode_name``).
  The name is the caption shown for the highlighted button, the same in every language.
* **dir 119 file 4**, the popup picture bank (``Icons/layout2d.Layout``). A button's picture is resource row
  choice + 0x22, so ours is row 0x68, one past the 0x68 stock rows: a copy of the Level 4 cell's row (0x37),
  so button 5 shows the Level 4 picture. No new texture page; each copy grows by one row.

Both files are rebuilt from their **stock** copy every time (offsets and lengths from ``1_Input/main.dol``'s
record, bytes from the output ``dt_na.dat`` there: nothing writes below the hammerspace base), written to DAT
hammerspace and routed by their directory record. A roster run's reset keeps records it does not own, so the
same code serves a first deploy, a redeploy and a roster run. Off puts the ``1_Input`` records back and zeroes
our copies.
"""

import struct

try:
    from ..Icons import layout2d
    from ..Roster import dat_hammerspace as dhs
    from ..Roster import names
    from . import engine
except ImportError:
    from Icons import layout2d
    import dat_hammerspace as dhs
    import names
    from CustomCpu import engine

MSG_DIR, MSG_FILE = 121, 4
ART_DIR, ART_FILE = 119, 4
ALL_STAR = 0xB1                              # the stock level 4 caption: ours takes its colour prefix
LEVEL4_ROW = 0x37
NAME_MAX = 25                                # the popup's longest stock caption is 26 characters
# Every printable ASCII character draws in the popup (Dolphin, 2026-11-25: the 22 that no stock text uses were
# shown as button names). Latin-1: the letters of the game's own text tables (all seven dir 121 tables, three
# languages) and the eight tested the same day (the second group). The other Latin-1 characters may not draw.
PROVEN_LATIN1 = '¡ªº¿ÁÉÍÚàáâçèéêíîïñóôùúû' 'ÄÖÜäöüßÑ'


class MenuFileError(RuntimeError):
    pass


def _encodable(ch: str) -> bool:
    return ch == ' ' or '!' <= ch <= '~' or 0xA1 <= ord(ch) <= 0xFF


def name_problem(text: str) -> str | None:
    """Why ``text`` cannot be the button name (None: it can)."""
    if not isinstance(text, str) or not text.strip():
        return 'the button name cannot be empty'
    if text != text.strip():
        return 'the button name cannot start or end with a space'
    if len(text) > NAME_MAX:
        return f'the button name has {len(text)} characters, at most {NAME_MAX}'
    bad = sorted({ch for ch in text if not _encodable(ch)})
    if bad:
        return ('the button name can hold letters, digits, punctuation and Western European accents only (not '
                + ' '.join(repr(ch) for ch in bad) + ')')
    return None


def unproven(text: str) -> str:
    """The characters of ``text`` not seen in the game yet: its font may have no glyph for them."""
    return ''.join(dict.fromkeys(ch for ch in text if ord(ch) >= 0xA1 and ch not in PROVEN_LATIN1))


def encode_name(text: str) -> bytes:
    """``text`` in the message tables' encoding, without the terminator."""
    problem = name_problem(text)
    if problem:
        raise MenuFileError(problem)
    return b''.join(struct.pack('>H', ord(ch) + 0xFEE0 if '!' <= ch <= '~' else ord(ch)) for ch in text)


def decode_text(raw: bytes) -> str:
    """A message's text (control codes dropped, other codes as ``<XXXX>``)."""
    out = []
    for (v,) in struct.iter_unpack('>H', raw):
        if 0xE000 <= v <= 0xFEFF:
            continue
        out.append(chr(v - 0xFEE0) if 0xFF01 <= v <= 0xFF5E else chr(v) if v == 0x20 or 0xA0 <= v <= 0xFF
                   else '\n' if v == 0x0D else f'<{v:04X}>')
    return ''.join(out)


def message_table(stock: bytes, button: str) -> bytes:
    """A stock popup message table with the button's caption appended (message ``engine.MESSAGE``)."""
    msgs = names.messages(stock)
    if len(msgs) != engine.MESSAGE:
        raise MenuFileError(f'the popup message table has {len(msgs)} messages, expected {engine.MESSAGE}')
    prefix = b''
    for (v,) in struct.iter_unpack('>H', msgs[ALL_STAR]):
        if not 0xE000 <= v <= 0xFEFF:
            break
        prefix += struct.pack('>H', v)
    return names.build_table(msgs + [prefix + encode_name(button)])


def picture_bank(stock: bytes) -> bytes:
    """A stock popup picture bank with row ``engine.ART_ROW``: a copy of the Level 4 cell's row."""
    lay = layout2d.Layout(stock)
    if len(lay.rows) != engine.ART_ROW:
        raise MenuFileError(f'the popup picture bank has 0x{len(lay.rows):X} rows, expected 0x{engine.ART_ROW:X}')
    page, _zero, *uv = struct.unpack('>HH4f', lay.rows[LEVEL4_ROW])
    if lay.add_row(page, tuple(uv)) != engine.ART_ROW:
        raise MenuFileError('the popup picture bank: the new row is not the button\'s row')
    return lay.to_bytes()


FILES = (('popup captions', MSG_DIR, MSG_FILE), ('popup pictures', ART_DIR, ART_FILE))


def _record(image, directory: int, file: int) -> int:
    return dhs.dir_pointers(image)[directory] + file * dhs.RECORD_SIZE


def _stock(dat, vanilla, directory: int, file: int, lang: str, input_dat=None) -> bytes:
    offset, length, _alloc = dhs.slot(dhs.read_record(vanilla, _record(vanilla, directory, file)), lang)
    if offset >= dhs.BASE_SIZE:
        raise MenuFileError(f'dir {directory} file {file}: 1_Input/main.dol routes it into hammerspace')
    return (input_dat or dat).read(offset, length)


def apply_dat(image, dat, vanilla, button: str, reserved=(), input_dat=None) -> list[str]:
    """Build both files for ``button`` and route them in ``image`` (``dat``: the output's ``DatFile``, written
    at its flush). ``vanilla``: ``1_Input/main.dol``'s image. ``reserved``: more ranges the allocator must not
    use (a roster run's kept routes). ``input_dat``: read the stock copies there instead. Returns log lines."""
    log = []
    for title, directory, file in FILES:
        record = _record(image, directory, file)
        words = dhs.read_record(image, record)
        moved = 0
        for lang in dhs.LANGS:
            stock = _stock(dat, vanilla, directory, file, lang, input_dat)
            try:
                new = message_table(stock, button) if directory == MSG_DIR else picture_bank(stock)
            except (names.NameConfigError, layout2d.Layout2dError) as exc:
                raise MenuFileError(f'{title} ({lang}): the stock copy in dt_na.dat cannot be read ({exc})') from exc
            offset, length, _alloc = dhs.slot(words, lang)
            if length == len(new) and dat.read(offset, length) == new:
                continue
            if offset >= dhs.BASE_SIZE:
                dat.write(offset, bytes(length))             # our old copy
            taken = [r for r in dhs.routed_ranges(image) if r != (offset, length)] + list(reserved)
            at = dhs.allocate(dat, len(new), taken)
            dat.write(at, new)
            dhs.set_slot(words, lang, at, len(new))
            dhs.write_record(image, record, words)
            moved += 1
        log.append(f'custom CPU level: {title} (dir {directory} file {file}): '
                   + (f'{moved} language cop{"y" if moved == 1 else "ies"} written to DAT hammerspace' if moved
                      else 'already in place'))
    return log


def remove_dat(image, dat, vanilla) -> list[str]:
    """Both files back to their ``1_Input`` records; our copies zeroed. Returns log lines."""
    log = []
    for title, directory, file in FILES:
        record = _record(image, directory, file)
        words = dhs.read_record(image, record)
        stock_words = dhs.read_record(vanilla, _record(vanilla, directory, file))
        if words == stock_words:
            continue
        for lang in dhs.LANGS:
            offset, length, _alloc = dhs.slot(words, lang)
            if offset >= dhs.BASE_SIZE and dhs.slot(stock_words, lang)[0] != offset and dat is not None:
                dat.write(offset, bytes(length))
        dhs.write_record(image, record, stock_words)
        log.append(f'custom CPU level: {title} (dir {directory} file {file}) back to the stock copies')
    return log


def in_place(image, vanilla) -> bool:
    """Whether both records are routed away from their stock copies (the files of a deployed custom level)."""
    return all(dhs.read_record(image, _record(image, d, f)) != dhs.read_record(vanilla, _record(vanilla, d, f))
               for _title, d, f in FILES)
