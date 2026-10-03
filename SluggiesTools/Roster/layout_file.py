"""Select layout file in DAT hammerspace (plan Phase 2).

dt_na dir 119 file 19 (= dir 0 file 1591) is the shared 2D layout bank of the
character-select screens: the exhibition grid (element ``0xBA``), the colour
wheel popup (``0xB3``), the swatch colours (``0xAD``/``0xAE``), the team bars
and the name labels. Phases 4d, 4e and 6 grow it, and the stock copies have
no room (only 0x10 bytes separate EN from SP), so this step moves it.

One DOL record (``layout2d.CSS_LAYOUT_DOL_RECORD``), shared by all 120
directory windows that list the file, holds three language slots. EN, SP and
FR point at three separate copies that differ in their text textures, and the
game loads only the console language's one. The step copies each into DAT
hammerspace (``dat_hammerspace.allocate``) and repoints that language's slot.
The copies are identical to their sources, so on its own the step must play
exactly like vanilla. The stock copies stay where they are.

Later steps edit the files through ``get(ctx).update(transform)``, which
rewrites each copy in place, or moves it when it grew.
"""

from typing import Callable

try:
    from ..Icons import layout2d
    from . import dat_hammerspace as dhs
    from . import steps
except ImportError:
    from Icons import layout2d
    import dat_hammerspace as dhs
    import steps

RECORD = dhs.dol_base_address(layout2d.CSS_LAYOUT_DOL_RECORD)
STATE_KEY = 'layout_file'


class LayoutFileError(RuntimeError):
    pass


class LayoutFiles:
    """The moved copies of dir 119 file 19: ``{lang: (offset, length)}``, plus the stock lengths."""

    def __init__(self, ctx: steps.RosterContext, stock_lengths: dict[str, int]):
        self.ctx = ctx
        self.stock_lengths = stock_lengths

    @property
    def copies(self) -> dict[str, tuple[int, int]]:
        words = dhs.read_record(self.ctx.dol, RECORD)
        return {lang: dhs.slot(words, lang)[:2] for lang in dhs.LANGS}

    def groups(self) -> dict[tuple[int, int], list[str]]:
        """Languages per copy (languages that share a copy are edited once)."""
        out: dict[tuple[int, int], list[str]] = {}
        for lang, where in self.copies.items():
            out.setdefault(where, []).append(lang)
        return out

    def read(self, lang: str) -> bytes:
        offset, length = self.copies[lang]
        return self.ctx.dat.read(offset, length)

    def update(self, transform: Callable[[str, bytes], bytes]) -> list[str]:
        """Apply ``transform(lang, data) -> data`` to each copy; returns log lines.

        A copy that keeps its size or shrinks is rewritten in place (a freed
        tail is zeroed); a grown copy moves to a new hammerspace range."""
        log = []
        for (offset, length), langs in self.groups().items():
            old = self.ctx.dat.read(offset, length)
            new = bytes(transform(langs[0], old))
            if new == old:
                continue
            layout2d.parse_bank(new)
            at = offset
            if len(new) <= length:
                self.ctx.dat.write(offset, new + bytes(length - len(new)))
            else:
                self.ctx.dat.write(offset, bytes(length))
                reserved = [r for r in dhs.routed_ranges(self.ctx.dol) if r != (offset, length)]
                at = dhs.allocate(self.ctx.dat, len(new), reserved)
                self.ctx.dat.write(at, new)
            words = dhs.read_record(self.ctx.dol, RECORD)
            for lang in langs:
                dhs.set_slot(words, lang, at, len(new))
            dhs.write_record(self.ctx.dol, RECORD, words)
            log.append(f'{"/".join(langs)}: 0x{length:X} -> 0x{len(new):X} bytes'
                       + (f', moved 0x{offset:08X} -> 0x{at:08X}' if at != offset else ' (in place)')
                       + self.heap_note(langs[0], len(new)))
        return log

    def heap_note(self, lang: str, length: int) -> str:
        growth = length - self.stock_lengths[lang]
        return f'; game heap {growth:+,} bytes against stock' if growth else ''


def get(ctx: steps.RosterContext) -> LayoutFiles:
    files = ctx.state.get(STATE_KEY)
    if files is None:
        raise LayoutFileError('the select layout file is not in DAT hammerspace (the layout_file step runs first)')
    return files


@steps.register('layout_file')
def apply(ctx: steps.RosterContext) -> list[str]:
    if ctx.dat is None:
        return ['dt_na.dat is missing in the output folder: the select layout stays where it is']
    words = dhs.read_record(ctx.dol, RECORD)
    stock_lengths = {lang: dhs.slot(words, lang)[1] for lang in dhs.LANGS}
    placed: dict[int, tuple[int, int]] = {}
    lines = []
    for lang in dhs.LANGS:
        offset, length, _alloc = dhs.slot(words, lang)
        if offset >= dhs.BASE_SIZE:
            raise LayoutFileError(f'the {lang} select layout already lives in DAT hammerspace (0x{offset:08X}); '
                                  'another tool moved it, so the roster expansion cannot own it')
        if offset not in placed:
            data = ctx.dat.read(offset, length)
            try:
                if layout2d.Layout(data).to_bytes() != data:
                    raise LayoutFileError(f'the {lang} select layout does not round-trip through the layout writer')
            except layout2d.Layout2dError as exc:
                raise LayoutFileError(f'the {lang} select layout at 0x{offset:08X} is not a layout bank: {exc}') \
                    from exc
            at = dhs.allocate(ctx.dat, length, dhs.routed_ranges(ctx.dol))
            ctx.dat.write(at, data)
            placed[offset] = (at, length)
            lines.append(f'{lang}: 0x{offset:08X} -> 0x{at:08X} (0x{length:X} bytes)')
        else:
            lines.append(f'{lang}: shares the copy at 0x{placed[offset][0]:08X}')
        dhs.set_slot(words, lang, *placed[offset])
        dhs.write_record(ctx.dol, RECORD, words)
    ctx.state[STATE_KEY] = LayoutFiles(ctx, stock_lengths)
    total = sum(n for _a, n in placed.values())
    lines.append(f'select layout (dir 119 file 19, record 0x{RECORD:08X}): {len(placed)} copies, '
                 f'0x{total:X} bytes of DAT hammerspace; identity copies, game heap unchanged'
                 + ('; dt_na.dat grows to 0x{:X} bytes'.format(ctx.dat.size) if ctx.dat.grown else ''))
    return lines
