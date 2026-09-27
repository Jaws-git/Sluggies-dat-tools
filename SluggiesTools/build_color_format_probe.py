"""Phase 0 probe 4 (E6): a non-white vertex color in the donor's own 2-byte format.

Overwrites Mario's cap color array (two white RGBA4444 entries, QuantizeInfo 48)
with entry 0 = red (f00f) and entry 1 = green (0f0f) through the same-size
in-place path. 1,248 of the cap's 1,254 face-corner color indices use entry 0,
so nearly the whole cap should turn red.

Usage: python build_color_format_probe.py SOURCE.sluggie [--write]
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
for import_path in (TOOLS_DIR, TOOLS_DIR / "Hammerspace"):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import HammerspaceMain as hammerspace
import build_add_submesh_fixture as probe

CAP_SUBMESH = 1
RED_GREEN_RGBA4444 = bytes.fromhex("f00f0f0f")


def build(source_path: Path, write: bool):
    data = json.loads(source_path.read_text(encoding="utf-8"))
    model = data["SluggiesModel"]
    probe.strip_donor_edits(model)
    model["UseHammerspace"] = True
    cap = model["Submeshes"][CAP_SUBMESH]
    channel = cap["ColorChannels"][0]
    if channel["ColorChannelQuantizeInfo"] != 48 or channel["ColorChannelLength"] != len(RED_GREEN_RGBA4444):
        raise ValueError("cap color channel is not the expected 2 x RGBA4444 array")
    channel["ColorChannelDataEdited"] = base64.b64encode(RED_GREEN_RGBA4444).decode()
    cap["ColorArraysEditedByImporter"] = True

    modes = hammerspace.SectionModes(gpl="build", act="clone", tex="clone", skn="clone", trailing="clone")
    block = hammerspace.BuildModelBlock(data, modes, sluggie_path=source_path)
    if not block.validation_report["valid"]:
        raise ValueError("block failed validation: " + "; ".join(block.validation_report["errors"]))
    out_offset = hammerspace.WriteModelBlock(block, f"{source_path.stem}.e6_red") if write else None
    print(f"Block: {len(block.block)} bytes, delta {block.validation_report['size_delta']:+d}, valid=True")
    print(f"Installed at output DAT offset 0x{out_offset:08X}" if out_offset is not None
          else "Dry run only; output DAT/DOL/FST were not modified")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("--write", action="store_true", help="install into output DAT/DOL/FST")
    args = parser.parse_args()
    try:
        build(args.source.resolve(), args.write)
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
