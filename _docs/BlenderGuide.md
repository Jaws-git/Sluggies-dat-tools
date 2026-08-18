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
3. select **the same .sluggies file for the character you imported earlier**. The updated file will contain both original and edited model data now.

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
>- change UVs in any way you want. split edges, unwrap, make new seams!
>- reimport edited PNG textures at any size!  
#### You can't (yet):
- add or remove vertices
- manipulate material slots
- manipulate bones or skinning data
- reorder face indices
- remove an object's custom properties
- you should also always refrain from renaming objects