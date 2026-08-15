import subprocess
import sys
import os
import argparse
import json
import importlib.util
import runpy

# this file is for dispatching only; patching and export logic live in SluggiesTools

# ---------------------------------------------------------------------------
# Initialize universal logging BEFORE anything else so every command,
# including invalid invocations, is captured.
# ---------------------------------------------------------------------------
if getattr(sys, 'frozen', False):
    ROOT_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

# Portable Windows builds carry Wiimm's tools beside the application. Put that
# directory first so existing subprocess calls find wimgt without user setup.
_BUNDLED_WIIMMS_BIN = os.path.join(ROOT_DIR, 'tools', 'wiimms-szs-tools', 'bin')
if os.path.isdir(_BUNDLED_WIIMMS_BIN):
    os.environ['PATH'] = _BUNDLED_WIIMMS_BIN + os.pathsep + os.environ.get('PATH', '')

# Ensure SluggiesTools package is importable when start.py is run directly.
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import SluggiesTools.slogger as slogger  # noqa: E402 – must come after sys.path fix

slogger.configure()

# Read batch metadata if StartTools.bat set environment variables.
_BATCH_SOURCE = os.environ.get("SLUGGIES_SOURCE", "")
_BATCH_MENU_SELECTION = os.environ.get("SLUGGIES_MENU_SELECTION", "")
_BATCH_MODEL_FILES = os.environ.get("SLUGGIES_MODEL_FILES", "")
_BATCH_ICON_SHARED_MODE = os.environ.get("SLUGGIES_ICON_SHARED_MODE", "")
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


def python_script_command(script, *args):
    """Return a child-script command that works in source and frozen builds."""
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--_run-script', script, *args]
    return [sys.executable, script, *args]


def run_bundled_script_mode():
    """Run an external project script through the frozen Python runtime."""
    if len(sys.argv) < 2 or sys.argv[1] != '--_run-script':
        return False
    if len(sys.argv) < 3:
        raise SystemExit('--_run-script requires a script path')

    script = os.path.abspath(sys.argv[2])
    if not os.path.isfile(script):
        raise SystemExit(f'Bundled script not found: {script}')
    if os.path.commonpath((ROOT_DIR, script)) != ROOT_DIR:
        raise SystemExit(f'Refusing to run a script outside the application folder: {script}')

    script_dir = os.path.dirname(script)
    tools_dir = os.path.join(ROOT_DIR, 'SluggiesTools')
    for path in (ROOT_DIR, tools_dir, script_dir):
        if path not in sys.path:
            sys.path.insert(0, path)

    sys.argv = [script, *sys.argv[3:]]
    runpy.run_path(script, run_name='__main__')
    return True


def run_hammerspace_helper():
    subprocess.run(python_script_command(HS_HELPER_SCRIPT), cwd=HS_DIR, check=True)


def run_export(debug=False, notex=False, untangle=False, dae=False):
    if importlib.util.find_spec('numpy') is None:
        slogger.error("Missing required package: numpy", source="dispatcher")
        slogger.error("Run: pip install numpy", source="dispatcher")
        sys.exit(1)
    if dae and importlib.util.find_spec('collada') is None:
        slogger.error("Missing required package for --dae export: pycollada", source="dispatcher")
        slogger.error("Run: pip install pycollada", source="dispatcher")
        sys.exit(1)

    extra_args = []
    if notex:
        extra_args.append('--notex')
    if debug:
        extra_args.append('--debug')
    if untangle:
        extra_args.append('--untangle')
    if dae:
        extra_args.append('--dae')

    subprocess.run(
        python_script_command(EXPORT_SCRIPT, *extra_args),
        cwd=TOOLS_DIR,
        check=True
    )
    slogger.info('Export complete. Find your files in the folder "2_Output_Models"', source="dispatcher")


def run_export_icons(use_output=False):
    if importlib.util.find_spec('PIL') is None:
        slogger.error("Missing required package: Pillow", source="dispatcher")
        slogger.error("Run: pip install Pillow", source="dispatcher")
        sys.exit(1)
    if importlib.util.find_spec('numpy') is None:
        slogger.error("Missing required package: numpy", source="dispatcher")
        slogger.error("Run: pip install numpy", source="dispatcher")
        sys.exit(1)

    cmd = python_script_command(ICON_EXPORT_SCRIPT)
    if use_output:
        dol_path = os.path.join(ROOT_DIR, '3_Output_Dat', 'main.dol')
        dat_path = os.path.join(ROOT_DIR, '3_Output_Dat', 'dt_na.dat')
        if not os.path.exists(dol_path) or not os.path.exists(dat_path):
            slogger.error('Missing 3_Output_Dat/main.dol or 3_Output_Dat/dt_na.dat', source="dispatcher")
            slogger.error('Run: python start.py --prepare-icon-routes', source="dispatcher")
            sys.exit(1)
        cmd += ['--dol-path', dol_path, '--dat-path', dat_path]

    subprocess.run(
        cmd,
        cwd=TOOLS_DIR,
        check=True
    )
    slogger.info('Icon export complete. Find your files in the folder "2_Output_Models/_ICONS"', source="dispatcher")


def run_prepare_icon_routes(no_overwrite_copy=False):
    cmd = python_script_command(ICON_ROUTE_PREP_SCRIPT)
    if no_overwrite_copy:
        cmd.append('--no-overwrite-copy')

    subprocess.run(
        cmd,
        cwd=TOOLS_DIR,
        check=True
    )
    slogger.info('Icon route prepatch complete. Patched files are in "3_Output_Dat"', source="dispatcher")


def run_patch_icons(source=None, dry_run=False):
    cmd = python_script_command(ICON_PATCH_SCRIPT)
    if source:
        cmd.append(source)
    if dry_run:
        cmd.append('--dry-run')

    subprocess.run(cmd, cwd=TOOLS_DIR, check=True)

    if dry_run:
        slogger.info('Icon reimport dry-run complete. Check metadata [META]/reimport_report.json for details.', source="dispatcher")
    else:
        slogger.info('Icon reimport complete. Patched DAT is in the folder "3_Output_Dat"', source="dispatcher")


def run_add_custom_icons(dry_run=False, diagnostic_stage=None, icon_fit='contain'):
    if importlib.util.find_spec('PIL') is None:
        slogger.error('Missing required package: Pillow', source="dispatcher")
        slogger.error('Run: pip install Pillow', source="dispatcher")
        sys.exit(1)

    cmd = python_script_command(CUSTOM_ICON_SCRIPT)
    if dry_run:
        cmd.append('--dry-run')
    if diagnostic_stage:
        cmd.extend(('--diagnostic-stage', diagnostic_stage))
    cmd.extend(('--icon-fit', icon_fit))
    subprocess.run(cmd, cwd=TOOLS_DIR, check=True)
    if dry_run:
        slogger.info('Custom icon dry run complete. No output files were changed.', source="dispatcher")
    else:
        if diagnostic_stage:
            slogger.info(
                f'Custom icon diagnostic stage {diagnostic_stage} complete. '
                'Patched files are in "3_Output_Dat".',
                source="dispatcher",
            )
        else:
            slogger.info('Custom icon installation complete. Patched files are in "3_Output_Dat".', source="dispatcher")


def hammerspace_section_args(model):
    """Select rebuild modes required by exported hammerspace edit markers."""
    if any(
        submesh.get('FaceSurfaceIdsEdited') is not None
        for submesh in model.get('Submeshes', [])
    ):
        return ['--gpl', 'build']
    return []


def run_patching(filenames, unpatch=False):
    for filename in filenames:
        matches = [
            os.path.join(root, f)
            for root, _, files in os.walk(SEARCH_DIR)
            for f in files
            if f == filename
        ]

        if not matches:
            slogger.info(f"No file named '{filename}' found in {SEARCH_DIR}", source="dispatcher")
            continue

        found = matches[0]
        slogger.info(f"Found: {found}", source="dispatcher")

        # Read UseHammerspace flag from the .sluggies JSON
        try:
            with open(found, 'r') as f:
                sluggies_data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            slogger.error(f"Could not read '{found}': {e}", source="dispatcher")
            continue

        model = sluggies_data.get('SluggiesModel', {})
        use_hammerspace = model.get('UseHammerspace', False)

        if use_hammerspace:
            cmd = python_script_command(HS_MAIN_SCRIPT, found, *hammerspace_section_args(model))
            if unpatch:
                cmd.append('--unpatch')
            subprocess.run(cmd, cwd=HS_DIR, check=True)
        else:
            cmd = python_script_command(PATCH_SCRIPT, found)
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
            '  python patch.py --export --dae\n'
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
    parser.add_argument('--dae', action='store_true', help='export only: also write .dae model files to disk (always writes .sluggie files)')
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
    if args.dae and not args.export:
        parser.error('--dae can only be used with --export.')
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


def _get_invocation_source() -> str:
    """Return 'StartTools.bat' when batch metadata is present, else 'CLI'."""
    if _BATCH_SOURCE:
        return _BATCH_SOURCE
    return "CLI"


def _log_batch_metadata() -> None:
    """Log batch menu selection and interactive inputs if coming from StartTools.bat."""
    if not _BATCH_SOURCE:
        return
    if _BATCH_MENU_SELECTION:
        slogger.info(
            f"Batch menu selection: {_BATCH_MENU_SELECTION}",
            source="dispatcher",
        )
    if _BATCH_MODEL_FILES:
        slogger.info(
            f"Batch model file input: {_BATCH_MODEL_FILES!r}",
            source="dispatcher",
        )
    if _BATCH_ICON_SHARED_MODE:
        slogger.info(
            f"Batch icon shared-mode input: {_BATCH_ICON_SHARED_MODE!r}",
            source="dispatcher",
        )


def main() -> int:
    """Dispatch user command. Returns 0 on success, 1 on failure."""
    source = _get_invocation_source()

    # 2.1 – Log normalized command before parsing so even invalid invocations
    # are captured.
    slogger.log_command(sys.argv, source=source)

    # 2.3 – Log batch metadata (menu choice, interactive inputs).
    _log_batch_metadata()

    try:
        args = parse_args()
    except SystemExit as exc:
        # argparse calls sys.exit on --help (code 0) or on parser.error (code 2).
        code = exc.code if isinstance(exc.code, int) else 1
        if code != 0:
            slogger.info(f"Command exited with code {code}", source="dispatcher")
        return code

    try:
        if args.hammerspace:
            run_hammerspace_helper()
        elif args.export:
            run_export(debug=args.debug, notex=args.notex, untangle=args.untangle, dae=args.dae)
        elif args.prepare_icon_routes:
            run_prepare_icon_routes(no_overwrite_copy=args.no_overwrite_copy)
        elif args.add_custom_icons:
            run_add_custom_icons(
                dry_run=args.dry_run,
                diagnostic_stage=args.custom_icon_stage,
                icon_fit=args.icon_fit or 'contain',
            )
        elif args.export_icons:
            run_export_icons(use_output=args.use_output)
        elif args.patch_icons is not None:
            source_path = args.patch_icons if args.patch_icons != '' else None
            run_patch_icons(
                source=source_path,
                dry_run=args.dry_run,
            )
        elif args.patch:
            run_patching(args.patch, unpatch=False)
        elif args.unpatch:
            run_patching(args.unpatch, unpatch=True)

        slogger.info("Command completed successfully", source="dispatcher")
        return 0
    except KeyboardInterrupt:
        slogger.info("Command interrupted by user", source="dispatcher")
        return 130
    except Exception as exc:
        slogger.exception(
            "Unexpected failure during command execution",
            source="dispatcher",
            exc=exc,
        )
        return 1


if __name__ == '__main__':
    if not run_bundled_script_mode():
        rc = main()
        if rc != 0:
            sys.exit(rc)
