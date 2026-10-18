"""Stadium collision files: read, add triangles, write to hammerspace.

Each stadium (dt_na.dat dirs 7-16) keeps its ball collision in a small file of its own
directory, not in the model block; main.dol ``0x806D99E0`` names it per stadium id and
variant (time of day 0/1/2) as ``{u16 dir, u16 file[3]}`` (``collision_routes``). Format
(main.dol fix-up ``fn_8038FB38``, query ``fn_8038FCB0`` / ``fn_8038F774``, 2026-10-18;
``_docs/_docs_model_format/stadium_collision.html``)::

    header   u16 regionCount N, u8 0x43, u8 relocated (0 in the file)
             u32 offset[N + 1]  file-relative
    section 0 (offset[0]): N boxes of 6 x f32  xmin ymin zmin xmax ymax zmax
    section i (offset[i], i = 1..N): region i's triangles, a list of strips
             u8 0, u8 kind, u16 count    kind 1: strip, count + 2 vertices
                                         kind 0: list, count * 3 vertices
             vertex = f32 x, f32 y, f32 z, u16 attr, u16 0
             ... repeated; a header with count 0 ends the region

Vanilla files are packed (no padding anywhere) and every box is the exact extent of its
region's vertices in file space. The file stores ``(x, -y, -z)`` of game/model space.
A triangle's attr is its last vertex's; triangles are one-sided (the ball hits the side
the right-hand normal of ``(v0, v1, v2)`` faces, the same in both spaces).

The writer always starts from the INPUT (vanilla) file, so writing again replaces the
earlier additions instead of stacking them; ``unpatch`` restores the vanilla route.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass

import HammerspaceHelper as hh

VERSION = 0x43
COLLISION_TABLE = 0x806D99E0  # {u16 dir, u16 file[3]} per stadium id 0-9
MODEL_TABLE = 0x806D9980      # the same shape for the stadium model files
STADIUM_COUNT = 10
VERTEX = struct.Struct('>3fHH')
STRIP_HEADER = struct.Struct('>BBH')
MAX_LIST_COUNT = 0xFFFF

# Low 7 bits of an attr (observed in the vanilla files), bit 0x80 = foul territory.
ATTR_GROUND, ATTR_FENCE, ATTR_WALL, ATTR_FOUL_POLE = 1, 2, 3, 4
ATTR_FOUL = 0x80


@dataclass
class Strip:
    kind: int     # 1 strip, 0 list
    count: int    # triangles
    verts: list   # (x, y, z, attr) in file space


def parse(data: bytes) -> tuple[list[tuple], list[list[Strip]]]:
    """Return ``(boxes, regions)``: boxes in file space, ``regions[i]`` a list of Strip."""
    n, version, _relocated = struct.unpack_from('>HBB', data, 0)
    if version != VERSION:
        raise ValueError(f'not a stadium collision file (version byte {version:#x})')
    offsets = struct.unpack_from(f'>{n + 1}I', data, 4)
    boxes = [struct.unpack_from('>6f', data, offsets[0] + 24 * i) for i in range(n)]
    regions = []
    for i in range(n):
        p = offsets[i + 1]
        strips = []
        while True:
            _, kind, count = STRIP_HEADER.unpack_from(data, p)
            p += STRIP_HEADER.size
            if count == 0:
                break
            nverts = count + 2 if kind else count * 3
            verts = [VERTEX.unpack_from(data, p + VERTEX.size * k)[:4] for k in range(nverts)]
            p += VERTEX.size * nverts
            strips.append(Strip(kind, count, verts))
        regions.append(strips)
    return boxes, regions


def serialize(boxes: list[tuple], regions: list[list[Strip]]) -> bytes:
    """Pack like the vanilla files: header, boxes, regions back to back."""
    if len(boxes) != len(regions):
        raise ValueError(f'{len(boxes)} boxes for {len(regions)} regions')
    n = len(regions)
    if n > 0xFFFF:
        raise ValueError(f'too many regions ({n})')
    bodies = []
    for strips in regions:
        out = bytearray()
        for strip in strips:
            expected = strip.count + 2 if strip.kind else strip.count * 3
            if not 0 < strip.count <= MAX_LIST_COUNT or len(strip.verts) != expected:
                raise ValueError(f'bad strip: kind {strip.kind}, count {strip.count}, {len(strip.verts)} vertices')
            out += STRIP_HEADER.pack(0, strip.kind, strip.count)
            for x, y, z, attr in strip.verts:
                out += VERTEX.pack(x, y, z, attr, 0)
        out += STRIP_HEADER.pack(0, 0, 0)
        bodies.append(bytes(out))
    offsets = [4 + 4 * (n + 1)]
    offsets.append(offsets[0] + 24 * n)
    for body in bodies[:-1]:
        offsets.append(offsets[-1] + len(body))
    head = struct.pack(f'>HBB{n + 1}I', n, VERSION, 0, *offsets)
    return head + b''.join(struct.pack('>6f', *box) for box in boxes) + b''.join(bodies)


def to_file_space(v) -> tuple[float, float, float]:
    # + 0.0 turns -0.0 into 0.0, so the same geometry always gives the same bytes
    return v[0] + 0.0, -v[1] + 0.0, -v[2] + 0.0


def region_from_triangles(triangles) -> tuple[tuple, list[Strip]]:
    """A new region from ``[(v0, v1, v2, attr), ...]`` in game/model space (winding kept:
    the ball hits the side the right-hand normal faces). Returns ``(box, strips)``."""
    tris = []
    for v0, v1, v2, attr in triangles:
        if not 0 <= attr <= 0xFFFF:
            raise ValueError(f'attr {attr} out of range')
        # every vertex gets the attr; the game reads the last one
        tris.append(tuple((*to_file_space(v), attr) for v in (v0, v1, v2)))
    if not tris:
        raise ValueError('a collision region needs at least one triangle')
    return region_box_of(tris), stripify(tris)


def stripify(tris) -> list[Strip]:
    """Join consecutive triangles into strips where each continues the previous one
    with the strip winding (k odd: ``v[k+1], v[k], v[k+2]``), as the vanilla files do;
    triangles left alone go into one list. Triangles listed in a vanilla strip's
    order come back as that strip (a strip carries one attr per vertex, the new
    vertex's attr being its triangle's)."""
    strips: list[list] = []
    singles = []
    current: list | None = None
    for tri in tris:
        if current is not None and len(current) - 2 < MAX_LIST_COUNT:
            k = len(current) - 2  # index of the triangle this one would be
            a, b = current[-2], current[-1]
            expected = (b, a) if k % 2 else (a, b)
            if _same_pos(tri[0], expected[0]) and _same_pos(tri[1], expected[1]):
                current.append(tri[2])
                continue
        if current is not None:
            (strips if len(current) > 3 else singles).append(current)
        current = list(tri)
    (strips if len(current) > 3 else singles).append(current)
    out = [Strip(1, len(s) - 2, s) for s in strips]
    flat = [v for s in singles for v in s]
    for i in range(0, len(flat), 3 * MAX_LIST_COUNT):
        chunk = flat[i:i + 3 * MAX_LIST_COUNT]
        out.append(Strip(0, len(chunk) // 3, chunk))
    return out


def _same_pos(u, v) -> bool:
    return u[0] == v[0] and u[1] == v[1] and u[2] == v[2]


def region_box_of(tris) -> tuple:
    vs = [v for tri in tris for v in tri]
    return tuple(min(v[k] for v in vs) for k in range(3)) + tuple(max(v[k] for v in vs) for k in range(3))


def region_box(strips: list[Strip]) -> tuple:
    vs = [v for strip in strips for v in strip.verts]
    return tuple(min(v[k] for v in vs) for k in range(3)) + tuple(max(v[k] for v in vs) for k in range(3))


def add_regions(data: bytes, triangle_groups) -> bytes:
    """*data* with one new region per entry of *triangle_groups* (each a list as in
    ``region_from_triangles``); the vanilla regions stay byte-identical."""
    boxes, regions = parse(data)
    for triangles in triangle_groups:
        box, strips = region_from_triangles(triangles)
        boxes.append(box)
        regions.append(strips)
    return serialize(boxes, regions)


def quad(a, b, c, d, attr: int) -> list:
    """Two triangles for the quad a-b-c-d (counter-clockwise seen from the hit side)."""
    return [(a, b, c, attr), (a, c, d, attr)]


def in_foul_territory(v0, v1, v2) -> bool:
    """Whether the triangle's centroid lies outside the foul lines (home plate at the
    origin, the outfield towards -z, the lines at 45 degrees). Every vanilla file sets
    bit 0x80 exactly on those triangles."""
    cx = (v0[0] + v1[0] + v2[0]) / 3.0
    cz = (v0[2] + v1[2] + v2[2]) / 3.0
    return not -cz >= abs(cx)


# --- the .sluggie field -----------------------------------------------------
# ``SluggiesModel.StadiumCollision``: a list (one region per entry) written by the
# Blender add-on (StadiumCollisionExport.py) for stadium models:
#   {"Name": str, "SurfaceType": 1..0x7F, "Sides": "front" | "both",
#    "Territory": "auto" | "fair" | "foul",
#    "Positions": [[x, y, z], ...] (model space), "Faces": [[a, b, c], ...]}
# Faces are wound counter-clockwise seen from the side the ball hits ("front").
# An empty list means "vanilla collision"; a file without the field leaves it alone.
# An entry with ``"Region": i`` is (an edited copy of) part of vanilla region i: as
# soon as one entry has it, the file is REBUILT from the entries (replace mode:
# vanilla parts without an entry are deleted, entries of one region share it again,
# the others get a region each); without it every entry is appended to the vanilla file.
#
# ``SluggiesModel.StadiumCollisionVanilla`` (written by export.py, ``vanilla_entries``):
# the vanilla collision of the model in that same entry shape, one entry per region
# and attr (Sides front, Territory fair/foul from bit 0x80), for the Blender importer.
FIELD = 'StadiumCollision'
FIELD_VANILLA = 'StadiumCollisionVanilla'
# Set by the add-on when the scene holds original colliders: rebuild even when all
# of them were deleted or unticked (then only the user's own colliders remain).
REPLACE_FIELD = 'StadiumCollisionReplace'
SIDES = ('front', 'both')
TERRITORIES = ('auto', 'fair', 'foul')
TOY_FIELD_DIR = 16


def entry_triangles(entry: dict) -> list:
    """``[(v0, v1, v2, attr), ...]`` in game space for one ``StadiumCollision`` entry."""
    name = entry.get('Name', '?')
    surface_type = entry.get('SurfaceType')
    if not isinstance(surface_type, int) or not 1 <= surface_type <= 0x7F:
        raise ValueError(f'collision object {name}: SurfaceType must be 1..127, not {surface_type!r}')
    sides = entry.get('Sides', 'both')
    territory = entry.get('Territory', 'auto')
    if sides not in SIDES:
        raise ValueError(f'collision object {name}: Sides must be one of {SIDES}, not {sides!r}')
    if territory not in TERRITORIES:
        raise ValueError(f'collision object {name}: Territory must be one of {TERRITORIES}, not {territory!r}')
    positions = [tuple(float(c) for c in p) for p in entry.get('Positions') or []]
    if any(len(p) != 3 or not all(c == c and abs(c) < 1e6 for c in p) for p in positions):
        raise ValueError(f'collision object {name}: every position needs three finite numbers')
    triangles = []
    for face in entry.get('Faces') or []:
        if len(face) != 3 or not all(isinstance(i, int) and 0 <= i < len(positions) for i in face):
            raise ValueError(f'collision object {name}: bad face {face!r} ({len(positions)} positions)')
        v0, v1, v2 = (positions[i] for i in face)
        foul = territory == 'foul' or (territory == 'auto' and in_foul_territory(v0, v1, v2))
        attr = surface_type | (ATTR_FOUL if foul else 0)
        triangles.append((v0, v1, v2, attr))
        if sides == 'both':
            triangles.append((v0, v2, v1, attr))
    if not triangles:
        raise ValueError(f'collision object {name}: no faces')
    return triangles


def collision_files_for_model(chunk_number: int, model_file: int, dol_path: str = hh.INPUT_DOL) -> list[int]:
    """The collision files loaded with stadium model *model_file* of dir *chunk_number*
    (every variant whose model is that file; day and night usually share one)."""
    from Dol import dolfile
    if chunk_number == TOY_FIELD_DIR:
        raise ValueError('Toy Field (dir 16) collision is not supported: its tiles have their own '
                         'collision objects')
    with open(dol_path, 'rb') as f:
        image = dolfile.DolImage(f.read())
    collision = image.read(COLLISION_TABLE, 8 * STADIUM_COUNT)
    models = image.read(MODEL_TABLE, 8 * STADIUM_COUNT)
    for i in range(STADIUM_COUNT):
        d, *model_files = struct.unpack_from('>4H', models, 8 * i)
        cd, *collision_files = struct.unpack_from('>4H', collision, 8 * i)
        if d != chunk_number:
            continue
        files = sorted({c for m, c in zip(model_files, collision_files) if m == model_file})
        if files and cd == d:
            return files
        raise ValueError(f'dir {chunk_number} file {model_file} is not a stadium model the game loads')
    raise ValueError(f'dir {chunk_number} is not a stadium directory (7-15)')


def is_replace(entries) -> bool:
    return any(entry.get('Region') is not None for entry in entries)


def build_from_entries(entries) -> bytes:
    """A whole collision file from ``StadiumCollision`` entries (replace mode): the
    entries of one ``Region`` share a region (in region order), every other entry
    gets its own."""
    grouped: dict[int, list] = {}
    loose = []
    for entry in entries:
        region = entry.get('Region')
        if region is None:
            loose.append(entry_triangles(entry))
        else:
            if not isinstance(region, int) or region < 0:
                raise ValueError(f"collision object {entry.get('Name', '?')}: bad Region {region!r}")
            grouped.setdefault(region, []).extend(entry_triangles(entry))
    boxes, regions = [], []
    for triangles in [grouped[r] for r in sorted(grouped)] + loose:
        box, strips = region_from_triangles(triangles)
        boxes.append(box)
        regions.append(strips)
    return serialize(boxes, regions)


def build_for_model(model: dict) -> dict[int, bytes]:
    """``{collision file index: new file bytes}`` for a .sluggie model with a non-empty
    ``StadiumCollision`` field: appended to the vanilla file, or the whole file rebuilt
    when an entry carries a vanilla ``Region`` (``is_replace``)."""
    entries = model.get(FIELD) or []
    files = collision_files_for_model(model['ChunkNumber'], model['FileIndex'])
    if model.get(REPLACE_FIELD) or is_replace(entries):
        data = build_from_entries(entries)
        return {f: data for f in files}
    groups = [entry_triangles(entry) for entry in entries]
    return {f: add_regions(read_vanilla(model['ChunkNumber'], f), groups) for f in files}


SURFACE_NAMES = {1: 'ground', 2: 'fence', 3: 'stands', 4: 'foul_pole', 5: 'wall', 6: 'dirt'}


def triangles_game(strips):
    """``(v0, v1, v2, attr)`` in game space with the hit side's winding (strips alternate)."""
    for strip in strips:
        for k in range(strip.count):
            if strip.kind:
                a, b, c = strip.verts[k], strip.verts[k + 1], strip.verts[k + 2]
                if k % 2:
                    a, b = b, a
            else:
                a, b, c = strip.verts[3 * k], strip.verts[3 * k + 1], strip.verts[3 * k + 2]
            yield to_file_space(a), to_file_space(b), to_file_space(c), c[3]


def vanilla_entries(data: bytes) -> list[dict]:
    """The collision file *data* as ``StadiumCollision`` entries (game space), one per
    region and attr, in file order; ``build_from_entries`` turns them back into an
    equivalent file (same triangles, attrs and regions; strips become lists)."""
    _boxes, regions = parse(data)
    entries = []
    for r, strips in enumerate(regions):
        by_attr: dict[int, list] = {}
        for a, b, c, attr in triangles_game(strips):
            by_attr.setdefault(attr, []).append((a, b, c))
        for attr, tris in by_attr.items():
            index: dict[tuple, int] = {}
            faces = [[index.setdefault(tuple(v), len(index)) for v in tri] for tri in tris]
            kind = attr & 0x7F
            foul = bool(attr & ATTR_FOUL)
            label = SURFACE_NAMES.get(kind, f'type{kind}') + ('_foul' if foul else '')
            entries.append({
                'Name': f'col{r:02d}_{label}', 'Region': r, 'SurfaceType': kind,
                'Sides': 'front', 'Territory': 'foul' if foul else 'fair',
                'Positions': [list(v) for v in index], 'Faces': faces,
            })
    return entries


def vanilla_entries_for_model(chunk_number: int, model_file: int):
    """``StadiumCollisionVanilla`` for a stadium model, or None (not a stadium the game
    loads, Toy Field, or several collision files)."""
    try:
        files = collision_files_for_model(chunk_number, model_file)
    except ValueError:
        return None
    if len(files) != 1:
        return None
    return vanilla_entries(read_vanilla(chunk_number, files[0]))


def apply_model(model: dict, restore: bool = False, dry_run: bool = False, name: str = '') -> list[str]:
    """Patch the collision of a stadium .sluggie model, or restore it (*restore*, or
    an empty field). Nothing happens without the field. Returns report lines."""
    if FIELD not in model:
        return []
    chunk = model['ChunkNumber']
    lines = []
    if model.get(REPLACE_FIELD) and not model[FIELD] and not restore:
        raise ValueError('every collider of the stadium was removed: the ball would fall through '
                         'the ground. Keep at least the ground, or re-import to start over.')
    if restore or not model[FIELD]:
        for f in collision_files_for_model(chunk, model['FileIndex']):
            if not current_is_patched(chunk, f):
                lines.append(f'dir {chunk} collision file {f}: vanilla, nothing to restore')
            elif dry_run:
                lines.append(f'dir {chunk} collision file {f}: would be restored (dry run)')
            elif unpatch(chunk, f):
                lines.append(f'dir {chunk} collision file {f}: restored')
            else:
                raise RuntimeError(f'dir {chunk} collision file {f}: restore failed (see the log)')
        return lines
    mode = 'rebuilt from' if model.get(REPLACE_FIELD) or is_replace(model[FIELD]) else 'vanilla +'
    for f, data in build_for_model(model).items():
        regions = len(parse(data)[1])
        added = len(model[FIELD])
        if dry_run:
            lines.append(f'dir {chunk} collision file {f}: {mode} {added} object(s) -> {regions} regions, '
                         f'{len(data):,} bytes (dry run)')
        else:
            offset = write(chunk, f, data, f'{name or "stadium"} collision')
            lines.append(f'dir {chunk} collision file {f}: {mode} {added} object(s) -> {regions} regions, '
                         f'{len(data):,} bytes at 0x{offset:08X}')
    return lines


# --- routes -----------------------------------------------------------------

def collision_routes(dol_path: str = hh.INPUT_DOL) -> list[tuple[int, tuple[int, int, int]]]:
    """``[(dir, (file_variant0, file_variant1, file_variant2)), ...]`` per stadium id."""
    from Dol import dolfile
    with open(dol_path, 'rb') as f:
        image = dolfile.DolImage(f.read())
    raw = image.read(COLLISION_TABLE, 8 * STADIUM_COUNT)
    rows = [struct.unpack_from('>4H', raw, 8 * i) for i in range(STADIUM_COUNT)]
    return [(d, tuple(files)) for d, *files in rows]


def collision_files_of_dir(chunk_number: int) -> list[int]:
    """The collision file indices the game loads from stadium dir *chunk_number*."""
    for d, files in collision_routes():
        if d == chunk_number:
            return sorted(set(files))
    raise ValueError(f'dir {chunk_number} is not a stadium directory (7-16)')


def read_vanilla(chunk_number: int, file_index: int) -> bytes:
    offset, length = hh.readDolEntry(chunk_number, file_index)
    if offset < 0:
        raise ValueError(f'no DOL entry for dir {chunk_number} file {file_index}')
    with open(hh.INPUT_DAT, 'rb') as f:
        f.seek(offset)
        data = f.read(length)
    parse(data)  # refuses anything that is not a collision file
    return data


def write(chunk_number: int, file_index: int, data: bytes, name: str) -> int:
    """Write the collision file *data* to hammerspace and route the entry (and every
    entry sharing its vanilla range) to it. Returns the new DAT offset."""
    if file_index not in collision_files_of_dir(chunk_number):
        raise ValueError(f'dir {chunk_number} file {file_index} is not a collision file the game loads')
    parse(data)
    import HammerspaceMain
    if not hh.ensureOutputDat():
        raise RuntimeError('Unable to prepare output dt_na.dat')
    return HammerspaceMain._write_route(data, chunk_number, file_index, name,
                                        log_suffix=' | Stadium collision')


def unpatch(chunk_number: int, file_index: int) -> bool:
    """Restore the vanilla collision route; False when it was not in hammerspace."""
    ok, *_ = hh.removeModelFromHammerspace(chunk_number, file_index)
    return ok


def current_is_patched(chunk_number: int, file_index: int) -> bool:
    if not os.path.exists(hh.OUTPUT_DOL):
        return False
    offset, _length = hh.readOutputDolEntry(chunk_number, file_index)
    return offset >= hh.BASE_SIZE


def main(argv=None) -> int:
    import argparse
    import slogger
    from binfmt import load_sluggie
    parser = argparse.ArgumentParser(description="Write (or restore) a stadium .sluggie's StadiumCollision.")
    parser.add_argument('sluggie', help='the stadium model .sluggie')
    parser.add_argument('--unpatch', action='store_true', help='restore the vanilla collision')
    parser.add_argument('--dry-run', action='store_true', help='build and check, write nothing')
    parser.add_argument('--add-vanilla-field', action='store_true',
                        help=f'write {FIELD_VANILLA} into the .sluggie (for exports made before it existed)')
    args = parser.parse_args(argv)
    slogger.configure()
    try:
        if args.add_vanilla_field:
            from binfmt import dump_sluggie
            document = load_sluggie(args.sluggie)
            model = document['SluggiesModel']
            entries = vanilla_entries_for_model(model['ChunkNumber'], model['FileIndex'])
            if entries is None:
                raise ValueError('this model has no single vanilla collision file to copy')
            model[FIELD_VANILLA] = entries
            dump_sluggie(document, args.sluggie)
            slogger.info(f'{args.sluggie}: {FIELD_VANILLA} = {len(entries)} entries',
                         source='hammerspace.collision')
            return 0
        model = load_sluggie(args.sluggie)['SluggiesModel']
        name = os.path.splitext(os.path.basename(args.sluggie))[0]
        for line in apply_model(model, restore=args.unpatch, dry_run=args.dry_run, name=name):
            slogger.info(line, source='hammerspace.collision')
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        slogger.error(f'Stadium collision: {exc}', source='hammerspace.collision')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
