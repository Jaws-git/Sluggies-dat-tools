"""Built-in template capture provenance (manual probe).

PLAN_AddSubmesh.md decision 9: every entry in
``build_template_source_fixture.BUILTIN_TEMPLATES`` is a byte-exact capture of
one whole vanilla rigid draw list. This re-checks each capture against the
export it was taken from, named by the entry's own ``Provenance``.

This is a **probe, not a unit test**: it compares checked-in constants against
real exports under ``2_Output_Models/``, which are gitignored working files
(see `.clinerules`, "tests never read production data"). A test that reads
them can only skip in CI while rotting silently on the one machine that has
them. The registry's self-consistency -- stored hashes, record shape, and the
Blender-side ``TemplateSources`` mirror -- is covered synthetically in
``tests/test_template_source_fixture.py``; only the tie back to the vanilla
source lives here.

Run it after capturing a new built-in, or after re-exporting a model a
built-in was captured from:

    uv run --project SluggiesTools/_build python SluggiesTools/probe_builtin_template_captures.py

Exits 0 when every capture that has its export present still matches, 1 on a
mismatch. A built-in whose source export is not in this checkout is reported
as unchecked, not as a failure.
"""
from __future__ import annotations

import json
import pathlib
import sys

TOOLS_DIR = pathlib.Path(__file__).resolve().parent
HAMMERSPACE_DIR = TOOLS_DIR / 'Hammerspace'
for _path in (TOOLS_DIR, HAMMERSPACE_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import build_template_source_fixture as tsf  # noqa: E402

MODELS_DIR = TOOLS_DIR.parent / '2_Output_Models'


def capture_exports(provenance: dict) -> list[pathlib.Path]:
    """Locate a built-in's capture export from its provenance.

    Matched by prefix because some exported folder names carry the game's own
    mojibake (Toadette's `kinopico.gplp`).
    """
    folder, _sep, model = provenance['Model'].partition('/')
    return sorted((MODELS_DIR / folder).glob(f'{model}*/*.sluggie'))


def run_capture_provenance_probe() -> int:
    """Check every built-in against its vanilla source; return an exit code."""
    checked: list[str] = []
    unchecked: list[str] = []
    failures: list[str] = []

    for name, template in tsf.BUILTIN_TEMPLATES.items():
        provenance = template['Provenance']
        exports = capture_exports(provenance)
        if not exports:
            unchecked.append(f"{name} ({provenance['Model']})")
            continue

        model = json.loads(exports[0].read_text(encoding='utf-8'))['SluggiesModel']
        source = next(
            (sub for sub in model['Submeshes'] if sub['MeshName'] == provenance['MeshName']),
            None,
        )
        if source is None:
            failures.append(f"{name}: no submesh named {provenance['MeshName']!r} in {exports[0]}")
            continue

        comp_count = int(source['VertexBuffer']['VertexBufferCompCount'])
        if comp_count != 3:
            failures.append(f'{name}: source submesh is not rigid (CompCount {comp_count})')
            continue

        surface_id = source['DisplayStates'][-1]['SurfaceId']
        if surface_id != provenance['SurfaceId']:
            failures.append(
                f"{name}: source's last surface is {surface_id!r}, "
                f"provenance says {provenance['SurfaceId']!r}"
            )
            continue

        records = tuple(tsf._record(state) for state in source['DisplayStates'])
        if records != template['States']:
            failures.append(
                f'{name}: capture no longer matches its source\n'
                f'    stored: {template["States"]}\n'
                f'    source: {records}'
            )
            continue

        checked.append(name)

    print(
        f'[captures] {len(tsf.BUILTIN_TEMPLATES)} built-in(s): {len(checked)} verified, '
        f'{len(unchecked)} unchecked (export not in this checkout), {len(failures)} failed'
    )
    if unchecked:
        print(f"[captures] unchecked: {', '.join(unchecked)}")
    if failures:
        print('[captures] FAIL:')
        for line in failures:
            print(f'  {line}')
        return 1
    if not checked:
        print('[captures] nothing verified: no built-in capture exports present in this checkout')
    else:
        print('[captures] OK')
    return 0


if __name__ == '__main__':
    sys.exit(run_capture_provenance_probe())
