"""Round-3 tests for crash-report redaction of log-tail lines (issue #152, PR #176 review).

Decky calls install() without log_text, so the report uses the reporter's own _LogTail.
A line must be redacted before the byte cap cuts it, or an identifier straddling the
cut leaks as a partial prefix. build_zip() must also mark a cut traceback.

No hardware, no real HOME: everything lives under tmp_path, identity lookups are patched.
"""

import getpass
import io
import json
import logging
import pwd
import socket
import zipfile
from unittest import mock

import pytest

import crash_report as cr

BUILD = {
    "base": "https://drop.invalid/reports",
    "token": "test-only-token-r3",
    "version": "0.5.1",
    "commit": "abc1234",
}
CUT = "\n... [cut] ...\n"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("USER", "LOGNAME", "LNAME", "USERNAME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(pwd, "getpwuid", mock.Mock(side_effect=KeyError("no passwd entry")))
    monkeypatch.setattr(socket, "gethostname", lambda: "testhost-r3")
    monkeypatch.setattr(cr, "upload_report", mock.Mock(side_effect=AssertionError("no upload in r3 tests")))
    return home


def _reporter(tmp_path, name):
    """Reporter built the way install() builds it without log_text: it owns a _LogTail."""
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    logger = logging.getLogger(f"test.crash.r3.{name}")
    logger.setLevel(logging.INFO)
    return cr.CrashReporter(dict(BUILD), str(tmp_path / "settings.json"), state, logger)


def _zip_parts(data: bytes):
    z = zipfile.ZipFile(io.BytesIO(data))
    return json.loads(z.read("report.json")), z.read("app.log").decode("utf-8"), z


def test_log_tail_line_straddling_byte_cap_is_redacted_whole(isolated, tmp_path, monkeypatch):
    """A username crossing the 2048-byte line cap must not survive as the partial prefix "qwx"."""
    monkeypatch.setattr(getpass, "getuser", lambda: "qwxzebra")
    reporter = _reporter(tmp_path, "straddle")
    # "qwxzebra" starts at byte 2045, so the raw cut at 2048 would keep "qwx".
    reporter.log.info("a" * 2044 + " qwxzebra trailing words")
    try:
        _name, data = reporter._report(ValueError("boom"), None, None)
    finally:
        reporter.log.removeHandler(reporter.tail)
    report, log, _ = _zip_parts(data)
    text = json.dumps(report) + log
    assert "qwxzebra" not in text
    assert "qwx" not in text


def test_log_tail_straddling_build_token_is_redacted_whole(isolated, tmp_path):
    """The build token crossing the line cap must not leak as a partial prefix either."""
    reporter = _reporter(tmp_path, "token")
    reporter.log.info("b" * 2040 + " test-only-token-r3 end")
    try:
        _name, data = reporter._report(ValueError("boom"), None, None)
    finally:
        reporter.log.removeHandler(reporter.tail)
    report, log, _ = _zip_parts(data)
    text = json.dumps(report) + log
    assert "test-only-token-r3" not in text
    assert "test-only" not in text
    assert "test-" not in log


def test_huge_log_line_is_held_raw_at_8_kib_and_redacted_in_the_report(isolated, tmp_path, monkeypatch):
    """A 1 MB line is kept raw at no more than 8 KiB; the report line is redacted and within 2048 bytes."""
    monkeypatch.setattr(getpass, "getuser", lambda: "qwxzebra")
    reporter = _reporter(tmp_path, "huge")
    reporter.log.info("qwxzebra " + "y" * (1024 * 1024))
    try:
        assert len(reporter.tail.lines[-1].encode("utf-8")) <= 8 * 1024
        _name, data = reporter._report(ValueError("boom"), None, None)
    finally:
        reporter.log.removeHandler(reporter.tail)
    _report, log, _ = _zip_parts(data)
    (line,) = [ln for ln in log.splitlines() if "yyy" in ln]
    assert line.endswith(" ... [cut]")
    assert "qwx" not in log
    assert len(line.encode("utf-8")) <= cr.MAX_LOG_LINE_CHARS + len(" ... [cut]")


def test_build_zip_marks_a_cut_traceback_and_stays_under_cap():
    """A traceback cut to fit REPORT_CAP_BYTES carries the cut marker, like the other cut paths."""
    report = {"traceback": "t" * (900 * 1024), "message": "m"}
    rep, _log, z = _zip_parts(cr.build_zip(report, "short\n"))
    assert rep["traceback"].endswith(CUT)
    total = sum(z.getinfo(name).file_size for name in ("report.json", "app.log"))
    assert total <= cr.REPORT_CAP_BYTES
