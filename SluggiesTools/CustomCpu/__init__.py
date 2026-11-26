"""The custom CPU level: one optional 5th button on the exhibition CPU level popup.

* ``settings.py``: every setting (keys, units, ranges, encodings), the checks, ``.slgcpu`` files and the bytes
  a custom match swaps in. No DOL writes.
* ``engine.py``: the DOL side (match-start hook with snapshot swaps, the popup's open and answer stubs, the
  settings record), all in one block of the DOL hammerspace text section.
* ``menu_files.py``: the two ``dt_na.dat`` files (the popup's message table and picture bank).
* ``runner.py``: ``detect`` / ``apply`` for deploys, roster runs and the game options, and the command line.
* ``view.py``: what the GUI tab shows (no Dear PyGui).
"""
