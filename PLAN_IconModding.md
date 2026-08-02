# Icon Modding Implementation Plan

Reference: `custom_character_icons_discord_guide_20260725.txt`

This plan adds support for custom character icons to Sluggies Tools: expanding the icon bank with new CMPR texture pages in hammerspace, wiring up new characters in the icon source/resource tables, and installing runtime DOL hooks so the game resolves custom icons through a donor-safe path.

**Primary approach:** Use palette-free CMPR pages (as proven in the guide). Users provide regular RGBA PNGs -- no palette editing required. This is simpler for end users than working with palettized/indexed images. CMPR encoding is handled by `wimgt`, which is already a project dependency.

**Existing C8 pipeline unchanged:** The existing in-place atlas patcher (`Icons/patch_icons_inplace.py`) continues to handle stock C8 pages as before. It is not affected by this plan.

**Scope:** Limited to the game's 6 unused characters. All 6 are pre-defined in a single description file shipped with the repository. Users can edit the description (e.g. change donor assignments or swap artwork) but it works out-of-the-box with defaults. The tool processes all 6 characters in a single invocation -- no per-character workflow needed.

**Hard limit:** The DOL data cave has finite space. The actual maximum may turn out lower than 6 once the data cave is measured (see U9). The description file and tool must handle this gracefully.

**Incremental expansion:** We will start with a fixed 4 unused character expansion, since that count was confirmed working by the guide author. In a second iteration we can try expanding to 6 and see if the game crashes due to overwriting critical neighboring data or not.

---

## Existing Tool Landscape (what we already have)

| Module | What it does | Role in this plan |
|---|---|---|
| `Icons/export_icons.py` | Extracts stock icon atlases (C8 pages) to PNG/ACT, writes metadata CSV | Not directly used for new CMPR pages. May be extended later to also export the private pages for inspection. |
| `Icons/patch_icons_inplace.py` | Reimports edited C8 palette/image data in-place (no resize) | Unchanged. Continues handling stock page edits only. |
| `Icons/prepare_icon_routes.py` | Patches color-wheel table (icon_valid, icon_slot) and a resolver at 0x80395EA8 | **Superseded.** The existing scripts can re-import edited atlases but cannot successfully manipulate character-icon assignments for unused characters. The guide's donor-substitution strategy replaces this workflow. |
| `HammerspaceHelper.py` | Expands dt_na.dat beyond 715 MB, patches DOL directory entries, updates FST size | Directly reusable for placing the expanded icon bank clone in hammerspace and determining dynamic offsets for new data. |
| `tpl.py` / `base.py` | Binary parsing, texture descriptors | Reusable for reading/writing texture descriptors in the expanded bank. |

---

## Implementation Steps

### Step 0 -- Character Description File

A single JSON description file is shipped with the repository, pre-configured for all 6 unused characters with default donor assignments and artwork filenames.

**File location:** `SluggiesTools/Icons/icon_characters.json`

**Contents per character entry:**
- Custom character ID
- Donor character ID
- Color-wheel row values (species, captain, model, is_captain, flags, variant, icon_valid=1, icon_slot)
- Side-view image filename (48x51 RGBA PNG)
- Front-view image filename (48x51 RGBA PNG)

**Artwork location:** `SluggiesTools/Icons/icon_artwork/` — PNGs referenced by filename in the description file. Users replace these with custom artwork before running the tool.

**Editability:** Users may edit the description file to change donor assignments or other values, but the defaults work out-of-the-box. The tool validates the file and reports errors for invalid IDs or missing PNGs.

The guide's section 8 ("Confirmed Character Mappings") provides the reference for 4 of the 6 characters. The remaining 2 need their donor assignments defined.

**Verified unused character slots** (all icon_valid=0 in stock DOL):

| Char ID | Stock Species | Variant | Guide Name |
|---------|--------------|---------|------------|
| 0x47 | 0x0B Yoshi | 6 | *(unmapped, needs donor)* |
| 0x48 | 0x0B Yoshi | 7 | Rosalina (donor: 0x05 Daisy) |
| 0x49 | 0x02 Toad | 5 | Orange Toad (donor: 0x0D Red Toad) |
| 0x4A | 0x05 Pianta | 3 | Larry (donor: 0x13 Bowser Jr.) |
| 0x4B | 0x0A Kritter | 4 | Luma (donor: 0x0E Boo) |
| 0x4C | 0x01 Koopa | 2 | *(unmapped, needs donor)* |

71 characters with icon_valid=1 are available as donors. Full reference: `Available_Donor_Characters.md`.

---

### Step 1 -- Clone and Expand the Icon Bank

**What:** Clone group 119 entry 2 to hammerspace with extra room for the initial 4-character expansion's new texture pages, source table entries, and resource rows.

**Implementation status:** Implemented in `SluggiesTools/Icons/clone_icon_bank.py`. Run `python SluggiesTools/Icons/clone_icon_bank.py --dry-run` to validate the stock bank and report the planned aligned destination without writing files. The write mode intentionally remains standalone until Step 8 integrates the complete workflow.

**Details (from guide section 3):**
1. Read the stock entry offset and length from the DOL directory table at file offset **0x68DE88** (the group-entry record for group 119, entry 2).
2. Use `HammerspaceHelper.findFreeMemoryChunk` (or append at end) to allocate space in hammerspace for the expanded clone. For the initial 4-character implementation, reserve the guide-proven expanded length `0x118BF0` (stock length `0x985F0` plus `0x80600` bytes).
3. Copy the stock entry verbatim to the new hammerspace location.
4. Patch the DOL directory record (all three language slots: EN/SP/FR) to point to the new offset and length. Reuse `HammerspaceHelper.patchDolEntry`.
5. Update FST size via `HammerspaceHelper.patchFstFileSize`.

**Key constraint:** Always append at an aligned free location and calculate the real offset dynamically. The hammerspace allocator handles this.

**Caution -- guide table offsets (CONFIRMED MISMATCH):** The guide provides bank-local offsets (normal_a at 0x87520, normal_b/side at 0x88B78, front at 0x8A310, icon descriptor at 0x113C94) for its expanded bank. **These do NOT exist in the stock bank.** Verified findings:

- Reading the stock bank at `0x167E7420 + 0x88B78` and `+ 0x8A310` returns **all zeros** -- the guide's source table offsets are from its reorganized expanded layout, not the stock data.
- The icon descriptor offset `0x113C94` exceeds the stock bank length (`0x985F0`), confirming it only exists in the guide's expanded bank.
- The stock bank's `icon_table_ptr` at bank+0x04 is `0x93680`, which points to the stock icon table structure. This is where the stock source tables are reachable from, but it has a different internal layout than the guide's descriptor at `0x113C94` (see U10).

**Implication:** A verbatim clone of the stock bank preserves the stock layout, NOT the guide's layout. The expansion step must either (a) parse the stock icon table at `0x93680` to locate stock source tables and work with those, or (b) reorganize internal structures during expansion to match the guide's layout. The guide author clearly did (b).

---

### Step 2 -- Add Private CMPR Texture Pages

**What:** Increase the texture-page count and append two new 1024x256 CMPR texture descriptors + empty image payloads inside the expanded bank.

**Details (from guide section 4):**
1. Read the texture-page count as **u16** at bank-local offset +0x20. Increment by 2 (stock 0x92 -> 0x94). *(Verified: reading as u32 returns 0x920000 due to trailing bytes; must read as u16.)*
2. Write two new 0x20-byte texture descriptors:
   - Page 0x92 (side): image local offset, height 256, width 1024, format **0x0E (CMPR)**, palette ptr/count/format all **zero**.
   - Page 0x93 (front): same format, different image offset.
3. Allocate **0x20000 bytes** per page for CMPR image data.
4. Image data must be placed **before** the icon-table pointer (guide section 4 constraint).
5. Zero-fill the image regions initially (blank atlas).

**Descriptor offset formula:** `bank_local + 0x24 + page_id * 0x20`

---

### Step 3 -- Prepare and Encode Artwork

**What:** Take the 48x51 RGBA PNGs for all 6 characters, harden alpha, composite them into the 1024x256 atlas layout, and encode as GX CMPR using `wimgt`.

**Details (from guide section 5):**
1. For each character in the description file, read its side and front PNG (48x51, RGBA).
2. Harden alpha: >= 128 -> 255, < 128 -> fully transparent.
3. Place all icons into the full 1024x256 atlas images (one for side, one for front). The placement coordinates come from the resource row UV mapping (step 5). Apply the **+8px X offset** universally (confirmed for all slots).
4. Encode the complete atlases as GX CMPR using `wimgt`.
5. Write the encoded bytes into the image payload region of pages 0x92 (side) and 0x93 (front) in the expanded bank.

Since all 6 characters are processed in one invocation, the full atlas is composed once and written once -- no incremental compositing needed.

---

### Step 4 -- Update Icon Source Tables

**What:** Add new 0x50-byte records to the side (normal_b) and front source tables for all 6 characters.

**Details (from guide section 6):**

> **NOTE (verified mismatch):** The guide's hardcoded offsets (normal_b at 0x88B78, front at 0x8A310) are from the guide's expanded/reorganized bank. They contain **all zeros in the stock bank** and cannot be used directly after a verbatim clone. The stock source tables must be located by parsing the icon table structure at bank-local `0x93680` (see U10), OR the expansion step must reorganize the bank to place tables at known offsets.

1. Locate the side table and front table. Two approaches:
   - **(a) Parse stock layout:** Follow the icon table structure at bank+`0x93680` to find the stock source tables (offsets TBD, see U10). After a verbatim clone these are at the same bank-local positions.
   - **(b) Reorganize during expansion:** Relocate the source tables to new known offsets during the expansion step, matching the guide's approach. This requires understanding the full icon table structure well enough to update all internal pointers.
2. For each character:
   - Copy the donor's existing 0x50-byte record.
   - Replace the character ID at record +0x02 with the custom character's ID.
   - Keep +0x04 as 0x0400.
   - Set +0x06 to the custom character's resource ID.
3. Append all new records, update the table's record count (+0x24) and total length (+0x08).
4. Recalculate the icon descriptor's signed relative offsets (descriptor +0x04/+0x08/+0x0C/+0x10) if any table moved.

**Record stride** is fixed at 0x50.

---

### Step 5 -- Add Resource Rows (UV Coordinates)

**What:** Create resource rows that map all 6 characters' icons to specific rectangles on the private CMPR pages.

**Details (from guide section 7):**

> **NOTE:** The guide places its resource table at bank-local `0x118000`. Like the source table offsets, this is from the guide's reorganized layout. In a verbatim clone of the stock bank, the stock resource table is reachable via the icon table structure at `0x93680` (see U10). The resource table offset for our expanded bank depends on whether we parse stock layout or reorganize.

1. The resource table offset is determined dynamically by the hammerspace helpers based on available free space in the expanded bank.
2. For each character, write two 0x14-byte resource rows (one side, one front):
   - +0x00 u16: texture page (0x92 for side, 0x93 for front)
   - +0x02 u16: reserved (0)
   - +0x04 f32 V1, +0x08 f32 U1, +0x0C f32 V2, +0x10 f32 U2
   - UV formula: U1=x/1024, V1=y/256, U2=(x+48)/1024, V2=(y+51)/256
3. The resource IDs assigned here must match the IDs written into the source table records in step 4.

---

### Step 6 -- Patch Color-Wheel Table

**What:** Make all 6 unused characters selectable by setting their color-wheel row fields.

**Details (from guide section 2):**
1. For each character, write its 8-byte row at DOL file offset `0x0062D650 + char_id * 8`.
2. Set icon_valid (+6) to 0x01 and icon_slot (+7) to the appropriate value.

*(Verified: the color-wheel table at 0x0062D650 contains 101 entries as expected. 71 have icon_valid=1, 6 unused characters (0x47-0x4C) have icon_valid=0, and 24 team NPCs (0x4D-0x64) have icon_valid=0. Full dump in `Available_Donor_Characters.md`.)*

---

### Step 7 -- Install Donor-Safe Runtime Hooks (DOL Code Patches)

**What:** Patch three locations in main.dol so the game resolves custom character icons through their donor, then swaps in the custom resource row at the last moment. This replaces the existing `Icons/prepare_icon_routes.py` approach, which could not successfully handle icon assignments for unused characters.

**Details (from guide section 9):**

Three hooks:
1. **Lower lookup hook** (0x8050A5AC -> stub at 0x80004C6C): Records the real custom ID, submits the donor ID to the stock resolver. Displaced instruction: 0x9421FFA0.
2. **Key hook** (0x80519478 -> stub at 0x8000576C): Confirms that the expected donor was resolved. Displaced instruction: 0x4BFF5099.
3. **Final row hook** (0x8051952C -> stub at 0x80005DB0): Checks the donor's side/front resource ID and redirects r31 to the custom 0x14-byte resource row stored in a DOL data cave. Displaced instruction: 0xA0BF0000.

Per custom character, the stub code needs:
- A mapping entry (custom ID -> donor ID)
- Two 0x14-byte resource rows stored in the DOL data cave (starting at 0x80004E08)
- The donor's side/front resource IDs as match keys

Since the character count is fixed (max 6), the stubs are written for all characters in a single pass.

#### Example: Lower Lookup Hook Stub (hook 1 of 3)

This is the simplest of the three hooks. It intercepts the icon-lookup function's entry point, checks whether the character being looked up is one of our custom characters, and if so saves the real ID and swaps in the donor ID. The stock resolver then runs normally using the donor — it never sees the custom ID. The other two hooks (key hook, final row hook) act later in the pipeline to confirm the donor was resolved and redirect to the custom resource row.

**How the hook is installed:** The stock instruction at virtual address `0x8050A5AC` is replaced with a branch (`b`) to the stub at `0x80004C6C`. The stub executes its logic, then runs the displaced stock instruction before branching back to `0x8050A5B0` (the next instruction after the hook site).

**Displaced instruction:** `0x9421FFA0` = `stwu r1, -0x60(r1)` — a standard PPC function prologue that creates a 0x60-byte stack frame. This must be executed by the stub before returning.

**Register convention:** `r3` holds the character ID being looked up (first argument in PPC calling convention). `r12` and `r0` are used as scratch registers (caller-saved, safe to clobber in a prologue context).

```asm
# ============================================================
# Lower Lookup Hook Stub        (installed at 0x80004C6C)
# Hooked site:  0x8050A5AC      (stock: stwu r1,-0x60(r1))
# Return to:    0x8050A5B0
# ============================================================
# r3 = character ID the game wants to look up.
#
# If r3 is a custom character:
#   1. Store r3 into pending_id so later hooks know a
#      substitution is in progress.
#   2. Replace r3 with the donor's ID.
#
# If r3 is NOT a custom character:
#   1. Clear pending_id to zero (no substitution).
#
# Then execute the displaced instruction and return.
# ============================================================

.set pending_id, 0x80004E00          # shared flag/ID word

stub_entry:                          # 0x80004C6C
    lis   r12, pending_id@ha         # load upper half of &pending_id
    ori   r12, r12, pending_id@l     # r12 = &pending_id

    # --- per-character compare-and-branch table ---

    cmpwi r3, 0x48                   # Rosalina?
    beq   sub_daisy                  #   -> donor Daisy (0x05)

    cmpwi r3, 0x49                   # Orange Toad?
    beq   sub_red_toad               #   -> donor Red Toad (0x0D)

    cmpwi r3, 0x4A                   # Larry?
    beq   sub_bowser_jr              #   -> donor Bowser Jr. (0x13)

    cmpwi r3, 0x4B                   # Luma?
    beq   sub_boo                    #   -> donor Boo (0x0E)

    # (characters 5 and 6 would add two more cmpwi/beq pairs here)

    # --- not a custom character ---
    li    r0, 0
    stw   r0, 0(r12)                 # pending_id = 0 (clear flag)
    b     done

sub_daisy:
    stw   r3, 0(r12)                 # pending_id = 0x48 (Rosalina)
    li    r3, 0x05                   # feed Daisy's ID to the resolver
    b     done

sub_red_toad:
    stw   r3, 0(r12)                 # pending_id = 0x49
    li    r3, 0x0D
    b     done

sub_bowser_jr:
    stw   r3, 0(r12)                 # pending_id = 0x4A
    li    r3, 0x13
    b     done

sub_boo:
    stw   r3, 0(r12)                 # pending_id = 0x4B
    li    r3, 0x0E
    b     done

done:
    stwu  r1, -0x60(r1)             # displaced stock instruction
    b     0x8050A5B0                 # return to stock code flow
```

**Scaling notes:** Each additional character adds one `cmpwi` + `beq` pair (8 bytes) to the branch table and one substitution block of 3 instructions (12 bytes). For 6 characters the stub is roughly 30 instructions (~120 bytes). The stub itself lives in an unused code region starting at `0x80004C6C`, not in the data cave — the data cave at `0x80004E08` stores resource rows only.

---

### Step 8 -- Integration with CLI

**What:** Wire everything into `start.py` as a new command, e.g. `--add-custom-icons`.

Proposed workflow:
```
python start.py --add-custom-icons
```

1. Reads the description file from its shipped location in the repo.
2. Validates all entries: checks character IDs, donor IDs, and that all referenced PNGs exist.
3. Runs steps 1-7 in a single pass for all characters, reading from `1_Input/` and writing to `3_Output_Dat/`.
4. Generates a report JSON in `2_Output_Models/_ICONS/metadata/`.

No arguments needed (the description file location is known). Users who want to customize edit the description file and/or replace the PNGs before running the command.

Should also support a `--dry-run` mode that validates the description file and reports what would change without writing bytes.

---

## Unknowns and Open Questions

### U5 -- Stub Code Generation vs. Hardcoded Assembly

The guide provides specific PPC (PowerPC) assembly stubs at fixed virtual addresses (0x80004C6C, 0x8000576C, 0x80005DB0). For N custom characters, these stubs contain per-character branch tables.

With the scope fixed at max 6 characters, pre-assembled binary blobs become more practical (only need variants for 1-6 characters, or a single blob for all 6). However, this depends on U9 confirming that the data cave can fit all 6.

**Question:** Should the tool generate the PPC machine code dynamically based on the description file, or ship pre-assembled blobs? With a fixed max of 6, blobs are feasible -- but dynamic generation would still be needed if individual characters can be enabled/disabled in the description file.

### U10 -- Stock Icon Table Structure at 0x93680

The stock bank's `icon_table_ptr` (bank+0x04) is `0x93680`. The guide's icon descriptor is at `0x113C94` in its expanded bank (unreachable in stock). The structure at `0x93680` is the stock equivalent, but its internal layout doesn't cleanly match the guide's descriptor format.

**Raw data at stock bank + 0x93680:**
```
+0x00: 0x028001C0   (flags/magic -- not a simple count)
+0x04: 0x3C240002   (not a plausible relative offset -- too large)
+0x08: 0x00000014   -> bank-local 0x9369C  (plausible normal_a)
+0x0C: 0x00004380   -> bank-local 0x97A0C  (plausible normal_b)
+0x10: 0x00004F68   -> bank-local 0x985F8  (8 bytes past bank end)
```

If `+0x08`/`+0x0C`/`+0x10` are relative offsets from their own field positions, two resolve inside the bank but the front table offset overshoots by 8 bytes. The `+0x04` value `0x3C240002` is clearly not a relative offset in the same sense.

**What this means:** The stock icon table header has a different format than the guide's icon descriptor, or the offset base calculation is wrong. Steps 4 and 5 cannot proceed without resolving this -- either by reverse-engineering the stock structure or by understanding exactly how the guide author reorganized it.

**Question:** What is the correct format of the icon table header at `0x93680`? Is it a wrapper structure that contains the icon descriptor at some sub-offset? Are the relative offsets calculated differently than the guide implies?

---

### U9 -- Data Cave Capacity

The guide demonstrates 4 custom characters and mentions a 5th. The DOL data cave at 0x80004E08 has finite space. We want to support 6 (all unused characters).

**Question:** What is the practical upper limit? How much space is available in the data cave region (0x80004E00 onward) before we collide with other code/data? Per character, the data cave needs ~0x28 bytes (two 0x14-byte resource rows) plus the branch table entries in the stubs. For 6 characters this is ~0xF0 bytes of resource rows alone, plus stub code. If 6 doesn't fit, the description file must document the actual limit and the tool must enforce it.
