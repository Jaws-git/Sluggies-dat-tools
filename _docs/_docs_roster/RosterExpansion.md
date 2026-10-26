# Roster Expansion: Format and Code Reference

How SluggiesTools extends Mario Super Sluggers' (US, `RMBE01`) character
roster: new character IDs, colour wheels of up to 10, a larger exhibition
draft grid, icons and names. Addresses are US `main.dol` virtual addresses;
all values are big-endian. The user-facing workflow is in
[`../RosterGuide.md`](../RosterGuide.md).

**Sources and dates.** Static analysis of `main.dol` and `dt_na.dat`
(September–October 2026), cross-checked against the Sluggers Characters Beta
tool (a third-party patcher whose code we may reuse). Its word-level patches
were compared site by site with our build (`SluggiesTools/Dol/site_inventory.json`,
687 sites). Everything marked *Dolphin* was confirmed in game on 2026-10-03
unless another date is given.

Code: `SluggiesTools/Roster/` (one module per step, run in this order by menu
[7] / `start.py --roster`): `dol_hammerspace`, `layout_file`, `ids`,
`model_dirs`, `wheels`, `icons`, `voices`, `grid`, `names`. Before every run, `reset.py` resets the
roster to vanilla against `1_Input` (no record of earlier runs is kept):

- `main.dol` is rebuilt from the input DOL, keeping the output's directory
  records. Those records are the only DOL bytes the other tools write (model
  patches, the untangler). The roster's own three records go back to the input
  too: select layout, icon bank, name table.
- In `dt_na.dat` the roster writes only past the stock end, into the copies
  those three records point at. The reset zeroes them unless another record
  still routes there. The file and its `fst.bin` size stay grown.

## Character IDs

| IDs | Meaning |
|---|---|
| `0x00`–`0x46` | stock players (71) |
| `0x47`–`0x4C` | the six unused characters (spare rows; own model data in dt_na dirs 89–94) |
| `0x4D`–`0x64` | Miis: wheel groups `0x0C` (male) and `0x0D` (female), species `0x29`/`0x2A`, dirs 95–106 and 107–118. A Mii is a base body plus a head built at runtime from the console's Mii data. |
| `0x65` | "no character" sentinel |
| `0x66`–`0xFE` | new IDs (153) |
| `0xFF` | ends ID lists |

A new ID plays as its **template**, a stock character whose row it copies in
every per-ID table. 24 tables move to the DOL hammerspace data section with
256 rows each (selector, stats, pitching, catching, hitbox, size and effect
scales, model handles and others). The game's ID range checks are widened to
`0xFF`, and about 15 hooks (roster list, family path, model resolver and
directory map, portrait tests, chemistry, team list, charge-effect stack
copies) let new IDs through. Two tables stay where they are on purpose:
`0x806314E8` (read only by a `< 0x4D` loop) and `0x80630A28`.

**The moved tables** (row size in bytes where it is not 1):

| Address | Contents |
|---|---|
| `0x80631550` | selector rows (8; see Colour wheels) |
| `0x806CE9A0` | stats: 8-byte header, then `0x8E`-byte rows; bytes 0–1 are the row's own ID, chemistry follows |
| `0x80628400`, `0x806288BC`, `0x80628924`, `0x80628EB0`, `0x806289F0` | pitching: windup, star pitch, stamina, change-up, throw floats |
| `0x806291D8` (`0x24`), `0x8062A00C`, `0x8062A15C`, `0x8062A228`, `0x8062A424` | character floats, trajectory and other per-ID parameters (not decoded) |
| `0x8062A688` (`0x28`), `0x8062B678`, `0x8062B874`, `0x8062BA08` | catch range, throw variant, hitbox |
| `0x8062EB50` | size scale |
| `0x806B4970` | own character data flag (below) |
| `0x806250E8`, `0x80624F30`, `0x80624D98`, `0x80624C00`, `0x806252B0` | ice-block, pitch-charge, bat-charge and two effect scales |
| `0x80709408` (bss, `0xC`) | model handles |

The colour-wheel table alone is built by 111 `lis`/`addi` pairs; none points
into the middle of a table and no `lis` is shared with other data, so a table
moves by rewriting every pair. A table that is not grown shows up only as a
bug on new IDs (missing ice block, a ball never released, missing charge
circles), so this list was found table by table; a sweep for further
101-row tables found none. Not per-ID, despite earlier readings:
`0x80630A28` (49-byte pattern records) and `0x80672970` (an effect factory).
The character manager also keeps per-ID heap records (`mulli id,0x68` at
`0x80366FE0`).

**What follows the ID.** On the select screen and in game (*Dolphin*
2026-09-24/25):

| Property | Comes from |
|---|---|
| wheel membership and order | selector byte 2 (species), then character ID order |
| selectable | selector byte 6 (plus the unlock check for IDs `0x36`–`0x46`) |
| swatch colour | selector byte 7 |
| model | the ID's model directory (below) |
| icon, stats, name | the character ID |
| voice, on the select screen and on the field | the species (byte 2): a variant moved to another species' wheel speaks with that species' voice (see Stats, size and voice) |
| everything on the field | the ID after the own-data substitution: an ID without own data is Peach as a whole character |

The select screen does not go through that substitution: it shows the raw
ID's preview model, so a missing own-data byte is only visible once the game
starts.

**Model routing.** A character's model directory is ID + `0x12`
(`addi r4,rN,0x12` at `0x8036625C`, `0x803664E8` (model loader entries),
`0x80376480` and `0x804A519C`/`0x804A5224` (the select-screen model task)).
Directories 18–118 are the 101 character directories (15 files of 48-byte
records each); 119 and up hold other content. A variant is a complete model
of its own, not a palette swap: Red to Pink Yoshi (`0x42`–`0x46`) are dirs
84–88, each a full block. The unused IDs' records point at their wheel host's
block until the untangler splits them. For new IDs these five sites read a
halfword `dirmap[id]`, which gives a new ID its template's directory. Two
tables in the model loader `0x8036629C`, `0x806B49D8` (8-byte rows) and
`0x806B4D00` (12-byte rows), are indexed by directory − `0x12`, not by the
picked ID; they only matter once an ID gets its own directory. The external
tool found that an ID with its own directory needs its own copy of **every**
file: sharing the template's copies made two same-template characters
invisible when both were loaded.

**Own model directories** (`ids[].model = {"from": "0xNN"}`,
`Roster/model_dirs.py`; *Dolphin* 2026-10-05). Every file of the source
character's directory is copied from `1_Input/dt_na.dat` into DAT
hammerspace. The new directory's records go into the DOL data section behind
a moved directory table: `SLGDIRS\x01`, u32 directory count, then one
pointer per directory (`Dol/dirtable.py`), so every reader finds the new
directories from the game's own lookup without other metadata. `dirmap[id]`
points at the new directory. The model loader `FUN_8036629C` also treats a
directory as a model ID (above `0x5E` selects the Mii format, directory −
`0x12` indexes the per-model rows above), so two hooks map a new directory to
its source's first (`revmap`, at `0x80366340` `cmplwi r27,0x5F` and
`0x803663A8` `subi r31,r27,0x12`). The model resolver keeps an own-model ID's
own ID, and its model handle rows and body tables (size, hitbox, effect
scales) follow the source. The copies are vanilla bytes, without the
untangled texture bytes of an untangle export. A rebuild keeps a directory's
existing copies when the config lists their `routes`, so model patches made
into it survive; the reset zeroes every other old copy. Own directories
cost DAT space, not RAM: only the models a match loads count against its
memory.

**IDs `0x80` and up.** Team and record code (`0x8018A7D4`–`0x80196414`) loads
IDs with `extsb`, so these IDs turn negative there. *Dolphin*: the shipped
presets (new IDs up to `0xF3`) pass drafting, random picks and full games.
Whether an ID of `0x80` or more was on a team in those games, and the results
and records screens, were not checked specifically.

**Where new IDs do not appear.** The screens behind `0x801C32A0` and
`0x8018BED0` scan only IDs `0..0x46` / `0..0x4C`. Toy Field, Minigames and
Free practice (the colour-wheel select screen) are out of scope: they get
only the patches that keep shared code from overrunning, untested.

**Save data.** The save's unlock bytes cover IDs `0x36`–`0x46` only, and every
other ID counts as unlocked, so new IDs are always available
([`../SaveData.md`](../SaveData.md)).

**Own character data.** `0x806B4970[id]` is a flag, not a selectable table:
`0x80367060` returns `own_data[id] ? id : 4`, so an ID with 0 there plays as
**Peach (ID 4)** on the field (model, animations, stats, voice; *Dolphin*
2026-09-25). The spare rows need it set to 1; new IDs copy their template's.

## Stats, size and voice

Static analysis of `main.dol`, 2026-10-05, with the external tool's voice
routing notes (`_docs/Custom Character Sounds Pipeline - Rosalina Luma
Larry.txt`) as the starting point.

**Voice is the species.** Every voice path takes the family from selector
byte 2 of the character ID (the ID in the player's stats row, bytes 0–1):

| Path | Code | What byte 2 picks |
|---|---|---|
| voice bank load | `0x803868AC`, `0x80233014`, `0x80233368` → `0x804B231C` | the sound group `0x80631F10[species]` (u32, 48 entries; `-1` for the Mii groups `0x29`/`0x2A`: no voice) |
| voice clip | wrapper `0x804B2898`, r5 = species; callers `0x80386FA4`, `0x80388694`, `0x80387104`, `0x804A5628` | the clip `0x80631FD0[species × 0x30 + slot × 4]` (12 sound INFO IDs per species) |
| family search | `0x80387028` (27 callers) | the first player of both teams whose byte 2 equals the requested family speaks |
| select-screen voice | `0x804A5628` (slot 10, `v13`) | as the clip row; all select voices are in one shared group |

So a character speaks with the species of its byte 2 everywhere, and the
game loads that species' bank for it: the stock game has no per-ID voice.

**Character voices** (`character_voices`, `Roster/char_voices.py`,
2026-10-10; the external tool's `voice_hooks.py` ported, permission to reuse)
give one ID a voice of its own. The exact ID travels as `ID * 8`, the
selector-row offset the callers already hold in r0, to the clip wrapper
`0x804B2898`, which remembers the ID's own 12-slot table; the lookup
`0x804B2910` (`lwzx r5,r5,r0`) reads that table instead of the species row.
Nine sites (inventory group `char_voices`): the wrapper entry (`stwu`,
branch), the lookup (branch), the owner `0x80142078` (`bl` to the family
search `0x80387028`, which publishes r0 around the call: inside the search
r0 is the first family member, not the actor), the scripted actor resolver
call `0x8036C674` (`bl 0x8036B4E0`; mound entrance, strikeout celebration)
and its five `rlwinm r0,r0,3,0,28` ID-to-token conversions, which publish
the ID while the resolver runs. The wrapper takes the token from the owner
word when its return address is `0x8038710C` (the search), from r0 for the
direct callers `0x80386FA8`, `0x80388698` and `0x804A5630` (the select
screen), and none otherwise: every other caller of the wrapper, and the
other callers of the family search, keep the species voice (coverage as the
external tool's; not surveyed further). Stubs live in the DOL hammerspace
text section, the tables, a 12-byte state and a pointer table (one word per
ID up to the highest voiced one) in the data section. Checked on the clean
DOL (2026-10-10): all nine sites hold the expected stock words, and the three
direct callers do leave `ID * 8` in r0 (`rlwinm r0,r0,3,0,28`, then
`add r5,rX,r0; lbz r5,2(r5)` before the `bl`), so the wrapper's token read
is sound.

An ID's table starts from its species' clip row *as the run leaves it*
(after stock square swaps and square voices), with the chosen slots
replaced. The replacing clips come from the chosen voice's *stock* row
(`voices.py` publishes `ctx.state['stock_clips']` before it writes the
swaps), so "Bowser's voice" means Bowser's own sounds whatever the swaps
did.

A gameplay clip plays only while its bank is loaded, and the game loads the
bank of the species on the field (the group word, after any stock square
swap), so the voice's 11 gameplay clips are copied into the ID's *family
file*: the archived RWSD + RWAR file (`MY2.brsar`) that holds its species'
current first clip (`v1`). `Sound/brsar_write.add_sounds` appends them
(RWAR with the RWAVs byte for byte, RWSD entries copied from entry 0, INFO
entries copied from the family's same-slot clip with the voice clip's
volume, the sound table and the SYMB string table moved to their sections'
ends, the FILE section laid out again); stock sounds keep their IDs, bytes
and files. Each (file, voice, slot) is copied once, named
`zz<clip>_f<file>`. The name call (slot 10, `v13`) is in the select file
(file 160, every species' calls, always loaded on the select screen): the
table takes the voice's stock ID there. An ID whose species already speaks
the voice gets its row as its table and no copies. The runner reads
`1_Input/sound_NA/MY2.brsar` and writes `3_Output_Dat/sound_NA/MY2.brsar`
on every run, the input bytes plus the copies, so no run builds on another;
voices that need copies are refused without the input archive. Vanilla family
files hold 79–252 KB of wave data (the select file 726 KB); a copied voice
adds the source's 11 clips (Bowser's into Mario's file 14: 161,152 →
369,696 bytes). The external tool's fullest bank, about 1.16 MB, worked
(`FAMILY_FILE_TESTED_MAX`); the Maintenance "Sound memory" check warns past
it. *Dolphin* 2026-10-10: Mario with Bowser's name call on the select
screen (table only); the vanilla archive plus one unused copied clip plays
every stock voice as before; Mario with Bowser's copied clips speaks with
Bowser's voice on the field while his wheel members keep Mario's, no
crashes.

The vanilla US `MY2.brsar` (2026-10-10): 14,745,728 bytes, 1,050 sounds,
1,179 SYMB names. Family files: Mario 14 (161 KB of wave data), Bowser Jr
40 (148 KB), Bowser 105 (252 KB); select file 160 (726 KB; sound 673 is
`cuppa_v13`, Bowser's name call). With no sounds to add, `add_sounds` writes
the input back byte for byte. The external tool's writer does not: it always
moves the SYMB string table and the INFO sound table to the ends of their
sections (SYMB 65,248 → 69,984 bytes, INFO 107,488 → 115,904 bytes), while
its rebuilt FILE section is byte-identical; ours moves them only when it adds
sounds. With one copied clip (Bowser's `cuppa_v1` into file 14, +27,680
bytes: RWSD 2,112 → 2,304, RWAR 161,152 → 175,360), the archive re-parses
with every stock sound unchanged, the other 140 files and 72 of 73 groups
byte-identical, and the new sound decodes sample for sample like the source.
New INFO entries are appended, so stock sound IDs never move. The external
tool works from a different base archive (Extra Innings), so the INFO IDs in
its voice tables do not apply to ours. Voice clips use stock coefficient
sets, as the external tool's voices do. Its coefficient training
(`dsp_correlate_coefs`) was not ported; effects and music use our own
`brstm_write.train_coefs` (see "Sound archive: effects, music, volumes").

**Custom clips** (2026-10-10, `Roster/clip_files.py`, `Sound/clips.py`):
`character_voices[].clips` and `species_clips[].clips` map voice moments
(`v1 v3 v4 v5 v6 v7 v8 v10 v11 v12 v13 v14`, the clip row's slot order,
`Sound/sound_packs.MOMENTS`) to clip files. A WAV of any PCM or float format
is mixed to mono, resampled (linear) to 32,000 Hz, the stock clips' rate, cut
at 2.5 s (the longest stock clip is 2.419 s) and DSP-ADPCM encoded with the
coefficients of one of the first 8 waves of the select file (the one that
reproduces it best; about 5-6 s of encoding per second of audio with 8
candidates); an `.rwav` is taken as it is (checked: mono DSP-ADPCM). The
encoded RWAV is what every later stage carries, byte for byte, like the
portraits' CMPR blocks: derived configs and roster packs name clips by content
(`<sha1[:16]>.rwav` in `clips/` beside the config, format 3 packs), the
derive reads them back from the output archive by their INFO IDs, and an
unchanged derive -> rebuild stays byte-identical (`tests/test_roster_derive.py`,
"custom clips"). A relative clip name is looked up in the run's clip folder
(`RosterContext.clip_dir`: `clips/` beside a derived config, the preset's own
folder for a hand-written one). Species clips are written by the `voices`
step before the square swaps (the species' row word points at the new sound,
appended to its family file, or to the select file for `v13`, with the
species' own clip in that slot as the entry template), so a swap or a
character voice that copies the voice takes the replaced clips along; the
`char_voices` step adds a character's own clips the same way (one sound per
file, slot and content, named `zzc<hash>_s<slot>_f<file>`, species clips
`zzs...`), and when the species' current clip in that slot already is that
very RWAV the table takes the existing sound. Manifest: `species_clips` as
`[species, slot, INFO ID]`, `character_voices` as `[id, voice]` for a plain
stock voice (the old shape) or `[id, voice or null, {slot: INFO ID}, pack
label]`. The state carries `char_clip_ids` / `char_pack` per character and
`clip_ids` per stock square; `state_cli` turns the IDs into RWAV SHA-1s
(`char_clips`, `clips`) for the pack fingerprints (`clips`, `species_clips`;
absent in older packs, which count as "no custom clips").

**Voice packs** (`Sound/sound_packs.py`): a custom pack is a folder
`_gui/Sound_Packs/<name>/` with `pack.json` (format 1: `name`, `voice` = the stock
base voice or null, `clips` per moment: the encoded `.rwav`, the prepared
`.wav` for playback, the user's `origin` file, `seconds`), exportable as one
`.sluggiesvoice` zip. Assigning a pack to a slot (`--set-char-voice 0xNN
pack:NAME`, the grid's slot-level Voice...) turns it into a `character_voices`
entry with the pack's clips (`slot_plan.plan_char_voice`, `Env.sound_pack`),
so the game files never depend on the pack folder. A stock pack is a species'
clip row plus its sounds as the input files hold them (`stock_packs`); the
GUI's Voices tab lists both kinds (`gui_sounds.py`).

Real build (2026-10-10): Mario with Bowser's voice plus a custom `v1`
(Luigi's `luigi_v1.wav` re-encoded, 9,472 B): file 14 161,152 -> 364,992 wave
bytes, 11 sounds added. Dolphin check of custom clips still open.

**Byte 2 is more than the voice.** It has about 83 readers. Besides wheels,
squares and voice, gameplay code tests a few species directly: `0x19`
(Magikoopa family; `0x800AEB4C`, `0x800B3278`, `0x80148100`, `0x8014A20C`,
`0x80153C04`), `0x16` (Noki family; `0x801650C4`), `0x24` (Kritter family,
together with ID `0x3E` K. Rool; `0x80387F3C`, `0x80388098`, `0x8038820C`,
`0x803888F4`), and `0x29` (Miis). What these branches do is not decoded. A
character whose byte 2 names one of these species while its model and
animations come from another (or the reverse) may behave oddly; untested.

**Square voice.** On the exhibition draft a new square's members are listed
by ID (the grid's member-list hook), not by species, and a square-only new ID
(wheel group 0) is on no species list. Its byte 2 is therefore free: the
roster sets it to the square's voice. Every other member keeps its byte 2:
a spare row or stock ID is on its species' list (roster builder
`0x8006BA6C`, and the Toy Field wheels), and a new ID with a wheel is
appended to its wheel's species list. *Dolphin* (2026-10-05): two new IDs
on one new square, with Bowser's and Mario's models and stats and the square
voice Bowser, both speak with Bowser's voice on the select screen and on the
field.

**Stock square voices** (`stock_voices`, `Roster/voices.py`; static analysis
2026-10-05). The two species tables have exactly five readers, all voice
code: `0x80233010`, `0x80233364` and `0x80386894` (bank loads) read the group
word `0x80631F10[species]`, the clip wrapper reads both (`0x804B28DC` the
group, as a "has a voice" test, `0x804B2904` the clip row
`0x80631FD0 + species × 0x30`). Every other byte-2 reader (wheels, squares,
the species branches above) never reaches them. So copying species V's group
word and clip row over species S makes every character of S speak with V's
voice on the select screen and on the field, with byte 2, the wheels and the
gameplay branches unchanged. A stock square is one species, so its voice is
changed this way, for its whole wheel (spare rows and new IDs on it
included). Stock rows: species `0x00`–`0x28` have their own group and twelve
clip IDs; `0x29`/`0x2A` (Miis) have group `-1` (no voice) and repeat
species 0's clips. Data only, no hooks; the reset rebuilds the tables from
`1_Input`. A new square's square-only members then take the byte 2 of a
species that still speaks with the wanted voice (the voice's own species,
or the one that took its sounds in a swap); when none does, the build is
refused. *Dolphin* (2026-10-05): with Mario's and Bowser's voices swapped,
each wheel speaks with the other's voice on the select screen and on the
field.

**Stats and size.** A new ID copies one row per moved table. The rows split
into what the character plays like and what belongs to its body:

| Group | Tables |
|---|---|
| stats | stats (`0x8E` rows, chemistry included), pitch windup, star pitch, stamina, change-up, trajectory, catch range, character floats (`0x806291D8`, incl. the strike-zone height at `+0x1C`), throw floats, throw variant, `perid2`/`perid4`/`perid5a`/`perid6` |
| body | size scale, hitbox (body cylinder radius/height), ice-block scale, pitch- and bat-charge scales, the two effect scales |

The body tables size the model and its effects (the size scale scales
skeleton and mesh together, `FUN_80367080` → `FUN_80382558`), so they follow
the ID's model: its own directory's source, else the template. Within the
vanilla colour families (same body, different stats) the body tables are
identical except for the two Paratroopas' size scale and hitbox. The
`perid*` and throw-variant rows are not decoded; some hold frame counts
(they pass through the 50/60 Hz converter `FUN_804B9BD4`) and may belong to
the animation set rather than to the stats. Chemistry: a new ID's stats row
holds its chemistry towards the stock IDs; two new IDs use their stats
sources' pair.

**Stock characters' stats** (`stock_stats`, 2026-10-05). A stock ID's rows
of the stats group become another stock ID's (the stats row keeps its own ID
in bytes 0–1); its body rows, selector row and own-data flag stay. Chemistry
stays symmetric: in every row, the column of a restatted ID A becomes the
row's stats source's column for A's source, so the pair (A, B) always reads
the stock pair (source of A, source of B), for stock and new IDs alike.
With an `ids` key this is done in the moved tables, otherwise in place.
*Dolphin* (2026-10-05): a stock character with Bowser's stats shows and plays
them, for stock and new slots.

## Sound archive: effects, music, volumes

`SluggiesTools/Sound/` reads and writes the game's `files/sound_NA` (copied by
the user into `1_Input/sound_NA/`). Character voices and custom clips are
described under "Stats, size and voice"; this section covers the other
sounds. Static analysis of the vanilla US files, 2026-10-10..12.

**Archive contents.** `MY2.brsar` holds 1,050 sounds: 853 WAVE (all
DSP-ADPCM; 830 at 32 kHz and 23 at 44.1 kHz, one stereo, 26 looped), 78 STRM
(music and jingles, streamed from `stream/*.brstm`) and 119 SEQ (note data
played through an instrument bank, such as the bat hits `bb_hit_m`). It also
has 219 files, 73 groups, 22 players and 35 banks. Groups `group_v001` to
`group_v041` are the voice banks, one per voice, and `group_sel_v` (file 160)
holds the select-screen name calls. No wave sits in two groups, and no wave is
unreferenced; in 10 files several RWSD entries share a wave. Sound INFO IDs
are the `id`s in the sound export's `index.json` (Mario's `v1` is 196).
DSP-ADPCM decodes to exact 16-bit PCM, so the export's WAVs are lossless. The
decoder matched the external tool's bit for bit on 29 waves and 5 streams.
Streams are decoded block by block from the ADPC chunk's history, as the
player does.

**Sound effects** (`Sound/effects.py`, the GUI's Sound FX tab). These are the
345 WAVE sounds of archived files outside the voice-bank groups: 299 mono at
32 kHz, 23 mono at 44.1 kHz, 22 mono looped and one stereo. The longest is about 2.5 s. SEQ
sounds have no single waveform and cannot be replaced. A replacement is
encoded in the stock wave's shape: its channel count, rate and loop (a WAV
`smpl` loop is kept, otherwise a looping stock wave loops from the start).
Each channel is encoded twice, once with coefficients trained on the sound
and once with the stock wave's, and the closer result is kept. Sounds are cut
at 30 s; a wave over twice the stock size is noted, because a group loads
into sound memory whole. `brsar_write.replace_waves` puts a wave in place when
only that sound plays it. When other sounds share the wave, it appends the
new one and points the sound's RWSD note at it. Either way, every sound keeps
its INFO ID and entry. The FILE section is laid out again with
`add_sounds`' layout, which writes wave offset 0 for items without wave
bytes, as vanilla does, so a relayout with no changes is byte-identical to
the vanilla archive. The library `_gui/Sound_FX/` (`effects.json` format 1 plus
one `<sound name>.rwav` per sound) is keyed by sound name. The tab writes
`3_Output_Dat/sound_NA/MY2.brsar` at once (`sync_output`: library waves in,
stock waves back for sounds no longer in the library). *Not yet tested in
Dolphin* (2026-10-12): whether replaced, looped and stereo effects play, and
how far a group can grow before sound memory overflows.

**Music** (`Sound/brstm_write.py`, the Music tab). The 78 stock streams are
all stereo DSP-ADPCM at 32,000 or 44,100 Hz, in blocks of 8,192 bytes
(14,336 samples) per channel. A replacement keeps the stock file's channel
count, rate and block layout: `build` copies its HEAD chunk and patches the
counts, the loop and the per-channel ADPCM info. The archive's STRM entry
therefore needs no change, and only the `.brstm` is written, directly to
`3_Output_Dat/sound_NA/stream/`. The roster run does not touch streams.
Encoding is built for tracks several minutes long:
- **Coefficients:** 8 predictor pairs are trained per channel, by clustering
  the 14-sample frames on their prediction error.
- **Block batching:** every block of every channel is encoded side by side
  in one batch. Each frame tries the 8 predictors at 3 scales around the one
  its residual needs (24 candidates instead of 128).
- **Seam-free blocks:** blocks whose real starting history (the decoded end
  of the block before) differs from the first pass are re-encoded until they
  rejoin it, so the stream plays without seams when decoded back to back.
- **Resampling:** FFT band-limited.

**Volumes** (`Sound/volumes.py`). Every INFO sound entry (wave, sequence or
stream) holds a `u8` volume at `+0x14`, 0–127. In vanilla the mean is 103.1,
666 sounds use 107, and streams sit lower (36–117, mean 80.8). The byte only
scales a sound down from its wave's own level. `sound_volumes.json` (format 1,
`{"volumes": {name: volume}}`, beside `1_Input`) holds the user's values by
sound name; a value equal to the stock one drops out. They are written in
place (nothing moves), at once by the GUI and again on every run. Sounds that
the voice steps add keep their source's volume. The tabs' **Replace gain**
(`clips.apply_gain`, ±24 dB) scales the input samples before encoding, which
is the only way to go louder than stock.

**Order in a roster run** (`Roster/runner.py`): the input archive, then the
voice steps (species clips, square swaps, character voices), then the effect
library (`effects.apply_library`), then the volumes (`volumes.apply`). So
**Patch Game** always rebuilds `3_Output_Dat/sound_NA/MY2.brsar` from
`1_Input` with every kind of edit in it.

**Input files.** Files that are not WAV (MP3, OGG, FLAC, ...) are decoded by
ffmpeg (`Sound/ffmpeg.py`), which is looked up on PATH and then in the
winget, Scoop and Chocolatey link folders, because a winget install adds to
PATH only for programs started after it. A loop comes from a WAV `smpl` chunk
only.

## Stat values and the stat editor bridge

The values Philenarion's Sluggers Stat Editor edits, where they are, and how
Sluggies hands them to the editor and takes the edits back. Code:
`SluggiesTools/StatEditor/` (`fields.py` is the one field table, `bridge.py`,
`carry.py`, `apply.py`). The editor side is `sluggies_bridge.py` in the
editor's own repository.

**Per-character fields.** Nine of the moved tables hold everything the editor
edits per character: `stats`, `pitchwindup`, `starpitch`, `stamina`,
`changeup`, `traj`, `catchrange`, `hitbox`, `sizescale`. Field names are the
editor's own list entries. A name that repeats in its list gets `#index`
(`height#5`).

| Editor field | Place | Type |
|---|---|---|
| stats 0–9 | stats row `+0x02`…`+0x0B` (stat `j` at `+j+2`) | u8 |
| stats 10–17 (slap/charge size and power, bunting, speed, throwing, fielding) | `+0x0C`…`+0x1B` | u16 |
| stats 18–21 (displayed pitching, batting, fielding, speed) | `+0x1C`…`+0x1F` | u8 |
| stats 22–25 (curveball/charge pitch speed, curve, curse ball) | `+0x20`…`+0x27` | u16 |
| chemistry towards stock ID `k` (0–2) | `+0x28 + k` (101 columns) | u8 |
| stats 26/27 (traj, hit curve) | `traj` row `+0`/`+1` | u8 |
| stats 28 (stamina) / 29 (star pitch type) | `stamina` / `starpitch` row | u16 / u8 |
| pitching 0–2 / 3–4 | `pitchwindup` / `changeup` row | f32 |
| size 0–1 / 2–11 / 12–13 | `sizescale` / `catchrange` / `hitbox` row | f32 |

Stats row bytes 0–1 are the row's own ID and are never written.

*Dolphin* (2026-10-06): after expansion the game reads only the moved tables,
for stock and new IDs. The stock tables are no longer read. The
character-select bars come from the "displayed" bytes (`+0x1C`…`+0x1F`), not
from the internal stats. The two are independent: editing the internal stats
does not move the bars.

**Chemistry** lives in three places. Stock × stock is the stock row's column
and is directional. Stock × new and new × stock are **one** byte in the new
ID's row, at the stock ID's column, so that pair is symmetric. New × new is a
separate directional 154 × 154 matrix (IDs `0x66`–`0xFF`) in the data
section. Its address is read from the chemistry hook stub at `0x8015C880`
(the stub's second `lis`/`addi` pair).

**Global tables.** These never move.

| Table | Address | Layout |
|---|---|---|
| speed | Fielding `0x80625898`, Baserunning `0x80626208` | 43 f32 each, indexed by the speed stat |
| traj heights | `0x80626E88` | 24 × 25 u8 |
| team stars | `0x8062BD50` | 12 teams × 41 events, s16 |
| star handicap | `0x8062C128` | 4 × 2 f32 |
| star boosts | `0x806318D8` | 16 rows of 12 bytes: u32 op (1 add, 2 mult), f32 amount, s16 min, s16 max |
| handicap params | `0x801808EF`, `…8FB`, `…903`, `0x80180977`, `…983`, `…98B` | the low byte of six `addi`/`li` immediates in code |

**Roster runs carry stat edits.** A roster run rebuilds `main.dol` from
`1_Input` and so resets every stat value, globals and handicap immediates
included (dry runs with marked bytes, 2026-10-06). The runner therefore
reads the edits before the reset and writes them back afterwards, as it does
for the game options (`carry.detect`/`apply` in `Roster/runner.py`). No stat
record is kept anywhere else: the edits are re-derived from the DOL on every
run, so derive → rebuild stays byte-identical.

- A per-character field is an edit where the live row differs from the row the
  roster writes without edits. That row is the stats source's vanilla row
  (`bridge.baseline_rows`). Edits are keyed by ID and field. They follow the
  ID into a moved table, and they survive a stats-source change: the other
  fields take the new source's values.
- Chemistry is keyed `(row ID, column ID)`. IDs keep their kind, so an edit
  always lands in the same region.
- Globals are compared with `1_Input`.
- Edits of IDs the new roster lacks are dropped, with a log line.
- Edits are carried as bytes, so floats round-trip exactly.
- `--roster --remove` clears all stat edits; `--keep-stat-edits` keeps them.
- The all-in-one export copies `1_Input/main.dol` and loses them all.

**The bridge** is two JSON files in the editor's `Bridge/` folder. That folder
sits next to `sluggers-stat-editor.exe`, or next to `editor.py` when the editor
runs from source.

- `stat_bridge.json` (format `sluggies-stat-bridge` v1) is written by
  `start.py --stat-bridge-export FILE [--focus 0xNN]`. It holds:
  - `files`: the paths to `main.dol` and `dt_na.dat`, and the DOL's SHA-1.
  - `dol_sections`: address → file offset per section.
  - `characters`: stock `0x00`–`0x64` plus the roster's new IDs, each with its
    displayed name, family, stats and model source, and kind.
  - `tables`: per table, the address, file offset, header, row size, row count
    (256 when moved with new IDs, otherwise 101) and `moved`.
  - `chemistry`: the three regions, with the new × new matrix's place.
  - `globals`: the global tables' places.
  - `baseline`: base64 rows the roster writes without stat edits. A row is
    listed only where it differs from vanilla; new IDs are always listed, and
    the new × new matrix comes whole. The editor uses these as its defaults.
  - `focus`: an ID to preselect, or null.
- `stat_edits.json` (format `sluggies-stat-edits` v1) is written by the
  editor's **Send to Sluggies**. It holds only values that differ from what
  the bridge reported, by ID and field key (`stats`/`pitching`/`size`/
  `chemistry`), and globals as table → row → column. Rows and columns without
  names are numbered from `"0"`. It never contains addresses.
- **Applying** (`start.py --apply-stat-edits FILE... [--dry-run]`, slot op
  `stat_edits`):
  - Checks the file:
    - the file must carry the current `main.dol`'s SHA-1;
    - IDs, fields and keys must be known;
    - every value must fit its storage;
    - chemistry must be 0–2 and the star-boost op 1 or 2;
    - a stock × new pair given both ways must not hold two different values.

    Any failure refuses the whole set.
  - Warns about values outside the editor's own input ranges (vanilla `0x0B`
    has slap size 5, below the editor's minimum of 10), and about a character
    with both a fielding and a baserunning ability, which the editor marks as
    a crash.
  - In a Patch Game batch, stat edit files and `reset:0xNN` items (clear one
    character's edits back to the roster's values) run as one step before the
    roster rebuild, which then carries them.
- **Session lifecycle.** Sluggies writes the bridge and starts the editor.
  Send to Sluggies writes the edits, deletes the bridge and closes the editor.
  The editor deletes only that one bridge file, and only while it still holds
  the bytes it read.
  - When the editor exits, Sluggies moves `stat_edits.json` into
    `_gui/stat` and deletes what is left.
  - Leftovers from a crash are handled at start-up: a bridge is deleted, and an
    edits file is offered only if its hash still fits.
  - Without a bridge file the editor runs Standalone. Its Gecko codes target
    the vanilla addresses, so on an expanded game they silently miss the nine
    moved tables.

## DOL hammerspace

Two sections added to `main.dol`: code at `0x807B7000` (0x9000 bytes) and data
from `0x807C0000`. OSInit's four arena-start `lis`/`addi` pairs move up behind
the data section; arena low may rise to `0x808BF000`. Each section starts with
a 16-byte magic so later runs can reopen and extend it.

The stock DOL uses 10 of its 18 section slots (T0, T1, D0–D7); T2–T6 and
D8–D10 are free, and new sections are appended at the file end (`0x716F20`).
Memory above the loaded image:

| Range | What |
|---|---|
| `0x80706D00`–`0x807A4E80` | bss (the end is the highest loaded address) |
| `0x807A4E80`–`0x807B4E80` | main-thread stack (grows down; canary `0xDEADBABE` at its bottom). Not a cave, although it usually reads as zeros. |
| `0x807B4E80`–`0x807B6E80` | debugger stack. `0x80612D80` also uses `0x807B6E80` as a stack top, so its frame writes land just above it: new sections start at `0x807B7000`. |
| `0x807B7000`– | our text, then data section; the MEM1 arena follows |

**Arena start** (OSInit `0x80595EB4`): the low-memory value at `0x8000310C`,
else BootInfo `0x80000030`, else one of two code constants: `0x807B4E80`
(`lis`/`addi` at `0x80596014`, the retail path) or `0x807B6E80`
(`0x80595FC4`). `0x8059606C` and `0x805960A0` repeat both for a MEM2 arena;
all four are patched. *Dolphin* (2026-09-24): the boot leaves both low-memory
values at 0, so the patched constants decide (`bl OSSetMEM1ArenaLo` at
`0x8059602C` receives them). Real hardware and Riivolution are untested.
**Ceiling:** the game creates a fixed 15 MB MEM1 heap at arena low +
`0x4006C`, which must end below arena high, hence `0x808BF000` (measured by
the external tool in six RAM dumps): about 1 MB of DOL hammerspace. All
tables for 153 new IDs take about 80 KB, almost half of it stats.

**Game options share the sections.** The gameplay options
(`GameOptions/game_options.py`: CPU vs CPU, CPU management, player memory,
see *Game memory*) put their hook
stubs into the same text section, creating the sections on a stock output.
So the sections alone do not mean a roster was built: a DOL whose data
section holds nothing but the magic counts as the stock roster
(`DolHammerspace.has_data`). Turning the last option off on a stock output
drops the sections and restores the arena words; with a roster the stubs
stay until the next roster run.

**Incompatible with the community Gecko codes they replace** (confirmed in
Dolphin, 2026-10-08, on a stock roster too). With the Gecko codes "CPU vs
CPU V2" and "CPU vs CPU human management" still enabled in Dolphin, CPU vs
CPU matches start as normal human vs CPU matches; disabling both Gecko codes
makes our options work. Two reasons fit: the Gecko codes hook the same
instructions (`C2` at `0x80063D4C` and the three management sites), so
Dolphin overwrites our branches with its own when it applies them; and they
read and write stock heap addresses (`0x81317BB0`, `0x900D5BD0`), which any
DOL hammerspace moves, even one holding only option stubs (the arena start
rises from `0x807B6E80` to past the sections). Turn the Gecko codes off
before using the options.

**The MSS-AutoTeam autopicker** (`_Sluggers Characters Beta External Tool/
Autopicker Source`, 2026-10-24) breaks the same way, for a different reason
than its Gecko code. Its three Gecko lines (`04 0802B4` / `04 0802B8`: nop
the `bl 0x80069CC0` and `bl 0x802C946C` in `fn_80080010`, the lineup
screen's update; `04 06AED8 48000B80`: skip the body of `fn_8006AEA4`, the
lineup reset, to its exit at `0x8006BA58`) and the stub it writes at
`0x800021F0` (reached by a `b` it puts at `0x8006AED4`) touch no instruction
our patchers change, and the stock and output DOL are byte-identical in both
functions. What breaks is `finalize()`: it writes the stadium and the two
captains at `0x811F769D`–`0x811F76AD`, i.e. +5..+7 and +0x14/+0x15 of the
match settings object `*(r13-0xB00)` (stock `0x811F7698`; r13 =
`0x807961C0`, set at `0x8000422C`), which the external tool saw move by
exactly the arena-low delta in three RAM dumps of its own builds. The
settings pointer at `0x807956C0` is in the DOL's data, so the patched
autopicker reads it and uses the object wherever it is. The tool also
treats every ID above `0x4C` as a Mii index, so new IDs (`0x66` and up)
went down its Mii path; it now takes the Mii base from an optional
`characters.json` (`start.py --export-autopicker`,
`Roster/autopicker_export.py`: one English name per ID, empty for IDs the
grid does not offer). Its rules bytes (`0x80794328`, bss) and the lineup
pointer it reads (`0x806D121E`, data) never moved. Confirmed in a Dolphin
MEM1 dump (2026-10-24, `player_heap_32` on, arena low `0x807C0020`, Dolphin
memory override on: arena high `0x83FFF480`), taken on the exhibition captain
select screen: `*(0x807956C0)` = `0x81202838` = stock + `0xB1A0` = arena
low − `0x807B4E80` (the heaps start from the lowest of the four arena
constants, as the external tool found), with the stadium at +5 and the
captains at +0x14/+0x15; the stock address `0x811F7698` held zeros, so the
released autopicker's writes land in nothing there. A live run of the
patched autopicker on that DOL then set both 9-player teams (a Mii on
each) as configured. Not yet checked: a new ID (`0x66` and up) written into
the lineup by the autopicker's stub.

The game option `autopicker` (`GameOptions/game_options.AUTOPICKER_PATCHES`,
Options tab) writes the three Gecko lines into `main.dol` as plain
in-place patches (`Patch`: no stub, no sections), so the DOL keeps the stock
arena and `gecko_compat` still counts it as compatible. It is detected and
carried over by roster runs like the hooked options.

**CPU vs CPU extras** (2026-10-25; ported statically from the community
Gecko codes' CPU vs CPU section, not yet tried in Dolphin). Every one avoids
the stock heap addresses the Gecko codes use, so the roster's memory layout
and the player memory levels leave them working:

- `close_play_<0..100>` (group `close_play`, a hook at `0x80156348`): port
  of "CPU Closeplay Overhaul". The Gecko code reads the CPU vs CPU state at
  `0x811F76B0` (settings +0x18) and a random bit at `0x900D5BFC` (heap);
  the stub reads `*(r13-0xB00)` +0x18 and the time base (`mftb`) instead,
  and writes the static s16 `0x80624B1E` (row 0, column 3 of the table at
  `0x80624B18` that the close play code at `0x80156A5C` adds to its frame
  check): 2 for a player record (r31) with +0x1A1 set, else 1 at the chosen
  odds and 60 otherwise; outside CPU vs CPU it writes the stock 20 back.
- `triple_take`, `strikeout_camera`, `outfield_dives`: plain patches at
  `0x802500BC`/`0x802529B4` (`beq` → nop, the same as the Gecko code's
  forced r0 = 1), `0x802504A0` (`li r0,0`) and `0x800E2750` (`cmplwi r0,6`).
  No sections: `gecko_compat` still counts the DOL as compatible.
- `no_precharge` (`Floor`): "CPU Remove Precharge" writes 8.0 to six stock
  addresses, which are rows of the per-ID `pitchwindup` table (`charge`,
  f32 +0) of IDs `0x08`, `0x09`, `0x0F`, `0x27`, `0x3F` and `0x40`, the only
  stock rows below 8.0. A roster run with new IDs moves that table, so the
  option raises every character's row below 8.0 where the table's first
  lis/low pair points (new IDs' rows too, written after the hammerspace
  commit) and turning it off puts back the rows `ids.extended_table` builds
  from `1_Input`. The stat edit carry also sees the raised rows as stat
  edits and carries the same values.
- `star_pitch_buff` (2026-10-26, not yet tried in Dolphin): "Buff
  Waluigi/Yoshi/Peach Star Pitches" writes three halfwords (02 lines) of the
  static table at `0x80624908` (8-byte rows of four (s8, u8) pairs): the
  last pair of rows 6 (`0xFA01` → `0xF904`), 9 (`0xFA01` → `0xF703`) and 10
  (`0xFC04` → `0xF806`). Not a per-ID table, so it stays in place; the option
  patches the three words that hold them (`0x8062493C`, `0x80624954`,
  `0x8062495C`). The Gecko list's "Pre-Charge Removal" (8.0 at `0x80628460`
  and `0x8062846C`, the `pitchwindup` rows of `0x08` and `0x09`) is a subset
  of `no_precharge`.

**Unused character strike zones** (`unused_strike_zones`, Options tab,
2026-10-25, not yet tried in Dolphin): the Gecko code "Fix Unused Character
Strikezone Heights" writes f32 +0x1C of the `charfloats` rows (`0x806291D8`,
0x24 bytes per ID) of `0x49`-`0x4C` (Toad 1.1 → 0.8, Pianta 1.3 → 0.95,
Kritter 0.15 → 1.15, Koopa 1.0 → 0.9). `charfloats` is a stats table of the
roster: a run with new IDs moves it, a new ID copies its stats source's row,
and a stock ID that adopts stats takes its source's row (in place, without
new IDs). The option (`RowValue`) therefore writes the value into every row
copied from one of the four, wherever the table's first lis/low pair points,
and turning it off puts back the rows `ids.extended_table` builds from
`1_Input`. Only the roster runner rewrites these rows (`--set-stats` goes
through a rebuild), and it applies the game options after its steps. The
stat editor does not edit `charfloats`, so the stat edit carry ignores it.

**Characters and chemistry** (Gameplay → Extras, 2026-10-25; ported
statically from the community Gecko codes, not yet tried in Dolphin):

- `duplicate_characters` ("Duplicate Characters" [LlamaTrauma]): plain
  patches at `0x802C9210`/`0x802C9214` (`cmpwi r24,0; beq` →
  `cmpwi r24,0xFF; bne`), `0x80074DA4` and `0x80073AF8` (`beq cr1` → nop)
  and `0x80071B10` (the "already on the team" `bl 0x800638A8` →
  `li r3,0`). No sections.
- `duplicate_captains` ("Duplicate Captains" [LittleCoaks], C2
  `0x80071B0C`): a hook whose stub replays `add r3,r0,r29` and, for team
  slots 1-8 (r27), copies slot 0's character (s16 at r0) into the slot
  (`sth` at r3). It returns to `0x80071B10`, so it combines with
  `duplicate_characters`. It adds the DOL sections.
- `all_captains` ("Requires all characters are captains" [philenarion],
  08 `0x806CE9AF`, 0x65 bytes 0x8E apart): the "captain" byte (+7) of every
  `stats` row (`0x806CE9A0` + header 8, 0x8E bytes per ID) set to 1
  (`RowByte`). It follows the moved table and sets new IDs' rows too.
- `captain_self_chemistry` ("Captains Have Chemistry With Themselves"
  [LittleCoaks], twelve 00 lines): chemistry 2 (stock 1) at stats row
  +0x28 + own ID for the twelve stock captains (`0x00`-`0x06`, `0x09`-`0x0B`,
  `0x11`, `0x13`). New IDs' chemistry with themselves lives in the
  new-by-new table and is left alone.

Turning either stats option off puts back the rows `ids.extended_table`
builds from `1_Input`. The stat edit carry sees the changed rows as stat
edits, as with `no_precharge`.

**99-0 tools** (Gameplay → Extras, 2026-10-25; ported statically from the
community Gecko codes, not yet tried in Dolphin). The score object
`*(r13-0x1544)` (0xA4 bytes, allocated at `0x8017F620`, set up by
`fn_8017F688`): +0 the inning (from 1), +4 and +0x1E the two teams' arrays
of 13 s16 (the score, then the runs of innings 1-12), +0x38 / +0x52 two more
arrays of the same shape, +0x74 the result, +0x8E the innings, +0x8F the
last inning.

- `always_away` ("Always away team" [LlamaTrauma], 04 `0x802CEAA4`): plain
  patch `clrlwi r0,r3,31` → `li r0,0`, so the match settings' +8 gets 0
  instead of a random bit and the first team always bats first.
- `extra_innings` ("Unlimited extra innings" [LittleCoaks], 04
  `0x8017F694`): plain patch `addi r0,r4,3` → `li r0,99` (the Gecko code
  leaves XX to the user), the last inning stored at +0x8F; stock it is the
  innings + 3, at most 12. Experimental: the arrays hold 12 innings, and
  nothing clamps the inning index, so from the 13th inning on team 0's runs
  land in team 1's score and team 1's in +0x6C onwards (the result at +0x74).
- `score_999` ("999 runs" [LlamaTrauma]): a hook at `0x8032FE14`
  (`cmpwi r27,99`, the scoreboard's score cap, in its loop over the teams:
  r28 the team, r27 its score, r3 its array) plus four plain patches that
  lay out the digits (`0x8032FE30` `cmpwi r27,10` → `cmpwi r27,0`,
  `0x8032FEB4` `blt` → `or r0,r0,r0`, `0x8032FECC` `li r0,4` → `li r0,5`,
  `0x8032FF34` `li r6,5` → `li r6,4`). The stub is the Gecko code's
  function: team 0's box shows (score % 1000) / 10, team 1's box
  (team 0's score % 10) * 10 + min(its score, 9). The Gecko code wrote that
  function over stock code at `0x80489FC8` (a `bl` at the site); the stub
  lives in the DOL sections instead and uses r0/r4/r5 without a stack frame.
  The Gecko code also holds the `always_away` line; the Extras button turns
  `always_away` on with it (and leaves it on when 999 runs is turned off).

**Miscellaneous** (Gameplay → Extras, 2026-10-25; ported statically from the
community Gecko codes, not yet tried in Dolphin):

- `mercy_rule_<runs>` ("Mercy Rule Modifier" [ZPL], 04 `0x8017F6A8`
  `li r0,XX`): `fn_8017F688` sets the score object's +0xA2 to 10 when the
  match settings' +9 (the mercy rule) is on, else to 0, and a match ends
  once a team leads by +0xA2 runs (checks at `0x80136EE4`, `0x8015E474`; 0
  skips them). Plain patch `li r0,10` → `li r0,<runs>`, one option per lead
  1-99 in the group `mercy_rule`; 10 is stock, so it has no option. The
  Extras tab takes the lead in an input field.
- `night_music` ("Day/Night Music Themes" [C^2], C2 `0x803A43F4`): the music
  table `0x8062E608` holds one u32 track per stadium index (match settings
  +5; stock `6, 0xD, 7, 0xE, 0xB, 0xC, 9, 8, 0xA, 0xF`). The Gecko code
  rewrote it at every stadium-select confirm: at night (settings +6 = 1)
  Mario Stadium (0) `0x15`, Wario City (2) `0x18`, Yoshi Park (3) `0x19`,
  Peach Ice Garden (4) `0x16` and DK Jungle (5) `0x17`, by day the stock
  tracks. The port hooks the table's two reads instead (`0x80389098`
  `lwzx r5,r4,r0` and `0x803899C4` `lwzx r5,r5,r0`, r0 = index × 4): at night
  the stub replaces r5 with the night track. The table stays stock, and
  matches that skip the stadium select (the autopicker) get the night music
  too. The stubs use r12 and cr1 (cr0 at the first site holds a compare that
  the next instruction branches on). It adds the DOL sections.
- `challenge_coins` ("Challenge Mode - Press B+2 to get 999 Coins" [Master
  Kirby], 28 `0x81317BB0` / 02 `0x80E55C0A`): the coins are the s16 at
  +0x12E of the challenge struct `*(r13-0xE80)` (made once, at
  `0x802552C4`). A RAM dump of an expanded game (2026-10-25) had the struct
  at `0x80E60C7C`, 0xB1A0 past the address the Gecko code assumes, with 849
  coins. The stub hooks the epilogue of the per-frame controller update
  `fn_8050B5A4` (`0x8050BF44` `addi r11,r1,0xB0`, replayed because the
  restore call after it reads r11). It reads controller 1's held buttons
  like the CPU vs CPU stub (both B `0x0400` and 2 `0x0100` held) and writes
  999 only while the challenge manager `*(r13-0x1224)` exists. It uses r10,
  r12 and cr0 and adds the DOL sections.

Not ported: "Random Star Hits" / "Random Star Pitches" write bytes at stock
heap addresses (`0x900D6A78`, `0x900D6929`, entropy from `0x900DFCFC`)
whose owning objects are not identified yet; a stock-game RAM dump during a
match is needed to find them through a register or an `r13` global.
"Graffiti Runner - Press A+Minus (-) to Mega" [Master Kirby] has the same
problem: it writes a halfword at `0x900D5CF4` (0x0807), `0x900D5D64` (0x6000)
and the odd address `0x900D5D69` (0x0C00), and f32 1.0 / 0.12 at
`0x900D5D58` / `0x900D5D5C`, in an object of the minigame that is not
identified yet. A stock-game MEM1 + MEM2 dump taken during Graffiti Runner
is needed.

**Cheats** (Options tab, Cheats; ported 2026-10-25, none tested in Dolphin):
the [TNTkryzt] codes of Dolphin's code list, which the list marks "may not
work". The three 06/04 codes put a function in the MEM1 tail and branch to
it; their stubs are those functions, instruction for instruction.

- `toy_field_coins` ("Toy Field 9999 Coins", 04 `0x804CA690`): `fn_804CA354`
  clamps a value (r24 +0x20) to 0..r24 +0xA8 and stores the clamped r3; the
  patch `stw r3,0x20(r24)` → `stw r0,0x20(r24)` stores the maximum. Plain
  patch.
- `double_home_runs` ("Homeruns Worth Double", 06 `0x817FED64` / 04
  `0x8017FD94`): at `lha r0,0x6C(r28)` in `fn_8017F924` (the play's runs,
  added to the score) the runs are doubled while the word r28 +0xA94 is 1.
- `reset_outs` ("Reset Out Count B+1", 06 `0x817FED14` / 04 `0x80132230`):
  in `fn_801320D8` (r30 = `*(r13-0x1D38)`, +9 the outs), while the word r28
  +0x34 is 1 and the halfword r30 +0x48 is exactly `0x0600`, the outs are
  set to 0.
- `fast_movement` ("Extremely Fast Movement P1", 06 `0x817FED40` / 04
  `0x8011B630`): before `lfs f31,0xEC(r3)` in `fn_8011B600`, f32 0.46875
  (`0x3EF00000`) is written to r3 +0xEC unless r5 is 0 or the word r5 -0x2A0
  is 1.
- `all_stars` ("All Star Players" and "All MVP's": the list gives the same
  code under both names; 00 `0x80E559A7` = 1, 08 `0x80E559A8` `0x01010101`
  × 10): 41 bytes at +0x197 of the progress object `*(r13-0x300)` (see
  `_docs/SaveData.md`). The stock address follows from the 2026-10-25 RAM
  dump of an expanded game: the progress object sits 0x2CC below the
  challenge struct there (`0x80E609B0`), so stock `0x80E55810`, and its 41
  bytes at +0x197 hold 0s and 1s. Their meaning is not decoded.
- `all_items` ("Unlock All Items", 04 `0x80E55DAC`..`0x80E55DB4` =
  `0x63636363`): twelve bytes at +0x2D0 of the challenge struct
  `*(r13-0xE80)` set to 99 (in the same dump: small counts such as 4, 9, 11).

The Gecko codes wrote the two save data blocks every frame at their stock
heap addresses; the stubs write the same bytes through the `r13` globals
(null-checked), from the end of the per-frame controller update
`fn_8050B5A4`: `0x8050BF9C` `lwz r0,0x154(r1)` and `0x8050BFA0` `mtlr r0`,
after `__restore_gpr`. Its only caller (`0x8045D360`) drops r3, so every
volatile register and cr0 are free there. Since the game saves these blocks,
the values stay after the option is turned off. Every cheat but
`toy_field_coins` adds the DOL sections.

The same applies to roster runs: any run that adds the DOL sections (new IDs,
names of new IDs, grid, wheel order, character voices, ...) breaks those
Gecko codes. Renaming stock characters alone adds no sections (see *Names*).
Seen 2026-10-15: a stock-portrait-only run broke them while its sections held
only the manifest (arena start `0x807C00A0`); an older build of the same edit
without sections kept them working. Since then such runs add no sections (see
*Read-back*); confirmed in Dolphin the same day: a stock-portrait patch keeps
the Gecko codes working. Since 2026-10-16 runs that need no DOL code but do
record something (adopted stats or square voices of stock characters, a
`wheels` key without new rows) store their manifest as an unloaded trailer
of `main.dol` instead of creating the sections (see *Read-back*).

**Compatibility check** (`SluggiesTools/gecko_compat.py`, 2026-10-16). A
`main.dol` counts as Gecko compatible while it has no DOL hammerspace
sections and all four `arena_lo` values are stock (`0x807B6E80` /
`0x807B4E80`). Only two writers add the sections, and both check before they
write: the roster runner (`Roster/runner.py`) and the game options
(`GameOptions/runner.py`). Each compares the file before and after the change
in memory. When the change breaks compatibility (compatible now, not after),
the runner asks first (`slogger.ask`, kind `danger`). Cancel writes nothing.
A game that is already incompatible is not asked about again. Planned chains
(`--apply-slots`, `--roster --config`, `--load-roster`) write stat edits
before their roster run. So the dispatcher builds each planned roster or
option command in memory first and asks once, before the first command
(`gecko_compat.guard_chain`). Their dry runs record a break in
`_gui/gecko_check.json`; the GUI's summary dialogs then show the warning in
red with a red Confirm, and pass `--accept-gecko-break` on. The grid's
staging check (`slot_cli --apply FILE --dry-run`, `slot_cli.gecko_check`) also
records `new_breaks`: whether the edits not yet staged break compatibility on
their own. The planner plans them alone into `_gui/slot_gecko_probe/` and
builds that roster in memory (about 2.2 s per staging check on the real files
instead of 1.6 s, 2026-10-16, and only while the output is compatible and the
batch breaks it). The GUI keeps the breaking slots for the session
(`PendingEdits.gecko`) and marks them red. A slot is marked when its edit
breaks compatibility on its own, or completes a break that no marked slot
causes.

Changes that break compatibility on a stock output:

- roster runs that need DOL code: new IDs, names of new IDs, `grid`,
  `wheel_order`, wheels of 8+ members, portraits of new IDs, own model
  directories, `character_voices` (voice packs copied into a slot's own
  voice);
- the config keys `ids`, `grid` or `wheel_order`, even empty (an `ids` key
  moves the per-ID tables into the sections);
- every game option (CPU vs CPU, CPU management, `player_heap_*`);
- roster presets and packs that contain any of the above.

Model patches, defragmenting, stat edits, stock renames, stock portraits,
sound effects, music and volumes only patch words in place and keep
compatibility. So do roster runs that need no DOL code, since 2026-10-16:
`stock_stats` (the grid's "Adopt stats..." on a stock slot: its rows are
rewritten in place), `stock_voices`, `species_clips` (sound archive only) and
a `wheels` key without new rows. Before that they broke it only because their
manifest created the sections (checked 2026-10-16 by dry runs on the real
`main.dol`; `species_clips` from the code only). Confirmed in Dolphin
2026-10-16: Blue Noki with Mario's adopted stats, no Gecko warning, the game
boots with the manifest trailer, Blue Noki plays with Mario's stats and the
"CPU vs CPU" Gecko code still works. The All-In-One Export (GUI and menu [1]) no longer turns on
CPU vs CPU: before this change, every full export broke the Gecko codes.

## DAT hammerspace

Grown files are copied past the stock end of `dt_na.dat` (715,046,144 bytes)
into the first 32-aligned zero run that no DOL record uses; the file grows
when nothing fits, and `fst.bin` gets the new size (offset `0x14`). A DOL
directory record is 48 bytes: three language slots of (length, offset,
allocation). **The slots are English, French, Spanish** (slot 2 holds
"Toad rouge", slot 3 "Toad rojo" in the name table).

Every roster copy is DOL-routed, so `start.py --defrag-hammerspace` (GUI:
Maintenance, Defragment) moves it with the model blocks and repoints its
records; see "Defragmenting hammerspace" in
`_docs/_docs_model_format/dol_routing.html`.

Files the expansion moves: the select-screen layout (dir 119 file 19 = dir 0
file 1591, record `0x806920B8`, three copies of `0x138E50` bytes), the icon
bank (dir 0 file 1574, record `0x68DE88` in file offsets) and the name table
(dir 121 file 5). Dir 119 files stay resident in the MEM2 game heap during a
match, so their growth counts against the match's memory.

## Colour wheels

**Selector rows** (`0x80631550`, 8 bytes per ID). A wheel is all selectable
rows of one species. Which code reads each byte:

| Byte | Meaning | Readers |
|---|---|---|
| 0 | wheel group; 0 = no wheel | 13, almost all zero tests ("has a wheel"); the base character's row needs it non-zero too |
| 1 | host character ID | 1 (`0x80234010`, never hit): it picks neither square, wheel nor voice |
| 2 | species `0x00`–`0x28` (`0x29`/`0x2A` Mii groups) | 84: the main key (wheel membership, voice) |
| 3 | captain flag | none |
| 4 | flags, 0 on every stock row | 4 zero tests |
| 5 | slot in the wheel | none: the order is character-ID order within the species |
| 6 | **selectable** | the roster builder |
| 7 | swatch colour | 3 |

Stock wheel groups (base ID, selectable/total members): `01` Koopa `0x0C`
(2/3), `02` Toad `0x0D` (5/6), `03` `0x10` (5/5), `04` Paratroopa `0x14`
(2/2), `05` Pianta `0x15` (3/4), `06` `0x18` (3/3), `07` `0x1B` (3/3), `08`
`0x21` (4/4), `09` `0x30` (4/4), `0A` Kritter `0x3A` (4/5), `0B` Yoshi `0x06`
(6/8). A character without a wheel gets the next free group, `0x0E` and up.
`0x80631878` (right after the table) maps species to its base character ID.

**A spare row** (`0x47`–`0x4C`) joins a wheel with byte 6 = 1, bytes 0 and 2
set to the host's, byte 7 the swatch, and its own-data byte set to 1.

The roster builder `0x8006BA6C` scans IDs below `0x4D` into the roster struct
(embedded in both select-screen objects, `0x210` bytes): availability per ID
at `X + 0x00` (`0x4D` bytes, from byte 6), count at `X + 0x4D + species`, list
at `X + 0x76 + species × 10` (10 per species). New IDs are appended by a hook
at its end. The wheel-list builders treat the Mii range as always available;
raising its upper bound `0x64` to `0xFF` (`0x804302CC`, `0x804304C0`,
`0x80071B90`) makes new IDs available the same way.

**Up to 10 members** (*Dolphin*): the member-list builders return the full
count but store at most 6 IDs, so a 7th member reads a stale stack word and
crashes (*Dolphin* 2026-09-25). There are two copies: `0x80071BB0` (exhibition,
cap `0x80071ECC`) and `0x80430184` (Toy Field, cap `0x804303CC`). Raising both
caps to 7 is enough for 7 members (*Dolphin*, random select included). For
10, the caps (`cmpwi …,6`) become 10; 19 functions' stack buffers that hold a
member list grow by `0x10`; four `stb r27,off(r4)` sites map member index 6+
to popup node 9+; popup element `0xB3` of the select layout gets frames for
7–10 members (its info block's last time becomes 8). Seven members only need
the caps.

**Swatches**: elements `0xAD`/`0xAE` have one key per swatch colour (time =
selector byte 7, four vertex colours at `+0x40`). The colours: 0 red, 1 blue,
2 yellow, 3 green, 4 purple, 5 black, 6 brown, 7 light blue, 8 pink, 9 white.
Time 10 is an unused white key, recoloured orange for swatch 10.

**Wheel order**: a hook after the roster hook rewrites a species list into a
configured order.

## Icons

**Icon bank** (dir 0 file 1574): a texture section, then a container.
Header `+0` `0x20`, `+4` container offset (end of the textures), `+0x20`
texture count; descriptors (0x20 bytes) from `+0x24`.

- **Descriptor image and palette offsets count from bank `+0x20`**, not from
  the file start: the stock offsets start at `0x1260` (file `0x1280`), the
  first 32-aligned byte after the descriptor table.
- The container's descriptor (container `+0x14`) holds signed pointers,
  relative to itself, to the resource table (`+0x04`) and to the three
  source tables normal_a (`+0x08`), side (`+0x0C`) and front (`+0x10`).
  Container `+0x10` is its end.
- **Source tables**: header `0x28` bytes (`+0x08` length, `+0x18` last frame,
  `+0x24` count, `+0x26` stride `0x50`), records `0x50` bytes: `+0x00` flags
  (`0x0014` on the first record, `0x0114` on the others), `+0x02` character
  ID (the key frame), `+0x04` `0x0400`, `+0x06` resource row, then the
  record's own data. Records are sorted by ID, highest first; a wrong order or
  a wrong first-record flag makes resolved icons disappear. A view shows the record with the highest
  ID ≤ the character, so an ID without its own record shows a neighbour's
  icon (*Dolphin*: the unused characters showed Pink Yoshi `0x46`). Each
  table's last frame must cover the highest key. Side records differ only in
  byte `+0x26` (`0x02` for IDs 0x01, 0x08, 0x0B, 0x27, 0x3E–0x41, else `0x82`):
  its `0x80` bit is the mirror flag (see *The alternate side view* below).
- **Resource table**: u32 row count, u32 length (8 + count × `0x14`), then
  the rows (`0x14` bytes): u16 page, u16 0, f32 v1, u1, v2, u2. The stock bank
  has 152 rows; 8 bytes follow the table at the end of the bank.
- **Icon record** (DOL file `0x68DE88`, 48 bytes): three language slots of
  (file name pointer, length, offset, allocation); all three point at the
  same bank.
- **Keys alone are enough** to give an ID its own icon (*Dolphin*); no
  runtime hook is needed.
- Our bank: the stock texture section, then page `0x86`'s palette, then two
  CMPR pages (`0x92` side, `0x93` front) holding each distinct 48×51
  portrait once in a 52×52 cell (the smallest power-of-two page that fits),
  then the stock container with the rows appended. The source tables follow
  the pages; page images and source tables must stay inside the texture
  section (the external tool found that images placed after the container
  draw garbage). The six unused characters' icons cost 51,728 bytes over stock.
- **The stock atlases stay byte-identical.** The two stock C8 atlases (bank
  images `0x13660` and `0x53660`) are what Dolphin custom textures are keyed
  on: `tex1_1024x256_a02bc526e5fe683e_<palette>_9` and
  `tex1_1024x256_20b4a21f84f059dc_<palette>_9` (XXH64 of the 256 KB image;
  the side portraits sit on the `20b4a21f` sheet). Until 2026-10-17 the
  source tables were written into the free rows 204–227 of the second atlas
  (bank file `0x87520`), which changed its hash with every icon change and
  stopped every side-portrait override from loading.
- **Stock characters' portraits** (`stock_icons`, IDs `0x00`–`0x46`, which
  own keys in all three tables) are replaced by pointing their existing
  records at packed rows; no key is added and the records' own data stays.
  normal_a goes to the side row unless the entry has an alternate side view
  (below). (New IDs' normal_a keys show the front row, as in the external
  tool, unless they have an alternate side view.)
- **The alternate side view** (2026-10-20). The first source table
  (normal_a) is the side portrait facing the other way. For 63 of the 71
  stock characters its record names the same row as the side record, and the
  two records differ only in byte `+0x26`: `0x02` in normal_a, `0x82` in
  side. The `0x80` bit is a horizontal mirror flag (inferred from this
  pattern; not yet traced in the renderer), so one cell draws both facings.
  The eight characters whose art is asymmetric (Luigi 0x01, Baby Luigi 0x08,
  Waluigi 0x0B, Dixie Kong 0x27, King K. Rool 0x3E, Baby Peach 0x3F, Baby
  Daisy 0x40, Baby DK 0x41) have a second hand-drawn cell on the side atlas
  (rows 1/2, 9/10, 13/14, 42/43, 66/67, 68/69, 70/71, 72/73: normal_a row
  first) and `0x02` in both records; their emblems (the "L", the "Γ", the
  pacifier, the hair parting) read correctly in both facings. The icons step
  takes `side_alt` in an `icon` object (a model folder's `SideIconAlt.png`;
  the icon export writes it for characters whose normal_a row is a cell of
  its own): the cell goes on the side page after the side cells, its row
  after the side and front rows, the normal_a key points at it (added for a
  new ID or spare row, re-pointed for a stock character) and the mirror bit
  of the side record is cleared, so both PNGs are drawn as given. Which
  screen draws normal_a is still unconfirmed in Dolphin.
- **Stock portrait pages are C8.** All 71 stock characters' portraits (both
  views) sit on C8 pages (format 9), at 48×51 and not on 4-texel boundaries.
  Rebuilding one as our own cell re-encodes it to CMPR once: on Red Toad's side
  view the visible pixels moved by 5.5/255 on average (CMPR colour drift plus
  the hardened soft edges; the stock art has 8 alpha levels), not visible at
  a glance.
- **Decoding** (`Icons/gx_decode.py`, numpy, pinned to wimgt output): wimgt
  expands an n-bit channel as `round(v × 255 / max)`, not by bit replication
  (bit replication is off by one for a few values). In an IA8 palette entry
  the first byte is alpha, the second intensity (the Mii icon decodes
  correctly only this way).
- **CMPR re-encoding is not stable.** wimgt's CMPR decode → encode changes
  blocks (real art: 13 of 32,768) and the pixels drift, so a rebuild keeps an
  unchanged portrait's encoded 4×4 blocks (`icons.keep_blocks`) instead of
  encoding it again.
- **User images** become 48×51 RGBA before encoding (`Roster/icon_import.py`):
  trimmed to the alpha ≥ 128 box, fitted (`contain`, `cover` or `strict`),
  and the alpha hardened to on/off for CMPR's 1-bit alpha (≥ 128 opaque).
- New descriptors for pages `0x92`/`0x93` occupy file `0x1264`–`0x12A4`,
  which covers the first 36 bytes of page `0x86`'s palette at `0x1280`, so
  that palette moves.

**Stock rows 149–151** (the last three of 152) sit on palette pages `0x8F`,
`0x90` and `0x91` of the front sheet: 149 Pink Yoshi, 150 "?", 151 the Mii
icon (IA8, greyscale). The front table keys `0x46` to row 149 and `0x4D` to
row 150; no record uses row 151.

**Portrait renderer** `0x80395DB0`, three paths:

- player (`0 ≤ id < 0x4D`): the tracks are evaluated with key `id << 16`;
- Mii (`0x4D`–`0x64`): row `0x97` (151) is written into the runtime row slot
  `0x8071FF78` (`li r0,0x97` at `0x80395EBC`), then the slot table is reset;
- invalid (below 0 or above `0x64`): key `0x4D`, the "?" icon.

A slot value below 0 falls back to the resource of the keyframe the track
evaluation lands on (`obj+0xB8`), which is how an ID without its own key gets
its neighbour's icon. IDs ≥ `0x4D` take the Mii path at
`0x80395E1C`. A branch there sends IDs ≥ `0x66` down the normal track path;
a new ID without its own art is aliased to its template at the renderer entry
and its two preview calls.

## Exhibition draft grid

Only the exhibition team draft (layout element `0xBA`) grows. Toy Field,
minigames and Free practice use a second grid (element `0x97`, 41 squares)
that is not expanded, but both screens share the grid widget code, so both
screen objects grow.

| | Screen A: exhibition team draft | Screen B: colour-wheel select |
|---|---|---|
| used by (*Dolphin* 2026-09-24) | exhibition | Toy Field and Minigames (`0x80435E08`), Free practice (`0x804EA464`); `0x8038DF98` never ran |
| object ctor, heap size | `0x8006E9C4`, `0x67C` (allocated at `0x802C8CFC`) | `0x8042C424`, `0x6CC` |
| grid widget | `obj+0x338`, mode 0 | `obj+0x2A0`, mode 1 |
| element, squares | `0xBA`, 40 (10×4) | `0x97`, 41 (12 wide) |
| square order | `0x80623458` (40), copied to `obj+0x238` | `0x80630930` (41, the 12 captains first) |

The grid widget class: ctor `0x80067C00`, builder `0x80067EF4(grid, layout,
slot, mode)`, hit test `0x8006887C`, vtable `0x8063A960`. Both screen objects
live in MEM2, so growing them costs no MEM1. Square positions exist only as
layout tracks; there is no spacing constant in code. Screen A's cursor
positions: 0–3 specials, 4–`0x15` roster slots, `0x16`–`0x3D` the 40 grid
squares, `0x3E`–`0x47` the Mii rows, then buttons (so the `0x3E`–`0x47` range
tests in screen-A code are cursor ranges, not Yoshi IDs). The 12 captains are
listed at `0x806318A8`: `00 01 02 03 04 05 06 09 0A 0B 11 13`.

**Stock layout:** 40 squares (10×4) for 41 heads. The square → head map
`0x80623458` holds head indices; head *h* is species *h*, and the head list
`0x80631878` gives each head's character. The constructor `0x8006E9C4` hands
the square nearest column 5 of one captain's family to Luigi (head 1), unless
the captain is Luigi or Yoshi. A captain with a wheel therefore lost its
whole square (*Dolphin* 2026-10-03: with Mario as a captain, Mario's square
went to Luigi and his new IDs were unreachable). Yoshi is exempt because his
wheel would vanish too.

**Shapes:** 11×4, 12×4, 10×5, 11×5, 12×5 (at most 60; 12 columns reach the
screen edge, 5 rows the team bars). What changes:

- layout: element `0xBA` rebuilt (49 px pitch, right edge at stock column 9;
  5 rows at y = 105 + 48·row), team bars `0x9A`/`0x9B` stretched, roster slots
  `0xB9`/`0xB8` spread, banner `0xA0` moved, the screen shifted 20/40 px right;
- square counts in `0x80067EF4`, the widget's per-square arrays moved behind
  both screen objects (`0x67C` → `0xC30`, `0x6CC` → `0xB98` bytes);
- the map and per-head arrays moved into the object, filled from one table;
  **the captain swap removed** (Luigi has his own square; *Dolphin*: a
  captain Mario's other Marios stay draftable);
- heads 43+ for new squares (head list moved and grown); the member-list
  builder `0x80071BB0` lists a square's own members, and square members
  leave their family's wheel;
- 191 cursor-position constants, the D-pad's divide (`0x2E8BA2E9` for 11
  columns, `0x2AAAAAAB` for 12), bounds and wraps, the random-team pools;
- a picked square gets "decided" flag 2 (stock: 1 forever), so it shows the
  next member or goes dark;
- empty cells are moved off screen and skipped by the pointer and the D-pad.

A new ID that is only on a square has wheel group 0 and is kept off the
roster's species lists.

## 2D layout banks

The select screens, the icon bank and 75 other files in dir 0 are 2D layout
banks (`SluggiesTools/Icons/layout2d.py` reads and writes them; all 223 banks
the DOL routes to round-trip byte for byte):

- header `0x20` bytes: `+0x04` container offset, `+0x20` texture count, then
  `0x20`-byte texture descriptors and the texture data;
- container: screen size `640×448`, descriptor offset `0x14`, resource-section
  offset and the container endpoint (= the end of the resource section). The
  descriptor is an element count followed by signed, descriptor-relative
  pointers; **an element's index is its position in that list**, and elements
  are packed in index order;
- element: u16 flags, u32 sub-block count, u32 length, sub-block offsets.
  Sub-block 0 is a `0x10`-byte info block, the others are tracks, one per
  sprite;
- track: u16 record count, u16 size of the first record, then keyframe
  records: byte 0 continuation flag, byte 1 size in words, u16 key (the
  frame; the character ID in the icon source tables), u16 kind, u16 resource
  row. `0x3C`-byte sprite records hold x/y (s16) at `+0x08`, RGBA at `+0x2C`
  and x/y scale (f32) at `+0x34`;
- resource section: count, length, then `0x14`-byte rows (texture page and
  four UV floats).

Growing an element shifts every later element and the resource section, so
their pointers, the resource offset and the endpoint are rewritten; the
texture data before the container does not move. The select layout (dir 119
file 19) holds 204 elements and 191 textures; its three language copies have
identical elements and differ only in text textures. Elements `0x65` and
`0x5C` (two more 41-square grids) were never drawn in any visited menu.

## Names

**Text** (dir 121 file 5, one table per language): `u8 1, u8 1, u16 count,
u32 offset[count]` (in UTF-16 units from the text start), then the UTF-16
strings. Message *i* is character *i*'s name; 71–76 are "#N/A", 77–100
"Mii", and 101–103 are formatting codes. New IDs `0x66`/`0x67` would land on
codes 102/103, so the table runs to `0xFE` and the codes move to 255/256;
their readers `0x80486E68`/`0x80486E88` (`lwz r0,0x198(r3)`) and
`0x8047C4C0`/`0x8047C4D8` (`lwz r5,0x19c(r5)`) are repointed. The name widgets
`0x80486EAC`, `0x80489F94` and `0x804923C0` show a name only for IDs below
`0x4D`; their test also passes IDs ≥ `0x66`.

**Name plates** (select screen, batting order and in-match bubbles): images,
resource row `id + 0x149` of the select layout (115×16, page 124). Four sites
compute that row (`0x8006E660`, `0x8007F4FC`, `0x8042BBD8`, `0x8031DC1C`); no
other layout bank has plates at row `0x149`. For new IDs they use rows after
the stock 483, on a new RGB5A3 page per language; adding a page pushes the
image data down 0x20 bytes, so every stock offset moves with it.

*Dolphin*: names show on the draft, the batting order and in the match, in
English, French and Spanish.

**Stock names** (`stock_names`, *Dolphin* 2026-10-05) replace a stock ID's
text in all three tables and redraw its stock plate row `id + 0x149`; spare
rows and new IDs keep their names on their `wheels`/`ids` entries. A user
name must fit the plate without shrinking: no edge spaces or control
characters, and at most 113 px wide at the stock size (font size 14,
weight 800; the 115 px plate minus the 2 px margin the plate drawing shrinks
by), `names.fit_problem`. Plates are drawn again on every rebuild, and the
built exe (bundled Pillow/FreeType) draws a few pixels differently from a
development Python (7 bytes in one Spanish plate); each is stable by itself,
so byte-for-byte comparisons need both builds made by the same one.

**Without new IDs the names are pure DAT edits** (since 2026-10-16). Renamed
stock characters and spare rows are all below `0x4D`, which the stock name
widgets already show. So the tables keep their 104 messages (the codes stay
at 102/103), the plate page holds only the given names, and only their own
rows are repointed. The table readers, widget tests and plate sites stay
stock, so `main.dol` gets no DOL hammerspace and the MEM1 arena stays where
stock Gecko codes expect it. No manifest records them: the read-back takes a
plate row off page 124 as a rename (`names.renamed_ids`). Untested in
Dolphin.

## Models in slots

A `.sluggie` can be patched into another character's slot
(`Hammerspace/SlotTarget.py`, `--target-id`). The block is still built from
its own donor (`ChunkNumber`, `FileIndex`, `1_Input`); only the route it is
written to changes. *Dolphin* (2026-10-04/05): Toad's High and Low models in
Blue Toad's slot, and models in new IDs' own directories, show on the select
screen and on the field; the source character stays unchanged.

- **Always Hammerspace.** An in-place patch writes over the source's own
  block, so in-place edits are promoted to a Hammerspace build. Facial pose
  edits (shape keys) go through: every Hammerspace build splices the edited
  pose arrays into the ptr7 section (`facial_pose_edits.py`, 2026-10-15).
  SK vertex-count changes exist only in the in-place patcher and are
  refused. The slot editor sends such files only to their own stock slot,
  as a plain in-place `--patch` (the slot first gets its vanilla models back
  when both files are patched); any other slot refuses them at planning
  time (`slot_plan.inplace_only_edits`, 2026-10-15).
- **Skeleton.** A stock slot takes only a model whose vanilla skeleton has the
  slot's bone count and parent chain (different rest SRTs only warn); Yoshi
  (92 bones) or Bowser (105) into a Toad slot (89) is refused. A finished
  block (roster packs, equipment) may add bones: its skeleton must start with
  the slot's vanilla skeleton.
- **High and Low.** The Low model may draw only on bones its High model has,
  and binds textures by index into the High model's TEX
  ([`act_section.html#lod-pairs`](../_docs_model_format/act_section.html#lod-pairs)). A Low model alone goes only under the slot's
  current High model when that is its own partner; a new ID takes a character
  as a whole and refuses a lone Low model. A High model without a Low partner
  is written to file 1 as well, as its own copy (a shared block would make a
  later patch of file 1 repoint file 0 too), and is loaded twice on the field.
- **Sharing.** In vanilla data only the unused characters share High/Low
  blocks with other routes (split by the untangler). Bats and gloves are
  shared widely: one bat block serves up to 13 routes. A slot patch moves
  only the target's route; the vanilla block is zeroed only when no other
  live route reads it.
- **Equipment** (files 2–5, `Roster/gear.py`): a bat fits file 2 or 5, a
  glove only its own hand's file. Equipment blocks are one-bone models inside
  the 32-byte archive container, which is kept; an archive with several
  members is refused. File 5's survey and its open question are in
  [`dol_routing.html`](../_docs_model_format/dol_routing.html).
- **What counts as vanilla.** An untangle export rewrites texture bytes of
  stock blocks in place (after one, 46 stock characters' blocks differ from
  `1_Input`), so for stock directories "vanilla" means the route still points
  at the input DOL's own entry, not equal bytes. An own directory's file is
  vanilla when it equals its source's block.
- **Copy / paste** (`slot_plan.plan_copy`, edit op `copy`, `start.py
  --copy-slot`): the target becomes a clone of the source as the game holds
  it. The planner snapshots the source's current blocks, shown portraits
  and live stat values into `_gui/slot/copies/` before
  anything is written, so pending edits never leak in. The blocks go in
  with `--write-slot-blocks` / `--write-slot-equipment`. A new ID gets an
  own directory from the source's model source. Any file whose bytes differ
  from `1_Input` goes on top, so a clone also keeps the source's untangled
  texture bytes, and with them its Dolphin texture hashes. A gear file still
  on its vanilla route is never unpatched, because that would rewrite a
  route other slots may share. The stat values are written by a
  `--apply-stat-edits copy:<snapshot>` step after the roster rebuild,
  because only then do the target's rows follow the copied stats source.
  The step writes every field, and the chemistry in both directions; the
  target's pair with the source stays, and the source's self pair becomes
  the target's. Stock portraits are C8: a pasted copy is CMPR, about
  4.5-8.5/255 mean drift on the visible pixels (Bowser, Red Toad). The
  real-file chain was checked on 2026-10-06; not yet tested in Dolphin.
- **Validator exceptions.** A finished block is judged only by errors its
  slot's vanilla block does not have too. The two vanilla quirks that made
  this necessary (Birdo's wider memClr range; the zero CLUT count of Donkey
  Kong and Baby DK) only warn since 2026-10-15, and every vanilla High and
  Low block of directories 18-118 now passes.

## Read-back: manifest, derive and packs

The roster is rebuilt from scratch on every run, and the game files are the
only record of it. To edit a built roster, the tools read it back, turn it
into a config that rebuilds it, change that config and rebuild once.

**Manifest.** Facts that exist only inside hook code (new squares' members,
wheel order, new IDs' templates, `portrait_of`, stats sources, square and
stock voices, own directories, which top-level config keys were given) are
stored at the end of every roster run in the DOL data section:
`SLUGGIES ROSTER\x03`, u32 compressed length, zlib JSON
(`Roster/manifest.py`; preset 04's: 42,686 bytes raw, 1,634 compressed). A
run that records nothing and writes no DOL code or tables (stock portraits
only, since 2026-10-15) stores no manifest and adds no DOL sections, so the
arena start stays stock; derive reads such an output from the DAT alone. A
run that records something but writes no DOL code or tables (e.g. stock
characters' adopted stats or square voices, since 2026-10-16) stores the same
blob as the file's **trailer**: appended after the last byte of `main.dol`,
outside every section, so the game never loads it and the arena start stays
stock. The reader looks for it (the magic outside every section) when there
is no data section; game option sections added later go after it; the reset
cuts it off with the sections (`manifest.put_trailer` / `find_trailer` /
`strip_trailer`). The
reader (`Roster/state.py`) cross-checks everything it can also read from the
binary (shape, map, heads, names) and the binary wins on a mismatch. A
roster DOL without a manifest (built by an older version) is refused. On the
stock grid Luigi's family has no square; the reader lists it as an extra
square on no cell. Portraits are resolved as the renderer does
(`Roster/state_icons.py`: `portrait_of`, the highest key ≤ ID, the Mii and
"?" paths) and recorded as own, neighbour, template, Mii or invalid.

**Derive → rebuild** (`Roster/derive.py`): the derived config holds own
directories as their existing DAT routes and portraits as the bank's own
48×51 cells plus their CMPR blocks. Unchanged, derive → rebuild gives
byte-identical `main.dol` and `dt_na.dat` (presets 01–04). The first rebuild
after a model patch is the exception: the roster's own DAT blocks (for
example the name table) land in other free space, because the patch took
some. The content is the same, and the next round trip is byte-identical
again.

**Roster packs** (`.sluggiesroster`, `Roster/pack.py`): a zip of `pack.json`
(format 3; format 1 has no equipment and loads as "equipment unchanged",
format 2 no voice clips and loads as "no custom clips"),
`state.json` (the derived config, own directories by source only),
`icons/` (the derived portraits; their names are kept because two entries
can share one cell), `clips/<sha1>.rwav` (the custom voice clips, by content),
`models/0xNN_hp.bin`, `_l.bin`, `_bat.bin` … (the
finished blocks that differ from the slot's vanilla start) and
`fingerprints.json` (per slot: block SHA-1s, own-directory source, portrait
pixel SHA-1s, name, stats source, square voice, own voice, own and species
clip SHA-1s, and head). After an untangle
export a pack of the whole output holds about 62 blocks, 7.2 MB. Loading
checks every block before anything is written, keeps an own directory whose
source and blocks already match, and otherwise copies the directory afresh
from `1_Input` and writes the pack's blocks into it.

**Switching presets** (`start.py --roster --config FILE`, `Roster/migrate.py`,
since 2026-10-06): a roster run on its own resets the roster to vanilla
first, which keeps only the stock directory records (models patched into
stock slots) and the stat edits. The switch instead derives the current
roster, merges it into the preset by character ID and rebuilds once from
the merged config (`--roster --state`):

- IDs on both grids keep what they hold; where they sit (template, wheel,
  swatch, square, wheel order) comes from the preset. A new ID keeps its own
  model directory as its existing DAT routes (not when it is only an
  unchanged copy of its old template's files), its stats source, and its
  name and portraits unless they are the open-slot ones; a portrait `like`
  that was the old template is dropped. Spare rows keep name and portraits.
  The game's `stock_names`, `stock_icons`, `stock_stats`, `stock_voices`,
  `character_voices` and `species_clips` entries replace the preset's for the
  same ID (custom clips travel as the derived `clips/` files); a new square headed by a
  kept new ID takes that ID's old square voice. A new ID's directory holds
  all of its source's files (models, equipment, animations), so a different
  template does not change what poses its models, and stock IDs never change
  directory: no block moves, and none is checked again.
- IDs only on the old grid lose everything. New IDs leave the config (the
  reset frees their directories, the stat carry drops their edits). Spare
  rows 0x47-0x4C first get their stat edits cleared (`--apply-stat-edits
  reset:0xNN`) and their models and equipment unpatched where they are not at
  their baseline. A split copy (dirs 89-94) is at its baseline when it is the
  route's own copy and its bytes equal the vanilla block with the exported
  `.sluggie`'s untangled textures replayed
  (`UntangledTextures.split_at_baseline`); on the live output all 36 split
  files compared equal, 10-50 ms each.
- IDs only in the preset get the preset's entry (the open slot).

Who is on a grid follows from the config alone: 0x00-0x46 always, spare rows
listed in `wheels`, new IDs in `ids` (`migrate.on_grid`; equal to the read
state of presets 01 and 03). The preset's own portrait files are copied
beside the derived cells as `preset_<name>`. A merged config without roster
keys rebuilds as `--roster --remove --keep-stat-edits`. Switching a preset 03
output to preset 03 again gave a byte-identical `main.dol`. `--fresh` gives
the plain run. `--roster --remove` (reset to vanilla) also clears every stat
edit since 2026-10-06; `--keep-stat-edits` keeps them (a stock roster pack's
load uses it).

## Game memory

Dir 119 files stay resident in the MEM2 game heap (`0x900AE64C`–`0x930AE5FC`)
during a match, so their growth reduces the match's headroom (the external
tool's crash dumps ran out at a scored run or at the VS screen capture). Costs
measured on the shipped presets: the select layout grows by about 22.7 KB
(grid, 10-member popup and name plates together; the popup alone 2,904
bytes, a 12×5 grid 6,288 bytes); the packed icon bank grows by 33,600 bytes
for 8 portraits and stays under 600 KB for 159.

The rest of this section comes from Dolphin RAM dumps (MEM1 + MEM2, the
Debug UI's Memory panel, *Dolphin* 2026-10-10) taken at the first pitch of
an exhibition match. The scene was Mario Stadium and Wario City, both teams
CPU. The heaps are Nintendo `MEM` heaps. An expanded heap starts with
`EXPH`, its start/end at `+0x18`/`+0x1C`, its free list at `+0x3C` and its
used list at `+0x44`. Each block has a 16-byte header: `UD` (used) or `FR`
(free), then the size at `+4` and prev/next at `+8`/`+0xC`.

### Heap map

The game wraps every heap in a C++ object: vtable `0x806DF220`, made by
`0x804FB7C0`. The size is at `+0x14`, the start at `+0x18` and the `MEM`
handle at `+0x48`. Its initialise method (`0x804FB9F0`) calls
`MEMCreateExpHeapEx` (`0x805C2170`). All sizes are fixed when the game
boots:

| heap | where | size | source of the size |
|---|---|---|---|
| MEM1 heap | `0x8080008C` | 15 MB (`0xF00000`) | see *DOL hammerspace* (its end is the hammerspace ceiling) |
| MEM1 heap | `0x8080026C` | 10 MB | heap table entry 2 |
| MEM1 heap | `0x812002F8` | about 5 MB, the rest of the 15 MB heap | holds table entry 0 (`0x120100`) |
| MEM2 root heap | `0x9000086C` | 51 MB (`0x3300000`) | `fn_8039A85C`, just `lis r3,0x330; blr` (a getter called through a vtable) |
| MEM2 game heap ("heap 3") | `0x900AE5FC` | 48 MB (`0x3000000`), inside the root heap | heap table entry 3 |

The **heap table** is at `0x8062F048`: four entries of `{u32 size, u8
parent, u8 flag}`, holding `0x120100`, `0x20000`, `0xA00000` and
`0x3000000`. `fn_803A5840` makes the four heaps, and reads each size with
`lwz r4,0(r28)` at `0x803A58E8`.

**Dolphin's "Emulated Memory Size Override"** (MEM1 64 MB, MEM2 128 MB)
shows up in the OS globals: MEM2 size `0x8000311C` = `0x08000000` (stock
`0x04000000`) and MEM2 arena high `0x97FC0000` (stock `0x935E0000`). The
game ignores the extra memory: every heap keeps its stock address and size,
and the memory above them stays empty. With the override, MEM2 from
`0x9330086C` up to the IOS area at `0x97FC0000` is free.

In the stock-size dumps, MEM2 from the end of the root heap (`0x9330086C`)
to the arena top (`0x935E0000`) was zero apart from one 4 KB page near the
top: about 2.9 MB. So was MEM1 `0x81700090`–`0x817FF480`, about 1 MB. No
heap covers either range. Whether anything claims them at other moments
(menus, saving, a run scored) is not known, nor what owns the page at
`0x935DF86C`. If the MEM2 tail stays unused, growing the root heap and the
game heap into it (2,951,060 bytes up to `0x935DF000`) would allow about
+220 KB more per player, on a real Wii too.

### Player heaps

Every player on the field gets **its own expanded heap of 870,400 bytes**
(`0xD4800`, 870,320 usable). There are 13 of them, created from the game
heap back to back: batter, 3 runners and 9 fielders. At the first pitch, 10
were in use. `fn_80364880` makes them in a loop that calls `0x803762A0`
with `r5` = the size, from `lis r14,0xD` at `0x80365180` and `addi
r5,r14,0x4800` at `0x8036518C`.

Each one holds **that player's High model (file 0), Low model (file 1) and
one gear file**, plus 8 tiny blocks (132 bytes). Gear is the bat (file 2)
for the batter and a glove for a fielder. With the block headers that
gives:

```
High + Low + gear file + about 400 bytes  <=  870,320
```

**A player whose files don't fit crashes the game when it is loaded onto
the field.** It doesn't depend on the stadium or the other players.
Character select loads models elsewhere and never crashes. Batting carries
the smaller bat, so an oversized character may only crash once it has to
field. Evidence (*Dolphin* 2026-10-10), Mario with an unbound CMPR texture
appended to his High model, Low vanilla (77,984 bytes), glove 26,944
bytes:

| player heap | Mario High | High + Low + glove | result |
|---|---|---|---|
| 870,400 (stock) | 761,472 | 866,400 | good 8 of 8 |
| 870,400 (stock) | 777,856 | 882,784 | crash 7 of 7 |
| 903,168 (+32 KB) | 777,856 | 882,784 | good 4 of 4 |
| 903,168 (+32 KB) | 810,624 | 915,552 | crash |
| 919,552 (+48 KB) | 810,624 | 915,552 | good 3 runs + a full match |
| 919,552 (+48 KB) | about 843,400 | about 948,300 | crash |

Mesh and texture bytes count alike. The edge is sharp: repeated runs at the
last good and first crashing build (5 each, same scene) gave no mixed
results. In September, crash rates near the edge looked random (one build
worked 4 of 7 times). Which team bats first is random, so those runs most
likely crashed only when the character had to field.

The crash is an overflow of that one player heap: the allocation for the
player's file fails and the game uses the result unchecked. The game heap
itself still had about 4.8 MB free in the same scene. The player heaps are
created after the stadium heap, so their address moves with the stadium
(`0x91FEB648` with Mario Stadium, `0x9206A248` with Wario City).

### Stadium heap

The stadium model (file 0 of the stadium's directory) gets **its own
expanded heap, sized to the file**: `fn_8039A4C4` makes it align32(size) +
`0x200` bytes. It sits at `0x9143F680` in both dumps, with 264 bytes free:

| stadium | file 0 | heap |
|---|---|---|
| Mario Stadium (dir 7) | 2,237,524 | 2,238,048 |
| Wario City (dir 9) | 2,655,048 | 2,655,584 |

So stadiums have no fixed cap. A bigger file 0 takes more of the game heap.
The other variants (files 1 and 2) are not loaded. Stock file 0 sizes (dirs
7-16): Mario Stadium 2,237,524; Bowser Castle 1,558,792; Wario City
2,655,048; Yoshi Park 1,652,128; Peach Ice Garden 1,613,564; DK Jungle
2,349,280; Luigi's Mansion 2,249,856; Daisy Cruiser 1,820,852; Bowser Jr
Playroom 2,468,288; Toy Field 1,027,776. Wario City's is the largest, and
the Maintenance check uses it as its threshold.

Two other heaps of the same shape but fixed size (391,264 bytes, about
250 KB free each) hold dir 159 files (1 or 13, and 20). What dir 159 holds
is not known.

### Game heap headroom

Free in the game heap at the first pitch: 5,036,676 bytes (Mario Stadium,
stock player heaps) and 4,115,820 bytes (Wario City, player heaps +32 KB).
Only one animation bank (file 14, 123,296 bytes) was loaded at that moment.
Of the 920,856-byte difference, 425,984 is the player heaps and 417,536 the
stadium. The other 77,336 is other stadium files or the usual run-to-run
spread (two Mario Stadium dumps differed by 24,864).

How far the player heaps can grow on stock memory (cautious estimate):

| item | bytes |
|---|---|
| free before banks, Wario City, stock player heaps | 4,665,100 |
| the roster's 10 largest file 14 banks (worst at-bat) | −2,260,352 |
| a run scored (the external tool's `heap_budget.py`) | −1,234,944 |
| safety margin (the external tool's) | −524,288 |
| left for 13 player heaps | 645,516, about **48 KB per player** |

A VS at-bat (two 573,440-byte screen captures plus the same event cost,
2,381,824 bytes) needs less than a run scored, so the run scored decides.
Each extra KB per player costs 13 KB of the game heap. The estimate is
cautious: it assumes the roster's 10 largest banks are loaded together.
But the run-scored figure and the margin come from the external tool's
build, not from a dump of ours. A roster that grows dir 119 eats the same
headroom. +128 KB (1.66 MB) ran a full match in Mario Stadium; Wario City
and heavy teams are untested.

### Player memory options

`GameOptions/game_options.py` offers the player heap as game options. They
are one group that shares the hook at `0x8036518C`, so only one can be on.
`player_heap_32`, `_48`, `_64` and `_128` set the size, for example
`lis r5,0xE` + `addi r5,r5,-0x3800` = 903,168.

`player_heap_big_512`, `_1024`, `_2048` and `_4096` are for MEM2 at 128 MB.
They also hook the root heap getter (`0x8039A85C`) and the heap table read
(`0x803A58E8`, entry 3 only, `r28` = `0x8062F060`), and add 64 MB to both.
The grown root heap ends at `0x9730086C`. Every stub first reads the MEM2
size at `0x8000311C`. Below `0x08000000` it keeps the stock root heap and
game heap, and a player heap of +48 KB, so the same DOL boots without the
override and on a real Wii.

*Dolphin* 2026-10-10, with a Mario needing 915,552 bytes:
- `_32` crashed, `_48` and `_128` played full matches.
- `_big_4096` played a full game with the override, and also played
  without it (on the fallback).
- A Mario about 29 KB over +48 KB crashed on `_big_4096` without the
  override, which confirms the fallback is +48 KB.

**Checks** (`game_memory.py`): a character fits when High + Low + its
largest gear file (files 2-5) + 404 bytes is at most the player heap minus
its 0x50-byte header. The 404 bytes are the 8 small blocks, 11 block
headers and up to 32 bytes of alignment per model/gear block. The
dispatcher (`start.py`) snapshots the output DOL before any writing command
and warns afterwards about characters that don't fit and whose files or
player heap changed. The Maintenance tab (`maintenance.py`) checks every
character, and flags stadium files above the largest stock stadium model.

**Check before writing** (`memory_guard.py`, 2026-10-16): planned chains
are checked before they write. `Roster/slot_memory.py` replays the planned
commands of a slot edit batch (`slot_plan`) or a roster pack load (`pack`)
on the lengths of each slot's files 0-5:
- The roster rebuild: a new ID whose `ids[].model` has no `routes` gets a
  fresh copy of its source's `1_Input` files. A new ID without its own
  directory loads its template's files, so the template's check covers it.
- `--patch --target-id`: the built block (`--as-low`: in file 1 too).
- `--unpatch`: the vanilla file.
- `--write-slot-*`: the block file's size.

A slot write stores exactly the block its build check built (`_write_route`
records `len(block)`, and every `--target-id` run applies
`PromoteInplaceEdits`), so each `--validate-only` records its block length
in `_gui/build_sizes.json` (keyed by path, modification time and size).
`start.py` runs the build checks, then `memory_guard.guard`, then the
writes. The limit is the **selected** level's heap: the full size of a
`player_heap_big_*` level, not its +48 KB fallback. A dry run records what
it finds in `_gui/memory_check.json` for the GUI's dialogs (red lines,
"... anyway"). A writing run asks (a `danger` popup), unless
`--accept-memory-over` is given. A slot whose block size is unknown (no
build check) is noted and not checked. `GameOptions/runner.py` asks the
same way before a lower level leaves a character that fits now without
room. A build check took about 0.4 s per model (Bowser High, 2026-10-16).
