import subprocess
import sys
import os

EXPORT_DAE = True
EXPORT_TEX = True
EXPORT_JSON = True

script = os.path.join(os.path.dirname(__file__), 'SluggiesTools', 'export.py')
subprocess.run(
    [sys.executable, script,
     str(int(EXPORT_DAE)),
     str(int(EXPORT_TEX)),
     str(int(EXPORT_JSON))],
    cwd=os.path.join(os.path.dirname(__file__), 'SluggiesTools'),
    check=True
)

