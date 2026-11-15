"""Model export: ``1_Input`` (``dt_na.dat`` + ``main.dol``) into ``2_Output_Assets``.

``start.py`` launches this file as a child script with ``SluggiesTools`` as the working directory; the
``../1_Input``, ``../2_Output_Assets`` and ``../3_Output_Dat`` paths below are relative to it. Flags:
``--notex``, ``--untangle``, ``--glb``, ``--anim-glb``, ``--debug``. ``main(argv)`` is the whole run and returns
the exit code; importing the module does nothing.

What is here: the run (prompts, the ``--untangle`` preparation of ``3_Output_Dat``, the loop over the
directories, the report, the exit code). The ``.sluggie`` document builders are in ``sluggie_document.py`` and
the directory facts in ``dol_dirs.py``; their names, and the reader classes of ``model0.py``, stay importable
from this module (``__all__``).
"""
from base import File
from anm import ANM
from model0 import (
    Archive, EXPORT_FAILURES, ExpectedFormatSkip, MaybeArchive, Model0, UNTANGLE_SKIP_STADIUMS,
    set_log_dir_index,
)
import contextlib
import os
import shutil
import sys

import slogger as _slogger
import sluggie_document
from dol_dirs import (
    DAT_FNAME_PTR, DIRS_END, DIRS_LEN, DIRS_START, DIR_PTR_PTRS,
    folderNameMap, load_dol_dirs, top_level_folder_name,
)
from sluggie_document import (
    compact_faces_json,
    extract_act_header, extract_act_user_data, extract_bone_data, extract_facial_pose_data,
    extract_gpl_userdata, extract_skin_data, extract_submeshes, extract_tex_header,
    extract_texture_descriptors, extract_trailing_sections,
    model_document, write_model_document,
)

_HS_DIR = os.path.join(os.path.dirname(__file__), 'Hammerspace')
if _HS_DIR not in sys.path:
    sys.path.insert(0, _HS_DIR)
import UntanglePolicy  # noqa: E402
from anm_to_gltf import convert_anm_directory  # noqa: E402

# Every name other modules took from here before the split (Roster/slot_export.py reads the reader classes,
# ``model_document`` and ``write_model_document`` from this module).
__all__ = [
    'ANM', 'Archive', 'ExpectedFormatSkip', 'MaybeArchive', 'Model0', 'set_log_dir_index',
    'DAT_FNAME_PTR', 'DIRS_END', 'DIRS_LEN', 'DIRS_START', 'DIR_PTR_PTRS',
    'folderNameMap', 'load_dol_dirs', 'top_level_folder_name',
    'compact_faces_json',
    'extract_act_header', 'extract_act_user_data', 'extract_bone_data', 'extract_facial_pose_data',
    'extract_gpl_userdata', 'extract_skin_data', 'extract_submeshes', 'extract_tex_header',
    'extract_texture_descriptors', 'extract_trailing_sections',
    'model_document', 'write_model_document',
    'STADIUM_DIR_INDICES', 'UNUSED_DIRS_TO_UNTANGLE_CLONE',
    'Dat', 'clone_unused_dirs_to_hammerspace_for_untangle', 'confirm_overwrite', 'export_directories',
    'main', 'open_export_dat', 'prepare_untangle_output_files', 'write_texture_hash_overlaps_report',
]

# Model directory indices that hold stadiums (see folderNameMap):
# 7 Mario Stadium ... 16 Toy Field. When UNTANGLE_SKIP_STADIUMS is enabled,
# texture untangling is bypassed for these directories so their texture
# names/bytes in the dat are left untouched.
STADIUM_DIR_INDICES = UntanglePolicy.STADIUM_DIRS
UNUSED_DIRS_TO_UNTANGLE_CLONE = list(UntanglePolicy.UNUSED_CHARACTER_DIRS)

outdir = "../2_Output_Assets/"
_export_placeholder = 'exports will be created here'


def confirm_overwrite():
    """Ask before overwriting earlier exports; False when the user declines."""
    existing = (
        [f for f in os.listdir(outdir) if f != _export_placeholder]
        if os.path.exists(outdir) else []
    )
    if existing:
        answer = _slogger.ask(
            'Previous export files already exist in 2_Output_Assets. '
            'Continue and overwrite? (y/n): ',
            kind='overwrite',
        ).strip().lower()
        _slogger.log_user_input('Model export overwrite confirm', answer, source='export')
        if answer != 'y':
            _slogger.info('Model export canceled by user.', source='export')
            return False
    else:
        os.makedirs(outdir, exist_ok=True)
    return True


def _hammerspace_helper():
    """``HammerspaceHelper``, imported when ``--untangle`` needs it: an export without ``--untangle`` runs
    without that module, and an error in importing it shows as the error it is."""
    import HammerspaceHelper
    return HammerspaceHelper


def clone_unused_dirs_to_hammerspace_for_untangle(input_dol_path, input_dat_path, report_lines=None):
    """Clone all model entries in dirs 89-94 into hammerspace and repoint output main.dol.

    This decouples unused character directories from shared offsets before
    texture untangling so each referenced model block can be modified independently.
    A record that cannot be repointed raises (``HammerspaceHelper.DolEntryError``) and ends the export.
    """
    hh = _hammerspace_helper()

    source_dirs = load_dol_dirs(input_dol_path)

    clone_entries = []
    for dir_ind in UNUSED_DIRS_TO_UNTANGLE_CLONE:
        for file_index, file in enumerate(source_dirs.get(dir_ind, [])):
            length = file['en'][1]
            if length > 0:
                clone_entries.append((dir_ind, file_index, file))

    # Reserve one run up front. Re-running first-fit search after every write
    # rescans all preceding clone data and makes this pre-pass quadratic.
    required_growth = sum(
        file['en'][1] + hh.HS_BUFFER_BYTES + hh.HS_ALIGN_BYTES - 1
        for _, _, file in clone_entries
    )

    base_size = os.path.getsize(hh.OUTPUT_DAT) if os.path.exists(hh.OUTPUT_DAT) else hh.BASE_SIZE
    reserve = 1024 * 1024
    required_total_size = max(base_size, hh.BASE_SIZE) + required_growth + reserve
    if not hh.ensureOutputDat(required_total_size):
        raise RuntimeError('Unable to prepare output dt_na.dat for untangle clone step.')
    hh.patchFstFileSize(os.path.getsize(hh.OUTPUT_DAT))

    # Live hammerspace blocks can end in zero padding that the zero-byte scan
    # would otherwise treat as free (see WriteModelBlock).
    reserved_ranges = hh.routedHammerspaceRanges()
    allocation_start = hh.findFreeMemoryChunk(required_growth, reserved_ranges=reserved_ranges)
    if allocation_start == -1:
        grow_by = max(required_growth + reserve, 64 * 1024 * 1024)
        next_size = os.path.getsize(hh.OUTPUT_DAT) + grow_by
        if not hh.ensureOutputDat(next_size):
            raise RuntimeError('Unable to expand output dt_na.dat for untangle clone step.')
        hh.patchFstFileSize(os.path.getsize(hh.OUTPUT_DAT))
        allocation_start = hh.findFreeMemoryChunk(required_growth, reserved_ranges=reserved_ranges)
        if allocation_start == -1:
            raise RuntimeError(
                f'Unable to reserve a contiguous hammerspace region ({required_growth} bytes).'
            )

    clone_count = 0
    next_offset = allocation_start
    with open(input_dat_path, 'rb') as input_dat:
        for dir_ind, file_index, file in clone_entries:
            src_offset = file['en'][0]
            src_length = file['en'][1]
            new_offset = (
                next_offset + hh.HS_ALIGN_BYTES - 1
            ) & ~(hh.HS_ALIGN_BYTES - 1)

            input_dat.seek(src_offset, 0)
            block = input_dat.read(src_length)
            if len(block) != src_length:
                raise RuntimeError(
                    f'Failed reading source model block for dir {dir_ind} file {file_index}: '
                    f'expected {src_length} bytes, got {len(block)}'
                )

            hh.writeModelBlock(block, new_offset)
            hh.patchDolEntry(dir_ind, file_index, new_offset, src_length)
            next_offset = new_offset + src_length + hh.HS_BUFFER_BYTES
            clone_count += 1

            if report_lines is not None:
                report_lines.append(
                    f'Hammerspace clone: dir {dir_ind} file {file_index} '
                    f'0x{src_offset:x} -> 0x{new_offset:x} ({src_length} bytes)'
                )

    return clone_count


def write_texture_hash_overlaps_report(output_dir, untangle_report_lines=None, untangle_warnings=None):
    """Write repeated PNG filename counts under the export root.

    Any PNG basename encountered more than once anywhere below output_dir is
    reported once in texture_hash_overlaps.txt.
    """
    report_path = os.path.join(output_dir, 'texture_hash_overlaps.txt')
    png_counts = {}

    for root, _dirs, files in os.walk(output_dir):
        for fname in files:
            if not fname.lower().endswith('.png'):
                continue
            png_counts[fname] = png_counts.get(fname, 0) + 1

    duplicate_names = sorted(
        (name, count) for name, count in png_counts.items() if count > 1
    )

    with open(report_path, 'w', encoding='utf-8') as report_f:
        for name, count in duplicate_names:
            report_f.write(f'{name} (x{count} identical duplicates)\n')
        if untangle_report_lines:
            report_f.write('\n')
            report_f.write('Untangle updates:\n')
            for line in untangle_report_lines:
                report_f.write(line + '\n')
        if untangle_warnings:
            report_f.write('\n')
            report_f.write('Untangle warnings:\n')
            for line in untangle_warnings:
                report_f.write(line + '\n')


class Dat(File):
    def __init__(self, f):
        super().__init__(f)


@contextlib.contextmanager
def open_export_dat(path, untangle_context=None):
    """The ``Dat`` reader on the DAT the export reads, closed when the block ends or raises.

    With an untangle context the file is the output DAT and the one handle serves both directions: it
    becomes the context's ``dat_output_handle``, so the bytes untangling writes are the bytes every later
    read of that block gets. (Two handles on the same file, each with its own buffer, could hand a reader
    the bytes from before a write.)"""
    with open(path, 'r+b' if untangle_context is not None else 'rb') as handle:
        if untangle_context is not None:
            untangle_context['dat_output_handle'] = handle
        yield Dat(handle)


def prepare_untangle_output_files(input_dir='../1_Input', output_dat_dir='../3_Output_Dat'):
    """Copy the input ``dt_na.dat`` and ``main.dol`` over the output ones (asks first when one exists);
    ``(output DAT path, output DOL path)``, or ``(None, None)`` when the user declines."""
    output_dat_path = os.path.join(output_dat_dir, 'dt_na.dat')
    output_dol_path = os.path.join(output_dat_dir, 'main.dol')
    input_dat_path = input_dir + '/dt_na.dat'
    input_dol_path = input_dir + '/main.dol'

    if not os.path.exists(output_dat_dir):
        os.mkdir(output_dat_dir)

    if os.path.exists(output_dat_path) or os.path.exists(output_dol_path):
        answer = _slogger.ask('Untangle mode will overwrite 3_Output_Dat/dt_na.dat and main.dol. Continue? (y/n): ',
                              kind='overwrite').strip().lower()
        _slogger.log_user_input(
            'Untangle overwrite confirm', answer, source='export'
        )
        if answer != 'y':
            _slogger.info('Untangle export canceled by user.', source='export')
            return None, None

    # Each through a temporary file: a killed copy of the 715 MB DAT must not leave a short dt_na.dat that
    # every later patch takes for a complete one.
    for source, target in ((input_dat_path, output_dat_path), (input_dol_path, output_dol_path)):
        temp = target + '.copying'
        try:
            shutil.copyfile(source, temp)
            os.replace(temp, target)
        except BaseException:
            if os.path.exists(temp):
                os.remove(temp)
            raise
    return output_dat_path, output_dol_path


def export_directories(dat, dirs, untangle_context=None, *, export_tex=True, export_glb=False,
                       export_anim=False, untangle_tex=False):
    """Export every directory of ``dirs`` (``load_dol_dirs``) read through ``dat`` into ``outdir``; the
    ``(dir, file index, error)`` of each entry that is a model and still could not be written."""
    failed_entries = []
    for dir_ind, file_arr in dirs.items():
        set_log_dir_index(dir_ind)
        if not untangle_tex and dir_ind in UNUSED_DIRS_TO_UNTANGLE_CLONE:
            _slogger.info(
                'Skipping unused character (no untangle).',
                source=f'export.dir{dir_ind}'
            )
            continue
        dir_dir = outdir + top_level_folder_name(dir_ind) + '/'
        if not os.path.exists(dir_dir):
            os.mkdir(dir_dir)
        # Stadiums (dirs 7-16) keep their original texture names and bytes:
        # pass a None untangle context so no renaming or dat rewriting happens.
        dir_untangle_context = untangle_context
        if UNTANGLE_SKIP_STADIUMS and dir_ind in STADIUM_DIR_INDICES:
            dir_untangle_context = None
            _slogger.info(
                'Skipping texture untangling for stadium directory.',
                source=f'export.dir{dir_ind}'
            )
        for file_index, file in enumerate(file_arr):
            languages = ['en']
            # if file['en'][0] != file['sp'][0]:
            #     languages = ['en', 'sp', 'fr']
            is_model = False     # the entry parsed as a model: a failure after that is a failed export
            try:
                for lan in languages:
                    offset = file[lan][0]
                    length = file[lan][1]
                    lan_dir = dir_dir
                    if len(languages) > 1:
                        lan_dir += lan + '/'
                        if not os.path.exists(lan_dir):
                            os.mkdir(lan_dir)
                    child = dat.add_child(offset, length, MaybeArchive)
                    child.analyze()
                    if child.child:
                        if isinstance(child.child, ANM):
                            if export_anim:
                                child.child.dumpRaw(dir_dir, file_index, folderNameMap.get(str(dir_ind)))
                        else:
                            child.child.analyze()
                            is_model = True
                            child.child.toFile(lan_dir, export_tex=export_tex, export_glb=export_glb,
                                               untangle_context=dir_untangle_context)
                            if isinstance(child.child, Archive):
                                archive_dir = os.path.join(lan_dir, str(child.child.absolute))
                                for i in child.child.success:
                                    sub_model = child.child.files[i]
                                    sub_dir = os.path.join(archive_dir, sub_model.name)
                                    write_model_document(
                                        model_document(sub_model, dir_ind, file_index, sub_model.absolute,
                                                       sub_model.length, dir_untangle_context),
                                        os.path.join(sub_dir, f"{sub_model.name}.sluggie"))
                            else:
                                model_name = child.child.name
                                model_dir = os.path.join(lan_dir, model_name)
                                write_model_document(
                                    model_document(child.child, dir_ind, file_index, offset, length,
                                                   dir_untangle_context),
                                    os.path.join(model_dir, f"{model_name}.sluggie"))
                    del child
            except ExpectedFormatSkip as exc:
                _slogger.warning(f'{exc}', source=f'export.dir{dir_ind}')
            except Exception as e:
                if is_model:
                    _slogger.error(f'file {file_index}: the model could not be written: {type(e).__name__}: {e}',
                                   source=f'export.dir{dir_ind}')
                    failed_entries.append((dir_ind, file_index, f'{type(e).__name__}: {e}'))
                else:           # not a model at all (other data, a short read): skipped, as before
                    _slogger.warning(f'skipping entry: {type(e).__name__}: {e}', source=f'export.dir{dir_ind}')
            finally:
                # The Dat keeps every entry it handed out; without this the export held all parsed models
                # to the end (measured 2026-11-12: about 10 MB per character directory).
                dat.children.clear()
        if export_anim and export_glb and os.path.isdir(os.path.join(dir_dir, 'anm')):
            convert_anm_directory(dir_dir)
        if len(os.listdir(dir_dir)) == 0:
            os.rmdir(dir_dir)
        _slogger.info('Finished exporting', source=f'export.dir{dir_ind}')
    return failed_entries


def main(argv=None) -> int:
    """Run the export with the flags in ``argv`` (default: the command line); the exit code."""
    argv = sys.argv[1:] if argv is None else list(argv)
    # Initialize universal logger in child process.
    _slogger.configure()
    export_tex = '--notex' not in argv
    untangle_tex = '--untangle' in argv
    export_glb = '--glb' in argv
    export_anim = '--anim-glb' in argv  # raw anm/*.anm; their .glb files also need --glb
    sluggie_document.set_compression('--debug' not in argv)

    if not confirm_overwrite():
        return 0
    untangle_context = None
    active_dol_path = '../1_Input/main.dol'
    active_dat_path = '../1_Input/dt_na.dat'

    if untangle_tex:
        if not export_tex:
            _slogger.warning('--untangle has no effect with --notex; untangle mode disabled.', source='export')
        else:
            _slogger.info('Starting texture untangling process...', source='export')
            _hammerspace_helper()       # an import error shows here, before the output files are replaced
            untangle_output_path, untangle_output_dol_path = prepare_untangle_output_files()
            if untangle_output_path is None:
                return 0

            untangle_bootstrap_report = []
            clone_count = clone_unused_dirs_to_hammerspace_for_untangle(
                input_dol_path='../1_Input/main.dol',
                input_dat_path='../1_Input/dt_na.dat',
                report_lines=untangle_bootstrap_report,
            )

            active_dol_path = untangle_output_dol_path
            active_dat_path = untangle_output_path

            untangle_context = {
                'enabled': True,
                'seen_names': set(),
                'handled_images': {},
                'report_lines': [
                    f'Hammerspace clone pre-pass complete: cloned {clone_count} entries from dirs 89-94.'
                ] + untangle_bootstrap_report,
                'warnings': [],
                'name_overrides': {},
                'max_attempts': 8192,
                'dat_output_handle': None,      # open_export_dat sets it: the handle the export reads through
            }
            _slogger.info(
                'Hammerspace duplication complete. Unused character textures are now independent.',
                source='export'
            )

    # The export is now fully confirmed (any "overwrite 3_Output_Dat?" prompt has
    # been answered). Clear the running log so the new log starts where this
    # export begins.
    _slogger.clear_log_file()
    if export_tex and shutil.which('wimgt') is None:
        # Without it every textured model fails one by one, and its .sluggie names PNGs that do not exist.
        _slogger.error('wimgt (Wiimms SZS Tools) was not found on PATH: it decodes the textures. Install it, '
                       'or export without textures (--notex).', source='export')
        return 1
    _slogger.info('Starting model export...', source='export')
    dirs = load_dol_dirs(active_dol_path)
    with open_export_dat(active_dat_path, untangle_context) as dat:
        # (dir, file index, error) of entries that are models and still failed
        failed_entries = export_directories(
            dat, dirs, untangle_context,
            export_tex=export_tex, export_glb=export_glb, export_anim=export_anim, untangle_tex=untangle_tex)

    write_texture_hash_overlaps_report(
        outdir,
        untangle_report_lines=(untangle_context or {}).get('report_lines'),
        untangle_warnings=(untangle_context or {}).get('warnings')
    )

    # Entries that are no models are skipped silently above; a model that failed is an error of the run.
    failures = [f'{name}: {error}' for name, error in EXPORT_FAILURES]
    failures += [f'dir {d} file {i}: {error}' for d, i, error in failed_entries]
    if failures:
        shown = '; '.join(failures[:10]) + (f'; and {len(failures) - 10} more' if len(failures) > 10 else '')
        _slogger.error(f'{len(failures)} model(s) could not be exported: {shown}', source='export')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
