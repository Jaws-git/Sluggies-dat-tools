"""Sound archive tooling: ``MY2.brsar`` (voices, effects) and the ``stream/*.brstm`` music of Mario Super Sluggers.

Readers (``brsar.py``, ``brstm.py``), the DSP-ADPCM codec they share (``dsp_adpcm.py``: decoder and encoder), a WAV
writer (``wavfile.py``), the exporter child script (``export_sounds.py``, ``start.py --export-sounds``) and the
archive writer (``brsar_write.py``: append wave sounds to ``MY2.brsar``; hand-run ``probe_brsar_write.py``).
"""
