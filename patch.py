import subprocess
import sys
import os

search_dir = os.path.join(os.path.dirname(__file__), '2_Output_Models')
script = os.path.join(os.path.dirname(__file__), 'SluggiesTools', 'patch_dat.py')

filename = input("\nEnter .sluggies file name to use for patching: ").strip()

matches = [
    os.path.join(root, f)
    for root, _, files in os.walk(search_dir)
    for f in files
    if f == filename
]

if not matches:
    print(f"No file named '{filename}' found in {search_dir}")
    sys.exit(1)

found = matches[0]
print(f"Found: {found}")

subprocess.run(
    [sys.executable, script, found],
    cwd=os.path.join(os.path.dirname(__file__), 'SluggiesTools'),
    check=True
)
