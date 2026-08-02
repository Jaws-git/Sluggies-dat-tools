import subprocess
import sys
import os
import argparse
import json
import importlib.util

# this file is for dispatching only; patching and export logic live in SluggiesTools

ROOT_DIR = os.path.dirname(__file__)
SEARCH_DIR = os.path.join(ROOT_DIR, '2_Output_Models')
PATCH_SCRIPT = os.path.join(ROOT_DIR, 'SluggiesTools', 'patch_inplace.py')
EXPORT_SCRIPT = os.path.join(ROOT_DIR, 'SluggiesTools', 'export.py')
TOOLS_DIR = os.path.join(ROOT_DIR, 'SluggiesTools')
ICONS_DIR = os.path.join(TOOLS_DIR, 'Icons')
ICON_EXPORT_SCRIPT = os.path.join(ICONS_DIR, 'export_icons.py')
ICON_PATCH_SCRIPT = os.path.join(ICONS_DIR, 'patch_icons_inplace.py')
ICON_ROUTE_PREP_SCRIPT = os.path.join(ICONS_DIR, 'prepare_icon_routes.py')
CUSTOM_ICON_SCRIPT = os.path.join(ICONS_DIR, 'add_custom_icons.py')
HS_DIR = os.path.join(TOOLS_DIR, 'Hammerspace')
HS_HELPER_SCRIPT = os.path.join(HS_DIR, 'HammerspaceHelper.py')
HS_MAIN_SCRIPT = os.path.join(HS_DIR, 'HammerspaceMain.py')


def run_hammerspace_helper():
    subprocess.run([sys.executable, HS_HELPER_SCRIPT], cwd=HS_DIR, check=True)


def run_export(debug=False, notex=False, untangle=False):
    missing = [pkg for pkg in ['numpy', 'collada'] if importlib.util.find_spec(pkg) is None]
    if missing:
        print(f"Missing required packages: {', '.join(missing)}")
        print('Run: pip install numpy pycollada')
        sys.exit(1)

    extra_args = []
    if notex:
        extra_args.append('--notex')
    if debug:
        extra_args.append('--debug')
    if untangle:
        extra_args.append('--untangle')

    subprocess.run(
        [sys.executable, EXPORT_SCRIPT] + extra_args,
        cwd=TOOLS_DIR,
        check=True
    )
    print('\nExport complete. Find your files in the folder "2_Output_Models"')


def run_export_icons(use_output=False):
    required = [
        ('Pillow', 'PIL'),
        ('numpy', 'numpy'),
    ]
    missing = [display for display, module in required if importlib.util.find_spec(module) is None]
    if missing:
        print(f"Missing required packages: {', '.join(missing)}")
        print('Run: pip install Pillow numpy')
        sys.exit(1)

    cmd = [sys.executable, ICON_EXPORT_SCRIPT]
    if use_output:
        dol_path = os.path.join(ROOT_DIR, '3_Output_Dat', 'main.dol')
        dat_path = os.path.join(ROOT_DIR, '3_Output_Dat', 'dt_na.dat')
        if not os.path.exists(dol_path) or not os.path.exists(dat_path):
            print('Missing 3_Output_Dat/main.dol or 3_Output_Dat/dt_na.dat')
            print('Run: python start.py --prepare-icon-routes')
            sys.exit(1)
        cmd += ['--dol-path', dol_path, '--dat-path', dat_path]

    subprocess.run(
        cmd,
        cwd=TOOLS_DIR,
        check=True
    )
    print('\nIcon export complete. Find your files in the folder "2_Output_Models/_ICONS"')


def run_prepare_icon_routes(no_overwrite_copy=False):
    cmd = [sys.executable, ICON_ROUTE_PREP_SCRIPT]
    if no_overwrite_copy:
        cmd.append('--no-overwrite-copy')

    subprocess.run(
        cmd,
        cwd=TOOLS_DIR,
        check=True
    )
    print('\nIcon route prepatch complete. Patched files are in "3_Output_Dat"')


def run_patch_icons(source=None, dry_run=False):
    cmd = [sys.executable, ICON_PATCH_SCRIPT]
    if source:
        cmd.append(source)
    if dry_run:
        cmd.append('--dry-run')

    subprocess.run(cmd, cwd=TOOLS_DIR, check=True)

    if dry_run:
        print('\nIcon reimport dry-run complete. Check metadata [META]/reimport_report.json for details.')
    else:
        print('\nIcon reimport complete. Patched DAT is in the folder "3_Output_Dat"')


def run_add_custom_icons(dry_run=False, diagnostic_stage=None, icon_fit='contain'):
    if importlib.util.find_spec('PIL') is None:
        print('Missing required package: Pillow')
        print('Run: pip install Pillow')
        sys.exit(1)

    cmd = [sys.executable, CUSTOM_ICON_SCRIPT]
    if dry_run:
        cmd.append('--dry-run')
    if diagnostic_stage:
        cmd.extend(('--diagnostic-stage', diagnostic_stage))
    cmd.extend(('--icon-fit', icon_fit))
    subprocess.run(cmd, cwd=TOOLS_DIR, check=True)
    if dry_run:
        print('\nCustom icon dry run complete. No output files were changed.')
    else:
        if diagnostic_stage:
            print(
                f'\nCustom icon diagnostic stage {diagnostic_stage} complete. '
                'Patched files are in "3_Output_Dat".'
            )
        else:
            print('\nCustom icon installation complete. Patched files are in "3_Output_Dat".')


def run_patching(filenames, unpatch=False):
    for filename in filenames:
        matches = [
            os.path.join(root, f)
            for root, _, files in os.walk(SEARCH_DIR)
            for f in files
            if f == filename
        ]

        if not matches:
            print(f"No file named '{filename}' found in {SEARCH_DIR}")
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
            cmd = [sys.executable, HS_MAIN_SCRIPT, found]
            if unpatch:
                cmd.append('--unpatch')
            subprocess.run(cmd, cwd=HS_DIR, check=True)
        else:
            cmd = [sys.executable, PATCH_SCRIPT, found]
            if unpatch:
                cmd.append('--unpatch')
            subprocess.run(cmd, cwd=TOOLS_DIR, check=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description='Central dispatcher for Sluggies patching and export tasks.',
        epilog=(
            'Examples:\n'
            '  python patch.py --export\n'
            '  python patch.py --export --debug --notex --untangle\n'
            '  python patch.py --export --untangle\n'
            '  python start.py --prepare-icon-routes\n'
            '  python start.py --add-custom-icons --dry-run\n'
            '  python start.py --add-custom-icons --custom-icon-stage a\n'
            '  python start.py --add-custom-icons\n'
            '  python patch.py --export-icons\n'
            '  python patch.py --export-icons --use-output\n'
            '  python patch.py --patch-icons\n'
            '  python patch.py --patch-icons --palette-only\n'
            '  python patch.py --patch-icons --shared-mode first-page\n'
            '  python patch.py --patch-icons --dry-run\n'
            '  python patch.py --patch model.sluggies\n'
            '  python patch.py --patch model1.sluggies model2.sluggies\n'
            '  python patch.py --unpatch model.sluggies\n'
            '  python patch.py --hammerspace\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--patch', nargs='+', metavar='FILENAME', help='patch one or more .sluggies files')
    mode.add_argument('--unpatch', nargs='+', metavar='FILENAME', help='restore original data for one or more .sluggies files')
    mode.add_argument('-hs', '--hammerspace', action='store_true', help='change available memory space in outputdt_na.dat')
    mode.add_argument('--export', action='store_true', help='export all models from 1_Input to 2_Output_Models')
    mode.add_argument('--prepare-icon-routes', action='store_true', help='prepare output DOL/DAT copies and apply icon routing prepatch rules')
    mode.add_argument('--add-custom-icons', action='store_true', help='install the complete six-character custom icon pipeline')
    mode.add_argument('--export-icons', action='store_true', help='export character-select icon atlases and metadata to 2_Output_Models/_ICONS')
    mode.add_argument(
        '--patch-icons',
        nargs='?',
        const='',
        metavar='SOURCE',
        help='reimport icon sheets and patch dt_na.dat using metadata from _ICONS (optional SOURCE path)'
    )

    parser.add_argument('--debug', action='store_true', help='export only: write binary blobs as raw byte arrays instead of base64')
    parser.add_argument('--notex', action='store_true', help='export only: skip texture extraction')
    parser.add_argument('--untangle', action='store_true', help='export only: pass untangling flag through to export process')
    parser.add_argument('--use-output', action='store_true', help='export-icons only: read DOL/DAT from 3_Output_Dat instead of 1_Input')
    parser.add_argument('--no-overwrite-copy', action='store_true', help='prepare-icon-routes only: patch existing 3_Output_Dat files without recopying from 1_Input')
    parser.add_argument('--dry-run', action='store_true', help='patch-icons/add-custom-icons: validate without writing bytes')
    parser.add_argument(
        '--custom-icon-stage',
        choices=tuple('abcdefghijk'),
        help='add-custom-icons only: cumulative diagnostic stage to build from pristine inputs',
    )
    parser.add_argument(
        '--icon-fit',
        choices=('contain', 'cover', 'strict'),
        help='add-custom-icons only: fit source artwork into 48x51 slots (default: contain)',
    )

    args = parser.parse_args()

    if args.debug and not args.export:
        parser.error('--debug can only be used with --export.')
    if args.notex and not args.export:
        parser.error('--notex can only be used with --export.')
    if args.untangle and not args.export:
        parser.error('--untangle can only be used with --export.')
    if args.use_output and not args.export_icons:
        parser.error('--use-output can only be used with --export-icons.')
    if args.no_overwrite_copy and not args.prepare_icon_routes:
        parser.error('--no-overwrite-copy can only be used with --prepare-icon-routes.')
    if args.dry_run and not (args.patch_icons is not None or args.add_custom_icons):
        parser.error('--dry-run can only be used with --patch-icons or --add-custom-icons.')
    if args.custom_icon_stage and not args.add_custom_icons:
        parser.error('--custom-icon-stage can only be used with --add-custom-icons.')
    if args.icon_fit and not args.add_custom_icons:
        parser.error('--icon-fit can only be used with --add-custom-icons.')
    if not any([args.patch, args.unpatch, args.hammerspace, args.export, args.prepare_icon_routes, args.add_custom_icons, args.export_icons, args.patch_icons is not None]):
        parser.print_help()
        sys.exit(0)

    return args


def main():
    args = parse_args()
    if args.hammerspace:
        run_hammerspace_helper()
        return
    if args.export:
        run_export(debug=args.debug, notex=args.notex, untangle=args.untangle)
        return
    if args.prepare_icon_routes:
        run_prepare_icon_routes(no_overwrite_copy=args.no_overwrite_copy)
        return
    if args.add_custom_icons:
        run_add_custom_icons(
            dry_run=args.dry_run,
            diagnostic_stage=args.custom_icon_stage,
            icon_fit=args.icon_fit or 'contain',
        )
        return
    if args.export_icons:
        run_export_icons(use_output=args.use_output)
        return
    if args.patch_icons is not None:
        source = args.patch_icons if args.patch_icons != '' else None
        run_patch_icons(
            source=source,
            dry_run=args.dry_run,
        )
        return
    if args.patch:
        run_patching(args.patch, unpatch=False)
        return
    if args.unpatch:
        run_patching(args.unpatch, unpatch=True)


if __name__ == '__main__':
    main()