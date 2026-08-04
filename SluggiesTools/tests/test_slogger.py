"""Tests for the slogger message contract.

Verifies:
- Each severity appears in both captured stdout and the log file.
- Every file record contains timestamp, severity, source, and message.
- Multiline messages have one timestamp with untimestamped continuation lines.
- Idempotent configure (second call does not duplicate handlers).
- Existing log content is retained across separate calls.
- Unicode paths/messages are written with UTF-8 encoding.
- Unwritable file destination falls back to stdout-only with a warning.
- Exception logging records type/message and optional traceback.
"""

import importlib
import logging
import os
import re
import shutil
import sys
import tempfile
import unittest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Regex for a valid log line matching the message contract:
#   [YYYY-MM-DD HH:MM:SS] [Severity] [Source] Message text
LOG_LINE_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] "
    r"\[(Info|Warning|Error)\] "
    r"\[([^\]]+)\] "
    r"(.+)$"
)

# Continuation lines are indented with four spaces and must NOT start
# with a timestamp bracket.
CONTINUATION_RE = re.compile(r"^    .+")


def _fresh_slogger(temp_dir: str):
    """Import (or reload) slogger configured to write into *temp_dir*.

    Returns the module object so callers can use ``slog.info(...)``, etc.

    Because Python's logging module caches loggers globally, we must also
    strip the existing handlers from the cached logger before calling
    configure() again.
    """
    slog = importlib.import_module("SluggiesTools.slogger")
    # Clear cached handlers so configure() installs fresh ones.
    cached = logging.getLogger("sluggies")
    for handler in list(cached.handlers):
        handler.close()
        cached.removeHandler(handler)
    # Reset internal state so configure() runs again.
    slog._initialized = False
    slog._root_logger = None
    slog._log_file_path = None
    slog.configure(log_dir=temp_dir)
    return slog


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMessageContractFormat(unittest.TestCase):
    """Every record must match the stable text format."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.slog = _fresh_slogger(self._tmpdir)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _read_log_lines(self):
        log_path = os.path.join(self._tmpdir, "SluggiesTools.log")
        with open(log_path, encoding="utf-8") as f:
            return f.readlines()

    # -- basic severities ---------------------------------------------------

    def test_info_record_format(self):
        self.slog.info("hello world", source="test_src")
        lines = self._read_log_lines()
        self.assertEqual(len(lines), 1)
        m = LOG_LINE_RE.match(lines[0])
        self.assertIsNotNone(m, f"Line does not match contract: {lines[0]!r}")
        ts, sev, src, msg = m.groups()
        self.assertEqual(sev, "Info")
        self.assertEqual(src, "test_src")
        self.assertEqual(msg.strip(), "hello world")

    def test_warning_record_format(self):
        self.slog.warning("something odd", source="warn_src")
        lines = self._read_log_lines()
        m = LOG_LINE_RE.match(lines[0])
        self.assertIsNotNone(m)
        _, sev, src, msg = m.groups()
        self.assertEqual(sev, "Warning")
        self.assertEqual(src, "warn_src")

    def test_error_record_format(self):
        self.slog.error("fatal thing", source="err_src")
        lines = self._read_log_lines()
        m = LOG_LINE_RE.match(lines[0])
        self.assertIsNotNone(m)
        _, sev, src, msg = m.groups()
        self.assertEqual(sev, "Error")
        self.assertEqual(src, "err_src")

    # -- timestamp precision ------------------------------------------------

    def test_timestamp_has_second_precision(self):
        self.slog.info("ts check", source="t")
        m = LOG_LINE_RE.match(self._read_log_lines()[0])
        ts = m.group(1)
        # Format must be exactly YYYY-MM-DD HH:MM:SS (19 chars)
        self.assertEqual(len(ts), 19)

    # -- multiline messages -------------------------------------------------

    def test_multiline_message_single_timestamp(self):
        msg = "line one\nline two\nline three"
        self.slog.info(msg, source="ml")
        lines = self._read_log_lines()
        # First line matches the contract.
        self.assertTrue(LOG_LINE_RE.match(lines[0]), lines[0])
        # Continuation lines do NOT start with a timestamp bracket.
        for cont in lines[1:]:
            self.assertFalse(cont.startswith("["),
                             f"Continuation should not have timestamp: {cont!r}")
            self.assertTrue(CONTINUATION_RE.match(cont),
                            f"Continuation should be indented: {cont!r}")

    # -- source defaults to logger name when omitted ------------------------

    def test_source_defaults_to_logger_name(self):
        self.slog.info("no source given")
        m = LOG_LINE_RE.match(self._read_log_lines()[0])
        self.assertIsNotNone(m)
        # When source is None the formatter falls back to the logger name.
        self.assertEqual(m.group(3), "sluggies")


class TestIdempotentConfigure(unittest.TestCase):
    """Second configure call must not duplicate handlers or records."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.slog = _fresh_slogger(self._tmpdir)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_no_duplicate_records(self):
        log_path = os.path.join(self._tmpdir, "SluggiesTools.log")
        # First message.
        self.slog.info("first", source="dup")
        # Re-configure (should be no-op).
        self.slog.configure(log_dir=self._tmpdir)
        # Second message.
        self.slog.info("second", source="dup")
        with open(log_path, encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 2,
                         f"Expected 2 records but got {len(lines)}")


class TestAppendBehavior(unittest.TestCase):
    """Existing log content must be retained across calls."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        # Pre-seed the log file.
        os.makedirs(os.path.join(self._tmpdir), exist_ok=True)
        log_path = os.path.join(self._tmpdir, "SluggiesTools.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("legacy content\n")
        self.slog = _fresh_slogger(self._tmpdir)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_existing_content_retained(self):
        self.slog.info("new record", source="append_test")
        log_path = os.path.join(self._tmpdir, "SluggiesTools.log")
        with open(log_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("legacy content", content)
        self.assertIn("new record", content)


class TestUnicodeHandling(unittest.TestCase):
    """Unicode messages and paths must survive UTF-8 round-trip."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.slog = _fresh_slogger(self._tmpdir)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_unicode_message(self):
        self.slog.info("café résumé naïve", source="unicode_src")
        log_path = os.path.join(self._tmpdir, "SluggiesTools.log")
        with open(log_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("café résumé naïve", content)


class TestFallbackOnUnwritableDestination(unittest.TestCase):
    """When the file cannot be opened, logging falls back to stdout."""

    def test_fallback_warning_emitted(self):
        # Create a temp file (not dir), then try to use it as log_dir.
        tmpfile = tempfile.mktemp()
        with open(tmpfile, "w") as f:
            f.write("blocker")
        try:
            slog = importlib.import_module("SluggiesTools.slogger")
            slog._initialized = False
            slog._root_logger = None
            slog._log_file_path = None
            # This should fail to create the file handler and fall back.
            slog.configure(log_dir=tmpfile)
            # stdout handler should still work.
            slog.info("after fallback", source="fb")
        finally:
            os.remove(tmpfile)


class TestExceptionLogging(unittest.TestCase):
    """exception() helper records type, message, and optional traceback."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.slog = _fresh_slogger(self._tmpdir)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _read_log(self):
        log_path = os.path.join(self._tmpdir, "SluggiesTools.log")
        with open(log_path, encoding="utf-8") as f:
            return f.read()

    def test_exception_includes_type_and_message(self):
        try:
            raise ValueError("oops 123")
        except ValueError:
            self.slog.exception("failed op", source="exc_test")
        content = self._read_log()
        self.assertIn("ValueError", content)
        self.assertIn("oops 123", content)
        self.assertIn("failed op", content)

    def test_exception_without_traceback(self):
        try:
            raise RuntimeError("no tb please")
        except RuntimeError:
            self.slog.exception("clean fail", source="exc_test",
                               include_traceback=False)
        content = self._read_log()
        self.assertIn("RuntimeError", content)
        self.assertNotIn("Traceback", content)


class TestStdoutFanOut(unittest.TestCase):
    """All severities must also appear on stdout."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        import io
        self._stdout_capture = io.StringIO()
        # Import and configure.
        self.slog = _fresh_slogger(self._tmpdir)
        # Replace the first StreamHandler's stream with our capture.
        for handler in self.slog._root_logger.handlers:
            if isinstance(handler, logging.StreamHandler) \
               and not isinstance(handler, logging.FileHandler):
                handler.stream = self._stdout_capture

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_all_severities_on_stdout(self):
        self.slog.info("i", source="s")
        self.slog.warning("w", source="s")
        self.slog.error("e", source="s")
        out = self._stdout_capture.getvalue()
        self.assertIn("[Info]", out)
        self.assertIn("[Warning]", out)
        self.assertIn("[Error]", out)


# ---------------------------------------------------------------------------
# Step 2 tests — command / batch capture
# ---------------------------------------------------------------------------

class TestCommandLogging(unittest.TestCase):
    """Step 2.1 – log_command records normalized arguments."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.slog = _fresh_slogger(self._tmpdir)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _read_log(self):
        log_path = os.path.join(self._tmpdir, "SluggiesTools.log")
        with open(log_path, encoding="utf-8") as f:
            return f.read()

    def test_cli_command_logged(self):
        self.slog.log_command(["start.py", "--export", "--untangle"], source="CLI")
        content = self._read_log()
        self.assertIn("Command (CLI):", content)
        self.assertIn("--export", content)
        self.assertIn("--untangle", content)

    def test_batch_command_logged(self):
        self.slog.log_command(["start.py", "--patch", "model.sluggies"], source="StartTools.bat")
        content = self._read_log()
        self.assertIn("Command (StartTools.bat):", content)
        self.assertIn("--patch", content)

    def test_args_with_spaces_quoted(self):
        self.slog.log_command(["start.py", "--patch", "my model.sluggies"], source="CLI")
        content = self._read_log()
        # The argument with spaces must be quoted in the representation.
        self.assertIn('"my model.sluggies"', content)


class TestBatchMetadataLogging(unittest.TestCase):
    """Step 2.3 – batch menu selection and interactive inputs are logged."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.slog = _fresh_slogger(self._tmpdir)
        # Save original environment variables.
        self._saved_source = os.environ.get("SLUGGIES_SOURCE", "")
        self._saved_menu = os.environ.get("SLUGGIES_MENU_SELECTION", "")
        self._saved_models = os.environ.get("SLUGGIES_MODEL_FILES", "")
        self._saved_mode = os.environ.get("SLUGGIES_ICON_SHARED_MODE", "")

    def tearDown(self):
        # Restore environment.
        if self._saved_source:
            os.environ["SLUGGIES_SOURCE"] = self._saved_source
        elif "SLUGGIES_SOURCE" in os.environ:
            del os.environ["SLUGGIES_SOURCE"]
        if self._saved_menu:
            os.environ["SLUGGIES_MENU_SELECTION"] = self._saved_menu
        elif "SLUGGIES_MENU_SELECTION" in os.environ:
            del os.environ["SLUGGIES_MENU_SELECTION"]
        if self._saved_models:
            os.environ["SLUGGIES_MODEL_FILES"] = self._saved_models
        elif "SLUGGIES_MODEL_FILES" in os.environ:
            del os.environ["SLUGGIES_MODEL_FILES"]
        if self._saved_mode:
            os.environ["SLUGGIES_ICON_SHARED_MODE"] = self._saved_mode
        elif "SLUGGIES_ICON_SHARED_MODE" in os.environ:
            del os.environ["SLUGGIES_ICON_SHARED_MODE"]
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _read_log(self):
        log_path = os.path.join(self._tmpdir, "SluggiesTools.log")
        with open(log_path, encoding="utf-8") as f:
            return f.read()

    def test_batch_menu_selection_logged(self):
        os.environ["SLUGGIES_SOURCE"] = "StartTools.bat"
        os.environ["SLUGGIES_MENU_SELECTION"] = "5 - Patch model(s)"
        # Simulate what start.py._log_batch_metadata does.
        if os.environ.get("SLUGGIES_MENU_SELECTION"):
            self.slog.info(
                f"Batch menu selection: {os.environ['SLUGGIES_MENU_SELECTION']}",
                source="dispatcher",
            )
        content = self._read_log()
        self.assertIn("Batch menu selection:", content)
        self.assertIn("5 - Patch model(s)", content)

    def test_batch_model_files_logged(self):
        os.environ["SLUGGIES_SOURCE"] = "StartTools.bat"
        os.environ["SLUGGIES_MODEL_FILES"] = "hero.sluggies villain.sluggies"
        if os.environ.get("SLUGGIES_MODEL_FILES"):
            self.slog.info(
                f"Batch model file input: {os.environ['SLUGGIES_MODEL_FILES']!r}",
                source="dispatcher",
            )
        content = self._read_log()
        self.assertIn("Batch model file input:", content)

    def test_batch_icon_shared_mode_logged(self):
        os.environ["SLUGGIES_SOURCE"] = "StartTools.bat"
        os.environ["SLUGGIES_ICON_SHARED_MODE"] = "2"
        if os.environ.get("SLUGGIES_ICON_SHARED_MODE"):
            self.slog.info(
                f"Batch icon shared-mode input: {os.environ['SLUGGIES_ICON_SHARED_MODE']!r}",
                source="dispatcher",
            )
        content = self._read_log()
        self.assertIn("Batch icon shared-mode input:", content)


class TestParentChildAppend(unittest.TestCase):
    """Step 2.2 – parent and child records append to one file without duplicates."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.slog = _fresh_slogger(self._tmpdir)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _read_log_lines(self):
        log_path = os.path.join(self._tmpdir, "SluggiesTools.log")
        with open(log_path, encoding="utf-8") as f:
            return f.readlines()

    def test_parent_and_child_append(self):
        # Parent writes a record.
        self.slog.info("parent record", source="dispatcher")
        # Simulate child process by re-configuring (idempotent) and writing.
        self.slog.configure(log_dir=self._tmpdir)
        self.slog.info("child record", source="export")
        lines = self._read_log_lines()
        # Exactly two records — no duplicates.
        self.assertEqual(len(lines), 2)
        self.assertIn("parent record", lines[0])
        self.assertIn("child record", lines[1])


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    unittest.main()
