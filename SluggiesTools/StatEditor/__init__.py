"""Sluggers Stat Editor bridge (Philenarion's stat editor in Bridge Mode).

* ``fields.py``: the one field table (editor field name -> table, byte offset,
  type, range), per character and for the global tables.
* ``bridge.py``: ``stat_bridge.json`` (where the stat tables are in a modded
  ``main.dol``, the characters, the baseline rows).
* ``cli.py``: ``start.py --stat-bridge-export FILE`` / ``--apply-stat-edits FILE...``.
* ``apply.py``: the editor's ``stat_edits.json`` checked and written into ``main.dol``.
* ``carry.py``: roster runs keep stat edits.
* ``editor_install.py``: where the editor is deployed, its bridge files, the update check (no Dear PyGui).
* ``gui_tab.py``: the GUI's "Stat Editor" tab.
"""
