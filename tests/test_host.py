"""host.py: mode detection, each mode's argv/env, and run()'s never-raises contract.

run() is exercised with subprocess.run swapped for a recorder, so nothing runs
and neither the real ~/.config nor /run/user is touched.
"""

import subprocess

import pytest

import host

PATH_ENV = {"PATH": "/usr/bin", "HOME": "/home/deck"}
LOADER_ENV = {"PATH": "/usr/bin", "LD_LIBRARY_PATH": "/tmp/_MEI1234",
              "LD_LIBRARY_PATH_ORIG": "/usr/lib/host", "DECKY_PLUGIN_DIR": "/home/deck/homebrew/plugins/z13-helper"}


@pytest.mark.parametrize("environ,euid,mode", [
    ({"FLATPAK_ID": "io.github.iannitram.Z13Helper"}, 1000, "flatpak"),
    ({"FLATPAK_ID": "io.github.iannitram.Z13Helper"}, 0, "flatpak"),
    ({}, 0, "root"),
    (LOADER_ENV, 0, "root"),
    (LOADER_ENV, 1000, "decky"),
    ({"DECKY_PLUGIN_DIR": "/x"}, 1000, "decky"),
    ({"LD_LIBRARY_PATH_ORIG": ""}, 1000, "decky"),
    (PATH_ENV, 1000, "host"),
])
def test_detect(environ, euid, mode):
    assert host.detect(environ, euid) == mode


# ----------------------------------------------------------------------
# command(): argv and env per mode
# ----------------------------------------------------------------------

def test_flatpak_tunnels_out_and_inherits_env():
    for as_deck in (False, True):
        argv, env = host.command(["z13ctl", "profile", "--get"], as_deck=as_deck, mode="flatpak")
        assert argv == ["flatpak-spawn", "--host", "z13ctl", "profile", "--get"]
        assert env is None


def test_plain_host_runs_argv_as_is():
    for as_deck in (False, True):
        assert host.command(["systemctl", "--user", "is-active", "x"], as_deck=as_deck,
                            mode="host") == (["systemctl", "--user", "is-active", "x"], None)


def test_root_as_deck_hops_with_runuser():
    argv, env = host.command(["z13-profile", "list"], as_deck=True, mode="root", environ=LOADER_ENV)
    assert argv[:4] == ["runuser", "-u", "deck", "--"]
    assert argv[4] == "env"
    assert "HOME=/home/deck" in argv
    assert "XDG_RUNTIME_DIR=/run/user/1000" in argv
    assert "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus" in argv
    assert argv[-2:] == ["z13-profile", "list"]
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1000"


def test_root_without_as_deck_runs_directly_with_deck_env():
    argv, env = host.command(["systemctl", "start", "z13-lightbar-reset.service"], mode="root",
                             environ=LOADER_ENV)
    assert argv == ["systemctl", "start", "z13-lightbar-reset.service"]
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1000"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"


def test_decky_as_deck_never_uses_runuser():
    # runuser refuses to run at all when the caller is deck already.
    argv, env = host.command(["z13ctl", "batterylimit", "--set", "80"], as_deck=True,
                             mode="decky", environ=LOADER_ENV)
    assert "runuser" not in argv
    assert argv[0] == "env" and "HOME=/home/deck" in argv
    assert argv[-4:] == ["z13ctl", "batterylimit", "--set", "80"]
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1000"


@pytest.mark.parametrize("mode", ["root", "decky"])
def test_loader_library_path_is_swapped_for_the_hosts(mode):
    # The loader's bundled libcrypto broke every systemctl call (OPENSSL_3.4.0 not found).
    _, env = host.command(["systemctl", "--user", "is-active", "x"], mode=mode, environ=LOADER_ENV)
    assert env["LD_LIBRARY_PATH"] == "/usr/lib/host"


def test_library_path_dropped_when_no_original():
    environ = {"LD_LIBRARY_PATH": "/tmp/_MEI1234"}
    _, env = host.command(["true"], mode="root", environ=environ)
    assert "LD_LIBRARY_PATH" not in env
    assert environ == {"LD_LIBRARY_PATH": "/tmp/_MEI1234"}     # caller's dict untouched


# ----------------------------------------------------------------------
# run()
# ----------------------------------------------------------------------

@pytest.fixture
def recorded(monkeypatch):
    """subprocess.run replaced by a recorder; `answer` decides what it does."""
    calls = []

    def fake(cmd, **kw):
        calls.append((cmd, kw))
        return recorded_answer[0](cmd, kw)
    recorded_answer = [lambda cmd, kw: subprocess.CompletedProcess(cmd, 0, "out\n", "err\n")]
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(host, "MODE", "host")
    fake.calls, fake.answer = calls, recorded_answer
    return fake


def test_run_combines_stdout_and_stderr(recorded):
    r = host.run(["z13ctl", "status"], timeout=9)
    assert r == host.Result(0, "out\nerr", False)
    cmd, kw = recorded.calls[0]
    assert cmd == ["z13ctl", "status"]
    assert kw["timeout"] == 9 and kw["env"] is None and kw["capture_output"] and kw["text"]


def test_run_passes_input(recorded):
    host.run(["tee", "/sys/class/backlight/x/brightness"], input="5\n")
    assert recorded.calls[0][1]["input"] == "5\n"


def test_run_uses_the_detected_mode(recorded, monkeypatch):
    monkeypatch.setattr(host, "MODE", "flatpak")
    host.run(["z13ctl", "status"])
    assert recorded.calls[0][0][:2] == ["flatpak-spawn", "--host"]


def test_run_timeout(recorded):
    def boom(cmd, kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])
    recorded.answer[0] = boom
    r = host.run(["z13ctl", "--device", "lightbar", "brightness", "low"], timeout=30)
    assert r.rc == host.RC_TIMEOUT and r.timed_out
    assert "timed out" in r.out     # profiles.lightbar_failure_kind reads this as "wedged"


def test_run_missing_binary(recorded):
    def boom(cmd, kw):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])
    recorded.answer[0] = boom
    r = host.run(["/nope/z13ctl"])
    assert r.rc == host.RC_NOT_FOUND and not r.timed_out and "/nope/z13ctl" in r.out


def test_run_other_os_error(recorded):
    def boom(cmd, kw):
        raise PermissionError(13, "Permission denied")
    recorded.answer[0] = boom
    assert host.run(["/root/x"]).rc == 1


def test_run_lets_the_test_guard_through():
    # conftest's guard is still in place: a test that forgets fake_host fails loudly.
    with pytest.raises(AssertionError):
        host.run(["z13ctl", "status"])
