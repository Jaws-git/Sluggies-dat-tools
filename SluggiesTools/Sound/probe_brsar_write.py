"""Hand-run probe (not a test): the BRSAR writer on the real archive.

    python SluggiesTools/Sound/probe_brsar_write.py [--archive 1_Input/sound_NA/MY2.brsar] [--out PATH]

1. No additions: ``add_sounds`` returns the bytes unchanged.
2. One copied clip: Bowser's ``cuppa_v1`` RWAV appended to Mario's family file as ``zz_probe_cuppa_v1`` (template
   ``mario_v1``). The result is re-parsed: every stock sound's name, file, entry and wave bytes unchanged, the new
   sound decodes to the same samples as ``cuppa_v1``. With ``--out`` the archive is written there (e.g.
   ``3_Output_Dat/sound_NA/MY2.brsar``) for the Dolphin gate: every stock voice must play as before.
"""

import argparse
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.normpath(os.path.join(HERE, '..'))
ROOT = os.path.normpath(os.path.join(TOOLS, '..'))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import numpy as np                                        # noqa: E402

from Sound import brsar, brsar_write                      # noqa: E402

DEFAULT_ARCHIVE = os.path.join(ROOT, '1_Input', 'sound_NA', 'MY2.brsar')


def sound_map(archive):
    return {s.name: s for s in archive.sounds()}


def stock_signature(archive):
    """Everything a stock sound is: its entry facts and its wave bytes (None for non-waves)."""
    out = []
    for s in archive.sounds():
        rwav = brsar_write.sound_rwav(archive, s) if s.type == brsar.WAVE and archive.files()[s.file_id].positions else None
        out.append((s.index, s.name, s.type, s.file_id, s.player_id, s.volume, s.priority, s.wave_entry, s.bank,
                    hashlib.sha1(rwav).hexdigest() if rwav else None))
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--archive', default=DEFAULT_ARCHIVE)
    parser.add_argument('--out', help='write the archive with the probe clip here')
    args = parser.parse_args(argv)
    data = open(args.archive, 'rb').read()
    a = brsar.Brsar(data)
    print(f'{args.archive}: {len(data)} bytes, sha1 {hashlib.sha1(data).hexdigest()}, {len(a.sounds())} sounds')

    out, ids = brsar_write.add_sounds(data, [])
    print('1. no additions ->', 'identical' if out == data else 'DIFFERENT', ids)

    sounds = sound_map(a)
    src, tmpl = sounds['cuppa_v1'], sounds['mario_v1']
    rwav = brsar_write.sound_rwav(a, src)
    out, ids = brsar_write.add_sounds(data, [brsar_write.NewSound('zz_probe_cuppa_v1', tmpl.file_id, rwav, tmpl.index)])
    b = brsar.Brsar(out)
    print(f'2. one copied clip -> {len(out)} bytes (+{len(out) - len(data)}), new INFO ID {ids}')
    before, after = stock_signature(a), stock_signature(b)[:len(a.sounds())]
    print('   stock sounds unchanged:', before == after)
    new = b.sounds()[ids[0]]
    print(f'   new sound: {new.name} file {new.file_id} entry {new.wave_entry} volume {new.volume} '
          f'(template file {tmpl.file_id}, source file {src.file_id})')
    w_src = brsar.parse_rwav(rwav)
    w_new = brsar.parse_rwav(brsar_write.sound_rwav(b, new))
    d_src, d_new = brsar.decode_waves([w_src, w_new])
    print('   decodes as cuppa_v1:', all(np.array_equal(x, y) for x, y in zip(d_src, d_new)), f'{w_new.samples} samples, {w_new.rate} Hz')
    fa, fb = a.file_bytes(tmpl.file_id), b.file_bytes(tmpl.file_id)
    print(f'   family file {tmpl.file_id}: RWSD {len(fa[0])} -> {len(fb[0])}, RWAR {len(fa[1])} -> {len(fb[1])}')
    others = [f.index for f in a.files() if f.positions and f.index != tmpl.file_id]
    print('   other files byte-identical:', all(a.file_bytes(f) == b.file_bytes(f) for f in others), f'({len(others)} files)')
    ga, gb = a.groups(), b.groups()
    same = [a.b[g.offset:g.offset + g.size] == b.b[h.offset:h.offset + h.size]
            and a.b[g.wave_offset:g.wave_offset + g.wave_size] == b.b[h.wave_offset:h.wave_offset + h.wave_size]
            for g, h in zip(ga, gb)]
    touched = [g.index for g in ga if any(i.file_id == tmpl.file_id for i in g.items)]
    print(f'   groups laid out again: {sum(same)} of {len(same)} byte-identical; differing: '
          f"{[i for i, ok in enumerate(same) if not ok]} (the family file's group(s): {touched})")
    print('   names:', len(a.names), '->', len(b.names), '| sound table:', len(a.sounds()), '->', len(b.sounds()))
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, 'wb') as f:
            f.write(out)
        print('   written:', args.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
