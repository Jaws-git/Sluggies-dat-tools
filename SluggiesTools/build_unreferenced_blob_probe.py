"""Phase 0 probe 1 (E4): a rigid submesh blob moved, its old copy left behind.

Copies one donor rigid submesh blob *byte-for-byte* to a new spot before
GPLUserData, repoints its GEO descriptor at the copy and leaves the original
blob in the GPL unreferenced. Nothing else changes, so the model must look
identical in game. This isolates PLAN_EditRigidMeshes.md E4 ("unreferenced
whole blobs") from any rebuild code.

Usage: python build_unreferenced_blob_probe.py SOURCE.sluggie --submesh 1 [--write]
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
for import_path in (TOOLS_DIR, TOOLS_DIR / "Hammerspace"):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import HammerspaceMain as hammerspace
import build_add_submesh_fixture as probe


def relocate_blob(gpl: bytes, submesh: int) -> bytes:
    """Return *gpl* with blob *submesh* copied to the end of the blob region."""
    magic, user_data_len, user_data_ptr, count, desc_ptr = struct.unpack_from(">5I", gpl, 0)
    descriptors = [struct.unpack_from(">II", gpl, desc_ptr + i * 8) for i in range(count)]
    if not 0 <= submesh < count:
        raise ValueError(f"submesh {submesh} out of range (GPL has {count})")
    region_end = user_data_ptr or len(gpl)
    starts = sorted({pointer for entry in descriptors for pointer in entry} | {region_end})
    blob_start, name_ptr = descriptors[submesh]
    blob_end = next(pointer for pointer in starts if pointer > max(blob_start, name_ptr))
    blob = gpl[blob_start:blob_end]

    # Same address modulo 32 as the original: a blob's own array alignment is
    # calibrated to its GPL-absolute position.
    new_start = region_end + ((blob_start - region_end) & 31)
    out = bytearray(gpl[:region_end])
    out += b"\x00" * (new_start - len(out))
    out += blob
    out += b"\x00" * ((-len(out)) % 32)
    new_user_data = len(out) if user_data_ptr else 0
    out += gpl[region_end:]

    delta = new_start - blob_start
    struct.pack_into(">II", out, desc_ptr + submesh * 8, blob_start + delta, name_ptr + delta)
    if user_data_ptr:
        struct.pack_into(">I", out, 0x08, new_user_data)
    return bytes(out)


def build(source_path: Path, submesh: int, write: bool):
    data = json.loads(source_path.read_text(encoding="utf-8"))
    model = data["SluggiesModel"]
    probe.strip_donor_edits(model)
    model["UseHammerspace"] = True
    offset = model.get("ModelOffset", 0)
    source_model_offset = int(offset, 16) if isinstance(offset, str) else int(offset)

    original_clone = hammerspace.CloneGPL
    hammerspace.CloneGPL = lambda off, length: relocate_blob(original_clone(off, length), submesh)
    try:
        modes = hammerspace.SectionModes(gpl="clone", act="clone", tex="clone", skn="clone", trailing="clone")
        block = hammerspace.BuildModelBlock(data, modes, sluggie_path=source_path)
    finally:
        hammerspace.CloneGPL = original_clone
    if not block.validation_report["valid"]:
        raise ValueError("block failed validation: " + "; ".join(block.validation_report["errors"]))

    prefix = int(block.validation_report.get("container_prefix_size", 0))
    inner = int(block.validation_report.get("inner_assembled_size", len(block.block) - prefix))
    alignment = probe.section_alignment_facts(block.block[prefix:prefix + inner])
    if alignment["misaligned"]:
        raise ValueError("block is off a 32-byte boundary: " + "; ".join(alignment["misaligned"]))

    out_offset = hammerspace.WriteModelBlock(block, f"{source_path.stem}.e4_blob{submesh}") if write else None
    print(
        f"Submesh {submesh} blob relocated, original left unreferenced.\n"
        f"Block: {len(block.block)} bytes, delta {block.validation_report['size_delta']:+d}, valid=True"
    )
    print(f"Installed at output DAT offset 0x{out_offset:08X}" if out_offset is not None
          else "Dry run only; output DAT/DOL/FST were not modified")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("--submesh", type=int, default=1, help="rigid submesh to relocate (Mario's cap is 1)")
    parser.add_argument("--write", action="store_true", help="install into output DAT/DOL/FST")
    args = parser.parse_args()
    try:
        build(args.source.resolve(), args.submesh, args.write)
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
