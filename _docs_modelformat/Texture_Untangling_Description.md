# Texture Untangling

Dolphin emulator provides a very useful custom texture loading system that lets users easily mod Sluggers textures.
Unfortunately the system works by hashing textures and generating a filename out of this. Placing a .png texture with this filename in Dolphins LOAD folder will override *every* texture with this hash in the game, which can lead to multiple models unintentionally appearing with the same texture mod.

"Untangling" refers to making the hash code of every identical texture unique instead.
This process will run before texture files are written by the export, so the exported file names will already be unique.
You can choose to skip the untangling step in order to retain the old texture behavior (e.g. for old texture mod compatibility)

Untangling is deterministic and the new filenames (hashes) are shared between all untangled games.

# approach

"Untangle mode" will be activated with the additional CLI argument --untangle on a "start.py --export" call
In order to change the hash (ad with this, the file name), the texture in the data block itself needs to change.
First, iterate over all tpl texture files. Ignore other model aspects like GPL, ACT or SKN.

## preparation

First the export needs to check whether there already is a dt_na.dat in the 3_Output_Dat folder.
If not, copy the input dt_na.dat file there. If one is already there, ask if it can be overwritten y/n.
If user enters anything except y, cancel the export. 
If y, continue and copy & overwrtie the dt_na.dat file to the output folder.

## tracking hashes

Over the course of the untangling, the script needs to keep a list of all so far encountered texture hashes in memory.
If the current found texture matches an already contained hash in the list, the texture needs to be changed slightly.
Afterwards, continue writing the png file to disk and patching the memory of dt_na.dat in the output folder directly (in case an untangling took place).

## the texture changes

Important: in this format, "last pixel" e.t.c. is not a stable concept.
Texture payloads are encoded (tiled / indexed / compressed depending on format), and Dolphin naming in this tool is based on the raw encoded texture bytes (and TLUT hash when present), not on decoded linear RGBA pixels.

So the implementation mutates raw image payload bytes, not decoded PNG pixels. It doesn't touch Palette bytes.
Exit condition: if too many changes are attempted on one file and the hash never changes even after many attempts, the script may have to give up, put a warning into the log, and move on with the unchanged file kept and exported.

## the hashing algorithm

The xxh64 algorithm is implemented for exporter png file naming in tpl.py and now the untangling
Hash inputs that matter for uniqueness in current code:

- image_data is always hashed.
- tlut_data is additionally hashed for paletted textures when TLUT is present.

## notes on unised characters

Since unused characters do not have their own model data block, and instead just point to an existing (playable) models data block, an extra step is done beffore untangling.
Each unused character (indices 89-94) has all their data blocks cloned into hammer space at the start of the process. The untangler then works on these "new" hammerspace textures. These changes will take effect iin the output .dat file, which is then used for the main export step.

An overwrite warning is issued if untangle-export is called while a dat or .dol file is already in the output folder.