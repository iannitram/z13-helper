"""Crash wiring runs only against mocked transports and scratch state."""

import asyncio
import io
import json
import logging
import re
import sys
import threading
import types
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

import pytest
from build_crash_config import bake

import crash_report as cr

BUILD = {"base": "https://drop.invalid/reports", "token": "test-only-token",
         "version": "0.5.1", "commit": "abc1234"}


@pytest.fixture
def wiring(tmp_path, monkeypatch):
    build_path = tmp_path / "build.json"
    build_path.write_text(json.dumps(BUILD))
    monkeypatch.setattr(cr, "BUILD_CONFIG", build_path)
    passed = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: passed.append(args))
    monkeypatch.setattr(threading, "excepthook", lambda args: passed.append(args))
    reporters, sent = [], []
    monkeypatch.setattr(cr, "upload_report", lambda *args: sent.append(args))
    monkeypatch.setattr(cr.faulthandler, "enable", lambda **kw: None)
    monkeypatch.setattr(cr.faulthandler, "disable", lambda: None)
    logger = logging.getLogger("test.crash.wiring")
    logger.setLevel(logging.INFO)

    def start(**kw):
        reporter = cr.install(config_path=str(tmp_path / "settings.json"),
                              state_dir=str(tmp_path / "queue"), logger=logger, **kw)
        if reporter:
            reporters.append(reporter)
        return reporter

    yield types.SimpleNamespace(start=start, sent=sent, passed=passed, root=tmp_path, log=logger)
    for reporter in reversed(reporters):
        reporter.close()
        reporter.worker.join(timeout=3)
        assert not reporter.worker.is_alive()


def unpack(sent):
    with zipfile.ZipFile(io.BytesIO(sent[2])) as archive:
        return json.loads(archive.read("report.json")), archive.read("app.log").decode()


def test_main_qt_and_thread_hooks_keep_local_handler_and_deduplicate(wiring):
    reporter = wiring.start()
    try:
        raise ValueError("bad control")
    except ValueError as exc:
        # Qt slot errors use this same main-thread hook.
        sys.excepthook(type(exc), exc, exc.__traceback__)
        sys.excepthook(type(exc), exc, exc.__traceback__)
        threading.excepthook(types.SimpleNamespace(
            exc_type=type(exc), exc_value=exc, exc_traceback=exc.__traceback__, thread=None))
    reporter.events.join()
    assert len(wiring.sent) == 1 and len(wiring.passed) == 3
    assert re.fullmatch(r"z13-helper-linux-\d{8}T\d{6}Z-[0-9a-f]{8}\.zip", wiring.sent[0][1])
    report, _ = unpack(wiring.sent[0])
    assert report["app"] == "z13-helper" and report["version"] == "0.5.1"
    assert report["commit"] == "abc1234" and report["exception_type"] == "ValueError"
    assert "Traceback" in report["traceback"]
    assert re.fullmatch("[0-9a-f]{32}", report["install_id"])
    assert report["os"] and report["kernel"] and report["python"]


def test_uploaded_zip_scrubs_fields_traceback_and_last_500_log_lines(wiring, monkeypatch):
    import getpass
    import socket

    home = str(wiring.root / "private-home")
    monkeypatch.setattr(Path, "home", lambda: Path(home))
    monkeypatch.setattr(socket, "gethostname", lambda: "private-host")
    monkeypatch.setattr(getpass, "getuser", lambda: "private-user")
    planted = (f"{home}/file private-host private-user password=planted-secret "
               "aa:bb:cc:dd:ee:ff serial=planted-serial test-only-token")
    reporter = wiring.start(log_text=lambda: "old\n" * 600 + planted)
    reporter.capture(RuntimeError(planted))
    reporter.events.join()
    report, log = unpack(wiring.sent[0])
    text = json.dumps(report) + log
    for secret in (home, "private-host", "private-user", "planted-secret", "planted-serial",
                   "aa:bb:cc:dd:ee:ff", "test-only-token"):
        assert secret not in text
    assert "~/file" in log and len(log.splitlines()) == 500


def test_opt_out_writes_and_uploads_nothing_then_toggle_preserves_settings(wiring):
    settings = wiring.root / "settings.json"
    settings.write_text('{"logging": false}')
    cr.set_reporting(str(settings), False)
    reporter = wiring.start()
    reporter.capture(ValueError("off"))
    reporter.events.join()
    assert wiring.sent == [] and not (wiring.root / "queue").exists()
    assert json.loads(settings.read_text()) == {"logging": False, cr.SETTING: False}
    cr.set_reporting(str(settings), True)
    reporter.capture(ValueError("on"))
    reporter.events.join()
    assert len(wiring.sent) == 1


def test_missing_build_is_one_log_line_and_ignores_runtime_credentials(wiring, monkeypatch, caplog):
    cr.BUILD_CONFIG.unlink()
    monkeypatch.setenv("CRAFT_BUG_DROP_BASE", BUILD["base"])
    monkeypatch.setenv("CRAFT_BUG_DROP_TOKEN", BUILD["token"])
    with caplog.at_level(logging.INFO):
        assert wiring.start() is None
    assert len(caplog.records) == 1 and "no build-time" in caplog.text
    assert wiring.sent == [] and not (wiring.root / "queue").exists()


def test_offline_queue_retries_on_restart_with_same_name_and_install_id(wiring, monkeypatch):
    def offline(*args):
        raise OSError("offline")

    monkeypatch.setattr(cr, "upload_report", offline)
    first = wiring.start()
    first.capture(RuntimeError("offline crash"))
    first.events.join()
    queued = list((wiring.root / "queue").glob("*.zip"))
    assert len(queued) == 1
    original = queued[0].read_bytes()
    first.close()
    first.worker.join(3)
    monkeypatch.setattr(cr, "upload_report", lambda *args: wiring.sent.append(args))
    second = wiring.start()
    second.capture(ValueError("next crash"))
    second.events.join()
    assert wiring.sent[0][1:] == (queued[0].name, original)
    assert unpack(wiring.sent[0])[0]["install_id"] == unpack(wiring.sent[1])[0]["install_id"]
    assert not list((wiring.root / "queue").glob("*.zip"))


def test_daily_limit_shared_by_frontends_and_pending_queue_bounded(wiring):
    first, second = wiring.start(), wiring.start()
    for i in range(20):
        reporter = first if i % 2 else second
        reporter.capture(ValueError(str(i)))
        reporter.events.join()
    assert len(wiring.sent) == 5
    assert len(list((wiring.root / "queue").glob("*.zip"))) == 10


def test_upload_never_blocks_the_hook_and_opt_out_stops_pending_send(wiring, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def blocked(*args):
        entered.set()
        assert release.wait(3)
        wiring.sent.append(args)

    monkeypatch.setattr(cr, "upload_report", blocked)
    reporter = wiring.start()
    try:
        sys.excepthook(ValueError, ValueError("first"), None)
        assert entered.wait(3)
        sys.excepthook(ValueError, ValueError("second"), None)
        cr.set_reporting(str(wiring.root / "settings.json"), False)
        assert len(wiring.passed) == 2
    finally:
        release.set()
    reporter.events.join()
    assert len(wiring.sent) == 1


def test_async_loop_and_unload_restore_existing_hooks(wiring):
    loop = asyncio.new_event_loop()
    previous = []
    loop.set_exception_handler(lambda *args: previous.append(args))
    saved = (sys.excepthook, threading.excepthook, loop.get_exception_handler())
    try:
        reporter = wiring.start(loop=loop)
        loop.call_exception_handler({"exception": RuntimeError("async worker")})
        reporter.events.join()
        assert len(wiring.sent) == 1 and len(previous) == 1
        reporter.close()
        assert (sys.excepthook, threading.excepthook, loop.get_exception_handler()) == saved
    finally:
        loop.close()


def test_recovered_fatal_is_scrubbed_and_uploaded(wiring):
    fatal = wiring.root / "fatal.txt"
    fatal.write_text("Fatal Python error: Segmentation fault\npassword=planted-secret\n")
    reporter = wiring.start(previous_fatal=str(fatal))
    reporter.events.join()
    assert len(wiring.sent) == 1
    report, _ = unpack(wiring.sent[0])
    assert "Segmentation fault" in report["traceback"]
    assert "planted-secret" not in report["traceback"]


def test_build_config_generation_is_explicit_and_not_tracked(tmp_path):
    output = tmp_path / "_crash_build.json"
    bake(output, "abc1234", {"CRAFT_BUG_DROP_BASE": BUILD["base"],
                            "CRAFT_BUG_DROP_TOKEN": BUILD["token"]})
    assert json.loads(output.read_text()) == BUILD
    with pytest.raises(ValueError):
        bake(output, "abc1234", {"CRAFT_BUG_DROP_BASE": "http://drop.invalid"})
    repo = Path(__file__).resolve().parents[1]
    assert "src/_crash_build.json" in (repo / ".gitignore").read_text()
    assert "--share=network" in (repo / "flatpak/io.github.iannitram.Z13Helper.yml").read_text()


def test_transport_matches_shared_drop_protocol_and_timeout(monkeypatch):
    response = mock.MagicMock()
    response.__enter__.return_value.status = 201
    opener = mock.Mock()
    opener.open.return_value = response
    create = mock.Mock(return_value=opener)
    monkeypatch.setattr(urllib.request, "build_opener", create)
    name = "z13-helper-linux-20261102T000000Z-1234abcd.zip"
    cr.upload_report({**BUILD, "token": "test token/+"}, name, b"zip bytes")
    request = opener.open.call_args.args[0]
    assert request.full_url == (BUILD["base"] + "/public.php/dav/files/test%20token%2F%2B/" + name)
    assert request.get_method() == "PUT" and request.data == b"zip bytes"
    assert request.get_header("Content-type") == "application/zip"
    assert request.get_header("If-none-match") == "*"
    assert opener.open.call_args.kwargs == {"timeout": 10}
    handler = create.call_args.args[0]
    assert handler.redirect_request(request, None, 302, "", {}, "https://other.invalid") is None
    response.__enter__.return_value.status = 204
    with pytest.raises(OSError):
        cr.upload_report(BUILD, name, b"zip bytes")
    opener.open.side_effect = urllib.error.HTTPError(request.full_url, 412, "exists", {}, None)
    cr.upload_report(BUILD, name, b"zip bytes")
    opener.open.side_effect = urllib.error.HTTPError(request.full_url, 403, "refused", {}, None)
    with pytest.raises(urllib.error.HTTPError):
        cr.upload_report(BUILD, name, b"zip bytes")


def test_real_worker_thread_and_failed_task_are_reported(wiring):
    reporter = wiring.start()

    def fail():
        raise RuntimeError("worker exception")

    thread = threading.Thread(target=fail)
    thread.start()
    thread.join(3)
    loop = asyncio.new_event_loop()
    try:
        async def fail_task():
            raise ValueError("task exception")

        task = loop.create_task(fail_task())
        task.add_done_callback(reporter.task_done)
        loop.run_until_complete(asyncio.sleep(0))
        reporter.events.join()
        assert len(wiring.sent) == 2
        assert {unpack(item)[0]["message"] for item in wiring.sent} == {
            "worker exception", "task exception"}
    finally:
        loop.close()


def test_own_fatal_target_is_recovered_once_and_frontends_use_separate_files(wiring):
    directory = wiring.root / "queue"
    directory.mkdir()
    fatal = directory / "fatal-decky.log"
    fatal.write_text("Fatal Python error: Aborted\npassword=planted-secret\n")
    reporter = wiring.start(fatal_name="fatal-decky.log")
    reporter.capture(ValueError("startup barrier"))
    reporter.events.join()
    assert len(wiring.sent) == 2
    report, _ = unpack(wiring.sent[0])
    assert report["exception_type"] == "FatalError"
    assert "Aborted" in report["traceback"] and "planted-secret" not in report["traceback"]
    assert fatal.read_text() == "" and not (directory / "fatal-desktop.log").exists()
    reporter.close()
    reporter.worker.join(3)
    next_run = wiring.start(fatal_name="fatal-decky.log")
    next_run.capture(ValueError("another barrier"))
    next_run.events.join()
    assert len(wiring.sent) == 3


def test_settings_reject_corruption_and_keep_pending_reports_when_off(wiring):
    path = wiring.root / "settings.json"
    assert cr.reporting_enabled(str(path))
    for invalid in ("[]", '"unexpected"', "{broken"):
        path.write_text(invalid)
        assert not cr.reporting_enabled(str(path))
        with pytest.raises((ValueError, TypeError)):
            cr.set_reporting(str(path), True)
        assert path.read_text() == invalid


def test_scrubs_quoted_secrets_and_account_display_name(wiring, monkeypatch):
    import pwd

    monkeypatch.setattr(pwd, "getpwuid", lambda uid: types.SimpleNamespace(
        pw_dir=str(wiring.root / "account-home"), pw_name="private-login",
        pw_gecos="Private Person,Room"))
    planted = ('{"password": "two private words", "serial": "private serial"} '
               'psk=\'wifi private words\' Authorization: Basic cHJpdmF0ZQ== '
               'Private Person private-login')
    reporter = wiring.start(log_text=lambda: planted)
    reporter.capture(ValueError(planted))
    reporter.events.join()
    report, logs = unpack(wiring.sent[0])
    for secret in ("two private words", "private serial", "wifi private words",
                   "cHJpdmF0ZQ==", "Private Person", "private-login"):
        assert secret not in json.dumps(report) + logs


def test_queue_drops_oldest_arrival_when_random_suffix_sorts_later(tmp_path):
    import os

    pending = cr.CrashQueue(str(tmp_path), max_items=2)
    pending.add("z.zip", b"oldest")
    os.utime(tmp_path / "z.zip", ns=(1, 1))
    pending.add("a.zip", b"next")
    os.utime(tmp_path / "a.zip", ns=(2, 2))
    pending.add("b.zip", b"newest")
    assert pending.pending() == ["a.zip", "b.zip"]


def test_close_spools_accepted_crashes_and_worker_exits_when_idle(wiring, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def blocked(*args):
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr(cr, "upload_report", blocked)
    reporter = wiring.start()
    try:
        reporter.capture(ValueError("in flight"))
        assert entered.wait(3)
        assert not reporter.worker.daemon
        reporter.capture(ValueError("persist on exit"))
        reporter.close()
    finally:
        release.set()
    reporter.worker.join(3)
    assert not reporter.worker.is_alive()
    queued = list((wiring.root / "queue").glob("*.zip"))
    assert len(queued) == 1
    with zipfile.ZipFile(queued[0]) as archive:
        assert json.loads(archive.read("report.json"))["message"] == "persist on exit"


def test_decky_packaging_includes_helpers_and_replaces_missing_config_with_empty_default(tmp_path, monkeypatch):
    import install_check

    monkeypatch.setattr(install_check, "REPO", str(tmp_path))
    for module in ("crash_report.py", "crash_scrub.py", "crash_queue.py", "_crash_build.json"):
        assert f"py_modules/{module}" in install_check.DECKY_FILES
    default = tmp_path / "src/crash_build_defaults.json"
    assert install_check.decky_source("py_modules/_crash_build.json") == str(default)
    generated = tmp_path / "src/_crash_build.json"
    bake(generated, "abc1234", {})
    assert install_check.decky_source("py_modules/_crash_build.json") == str(generated)


def test_disk_failures_and_missing_account_do_not_escape_or_echo_credentials(wiring, monkeypatch, caplog):
    import pwd

    monkeypatch.setattr(pwd, "getpwuid", mock.Mock(side_effect=KeyError))
    reporter = wiring.start()
    reporter.capture(ValueError("anonymous uid"))
    reporter.events.join()
    assert len(wiring.sent) == 1
    monkeypatch.setattr(cr.CrashQueue, "add", mock.Mock(side_effect=OSError(BUILD["token"])))
    with caplog.at_level(logging.WARNING):
        reporter.capture(ValueError("disk full"))
        reporter.events.join()
    assert "unavailable" in caplog.text and BUILD["token"] not in caplog.text
