import os
import sys
import shutil
import json
import base64

INPUT_DAT  = '../1_Input/dt_na.dat'
OUTPUT_DIR = '../3_Output_Dat'
OUTPUT_DAT = os.path.join(OUTPUT_DIR, 'dt_na.dat')

def abort(message):
    print(f"ERROR: {message}")
    input("\nPress any key to exit...")
    raise SystemExit(1)

# --- parse arguments ---
unpatch = '--unpatch' in sys.argv
argv_clean = [a for a in sys.argv[1:] if a != '--unpatch']

if not argv_clean:
    abort("No .sluggies file path provided.\nUsage: python patch_dat.py <path_to_model.sluggies> [--unpatch]")

json_path = argv_clean[0]
if not os.path.exists(json_path):
    abort(f"JSON file not found: {json_path}")

# --- load and parse json ---
with open(json_path, 'r') as f:
    try:
        data = json.load(f)
    except json.JSONDecodeError as e:
        abort(f"JSON parse error in {json_path}: {e}")

if "SluggiesModel" not in data:
    abort(f"JSON does not contain a 'SluggiesModel' entry: {json_path}")

submeshes = data["SluggiesModel"].get("Submeshes", [])
if not submeshes:
    abort("No submeshes found in JSON.")

# --- decode base64 vertex buffers ---
mode_label = "original" if unpatch else "edited"
print(f"Mode: {'--unpatch (restoring original data)' if unpatch else 'patch (writing edited data)'}")
patches = []
for i, submesh in enumerate(submeshes):
    vb_orig = submesh.get("VertexBuffer", {})
    offset_hex = vb_orig.get("VertexBufferOffset")
    expected_length = vb_orig.get("VertexBufferLength")
    if offset_hex is None or expected_length is None:
        print(f"  Submesh {i}: missing VertexBufferOffset or VertexBufferLength, skipping.")
        continue
    if unpatch:
        vb_data = vb_orig.get("VertexBufferData")
        if not vb_data:
            print(f"  Submesh {i}: no VertexBufferData in VertexBuffer, skipping.")
            continue
        raw = base64.b64decode(vb_data)
    else:
        vb_edited = submesh.get("VertexBufferEdited")
        if not vb_edited or "VertexBufferDataEdited" not in vb_edited:
            print(f"  Submesh {i}: no VertexBufferEdited data, skipping.")
            continue
        raw = base64.b64decode(vb_edited["VertexBufferDataEdited"])
    offset = int(offset_hex, 16)
    if len(raw) != expected_length:
        abort(
            f"Submesh {i}: decoded {mode_label} buffer length {len(raw)} bytes "
            f"does not match original VertexBufferLength {expected_length} bytes.\n"
            f"Aborting to prevent corrupt output."
        )
    patches.append((i, offset, raw))
    print(f"  Submesh {i}: decoded {len(raw)} bytes at offset {offset_hex}")

# --- decode base64 UV channel buffers ---
uv_patches = []
for i, submesh in enumerate(submeshes):
    for ch in submesh.get("UVChannels", []):
        ch_ind = ch.get("UVChannelIndex", "?")
        offset_hex = ch.get("UVChannelOffset")
        expected_length = ch.get("UVChannelLength")
        if offset_hex is None or expected_length is None:
            print(f"  Submesh {i} UV ch {ch_ind}: missing offset or length, skipping.")
            continue
        if unpatch:
            raw_b64 = ch.get("UVChannelData")
            if not raw_b64:
                print(f"  Submesh {i} UV ch {ch_ind}: no UVChannelData, skipping.")
                continue
        else:
            raw_b64 = ch.get("UVChannelDataEdited")
            if not raw_b64:
                print(f"  Submesh {i} UV ch {ch_ind}: no UVChannelDataEdited, skipping.")
                continue
        raw = base64.b64decode(raw_b64)
        offset = int(offset_hex, 16)
        if len(raw) != expected_length:
            # Mismatched length means the deduplicated UV set changed size.
            # Patching would corrupt adjacent data, so skip with a clear warning.
            print(
                f"  WARNING  Submesh {i} UV ch {ch_ind}: {mode_label} buffer is {len(raw)} bytes "
                f"but the original slot is {expected_length} bytes — skipping. "
                f"(Edit UVs without adding or removing unique coordinate entries to keep sizes equal.)"
            )
            continue
        uv_patches.append((i, ch_ind, offset, raw))
        print(f"  Submesh {i} UV ch {ch_ind}: decoded {len(raw)} bytes at offset {offset_hex}")

if not patches and not uv_patches:
    abort(f"No valid {mode_label} buffers found to apply.")

print(f"\nLoaded {len(patches)} vertex patch(es) and {len(uv_patches)} UV patch(es) from {json_path}")

# --- ensure output_dat folder exists ---
if not os.path.exists(OUTPUT_DIR):
    os.mkdir(OUTPUT_DIR)
    print(f"Created folder: {OUTPUT_DIR}/")

# --- check input file ---
if not os.path.exists(INPUT_DAT):
    abort(f"Input file not found: {INPUT_DAT}\nCannot continue without a source dt_na.dat.")

# --- copy only if not already present ---
if os.path.exists(OUTPUT_DAT):
    print(f"Output file already exists, skipping copy: {OUTPUT_DAT}")
else:
    shutil.copy2(INPUT_DAT, OUTPUT_DAT)
    print(f"Copied {INPUT_DAT} -> {OUTPUT_DAT}")

# --- write patches to output dat ---
print(f"\nWriting {len(patches)} vertex patch(es) and {len(uv_patches)} UV patch(es) to {OUTPUT_DAT} ...")
written_vb = 0
written_uv = 0
with open(OUTPUT_DAT, 'r+b') as f:
    for i, offset, raw in patches:
        f.seek(offset)
        f.write(raw)
        print(f"  Submesh {i} vertex: wrote {len(raw)} bytes at offset 0x{offset:X}")
        written_vb += 1
    for i, ch_ind, offset, raw in uv_patches:
        f.seek(offset)
        f.write(raw)
        print(f"  Submesh {i} UV ch {ch_ind}: wrote {len(raw)} bytes at offset 0x{offset:X}")
        written_uv += 1

print(f"\n--- Summary ---")
print(f"Vertex submeshes patched : {written_vb} / {len(patches)}")
print(f"UV channels patched      : {written_uv} / {len(uv_patches)}")
print(f"Output file              : {OUTPUT_DAT}")
if unpatch:
    print(f"Done. The output file has been restored to the original vertex and UV data.")
else:
    print(f"Done. You can now overwrite your original dt_na.dat in the game folder.")
