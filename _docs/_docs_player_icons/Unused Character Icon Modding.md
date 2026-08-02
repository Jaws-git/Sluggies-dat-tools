# Unused Character Icon Replacement

Reference: `custom_character_icons_discord_guide_20260725.txt`

This document describes how Sluggies Tools replaces the player icons for Mario Super Sluggers' six unused character slots. It covers the user workflow, binary layout, runtime routing, validation rules, and the design decisions behind the implementation.

The system adds two private CMPR texture pages to an expanded copy of the player-icon bank, creates source and resource records for the unused characters, enables their color-wheel rows, and installs three PowerPC hooks that route each unused character through a compatible stock donor.

The replacement pipeline is separate from stock icon editing. `SluggiesTools/Icons/patch_icons_inplace.py` continues to reimport the game's existing C8 atlases and palettes without changing their layout. Unused-character replacement uses private palette-free CMPR pages so users can supply ordinary RGB or RGBA PNG files.

## Supported Characters

The implementation covers exactly the six unused character IDs `0x47` through `0x4C`. All six are configured and installed together.

| Character ID | Stock species | Variant | Default replacement | Donor |
|---:|---:|---:|---|---|
| `0x47` | `0x0B` Yoshi | 6 | Black Yoshi | Peach (`0x04`) |
| `0x48` | `0x0B` Yoshi | 7 | White Yoshi | Mario (`0x00`) |
| `0x49` | `0x02` Toad | 5 | Black Toad | Luigi (`0x01`) |
| `0x4A` | `0x05` Pianta | 3 | Black Pianta | Donkey Kong (`0x02`) |
| `0x4B` | `0x0A` Kritter | 4 | Black Kritter | Diddy Kong (`0x03`) |
| `0x4C` | `0x01` Koopa | 2 | Black Koopa | Daisy (`0x05`) |

These rows have `icon_valid=0` in the stock DOL. The replacement process preserves their stock species, captain, model, flags, variant, and icon-slot values while enabling icon resolution.

The fixed DOL code-cave layout cannot support a seventh route. The final-row hook uses `0x168` bytes of its `0x188`-byte cave; a seventh route would require `0x19C` bytes. Configuration loaders therefore require exactly six entries.

## User Workflow

1. Place clean `dt_na.dat` and `main.dol` files in `1_Input/`.
2. Put the twelve side/front artwork PNGs in `1_Input/_Icons/`.
3. Review `SluggiesTools/Icons/icon_characters.json` if character names, donors, or filenames need to change.
4. Validate the complete transformation:

   ```console
   python start.py --add-custom-icons --dry-run
   ```

5. Install the replacements:

   ```console
   python start.py --add-custom-icons
   ```

The same operation is exposed as option 4 in `StartTools.bat`.

Artwork fitting is controlled with `--icon-fit`:

```console
python start.py --add-custom-icons --icon-fit contain
python start.py --add-custom-icons --icon-fit cover
python start.py --add-custom-icons --icon-fit strict
```

- `contain` is the default. It preserves aspect ratio, centers the image, and adds transparent padding.
- `cover` preserves aspect ratio while filling and center-cropping the slot.
- `strict` requires the source to be exactly `48x51` pixels.

Stretching is intentionally unsupported.

## Configuration

`SluggiesTools/Icons/icon_characters.json` is the single source of truth for all six routes. Each entry defines:

- custom character ID;
- donor character ID and donor side/front resource IDs;
- complete eight-byte color-wheel row values;
- side-view and front-view PNG filenames.

Artwork paths are resolved relative to the configured artwork directory, normally `1_Input/_Icons/`. RGB and RGBA PNGs are accepted and normalized to RGBA. Invalid IDs, duplicate IDs, missing files, unsupported route counts, and mismatched artwork/color-wheel character sets are rejected before output files are modified.

Donors provide known-good source-table records and allow the stock resolver to complete normally. The stock color-wheel table contains 71 characters with `icon_valid=1` that can serve as donors. The shipped configuration uses the first six valid donor IDs in ascending order (`0x00` through `0x05`) and requires donor IDs to be unique.

## System Architecture

The game resolves an icon through several independent structures:

1. The color-wheel row enables the character and supplies its icon slot.
2. The side or front source table maps a character ID to a resource ID.
3. The resource table maps that resource ID to a texture page and UV rectangle.
4. The texture descriptor maps the page to encoded image data.

Unused IDs cannot be made reliable by changing only the color-wheel row. They fail later keyed registration and fall back to another icon. The replacement system therefore uses donor substitution during stock resolution and redirects only the final resource-row selection.

The complete transformation is coordinated by `SluggiesTools/Icons/add_custom_icons.py`:

1. Load and validate configuration and artwork.
2. Encode both custom atlases before mutating output.
3. Clone or reuse the supported expanded icon bank.
4. Add private texture pages and payloads.
5. Relocate and extend source tables.
6. Extend the resource table.
7. Enable the six color-wheel rows.
8. Generate and validate the runtime hooks.
9. Stage complete DAT and DOL files, then replace the output pair.

This all-or-nothing orchestration prevents a failed encode or binary validation from leaving half-patched output files.

## Icon Bank Expansion

The player-icon bank is group 119, entry 2 in `dt_na.dat`. Its stock location and size are:

| Property | Value |
|---|---:|
| Stock DAT offset | `0x167E7420` |
| Stock length | `0x985F0` |
| DOL directory record | `0x68DE88` |
| Expanded length | `0x118CE0` |

The stock entry is cloned to an aligned hammerspace location selected dynamically. The EN, SP, and FR fields in the DOL directory record are all updated to the same offset, length, and allocation. The FST size is updated when an input or output `fst.bin` is available.

Dynamic placement is required because hammerspace availability depends on the current DAT. No generated bank may assume a fixed absolute destination.

### Expanded Bank Layout

| Structure | Bank-local offset or value |
|---|---:|
| normal_a source table | `0x87520` |
| Side source table | `0x88B78` |
| Front source table | `0x8A3B0` |
| Side CMPR image | `0x93880` |
| Front CMPR image | `0xB3880` |
| Relocated icon-table container | `0x113C80` |
| Relocated icon descriptor | `0x113C94` |
| Resource table | `0x118000` |
| Expanded bank length | `0x118CE0` |

The offsets documented by the original guide describe its reorganized expanded bank, not the stock bank. For example, stock data at bank-local `0x88B78` and `0x8A310` is zero, and `0x113C94` is beyond the stock bank length. The implementation locates stock structures through their descriptor-relative pointers, copies them to the expanded layout, and rewrites those pointers.

### Icon-Table Container

The stock icon-table container begins at bank-local `0x93680`. Its relevant fields are:

| Container field | Stock value | Meaning |
|---|---:|---|
| `+0x08` | `0x14` | Descriptor offset |
| `+0x0C` | `0x4380` | Resource-subsection directory offset |
| `+0x10` | `0x4F68` | Final-subsection endpoint |

The container moves to `0x113C80`. Its descriptor stores signed pointers relative to the descriptor base, so every relocated resource or source table requires a recalculated pointer.

The final-subsection endpoint must grow with the resource table. Leaving it at `0x4F68` after adding rows causes an immediate startup crash. The four-character layout used `0x5008`; the twelve-row six-character layout uses `0x5058`.

## Private CMPR Pages

The stock texture count is a big-endian `u16` at bank offset `0x20`. It increases from `0x92` to `0x94`. Reading this field as a `u32` produces the misleading value `0x920000` because the following bytes are unrelated.

Two `0x20`-byte descriptors are added:

| Page | View | Dimensions | GX format | Payload size |
|---:|---|---:|---:|---:|
| `0x92` | Side | `1024x256` | `0x0E` CMPR | `0x20000` |
| `0x93` | Front | `1024x256` | `0x0E` CMPR | `0x20000` |

Descriptor locations follow `0x24 + page_id * 0x20`. CMPR is palette-free, so palette pointer, count, and format fields are zero. Both image regions are placed before the relocated icon table and are bounds-checked to prevent overlap.

CMPR was selected because it accepts ordinary true-color artwork and avoids exposing the stock C8 palette workflow to users. The existing C8 export/reimport tools remain unchanged for stock-page editing.

## Artwork Processing

Each source image is normalized to a `48x51` RGBA slot. Alpha is hardened before encoding:

- alpha values of 128 or greater become 255;
- lower alpha values become fully transparent black.

Six slots are arranged at resource X coordinates `0`, `64`, `128`, `192`, `256`, and `320`, with Y equal to zero. Artwork is composited at resource X plus 8 pixels. This padding matches the game's established icon placement while the resource rectangle itself remains 48 pixels wide.

The complete side and front atlases are each composed once and encoded through `wimgt`. Guardrails require every normalized slot to fit the atlas, every CMPR payload to be exactly `0x20000` bytes, and every write to remain inside its assigned private-page region. Artwork resizing can therefore never alter the bank layout or length.

## Source Tables

The icon descriptor points to normal_a, side, and front source tables. Source records have a fixed `0x50`-byte stride.

For each custom character, the side and front donor records are copied and changed only where routing requires it:

- record `+0x02`: custom character ID;
- record `+0x04`: retained as `0x0400`;
- record `+0x06`: assigned custom resource ID.

The custom side resource IDs are `0x98` through `0x9D`; front IDs are `0x9E` through `0xA3`.

Records must remain in descending character-ID order. The first record uses marker `0x0014` at `+0x00`; all continuation records use `0x0114`. New records are merged into the sorted table, and the first-record marker is transferred if a custom ID becomes the new first entry.

This ordering is a runtime invariant, not cosmetic organization. Appending custom records after stock ID `0x00` caused all source-table-resolved player icons to disappear in game. Sorting the records and preserving the marker restored icon resolution.

The front table is located at `0x8A3B0`, which is `0xA0` beyond the guide's four-character position. This additional space prevents six expanded side records from overlapping it.

## Resource Rows and UV Mapping

The stock resource table at `0x118000` contains `0x98` rows and has length `0xBE8`. Twelve `0x14`-byte rows are appended, producing count `0xA4` and length `0xCD8`. Eight trailing bytes remain after the expanded table.

Each row has this layout:

| Offset | Type | Meaning |
|---:|---|---|
| `+0x00` | `u16` | Texture page (`0x92` side, `0x93` front) |
| `+0x02` | `u16` | Reserved, zero |
| `+0x04` | `f32` | V1 |
| `+0x08` | `f32` | U1 |
| `+0x0C` | `f32` | V2 |
| `+0x10` | `f32` | U2 |

For a slot at `(x, y)`, the rectangle is:

$$
U_1 = \frac{x}{1024},\quad
V_1 = \frac{y}{256},\quad
U_2 = \frac{x + 48}{1024},\quad
V_2 = \frac{y + 51}{256}
$$

Rows are stored in resource-ID order: all six side rows first, followed by all six front rows. Resource IDs must agree with those written into the source records and referenced by the runtime hooks.

The expanded bank is `0xF0` bytes larger than the guide-sized `0x118BF0` layout because twelve rows require exactly `12 * 0x14 = 0xF0` additional bytes. Moving the resource table backward was tested and caused a startup crash, so the table remains at its verified offset and the bank grows instead.

## Color-Wheel Rows

The color-wheel table begins at DOL file offset `0x0062D650`, with an eight-byte row per character:

| Row offset | Meaning |
|---:|---|
| `+0x00` | Species ID |
| `+0x01` | Captain ID |
| `+0x02` | Model ID |
| `+0x03` | Is-captain flag |
| `+0x04` | Flags |
| `+0x05` | Variant index |
| `+0x06` | Icon-valid flag |
| `+0x07` | Icon slot |

The row address is `0x0062D650 + character_id * 8`. All eight configured bytes are validated, although the default six routes differ from stock only by setting `icon_valid` to 1.

The stock table has 101 entries: 71 enabled characters, six disabled unused characters, and 24 disabled team NPCs.

## Donor-Safe Runtime Routing

Color-wheel and source-table changes alone are insufficient for unused IDs because later keyed registration can still fail. Three hooks preserve the stock resolver path by temporarily substituting a known-good donor, then select the custom resource row only after donor resolution succeeds.

| Hook | Patched address | Stub address | Purpose | Displaced instruction |
|---|---:|---:|---|---:|
| Lower lookup | `0x8050A5AC` | `0x80004C6C` | Save custom ID and submit donor ID | `0x9421FFA0` |
| Key | `0x80519478` | `0x8000576C` | Confirm the expected donor route | `0x4BFF5099` |
| Final row | `0x8051952C` | `0x80005DB0` | Redirect to the custom side/front row | `0xA0BF0000` |

The lower hook records the real custom ID in shared state at `0x80004E00` and replaces the resolver argument with the configured donor ID. Non-custom lookups clear that state. The key hook verifies that the expected donor was actually resolved. The final-row hook matches the donor's side or front resource ID and redirects `r31` to the corresponding custom `0x14`-byte row stored in the DOL data cave.

This arrangement confines custom behavior to the six configured IDs. Ordinary characters continue through the displaced stock instructions without resource redirection.

Hook code is generated from the JSON routes rather than copied from fixed machine-code blobs. The internal PPC builder resolves labels, checks branch ranges, verifies the exact stock words at hook sites, and accepts a destination cave only when it is zero-filled or already contains the exact generated patch.

### Code-Cave Capacity

| Region | Capacity | Six-route usage | Generated end |
|---|---:|---:|---:|
| Lower stub at `0x80004C6C` | `0x2CC` | `0x98` | `0x80004D03` |
| State and rows at `0x80004E00` | `0x138` | `0xF8` | `0x80004EF7` |
| Key stub at `0x8000576C` | `0x1CC` | `0xE8` | `0x80005853` |
| Final-row stub at `0x80005DB0` | `0x188` | `0x168` | `0x80005F17` |

The final-row cave is the limiting region and leaves `0x20` bytes unused with six routes.

## Output, Reruns, and Migration

The integrated command writes `dt_na.dat` and `main.dol` to `3_Output_Dat/` and emits metadata under `2_Output_Models/_ICONS/metadata/`.

All artwork encoding and binary construction completes in memory first. Write mode stages complete temporary DAT and DOL files and replaces the output pair only after both are ready. If no source `fst.bin` is available, installation succeeds with a warning, but a rebuilt disc must still advertise the expanded `dt_na.dat` size before the hammerspace bank can be read.

The operation is idempotent for supported outputs. It can reuse an exact current six-character expansion and can migrate the generated four-character bank (`0x118C90`) to the six-character bank (`0x118CE0`). Legacy migration requires exact source-table, resource-table, hook, allocation, and alignment signatures. This strict validation avoids interpreting unrelated modifications as an older generated layout.

Migration preserves private texture payloads and unrelated DOL bytes while rebuilding structures whose size or location changed.

## Module Responsibilities

| Module | Responsibility |
|---|---|
| `add_custom_icons.py` | End-to-end planning, validation, migration, staging, and reporting |
| `clone_icon_bank.py` | Stock bank validation, expansion, hammerspace placement, directory/FST updates |
| `add_private_texture_pages.py` | Private CMPR descriptors, image regions, and icon-table relocation |
| `prepare_icon_artwork.py` | PNG loading, fitting, alpha hardening, atlas composition, and CMPR encoding |
| `update_icon_source_tables.py` | Donor-route loading, source relocation, ordered record merge, resource assignment |
| `add_icon_resource_rows.py` | Resource-table expansion and UV row construction |
| `patch_color_wheel.py` | Complete color-wheel row validation and patching |
| `install_runtime_hooks.py` | PPC generation, cave validation, hook installation, and runtime row data |
| `export_icons.py` | Existing stock C8 icon export; not part of private-page replacement |
| `patch_icons_inplace.py` | Existing stock C8 icon reimport; unchanged by this workflow |
| `prepare_icon_routes.py` | Superseded resolver experiment; not used by the integrated workflow |

## Design Decisions and Invariants

### Palette-Free Private Pages

CMPR pages let users provide normal PNG artwork and keep custom replacements independent from the stock shared C8 image/palette system. This reduces user-facing complexity and prevents custom palette edits from affecting stock pages.

### Donor Substitution Instead of Resolver Widening

Simply widening character bounds or setting `icon_valid` does not establish all downstream registration state for unused IDs. Donor substitution uses a route the stock game already understands, then changes only the final resource-row pointer. The complete six-route configuration is confirmed in game.

### Fixed Six-Character Batch

All six slots are built together because they share texture pages, source-table growth, resource-table growth, and fixed DOL caves. A single description-driven build avoids order-dependent incremental patching and makes capacity checks deterministic.

### Structural Validation Before Mutation

Every binary stage checks expected pointers, counts, lengths, alignment, stock hook words, cave contents, and non-overlap constraints. Known malformed layouts are rejected rather than repaired heuristically. This policy follows two observed failures:

- stale icon-container endpoint metadata caused a startup crash;
- incorrectly ordered source records made resolved icons disappear.

### Preserve Existing Systems

The custom CMPR workflow does not replace the C8 export/reimport tools. Each workflow owns a separate texture surface and can be used without changing the other's data model.

## Validation Status

- The complete six-route donor-substitution workflow is confirmed in game.
- Descending source-record order and marker transfer are confirmed in game.
- The icon-container endpoint requirement is confirmed by startup-crash isolation.
- The six-character bank, source/resource layout, color-wheel rows, and generated hooks pass real-input offline validation.
- The ascending donor mapping (`0x00` through `0x05`) is confirmed in game for all six routes.
