"""Shared fixtures for the z13-helper test suite.

Nothing here touches hardware, the network, sudo, or the real ~/.config:
- src/ and tooling/ go on sys.path so the modules import by their bare names,
  exactly as main.py and the host scripts import them.
- An autouse guard makes subprocess.run / Popen and urllib's urlopen raise, so a
  test that forgets the `fake_host` fixture fails loudly instead of running z13ctl
  (host.run lets the guard's error through).
- install_record.INSTALL_JSON (the logging choice) points into tmp_path.
- `profiles_env` redirects every path profiles.py and max_fans_state.py write
  into tmp_path.
"""

import functools
import importlib.util
import logging
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


@pytest.fixture(autouse=True)
def pinned_tdp_ranges(monkeypatch):
    """Profile applies and named-profile mirrors clamp TDP into the detected
    ranges (#203). Pin those to the 5-93 W envelope so no test reads this
    machine's asus-armoury metadata; a test that needs the firmware floors
    patches profiles._tdp_ranges / z13ctl_client._tdp_ranges itself."""
    import power_range
    import profiles
    import z13ctl_client
    monkeypatch.setattr(profiles, "_tdp_ranges", lambda: dict(power_range.DEFAULT_RANGES))
    monkeypatch.setattr(z13ctl_client, "_tdp_ranges", lambda: dict(power_range.DEFAULT_RANGES))


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
    """install_record's default path (the logging choice, the last install) and
    timeline's default path go to tmp_path, never the real ~/.config or ~/.local/state."""
    import install_record
    import timeline
    monkeypatch.setattr(install_record, "INSTALL_JSON", str(tmp_path / "install.json"))
    monkeypatch.setattr(timeline, "TIMELINE_PATH", str(tmp_path / "state" / "z13-helper" / "timeline.jsonl"))

    # Production hands deferred entries to a background writer. Tests look at the
    # file right after the call, so wait for the writer; the real enqueue stays
    # reachable as `timeline.enqueue.__wrapped__` for the tests of deferral itself.
    real_enqueue = timeline.enqueue

    @functools.wraps(real_enqueue)
    def enqueue_and_wait(*args, **kwargs):
        rec = real_enqueue(*args, **kwargs)
        timeline.flush(10)
        return rec

    monkeypatch.setattr(timeline, "enqueue", enqueue_and_wait)
    yield
    if timeline._PENDING.unfinished_tasks:   # (some tests patch time; only call flush when needed)
        timeline.flush(10)


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _refuse("subprocess.run"))
    monkeypatch.setattr(subprocess, "Popen", _refuse("subprocess.Popen"))
    monkeypatch.setattr(urllib.request, "urlopen", _refuse("urllib.request.urlopen"))
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", _refuse("urllib opener.open"))


@pytest.fixture(autouse=True)
def no_live_state_daemon(tmp_path, monkeypatch):
    import state_authority
    import profiles
    import max_fans_state
    monkeypatch.setattr(state_authority, "named_client", lambda: None)
    root = tmp_path / "isolated-state"
    monkeypatch.setattr(profiles, "STORE_PATH", str(root / "profiles.json"))
    monkeypatch.setattr(profiles, "MAX_FANS_PATH", str(root / "max-fans.json"))
    monkeypatch.setattr(profiles, "LEGACY_CURRENT_PATH", str(root / "legacy-current"))
    monkeypatch.setattr(max_fans_state, "STATE_PATH", str(root / "max-fans.json"))


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
        timeline=tmp_path / "state" / "z13-helper" / "timeline.jsonl",
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


def step_of(argv):
    """Map a host.run argv to the step name profiles.apply reports for it."""
    if argv[0] == "bash":
        return f"hook: {argv[-1]}"
    if argv[0] == "systemctl":
        return f"systemctl {argv[-1]}"
    if argv[1] == "--device":
        return f"brightness ({argv[2]})"
    return {"profile": "profile", "tdp": "tdp", "fancurve": "fans", "undervolt": "undervolt",
            "batterylimit": "battery limit"}[argv[1]]


@pytest.fixture
def fake_host(monkeypatch):
    """host.run replaced by a FakeHost (tests/fake_host.py): records argv, scripted results."""
    from fake_host import FakeHost
    return FakeHost().install(monkeypatch.setattr)


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


# ----------------------------------------------------------------------
# decky/main.py
# ----------------------------------------------------------------------

@pytest.fixture
def main(monkeypatch):
    """decky/main.py imported with a stub `decky` module (the loader's is absent)."""
    stub = types.ModuleType("decky")
    stub.logger = logging.getLogger("decky.test")
    stub.DECKY_PLUGIN_SETTINGS_DIR = "/nonexistent-settings-dir"
    monkeypatch.setitem(sys.modules, "decky", stub)
    spec = importlib.util.spec_from_file_location("decky_main_under_test",
                                                  REPO / "decky" / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "logging_enabled", lambda: False)   # the user said no logs
    return mod


@pytest.fixture(autouse=True)
def _cli_supported_platform(monkeypatch):
    """The CLI/MCP write gate (#22) reads the real machine; tests act as a supported Z13.

    tests/test_cli_mcp_gate.py overrides this to prove the refusal.
    """
    import z13_helper_cli
    monkeypatch.setattr(z13_helper_cli, "_unsupported_reason", lambda: "")
    import platform_support
    platform_support.reset_version_cache()
