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
