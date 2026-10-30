"""Decky Loader 3.3.0 minimum (issue #77): versions.older_than and repair_decky."""

import json
import types

import pytest

import installers
import versions


def test_decky_min_is_3_3_0():
    assert versions.DECKY_MIN == "3.3.0"


@pytest.mark.parametrize("have, minimum, expected", [
    ("3.2.10", "3.3.0", True),
    ("3.3.0", "3.3.0", False),
    ("3.10.1", "3.3.0", False),
    ("v3.2.10", "3.3.0", True),
    ("v3.3.0", "3.3.0", False),
    (None, "3.3.0", True),
    ("not-a-version", "3.3.0", True),
])
def test_older_than(have, minimum, expected):
    assert versions.older_than(have, minimum) is expected


@pytest.fixture
def decky_env(monkeypatch):
    """repair_decky with unit present, active, latest 3.3.0; install_decky recorded."""
    calls = []
    unit = "/etc/systemd/system/plugin_loader.service"
    real_exists = installers.os.path.exists
    monkeypatch.setattr(installers.os.path, "exists", lambda p: True if p == unit else real_exists(p))
    monkeypatch.setattr(installers.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout="active\n", stderr="", returncode=0))
    monkeypatch.setattr(installers, "fetch", lambda *a, **k: json.dumps({"tag_name": "v3.3.0"}))
    monkeypatch.setattr(installers, "install_decky", lambda: calls.append("install") or 0)
    return calls


def test_repair_with_old_version_installs(decky_env, monkeypatch):
    monkeypatch.setattr(installers, "_decky_installed_version", lambda: "3.2.10")
    installers.repair_decky()
    assert decky_env == ["install"]


def test_repair_with_3_3_0_present_active_latest_does_not_install(decky_env, monkeypatch):
    monkeypatch.setattr(installers, "_decky_installed_version", lambda: "3.3.0")
    assert installers.repair_decky() == 0
    assert decky_env == []


def test_repair_below_minimum_installs_when_latest_release_unavailable(monkeypatch):
    calls = []
    monkeypatch.setattr(installers, "_decky_installed_version", lambda: "3.2.10")
    monkeypatch.setattr(installers.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(installers.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout="active\n", stderr="", returncode=0))
    monkeypatch.setattr(installers, "fetch", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(installers, "install_decky", lambda: calls.append("install") or 0)
    assert installers.repair_decky() == 0
    assert calls == ["install"]


def test_updates_row_forces_minimum_repair_even_when_latest_was_seen():
    import update_watch

    cache = {"items": {"decky": {"latest": "3.3.0", "installed": "3.2.10", "seen": "3.3.0"}}}
    row = next(r for r in update_watch.summary(cache) if r["id"] == "decky")
    assert row["new"] is True
    assert "Needs 3.3.0 or newer" in row["note"]


def test_install_check_marks_old_decky_red(tmp_path, monkeypatch):
    import install_check

    version_file = tmp_path / "homebrew" / "services" / ".loader.version"
    version_file.parent.mkdir(parents=True)
    version_file.write_text("v3.2.10\n")
    monkeypatch.setattr(install_check, "HOME", str(tmp_path))
    monkeypatch.setattr(install_check, "_active", lambda *a, **k: "active")
    monkeypatch.setattr(install_check, "sh", lambda *a, **k: (0, "LISTEN 0 128 *:1337", ""))
    monkeypatch.setattr(install_check, "same", lambda *a, **k: True)
    monkeypatch.setattr(install_check, "DECKY_LOGS", str(tmp_path / "logs"))

    class Report:
        def __init__(self): self.rows = []
        def head(self, *_): pass
        def add(self, status, name, detail): self.rows.append((status, name, detail))

    report = Report()
    install_check.check_decky(report)
    row = next(row for row in report.rows if row[1] == "Decky version")
    assert row[0] == install_check.RED
    assert row[2] == "Decky 3.2.10 < 3.3.0 (Steam client 2026-10-13 needs 3.3.0+)"
