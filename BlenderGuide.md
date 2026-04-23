# Blender Editing Guide

After the plugin is installed import one .sluggies file using File->Import

One sluggies file can contain several meshes. These may not be aligned to each other, this is normal.
To keep things simple, do not try to scale/align the various meshes to each other. But if you do, make sure to remove the rotation/transformation/scale from the objects before exporting. (Alt+G, Alt+R, Alt+S)

- **This workflow currently does not support adding or removing vertices. It would lead to crashes later on when writing data back to the game.**
- currently, only manipulation of vert positions and face normals is supported. More is in the works. Please wait warmly (or contribute to speed up developments!).
- Materials, UVs and bone binding are also not getting touched at all.
- Do not rename models, the names contain information about where the mesh belongs.
- Do not remove the objects custom properties

When everything is done, export the file via File->Export, select **the same .sluggies file you imported earlier**!
The updated file will hold both original and edited model data.