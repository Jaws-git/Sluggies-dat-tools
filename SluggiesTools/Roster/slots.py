"""Which model directory a character ID's slot loads from (patch a slot).

``model_dir(image, cid)`` resolves an ID the way the game does on the
output DOL:

* stock IDs (0x00-0x64): directory ``id + 0x12`` (``ids.MODEL_DIR_BASE``),
  also on an expanded roster, whose directory map keeps them in place;
* new IDs (0x66-0xFE) with an own model directory (``model_dirs`` step,
  listed in the roster manifest): that directory;
* other new IDs load their **template's** directory (``dirmap[id]``), so
  patching one would change the template too: refused (give the ID an own
  directory first, ``ids[].model``).

Anything else (0x65, IDs the roster does not have, a DOL it did not build) is
refused with ``SlotError``, never guessed.
"""

try:
    from ..Dol import dolfile
    from . import dol_hammerspace, ids, manifest
except ImportError:
    from Dol import dolfile
    import dol_hammerspace
    import ids
    import manifest


class SlotError(ValueError):
    pass


def _hex(cid: int) -> str:
    return f'0x{cid:02X}'


def parse_id(text: str | int) -> int:
    """``"0x4A"`` / ``"74"`` / 74 -> 74; refuses values outside one byte."""
    try:
        cid = text if isinstance(text, int) else int(str(text), 0)
    except ValueError:
        raise SlotError(f'{text!r} is not a character ID (e.g. 0x4A)') from None
    if not 0 <= cid <= 0xFF:
        raise SlotError(f'{text!r} is not a character ID (0x00-0xFE)')
    return cid


def _manifest(image: dolfile.DolImage) -> dict:
    """The roster manifest; empty for a stock DOL."""
    try:
        hs = dol_hammerspace.DolHammerspace.open(image)
    except dolfile.DolError as exc:
        raise SlotError(f'the output main.dol is not a roster this tool built: {exc}') from exc
    if hs is None or not hs.has_data:   # no sections, or only game option stubs (CPU vs CPU)
        return {}
    try:
        mf = manifest.find(bytes(hs.data.blob))
    except manifest.ManifestError as exc:
        raise SlotError(str(exc)) from exc
    if mf is None:
        raise SlotError('this roster was built by an older version of the roster tool (no manifest): run the '
                        'roster again (menu [7]) first')
    return mf


def new_ids(image: dolfile.DolImage) -> dict[int, int]:
    """``{new id: template}`` from the roster manifest; empty for a stock DOL."""
    return {c[0]: c[1] for c in _manifest(image).get('ids', [])}


def own_dirs(image: dolfile.DolImage) -> dict[int, tuple[int, int]]:
    """``{new id: (own model directory, source id)}`` from the roster manifest."""
    return {c[0]: (c[1], c[2]) for c in _manifest(image).get('model_dirs') or []}


def model_dir(image: dolfile.DolImage, cid: int) -> int:
    """The model directory the slot ``cid`` loads, when a patch may write into it (module docstring)."""
    if 0 <= cid < ids.STOCK_IDS:
        return cid + ids.MODEL_DIR_BASE
    own = own_dirs(image)
    if cid in own:
        return own[cid][0]
    templates = new_ids(image)
    if cid in templates:
        template = templates[cid]
        raise SlotError(
            f'{_hex(cid)} is a new ID without a model directory of its own: it loads its template '
            f'{_hex(template)}\'s (dir {template + ids.MODEL_DIR_BASE}), so a patch would change '
            f'{_hex(template)} too. Give it an own model directory first.')
    raise SlotError(f'{_hex(cid)} is not a character of this roster')
