"""Sluggers Stat Editor bridge (Philenarion's stat editor in Bridge Mode).

* ``fields.py``: the one field table (editor field name -> table, byte offset,
  type, range), per character and for the global tables.
* ``bridge.py``: ``stat_bridge.json`` (where the stat tables are in a modded
  ``main.dol``, the characters, the baseline rows).
* ``cli.py``: ``start.py --stat-bridge-export FILE``.
"""
