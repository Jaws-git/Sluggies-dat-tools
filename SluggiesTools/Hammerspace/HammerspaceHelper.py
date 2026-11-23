"""Constants of the Hammerspace tools (paths of the input and output game files, DAT sizes and alignment,
the DOL directory table, the FST entry of ``dt_na.dat``), and the set-up every Hammerspace module needs:
``SluggiesTools`` on ``sys.path`` and the logger configured.

The code lives in ``DatAllocation`` (the output DAT as a file), ``DolRecords`` (directory records of
``main.dol``) and ``RouteOps`` (operations that need both). They read these constants at call time, so a
test replaces a path here and a function on the module that defines it.
"""

import os
import sys

# Initialize universal logger in child process.
_HS_TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
if _HS_TOOLS_DIR not in sys.path:
    sys.path.insert(0, _HS_TOOLS_DIR)

import slogger as _slogger
_slogger.configure()

BASE_SIZE      = 715046144      # ~715 MB
CHUNK_SIZE     = 1024 * 1024   # 1 MB read buffer
HS_BUFFER_BYTES = 128  # 128B safety buffer appended after every write
HS_ALIGN_BYTES  = 32    # Required model-block alignment for GPL/SKN hot-path data
OUTPUT_DAT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '3_Output_Dat', 'dt_na.dat'))

_ROOT         = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..'))
INPUT_DAT     = os.path.join(_ROOT, '1_Input',      'dt_na.dat')
INPUT_DOL     = os.path.join(_ROOT, '1_Input',     'main.dol')
OUTPUT_DOL    = os.path.join(_ROOT, '3_Output_Dat', 'main.dol')

# DOL directory table constants (US version)
_DOL_BASE      = 0x80003f00
_DIRS_START    = 0x69C828
_DIRS_END      = 0x69CAD8
_DIRS_COUNT    = (_DIRS_END - _DIRS_START) // 4
_DAT_FNAME_PTR = 0x8067f658
_ENTRY_SIZE    = 48   # 12 × uint32 BE

# FST (File System Table) constants — Mario Super Sluggers (US) disc layout.
# dt_na.dat is FST entry index 1; its size field is at byte offset 0x14.
_FST_INPUT          = os.path.join(_ROOT, '1_Input', 'fst.bin')
_FST_OUTPUT         = os.path.join(_ROOT, '3_Output_Dat', 'fst.bin')
_FST_DAT_ENTRY_IDX  = 1
_FST_DAT_SIZE_OFF   = _FST_DAT_ENTRY_IDX * 12 + 8  # type/name(4) + offset(4) + size(4)
