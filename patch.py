import subprocess
import sys
import os
import argparse
import json

# this file is for dispatching only, the patching logic itself is found in patch_inplace.py

parser = argparse.ArgumentParser(
    description='Patch .dat files with sluggies intermediate model files.',
    epilog=(
        'Examples:\n'
        '  python patch.py model.sluggies\n'
        '  python patch.py model1.sluggies model2.sluggies model3.sluggies\n'
        '  python patch.py --unpatch model.sluggies\n'
        '  python patch.py --hammerspace\n'
    ),
    formatter_class=argparse.RawDescriptionHelpFormatter
)
parser.add_argument('filenames', nargs='*', help='.sluggies file name(s) to patch')
parser.add_argument('-u', '--unpatch', action='store_true', help='restore original vertex data instead of writing edited data')
parser.add_argument('-hs','--hammerspace', action='store_true', help='change the available memory space in the outputdt_na.dat file')
args = parser.parse_args()

if args.hammerspace and (args.filenames or args.unpatch):
    parser.error("--hammerspace cannot be combined with any other arguments.")

if not args.filenames and not args.hammerspace:
    parser.print_help()
    sys.exit(0)

search_dir = os.path.join(os.path.dirname(__file__), '2_Output_Models')
script = os.path.join(os.path.dirname(__file__), 'SluggiesTools', 'patch_inplace.py')

if args.hammerspace and not args.filenames:
    script = os.path.join(os.path.dirname(__file__), 'SluggiesTools', 'Hammerspace', 'HammerspaceHelper.py')
    cmd = [sys.executable, script]
    subprocess.run(
        cmd,
        cwd=os.path.join(os.path.dirname(__file__), 'SluggiesTools', 'Hammerspace'), 
        check=True
    )

hs_script = os.path.join(os.path.dirname(__file__), 'SluggiesTools', 'Hammerspace', 'HammerspaceMain.py')
hs_cwd    = os.path.join(os.path.dirname(__file__), 'SluggiesTools', 'Hammerspace')

for filename in args.filenames:
    matches = [
        os.path.join(root, f)
        for root, _, files in os.walk(search_dir)
        for f in files
        if f == filename
    ]

    if not matches:
        print(f"No file named '{filename}' found in {search_dir}")
        continue

    found = matches[0]
    print(f"Found: {found}")

    # Read UseHammerspace flag from the .sluggies JSON
    try:
        with open(found, 'r') as f:
            sluggies_data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: Could not read '{found}': {e}")
        continue

    model = sluggies_data.get('SluggiesModel', {})
    use_hammerspace = model.get('UseHammerspace', False)

    if use_hammerspace:
        cmd = [sys.executable, hs_script, found]
        if args.unpatch:
            cmd.append('--unpatch')
        subprocess.run(cmd, cwd=hs_cwd, check=True)
    else:
        cmd = [sys.executable, script, found]
        if args.unpatch:
            cmd.append('--unpatch')
        subprocess.run(
            cmd,
            cwd=os.path.join(os.path.dirname(__file__), 'SluggiesTools'),
            check=True
        )