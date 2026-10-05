"""Roster manifest: the facts of a roster run that exist only inside hook code, kept in the DOL data section.

The grid reader (``state.py``) reads most of a roster back from the binary:
the grid shape, the square -> head map, the head list, the selector rows,
the names. A few facts are only compiled into hooks: the member lists of new
squares, the wheel order, the new IDs' templates, where ``portrait_of``
lives, which new IDs have own model directories (and from whom), stats
sources and square voices as configured. The runner stores those here, as a zlib-compressed JSON blob behind
``MAGIC`` in the roster's DOL data section:

    MAGIC (16 bytes) | u32 compressed length | zlib(JSON)

The reset drops it with the rest of the data section. The reader
cross-checks every fact it can also read from the binary; on a mismatch the
binary wins.
"""

import json
import struct
import zlib

MAGIC = b'SLUGGIES ROSTER\x03'
VERSION = 1
# The config's top-level keys whose presence alone changes the build (``derive`` needs them back): "ids" moves the
# per-ID tables even when empty, "wheels" owns the spare rows even when empty.
CONFIG_KEYS = ('ids', 'wheels', 'grid', 'wheel_order')


class ManifestError(ValueError):
    pass


def build(state: dict, config: dict | None = None) -> dict:
    """The manifest for one run, from the steps' ``ctx.state`` (and the config's keys, ``CONFIG_KEYS``).

    ``config_keys`` and ``spare_wheels`` (``[id, wheel, swatch]`` as configured) serve the read -> rebuild round
    trip (``derive.py``), which infers them for manifests written before they existed."""
    grid = state.get('grid')
    out = {
        'config_keys': None if config is None else [k for k in CONFIG_KEYS if k in config],
        'spare_wheels': [[cid, w, s] for cid, (w, s) in sorted((state.get('spare_wheels') or {}).items())],
        'version': VERSION,
        'grid': None if grid is None else {
            'shape': [grid.cols, grid.rows],
            'cells': [None if c is None else list(c) for c in grid.cells],
            'squares': [list(sq) for sq in grid.squares],
        },
        'ids': [[c.id, c.template, c.wheel, c.swatch] for c in state.get('new_ids') or []],
        'spares': sorted(state.get('spares') or []),
        'wheel_order': [[s, list(o)] for s, o in state.get('wheel_order') or []],
        'names': {str(cid): dict(n) for cid, n in sorted((state.get('names') or {}).items())},
        'portrait_of': state.get('portrait_of'),
    }
    if state.get('model_dirs'):                # own model directories (model_dirs step): [id, directory, source id]
        out['model_dirs'] = [[cid, d, src] for cid, (d, src) in sorted(state['model_dirs'].items())]
    # Omitted when unused, so configs without them keep their bytes.
    stats = [[c.id, c.stats] for c in state.get('new_ids') or [] if c.stats is not None]
    stats += [[cid, src] for cid, src in sorted((state.get('stock_stats') or {}).items())]
    if stats:                                  # stats sources: [id, stock id] (new IDs, then stock_stats)
        out['stats'] = sorted(stats)
    if state.get('voice_remap'):               # stock squares' voices: [species, voice species]
        out['stock_voices'] = [[s, v] for s, v in sorted(state['voice_remap'].items())]
    if grid is not None and grid.voices:       # square voices, per square: a character ID or None
        out['grid']['voices'] = list(grid.voices)
    return out


def encode(manifest: dict) -> bytes:
    raw = json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    packed = zlib.compress(raw, 9)
    return MAGIC + struct.pack('>I', len(packed)) + packed


def find(blob: bytes) -> dict | None:
    """The manifest in a data-section blob (4-aligned), or None."""
    at = 0
    while True:
        at = blob.find(MAGIC, at)
        if at < 0:
            return None
        if at % 4 == 0:
            break
        at += 1
    start = at + len(MAGIC) + 4
    if start > len(blob):
        raise ManifestError('roster manifest is cut off')
    length = struct.unpack_from('>I', blob, at + len(MAGIC))[0]
    try:
        manifest = json.loads(zlib.decompress(blob[start:start + length]).decode('utf-8'))
    except (zlib.error, ValueError) as exc:
        raise ManifestError(f'roster manifest is damaged: {exc}') from exc
    if not isinstance(manifest, dict) or manifest.get('version') != VERSION:
        raise ManifestError(f'roster manifest version {manifest.get("version") if isinstance(manifest, dict) else "?"}'
                            f' is not {VERSION}')
    return manifest
