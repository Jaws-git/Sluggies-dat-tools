"""Square voices for stock squares: one species speaks with another's voice.

Config (``stock_voices`` in the roster preset)::

    "stock_voices": [{"id": "0x00", "voice": "0x09"}]

* ``id``: a stock square, named by its head (the species' base character,
  e.g. 0x00 Mario, 0x0D Toad, 0x01 Luigi);
* ``voice``: the base character whose voice the square takes.

Every voice path takes the family from selector byte 2 (the species) and
reads two tables by it, nothing else (``_docs/_docs_roster/RosterExpansion.md``,
"Stats, size and voice"): the voice bank ``GROUPS[species]`` (u32) and the
clip row ``CLIPS + species * 0x30`` (12 sound INFO IDs, the select-screen
voice included). Their five readers (``0x80233010``, ``0x80233364``,
``0x80386894``, ``0x804B28DC``, ``0x804B2904``) are all voice code, so copying
the voice species' word and row over the square's species changes the voice
of every member of that species (its wheel, spare rows and new IDs on it) on
the select screen and on the field alike, and nothing else: byte 2, the
wheels and the gameplay branches some species have stay as they are. Data
only, no hooks; the reset rebuilds both tables from ``1_Input``.

A new square's voice (``grid.squares[k].voice``, the ``grid`` step) works
through byte 2 instead. When the voice's own species has given its sounds
away, the grid step picks a species that still speaks with them
(``species_for_voice``), e.g. the other half of a swap.
"""

import struct

try:
    from . import ids, steps
except ImportError:
    import ids
    import steps

STOCK_KEY = 'stock_voices'
GROUPS = 0x80631F10                 # u32 voice bank per species (48 entries; -1: no voice, the Mii groups)
CLIPS = 0x80631FD0                  # 12 sound INFO IDs per species
CLIP_ROW = 0x30
SPECIES = 41                        # species 0x00-0x28 have a square and a voice; 0x29/0x2A are the Miis
HEAD_LIST = 0x80631878              # species -> base character (``grid.HEAD_LIST``; test-pinned)


class VoiceConfigError(ValueError):
    pass


def base_species(heads: bytes) -> dict[int, int]:
    """``{base character: species}`` from the stock head list (head ``s`` is species ``s``)."""
    return {heads[s]: s for s in range(SPECIES)}


def parse_stock_voices(config: dict, heads: bytes) -> dict[int, int]:
    """``{species: voice species}`` from ``stock_voices``; entries that give a square its own voice drop out."""
    entries = config.get(STOCK_KEY) or []
    if not isinstance(entries, list):
        raise VoiceConfigError(f'"{STOCK_KEY}" must be a list of {{"id", "voice"}} entries')
    species_of = base_species(heads)
    names = ', '.join(f'0x{c:02X}' for c in sorted(species_of))
    out: dict[int, int] = {}
    for n, entry in enumerate(entries):
        where = f'{STOCK_KEY}[{n}]'
        if not isinstance(entry, dict) or entry.get('id') is None or entry.get('voice') is None:
            raise VoiceConfigError(f'{where}: expected {{"id": "0xNN", "voice": "0xNN"}}')
        cid = ids._number(entry['id'], f'{where}.id')
        voice = ids._number(entry['voice'], f'{where}.voice')
        for what, value in (('id', cid), ('voice', voice)):
            if value not in species_of:
                raise VoiceConfigError(f'{where}.{what}: 0x{value:02X} is not the base character of a stock square '
                                       f'(one of {names})')
        species = species_of[cid]
        if species in out:
            raise VoiceConfigError(f'{where}: square 0x{cid:02X} is listed twice')
        if species_of[voice] != species:
            out[species] = species_of[voice]
    return out


def effective(remap: dict[int, int], species: int) -> int:
    """The species whose stock sounds ``species`` speaks with."""
    return remap.get(species, species)


def species_for_voice(voice_species: int, remap: dict[int, int]) -> int | None:
    """A species that speaks with ``voice_species``' stock sounds: that species itself unless it gave them away,
    else the lowest species that took them; None when none speaks with them."""
    if effective(remap, voice_species) == voice_species:
        return voice_species
    return next((s for s in range(SPECIES) if effective(remap, s) == voice_species), None)


def write_remap(image, remap: dict[int, int]) -> None:
    """Copy each voice species' stock group word and clip row over its square's species (all read first)."""
    groups = {s: image.read(GROUPS + 4 * s, 4) for s in set(remap.values())}
    clips = {s: image.read(CLIPS + CLIP_ROW * s, CLIP_ROW) for s in set(remap.values())}
    for species, voice in sorted(remap.items()):
        image.write(GROUPS + 4 * species, groups[voice])
        image.write(CLIPS + CLIP_ROW * species, clips[voice])


def group_of(image, species: int) -> int:
    return struct.unpack('>i', image.read(GROUPS + 4 * species, 4))[0]


@steps.register('voices')
def apply(ctx: steps.RosterContext) -> list[str]:
    if STOCK_KEY not in ctx.config:
        return [f'no "{STOCK_KEY}" in the roster config: stock squares keep their voices']
    heads = ctx.dol.read(HEAD_LIST, SPECIES)
    remap = parse_stock_voices(ctx.config, heads)
    ctx.state['voice_remap'] = remap
    write_remap(ctx.dol, remap)
    if not remap:
        return ['every stock square keeps its own voice']
    return [f'square 0x{heads[s]:02X} (species 0x{s:02X}) speaks with 0x{heads[v]:02X}\'s voice '
            f'(group {group_of(ctx.dol, s)}, clips from species 0x{v:02X})' for s, v in sorted(remap.items())]
