"""Sound archive tooling: ``MY2.brsar`` (voices, effects) and the ``stream/*.brstm`` music of Mario Super Sluggers.

Readers (``brsar.py``, ``brstm.py``), the DSP-ADPCM codec they share (``dsp_adpcm.py``: decoder and encoder), a WAV
writer (``wavfile.py``; ``clips.read_wav`` reads, other formats through ``ffmpeg.py``), the exporter child script (``export_sounds.py``,
``start.py --export-sounds``), the writers (``brsar_write.py``: append or replace wave sounds in ``MY2.brsar``,
hand-run ``probe_brsar_write.py``; ``brstm_write.py``: re-encode a music stream) and the user libraries on top of
them: custom voice clips and packs (``clips.py``, ``sound_packs.py``), sound effect replacements (``effects.py``)
and per-sound volumes (``volumes.py``).
"""
