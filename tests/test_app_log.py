"""src/app_log.py: log file, in-memory ring, crash reports, hard-crash recovery."""

import faulthandler
import logging
import os
import sys
import threading
import time

import pytest

import app_log


@pytest.fixture
def logenv(tmp_path, monkeypatch):
    """app_log pointed at tmp_path; root logger, hooks and faulthandler restored after."""
    monkeypatch.setattr(app_log, "LOG_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(app_log, "_state", {"app_version": "?", "ring": None, "fatal_file": None,
                                            "crash_listeners": [], "previous_fatal": None})
    root = logging.getLogger()
    saved = (list(root.handlers), root.level, sys.excepthook, threading.excepthook)
    yield tmp_path / "state"
    app_log.shutdown()
    for h in list(root.handlers):
        if h not in saved[0]:
            root.removeHandler(h)
            h.close()
    root.setLevel(saved[1])
    sys.excepthook, threading.excepthook = saved[2], saved[3]
    if faulthandler.is_enabled():
        faulthandler.disable()


def _raise_and_report(where="main thread"):
    try:
        raise ValueError("boom in a slot")
    except ValueError:
        return app_log.report_exception(*sys.exc_info(), where=where)


def test_setup_writes_log_file_and_ring(logenv):
    rh = app_log.setup("9.9.9", stderr=False)
    logging.getLogger("z13.test").warning("something odd")
    log = (logenv / "app.log").read_text()
    assert "Z13-Helper 9.9.9 starting" in log
    assert "WARNING [MainThread] z13.test: something odd" in log
    assert rh.counts["WARNING"] == 1
    assert any("something odd" in t for t in rh.lines(logging.WARNING))
    assert not any("starting" in t for t in rh.lines(logging.WARNING))   # INFO filtered


def test_ring_listener_errors_do_not_break_logging(logenv):
    rh = app_log.setup(stderr=False)
    seen = []
    rh.add_listener(lambda item: 1 / 0)
    rh.add_listener(seen.append)
    logging.getLogger("x").error("e1")
    assert seen and seen[-1][0] == logging.ERROR and "e1" in seen[-1][2]


def test_ring_is_bounded(logenv):
    rh = app_log.RingHandler(size=5)
    lg = logging.getLogger("bounded")
    lg.addHandler(rh)
    lg.setLevel(logging.INFO)
    try:
        for i in range(12):
            lg.info("n%d", i)
    finally:
        lg.removeHandler(rh)
    assert len(rh.records) == 5 and rh.records[-1][2].endswith("n11")


def test_uncaught_exception_writes_report_with_context(logenv):
    app_log.setup("1.2.3", stderr=False)
    logging.getLogger("z13.profiles").warning("Profile → Turbo: brightness (lightbar) (timed out)")
    got = []
    app_log.on_crash(lambda path, summary: got.append((path, summary)))
    path = _raise_and_report()
    text = open(path).read()
    assert "Where:  main thread" in text and "Z13-Helper 1.2.3" in text
    assert "ValueError: boom in a slot" in text
    assert "Profile → Turbo" in text                       # recent log lines included
    assert got == [(path, "ValueError: boom in a slot")]
    assert app_log.list_crash_reports()[0][2] == "ValueError: boom in a slot"
    # ...and the exception itself went to the log as CRITICAL
    assert "CRITICAL" in (logenv / "app.log").read_text()


def test_excepthook_is_installed_and_keyboardinterrupt_passes_through(logenv, monkeypatch):
    app_log.setup(stderr=False)
    assert sys.excepthook is app_log._excepthook
    passed = []
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: passed.append(a[0]))
    sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    assert passed == [KeyboardInterrupt] and app_log.list_crash_reports() == []
    try:
        raise RuntimeError("from excepthook")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    assert app_log.list_crash_reports()[0][2] == "RuntimeError: from excepthook"


def test_worker_thread_exception_is_reported(logenv):
    app_log.setup(stderr=False)

    def worker():
        raise OSError("worker died")
    t = threading.Thread(target=worker, name="ApplyWorker")
    t.start()
    t.join()
    reports = app_log.list_crash_reports()
    assert len(reports) == 1 and reports[0][2] == "OSError: worker died"
    assert "Where:  thread ApplyWorker" in open(reports[0][0]).read()


def test_reports_are_pruned_to_the_newest(logenv, monkeypatch):
    app_log.setup(stderr=False)
    monkeypatch.setattr(app_log, "MAX_CRASH_REPORTS", 3)
    os.makedirs(app_log.crash_dir(), exist_ok=True)
    for i in range(5):
        p = os.path.join(app_log.crash_dir(), f"crash-2026010{i}-000000-1-1.txt")
        open(p, "w").write("--- traceback ---\nValueError: old\n")
        os.utime(p, (1_000_000 + i, 1_000_000 + i))
    _raise_and_report()
    names = sorted(os.path.basename(p) for p, _m, _s in app_log.list_crash_reports())
    assert len(names) == 3
    assert "crash-20260100-000000-1-1.txt" not in names and "crash-20260101-000000-1-1.txt" not in names


def test_hard_crash_from_previous_run_is_recovered(logenv):
    os.makedirs(logenv, exist_ok=True)
    (logenv / "fatal.log").write_text("Fatal Python error: Segmentation fault\n\nThread 0x1 (most recent call first):\n")
    app_log.setup(stderr=False)
    prev = app_log.previous_fatal()
    assert prev and os.path.basename(prev).startswith("fatal-")
    assert "Segmentation fault" in open(prev).read()
    assert app_log.list_crash_reports()[0][2] == "Fatal Python error: Segmentation fault"
    assert "previous run crashed hard" in (logenv / "app.log").read_text()
    # this run's fatal.log is fresh and empty
    assert (logenv / "fatal.log").read_text() == ""


def test_clean_shutdown_removes_empty_fatal_log(logenv):
    app_log.setup(stderr=False)
    assert (logenv / "fatal.log").exists() and faulthandler.is_enabled()
    app_log.shutdown()
    assert not (logenv / "fatal.log").exists() and not faulthandler.is_enabled()
    assert app_log.previous_fatal() is None


def test_unwritable_dir_falls_back_to_memory(logenv, monkeypatch):
    monkeypatch.setattr(app_log, "LOG_DIR", "/proc/no-such-dir/z13")
    rh = app_log.setup(stderr=False)
    logging.getLogger("x").error("still recorded")
    assert any("still recorded" in t for t in rh.lines())
    assert _raise_and_report() is None          # no report on disk, but no exception either


def test_clear_and_read_text(logenv):
    app_log.setup(stderr=False)
    p = _raise_and_report()
    assert "boom in a slot" in app_log.read_text(p)
    assert app_log.clear_crash_reports() == 1 and app_log.list_crash_reports() == []
    assert "can't read" in app_log.read_text(p)


def test_read_text_tails_large_files(tmp_path):
    p = tmp_path / "big.log"
    p.write_text("".join(f"line {i}\n" for i in range(50_000)))
    t = app_log.read_text(str(p), max_bytes=1000)
    assert t.startswith("…(earlier lines cut)…") and t.rstrip().endswith("line 49999")


def test_crash_report_never_runs_subprocesses(logenv):
    # conftest's autouse guard makes subprocess.run/Popen raise; a report must still be written
    app_log.setup(stderr=False)
    assert _raise_and_report() is not None


def test_install_check_flags_recent_crash_reports(logenv):
    import install_check as ic
    app_log.setup(stderr=False)
    r = ic.Report()
    ic.check_crash_reports(r)
    assert r.rows[-1][0] == ic.GREEN
    _raise_and_report()
    r = ic.Report()
    ic.check_crash_reports(r)
    assert r.rows[-1][0] == ic.YELLOW and "boom in a slot" in r.rows[-1][2]
    r = ic.Report()
    ic.check_crash_reports(r, now=time.time() + 8 * 24 * 3600)
    assert r.rows[-1][0] == ic.GREEN and "1 older" in r.rows[-1][2]
