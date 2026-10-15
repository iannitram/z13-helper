"""Shared fixtures for the z13-helper test suite.

Nothing here touches hardware, the network, sudo, or the real ~/.config:
- src/ and tooling/ go on sys.path so the modules import by their bare names,
  exactly as main.py and the host scripts import them.
- An autouse guard makes subprocess.run / Popen and urllib's urlopen raise, so a
  test that forgets to inject a fake fails loudly instead of running z13ctl.
- install_record.INSTALL_JSON (the logging choice) points into tmp_path.
- `profiles_env` redirects every path profiles.py and max_fans_state.py write
  into tmp_path.
"""

import os
import subprocess
import sys
import types
import urllib.request
from pathlib import Path

import pytest

# Keep src/__pycache__ out of the checkout (flatpak_diff walks src/).
sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parent.parent
for sub in ("tooling", "src"):
    p = str(REPO / sub)
    if p not in sys.path:
        sys.path.insert(0, p)


# ----------------------------------------------------------------------
# Safety net: no real processes, no real network.
# ----------------------------------------------------------------------

class UnexpectedExternalCall(AssertionError):
    pass


def _refuse(what):
    def _raise(*args, **kwargs):
        raise UnexpectedExternalCall(f"test tried to call {what}: {args[:1]!r}")
    return _raise


_REAL_RUN, _REAL_POPEN = subprocess.run, subprocess.Popen


@pytest.fixture
def real_subprocess(monkeypatch):
    """Opt-in for the few tests that run one of the repo's own shell scripts
    against a fake tree in tmp_path (sh -n, the lightbar reset script, install.sh
    --dry-run with PATH stubs). Requested explicitly, so it runs after the guard."""
    monkeypatch.setattr(subprocess, "run", _REAL_RUN)
    monkeypatch.setattr(subprocess, "Popen", _REAL_POPEN)
    return _REAL_RUN


@pytest.fixture(autouse=True)
def no_real_install_json(tmp_path, monkeypatch):
    """install_record's default path (the logging choice, the last install) goes
    to tmp_path, never the real ~/.config/z13-helper/install.json."""
    import install_record
    monkeypatch.setattr(install_record, "INSTALL_JSON", str(tmp_path / "install.json"))


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _refuse("subprocess.run"))
    monkeypatch.setattr(subprocess, "Popen", _refuse("subprocess.Popen"))
    monkeypatch.setattr(urllib.request, "urlopen", _refuse("urllib.request.urlopen"))


# ----------------------------------------------------------------------
# profiles.py / max_fans_state.py
# ----------------------------------------------------------------------

@pytest.fixture
def profiles_env(tmp_path, monkeypatch):
    """profiles + max_fans_state with every file path inside tmp_path.

    profiles.MAX_FANS_PATH and max_fans_state.STATE_PATH point at the same file,
    as they do on the device.
    """
    import max_fans_state
    import profiles

    cfg = tmp_path / "config" / "z13-helper"
    paths = types.SimpleNamespace(
        store=cfg / "profiles.json",
        max_fans=cfg / "max-fans.json",
        legacy=tmp_path / "config" / "z13-profiles" / "current-profile",
    )
    monkeypatch.setattr(profiles, "STORE_PATH", str(paths.store))
    monkeypatch.setattr(profiles, "MAX_FANS_PATH", str(paths.max_fans))
    monkeypatch.setattr(profiles, "LEGACY_CURRENT_PATH", str(paths.legacy))
    monkeypatch.setattr(max_fans_state, "STATE_PATH", str(paths.max_fans))
    # Lightbar recovery: stamp inside tmp_path, no real settle wait.
    paths.run = tmp_path / "run"
    paths.run.mkdir()
    paths.lightbar_stamp = paths.run / "z13-lightbar-reset.stamp"
    paths.lightbar_daemon_stamp = paths.run / "z13-lightbar-daemon-restart.stamp"
    monkeypatch.setattr(profiles, "lightbar_stamp_path", lambda: str(paths.lightbar_stamp))
    monkeypatch.setattr(profiles, "lightbar_daemon_stamp_path", lambda: str(paths.lightbar_daemon_stamp))
    monkeypatch.setattr(profiles, "LIGHTBAR_RESET_SETTLE_S", 0)
    monkeypatch.setattr(profiles, "LIGHTBAR_DAEMON_RESTART_WAIT_S", 0)
    return paths


class FakeRunner:
    """Stands in for profiles.host_runner: records argv, returns (rc, out).

    `fail(pred, rc, out)` makes every call whose argv satisfies pred return
    (rc, out); everything else succeeds with (0, "").
    """

    def __init__(self):
        self.calls = []
        self._rules = []

    def fail(self, pred, rc=1, out="boom"):
        self._rules.append((pred, rc, out))
        return self

    def __call__(self, argv, timeout=120):
        self.calls.append(list(argv))
        for pred, rc, out in self._rules:
            if pred(argv):
                return rc, out
        return 0, ""

    @property
    def steps(self):
        return [step_of(a) for a in self.calls]


def step_of(argv):
    """Map a runner argv to the step name profiles.apply reports for it."""
    if argv[0] == "bash":
        return f"hook: {argv[-1]}"
    if argv[0] == "systemctl":
        return f"systemctl {argv[-1]}"
    if argv[1] == "--device":
        return f"brightness ({argv[2]})"
    return {"profile": "profile", "tdp": "tdp", "fancurve": "fans", "undervolt": "undervolt",
            "batterylimit": "battery limit"}[argv[1]]


@pytest.fixture
def runner():
    return FakeRunner()


# ----------------------------------------------------------------------
# fan_watchdog.py / sensor_reader.py
# ----------------------------------------------------------------------

class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


@pytest.fixture
def clock():
    return FakeClock()


def make_hwmon(root, index, name, files=None):
    """Create root/hwmon<index>/ with a `name` file and the given node files."""
    d = Path(root) / f"hwmon{index}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "name").write_text(name + "\n")
    for fname, value in (files or {}).items():
        (d / fname).write_text(f"{value}\n")
    return d


def curve_files(points, enable=(1, 1)):
    """sysfs files of an asus_custom_fan_curve node for `points` [(temp, pwm)]."""
    files = {"pwm1_enable": enable[0], "pwm2_enable": enable[1]}
    for fan in (1, 2):
        for i, (t, p) in enumerate(points, start=1):
            files[f"pwm{fan}_auto_point{i}_temp"] = t
            files[f"pwm{fan}_auto_point{i}_pwm"] = p
    return files
