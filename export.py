import subprocess
import sys
import os

script = os.path.join(os.path.dirname(__file__), 'SluggiesTools', 'export.py')
extra_args = [arg for arg in sys.argv[1:] if arg == '--notex']
subprocess.run(
    [sys.executable, script] + extra_args,
    cwd=os.path.join(os.path.dirname(__file__), 'SluggiesTools'),
    check=True
)

print('\nExport complete. Find your files in the folder "2_Output_Models"')