"""Pure-Python encoding for stadium collision objects (``StadiumCollision``).

bpy-free, like CustomSubmeshExport.py, so the rules can be unit-tested without
Blender. Any mesh object with collision turned on in the Sluggies panel
(``Object.sluggies_collision``) becomes one entry: its faces in model space, wound
counter-clockwise seen from the side the ball hits (Blender's face normal side).
The patcher (SluggiesTools/Hammerspace/StadiumCollision.py) turns each entry into a
new region of the stadium's collision file, sets the foul-territory bit per
triangle (``auto``: by the foul lines, like every vanilla file) and adds the back
faces for ``both``.

Surface types (low 7 bits of the game's attribute; Dolphin 2026-10-18 for 2 and 3):
the ball bounces off type 2 like off the outfield fence and stays in play; touching
type 3 (the stands) is a home run wherever it is.
"""

import math

try:  # inside Blender this module is part of the addon package
    from . import CustomSubmeshExport
    from . import TemplateSources
except ImportError:  # imported flat by the unit tests
    import CustomSubmeshExport
    import TemplateSources

FIELD = 'StadiumCollision'
# Mirror StadiumCollision.FIELD_VANILLA / REPLACE_FIELD (the patcher's names).
FIELD_VANILLA = 'StadiumCollisionVanilla'
REPLACE_FIELD = 'StadiumCollisionReplace'
# Object property of an imported original collider: its vanilla region index.
REGION_PROP = 'SluggiesCollisionRegion'
# Mirrors StadiumCollision.TOY_FIELD_DIR (pinned by tests/test_stadium_collision_export.py).
TOY_FIELD_CHUNK = 16

# (identifier, surface type, label, description); the first is the default.
SURFACE_TYPES = (
    ('FENCE', 2, "Fence (bounces off)",
     "Type 2, the outfield fence: the ball bounces back and stays in play"),
    ('STANDS', 3, "Home run)",
     "Type 3, A batted ball that touches it is a home run, wherever it is"),
    ('GROUND', 1, "Ground (grass)", "Type 1, grass and general field ground"),
    ('DIRT', 6, "Infield dirt", "Type 6, infield dirt and mound"),
    ('WALL', 5, "Wall (type 5)", "Type 5, Fence type of Peach, Wario and Luigi stadiums (untested)"),
    ('FOUL_POLE', 4, "Foul pole", "Type 4, the foul poles"),
    ('CUSTOM', None, "Custom number", "Any surface type number 1-127"),
)
SURFACE_TYPE_NUMBERS = {ident: number for ident, number, _label, _desc in SURFACE_TYPES}
SIDES = (
    ('BOTH', 'both', "Both sides", "The ball bounces off either side"),
    ('FRONT', 'front', "Front only, ball can pass through the back",
     "Only the side the face normals point to (a closed mesh with outward normals needs no more)"),
)
TERRITORIES = (
    ('AUTO', 'auto', "Auto (by foul lines)", "Fair or foul per face, from where it lies (like the game's own data)"),
    ('FAIR', 'fair', "Fair", "Every face counts as fair territory"),
    ('FOUL', 'foul', "Foul", "Every face counts as foul territory"),
)
_SIDE_VALUES = {ident: value for ident, value, _label, _desc in SIDES}
_TERRITORY_VALUES = {ident: value for ident, value, _label, _desc in TERRITORIES}


def surface_type_number(identifier, custom_number):
    number = SURFACE_TYPE_NUMBERS.get(identifier)
    if identifier == 'CUSTOM':
        number = int(custom_number)
    if number is None or not 1 <= number <= 0x7F:
        raise ValueError(f"surface type must be 1-127, not {custom_number if identifier == 'CUSTOM' else identifier}")
    return number


def model_space_geometry(vertex_cos, triangles, obj_world, arm_world):
    """Model-space positions and faces of a mesh: the custom-submesh transform with
    the armature as the host (identity bind), so the importer's axis rotation and
    any move of the whole armature cancel out. Mirrored objects get their winding
    reversed, so the front stays the side the Blender normals show."""
    geometry = CustomSubmeshExport.bone_local_geometry(
        vertex_cos, triangles, obj_world, arm_world, CustomSubmeshExport.IDENTITY4)
    return geometry.positions, geometry.faces


def collision_entry(name, positions, faces, surface_type, sides='BOTH', territory='AUTO', region=None):
    """One ``StadiumCollision`` entry (plain JSON). Degenerate faces are dropped.
    *region*: the vanilla region an imported original collider came from."""
    if sides not in _SIDE_VALUES or territory not in _TERRITORY_VALUES:
        raise ValueError(f"{name}: unknown collision sides/territory {sides!r}/{territory!r}")
    if not 1 <= int(surface_type) <= 0x7F:
        raise ValueError(f"{name}: surface type must be 1-127, not {surface_type}")
    rounded = [[round(float(c), 4) for c in p] for p in positions]
    if any(not all(math.isfinite(c) for c in p) for p in rounded):
        raise ValueError(f"{name}: a collision vertex is not a finite number")
    kept = [list(face) for face in faces if len({tuple(rounded[i]) for i in face}) == 3]
    if not kept:
        raise ValueError(f"{name}: collision object has no faces")
    used = sorted({i for face in kept for i in face})
    remap = {old: new for new, old in enumerate(used)}
    entry = {
        'Name': name,
        'SurfaceType': int(surface_type),
        'Sides': _SIDE_VALUES[sides],
        'Territory': _TERRITORY_VALUES[territory],
        'Positions': [rounded[i] for i in used],
        'Faces': [[remap[i] for i in face] for face in kept],
    }
    if region is not None:
        entry['Region'] = int(region)
    return entry


def import_entries(model):
    """The collision entries the importer builds objects for: the file's own
    ``StadiumCollision`` (an earlier export), plus the stadium's original
    collision (``StadiumCollisionVanilla``) unless that export already rebuilt it."""
    own = list(model.get(FIELD) or [])
    if model.get(REPLACE_FIELD) or any(e.get('Region') is not None for e in own):
        return own
    return list(model.get(FIELD_VANILLA) or []) + own


def chunk_error(chunk_number, object_names):
    """Why collision objects can't go into this model, or ''."""
    if not object_names:
        return ''
    names = ', '.join(object_names)
    if not TemplateSources.is_stadium_chunk(chunk_number):
        return (f"{names}: stadium collision is on, but this model is not a stadium. "
                "Turn collision off for these objects or export them into a stadium model.")
    if chunk_number == TOY_FIELD_CHUNK:
        return f"{names}: Toy Field collision is not supported (its tiles use their own collision objects)."
    return ''


def invisible_model_mesh_error(object_names):
    """Why "Make invisible" can't apply to these original model meshes, or ''.
    Only a custom submesh or a plain mesh can be left out of the drawn model;
    hiding an original stadium mesh would need a patcher change."""
    if not object_names:
        return ''
    return (f"{', '.join(object_names)}: 'Make invisible' works only on new submeshes and plain "
            "meshes, not on the stadium's original meshes. Untick it, or use a copy of the "
            "mesh (a plain mesh) as the invisible collider.")
