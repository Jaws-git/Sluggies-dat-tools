import os
import sys
import shutil
import json
import base64

INPUT_DAT  = 'input/dt_na.dat'
OUTPUT_DIR = 'output_dat'
OUTPUT_DAT = os.path.join(OUTPUT_DIR, 'dt_na.dat')

def abort(message):
    print(f"ERROR: {message}")
    input("\nPress any key to exit...")
    raise SystemExit(1)

# --- require json path argument ---
if len(sys.argv) < 2:
    abort("No JSON file path provided.\nUsage: python patch_dat.py <path_to_model.json>")

json_path = sys.argv[1]
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

# --- decode base64 edited vertex buffers ---
patches = []
for i, submesh in enumerate(submeshes):
    vb_edited = submesh.get("VertexBufferEdited")
    if not vb_edited or "VertexBufferDataEdited" not in vb_edited:
        print(f"  Submesh {i}: no VertexBufferEdited data, skipping.")
        continue
    vb_orig = submesh.get("VertexBuffer", {})
    offset_hex = vb_orig.get("VertexBufferOffset")
    expected_length = vb_orig.get("VertexBufferLength")
    if offset_hex is None or expected_length is None:
        print(f"  Submesh {i}: missing VertexBufferOffset or VertexBufferLength, skipping.")
        continue
    offset = int(offset_hex, 16)
    raw = base64.b64decode(vb_edited["VertexBufferDataEdited"])
    if len(raw) != expected_length:
        abort(
            f"Submesh {i}: decoded edited buffer length {len(raw)} bytes "
            f"does not match original VertexBufferLength {expected_length} bytes.\n"
            f"Vertex and face count must remain unchanged. Aborting to prevent corrupt output."
        )
    patches.append((i, offset, raw))
    print(f"  Submesh {i}: decoded {len(raw)} bytes at offset {offset_hex}")

if not patches:
    abort("No valid edited submesh buffers found to apply.")

print(f"\nLoaded {len(patches)} patch(es) from {json_path}")

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
print(f"\nWriting {len(patches)} patch(es) to {OUTPUT_DAT} ...")
written = 0
with open(OUTPUT_DAT, 'r+b') as f:
    for i, offset, raw in patches:
        f.seek(offset)
        f.write(raw)
        print(f"  Submesh {i}: wrote {len(raw)} bytes at offset 0x{offset:X}")
        written += 1

print(f"\n--- Summary ---")
print(f"Submeshes patched : {written} / {len(patches)}")
print(f"Output file       : {OUTPUT_DAT}")
print(f"Done. You can now overwrite your original dt_na.dat with the patched version, but consider making a backup of the original file.")
input("\nPress any key to exit...")

