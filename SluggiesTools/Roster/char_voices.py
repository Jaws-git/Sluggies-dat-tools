"""Character voices: one character ID speaks with another stock character's voice, or with custom clips, whatever
its species says.

Config (``character_voices`` in the roster preset)::

    "character_voices": [{"id": "0x66", "voice": "0x09"},
                         {"id": "0x67", "voice": "0x09", "clips": {"v1": "3f2a....rwav", "v13": "name.wav"},
                          "pack": "Rosalina"}]

* ``id``: a selectable character (a stock ID, a spare row on a wheel, a new ID); not a Mii (no voice bank);
* ``voice``: the base character whose stock voice the ID takes (any stock square's head); optional with ``clips``
  (then the moments without a clip keep the ID's species voice);
* ``clips``: custom clips per voice moment (``Roster/clip_files``: a file per moment, encoded RWAV or WAV),
  appended to the ID's family file (the name call to the select file) and pointed at by its table;
* ``pack``: a label, the voice pack the clips came from (``Sound/sound_packs``), shown by the GUI.

A character voice is an override: without an entry the ID follows its species and square voice exactly as before
(``voices.py``, the grid step). With one, the ID's clips come from its own 12-slot table instead of its species'
row, through hooks ported from the external tool's ``voice_hooks.py`` (``_docs/Custom Character Sounds Pipeline -
Rosalina Luma Larry.txt``): the exact ID travels as ``ID * 8`` (the selector-row offset the callers already have
in r0) to the voice wrapper ``0x804B2898``, which remembers the ID's table; the clip lookup ``0x804B2910`` reads
the table instead of the species row. Sites (inventory group ``char_voices``):

  A. wrapper entry ``0x804B2898``: ACTIVE = 0; the token is OWNER (consumed) when the caller is the family search
     ``0x80387028`` (return address ``0x8038710C``), r0 for the three direct callers (``0x80386FA8``,
     ``0x80388698``, the select screen ``0x804A5630``), none otherwise (stock voice). A token with a table sets
     ACTIVE. r10-r12 and CR are saved and restored; LR and r0 are only read.
  B. owner ``0x80142078`` (``bl`` to the family search): OWNER = r0 around the call, 0 after it.
  C. resolver ``0x8036C674`` (``bl`` to the scripted actor resolver: mound entrance, strikeout celebration):
     RESOLVING = 1 and OWNER = 0 around it; the five ``rlwinm r0,r0,3,0,28`` inside it (ID -> token) publish
     OWNER while RESOLVING. OWNER is kept for the voice that follows.
  D. lookup ``0x804B2910`` (``lwzx r5,r5,r0``, r5 = slot * 4): with ACTIVE, r5 = ACTIVE[slot] and ACTIVE = 0.

Every other caller of the wrapper keeps the species voice (the external tool's coverage; untested callers of the
family search are listed in ``_docs/_docs_roster/RosterExpansion.md``).

**The table and the sound archive.** A gameplay clip plays only while the bank that holds it is loaded, and the
game loads the bank of the species on the field (the group word ``0x80631F10[species]``, as the run leaves it:
stock square swaps included). So the 11 gameplay clips of the voice are copied, byte for byte, into the ID's
family file: the archived file that holds its species' current first clip (``MY2.brsar``, ``Sound/brsar_write``).
Each (family file, voice, slot) is copied once, however many IDs share it; the new sounds are named
``zz<voice clip>_f<file>`` and take the family's same-slot entry as template (player, priority, 3D parameters)
with the voice clip's volume. The name call (slot 10, ``v13``) lives in the select file, which holds every
species' call and is always loaded on the select screen: the table takes the voice's stock ID there, no copy.
An ID whose species already speaks with the voice (after the swaps) gets its row as its table and no copies.

The step runs after ``grid`` (which sets the square-only new IDs' species). It reads the vanilla archive from
``ctx.sound`` (``1_Input/sound_NA/MY2.brsar``, read by the runner) and puts the grown archive into
``ctx.state['sound_out']``, which the runner writes to ``3_Output_Dat/sound_NA/MY2.brsar``; without an input
archive, voices that need copies are refused. The stock rows come from the ``voices`` step
(``ctx.state['stock_clips']``: a voice is its base character's *stock* sounds, whatever square swaps did).
Tables and the hook state live in the DOL data section, the stubs in the text section; the manifest carries the
entries for the read-back (``state.py``, ``derive.py``). The output archive is rebuilt from the input on every
run, like the DOL, so an unchanged config gives identical bytes.

**Memory.** A family file loads whole whenever its species plays, so every copied voice (about 100-250 KB of wave
data) adds to the match's sound memory. ``family_files`` reports the sizes; ``FAMILY_FILE_TESTED_MAX`` is the
largest family file known to work (the external tool's Bowser Jr file with every Koopaling, about 1.16 MB of wave
data); the Maintenance "Sound memory" check warns above it.
"""

import struct
from dataclasses import dataclass, field

try:
    from ..Dol import dolfile, inventory
    from ..Dol.ppc import Asm, one
    from ..Sound import brsar, brsar_write, sound_packs
    from . import clip_files, dol_hammerspace, ids, steps, voices, wheels
except ImportError:
    from Dol import dolfile, inventory
    from Dol.ppc import Asm, one
    from Sound import brsar, brsar_write, sound_packs
    import clip_files
    import dol_hammerspace
    import ids
    import steps
    import voices
    import wheels

KEY = 'character_voices'
GROUP = 'char_voices'                                     # the inventory group of the hook sites
WRAPPER, LOOKUP = 0x804B2898, 0x804B2910
OWNER_CALL, SEARCH = 0x80142078, 0x80387028               # bl FUN_80387028
RESOLVER_CALL, RESOLVER = 0x8036C674, 0x8036B4E0          # bl FUN_8036b4e0
CAPTURES = (0x8036B5C0, 0x8036B600, 0x8036B674, 0x8036B6AC, 0x8036B6E4)
CAPTURE_WORD = 0x54001838                                 # rlwinm r0,r0,3,0,28 (slwi r0,r0,3)
SEARCH_RETURN = SEARCH + 0xE4                             # 0x8038710C: the wrapper call inside the family search
DIRECT_RETURNS = (0x80386FAC, 0x8038869C, 0x804A5634)     # the three direct callers' return addresses
SLOTS = 12
NAME_CALL_SLOT = 10                                       # v13, the select-screen name call
GAMEPLAY_SLOTS = tuple(k for k in range(SLOTS) if k != NAME_CALL_SLOT)
OWNER, RESOLVING, ACTIVE = 0, 4, 8                        # the state words
STATE_SIZE = 12
STATE_MAGIC = b'SLUGGIES VOICES\x01'                      # heads the tables in the data section (a marker only)
SOUND_PREFIX = 'zz'                                       # copied clips: zz<voice clip name>_f<family file>
FAMILY_FILE_TESTED_MAX = 1_160_000                        # wave bytes of a family file known to work (see above)


class CharVoiceConfigError(ValueError):
    pass


@dataclass
class Choice:
    """One ``character_voices`` entry: the stock voice (None: the species' own) and the custom clips per slot."""
    voice: int | None = None
    clips: dict = field(default_factory=dict)        # {slot: clip file}
    pack: str | None = None

    def __eq__(self, other):
        if isinstance(other, int):                   # the old ``{id: voice}`` shape (tests, callers)
            return self.voice == other and not self.clips
        return isinstance(other, Choice) and (self.voice, self.clips, self.pack) == (other.voice, other.clips, other.pack)

    __hash__ = None


def parse(config: dict, heads: bytes) -> dict[int, Choice]:
    """``{character ID: Choice}`` from ``character_voices``."""
    entries = config.get(KEY) or []
    if not isinstance(entries, list):
        raise CharVoiceConfigError(f'"{KEY}" must be a list of {{"id", "voice"}} entries')
    species_of = voices.base_species(heads)
    names = ', '.join(f'0x{c:02X}' for c in sorted(species_of))
    out: dict[int, Choice] = {}
    for n, entry in enumerate(entries):
        where = f'{KEY}[{n}]'
        if not isinstance(entry, dict) or entry.get('id') is None or (entry.get('voice') is None
                                                                       and entry.get('clips') is None):
            raise CharVoiceConfigError(f'{where}: expected {{"id": "0xNN", "voice": "0xNN"}} (and/or "clips")')
        cid = ids._number(entry['id'], f'{where}.id')
        if not 0 <= cid < ids.ROWS:
            raise CharVoiceConfigError(f'{where}.id: 0x{cid:02X} is not a character ID (0x00-0x{ids.ROWS - 1:02X})')
        voice = None
        if entry.get('voice') is not None:
            voice = ids._number(entry['voice'], f'{where}.voice')
            if voice not in species_of:
                raise CharVoiceConfigError(f'{where}.voice: 0x{voice:02X} is not the base character of a stock square '
                                           f'(one of {names})')
        if cid in out:
            raise CharVoiceConfigError(f'{where}: 0x{cid:02X} is listed twice')
        clips = {}
        if entry.get('clips') is not None:
            try:
                clips = clip_files.parse_clips(entry['clips'], where)
            except clip_files.ClipConfigError as exc:
                raise CharVoiceConfigError(str(exc)) from exc
        pack = entry.get('pack')
        if pack is not None and not isinstance(pack, str):
            raise CharVoiceConfigError(f'{where}.pack: expected the pack\'s name')
        out[cid] = Choice(voice, clips, pack or None)
    return out


def row_ids(row: bytes) -> list[int]:
    return list(struct.unpack('>12I', row))


def table_for(species_row: bytes, voice_stock_row: bytes) -> bytes:
    """Phase 1's table: the ID's species row with the name call (slot 10) replaced by the voice's."""
    if len(species_row) != voices.CLIP_ROW or len(voice_stock_row) != voices.CLIP_ROW:
        raise ValueError('a clip row is 0x30 bytes')
    at = 4 * NAME_CALL_SLOT
    return species_row[:at] + voice_stock_row[at:at + 4] + species_row[at + 4:]


# ------------------------------------------------------------------------------------------------ archive

def copy_name(source_name: str, file_id: int) -> str:
    return f'{SOUND_PREFIX}{source_name}_f{file_id}'


@dataclass
class VoicePlan:
    """What the archive and the tables need for a set of character voices."""
    tables: dict                    # {ID: [12 INFO IDs]} (new sounds as ``('copy', key)`` / ``('clip', key)``
    #                                 until ``resolve``)
    copies: list                    # [(family file, voice species, slot)] in copy order, each once
    rows: dict                      # {ID: (species, family file, voice species or None)}
    templates: dict                 # {copy key: INFO ID of the family's own clip in that slot (the entry template)}
    clips: list = field(default_factory=list)       # [(file, slot, RWAV bytes)] custom clips, each once
    clip_templates: dict = field(default_factory=dict)   # {clip key: template INFO ID}
    custom: dict = field(default_factory=dict)      # {ID: {slot: clip key, or the INFO ID of a sound that plays
    #                                                 the clip already}} the custom moments per ID

    def needs_archive(self) -> bool:
        return bool(self.copies or self.clips)


def _check_wave(sounds, sid: int, what: str) -> None:
    if sounds is None or not 0 <= sid < len(sounds) or sounds[sid].type != brsar.WAVE:
        raise CharVoiceConfigError(f'{KEY}: {what} (sound {sid}) is not a wave sound of this archive: is '
                                   '1_Input/sound_NA/MY2.brsar the game\'s?')


def plan(image, chosen: dict, stock: dict[int, bytes], species_of: dict[int, int], selector: int,
         rows: int, archive, loader: clip_files.ClipLoader | None = None) -> VoicePlan:
    """Per ID: its current species row (after the swaps), the family file that row's first clip lives in (needs
    ``archive``, a ``Brsar``; None: unknown, only same-voice IDs can be planned), the copies the voice needs and
    the custom clips (``loader`` reads them). ``chosen``: ``{ID: Choice}`` (an int stands for a stock voice)."""
    vp = VoicePlan({}, [], {}, {})
    sounds = archive.sounds() if archive is not None else None
    clip_index: dict[tuple, int] = {}
    for cid, choice in sorted(chosen.items()):
        if isinstance(choice, int):
            choice = Choice(choice)
        if cid >= rows:
            raise CharVoiceConfigError(f'{KEY}: 0x{cid:02X} is not a character of this roster')
        row = image.read(selector + 8 * cid, 8)
        species = row[2]
        if species >= voices.SPECIES or voices.group_of(image, species) < 0:
            raise CharVoiceConfigError(f'{KEY}: 0x{cid:02X} has no voice bank (a Mii): it cannot take a voice')
        if not row[6]:
            raise CharVoiceConfigError(f'{KEY}: 0x{cid:02X} is not selectable (new IDs need an "ids" entry, '
                                       'spare rows a "wheels" entry)')
        current = row_ids(voices.clip_row(image, species))
        vs = species_of[choice.voice] if choice.voice is not None else None
        source = row_ids(stock[vs]) if vs is not None else current
        same = source == current
        if same and not choice.clips:                           # the species speaks with that voice already
            vp.tables[cid] = current
            vp.rows[cid] = (species, None, vs)
            continue
        if sounds is None:
            what = (f'{choice.voice:#04x}\'s clips copied into its voice bank' if not same
                    else 'its custom clips added to its voice bank')
            raise CharVoiceConfigError(f'{KEY}: 0x{cid:02X} needs {what}, which needs the game\'s sound archive: '
                                       'copy files/sound_NA/MY2.brsar into 1_Input/sound_NA first')
        _check_wave(sounds, current[0], f'species 0x{species:02X}\'s first clip')
        family = sounds[current[0]].file_id
        table = []
        for k in range(SLOTS):
            if k in choice.clips:
                if loader is None:
                    raise CharVoiceConfigError(f'{KEY}: 0x{cid:02X} has custom clips, but none can be read here')
                _check_wave(sounds, current[k], f'species 0x{species:02X}\'s clip {k}')
                target_file = sounds[current[k]].file_id       # the family file, or the select file for the name call
                try:
                    rwav = loader.load(choice.clips[k], f'{KEY} 0x{cid:02X}.clips.{sound_packs.MOMENT_KEYS[k]}')
                except clip_files.ClipConfigError as exc:
                    raise CharVoiceConfigError(str(exc)) from exc
                if brsar_write.sound_rwav(archive, sounds[current[k]]) == rwav:
                    table.append(current[k])                   # the species plays that very clip already
                    vp.custom.setdefault(cid, {})[k] = current[k]
                    continue
                key = (target_file, k, rwav)
                if key not in clip_index:
                    clip_index[key] = len(vp.clips)
                    vp.clips.append(key)
                    vp.clip_templates[key] = current[k]
                table.append(('clip', key))
                vp.custom.setdefault(cid, {})[k] = key
            elif same:
                table.append(current[k])
            elif k == NAME_CALL_SLOT:
                table.append(source[k])
            else:
                key = (family, vs, k)
                if key not in vp.copies:
                    vp.copies.append(key)
                    vp.templates[key] = current[k]
                table.append(('copy', key))
        vp.tables[cid] = table
        vp.rows[cid] = (species, family, vs)
    vp.copies.sort()
    return vp


def new_sounds(archive, vp: VoicePlan, stock: dict[int, bytes]) -> list:
    """The ``NewSound`` list of a plan: its copies (copy order), then its custom clips (plan order)."""
    sounds = archive.sounds()
    out = []
    for family, vs, k in vp.copies:
        source_id = row_ids(stock[vs])[k]
        if not 0 <= source_id < len(sounds) or sounds[source_id].type != brsar.WAVE:
            raise CharVoiceConfigError(f'{KEY}: voice species 0x{vs:02X} clip {k} (sound {source_id}) is not a wave '
                                       'sound of this archive')
        source = sounds[source_id]
        template = vp.templates[(family, vs, k)]              # the family's own clip in that slot
        if not 0 <= template < len(sounds) or sounds[template].type != brsar.WAVE:
            raise CharVoiceConfigError(f'{KEY}: the family\'s clip {k} (sound {template}) is not a wave sound of '
                                       'this archive')
        out.append(brsar_write.NewSound(copy_name(source.name, family), family, brsar_write.sound_rwav(archive, source),
                                        template, volume=source.volume))
    for target_file, k, rwav in vp.clips:
        out.append(brsar_write.NewSound(clip_files.sound_name(rwav, k, target_file), target_file, rwav,
                                        vp.clip_templates[(target_file, k, rwav)]))
    return out


def resolve(vp: VoicePlan, ids_of: dict) -> dict[int, bytes]:
    """The tables as bytes, the new sounds replaced by their INFO IDs (``ids_of``: ``{copy or clip key: INFO ID}``)."""
    out = {}
    for cid, table in vp.tables.items():
        words = [ids_of[entry[1]] if isinstance(entry, tuple) else entry for entry in table]
        out[cid] = struct.pack('>12I', *words)
    return out


def family_files(image, archive) -> dict[int, tuple[int, int]]:
    """``{species: (family file, its wave bytes)}`` for every voiced species, as the DOL's clip rows and the
    archive hold them now (the file of each species' first clip)."""
    sounds, files, groups = archive.sounds(), archive.files(), archive.groups()
    out = {}
    for s in range(voices.SPECIES):
        first = row_ids(voices.clip_row(image, s))[0]
        if not 0 <= first < len(sounds) or sounds[first].type != brsar.WAVE:
            continue
        fid = sounds[first].file_id
        parts = archive.file_bytes(fid, groups, files)
        out[s] = (fid, len(parts[1]) if parts else 0)
    return out


# ------------------------------------------------------------------------------------------------ hooks

def _mfcr(a: Asm, rt: str) -> Asm:
    return a.word(0x7C000026 | (int(rt[1:]) << 21))


def _mtcrf_all(a: Asm, rs: str) -> Asm:
    return a.word(0x7C000120 | (int(rs[1:]) << 21) | (0xFF << 12))


def _stub(hs: dol_hammerspace.DolHammerspace, build) -> int:
    hs.code.put(b'', 4)
    a = Asm(hs.code.here)
    build(a)
    return hs.code.put(a.assemble(), 4)


def _patch(image: dolfile.DolImage, address: int, new: int) -> None:
    image.patch_word(address, inventory.site(GROUP, address).stock, new)


def install(image: dolfile.DolImage, hs: dol_hammerspace.DolHammerspace, tables: dict[int, bytes]) -> dict:
    """Put the tables and the hook state into the data section, the four stubs into the text section, and
    branch the nine sites to them. ``tables``: ``{ID: 12-slot row bytes}``, at least one. Returns where things
    went (``state``, ``pointers``, ``tables``: ``{ID: address}``)."""
    if not tables:
        raise ValueError('no tables')
    if any(len(t) != SLOTS * 4 for t in tables.values()):
        raise ValueError('a table is 12 words')
    bad = inventory.stock_mismatches(image, inventory.group(GROUP))
    if bad:
        raise dolfile.DolError('character voice hook sites are not stock: ' + '; '.join(bad))
    max_id = max(tables)
    hs.data.put(STATE_MAGIC, 4)
    state = hs.data.put(bytes(STATE_SIZE), 4)
    where = {}
    pointers = [0] * (max_id + 1)
    for cid, row in sorted(tables.items()):
        pointers[cid] = where[cid] = hs.data.put(bytes(row), 4)
    ptr_table = hs.data.put(struct.pack(f'>{len(pointers)}I', *pointers), 4)

    # A. wrapper entry: pick the table of the exact owner, then the displaced stwu
    def wrapper(a: Asm):
        a.stwu('r1', -0x20, 'r1').stw('r10', 8, 'r1').stw('r11', 0xC, 'r1').stw('r12', 0x10, 'r1')
        _mfcr(a, 'r12')
        a.stw('r12', 0x14, 'r1')
        a.load_addr('r12', state).li('r11', 0).stw('r11', ACTIVE, 'r12')
        a.mflr('r11')
        a.load_addr('r10', SEARCH_RETURN).cmpw('r11', 'r10').beq('family')
        for ret in DIRECT_RETURNS:
            a.load_addr('r10', ret).cmpw('r11', 'r10').beq('direct')
        a.b('done')
        a.label('family')
        a.lwz('r10', OWNER, 'r12').li('r11', 0).stw('r11', OWNER, 'r12').b('token')
        a.label('direct')
        a.mr('r10', 'r0')
        a.label('token')                                     # r10 = ID * 8
        a.andi_('r11', 'r10', 7).bne('done')
        a.rlwinm('r11', 'r10', 29, 3, 31).cmplwi('r11', max_id).bgt('done')
        a.slwi('r11', 'r11', 2).load_addr('r10', ptr_table).lwzx('r11', 'r10', 'r11')
        a.stw('r11', ACTIVE, 'r12')
        a.label('done')
        a.lwz('r12', 0x14, 'r1')
        _mtcrf_all(a, 'r12')
        a.lwz('r12', 0x10, 'r1').lwz('r11', 0xC, 'r1').lwz('r10', 8, 'r1').addi('r1', 'r1', 0x20)
        a.stwu('r1', -0x40, 'r1').b(WRAPPER + 4)
    at = _stub(hs, wrapper)
    _patch(image, WRAPPER, one(WRAPPER, lambda a: a.b(at)))

    # B. owner around the family search
    def owner(a: Asm):
        a.stwu('r1', -0x10, 'r1').mflr('r12').stw('r12', 8, 'r1')
        a.load_addr('r12', state).stw('r0', OWNER, 'r12')
        a.bl(SEARCH)
        a.load_addr('r12', state).li('r11', 0).stw('r11', OWNER, 'r12')
        a.lwz('r12', 8, 'r1').mtlr('r12').addi('r1', 'r1', 0x10).blr()
    at = _stub(hs, owner)
    _patch(image, OWNER_CALL, one(OWNER_CALL, lambda a: a.bl(at)))

    # C. the scripted resolver and its five ID -> token conversions
    def resolver(a: Asm):
        a.stwu('r1', -0x10, 'r1').mflr('r12').stw('r12', 8, 'r1')
        a.load_addr('r12', state).li('r11', 1).stw('r11', RESOLVING, 'r12').li('r11', 0).stw('r11', OWNER, 'r12')
        a.bl(RESOLVER)
        a.load_addr('r12', state).li('r11', 0).stw('r11', RESOLVING, 'r12')
        a.lwz('r12', 8, 'r1').mtlr('r12').addi('r1', 'r1', 0x10).blr()
    at = _stub(hs, resolver)
    _patch(image, RESOLVER_CALL, one(RESOLVER_CALL, lambda a: a.bl(at)))

    def capture(a: Asm):
        a.word(CAPTURE_WORD)                                 # the displaced slwi r0,r0,3
        a.load_addr('r12', state).lwz('r11', RESOLVING, 'r12').cmpwi('r11', 0).beq('out')
        a.stw('r0', OWNER, 'r12')
        a.label('out')
        a.blr()
    at = _stub(hs, capture)
    for site in CAPTURES:
        _patch(image, site, one(site, lambda a, at=at: a.bl(at)))

    # D. the clip lookup
    def lookup(a: Asm):
        a.load_addr('r12', state).lwz('r11', ACTIVE, 'r12').cmpwi('r11', 0).beq('stock')
        a.lwzx('r5', 'r5', 'r11').li('r11', 0).stw('r11', ACTIVE, 'r12').b(LOOKUP + 4)
        a.label('stock')
        a.word(inventory.site(GROUP, LOOKUP).stock).b(LOOKUP + 4)
    at = _stub(hs, lookup)
    _patch(image, LOOKUP, one(LOOKUP, lambda a: a.b(at)))
    return {'state': state, 'pointers': ptr_table, 'tables': where}


def installed(image: dolfile.DolImage) -> bool:
    """Whether the hooks are in (every site branches away from its stock word)."""
    return not any(image.u32(s.address) == s.stock for s in inventory.group(GROUP))


# ------------------------------------------------------------------------------------------------ step

def state_entry(choice: Choice, custom: dict | None, ids_of: dict) -> dict:
    """What the manifest keeps of one ID's voice: ``{'voice', 'clips': {slot: INFO ID}, 'pack'}``."""
    return {'voice': choice.voice,
            'clips': {k: ids_of[key] if isinstance(key, tuple) else key for k, key in sorted((custom or {}).items())},
            'pack': choice.pack}


@steps.register('char_voices')
def apply(ctx: steps.RosterContext) -> list[str]:
    if KEY not in ctx.config:
        return [f'no "{KEY}" in the roster config: every character speaks with its species\' voice']
    heads = ctx.dol.read(voices.HEAD_LIST, voices.SPECIES)
    chosen = parse(ctx.config, heads)
    if not chosen:
        ctx.state['char_voices'] = {}
        return ['no character voices set']
    species_of = voices.base_species(heads)
    stock = ctx.state.get('stock_clips') or voices.stock_clips(ctx.dol)
    selector, rows = wheels.table_location(ctx, 'selector')
    sound = voices.current_sound(ctx)
    archive = brsar.Brsar(sound) if sound is not None else None
    loader = clip_files.ClipLoader(ctx.clip_dir, sound)
    vp = plan(ctx.dol, chosen, stock, species_of, selector, rows, archive, loader)
    log = []
    ids_of = {}
    if vp.needs_archive():
        touched = sorted({c[0] for c in vp.copies} | {c[0] for c in vp.clips})
        before = {fid: len(archive.file_bytes(fid)[1]) for fid in touched}
        added = new_sounds(archive, vp, stock)
        out, new_ids = brsar_write.add_sounds(sound, added)
        ids_of = dict(zip(vp.copies + vp.clips, new_ids))
        ctx.state['sound_out'] = out
        grown = brsar.Brsar(out)
        for fid, old in before.items():
            new = len(grown.file_bytes(fid)[1])
            species = sorted(s for s, (_sp, fam, _vs) in vp.rows.items() if fam == fid)
            warn = f' -- above the largest family file known to work ({FAMILY_FILE_TESTED_MAX:,})' \
                if new > FAMILY_FILE_TESTED_MAX else ''
            copies = len([c for c in vp.copies if c[0] == fid])
            customs = len([c for c in vp.clips if c[0] == fid])
            what = ', '.join(([f'{copies} clips copied'] if copies else []) + ([f'{customs} custom clips'] if customs else []))
            log.append(f'file {fid}: wave data {old:,} -> {new:,} bytes ({what} for '
                       + ', '.join(f'0x{c:02X}' for c in species) + f'){warn}')
        log += loader.notes
        log.append(f'sound archive: {len(sound):,} -> {len(out):,} bytes, {len(added)} sounds added '
                   f'(INFO IDs {new_ids[0]}..{new_ids[-1]})')
    tables = resolve(vp, ids_of)
    ctx.state['char_voices'] = {cid: state_entry(choice if isinstance(choice, Choice) else Choice(choice),
                                                 vp.custom.get(cid), ids_of) for cid, choice in chosen.items()}
    for cid, choice in sorted(chosen.items()):
        species, family, vs = vp.rows[cid]
        words = row_ids(tables[cid])
        own = (f'0x{choice.voice:02X}\'s voice' if choice.voice is not None else 'its species\' voice')
        custom = vp.custom.get(cid) or {}
        extra = (f' with {len(custom)} custom clip(s) ({", ".join(sound_packs.MOMENT_KEYS[k] for k in sorted(custom))})'
                 if custom else '')
        if family is None:
            log.append(f'0x{cid:02X} (species 0x{species:02X}) speaks with {own} already: its species row is its table')
        else:
            log.append(f'0x{cid:02X} (species 0x{species:02X}, family file {family}) speaks with {own}{extra}: '
                       f'clips {words[0]}..{words[11]}, name call {words[NAME_CALL_SLOT]}')
    hs = dol_hammerspace.get(ctx)
    placed = install(ctx.dol, hs, tables)
    hs.commit()
    log.append(f'voice hooks installed (wrapper, owner, resolver + 5 captures, lookup); {len(tables)} table(s) '
               f'at 0x{min(placed["tables"].values()):08X}, state 0x{placed["state"]:08X}, '
               f'pointers 0x{placed["pointers"]:08X}')
    return log
