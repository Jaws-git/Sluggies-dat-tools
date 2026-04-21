# MSS-dat-tools

Some random tools for dealing with Mario Super Sluggers' dat file. Thanks to the MSS/MSSB communities for help, esp. Roeming who figured out a bunch of the model stuff and made https://github.com/roeming/MSSB-Export-Models.

# Sluggies-dat-tools

This version of the .dat tools is laser focused on Mario Super Sluggers and will probably not work with much else.
Goal is the export of original MSS 3D player models and subsequent re-import of edited models. For funny.

## requirements

- Dolphin Emulator https://dolphin-emu.org/
- US(!) copy of Mario Super Sluggers
- **wimgt** (part of [Wiimms SZS Tools](https://szs.wiimm.de/wimgt/) ) — must be on `PATH`; used to convert textures between TPL and PNG. No textures without this.
- Python https://www.python.org/downloads/
- Autism

## Workflow  
### Export  

1) Set up Dolphin & Game iso
2) Try running the game to make sure everything is prepped correctly
3) right click MSS -> properties -> Filesystem -> right click top node -> extract entire disc
4) clone or download this repository
5) create a folder "bin" inside \export_daes\
6) from the extracted disc data, copy both "dt_na.dat" and "main.dol" to the new folder \export_daes\bin\
7) cmd 
```
cd export_daes
python export.py
```

This will extract the entire content into a new folder \daes_model_only\\...
It will contain more than just player models, but props and environment models as well.
Everything is sorted into numbered folders, for example Tiny Kong + TK Bat + TK Glove is in 75

### Import

**not yet functional, this readme part is a stub**

1) once you have edited your model in Blender, export it as .dae 
   (Blender 5.x and higher no longer support thus format! Use third party plugins or install an older versio nof blender, 4.5 and lower)
2) put the file in a new folder \import_model\somefoldername\
3) update path at the beginning of import.py to your new folder
4) sacrifice a goat to the machine god
5) cmd
``` 
cd import_model
python import_dae.py
```
5) ???
6) receive "out" file
7) integrate "out" contents into dt_na.dat