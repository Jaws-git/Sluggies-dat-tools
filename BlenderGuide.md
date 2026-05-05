# Blender Editing Guide

After the plugin is installed import one .sluggies file using File->Import

One sluggies file can contain several meshes. These may not be aligned to each other, this is normal.
To keep things simple, do not try to scale/align the various meshes to each other. But if you do, make sure to remove the rotation/transformation/scale from the objects before exporting. (Alt+G, Alt+R, Alt+S)

# Meshes and UVs
The current workflow supports editing mesh and UV shape in blender and writing this back into the game.
However, there's restrictions imposed in order to not violate the game's file's memory. Change the length of the model data and you risk crashing the game.

## You can:
- change the position of existing verts in space
- edit face normals (each model is imported with its original custom normals)
- change the position of existing UVs 
- in case of multiple UVs concentrated in one single spot, you can move around the whole "unit" as one

## You can't:
- add or remove vertices
- manipulate material slots (this feature is planned to be added in the future)
- manipulate bones or skinning data (again, planning to add this in the futre)
- reorder face indices
- remove an object's custom properties
- you should also refrain from renaming objects
- create new UV seams
- create or remove UV faces
- split up a connected UV edge

# Exporting from Blender
When everything is done, go abck to object mode and select the  objects you've modified.
Export the file via File->Export, select **the same .sluggies file you imported earlier**!
The updated file will hold both original and edited model data.

### Options:
[] Use Hammerspace - instead of overwriting the original model data ("in-place" patching), write the edits to hammerspace. This is currently a manual setting and not determined automatically.
[] Include Custom Split Normals - When off, writes averaged blender normals. When on, Writes custom split normals data. 