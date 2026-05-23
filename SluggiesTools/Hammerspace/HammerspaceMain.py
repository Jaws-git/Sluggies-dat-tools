import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import HammerspaceHelper as hh
from HammerspaceChunkBuilder import cloneModelToHammerspace


if __name__ == '__main__':
    import argparse as _ap
    import json as _json

    _parser = _ap.ArgumentParser(description='Clone or remove a model block in hammerspace.')
    _parser.add_argument('sluggies_path', help='Path to the .sluggies file')
    _parser.add_argument('--unpatch', action='store_true', help='Remove the model from hammerspace and restore the original DOL entry')
    _args = _parser.parse_args()

    with open(_args.sluggies_path, 'r') as _f:
        _data = _json.load(_f)
    _model = _data['SluggiesModel']
    _chunk = _model['ChunkNumber']
    _index = _model['FileIndex']

    if _args.unpatch:
        _success = hh.removeModelFromHammerspace(_chunk, _index)
    else:
        _success = cloneModelToHammerspace(_chunk, _index)
    raise SystemExit(0 if _success else 1)
