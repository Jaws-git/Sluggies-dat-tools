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

The armature's bones are sorted into **bone collections** (Armature properties -> Bone Collections) by their role in the original model. A bone can be in more than one.
- **Skinning:** bends the body (the bones the body's weights use).
- **Rigid mesh:** carries a rigid mesh (Mario's cap, for example).
- **Tracked joint** / **Tracked tip:** no vertices of their own, but an animation track id; joints have child bones (root, hip, neck, head), tips don't (hand attach points for bat and glove, toe tips).
- **Structural:** no vertices, no track, but child bones (branch points such as the pelvis).
- **End tip** (hidden): no vertices, no track, no children (finger and nose tips).
- **Marker** (hidden): bones with no parent and no children. The game addresses them by track id; what for is unknown.
- **Added:** bones made with Add Bone.

The collections only sort the bones for display; any bone can still be used for skinning (see "Editing the body").

## Export

1. Select all submeshes you want to write back to the sluggie file in the viewport
2. File->Export->Sluggers intermediate (.sluggie)  
3. The file browser opens on the .sluggie the model was imported from. Either:
   - keep it, to update that file. It then holds both the original and the edited model data; or
   - type a **new file name** to save a variant (for example `78277664_mario_red.gpl.sluggie`). The new file holds the original and the edited model data too, and the file you imported from stays untouched. From then on the scene uses the new file: the next export, Check Models and Manage texture slots all start from it.

A variant can go next to the exported file or into any other folder. A file in another folder looks for its `tex/` folder (and a High model's `icon/` folder) next to itself, so copy those folders there yourself; the tools don't redirect the paths. Patch a variant like any other `.sluggie`: by its name when it is in `2_Output_Assets`, otherwise by its full path. The export copies the matching text to the clipboard.

A variant you save over an existing file must be of the same model: export refuses a `.sluggie` of another model.

Re-running the model export never deletes `.sluggie` files or PNGs: it overwrites the files it exports itself (after its overwrite question), and keeps variants and textures you saved under new names.

*SAVE YOUR EDITS AS .BLEND PROJECTS FOR SAFEKEEPING!*  
*Pro Tip: you can add the import/export menus to your quick favorites by right clicking them. Then press "q" (default) to see all your quick favorites.*

#### Exporter Options:
[] Include Custom Split Normals - When off, writes averaged blender normals. When on, Writes custom split normals data.   
[] Reimport textures from tex folder - write the model's original textures (the PNGs the model export wrote to `tex/`) back into the game files, including any edits made to them. Leave it off to keep the original textures as they are. New textures (a new PNG on a material, a custom submesh or a new material) are always added, with or without this option.

#### In-place or Hammerspace: chosen automatically
The exporter picks the mode for you. It writes your edits **in place** (over the original model data) whenever they fit there, and switches to **Hammerspace** when an edit needs a rebuilt model. The export always reports its choice, e.g. `Export mode: Hammerspace, needed for: Hat: UV seams split`.

Hammerspace is used for:
- custom submeshes and bones added with **Add Bone**;
- texture changes on materials (a new PNG, or another of the model's textures), and original PNGs whose size changed (with Reimport textures on);
- added or removed vertices, and changed, added or removed faces;
- split UV seams;
- edited vertex colours;
- faces moved to another material;
- vertices moved between bones, or skin data that no longer fits its original size;
- normals that split where the original model shares one normal (Overwrite Normals);
- the unused characters (folders 89-94);
- slot exports (**Export as .sluggie** in the character grid, `Custom <name> NN` folders): they carry their own copy of the model they were read from, which only Hammerspace can build from (see the Roster Guide, "Exporting a slot as `.sluggie` files");
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
- change a material's specular (gloss) strength with **Manage specular strength** (Sluggies sidebar, *Textures & Materials* box; 0 = no highlight, 255 = maximum; linear)
  >- the dialog lists every material of the High model and its Low partner (whichever are imported) and sets the strength of each specular one. Export each changed model afterwards.
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
- reshape the body (the first mesh) freely: add or delete geometry, paint new weights, add materials (see "Editing the body" below)

#### Adding a new model texture how-to

1. Put the new PNG in the model's own `tex/` folder.
2. Keep the imported material and its `SurfaceId`. (For a new material use **Add material**; see "Editing rigid meshes" and "Editing the body" below.)
3. Replace the image in the Image Texture node or create a new one.
   Leave at most one Image Texture node connected to the active Material Output!
4. Export. The new PNG is added whatever **Reimport textures from tex folder** says (that option only decides whether the original textures are rewritten). The export uses Hammerspace by itself.
5. Patch the exported `.sluggie` normally.

- Only Image Texture nodes connected to the Material Output count. Unconnected helper nodes are ignored, and a material with no connected image node keeps its original texture. A connected Image Texture node **without an image** stops the export and names the material: load a PNG into it or remove the node.
- If more than one Image Texture node is connected, export stops and names the material.
- The PNG is looked up only in this model's own `tex/` folder. If it's missing, the export stops.
- Choosing a PNG the model already has (another texture from its `tex/` folder) just points the material at that texture. Nothing new is added.
- The same PNG on several materials is stored only once.

> [!NOTE]
> **Player characters and their Low model.** Every player character has a High model and a Low partner model (the Low model's folder name contains `_L_`). The Low model owns no textures and draws with the High model's textures by number. When you move a High material to another texture, the Low model keeps its original textures, so at a distance the character shows the old look. This is only cosmetic. To make them match, use **Manage texture slots** (Sluggies sidebar, *Textures & Materials* box) with both models imported: it lists the High model's texture slots (as of its last export, added textures included) and lets you pick a slot for each Low material. The material shows the picked slot's PNG in the viewport right away; **OK** keeps the picks, **Cancel** puts back the textures the dialog opened with. Export the Low model afterwards (it goes to Hammerspace), and patch the High model first when a Low material uses a texture the High model added. Editing an existing PNG in place (same file name) changes both models.
>
> **Keep new textures small.** On the field every player gets a fixed memory area of 870,400 bytes (850 KB) for its High model, Low model and bat or glove together. A character whose files don't fit crashes the game when it is loaded onto the field, often only once it has to field (fielders carry the bigger glove); character select still works. Vanilla players use about 0.5-0.65 MB of it, and a 1024x1024 texture alone adds 512 KB, so 256x256 or 512x512 is usually enough. The roster guide's **Player memory** option can raise the area (`RosterGuide.md`).

#### Adding a new submesh how-to
A new submesh is a static (rigid) mesh that follows one bone, like a hat or a held item. It gets its own material and texture.

1. Import any sluggie file.
2. Select the armature or any of its child meshes, then open the Sluggies Tools tab in the side panel (Object Mode).
3. Click **Add New Submesh** and set a name, the host bone and the material template for your new object.
4. A small cube appears at the host bone. Edit it freely: model, UV-unwrap, paint, and swap its texture.
5. When done, select every mesh you'd like to include, **including the new submesh**. Then export back to the original sluggie file. Its texture is added with or without **Reimport textures from tex folder**. The export uses Hammerspace by itself.
6. Patch the exported `.sluggie` normally. The patcher picks the build steps it needs by itself.

**Using a mesh you already have**
Any mesh object can become a new submesh, for example one you modeled or appended from another file. It needs no Sluggies properties. **Check Models** lists such meshes while they are not part of a model yet (no Sluggies armature above them, no Sluggies properties, or weights on several bones, which only the main body may have), with a button for this tool; meshes in hidden or excluded collections are left out.

1. Make the mesh the active object (Object Mode) and click **Make Active Mesh into a Submesh**.
2. Pick the armature (preselected: the one the mesh is parented to, else a selected one, else the nearest) and a free host bone (preselected: the free bone nearest the mesh).
3. Click OK. The mesh stays where it is in the world. The tool:
   - removes all its vertex groups and puts every vertex on the host bone at full weight;
   - parents it to the armature and adds or updates its Armature modifier (extra Armature modifiers are removed);
   - renames its UV map to `UV0` when it has exactly one (with several UV maps nothing changes; the render-active one is exported);
   - turns its materials into its own surfaces with the default template (`builtin:rigid_spec_v1`, in a stadium `builtin:stadium_shdw_opaque_v1`) and specular strength 50. The material most faces use becomes the main surface, the others become Add-material surfaces. Empty material slots are removed and their faces move to the main surface; a mesh without materials gets one;
   - gives every material without a texture a new PNG in the material's colour in the model's `tex/` folder, and saves images without a PNG file (generated, packed, other formats) as PNGs there.
   
   A material that other objects use too is copied first, so they keep theirs. A mesh that shares its mesh data with another object gets its own copy.
4. Run **Check Models** for anything it could not fix (for example a missing UV map, or several textures in one material).

Running it on a copy of a new submesh (Shift+D) gives the copy its own id and materials.

**Material templates**
The template decides how the new submesh is lit. It is listed in this order, and the first entry is the default:
- `rigid:<surface>`: a copy of one of the model's own rigid meshes (a cap or head, for example). Best match, when the model has one.
- `derived:<surface>`: built from a body surface. Use it on models without rigid meshes (Boo, the Piantas, Monty Mole, Petey).
- `builtin:rigid_spec_v1`: a generic lit surface that works on every model, including bats and gloves.

All three looked the same in game tests. A `rigid:` template limits how detailed the mesh can be. If export says the mesh has too many vertices, normals or UVs for its template, pick a `derived:` or `builtin:` one.

**Host bones**
- One bone can carry only one mesh. The dialog lists only bones that are still free. The **Free host bones** list at the bottom of the panel's **Bones** group shows them too.
- Bones that already carry an original mesh are never offered. A bone that drives skinning (a hand, for example) is allowed.
- The mesh must stay weighted to exactly one `bone_<id>` vertex group, its host bone, at full weight. Use **Reassign to New Bone** to move it to another bone instead of editing the groups by hand.
- Export re-checks the bones. It stops with the object names when a host bone was taken in the meantime (for example, by a rigid mesh you moved there) or when two new submeshes share one bone.
- **Stadiums: host bones.** Every original stadium bone is a root bone (it has no parent). A new submesh in a stadium hangs on a bone without a mesh: one of the stadium's own free root bones, or a new one from **Add Bone**, which in a stadium starts at the 3D cursor and is a root bone by default (*Parent bone: None*). You can also pick a parent: the new bone then follows that bone (tested in Yoshi Park, 2026-10-18), which only matters if that bone ever moves; in a stadium a root bone does the same job. **A new bone is usually the better choice:** you can put it right next to the mesh, which keeps the mesh at full position precision (see Placement below), and it draws after every original stadium mesh. Several separate pieces can share one bone: put them in one object. A model counts as a stadium when it comes from model folders 7-16 (Mario Stadium to Toy Field).
- **Stadiums: pick a stadium template.** In a stadium, Add New Submesh lists two stadium templates first: `builtin:stadium_shdw_opaque_v1` (preselected) and `builtin:stadium_shdw_cutout_v1`. They draw like the stadium's own surfaces: one texture, no normals, no specular highlight. The opaque one shows transparent pixels black; for a PNG with transparent areas use the cut-out one. Transparency is on/off only: semi-transparent pixels become fully opaque or fully clear. The patcher warns when a stadium submesh's PNG has transparent pixels but its template ignores them. The character template `builtin:rigid_spec_v1` also works in stadiums, and so does copying a stadium surface with `rigid:` (e.g. `rigid:sm1_ds42` in Yoshi Park day).

**Placement**
- What you see in the rest pose is what you get. Moves in Object Mode and in Edit Mode both count, and so do rotation, scale and mirroring. Nothing needs to be applied first, but switch back to Object Mode before exporting: export is refused in Edit Mode.
- Posing the armature doesn't affect the export. An object scaled to zero on an axis is refused.
- Keep the mesh close to its host bone. Far away from it, positions are stored with less precision, and export warns you when that happens. **Treat that warning as a problem to fix:** expect graphical glitches such as flickering. In Yoshi Park, two planes about 75 units from their bone (stored 8x coarser) flickered from some camera angles; moved close to the bone, at full precision, they did not. In a stadium, add a new root bone next to the mesh (Add Bone at the 3D cursor) and host the mesh on it.

**What gets exported**
- The mesh as it is, triangulated on export. The object in your scene isn't changed.
- One UV map: the one marked for rendering. A mesh without a UV map is refused.
- Vertex colors from `color0` (or the active color attribute). Without one, the mesh is white.
- Its materials: the one Add New Submesh created, plus any made for it with **Add material** (see "Editing rigid meshes" below). Every face must use one of those; an imported material of another mesh or a plain Blender material stops the export. An Add-material surface no face uses is skipped with a warning.
- These are ignored, and export warns you about them:
  - shape keys other than Basis;
  - extra materials that are not its own;
  - extra vertex groups;
  - modifiers other than Armature. Apply Mirror, Subdivision and the like before exporting.

**Textures**
- Add New Submesh saves a blank `<name>.png` into the model's `tex/` folder and connects it. Paint it or replace it.
- The texture can be a PNG from anywhere on disk. Export copies it into the model's `tex/` folder under a free name.
- A Low model (folder name contains `_L_`) owns no textures: it draws with its High model's textures. When the High model's folder holds several `.sluggie` files (variants), the Low file pairs with the one of the same name, without the leading offset and `L_` (`78711424_L_mario_red.gpl.sluggie` pairs with `78277664_mario_red.gpl.sluggie`). Without such a file it pairs with the exported one, named after its folder. Manage texture slots and the patcher's slot planner use the same rule. Its new submesh can use any PNG from the High model's `tex/` folder, including textures the High model's own export added. Patch the High model first, so those added textures exist in the game when the Low model is patched.
- The new submesh exists only in the model you patch. The game switches to the Low model at a distance, so add a matching submesh there too if it should stay visible.

**Things to keep in mind**
- **Keep the new submesh selected on every export.** The `.sluggie` holds only the new submeshes that were selected at export. One left unselected is removed from the file.
- Make every new submesh with **Add New Submesh** or **Make Active Mesh into a Submesh**, not by duplicating one (Shift+D). A copy keeps the original's internal id and material, which the tools don't expect; run **Make Active Mesh into a Submesh** on the copy to fix it.
- Export to the `.sluggie` the armature was imported from, or to a new file name. Export refuses an existing file that belongs to a different model.
- An armature imported with an older add-on version must be re-imported before you can add a submesh to it.
- **Specular strength:** a new submesh made from a built-in template starts at 50. One made from a donor material starts at that material's current strength. Change it with **Manage specular strength**.
- **Bats and gloves work too.** They have only one bone by default, and it already carries the original mesh. To add a new rigid mesh to them, add a custom bone first with **Add Bone**.

#### Editing rigid meshes

A rigid mesh is a part that follows one bone without skinning: a cap, a head, a bat, Birdo's bow. You can edit it almost freely; the exporter decides what the game data needs:

- Moving vertices, editing UVs, normals or vertex colours, changing the specular strength: stored in the original data when it fits there, as before.
- **Adding or deleting geometry, re-unwrapping with new seams, moving the whole object in Object Mode, moving faces between its materials, or using an Add material surface:** the mesh is rebuilt as a whole. The export log says `rigid rebuild (<reasons>)` and lists the faces per surface before and after, so an accidental *Assign* is easy to spot. Hammerspace is used automatically.

Rules:
- Every face must use one of **that mesh's own** materials: the imported ones, or ones made with **Add material**. A face on an empty slot, on a plain Blender material or on another mesh's material stops the export.
- Every vertex must be in the mesh's `bone_<id>` vertex group. New vertices often have no group; **Reassign to new bone** fixes that in one click (also onto the bone the mesh is already on).
- Meshes with facial poses (Mario's head and the like) can be edited like any other rigid mesh. Their blink and mouth poses follow the rebuild through the imported facial shape keys (`facial_object_<o>_pose_<p>` and `Basis`); the export log lists `facial object N: ... animated`. Keep those keys: without them the rebuilt mesh keeps its rest shape and no longer animates (the log warns; re-import the mesh to get the keys back). Edited key shapes are exported as the new poses.
- The skinned body (the first mesh) has its own rules, see "Editing the body" below.
- A Low (`_L_`) model is edited on its own; nothing is mirrored from the High model. The game switches to it at a distance.
- A mesh you leave unselected keeps the edit an earlier export gave it, including its move to another bone and any new PNG on its new surfaces. The export log says so. To drop that edit, select the original (non-`_edit`) object and export again.

**Reassign to new bone** (Sluggies sidebar, *Rigid mesh* box) moves the active rigid mesh to another free bone:
- *Keep world position*: the mesh stays where it is and follows the new bone from now on (a rebuild).
- *Keep offset to bone*: the mesh jumps so it keeps the same offset from the new bone as it had from the old one. Its data is unchanged, so this also works in place, and `--unpatch` puts the mesh back on its original bone.

**Add material** (same box) adds a new surface to the active rigid mesh, custom submesh or body (a custom submesh can carry as many as you like; its faces are split between its materials just like a rigid mesh's). Pick a template as for Add New Submesh (`builtin:rigid_spec_v1` is the default; see the template list above), assign faces to the new slot (in Edit Mode the selected faces are assigned right away), and load a PNG into its empty Image Texture node. Export stops until that node has an image. A PNG the model already has just binds that texture; a new PNG is appended on export (a Low model can only use its High model's textures, including the ones the High model added). The hand-mitt templates `rigid_rhsp_v1` / `rigid_lhsp_v1` hide the surface's vertex-alpha-0 parts while that hand wears the mitt.

A re-import of the exported file always shows the original mesh unchanged on its original bone. Any edit, including a move with **Reassign to new bone** (either placement), appears as a second `<name>_edit` object on its current bone, with its new materials. Select only one of the two objects when exporting again: the `_edit` object keeps your changes, the original undoes them. New submeshes and bones added with **Add Bone** come back too, as they were exported: the submesh with its materials, texture and vertex colours on its host bone, the bone at its exported place. Export them again like any new submesh.

#### Making a stadium mesh solid for the ball how-to
In a stadium (model folders 7-15) any mesh can block batted balls: a new submesh, an original stadium mesh, or a plain mesh that only serves as an invisible wall.

1. Select the mesh, open the Sluggies Tools tab and, in the **Stadium collision** box, tick **Solid for the ball**.
2. **Make invisible**: tick it to export only the collision; the mesh itself is not drawn in game. Works on new submeshes and plain meshes; export refuses it on the stadium's original meshes.
3. Pick the **Surface**. **Fence** (the default) works like the outfield fence: the ball bounces off and stays in play. **Stands** works like the stands: a ball that touches it is a **home run**, wherever the mesh stands. The other types (Ground, Infield dirt, Wall, Foul pole, or any number 1-127) copy the stadium's own surfaces and are untested.
4. **Sides**: *Both sides* (default) blocks the ball from either side of each face. *Front only* blocks it only on the side the face normals point to, so a ball coming from behind passes through. Enough for a closed mesh whose normals point outwards.
5. **Territory**: *Auto* marks each face fair or foul by where it lies relative to the foul lines, as the game's own data does. *Fair* or *Foul* forces one for the whole mesh.
6. Export with the mesh **and** the stadium's armature (or any of its meshes) selected. Patch as usual: the patcher writes the stadium's collision file next to the model.

Things to know:
- The collision itself is invisible. The mesh is drawn in game only if it is a model mesh or a submesh without **Make invisible**; a plain mesh is never drawn.
- Very high fly balls still fly over a low wall. Fielders don't seem to react to collision meshes (not fully tested).
- Day and night (and on some stadiums the third variant) share one collision file. Exporting the night model without any collision mesh leaves the day model's collision alone; patching collision from both models keeps the last one patched.
- Once a file has collision, the export always writes what is selected: export without any collision mesh selected and the patcher restores the stadium's original collision. `--unpatch` of the model restores it too.
- A re-import brings each collision mesh back as a wireframe `<name>_collision` object with its settings (and **Make invisible** ticked: it is collision only).
- Toy Field (folder 16) is not supported.

**The stadium's own collision.** Importing a stadium also brings in its original collision as wireframe objects (`col<region>_<surface>`, e.g. `col03_fence_foul`) in a collection named `<armature> collision`; hide the collection when you don't need it. They are plain collider meshes with their settings filled in (front side only, territory as in the game). As long as the scene holds any of them, export rebuilds the stadium's **whole** collision from the scene, selected or not: move or reshape one to change the original collision, untick **Solid for the ball** or delete it to remove that part (deleting all of them is refused: the ball would fall through the ground). Delete the whole collection to go back to adding your colliders on top of the original collision. Stadium exports made before this feature have no original collision to import: export the stadium again, or run `py SluggiesTools/Hammerspace/StadiumCollision.py <file>.sluggie --add-vanilla-field` once.

#### Adding a new bone how-to
A new bone gives a new submesh somewhere to attach when no free bone sits where you need one, or a new joint for the body to bend at. It follows its parent bone unless you give it an animation track. In a stadium, Add Bone starts the bone at the 3D cursor and makes it a root bone unless you pick a parent (see "Stadiums: host bones" above).

1. Select the armature or any of its child meshes and open the Sluggies Tools tab (Object Mode).
2. Click **Add Bone** and pick the parent bone. The active bone is preselected, and new bones can be parents too.
3. The new bone starts at the parent's tail. Move and rotate it in Edit Mode as you like; its rest position is what gets exported.
4. Attach a new submesh to it with **Add New Submesh**. The new bone is listed as a free host bone.
5. Export and patch normally. The export uses Hammerspace by itself.

**Rules**
- Bones can only be added, as leaves. Don't delete, rename, re-parent or reorder the original bones, and don't parent an original bone to a new one. The game's animations are built for the original skeleton, so these edits would make limbs move wrong. Export and the patcher refuse them.
- Deleting a bone you added yourself is fine. It is simply left out of the export, and the bones you added after it move down one number each.
- Don't scale the new bone. The game ignores bone scale, and export warns when a new bone has one.
- You can paint body weights onto a new bone: add a `bone_<id>` vertex group for it on the body and weight vertices to it like to any other bone. This rebuilds the whole body (see "Editing the body"). Give the High and the Low model the same new bone when both bodies use it.
- Bone numbers are assigned in the order you added the bones. After you delete an added bone, click **Renumber Added Bones** so the names match the numbers again. Meshes on those bones keep their bone, because their vertex groups are renamed too. **Add Bone**, **Add New Submesh** and **Reassign to New Bone** renumber automatically. Export is refused until the names match.

#### Animating an added bone how-to
An added bone follows its parent until you give it an animation track of its own. Confirmed in game (Mario, 2026-10-13): a new bone under the spine, weighted to the belly and keyed in a bank-7 animation, bent the belly as keyed.

1. Tick **Animation track** in the Add Bone dialog, or select the added bone and click **Toggle Animation Track** in the sidebar. The bone gets a track number no stock animation uses (`0xC0`-`0xFF`, at most 64 animated added bones per model).
2. Export the model and **patch it first**. The track number lives in the model's bone-to-track table, so an animation that moves the bone only fits once the patched model has the bone.
3. Import the animation bank you want to change (**Import Sluggers .anm**, files 6-14 of the character's `anm` folder), key the added bone in the sequences where it should move, and **Export Sluggers .anm**. Sequences where the bone stays in its rest pose get no track for it.
4. Replace the bank in the slot (**Replace animation...** on the character grid, or `--write-slot-animation`). The patcher checks the bank against the slot's skeleton as the game holds it now; an `.anm` that moves an added bone before the model is patched is refused with "the slot's skeleton has no bone for ...".

Rules:
- The bone's rest pose comes from the model, its motion from the bank: keep the two exports from the same armature.
- A Low model is posed with the High model's skeleton (see "New bones on Low models"), so the High model needs the bone and its track; the Low model only needs the bone when its body or a submesh uses it.
- Replacing a bank re-samples every sequence in it at whole frames with linear/slerp interpolation; stock half-frame keys are lost. Keep the bank's sequence count (the game picks animations by number).

#### Scaling the whole model how-to
The game sizes a character through its animations, not through its model: a scale on the right bones in every animation bank makes the whole body bigger or smaller, feet still on the ground. Confirmed in game (Mario, 2026-10-17, factor 1.5).

1. Import the model, then all nine animation banks (**Import Sluggers .anm**, files 6-14 of the character's `anm` folder; select them all at once).
2. In the Sluggies sidebar, *Animations* box, set the X/Y/Z factor (scene axes, Z is up; 1.5 = half again as big) and click **Scale model in all animations**. It keys the scale of the bones the game scales the body from (pelvis, spine, hands, neck, head) in every imported action, and Blender then shows the model at the size the game will.
3. Export every group (**Export Sluggers .anm**, once per bank) and replace the nine banks in the slot (**Replace animation...** on the character grid, or `--write-slot-animation`).
4. The bat does not follow the hand: scale the bat mesh itself (its own model, files 2/5) and patch it too. Gloves follow the hands.

Rules:
- The factor multiplies what is there: clicking twice gives the square. Re-import the banks to start over.
- Equal X/Y/Z factors are exact. Unequal factors are applied along each bone's own axes, so parts that turn against the body (head, hands) show a seam that grows with the difference; keep it small and judge the preview.
- The bones are found by their animation track numbers, which are the same on every character, but only Mario has been checked in game. If a body part comes out unscaled or doubly scaled on another character, that character needs a different bone set.
- Only the banks you replace are scaled; a bank left stock shows the model at its normal size in those animations.

#### Editing the unused characters

The six unused characters (folders 89-94) share all their models with a playable character in the original game. An untangle export (StartTools menu [1]) gives each of them a copy of its own, and from then on they can be edited like any other character:
- They always export and patch through hammerspace.
- An unpatch restores the unused character's own untangled data block, not the vanilly game's "shared model" state.
- If an unused character ever shows its counterpart's edits (for example after an unpatch with an older version of the tools), run menu [6] to re-split it.
- The High/Low rules below apply to them as well.

#### New bones on Low models

The game moves a character's Low model with the High model's skeleton. When you add a bone to a Low model and attach a submesh to it:
- Add the same bone, with the same parent and position, to the High model too, and patch the **High model first**. Otherwise the game crashes when the model loads, so the patcher refuses the Low patch.
- The Low model's submesh follows the **High** model's bone. If the two bones sit in different places, the patcher warns you, and the High placement is the one you'll see.
- The same holds for an added bone the Low body is **skinned** to: the High model must have the bone too, or the patcher refuses the Low patch.
- An added bone without a submesh on it and without body weights doesn't need a partner.

#### Editing the body

The body is the first mesh, the one that bends with the skeleton through its `bone_<id>` vertex groups. Small edits stay in place, as before: moving vertices, editing UVs without new seams, normals, specular strength, and changing weight values while every vertex keeps the same bones.

**Anything more rebuilds the whole body** in Hammerspace: adding or deleting vertices or faces, moving the body in Object Mode, changing which bones a vertex uses (adding, removing or moving vertex groups), moving only some faces of a surface to another material, moving faces to a material with another shader, or using an **Add material** surface. The export log says `body rebuild of <name> (<reasons>)`.

Rules for a rebuilt body:
- **Weights:** every vertex the faces use needs weight on at least one bone of the model, original or added; export stops and counts the vertices otherwise. Any bone works, including original bones the original body doesn't skin to and bones that carry a rigid mesh (tested in game on Mario's High model); the log names such bones. Weights on a `bone_<id>` group the model has no bone for are dropped. Weights are normalized for you; any number of bones per vertex works, but each bone past the second costs extra data.
- **Blink and mouth poses:** a body that the game animates in the face (Luigi, for example) keeps its expressions through the imported facial shape keys (`Basis` and `facial_object_<o>_pose_<p>`). Keep those keys; the log lists `facial object N: ... animated`. Edited key shapes are exported as the new poses. Without the keys, the patched body has no facial animation (the log warns). On a few characters some face vertices also use a third bone for their expressions (Kritter, King Boo, Peach, Daisy, Diddy); after a rebuild that third-bone part of the expression is left out, and the patch log warns.
- **Materials:** every face must use one of the body's own materials (the imported ones or ones made with **Add material**), as on rigid meshes.
- **Shape keys** other than the facial ones are ignored.
- **Size:** the rebuilt body is stored compactly, but every added vertex and texture still counts toward the memory the game has for the whole scene (see "Keep new textures small" above). Check the High and Low model together.
- A Low (`_L_`) body is rebuilt on its own.

#### You can't (yet):
- remove, reorder or re-parent original bones (adding bones is fine)
- skin a new submesh to more than one bone
- remove an object's custom properties
- rename imported objects

