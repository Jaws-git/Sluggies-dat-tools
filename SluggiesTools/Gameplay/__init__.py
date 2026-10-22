"""Gameplay settings (CPU behaviour, star boosts, item odds): static tables in ``main.dol``'s data sections.

* ``tables.py``: the one field table (key -> address, type, labels, checks); no values.
* ``read.py``: every field's value as a DOL holds it.
* ``apply.py``: edit and settings files (and ``reset``) checked and written into ``main.dol``.
* ``legacy.py``: the AI Gameplay Tool's (HTML) save files, migrated on load.
* ``carry.py``: roster runs keep the gameplay settings.
* ``cli.py``: ``start.py --apply-gameplay ITEM...`` / ``--save-gameplay FILE``.
"""
