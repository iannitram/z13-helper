"""Edge case tests for crash report queue and sizing (issue #152)."""

import json
import logging
from unittest import mock
import zipfile

import crash_report as cr

BUILD = {
    "base": "https://drop.invalid/reports",
    "token": "test-only-token",
    "version": "0.5.1",
    "commit": "abc1234",
}


def test_no_user_name_available_queues_redacted_report(tmp_path, monkeypatch):
    """When system user lookup fails and USER/LOGNAME are unset, queue report with placeholder redaction."""
    import pwd

    for var in ("USER", "LOGNAME", "LNAME", "USERNAME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(pwd, "getpwuid", mock.Mock(side_effect=KeyError("no passwd entry")))

    build_path = tmp_path / "build.json"
    build_path.write_text(json.dumps(BUILD))
    monkeypatch.setattr(cr, "BUILD_CONFIG", build_path)
    monkeypatch.setattr(cr.faulthandler, "enable", lambda **kw: None)
    monkeypatch.setattr(cr.faulthandler, "disable", lambda: None)
    # Mock upload so report stays in local queue
    monkeypatch.setattr(cr, "upload_report", mock.Mock(side_effect=OSError("drop offline")))

    logger = logging.getLogger("test.crash.edges.nouser")
    queue_dir = tmp_path / "queue"
    reporter = cr.install(
        config_path=str(tmp_path / "settings.json"),
        state_dir=str(queue_dir),
        logger=logger,
        log_text=lambda: "worker run for unknown user completed\n",
    )
    try:
        reporter.capture(ValueError("crash for unknown user"))
        reporter.events.join()
        reporter.worker.join(timeout=3)
    finally:
        reporter.close()
        reporter.worker.join(timeout=3)

    queued = list(queue_dir.glob("*.zip"))
    assert len(queued) == 1
    with zipfile.ZipFile(queued[0]) as z:
        rep = json.loads(z.read("report.json"))
        log = z.read("app.log").decode()

    assert "crash for unknown user" in rep["message"]
    assert "worker run for unknown user completed" in log


def test_log_tail_caps_oversized_single_line_with_cut_marker():
    """A log line longer than MAX_LOG_LINE_CHARS is cut at the cap with cut marker appended."""
    oversized = "x" * 3000
    tail = cr.log_tail(oversized + "\n")
    expected = ("x" * cr.MAX_LOG_LINE_CHARS) + " ... [cut]\n"
    assert tail == expected


def test_read_fatal_caps_oversized_file_with_cut_marker(tmp_path):
    """A fatal log file larger than MAX_FATAL_BYTES is cut at the byte cap with cut marker appended."""
    fatal_file = tmp_path / "fatal.log"
    # Write 300 KiB (> 256 KiB MAX_FATAL_BYTES)
    data = b"F" * (300 * 1024)
    fatal_file.write_bytes(data)

    out = cr.read_fatal(fatal_file)
    assert out.startswith("F" * cr.MAX_FATAL_BYTES)
    assert out.endswith("\n... [cut] ...\n")
    assert len(out) == cr.MAX_FATAL_BYTES + len("\n... [cut] ...\n")


def test_read_fatal_caps_oversized_string_with_cut_marker():
    """An oversized raw fatal text string is cut at the cap with cut marker appended."""
    raw_text = "E" * (300 * 1024)
    out = cr.read_fatal(raw_text)
    assert out.startswith("E" * cr.MAX_FATAL_BYTES)
    assert out.endswith("\n... [cut] ...\n")
    assert len(out) == cr.MAX_FATAL_BYTES + len("\n... [cut] ...\n")


def test_oversized_fatal_log_and_single_line_produces_capped_report_with_cut_marked(tmp_path, monkeypatch):
    """Oversized fatal crash log and oversized log line produce a report under cap with cuts marked."""
    build_path = tmp_path / "build.json"
    build_path.write_text(json.dumps(BUILD))
    monkeypatch.setattr(cr, "BUILD_CONFIG", build_path)
    monkeypatch.setattr(cr.faulthandler, "enable", lambda **kw: None)
    monkeypatch.setattr(cr.faulthandler, "disable", lambda: None)
    monkeypatch.setattr(cr, "upload_report", mock.Mock(side_effect=OSError("drop offline")))

    fatal_file = tmp_path / "fatal-desktop.log"
    fatal_file.write_bytes(b"Fatal crash line\n" + (b"Z" * (300 * 1024)))

    long_line = "INFO [main] test: " + ("A" * 3000)
    logger = logging.getLogger("test.crash.edges.oversized")
    queue_dir = tmp_path / "queue"

    reporter = cr.install(
        config_path=str(tmp_path / "settings.json"),
        state_dir=str(queue_dir),
        logger=logger,
        log_text=lambda: f"first line\n{long_line}\nlast line\n",
        previous_fatal=str(fatal_file),
    )
    try:
        reporter.events.join()
        reporter.worker.join(timeout=3)
    finally:
        reporter.close()
        reporter.worker.join(timeout=3)

    queued = list(queue_dir.glob("*.zip"))
    assert len(queued) == 1
    with zipfile.ZipFile(queued[0]) as z:
        rep = json.loads(z.read("report.json"))
        log = z.read("app.log").decode()

    # Traceback was capped at MAX_FATAL_BYTES with marker
    assert "... [cut] ..." in rep["traceback"]
    assert len(rep["traceback"]) <= cr.MAX_FATAL_BYTES + len("\n... [cut] ...\n")

    # Long line in app.log was capped at MAX_LOG_LINE_CHARS with marker
    log_lines = log.splitlines()
    cut_lines = [line for line in log_lines if "... [cut]" in line]
    assert len(cut_lines) == 1
    assert cut_lines[0].startswith(long_line[:cr.MAX_LOG_LINE_CHARS])
    assert cut_lines[0].endswith(" ... [cut]")
    assert len(cut_lines[0]) == cr.MAX_LOG_LINE_CHARS + len(" ... [cut]")
