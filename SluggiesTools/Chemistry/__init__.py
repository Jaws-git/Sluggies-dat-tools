"""Character chemistry (the GUI's Chemistry tab): every relationship of ``main.dol``, read, drawn and edited.

* ``read.py``: the chemistry of a DOL per pair of character IDs, and its characters.
* ``view.py``: the tab's model (layers, drafts, marks, mismatch list, the item writer); no Dear PyGui.
* ``layout.py``: grouping and the overview's node positions (numpy, deterministic).

Edits are written by ``StatEditor/apply.py`` (item ``chemistry:<file>`` of ``--apply-stat-edits``).
"""
