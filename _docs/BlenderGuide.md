# Blender Editing Guide

One sluggies file can contain several numbered submeshes.

Patching your game can be done in two different modes:
- In-Place patching
- Hammerspace patching

Mode is selected by setting the first checkbox when exporting back to the sluggie file from blender.

In-place overwrites the original data at its original location and is the most compatible, e.g. with real Wii hardware.
Hammerspace opens up additional memory at the end of the data file to store more data than the original would allow. May  be incompatible with original hardware under certain conditions.

## Import
1. Make sure the plugin is installed by going to Edit->Preference->Add-ons->Install from Disk (upper right corner drop down menu)  
2. Select the addon zip file as is, do not unpack it.  
3. After the plugin is installed, import one .sluggie file using File->Import->Sluggers intermediate (.sluggie)  

## Export

1. Select all submeshes you want to write back to the sluggie file in the viewport
2. File->Export->Sluggers intermediate (.sluggie)  
3. select **the same .sluggie file for the character you imported earlier**. The updated file will contain both original and edited model data now.

*SAVE YOUR EDITS AS .BLEND PROJECTS FOR SAFEKEEPING!*  
*Pro Tip: you can add the import/export menus to your quick favorites by right clicking them. Then press "q" (default) to see all your quick favorites.*

#### Exporter Options:
[] Include Custom Split Normals - When off, writes averaged blender normals. When on, Writes custom split normals data.   
[] Reimport textures from tex folder - write the PNG files found in the exports back into the game files, including any edits made to them. Needed for new textures and custom submeshes.

#### In-place or Hammerspace: chosen automatically
The exporter picks the mode for you. It writes your edits **in place** (over the original model data) whenever they fit there, and switches to **Hammerspace** when an edit needs a rebuilt model. The export always reports its choice, e.g. `Export mode: Hammerspace, needed for: Hat: UV seams split`.

Hammerspace is used for:
- custom submeshes and bones added with **Add Bone**;
- texture changes on materials, and PNGs whose size changed (with Reimport textures on);
- added or removed vertices, and changed, added or removed faces;
- split UV seams;
- edited vertex colours;
- faces moved to another material;
- vertices moved between bones, or skin data that no longer fits its original size;
- normals that split where the original model shares one normal (Overwrite Normals);
- the unused characters (folders 89-94);
- earlier Hammerspace edits on parts you didn't select this time, so they aren't lost.

Everything else, such as moving vertices, editing UVs without new seams, shape keys, specular strength or a same-size PNG edit, stays in place. A model that is in hammerspace from an earlier patch and now exports in place is moved back automatically when you patch it.

## Exporter capabilities and restrictions
### In-Place mode
#### You can:
- change the position of existing verts in space
- edit face normals (each model is imported with its original custom normals, where available)
- edit facial expressions (shapekeys)
- change the position of existing UVs 
- in case of multiple UVs concentrated in one single point, you can move the whole "unit" around as one
- reimport edited PNG textures as long as their dimensons didn't change
- change a material's specular (gloss) strength with **Set Specular Strength** (0 = no highlight, 255 = maximum; linear)
  >- only the strength can be changed. The highlight's shape comes from a small sphere-map texture that many models share, so editing that PNG changes every model using it.
  >- editing the second UV map does nothing: the game computes the specular texture coordinates from the vertex normals
#### You can't:
- add or remove vertices
- manipulate material slots
- manipulate bones or skinning data
- reorder face indices
- remove an object's custom properties
- you should also always refrain from renaming objects
- create new UV seams
- create or remove UV faces
- split up a connected UV edge

### Hammerspace mode
#### You can:
- change the position of existing verts in space
- edit face normals (each model is imported with its original custom normals, where available)
- edit facial expressions (shapekeys)  
- change UVs in any way you want. split edges, unwrap, make new seams!
- reimport edited PNG textures at any size!  
- change a material's specular strength (see In-Place mode)
- append a new model texture by changing the connected Image Texture node on an imported donor material (not yet on player characters, see the warning below)
>- create a new static submesh that has it's own texture and follows a bone of your choice

#### Adding a new model texture how-to

1. Put the new PNG in the model's own `tex/` folder.
2. Keep the imported material and its `SurfaceId`; do not create a new material.
3. Replace the image in the Image Texture node or create a new one.
   Leave at most one Image Texture node connected to the active Material Output!
4. Export with **Reimport textures from tex folder** enabled. The export uses Hammerspace by itself.
5. Patch the exported `.sluggie` normally.

- Only Image Texture nodes connected to the Material Output count. Unconnected helper nodes are ignored, and a material with no connected image keeps its original texture.
- If more than one Image Texture node is connected, export stops and names the material.
- If a texture change is found but the option from step 4 is off, export stops before writing anything and lists the materials.
- The PNG is looked up only in this model's own `tex/` folder. If it's missing, the export stops.
- Choosing a PNG the model already has (another texture from its `tex/` folder) just points the material at that texture. Nothing new is added.
- The same PNG on several materials is stored only once.

> [!WARNING]
> **Not yet possible on player characters.** Every player character has a low-poly (`L_`) partner model that owns no textures and draws with the high-poly model's textures. If the high-poly model moves a material to another texture, some matching low-poly surfaces must follow, or the game crashes as soon as the low-poly model loads in a match. Character select still works, because it shows only the high-poly model. Those surfaces can't be matched reliably yet. So export refuses to move a material to another texture, new or existing, on any model that has an `_L_` partner folder next to it, and lists the affected materials.
> - Still allowed: editing an existing PNG in place (same file name, so the low-poly model sees the change too), and new textures on custom submeshes.
> - To give a character's existing surfaces a different texture, use Dolphin's custom texture loading instead.

#### Adding a new submesh how-to
A new submesh is a static (rigid) mesh that follows one bone, like a hat or a held item. It gets its own material and texture.

1. Import any sluggie file.
2. Select the armature or any of its child meshes, then open the Sluggies Tools tab in the side panel (Object Mode).
3. Click **Add Submesh** and set a name, the host bone and the material template for your new object.
4. A small cube appears at the host bone. Edit it freely: model, UV-unwrap, paint, and swap its texture.
5. When done, select every mesh you'd like to include, **including the new submesh**. Then export back to the original sluggie file with **Reimport textures from tex folder** enabled. The export uses Hammerspace by itself.
6. Patch the exported `.sluggie` normally. The patcher picks the build steps it needs by itself.

**Material templates**
The template decides how the new submesh is lit. It is listed in this order, and the first entry is the default:
- `rigid:<surface>`: a copy of one of the model's own rigid meshes (a cap or head, for example). Best match, when the model has one.
- `derived:<surface>`: built from a body surface. Use it on models without rigid meshes (Boo, the Piantas, Monty Mole, Petey).
- `builtin:rigid_spec_v1`: a generic lit surface that works on every model, including bats and gloves.

All three looked the same in game tests. A `rigid:` template limits how detailed the mesh can be. If export says the mesh has too many vertices, normals or UVs for its template, pick a `derived:` or `builtin:` one.

**Host bones**
- One bone can carry only one mesh. The dialog lists only bones that are still free. The **Free host bones** list in the panel shows them too.
- Bones that already carry an original mesh are never offered. A bone that drives skinning (a hand, for example) is allowed.
- The mesh must stay weighted to exactly one `bone_<id>` vertex group, its host bone, at full weight. Use **Reassign to New Bone** to move it to another bone instead of editing the groups by hand.
- Export re-checks the bones. It stops with the object names when a host bone was taken in the meantime (for example, by a rigid mesh you moved there) or when two new submeshes share one bone.
- **Stadiums: use a free original root bone.** In a stadium, a new submesh must hang on one of the stadium's own bones that has no parent and no mesh. On a bone you added, it does not show at all; on a child bone it showed almost fully transparent. In a stadium, Add Submesh, Reassign to New Bone and the **Free host bones** list therefore offer only free original root bones, **Add Bone** is refused, export stops while the armature still has added bones, and the patcher refuses both. How many free root bones there are depends on the stadium: Yoshi Park's day model has one (`bone_0`, at the stadium origin), its night model has 19. Several separate pieces can share one bone: put them in one object. A model counts as a stadium when it comes from model folders 7-16 (Mario Stadium to Toy Field).
- **Stadiums: pick a stadium template.** In a stadium, Add Submesh lists two stadium templates first: `builtin:stadium_shdw_opaque_v1` (preselected) and `builtin:stadium_shdw_cutout_v1`. They draw like the stadium's own surfaces: one texture, no normals, no specular highlight. The opaque one shows transparent pixels black; for a PNG with transparent areas use the cut-out one. Transparency is on/off only: semi-transparent pixels become fully opaque or fully clear. The patcher warns when a stadium submesh's PNG has transparent pixels but its template ignores them. The character template `builtin:rigid_spec_v1` also works in stadiums, and so does copying a stadium surface with `rigid:` (e.g. `rigid:sm1_ds42` in Yoshi Park day).

**Placement**
- What you see in the rest pose is what you get. Moves in Object Mode and in Edit Mode both count, and so do rotation, scale and mirroring. Nothing needs to be applied first, but switch back to Object Mode before exporting: export is refused in Edit Mode.
- Posing the armature doesn't affect the export. An object scaled to zero on an axis is refused.
- Keep the mesh close to its host bone. Far away from it, positions are stored with less precision, and export warns you when that happens. **Treat that warning as a problem to fix:** expect graphical glitches such as flickering. In Yoshi Park, two planes about 75 units from their bone (stored 8x coarser) flickered from some camera angles; moved close to the bone, at full precision, they did not. In a stadium you can't add a nearer bone, so move the mesh closer to its bone instead.

**What gets exported**
- The mesh as it is, triangulated on export. The object in your scene isn't changed.
- One UV map: the one marked for rendering. A mesh without a UV map is refused.
- Vertex colors from `color0` (or the active color attribute). Without one, the mesh is white.
- One material: the one Add Submesh created. Don't assign other imported materials to it.
- These are ignored, and export warns you about them:
  - shape keys other than Basis;
  - extra materials;
  - extra vertex groups;
  - modifiers other than Armature. Apply Mirror, Subdivision and the like before exporting.

**Textures**
- Add Submesh saves a blank `<name>.png` into the model's `tex/` folder and connects it. Paint it or replace it.
- The texture can be a PNG from anywhere on disk. Export copies it into the model's `tex/` folder under a free name.
- A low-poly (`L_`) model owns no textures, so its new submesh can only use a texture the model already has.
- The new submesh exists only in the model you patch. The game switches to the low-poly (`L_`) model at a distance, so add a matching submesh there too if it should stay visible.

**Things to keep in mind**
- **Keep the new submesh selected on every export.** The `.sluggie` holds only the new submeshes that were selected at export. One left unselected is removed from the file.
- Make every new submesh with **Add Submesh**, not by duplicating one (Shift+D). A copy keeps the original's internal id and material, which the tools don't expect.
- Export to the `.sluggie` the armature was imported from. Export refuses a file that belongs to a different model.
- An armature imported with an older add-on version must be re-imported before you can add a submesh to it.
- **Specular strength:** a new submesh made from a built-in template starts at 50. One made from a donor material starts at that material's current strength. Change it with **Set Specular Strength**.
- **Bats and gloves work too.** They have only one bone by default, and it already carries the original mesh. To add a new rigid mesh to them, add a custom bone first with **Add Bone**.

#### Adding a new bone how-to
A new bone gives a new submesh somewhere to attach when no free bone sits where you need one. It follows its parent bone and nothing else: it isn't animated and it can't drive skinning. Not available in stadiums (see above).

1. Select the armature or any of its child meshes and open the Sluggies Tools tab (Object Mode).
2. Click **Add Bone** and pick the parent bone. The active bone is preselected, and new bones can be parents too.
3. The new bone starts at the parent's tail. Move and rotate it in Edit Mode as you like; its rest position is what gets exported.
4. Attach a new submesh to it with **Add Submesh**. The new bone is listed as a free host bone.
5. Export and patch normally. The export uses Hammerspace by itself.

**Rules**
- Bones can only be added, as leaves. Don't delete, rename, re-parent or reorder the original bones, and don't parent an original bone to a new one. The game's animations are built for the original skeleton, so these edits would make limbs move wrong. Export and the patcher refuse them.
- Deleting a bone you added yourself is fine. It is simply left out of the export, and the bones you added after it move down one number each.
- Don't scale the new bone. The game ignores bone scale, and export warns when a new bone has one.
- Don't paint weights for a new bone onto the body mesh. Skinning to new bones isn't supported.
- Bone numbers are assigned in the order you added the bones. After you delete an added bone, click **Renumber Added Bones** so the names match the numbers again. Meshes on those bones keep their bone, because their vertex groups are renamed too. **Add Bone**, **Add Submesh** and **Reassign to New Bone** renumber automatically. Export is refused until the names match.

#### Editing the unused characters

The six unused characters (folders 89-94) share all their models with a playable character in the original game. An untangle export (StartTools menu [1]) gives each of them a copy of its own, and from then on they can be edited like any other character:
- They always export and patch through hammerspace.
- An unpatch restores the unused character's own untangled data block, not the vanilly game's "shared model" state.
- If an unused character ever shows its counterpart's edits (for example after an unpatch with an older version of the tools), run menu [8] to re-split it.
- The high-/low-poly rules below apply to them as well.

#### New bones on low-poly (`L_`) models

The game moves a character's low-poly model with the high-poly model's skeleton. When you add a bone to an `L_` model and attach a submesh to it:
- Add the same bone, with the same parent and position, to the high-poly model too, and patch the **high-poly model first**. Otherwise the game crashes when the model loads, so the patcher refuses the `L_` patch.
- The `L_` model's submesh follows the **high-poly** model's bone. If the two bones sit in different places, the patcher warns you, and the high-poly placement is the one you'll see.
- An added bone without a submesh on it doesn't need a partner.

#### Moving vertices to a different bone (vertex groups)

You can move vertices between the model's existing `bone_<id>` vertex groups, e.g. assign all of `bone_28` to `bone_63` and remove them from `bone_28`.
- Keep the number of bones per vertex the same. A two-bone vertex should stay two-bone and a one-bone vertex one-bone. Reassigning a whole group is the safest edit.
- Merge weights instead of assigning at weight 1.0 if you want to keep the original blend between bones (e.g. with a Vertex Weight Mix modifier set to *Add*). Assigning at 1.0 overwrites it.
- If an edit would need the game's vertex order changed, the patcher stops with a message naming the affected bone entries. For example, giving part of a two-bone area a single bone does this. Undo that part, or reassign the whole area.
- Moving vertices in space is fine in the same export, but don't add, remove or reorder vertices or faces in it.

#### You can't (yet):
- add or remove vertices on the main mesh (always the first)
- reorder main mesh face indices
- add new materials to imported meshes (moving faces between their existing materials is fine)
- remove, reorder or re-parent original bones (adding bones is fine)
- skinning edits beyond moving vertices between existing bone vertex groups (see above)
- skin a new submesh to more than one bone
- remove an object's custom properties
- rename imported objects

