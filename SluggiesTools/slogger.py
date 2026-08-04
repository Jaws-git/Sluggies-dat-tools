"""Universal logging module for Sluggies tools.

Message Contract
----------------
Every log record follows a single, stable text format:

    [YYYY-MM-DD HH:MM:SS] [Severity] [Source] Message text

Fields
~~~~~~
- Timestamp: Local time with second-level precision, ISO-like format.
- Severity:  One of ``Info``, ``Warning``, or ``Error``.
- Source:    The originating tool or module name (e.g. ``export``,
             ``patch_inplace``, ``icons.export_icons``).
- Message:   The human-readable message text.

Multiline messages
~~~~~~~~~~~~~~~~~~
The first line carries the full timestamp and severity header. Continuation
lines are indented with four spaces and do NOT repeat the timestamp prefix,
so a multiline summary remains readable both in the console and in the log
file as one logical record.

Severity Rules
~~~~~~~~~~~~~~
- **Info**:    Normal progress, completion summaries, selected options,
               file paths, counts, optional diagnostic detail.
- **Warning**: Recoverable unexpected conditions, skipped data, clamped
               values, overwrite notices, operations that continue with
               reduced functionality.
- **Error**:   Failed commands, invalid required input, missing required
               files or dependencies, exceptions, operations that abort
               or return failure.

After migration callers must select severity explicitly. The formatter does
NOT infer severity from message prefixes such as ``WARNING:`` or ``ERROR:``.

All three severities are sent to stdout to preserve current console behavior
and ordering.
"""

from __future__ import annotations

import datetime
import logging
import os
import sys
import traceback
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Allowed severity labels -- must match the display names used in log lines.
SEVERITY_INFO = "Info"
SEVERITY_WARNING = "Warning"
SEVERITY_ERROR = "Error"
ALL_SEVERITIES = (SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_ERROR)

# Timestamp format string -- local time, second precision.
TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S"

# Default log file location relative to the project root.
DEFAULT_LOG_DIR_NAME = "3_Output_Dat"
DEFAULT_LOG_FILE_NAME = "SluggiesTools.log"

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

_initialized: bool = False
_root_logger: Optional[logging.Logger] = None
_log_file_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Custom formatter
# ---------------------------------------------------------------------------

class _SluggiesFormatter(logging.Formatter):
    """Produce records that match the message contract.

    Format: ``[YYYY-MM-DD HH:MM:SS] [Severity] [Source] Message``

    Multiline messages get the header only on the first line; continuation
    lines are indented with four spaces.
    """

    def format(self, record: logging.LogRecord) -> str:
        # --- timestamp --------------------------------------------------
        dt = datetime.datetime.fromtimestamp(
            record.created, tz=datetime.timezone.utc
        ).astimezone(datetime.timezone.utc)  # will be overridden by _localize
        # Use local time instead of UTC.
        dt = datetime.datetime.fromtimestamp(record.created)
        timestamp = dt.strftime(TIMESTAMP_FMT)

        # --- severity ---------------------------------------------------
        severity = self._severity_label(record.levelno)

        # --- source (stored in record.source or falls back to name) ------
        source = getattr(record, "source", None) or record.name

        # --- message ----------------------------------------------------
        message = super().format(record)

        # --- assemble first line ----------------------------------------
        header = f"[{timestamp}] [{severity}] [{source}]"
        first_line = f"{header} {message}"

        # --- handle multiline messages ----------------------------------
        if "\n" in message:
            lines = message.split("\n")
            first_part = lines[0]
            continuations = [
                f"    {line}" for line in lines[1:]
            ]
            first_line = f"{header} {first_part}"
            return "\n".join([first_line] + continuations)

        return first_line

    @staticmethod
    def _severity_label(level: int) -> str:
        mapping = {
            logging.INFO: SEVERITY_INFO,
            logging.WARNING: SEVERITY_WARNING,
            logging.ERROR: SEVERITY_ERROR,
        }
        return mapping.get(level, SEVERITY_INFO)


# ---------------------------------------------------------------------------
# Custom flushing file handler
# ---------------------------------------------------------------------------

class _FlushingFileHandler(logging.FileHandler):
    """File handler that flushes after every record for crash resilience."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def configure(
    log_dir: Optional[str] = None,
    log_file: Optional[str] = None,
) -> None:
    """Initialize the universal logger (idempotent).

    Resolves the project root, creates the output directory if needed, and
    installs both a stdout handler and an append-only file handler.

    Parameters
    ----------
    log_dir :
        Override for the log directory path.  When *None* the default
        ``3_Output_Dat`` under the project root is used.
    log_file :
        Override for the log file name.  When *None* the default
        ``SluggiesTools.log`` is used.

    Falls back to console-only logging when the file destination cannot be
    created or written, emitting a warning to stdout without hiding original
    messages or causing recursive failures.
    """
    global _initialized, _root_logger, _log_file_path

    if _initialized and _root_logger is not None:
        return  # already configured -- idempotent guard

    _root_logger = logging.getLogger("sluggies")
    _root_logger.setLevel(logging.DEBUG)  # accept everything; handlers filter

    # Prevent duplicate handlers on repeated imports.
    if _root_logger.handlers:
        _initialized = True
        return

    formatter = _SluggiesFormatter()

    # --- stdout handler -------------------------------------------------
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.setFormatter(formatter)
    _root_logger.addHandler(stdout_handler)

    # --- file handler ---------------------------------------------------
    if log_dir is None:
        project_root = os.path.dirname(os.path.abspath(__file__))
        # __file__ is inside SluggiesTools/; go one level up.
        project_root = os.path.dirname(project_root)
        log_dir = os.path.join(project_root, DEFAULT_LOG_DIR_NAME)

    if log_file is None:
        log_file = DEFAULT_LOG_FILE_NAME

    log_path = os.path.join(log_dir, log_file)
    _log_file_path = log_path

    try:
        os.makedirs(log_dir, exist_ok=True)
        file_handler = _FlushingFileHandler(
            log_path, mode="a", encoding="utf-8"
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        _root_logger.addHandler(file_handler)
        # Flush promptly so diagnostics survive a crash.
        file_handler.flush()
    except OSError as exc:
        # Fallback: console-only logging with a visible warning.
        print(
            f"[Logging Warning] Cannot open log file {log_path}: {exc}. "
            "Logging to console only.",
            file=sys.stdout,
        )

    _initialized = True


def _get_logger() -> logging.Logger:
    """Return the root sluggies logger, configuring if needed."""
    if not _initialized or _root_logger is None:
        configure()
    return _root_logger  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Convenience entry points
# ---------------------------------------------------------------------------

def info(message: str, source: Optional[str] = None) -> None:
    """Log an *Info* severity message."""
    logger = _get_logger()
    logger.info(message, extra={"source": source})


def warning(message: str, source: Optional[str] = None) -> None:
    """Log a *Warning* severity message."""
    logger = _get_logger()
    logger.warning(message, extra={"source": source})


def error(message: str, source: Optional[str] = None) -> None:
    """Log an *Error* severity message."""
    logger = _get_logger()
    logger.error(message, extra={"source": source})


def exception(
    message: str,
    source: Optional[str] = None,
    exc: Optional[BaseException] = None,
    include_traceback: bool = True,
) -> None:
    """Log an *Error* severity message with exception details.

    Parameters
    ----------
    message :
        Human-readable description of the failure.
    source :
        Originating tool or module name.
    exc :
        The exception to log.  When *None* the current exception from
        ``sys.exc_info()`` is used.
    include_traceback :
        When True (the default), append a full traceback to the record.
    """
    logger = _get_logger()
    if exc is None:
        exc = sys.exc_info()[1]

    detail = message
    if exc is not None:
        detail += f" -- {type(exc).__name__}: {exc}"

    if include_traceback and exc is not None:
        tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
        detail += "\n" + "".join(tb_lines)

    logger.error(detail, extra={"source": source})


# ---------------------------------------------------------------------------
# Helpers for command / input recording
# ---------------------------------------------------------------------------

def log_command(
    args: list[str],
    source: str = "CLI",
) -> None:
    """Record a normalized user command.

    Parameters
    ----------
    args :
        Normalized argument list (e.g. ``sys.argv``).
    source :
        Invocation source label -- ``CLI`` or ``StartTools.bat``.
    """
    cmd_repr = " ".join(f'"{a}"' if " " in a else a for a in args)
    info(f"Command ({source}): {cmd_repr}", source="dispatcher")


def log_user_input(prompt: str, answer: str, source: Optional[str] = None) -> None:
    """Record an interactive user input.

    Parameters
    ----------
    prompt :
        The prompt text shown to the user.
    answer :
        The user's response.
    source :
        Originating tool or module name.
    """
    info(f"Input [{prompt}] -> {answer!r}", source=source)
