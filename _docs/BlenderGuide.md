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
> [!WARNING]
> Hammerspace model edits currently only support vertex position changes and texture edits. More capabilities to come soon!

[] Use Hammerspace - instead of overwriting the original model data ("in-place" patching), write the edits to hammerspace. This is currently a manual setting and not determined automatically.  
[] Include Custom Split Normals - When off, writes averaged blender normals. When on, Writes custom split normals data.   
[] Reimport textures from tex folder - write the PNG files found in the exports back into the game files, including any edits made to them

## Exporter capabilities and restrictions
### In-Place mode
#### You can:
- change the position of existing verts in space
- edit face normals (each model is imported with its original custom normals, where available)
- edit facial expressions (shapekeys)
- change the position of existing UVs 
- in case of multiple UVs concentrated in one single point, you can move the whole "unit" around as one
- reimport edited PNG textures as long as their dimensons didn't change
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
- append a new model texture by changing the connected Image Texture node on an imported donor material
>- create a new static submesh that has it's own texture and follows a bone of your choice

#### Adding a new model texture how-to

1. Put the new PNG in the model's own `tex/` folder.
2. Keep the imported material and its `SurfaceId`; do not create a new material.
3. Replace the image in the Image Texture node or create a new one.
   Leave at most one Image Texture node connected to the active Material Output!
4. Export with **Use Hammerspace** and **Reimport textures from tex folder** enabled.
5. Patch the exported `.sluggie` normally.

#### Adding a new submesh how-to
1. Import any sluggie file
2. select the armature or any of the child meshes, then open the sluggies tab in the side panel
3. pick a bone to use and a material to copy
4. click the "Add submesh" button, set name, bone and template material for your new object
5. A simple cube mesh will appear at the guiding bone. Yo ucan edit it freely in edit mode.
6. when done, select all meshes you'd like to include, then export back to the original sluggie
   file with "hammerspace" and "reimport textures" activated

#### Moving vertices to a different bone (vertex groups)

In hammerspace mode you can move vertices between the model's existing `bone_<id>` vertex groups, e.g. assign all of `bone_28` to `bone_63` and remove them from `bone_28`.
- Keep the number of bones per vertex the same. A two-bone vertex should stay two-bone and a one-bone vertex one-bone. Reassigning a whole group is the safest edit.
- Merge weights instead of assigning at weight 1.0 if you want to keep the original blend between bones (e.g. with a Vertex Weight Mix modifier set to *Add*). Assigning at 1.0 overwrites it.
- If an edit would need the game's vertex order changed, the patcher stops with a message naming the affected bone entries. For example, giving part of a two-bone area a single bone does this. Undo that part, or reassign the whole area.
- Moving vertices in space is fine in the same export, but don't add, remove or reorder vertices or faces in it.

#### You can't (yet):
- add or remove vertices on the main mesh (always the first) 
- manipulate material slots
- manipulate bones, or skinning edits beyond moving vertices between existing bone vertex groups (see above)
- reorder main mesh face indices
- remove an object's custom properties
- you should also always refrain from renaming objects

