import subprocess
import sys
import os
import importlib.util

missing = [pkg for pkg in ["numpy", "collada"] if importlib.util.find_spec(pkg) is None]
if missing:
    print(f"Missing required packages: {', '.join(missing)}")
    print("Run: pip install numpy pycollada")
    sys.exit(1)

script = os.path.join(os.path.dirname(__file__), 'SluggiesTools', 'export.py')
extra_args = [arg for arg in sys.argv[1:] if arg in ('--notex', '--debug')]
subprocess.run(
    [sys.executable, script] + extra_args,
    cwd=os.path.join(os.path.dirname(__file__), 'SluggiesTools'),
    check=True
)

print('\nExport complete. Find your files in the folder "2_Output_Models"')