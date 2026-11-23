"""Adapt Model: the table of its steps (``_ADAPT_STEPS``: label, description, order), read by every step's
operator for its tooltip and by the panel for the box, and the summary report the steps share.

Imports no tools module. Never imports ``SluggiesToolsPanel``.
"""

import bpy


# The 'Adapt' box: guided steps for porting a model from elsewhere onto a
# Sluggers donor, in order. (step id, button text, tooltip). Steps without an
# entry in _ADAPT_STEP_OPERATORS only report that they are not implemented.
_ADAPT_TRANSFORM_DESCRIPTION = (
    "Turn, scale (evenly) and move the selected objects together so their meshes stand on the Sluggies "
    "armature they were assigned to: upright, facing its front, head at its model's height. A rough "
    "start for manual fitting")


_ADAPT_STEPS = (
    ('CLEANUP', "Clean Up",
     "On meshes not imported from a .sluggie (all of them, or the selected ones; a selected "
     "non-Sluggies armature includes its children): remove smoothing and mask modifiers, physics "
     "simulations, loose vertices and edges, empty vertex groups and unused material slots. "
     "Duplicate vertices are not merged (use Merge by Distance yourself if needed). "
     "Materials themselves stay as they are"),
    ('ARMATURE', "Assign to Armature",
     "Parent the meshes not imported from a .sluggie (all of them, or the selected ones; a selected "
     "non-Sluggies armature includes its children) to the "
     "Sluggies armature you pick, keeping where they are, and point their Armature modifiers at it"),
    # Hidden for now; SLUGGIES_OT_adapt_transform is kept unregistered. To bring
    # it back, uncomment this entry, its _ADAPT_STEP_OPERATORS entry and its
    # register/unregister lines.
    # ('TRANSFORM', "Scale and Position", _ADAPT_TRANSFORM_DESCRIPTION),
    ('MESHES', "Join or Replace Meshes",
     "For the Sluggies armature selected (or above the selected object): join each added mesh into the "
     "main body, let one replace it, add it as a rigid submesh on a free bone, or ignore it"),
    ('WEIGHTS', "Map Weights or Autorig",
     "For the Sluggies armature selected (or above the selected object): map the main body's vertex "
     "groups from its old rig onto the Sluggers bones, or weight it automatically"),
    ('MATERIALS', "Convert Materials",
     "For the Sluggies armature selected (or above the selected object): pick the shader mode and "
     "specular strength of each new material of the main body and rigid submeshes, turn them into "
     "Sluggers surfaces with a PNG texture, and remove the nodes that do not feed a shader's colour. "
     "In a saved .blend, the textures the models use are then copied to <blend name>_Tex next to it"),
    ('LOWPOLY', "Generate Lowpoly",
     "For the High model selected (or above the selected object), with its Low (_L_) model imported too: "
     "copy its meshes to the Low model, each left out, unchanged or decimated. The main body replaces "
     "the Low model's body, a rigid mesh the Low model's mesh on its bone"),
    # Opens the regular Check Models dialog (its own tooltip shows).
    ('CHECK', "Check For Problems", "Check the models in the scene for problems"),
    # Shown only while the selected model's High and Low are both imported.
    ('EXPORT', "Export (High+Low)",
     "For the High or Low model selected (or above the selected object), with its partner imported too: "
     "export both into .sluggie files of one name, the High model first. Every visible mesh of each "
     "model is exported; hidden or deleted original meshes are left out of the game"),
)


def _report_adapt_summary(op, context, title, lines, warnings=()):
    """*lines* (and *warnings*, marked) in the status bar and a popup."""
    op.report({"WARNING"} if warnings else {"INFO"}, "; ".join(list(lines) + list(warnings)))

    def draw_summary(menu, _context):
        for line in lines:
            menu.layout.label(text=line)
        for line in warnings:
            menu.layout.label(text=line, icon='ERROR')
    if not bpy.app.background:  # a popup crashes a background (no UI) Blender
        context.window_manager.popup_menu(draw_summary, title=title, icon='INFO')
