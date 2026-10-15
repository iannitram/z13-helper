"""Desktop Mode autostart: the unit takes over from a session-restored copy, a
deliberate Quit is remembered, and the button watcher starts a missing app."""

import importlib.machinery
import importlib.util
import os
import socket
import threading
import time
from pathlib import Path

import pytest

import single_instance

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("FLATPAK_ID", "io.github.iannitram.Z13Helper")
    return tmp_path / "app" / "io.github.iannitram.Z13Helper"


def test_acquire_lock_waits_for_an_exiting_primary(runtime):
    held = single_instance.acquire_lock()
    assert held is not None
    assert single_instance.acquire_lock() is None
    threading.Timer(0.3, os.close, [held]).start()       # the old primary exits
    t0 = time.monotonic()
    fd = single_instance.acquire_lock(wait=3.0)
    assert fd is not None and time.monotonic() - t0 < 2.0
    os.close(fd)


def test_acquire_lock_wait_gives_up(runtime):
    held = single_instance.acquire_lock()
    assert single_instance.acquire_lock(wait=0.2) is None
    os.close(held)


def test_user_quit_marker(runtime):
    single_instance.mark_user_quit()
    assert (runtime / "z13-helper.user-quit").exists()
    single_instance.clear_user_quit()
    assert not (runtime / "z13-helper.user-quit").exists()
    single_instance.clear_user_quit()                     # absent: no error


def test_unit_starts_the_app_as_resident():
    unit = (REPO / "packaging/host/systemd/user/z13-helper.service").read_text()
    start = [l for l in unit.splitlines() if l.startswith("ExecStart=")]
    assert len(start) == 1 and start[0].endswith(" --resident")


def test_main_takes_over_instead_of_exiting_when_resident():
    src = (REPO / "main.py").read_text()
    i = src.index('if lock_fd is None and "--resident" in sys.argv:')
    block = src[i:src.index("if lock_fd is None:", i + 10)]
    assert 'send_command("quit"' in block and "acquire_lock(wait=" in block
    assert "RestartNever" in src                          # and opts out of session restore


# ---- z13-button-watcher, Desktop Mode ----

@pytest.fixture
def watcher(tmp_path, monkeypatch):
    path = str(REPO / "tooling" / "z13-button-watcher")
    loader = importlib.machinery.SourceFileLoader("z13_button_watcher", path)
    spec = importlib.util.spec_from_loader("z13_button_watcher", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    sock = tmp_path / "z13-helper.sock"
    monkeypatch.setattr(mod, "APP_SOCKET", str(sock))
    monkeypatch.setattr(mod, "in_gamemode", lambda: False)
    started = []
    monkeypatch.setattr(mod, "start_desktop_app", lambda: started.append(1))
    mod.started, mod.sock = started, sock
    return mod


def listen(path, got):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(4)

    def serve():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            got.append(c.recv(64))
            c.close()
    threading.Thread(target=serve, daemon=True).start()
    return srv


def test_watcher_starts_the_app_when_nothing_answers(watcher):
    watcher.handle_press(False)
    assert watcher.started == [1]


def test_watcher_leaves_a_running_app_alone(watcher):
    got = []
    srv = listen(watcher.sock, got)
    try:
        watcher.handle_press(False)
        assert watcher.started == []
        deadline = time.monotonic() + 2
        while not got and time.monotonic() < deadline:
            time.sleep(0.01)
        assert got and got[0].startswith(b"ping")
    finally:
        srv.close()


def test_watcher_stale_socket_counts_as_not_running(watcher):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(watcher.sock))
    srv.listen(1)
    srv.close()                                           # file left behind, nobody listening
    watcher.handle_press(False)
    assert watcher.started == [1]
