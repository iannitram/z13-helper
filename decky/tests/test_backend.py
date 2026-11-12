"""Unit tests for the Decky backend's Phase 11 helpers (no Decky, no z13ctl, no hardware).

Run from the repo root:  python3 -m unittest discover -s decky/tests -v
"""

import asyncio
import importlib.util
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(os.path.dirname(HERE), "main.py")
# Installed, main.py imports the shared modules from its py_modules/ (copies of src/).
SRC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "src")
# The fake host is shared with the pytest suite (tests/fake_host.py).
TESTS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "tests")
for _p in (SRC, TESTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fake_host import FakeHost

import fan_watchdog
import host
import power_range
import profiles
import state_authority
import sensor_reader

_SETTINGS = tempfile.mkdtemp(prefix="z13-helper-settings-")
# main.py does `import decky` at import time; give it a stand-in.
sys.modules.setdefault("decky", types.SimpleNamespace(
    logger=logging.getLogger("z13-helper-test"),
    DECKY_PLUGIN_SETTINGS_DIR=_SETTINGS,
))

_spec = importlib.util.spec_from_file_location("z13_decky_main", MAIN)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

# Never touch the real deck user's files: every path the backend reads or writes
# under ~/.config, ~/.local/state or /run/user goes to a scratch folder.
_HOME = tempfile.mkdtemp(prefix="z13-decky-home-")
REAL = {}   # the real values, for the tests that check the paths themselves
for _name in ("SHARED_CONFIG", "MAX_FANS_STATE", "PROFILES_STORE", "FAN_TRACE",
              "TIMELINE_PATH", "OS_RELEASE_PATH",
              "LIGHTBAR_STAMP", "LIGHTBAR_DAEMON_STAMP", "DESKTOP_APP_SOCKET",
              "DESKTOP_APP_USER_QUIT", "INSTALL_JSON"):
    if hasattr(m, _name):
        REAL[_name] = getattr(m, _name)
        setattr(m, _name, os.path.join(_HOME, _name.lower()))
REAL["FAN_TRACE_DIR"] = m.FAN_TRACE_DIR   # read only; never written by the tests


_REAL_WRITES_ALLOWED = m._writes_allowed


def setUpModule():
    # These tests run on any machine; the unsupported-machine gate has its own tests.
    gate = mock.patch.object(m, "_writes_allowed", return_value=True)
    gate.start()
    unittest.addModuleCleanup(gate.stop)
    # Backend writes target a fixed owner, independent of the runner's account.
    owner = types.SimpleNamespace(pw_uid=m.DECK_UID, pw_gid=m.DECK_GID,
                                  pw_dir=os.path.join(_HOME, "owner-home"))
    patcher = mock.patch("pwd.getpwuid", return_value=owner)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


def _store(active="Balanced"):
    """A store shaped like src/profiles.py's default_profiles()."""
    def prof(name, base, **kw):
        p = {"name": name, "builtin": True, "base": base, "tdp": None, "fan_curve": None,
             "undervolt": None, "brightness": None, "battery_limit": None,
             "hooks": {"enter": "", "exit": ""}}
        p.update(kw)
        return p
    return {"version": 1, "active": active, "profiles": [
        prof("Silent", "quiet", brightness="low", battery_limit=80),
        prof("Balanced", "balanced", brightness="medium", battery_limit=80,
             fan_curve=[[40, 20], [50, 35], [60, 55], [70, 75],
                        [75, 90], [80, 100], [85, 100], [90, 100]]),
        prof("Boondock", "quiet", tdp={"pl1": 20, "pl2": 30, "pl3": 40, "force": False},
             hooks={"enter": "boondock on", "exit": "boondock off"}),
    ]}


def _formatted_profile_timeout():
    stream = io.StringIO()
    with redirect_stdout(stream):
        profiles._print_results([
            ("fans", False, "z13ctl write timed out after 20 s (kernel asus-wmi hang?)")
        ])
    return stream.getvalue()


FAIL_OUT = "write /dev/hidraw5: connection timed out"


def fake_host(test, fail_if=(), out=FAIL_OUT):
    """A FakeHost installed as host.run for the rest of `test`; calls whose argv
    contains a fail_if substring answer (1, out)."""
    for attr, value in (("STORE_PATH", os.path.join(_HOME, "engine-profiles.json")),
                        ("MAX_FANS_PATH", os.path.join(_HOME, "engine-max-fans.json")),
                        ("LEGACY_CURRENT_PATH", os.path.join(_HOME, "engine-legacy"))):
        p = mock.patch.object(profiles, attr, value)
        p.start()
        test.addCleanup(p.stop)
    p = mock.patch.object(state_authority, 'named_client', return_value=None)
    p.start()
    test.addCleanup(p.stop)
    fh = FakeHost()
    for sub in fail_if:
        fh.fail(sub, out=out)
    patcher = mock.patch.object(host, "run", fh.run)
    patcher.start()
    test.addCleanup(patcher.stop)
    return fh


class TdpRangesTest(unittest.TestCase):
    def test_clamp_per_rail_with_armoury_ranges(self):
        self.assertEqual(m.clamp_tdp(20, 30, 40, power_range.ARMOURY_RANGES),
                         {"pl1": 28, "pl2": 32, "pl3": 45, "force": False})

    def test_tdp_ranges_runs_no_subprocess(self):
        fh = fake_host(self)
        self.assertEqual(m.tdp_ranges(), power_range.detect_ranges())
        self.assertEqual(fh.calls, [])


class ClampTdpTest(unittest.TestCase):
    def test_in_range_unchanged(self):
        self.assertEqual(m.clamp_tdp(20, 30, 40),
                         {"pl1": 20, "pl2": 30, "pl3": 40, "force": False})

    def test_bounds(self):
        self.assertEqual(m.clamp_tdp(1, 2, 3), {"pl1": 5, "pl2": 5, "pl3": 5, "force": False})
        self.assertEqual(m.clamp_tdp(200, 200, 200)["pl3"], 93)

    def test_spl_pushes_sppt_and_fppt_up(self):
        self.assertEqual(m.clamp_tdp(60, 40, 30),
                         {"pl1": 60, "pl2": 60, "pl3": 60, "force": False})

    def test_fppt_never_below_sppt(self):
        t = m.clamp_tdp(20, 50, 30)
        self.assertEqual((t["pl1"], t["pl2"], t["pl3"]), (20, 50, 50))

    def test_force_only_above_75(self):
        self.assertFalse(m.clamp_tdp(75, 80, 90)["force"])
        self.assertTrue(m.clamp_tdp(76, 80, 90)["force"])

    def test_floats_and_strings_round(self):
        t = m.clamp_tdp(20.4, "30", 39.6)
        self.assertEqual((t["pl1"], t["pl2"], t["pl3"]), (20, 30, 40))
        self.assertTrue(all(isinstance(t[k], int) for k in ("pl1", "pl2", "pl3")))

    def test_tdp_argv(self):
        self.assertEqual(m.tdp_argv(m.clamp_tdp(20, 30, 40))[1:],
                         ["tdp", "--set", "20", "--pl2", "30", "--pl3", "40"])
        self.assertEqual(m.tdp_argv(m.clamp_tdp(80, 85, 90))[-1], "--force")


class ClampBatteryTest(unittest.TestCase):
    def test_steps_and_bounds(self):
        self.assertEqual(m.clamp_battery(80), 80)
        self.assertEqual(m.clamp_battery(82), 80)
        self.assertEqual(m.clamp_battery(83), 85)
        self.assertEqual(m.clamp_battery(10), 40)
        self.assertEqual(m.clamp_battery(120), 100)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="z13-store-")
        self.path = os.path.join(self.dir, "profiles.json")
        self._orig = m.PROFILES_STORE
        m.PROFILES_STORE = self.path

    def tearDown(self):
        m.PROFILES_STORE = self._orig

    def _write(self, store):
        with open(self.path, "w") as f:
            json.dump(store, f)

    def _read(self):
        with open(self.path) as f:
            return json.load(f)









    def test_set_app_profile_preserves_formatted_profile_timeout(self):
        run = fake_host(self)
        output = _formatted_profile_timeout()
        run.fail("z13-profile", rc=1, out=output)
        result = asyncio.run(m.Plugin().set_app_profile("Boondock"))
        self.assertEqual(result["message"],
                         "z13ctl write timed out after 20 s (kernel asus-wmi hang?) (step: fans)")

    def test_set_profile_rejects_names_that_are_not_bases(self):
        run = fake_host(self)
        result = asyncio.run(m.Plugin().set_profile("custom"))
        self.assertFalse(result["ok"])
        self.assertIn("unknown profile", result["message"])
        self.assertEqual(run.calls, [])

    def test_set_profile_routes_through_the_profile_engine(self):
        run = fake_host(self)
        result = asyncio.run(m.Plugin().set_profile("performance"))
        self.assertTrue(result["ok"])
        self.assertEqual(run.argvs, [m.Z13_PROFILE + ["apply", "Turbo"]])
        self.assertTrue(run.calls[0].as_deck)

    def test_profile_timeout_without_step_keeps_bare_message(self):
        output = "z13ctl write timed out after 20 s (kernel asus-wmi hang?)\n"
        self.assertEqual(m._extract_z13ctl_timeout(output),
                         "z13ctl write timed out after 20 s (kernel asus-wmi hang?)")











class FileLockSafetyTest(unittest.TestCase):
    """The backend runs as root in a deck-writable dir: never follow a planted link."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="z13-lock-")
        self.store = os.path.join(self.dir, "profiles.json")
        self.victim = os.path.join(self.dir, "victim")
        with open(self.victim, "w") as f:
            f.write("x")
        os.chmod(self.victim, 0o600)

    def _untouched(self):
        self.assertEqual(os.stat(self.victim).st_mode & 0o777, 0o600)

    def test_symlinked_lock_is_refused(self):
        os.symlink(self.victim, self.store + ".lock")
        with self.assertRaises(m.StoreLockError), m._file_lock(self.store):
            pass
        self._untouched()

    def test_hardlinked_lock_is_refused(self):
        os.link(self.victim, self.store + ".lock")
        with self.assertRaises(m.StoreLockError), m._file_lock(self.store):
            pass
        self._untouched()

    def test_held_lock_times_out(self):
        import threading
        held, release = threading.Event(), threading.Event()

        def hold():
            with m._file_lock(self.store):
                held.set()
                release.wait(5)

        t = threading.Thread(target=hold)
        t.start()
        self.assertTrue(held.wait(5))
        with mock.patch.object(m, "LOCK_TIMEOUT", 0.2):
            try:
                with self.assertRaisesRegex(m.StoreLockError, "timed out"), m._file_lock(self.store):
                    pass
            finally:
                release.set()
                t.join(5)

    def test_lock_file_owned_by_current_user_is_accepted(self):
        real_fstat = os.fstat
        for uid in (0, 1000, 54321):
            with self.subTest(uid=uid):
                store = self.store + f".{uid}"
                expected_owner = m.DECK_UID if uid == 0 else uid

                def getpwuid(account_uid):
                    if account_uid != m.DECK_UID:
                        raise KeyError(account_uid)  # uid 54321 has no passwd entry
                    return types.SimpleNamespace(pw_dir=os.path.join(self.dir, "owner-home"))

                def fstat(fd, owner=expected_owner):
                    fields = list(real_fstat(fd))
                    fields[4] = owner
                    return os.stat_result(fields)

                with mock.patch.object(os, "getuid", return_value=uid), \
                        mock.patch.object(os, "geteuid", return_value=uid), \
                        mock.patch("pwd.getpwuid", side_effect=getpwuid), \
                        mock.patch.object(os, "fstat", side_effect=fstat), \
                        mock.patch.object(os, "fchown") as chown:
                    with m._file_lock(store):
                        pass
                    self.assertTrue(os.path.isfile(store + ".lock"))
                    self.assertEqual([call.args[1:] for call in chown.call_args_list],
                                     [(m.DECK_UID, m.DECK_GID)] if uid == 0 else [])
                    with m._file_lock(store):  # existing lock checks the owner
                        pass

    def test_normal_use_creates_plain_lock_file(self):
        with m._file_lock(self.store):
            pass
        self.assertTrue(os.path.isfile(self.store + ".lock"))








class TelemetryTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="z13-hwmon-")
        # Deliberately shuffled indices: lookup must go by `name`.
        nodes = {"hwmon0": ("amdgpu", {"temp1_input": "48000"}),
                 "hwmon3": ("acpitz", {"temp1_input": "52500"}),
                 "hwmon7": ("asus", {"fan1_input": "2100", "fan2_input": "2250"}),
                 "hwmon9": ("k10temp", {"temp1_input": "99000"})}
        for d, (name, files) in nodes.items():
            os.makedirs(os.path.join(self.root, d))
            if name == "amdgpu":
                device = os.path.join(self.root, d, "device")
                os.makedirs(device)
                for fn, value in (("vendor", "0x1002"), ("device", "0x1586")):
                    with open(os.path.join(device, fn), "w") as f:
                        f.write(value + "\n")
            with open(os.path.join(self.root, d, "name"), "w") as f:
                f.write(name + "\n")
            for fn, v in files.items():
                with open(os.path.join(self.root, d, fn), "w") as f:
                    f.write(v + "\n")
        self._orig = sensor_reader.HWMON_ROOT, m._sensors
        sensor_reader.HWMON_ROOT = self.root
        m._sensors = sensor_reader.SensorReader()

    def tearDown(self):
        sensor_reader.HWMON_ROOT, m._sensors = self._orig

    def test_by_name(self):
        self.assertEqual(m.telemetry(), {"apu": 52.5, "apu_src": "ACPI", "gpu": 48.0, "gpu_src": "amdgpu edge",
                                         "fan1": 2100, "fan2": 2250})

    def test_missing_nodes_are_none(self):
        sensor_reader.HWMON_ROOT = tempfile.mkdtemp(prefix="z13-empty-")
        m._sensors = sensor_reader.SensorReader()
        self.assertEqual(m.telemetry(), {"apu": None, "apu_src": None, "gpu": None, "gpu_src": None,
                                         "fan1": None, "fan2": None})


class StalledSensors:
    """Curve at 100%, both fans at 0 RPM: a stall on every tick."""

    def __init__(self):
        self.curve = [[t, 255] for t in (20, 30, 40, 50, 60, 70, 80, 90)]

    def custom_fan_curve_enabled(self):
        return True

    def custom_fan_curve_points(self):
        return [list(p) for p in self.curve]

    def expected_fan_pwm(self, temp_c):
        return 255

    def get_apu_temp(self):
        return 60.0

    def get_fan_speeds(self):
        return 0, 0


class DeckyFansTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(m, "tdp_ranges", return_value=dict(power_range.DEFAULT_RANGES))
        patch.start()   # not this machine's firmware floors
        self.addCleanup(patch.stop)
        self._orig = m._current_platform_profile
        m._current_platform_profile = lambda: "performance"

    def tearDown(self):
        m._current_platform_profile = self._orig

    def test_commands(self):
        run = fake_host(self)
        fans = m.DeckyFans()
        fans.rewrite_profile()
        fans.set_fans_max()
        fans.set_fancurve_pwm([(40, 51), (50, 102.0)])
        self.assertEqual([c[0][1:] for c in run.calls], [
            ["profile", "--set", "performance"],
            ["fancurve", "--set", m.MAX_FANS_SPEC],
            ["fancurve", "--set", "40:51,50:102"],
        ])
        self.assertTrue(all(c[0][0] == m.Z13CTL for c in run.calls))

    def test_auto_recovery_commands(self):
        run = fake_host(self)
        fans = m.DeckyFans()
        fans.apply_tdp({"pl1": 20, "pl2": 30, "pl3": 40, "force": False})
        fans.reset_fancurve()
        self.assertEqual([c[0][1:] for c in run.calls], [
            ["tdp", "--set", "20", "--pl2", "30", "--pl3", "40"],
            ["fancurve", "--reset"],
        ])
        self.assertEqual([c.as_deck for c in run.calls], [True, False])

    def test_restore_and_apply_tdp_write_the_clamped_values(self):
        run = fake_host(self)
        ranges = {"pl1": (28, 80), "pl2": (32, 92), "pl3": (45, 93)}
        store = {"active": "Boondock", "profiles": [{"name": "Boondock", "tdp":
                 {"pl1": 20, "pl2": 30, "pl3": 40, "force": False}}]}
        with mock.patch.object(m, "tdp_ranges", return_value=ranges), \
                mock.patch.object(m, "_read_store", return_value=store):
            fans = m.DeckyFans()
            self.assertIsNone(fans.restore_tdp())
            fans.apply_tdp({"pl1": 20, "pl2": 30, "pl3": 40, "force": False})
            written, note = fans.fit_tdp({"pl1": 20, "pl2": 30, "pl3": 40})
        self.assertEqual([c[0][1:] for c in run.calls],
                         [["tdp", "--set", "28", "--pl2", "32", "--pl3", "45"]] * 2)
        self.assertEqual(written["pl1"], 28)
        self.assertIn("outside the firmware range", note)

    def test_watchdog_reads_active_tdp_from_store(self):
        orig = m._read_store
        tdp = {"pl1": 20, "pl2": 30, "pl3": 40, "force": False}
        m._read_store = lambda: {"active": "Boondock", "profiles": [{"name": "Boondock", "tdp": tdp}]}
        try:
            self.assertEqual(m.make_fan_watchdog(StalledSensors(), m.DeckyFans())._active_tdp(), tdp)
            m._read_store = lambda: None
            self.assertIsNone(m.make_fan_watchdog(StalledSensors(), m.DeckyFans())._active_tdp())
        finally:
            m._read_store = orig

    def test_failure_raises(self):
        fans = (fake_host(self, fail_if=("profile",)) and m.DeckyFans())
        with self.assertRaises(RuntimeError):
            fans.rewrite_profile()

    def test_failed_rewrite_means_no_bare_curve_write(self):
        run = fake_host(self, fail_if=("profile",))
        wd = m.make_fan_watchdog(StalledSensors(), m.DeckyFans(), may_act=lambda: True)
        msgs = [wd.tick() for _ in range(fan_watchdog.STALL_TICKS)]
        self.assertTrue(msgs[-1].startswith("Fan recovery failed:"))
        self.assertEqual([c[0][1] for c in run.calls], ["profile"])


class RecoveryRestoresTuningTest(unittest.TestCase):
    """The firmware profile write resets undervolt and power limits: recovery puts
    the active profile's back (TDP, curve, undervolt) and reads them back."""

    def setUp(self):
        patch = mock.patch.object(m, "tdp_ranges", return_value=dict(power_range.DEFAULT_RANGES))
        patch.start()   # not this machine's firmware floors
        self.addCleanup(patch.stop)
        self._orig = m._current_platform_profile, m.PROFILES_STORE
        m._current_platform_profile = lambda: "quiet"
        d = tempfile.mkdtemp(prefix="z13-decky-store-")
        m.PROFILES_STORE = os.path.join(d, "profiles.json")
        store = _store("Boondock")
        store["profiles"][2]["undervolt"] = -12
        with open(m.PROFILES_STORE, "w") as f:
            json.dump(store, f)

    def tearDown(self):
        m._current_platform_profile, m.PROFILES_STORE = self._orig

    def test_recovery_ends_with_tdp_and_undervolt_after_profile_write(self):
        run = fake_host(self)
        wd = m.make_fan_watchdog(StalledSensors(), m.DeckyFans(), may_act=lambda: True)
        msgs = [wd.tick() for _ in range(fan_watchdog.STALL_TICKS)]
        self.assertEqual(msgs[-1], "Fans were stalled at 0 RPM; profile and curve re-applied")
        sets = [" ".join(c[0][1:]) for c in run.calls if "--set" in c[0]]
        self.assertEqual(sets[0], "profile --set quiet")
        self.assertEqual(sets[1], "tdp --set 20 --pl2 30 --pl3 40")
        self.assertTrue(sets[2].startswith("fancurve --set"))
        self.assertEqual(sets[3], "undervolt --set -12")
        self.assertEqual(" ".join(run.calls[-1][0][1:]), "undervolt --get")
        self.assertNotIn("tdp --get", [" ".join(c[0][1:]) for c in run.calls])

    def test_failed_undervolt_is_reported_and_curve_still_written(self):
        run = fake_host(self, fail_if=("undervolt",), out="ryzen_smu not loaded")
        wd = m.make_fan_watchdog(StalledSensors(), m.DeckyFans(), may_act=lambda: True)
        msgs = [wd.tick() for _ in range(fan_watchdog.STALL_TICKS)]
        self.assertIn("undervolt not restored", msgs[-1])
        self.assertTrue(any(c[0][1:3] == ["fancurve", "--set"] for c in run.calls))


class FanWatchdogLoopTest(unittest.TestCase):
    def setUp(self):
        m._write_lock = None
        self._orig = m._current_platform_profile, m._load_max_fans
        m._current_platform_profile = lambda: "balanced"
        m._load_max_fans = lambda: {"enabled": False}

    def tearDown(self):
        m._current_platform_profile, m._load_max_fans = self._orig
        m._write_lock = None

    def test_stall_recovers_through_z13ctl_in_order(self):
        run = fake_host(self)
        wd = m.make_fan_watchdog(StalledSensors(), m.DeckyFans(), may_act=lambda: True)
        msgs = [asyncio.run(m.fan_watchdog_tick(wd)) for _ in range(fan_watchdog.STALL_TICKS)]
        self.assertEqual(msgs[-1], "Fans were stalled at 0 RPM; profile and curve re-applied")
        self.assertEqual([c[0][1:3] for c in run.calls],
                         [["profile", "--set"], ["fancurve", "--set"]])

    def test_max_fans_flag_comes_from_shared_state(self):
        m._load_max_fans = lambda: {"enabled": True}
        run = fake_host(self)
        wd = m.make_fan_watchdog(StalledSensors(), m.DeckyFans(), may_act=lambda: True)
        for _ in range(fan_watchdog.STALL_TICKS):
            asyncio.run(m.fan_watchdog_tick(wd))
        self.assertEqual(run.calls[-1][0][1:], ["fancurve", "--set", m.MAX_FANS_SPEC])

    def test_outside_game_mode_nothing_is_written(self):
        run = fake_host(self)
        wd = m.make_fan_watchdog(StalledSensors(), m.DeckyFans(), may_act=lambda: False)
        for _ in range(10):
            asyncio.run(m.fan_watchdog_tick(wd))
        self.assertEqual(run.calls, [])

    def test_default_gate_is_game_mode(self):
        wd = m.make_fan_watchdog(StalledSensors(), (fake_host(self) and m.DeckyFans()))
        self.assertIs(wd._may_act, m._in_gamemode)

    def test_tick_skipped_while_a_write_holds_the_lock(self):
        ticks = []
        wd = types.SimpleNamespace(tick=lambda: ticks.append(1))

        async def scenario():
            async with m._lock():
                self.assertIsNone(await m.fan_watchdog_tick(wd))
            await m.fan_watchdog_tick(wd)
        asyncio.run(scenario())
        self.assertEqual(ticks, [1])

    def test_tick_holds_the_lock(self):
        seen = []
        wd = types.SimpleNamespace(tick=lambda: seen.append(m._lock().locked()))
        asyncio.run(m.fan_watchdog_tick(wd))
        self.assertEqual(seen, [True])

    def test_trace_goes_to_the_state_folder_owned_by_deck(self):
        d = tempfile.mkdtemp(prefix="z13-trace-")
        path = os.path.join(d, "new", "fan-trace-gamemode.log")
        owner = (os.getuid(), os.getgid())
        # A container may run as a numeric uid with no passwd entry. Supply a
        # deterministic passwd record because the tested path is outside HOME.
        with mock.patch("pwd.getpwuid", return_value=types.SimpleNamespace(
                pw_dir="/nonexistent-test-home")):
            trace = m.make_fan_trace(path, owner=owner)
        self.assertIsNotNone(trace)
        wd = m.make_fan_watchdog(StalledSensors(), (fake_host(self) and m.DeckyFans()),
                                 may_act=lambda: True, trace=trace)
        for _ in range(fan_watchdog.STALL_TICKS):
            asyncio.run(m.fan_watchdog_tick(wd))
        for h in trace.log.handlers:
            h.flush()
        with open(path) as f:
            text = f.read()
        self.assertIn("fan trace started (gamemode", text)
        self.assertIn("*** ACT stall", text)
        self.assertTrue(REAL["FAN_TRACE"].endswith("z13-helper/fan-trace-gamemode.log"))

    def _logging_choice(self, rec):
        if rec is None:
            if os.path.exists(m.INSTALL_JSON):
                os.remove(m.INSTALL_JSON)
        else:
            with open(m.INSTALL_JSON, "w") as f:
                json.dump(rec, f)
        self.addCleanup(lambda: os.path.exists(m.INSTALL_JSON) and os.remove(m.INSTALL_JSON))

    def test_logging_choice_on_off_missing(self):
        for rec, want in ((None, True), ({"mode": "repair"}, True), ({"logging": True}, True),
                          ({"logging": False}, False)):
            with self.subTest(rec=rec):
                self._logging_choice(rec)
                self.assertIs(m.logging_enabled(), want)
                d = tempfile.mkdtemp(prefix="z13-trace-")
                path = os.path.join(d, "fan-trace-gamemode.log")
                with mock.patch("pwd.getpwuid", return_value=types.SimpleNamespace(
                        pw_dir="/nonexistent-test-home")):
                    trace = m.make_fan_trace(path, owner=(os.getuid(), os.getgid()))
                self.assertEqual(trace is not None, want)
                self.assertEqual(os.path.exists(path), want)
        self.assertTrue(REAL["INSTALL_JSON"].endswith("/.config/z13-helper/install.json"))

    def test_unreadable_logging_choice_means_on(self):
        self._logging_choice(None)
        with open(m.INSTALL_JSON, "w") as f:
            f.write("{not json")
        self.assertTrue(m.logging_enabled())

    def test_unwritable_trace_is_none_not_an_error(self):
        self.assertIsNone(m.make_fan_trace("/proc/nope/fan-trace.log"))

    def test_tick_error_is_logged_not_raised(self):
        def boom():
            raise OSError("sysfs gone")
        self.assertIsNone(asyncio.run(m.fan_watchdog_tick(types.SimpleNamespace(tick=boom))))
        self.assertFalse(m._lock().locked())


class DesktopAppCheckTest(unittest.TestCase):
    def make(self, desktop=True, up=False, user_quit=False, restart_ok=True):
        self.state = {"desktop": desktop, "up": up, "user_quit": user_quit}
        self.restarts = []

        def restart():
            self.restarts.append(1)
            return restart_ok
        return m.DesktopAppCheck(in_desktop=lambda: self.state["desktop"],
                                 app_up=lambda: self.state["up"],
                                 user_quit=lambda: self.state["user_quit"], restart=restart)

    def test_running_app_is_left_alone(self):
        c = self.make(up=True)
        self.assertEqual([c.check() for _ in range(5)], ["ok"] * 5)
        self.assertEqual(self.restarts, [])

    def test_missing_app_restarted_after_two_misses(self):
        c = self.make()
        self.assertEqual(c.check(), "miss")
        self.assertEqual(c.check(), "restarted")
        self.assertEqual(self.restarts, [1])

    def test_not_restarted_in_a_loop(self):
        c = self.make()
        out = [c.check() for _ in range(6)]
        self.assertEqual(out, ["miss", "restarted", "gave-up", "gave-up", "gave-up", "gave-up"])
        self.state["up"] = True
        self.assertEqual(c.check(), "ok")                  # came back: armed again
        self.state["up"] = False
        self.assertEqual([c.check(), c.check()], ["miss", "restarted"])

    def test_user_quit_is_respected(self):
        c = self.make(user_quit=True)
        self.assertEqual({c.check() for _ in range(5)}, {"user-quit"})
        self.assertEqual(self.restarts, [])

    def test_game_mode_does_nothing(self):
        c = self.make(desktop=False)
        self.assertEqual({c.check() for _ in range(5)}, {"away"})
        self.assertEqual(self.restarts, [])

    def test_restart_failure_is_reported(self):
        c = self.make(restart_ok=False)
        self.assertEqual([c.check(), c.check()], ["miss", "restart-failed"])

    def test_app_answers_over_its_socket(self):
        import socket
        d = tempfile.mkdtemp(prefix="z13-sock-")
        path = os.path.join(d, "z13-helper.sock")
        self.assertFalse(m._desktop_app_answers(path))
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(1)
        try:
            self.assertTrue(m._desktop_app_answers(path))
        finally:
            srv.close()

    def test_profile_engine_runs_as_deck(self):
        self.assertEqual(m.Z13_PROFILE, ["/home/deck/.local/bin/z13-profile"])
        run = fake_host(self)
        m._run(m.Z13_PROFILE + ["list", "--json"], as_deck=True)
        self.assertEqual(run.calls[0].argv, m.Z13_PROFILE + ["list", "--json"])
        self.assertTrue(run.calls[0].as_deck)

    def test_session_checks_ask_deck_systemd(self):
        run = fake_host(self, fail_if=("plasma-workspace",), out="")
        self.assertTrue(m._in_gamemode())
        self.assertFalse(m._desktop_session_active())
        self.assertEqual([c.argv[-1] for c in run.calls],
                         ["gamescope-session.target", "plasma-workspace.target"])
        self.assertTrue(all(c.as_deck and c.argv[:2] == ["systemctl", "--user"] for c in run.calls))

    def test_paths_match_the_app(self):
        self.assertTrue(REAL["DESKTOP_APP_SOCKET"].endswith("/app/io.github.iannitram.Z13Helper/z13-helper.sock"))
        self.assertTrue(REAL["DESKTOP_APP_USER_QUIT"].endswith("z13-helper.user-quit"))
        self.assertEqual(REAL["TIMELINE_PATH"],
                         os.path.join(REAL["FAN_TRACE_DIR"], "timeline.jsonl"))


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="z13-decky-tl-")
        self.tl = os.path.join(self.td, "timeline.jsonl")
        self.install_json = os.path.join(self.td, "install.json")
        self.os_rel = os.path.join(self.td, "os-release")
        self.max_fans = os.path.join(self.td, "max-fans.json")
        self.store_path = os.path.join(self.td, "profiles.json")
        with open(self.store_path, "w") as f:
            json.dump(_store("Balanced"), f)
        for name, val in (
            ("TIMELINE_PATH", self.tl),
            ("INSTALL_JSON", self.install_json),
            ("OS_RELEASE_PATH", self.os_rel),
            ("MAX_FANS_STATE", self.max_fans),
            ("PROFILES_STORE", self.store_path),
        ):
            p = mock.patch.object(m, name, val)
            p.start()
            self.addCleanup(p.stop)

    def test_get_timeline_reads_newest_first_across_rotation_and_partial_tail(self):
        import timeline
        for idx in range(6):
            timeline.append(
                {"id": f"d-{idx}", "kind": "profile", "summary": f"p{idx}"},
                path=self.tl,
                max_bytes=350,
                backups=2,
            )
        with open(self.tl, "ab") as f:
            f.write(b'{"id":"unfinished","kind":"profile"')

        got = asyncio.run(m.Plugin().get_timeline(limit=3))
        self.assertEqual([e["id"] for e in got], ["d-5", "d-4", "d-3"])

    def test_set_max_fans_records_goal_change(self):
        fake_host(self)

        def fake_authority_set_max_fans(enabled):
            # What state_authority.set_max_fans does last: persist the intent.
            with open(self.max_fans, "w") as f:
                json.dump({"enabled": bool(enabled)}, f)
            return "Max Fans ON"

        with mock.patch.object(state_authority, "set_max_fans", fake_authority_set_max_fans):
            res = asyncio.run(m.Plugin().set_max_fans(True))
        self.assertTrue(res["ok"])
        import timeline
        self.assertTrue(timeline.flush(10))   # the entry is written by the background writer
        got = asyncio.run(m.Plugin().get_timeline(limit=10))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "goal")
        self.assertEqual(got[0]["after"]["goal"], "max")
        self.assertEqual(got[0]["rollback"]["goal"], "custom")

    def test_check_steamos_build_id_records_once_and_logs(self):
        fh = fake_host(self)
        with open(self.install_json, "w") as f:
            json.dump({"steamos": "3.8.28 (20260922.1)", "kernel": "6.18.50"}, f)
        with open(self.os_rel, "w") as f:
            f.write('VERSION_ID="3.9.5"\nBUILD_ID="20261023.100"\n')

        first = m._check_steamos_build_id()
        second = m._check_steamos_build_id()
        self.assertIsNotNone(first)
        self.assertEqual(first["id"], second["id"])

        got = asyncio.run(m.Plugin().get_timeline(limit=10))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "steamos_update")
        self.assertEqual(len([c for c in fh.calls if c.argv[0] == "logger"]), 1)


def _fake_lib(test, complete):
    """Point host.DECK_HOME at a temp dir whose installed library has (complete) or lacks __main__.py."""
    import tempfile
    d = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, d, True)
    pkg = os.path.join(d, ".local", "lib", "z13-helper", "post_update")
    os.makedirs(pkg)
    open(os.path.join(pkg, "__init__.py"), "w").close()
    if complete:
        open(os.path.join(pkg, "__main__.py"), "w").close()
    p = mock.patch.object(m.host, "DECK_HOME", d)
    p.start()
    test.addCleanup(p.stop)
    return os.path.join(d, ".local", "lib", "z13-helper")


class PostUpdateTest(unittest.TestCase):
    def test_argv_is_a_list_with_the_repo_on_pythonpath(self):
        lib = _fake_lib(self, True)
        self.assertEqual(m.post_update_argv("/r", True),
                         ["env", f"PYTHONPATH={lib}:/r/tooling:/r/src",
                          "python3", "-m", "post_update", "--tab-json", "--ack"])
        with self.assertRaises(ValueError):
            m.post_update_argv("rel")

    def test_partial_installed_library_is_left_out(self):
        _fake_lib(self, False)
        self.assertEqual(m.post_update_argv("/r")[:2], ["env", "PYTHONPATH=/r/tooling:/r/src"])
        self.assertEqual(m.post_repair_argv("/r")[1], "PYTHONPATH=/r/tooling:/r/src")

    def test_parse_skips_noise_and_rejects_junk(self):
        self.assertEqual(m.parse_post_update('log\n{"rows": []}'), {"rows": []})
        self.assertIsNone(m.parse_post_update("nope"))
        self.assertIsNone(m.parse_post_update('{"rows": 1}'))

    def _call(self, repo, result):
        calls = []

        def run(argv, timeout=30, *, as_deck=False, input=None):
            calls.append((argv, as_deck))
            return result

        with mock.patch.object(m, "_repo_path", return_value=repo), \
                mock.patch.object(host, "run", run):
            return asyncio.run(m.Plugin().post_update_status(True)), calls

    def test_status_runs_as_deck_and_returns_the_dict(self):
        out, calls = self._call("/r", host.Result(0, '{"rows": [], "updated": true}', False))
        self.assertTrue(out["ok"])
        self.assertTrue(out["status"]["updated"])
        self.assertEqual(calls[0][0][-2:], ["--tab-json", "--ack"])
        self.assertTrue(calls[0][1])

    def test_status_without_repo_or_with_bad_output_is_not_ok(self):
        out, calls = self._call(None, host.Result(0, "{}", False))
        self.assertFalse(out["ok"])
        self.assertEqual(calls, [])
        out, _ = self._call("/r", host.Result(1, "boom", False))
        self.assertFalse(out["ok"])
        self.assertIsNone(out["status"])


class PostUpdateRepairTest(unittest.TestCase):
    def test_argv_plan_and_run(self):
        lib = _fake_lib(self, True)
        base = ["env", f"PYTHONPATH={lib}:/r/tooling:/r/src", "python3", "-m", "post_update", "repair", "--json"]
        self.assertEqual(m.post_repair_argv("/r"), base)
        self.assertEqual(m.post_repair_argv("/r", ["kernel_args", "permission:/etc/x"]),
                         base + ["--run", "kernel_args,permission:/etc/x"])
        for bad in ([], ["a b"], ["a;b"], [""], "kernel_args"):
            with self.assertRaises(ValueError):
                m.post_repair_argv("/r", bad)
        with self.assertRaises(ValueError):
            m.post_repair_argv("rel")

    def test_parse_needs_the_key(self):
        self.assertEqual(m.parse_json_key('log\n{"steps": []}', "steps"), {"steps": []})
        self.assertIsNone(m.parse_json_key('{"rows": []}', "steps"))
        self.assertIsNone(m.parse_json_key("nope", "steps"))

    def _call(self, method, repo, result, *args):
        calls = []

        def run(argv, timeout=30, *, as_deck=False, input=None):
            calls.append((argv, as_deck, timeout))
            return result

        with mock.patch.object(m, "_repo_path", return_value=repo), mock.patch.object(host, "run", run):
            return asyncio.run(getattr(m.Plugin(), method)(*args)), calls

    def test_plan_changes_nothing_and_runs_as_deck(self):
        out, calls = self._call("post_update_repair_plan", "/r",
                                host.Result(0, '{"steps": [{"id": "kernel_args"}], "unplanned": []}', False))
        self.assertTrue(out["ok"])
        self.assertEqual(out["plan"]["steps"][0]["id"], "kernel_args")
        argv, as_deck, _ = calls[0]
        self.assertTrue(as_deck)
        self.assertNotIn("--run", argv)            # the plan call never runs a repair

    def test_run_passes_only_the_confirmed_ids_through_the_driver(self):
        out, calls = self._call("post_update_repair_run", "/r",
                                host.Result(1, '{"results": [["kernel_args", "rolled back"]]}', False),
                                ["kernel_args"])
        self.assertTrue(out["ok"])                  # rc 1 still carries the per-step result
        self.assertEqual(out["result"]["results"], [["kernel_args", "rolled back"]])
        argv, as_deck, _ = calls[0]
        self.assertEqual(argv[-2:], ["--run", "kernel_args"])
        self.assertTrue(as_deck)

    def test_run_refuses_bad_ids_without_calling_the_host(self):
        for ids in ([], ["a b"], None, "kernel_args"):
            out, calls = self._call("post_update_repair_run", "/r", host.Result(0, "{}", False), ids)
            self.assertFalse(out["ok"])
            self.assertEqual(calls, [])
        out, calls = self._call("post_update_repair_run", None, host.Result(0, "{}", False), ["x"])
        self.assertFalse(out["ok"])
        self.assertEqual(calls, [])

    def test_plan_without_repo_or_with_junk_is_not_ok(self):
        out, calls = self._call("post_update_repair_plan", None, host.Result(0, "{}", False))
        self.assertFalse(out["ok"])
        self.assertEqual(calls, [])
        out, _ = self._call("post_update_repair_plan", "/r", host.Result(0, "junk", False))
        self.assertFalse(out["ok"])
        self.assertIsNone(out["plan"])


if __name__ == "__main__":
    unittest.main()


class UnsupportedMachineGate(unittest.TestCase):
    """#22: on a machine that is not the Z13 the backend issues no write."""

    def test_writes_refused_without_running_anything(self):
        fake = FakeHost()
        plugin = m.Plugin()
        with mock.patch.object(m, "_writes_allowed", return_value=False), \
                mock.patch.object(host, "run", fake.run):
            results = [
                asyncio.run(plugin.set_app_profile("Balanced")),
                asyncio.run(plugin.set_profile("balanced")),
                asyncio.run(plugin.set_tdp(True, 20, 20, 20)),
                asyncio.run(plugin.set_battery_limit(80)),
                asyncio.run(plugin.set_brightness("high")),
                asyncio.run(plugin.set_max_fans(True)),
            ]
        for r in results:
            self.assertFalse(r["ok"])
            self.assertIn("Unsupported", r["message"])
        self.assertEqual(fake.calls, [])

    def test_dmi_check_reads_the_product_name(self):
        real = _REAL_WRITES_ALLOWED
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "product_name")
            with mock.patch.object(m, "DMI_PRODUCT_NAME", path):
                self.assertFalse(real())          # no file: refuse
                with open(path, "w") as f:
                    f.write("ROG Flow Z13 GZ302EA\n")
                self.assertTrue(real())
                with open(path, "w") as f:
                    f.write("Jupiter\n")
                self.assertFalse(real())
