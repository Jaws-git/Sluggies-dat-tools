### Export settings ###

# allowed values: True / False
# extracts dae and texture files. dae supports skeletons, UVs and materials - but they can't be re-imported into the game
EXPORT_DAE_TEX = True
# export Sluggies intermediate files, only supports mesh data right now, but CAN be reimported into the game .dat
EXPORT_SLUGGIES = True

### Export settings end ###



import subprocess
import sys
import os

script = os.path.join(os.path.dirname(__file__), 'SluggiesTools', 'export.py')
subprocess.run(
    [sys.executable, script,
     str(int(EXPORT_DAE_TEX)),
     str(int(EXPORT_SLUGGIES))],
    cwd=os.path.join(os.path.dirname(__file__), 'SluggiesTools'),
    check=True
)

print('\nExport complete. Find your files in the folder "2_Output_Models"')