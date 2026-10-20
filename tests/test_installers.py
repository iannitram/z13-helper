"""src/installers.py: CLI dispatch, release verification, dry-run repair.

No network, no sudo: `fetch`, `subprocess.run` and `run` are faked everywhere
(the autouse guard in conftest.py fails any test that slips through).
"""

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tarfile
import types

import pytest

import installers

VER = "1.4.2"
TAR = f"z13ctl_{VER}_linux_amd64.tar.gz"


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """Point every host path installers.py touches into tmp_path."""
    ns = types.SimpleNamespace(
        cache=tmp_path / "cache",
        units=tmp_path / "systemd-user",
        udev=tmp_path / "etc" / "99-z13ctl.rules",
        perms=tmp_path / "etc" / "z13ctl-perms.service",
        decky_version=tmp_path / "homebrew" / ".loader.version",
        z13ctl=tmp_path / "bin" / "z13ctl",
    )
    monkeypatch.setattr(installers, "CACHE", str(ns.cache))
    monkeypatch.setattr(installers, "USER_UNITS", str(ns.units))
    monkeypatch.setattr(installers, "UDEV_RULES", str(ns.udev))
    monkeypatch.setattr(installers, "PERMS_UNIT", str(ns.perms))
    monkeypatch.setattr(installers, "DECKY_VERSION_FILE", str(ns.decky_version))
    monkeypatch.setattr(installers, "Z13CTL", str(ns.z13ctl))
    return ns


class Systemctl:
    """Fake subprocess.run for read-only `systemctl is-enabled/is-active` probes.

    Any argv containing sudo, or any non-systemctl/non-probe command, fails the test.
    """

    def __init__(self, enabled=(), active=()):
        self.enabled, self.active, self.calls = set(enabled), set(active), []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        assert "sudo" not in argv, f"dry run called sudo: {argv}"
        assert argv[0] == "systemctl" and argv[-2] in ("is-enabled", "is-active"), argv
        unit = argv[-1]
        if argv[-2] == "is-enabled":
            out, rc = ("enabled", 0) if unit in self.enabled else ("disabled", 1)
        else:
            out, rc = ("active", 0) if unit in self.active else ("inactive", 3)
        return subprocess.CompletedProcess(argv, rc, stdout=out + "\n", stderr="")


def forbid(name):
    def _f(*a, **k):
        raise AssertionError(f"{name} must not be called in a dry run: {a}")
    return _f


@pytest.fixture
def dry(monkeypatch, paths):
    """Dry-run mode with every side-effecting helper forbidden."""
    monkeypatch.setattr(installers, "DRY_RUN", True)
    monkeypatch.setattr(installers, "run", forbid("run"))
    monkeypatch.setattr(installers, "_fetch_release", forbid("_fetch_release"))
    monkeypatch.setattr(installers, "_setup_perms", forbid("_setup_perms"))
    monkeypatch.setattr(installers, "_start_daemon", forbid("_start_daemon"))
    monkeypatch.setattr(installers, "_install_daemon_units", forbid("_install_daemon_units"))
    monkeypatch.setattr(installers.time, "sleep", forbid("time.sleep"))
    return paths


# ----------------------------------------------------------------------
# main()
# ----------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    [], ["plan"], ["install"], ["install", "z13ctl", "extra"], ["frob", "z13ctl"],
    ["install", "kernel"], ["plan", "asusctl"], ["repair", "steamos"], ["repair", "bogus"],
])
def test_main_usage(argv, capsys):
    assert installers.main(argv) == 2
    err = capsys.readouterr().err
    assert err.startswith("usage: installers.py plan|install {z13ctl|decky|steamos}")
    assert "repair {z13ctl|decky}" in err


@pytest.mark.parametrize("what", ["z13ctl", "decky", "steamos"])
def test_main_plan(what, capsys):
    assert installers.main(["plan", what]) == 0
    assert capsys.readouterr().out.strip()


def test_main_install_dispatch_and_failure(monkeypatch, capsys):
    def boom():
        raise RuntimeError("network down")
    monkeypatch.setitem(installers.INSTALLERS, "z13ctl", (installers.plan_z13ctl, boom))
    assert installers.main(["install", "z13ctl"]) == 1
    out = capsys.readouterr().out
    assert "== Installing z13ctl ==" in out and "FAILED: network down" in out


def test_main_repair_uses_repair_fn(monkeypatch, capsys):
    calls = []
    monkeypatch.setitem(installers.INSTALLERS, "decky",
                        (installers.plan_decky, forbid("install_decky")))
    monkeypatch.setitem(installers.REPAIRS, "decky", lambda: calls.append("repair") or 0)
    assert installers.main(["repair", "decky"]) == 0
    assert calls == ["repair"]
    assert "== Repairing decky ==" in capsys.readouterr().out


def test_main_returns_installer_rc(monkeypatch):
    monkeypatch.setitem(installers.REPAIRS, "z13ctl", lambda: 3)
    assert installers.main(["repair", "z13ctl"]) == 3


def test_dry_run_flag_read_from_env_at_import(monkeypatch):
    """Z13_INSTALL_DRY_RUN is read once at import (install.sh exports it first)."""
    def load(env):
        monkeypatch.setenv("Z13_INSTALL_DRY_RUN", env)
        spec = importlib.util.spec_from_file_location("installers_fresh", installers.__file__)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.DRY_RUN
    assert load("1") is True
    assert load("0") is False
    assert load("yes") is False


# ----------------------------------------------------------------------
# _fetch_release
# ----------------------------------------------------------------------

def make_tarball(extra_members=()):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in [("z13ctl", b"#!/bin/sh\necho z13ctl 1.4.2\n"),
                           ("contrib/systemd/user/z13ctl.service", b"[Service]\n"),
                           *extra_members]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def fake_fetch(tar_bytes, sums_text, assets=(TAR, "checksums.txt"), log=None):
    def fetch(url, dest=None, timeout=120):
        if log is not None:
            log.append(url)
        if "api.github.com" in url:
            return json.dumps({
                "tag_name": f"v{VER}", "published_at": "2026-10-01T00:00:00Z",
                "assets": [{"name": a, "browser_download_url": f"https://dl/{a}"} for a in assets],
            }).encode()
        if url.endswith(".tar.gz"):
            os.makedirs(os.path.dirname(dest), exist_ok=True)    # as the real fetch() does
            with open(dest, "wb") as f:
                f.write(tar_bytes)
            return dest
        if url.endswith("checksums.txt"):
            return sums_text.encode()
        raise AssertionError(f"unexpected fetch {url}")
    return fetch


def test_fetch_release_checksum_mismatch_raises(paths, monkeypatch):
    tar = make_tarball()
    sums = f"{'0' * 64}  {TAR}\n{hashlib.sha256(b'other').hexdigest()}  other.tar.gz\n"
    monkeypatch.setattr(installers, "fetch", fake_fetch(tar, sums))
    monkeypatch.setattr(installers, "_z13ctl_installed_version", forbid("version probe"))
    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        installers._fetch_release()


def test_fetch_release_missing_checksum_entry(paths, monkeypatch):
    monkeypatch.setattr(installers, "fetch", fake_fetch(make_tarball(), "abc  other.tar.gz\n"))
    with pytest.raises(RuntimeError, match="no entry"):
        installers._fetch_release()


def test_fetch_release_missing_assets(paths, monkeypatch):
    monkeypatch.setattr(installers, "fetch", fake_fetch(b"", "", assets=("README.md",)))
    with pytest.raises(RuntimeError, match="release has no"):
        installers._fetch_release()


def test_fetch_release_ok_and_skips_unsafe_members(paths, monkeypatch, tmp_path):
    tar = make_tarball(extra_members=[("../escape", b"pwned")])
    sums = f"{hashlib.sha256(tar).hexdigest()}  {TAR}\n"
    urls = []
    monkeypatch.setattr(installers, "fetch", fake_fetch(tar, sums, log=urls))
    monkeypatch.setattr(installers, "_z13ctl_installed_version", lambda path: VER)
    monkeypatch.setattr(installers.tempfile, "mkdtemp", lambda prefix: str(tmp_path / "x" / "extract"))
    (tmp_path / "x" / "extract").mkdir(parents=True)

    ver, out = installers._fetch_release("v1.4.2")
    assert ver == VER
    assert (tmp_path / "x" / "extract" / "z13ctl").is_file()
    assert not (tmp_path / "x" / "escape").exists()
    assert urls[0].endswith("/releases/tags/v1.4.2")


def test_fetch_release_version_mismatch(paths, monkeypatch, tmp_path):
    tar = make_tarball()
    sums = f"{hashlib.sha256(tar).hexdigest()}  {TAR}\n"
    monkeypatch.setattr(installers, "fetch", fake_fetch(tar, sums))
    monkeypatch.setattr(installers, "_z13ctl_installed_version", lambda path: "1.3.0")
    with pytest.raises(RuntimeError, match="reports 1.3.0, expected 1.4.2"):
        installers._fetch_release()


def test_sha256(tmp_path):
    f = tmp_path / "f"
    f.write_bytes(b"hello")
    assert installers.sha256(f) == hashlib.sha256(b"hello").hexdigest()


# ----------------------------------------------------------------------
# repair_z13ctl, dry run
# ----------------------------------------------------------------------

def logs(capsys):
    return capsys.readouterr().out


def install_units(p, override=None):
    (p.units / "z13ctl.service.d").mkdir(parents=True)
    (p.units / "z13ctl.socket").write_text("")
    (p.units / "z13ctl.service").write_text("")
    (p.units / "z13ctl.service.d" / "override.conf").write_text(
        installers.DAEMON_OVERRIDE if override is None else override)


def test_repair_z13ctl_dry_run_broken_machine(dry, monkeypatch, capsys):
    """After a SteamOS update: rules gone, units gone, daemon down."""
    monkeypatch.setattr(installers, "_z13ctl_installed_version", lambda path=None: VER)
    sysctl = Systemctl()
    monkeypatch.setattr(subprocess, "run", sysctl)

    assert installers.repair_z13ctl() == 0
    out = logs(capsys)
    assert f"Installed z13ctl: {VER} (kept" in out
    assert "(dry run) would run `sudo z13ctl setup` (rules or perms unit missing)" in out
    assert "Daemon units missing" in out
    assert "(dry run) would enable z13ctl.socket and (re)start z13ctl.service" in out
    assert not dry.units.exists()                    # nothing written


def test_repair_z13ctl_dry_run_healthy_machine(dry, monkeypatch, capsys):
    monkeypatch.setattr(installers, "_z13ctl_installed_version", lambda path=None: VER)
    dry.udev.parent.mkdir(parents=True)
    dry.udev.write_text("")
    dry.perms.write_text("")
    install_units(dry)
    monkeypatch.setattr(subprocess, "run", Systemctl(enabled={"z13ctl-perms.service", "z13ctl.socket"},
                                                     active={"z13ctl.service"}))
    assert installers.repair_z13ctl() == 0
    out = logs(capsys)
    assert "(dry run) would run `sudo z13ctl setup --perms-only`" in out
    assert "Daemon units + --no-button override: OK" in out
    assert "Daemon: active" in out
    assert "would enable" not in out


def test_repair_z13ctl_dry_run_stale_override_not_rewritten(dry, monkeypatch, capsys):
    monkeypatch.setattr(installers, "_z13ctl_installed_version", lambda path=None: VER)
    install_units(dry, override="[Service]\nExecStart=z13ctl daemon\n")
    monkeypatch.setattr(subprocess, "run", Systemctl(enabled={"z13ctl.socket"}, active={"z13ctl.service"}))
    assert installers.repair_z13ctl() == 0
    out = logs(capsys)
    assert "Daemon override missing/different" in out
    assert (dry.units / "z13ctl.service.d" / "override.conf").read_text() == "[Service]\nExecStart=z13ctl daemon\n"


def test_repair_z13ctl_dry_run_missing_binary_does_not_install(dry, monkeypatch, capsys):
    monkeypatch.setattr(installers, "_z13ctl_installed_version", lambda path=None: None)
    urls = []
    monkeypatch.setattr(installers, "fetch", fake_fetch(b"", "", log=urls))
    assert installers.repair_z13ctl() == 0
    out = logs(capsys)
    assert "z13ctl missing or not runnable" in out
    assert f"(dry run) would install z13ctl {VER}" in out
    assert urls == [f"{installers.API}/dahui/z13ctl/releases/latest"]   # metadata only
    assert not dry.z13ctl.exists()


def test_daemon_override_disables_button_with_absolute_path():
    o = installers.DAEMON_OVERRIDE
    assert "ExecStart=\n" in o                         # clears the packaged ExecStart first
    assert f"ExecStart={installers.Z13CTL} daemon --no-button" in o
    assert installers.Z13CTL.startswith("/")


# ----------------------------------------------------------------------
# repair_decky, dry run
# ----------------------------------------------------------------------

def decky_fetch(latest, urls):
    def fetch(url, dest=None, timeout=120):
        urls.append(url)
        if latest is None:
            raise OSError("no network")
        assert url.endswith("/decky-loader/releases/latest"), url
        return json.dumps({"tag_name": f"v{latest}"}).encode()
    return fetch


@pytest.fixture
def plugin_unit(monkeypatch):
    """Control the hard-coded /etc/systemd/system/plugin_loader.service check."""
    state = {"present": True}
    real_exists = installers.os.path.exists

    def exists(p):
        if p == "/etc/systemd/system/plugin_loader.service":
            return state["present"]
        return real_exists(p)
    monkeypatch.setattr(installers.os.path, "exists", exists)
    return state


def test_repair_decky_dry_run_missing(dry, monkeypatch, capsys, plugin_unit):
    plugin_unit["present"] = False
    urls = []
    monkeypatch.setattr(installers, "fetch", decky_fetch("3.1.10", urls))
    monkeypatch.setattr(subprocess, "run", Systemctl())
    assert installers.repair_decky() == 0
    out = logs(capsys)
    assert "installed none, latest 3.1.10, unit MISSING, NOT active" in out
    assert "(dry run) would run the official Decky installer" in out
    assert installers.DECKY_INSTALLER not in urls     # installer script not downloaded


def test_repair_decky_dry_run_behind_latest(dry, monkeypatch, capsys, plugin_unit):
    dry.decky_version.parent.mkdir(parents=True)
    dry.decky_version.write_text("v3.1.9\n")
    monkeypatch.setattr(installers, "fetch", decky_fetch("3.1.10", []))
    monkeypatch.setattr(subprocess, "run", Systemctl(active={"plugin_loader.service"}))
    assert installers.repair_decky() == 0
    assert "(dry run) would run the official Decky installer" in logs(capsys)


def test_repair_decky_ok(dry, monkeypatch, capsys, plugin_unit):
    dry.decky_version.parent.mkdir(parents=True)
    dry.decky_version.write_text("v3.1.10\n")
    monkeypatch.setattr(installers, "fetch", decky_fetch("3.1.10", []))
    monkeypatch.setattr(installers, "install_decky", forbid("install_decky"))
    monkeypatch.setattr(subprocess, "run", Systemctl(active={"plugin_loader.service"}))
    assert installers.repair_decky() == 0
    assert "Decky Loader: OK" in logs(capsys)


def test_repair_decky_offline_judges_local_state(dry, monkeypatch, capsys, plugin_unit):
    dry.decky_version.parent.mkdir(parents=True)
    dry.decky_version.write_text("3.0.0")
    monkeypatch.setattr(installers, "fetch", decky_fetch(None, []))
    monkeypatch.setattr(installers, "install_decky", forbid("install_decky"))
    monkeypatch.setattr(subprocess, "run", Systemctl(active={"plugin_loader.service"}))
    assert installers.repair_decky() == 0
    out = logs(capsys)
    assert "Could not read the latest Decky release" in out
    assert "Decky Loader: OK" in out


def test_decky_installed_version(paths):
    assert installers._decky_installed_version() is None
    paths.decky_version.parent.mkdir(parents=True)
    paths.decky_version.write_text("")
    assert installers._decky_installed_version() is None
    paths.decky_version.write_text("v3.1.10\n")
    assert installers._decky_installed_version() == "3.1.10"


# ----------------------------------------------------------------------
# Failure propagation + honest result (#17)
# ----------------------------------------------------------------------

def test_repair_z13ctl_fails_when_daemon_does_not_start(paths, monkeypatch, capsys):
    """Everything else healthy, daemon down and `_start_daemon` reports failure."""
    monkeypatch.setattr(installers, "DRY_RUN", False)
    monkeypatch.setattr(installers, "_z13ctl_installed_version", lambda path=None: VER)
    monkeypatch.setattr(installers, "_setup_perms", lambda perms_only=False: None)
    install_units(paths)
    monkeypatch.setattr(installers, "_unit_enabled", lambda *a, **k: True)
    monkeypatch.setattr(subprocess, "run", Systemctl())          # daemon inactive
    started = []
    monkeypatch.setattr(installers, "_start_daemon", lambda restart=True: started.append(1) or 3)
    assert installers.repair_z13ctl() != 0
    assert started
    assert "daemon" in logs(capsys).lower()


def test_decky_install_fails_when_health_check_fails(paths, monkeypatch):
    monkeypatch.setattr(installers, "DRY_RUN", False)
    monkeypatch.setattr(installers, "fetch", lambda url, dest=None, timeout=120: dest)
    monkeypatch.setattr(installers, "run", lambda argv, **k: (0, ""))
    monkeypatch.setattr(installers.time, "sleep", lambda s: None)
    monkeypatch.setattr(installers, "_decky_health", lambda: 3)
    assert installers.install_decky() != 0
    monkeypatch.setattr(installers, "_decky_health", lambda: 0)
    assert installers.install_decky() == 0


def _install_env(paths, monkeypatch, old_bytes=b"OLD-BINARY", daemon_rc=0, setup_raises=None):
    """install_z13ctl with the release, sudo and systemd faked; old binary on disk."""
    monkeypatch.setattr(installers, "DRY_RUN", False)
    paths.z13ctl.parent.mkdir(parents=True)
    paths.z13ctl.write_bytes(old_bytes)
    new_dir = paths.cache.parent / "extract"
    (new_dir / "contrib" / "systemd" / "user").mkdir(parents=True)
    (new_dir / "z13ctl").write_bytes(b"NEW-BINARY")
    monkeypatch.setattr(installers, "_z13ctl_installed_version", lambda path=None: "1.4.1")
    monkeypatch.setattr(installers, "fetch", lambda url, dest=None, timeout=120: json.dumps(
        {"tag_name": f"v{VER}", "published_at": "2026-10-01"}).encode())
    monkeypatch.setattr(installers, "_fetch_release", lambda tag=None: (VER, str(new_dir)))

    def setup(perms_only=False):
        if setup_raises:
            raise RuntimeError(setup_raises)
    monkeypatch.setattr(installers, "_setup_perms", setup)
    monkeypatch.setattr(installers, "_install_daemon_units", lambda d: None)
    monkeypatch.setattr(installers, "_start_daemon", lambda restart=True: daemon_rc)
    monkeypatch.setattr(installers, "run", lambda argv, **k: (0, ""))
    monkeypatch.setattr(installers.shutil, "rmtree", lambda *a, **k: None)
    installers.REPORT.reset()


def test_install_setup_failure_restores_verified_backup(paths, monkeypatch, capsys):
    _install_env(paths, monkeypatch, setup_raises="sudo exited 1")
    assert installers.main(["install", "z13ctl"]) == 1
    assert paths.z13ctl.read_bytes() == b"OLD-BINARY"
    out = logs(capsys)
    assert "RESULT:" in out
    res = installers.REPORT.as_dict()
    assert any("binary" in c for c in res["changed"])
    assert res["rollback"] == "restored-verified"


def test_install_daemon_failure_is_failure_and_rolls_back(paths, monkeypatch, capsys):
    _install_env(paths, monkeypatch, daemon_rc=3)
    assert installers.main(["install", "z13ctl"]) == 1
    assert paths.z13ctl.read_bytes() == b"OLD-BINARY"


def test_install_failure_without_previous_binary_says_not_rolled_back(paths, monkeypatch, capsys):
    _install_env(paths, monkeypatch, setup_raises="boom")
    paths.z13ctl.unlink()
    monkeypatch.setattr(installers, "_z13ctl_installed_version", lambda path=None: None)
    assert installers.main(["install", "z13ctl"]) == 1
    assert installers.REPORT.as_dict()["rollback"] == "not-rolled-back"


def test_failure_text_does_not_claim_nothing_changed(paths, monkeypatch, capsys):
    import install_record
    _install_env(paths, monkeypatch, setup_raises="boom")
    paths.z13ctl.unlink()
    monkeypatch.setattr(installers, "_z13ctl_installed_version", lambda path=None: None)
    installers.main(["install", "z13ctl"])
    lines = logs(capsys).splitlines()
    text = install_record.failure_text(lines, 1)
    assert "nothing else was changed" not in text
    assert "not rolled back" in text and "binary" in text
    # no RESULT line at all (older host script / crash): do not claim nothing changed
    assert "nothing else was changed" not in install_record.failure_text(["FAILED: x"], 1)


def test_rollback_restarts_daemon_and_reports_result(paths, monkeypatch, capsys):
    _install_env(paths, monkeypatch, daemon_rc=3)
    calls = []

    def start(restart=True):
        calls.append(paths.z13ctl.read_bytes())    # binary on disk at each call
        return 3
    monkeypatch.setattr(installers, "_start_daemon", start)
    assert installers.main(["install", "z13ctl"]) == 1
    assert calls == [b"NEW-BINARY", b"OLD-BINARY"]   # restarted after the restore
    res = installers.REPORT.as_dict()
    assert res["rollback"] == "restored-verified"
    assert res["daemon_after_rollback"] == "not-running"
    assert "daemon NOT running" in logs(capsys)
