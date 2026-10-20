"""Unit tests for the Decky backend's Phase 11 helpers (no Decky, no z13ctl, no hardware).

Run from the repo root:  python3 -m unittest discover -s decky/tests -v
"""

import asyncio
import importlib.util
import json
import logging
import os
import sys
import tempfile
import types
import unittest
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

import fan_watchdog  # noqa: E402
import host  # noqa: E402
import power_range  # noqa: E402
import sensor_reader  # noqa: E402
from fake_host import FakeHost  # noqa: E402

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
              "LIGHTBAR_STAMP", "LIGHTBAR_DAEMON_STAMP", "DESKTOP_APP_SOCKET",
              "DESKTOP_APP_USER_QUIT", "INSTALL_JSON"):
    if hasattr(m, _name):
        REAL[_name] = getattr(m, _name)
        setattr(m, _name, os.path.join(_HOME, _name.lower()))


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


FAIL_OUT = "write /dev/hidraw5: connection timed out"


def fake_host(test, fail_if=(), out=FAIL_OUT):
    """A FakeHost installed as host.run for the rest of `test`; calls whose argv
    contains a fail_if substring answer (1, out)."""
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

    def test_update_active_only(self):
        s = _store("Balanced")
        name = m.update_active_profile(s, tdp={"pl1": 25, "pl2": 30, "pl3": 35, "force": False})
        self.assertEqual(name, "Balanced")
        by = {p["name"]: p for p in s["profiles"]}
        self.assertEqual(by["Balanced"]["tdp"]["pl1"], 25)
        self.assertIsNone(by["Silent"]["tdp"])
        self.assertEqual(by["Boondock"]["tdp"]["pl1"], 20)

    def test_update_rejects_unknown_fields(self):
        with self.assertRaises(ValueError):
            m.update_active_profile(_store(), fan_curve=None)

    def test_update_without_active_raises(self):
        with self.assertRaises(KeyError):
            m.update_active_profile(_store("Nope"), battery_limit=60)

    def test_atomic_write_roundtrip(self):
        s = _store("Silent")
        m.write_store_atomic(s)
        self.assertEqual(self._read(), s)
        self.assertEqual(os.listdir(self.dir), ["profiles.json"])   # no tmp left behind
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o644)

    def test_schema_keys_preserved(self):
        self._write(_store("Balanced"))
        name, err = m._set_active_field(battery_limit=60)
        self.assertEqual((name, err), ("Balanced", None))
        after = self._read()
        self.assertEqual(set(after), {"version", "active", "profiles"})
        for p in after["profiles"]:
            self.assertEqual(set(p), {"name", "builtin", "base", "tdp", "fan_curve", "undervolt",
                                      "brightness", "battery_limit", "hooks"})
        self.assertEqual(m.active_profile(after)["battery_limit"], 60)

    def test_apply_tdp_on_writes_store_then_z13ctl(self):
        self._write(_store("Balanced"))
        run = fake_host(self)
        res = m.apply_tdp(True, 60, 40, 30)
        self.assertTrue(res["ok"] and res["saved"])
        self.assertEqual(m.active_profile(self._read())["tdp"],
                         {"pl1": 60, "pl2": 60, "pl3": 60, "force": False})
        self.assertEqual(len(run.calls), 1)
        argv = run.calls[0].argv
        self.assertEqual(argv[argv.index("tdp"):], ["tdp", "--set", "60", "--pl2", "60", "--pl3", "60"])
        self.assertTrue(run.calls[0].as_deck)

    def test_apply_tdp_off_clears_and_reapplies_profile(self):
        self._write(_store("Boondock"))
        run = fake_host(self)
        res = m.apply_tdp(False, 20, 30, 40)
        self.assertTrue(res["ok"])
        self.assertIsNone(m.active_profile(self._read())["tdp"])
        argv, timeout, as_deck, _ = run.calls[0]
        self.assertEqual(argv[:len(m.Z13_PROFILE)], m.Z13_PROFILE)
        self.assertTrue(as_deck)  # like set_app_profile
        self.assertTrue(argv[-4].endswith("/z13-profile"))
        self.assertEqual(argv[-3:], ["apply", "Boondock", "--no-hooks"])
        self.assertGreaterEqual(timeout, 60)

    def test_apply_tdp_hw_failure_still_saved(self):
        self._write(_store("Balanced"))
        fake_host(self, fail_if=("tdp",))
        res = m.apply_tdp(True, 20, 30, 40)
        self.assertFalse(res["ok"])
        self.assertTrue(res["saved"])

    def test_apply_tdp_no_store_touches_nothing(self):
        run = fake_host(self)
        orig = m._ensure_store
        m._ensure_store = lambda: None
        try:
            res = m.apply_tdp(True, 20, 30, 40)
        finally:
            m._ensure_store = orig
        self.assertFalse(res["saved"])
        self.assertEqual(run.calls, [])

    def test_written_profile_passes_engine_validation(self):
        """What we write must satisfy src/profiles.py's own validate() (the schema owner)."""
        src = os.path.join(os.path.dirname(os.path.dirname(HERE)), "src", "profiles.py")
        if not os.path.exists(src):
            self.skipTest("src/profiles.py not found")
        spec = importlib.util.spec_from_file_location("z13_profiles_engine", src)
        engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(engine)
        for args in ((True, 20, 30, 40), (True, 93, 5, 5), (True, 75, 75, 75), (False, 0, 0, 0)):
            self._write(_store("Balanced"))
            fake_host(self)
            m.apply_tdp(*args)
            m.apply_battery_limit(42)
            for p in self._read()["profiles"]:
                engine.validate(p)

    def test_apply_tdp_for_a_stale_profile_is_rejected(self):
        """A slider edit aimed at A that lands after B became active must not write B."""
        self._write(_store("Boondock"))
        run = fake_host(self)
        before = self._read()
        res = m.apply_tdp(True, 75, 75, 75, profile="Balanced")
        self.assertFalse(res["ok"])
        self.assertFalse(res["saved"])
        self.assertTrue(res["stale"])
        self.assertIn("Balanced", res["message"])
        self.assertEqual(self._read(), before)
        self.assertEqual(run.calls, [])

    def test_apply_battery_limit_for_a_stale_profile_is_rejected(self):
        self._write(_store("Silent"))
        run = fake_host(self)
        before = self._read()
        res = m.apply_battery_limit(60, profile="Balanced")
        self.assertFalse(res["ok"])
        self.assertTrue(res["stale"])
        self.assertEqual(self._read(), before)
        self.assertEqual(run.calls, [])  # no hardware write either

    def test_targeted_request_for_the_active_profile_is_applied(self):
        self._write(_store("Balanced"))
        fake_host(self)
        self.assertTrue(m.apply_tdp(True, 30, 30, 30, profile="Balanced")["saved"])
        self.assertEqual(m.apply_battery_limit(60, profile="Balanced")["value"], 60)
        self.assertEqual(m.active_profile(self._read())["battery_limit"], 60)

    def test_apply_battery_limit(self):
        self._write(_store("Silent"))
        run = fake_host(self)
        res = m.apply_battery_limit(63)
        self.assertTrue(res["ok"] and res["saved"])
        self.assertEqual(res["value"], 65)
        self.assertEqual(m.active_profile(self._read())["battery_limit"], 65)
        self.assertEqual(run.calls[0].argv[-3:], ["batterylimit", "--set", "65"])
        self.assertTrue(run.calls[0].as_deck)


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
        with self.assertRaises(m.StoreLockError):
            with m._file_lock(self.store):
                pass
        self._untouched()

    def test_hardlinked_lock_is_refused(self):
        os.link(self.victim, self.store + ".lock")
        with self.assertRaises(m.StoreLockError):
            with m._file_lock(self.store):
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
                with self.assertRaisesRegex(m.StoreLockError, "timed out"):
                    with m._file_lock(self.store):
                        pass
            finally:
                release.set()
                t.join(5)

    def test_lock_file_owned_by_current_user_is_accepted(self):
        # An unprivileged runner is neither root nor the deck uid.
        self.assertNotEqual(os.geteuid(), 0)
        with mock.patch.object(m, "DECK_UID", os.geteuid() + 1):
            with m._file_lock(self.store):
                pass
            with m._file_lock(self.store):   # now it exists: the owner check runs
                pass

    def test_normal_use_creates_plain_lock_file(self):
        with m._file_lock(self.store):
            pass
        self.assertTrue(os.path.isfile(self.store + ".lock"))


class BrightnessTest(unittest.TestCase):
    def test_one_call_per_device(self):
        run = fake_host(self)
        res = m.set_brightness_per_device("low")
        self.assertEqual(res, {"ok": True, "failed": [], "message": "Brightness low"})
        devs = [a[a.index("--device") + 1] for a in run.argvs]
        self.assertEqual(devs, ["keyboard", "lightbar"])

    def test_wedged_lightbar_does_not_fail_keyboard(self):
        recovers = []
        fake_host(self, fail_if=("--device lightbar",))
        res = m.set_brightness_per_device(
            "high",
            recover=lambda: recovers.append(1) or (False, "lightbar reset skipped"))
        self.assertTrue(res["ok"])
        self.assertEqual(res["failed"], ["lightbar"])
        self.assertIn("lightbar not responding", res["message"])
        self.assertEqual(recovers, [1])

    def test_all_failed(self):
        fake_host(self, fail_if=("brightness",))
        res = m.set_brightness_per_device("off",
                                          recover=lambda: (False, "skipped"))
        self.assertFalse(res["ok"])
        self.assertEqual(res["failed"], ["keyboard", "lightbar"])

    def test_bad_level_runs_nothing(self):
        run = fake_host(self)
        self.assertFalse(m.set_brightness_per_device("max")["ok"])
        self.assertEqual(run.calls, [])


class LightbarRecoveryTest(unittest.TestCase):
    """Copy of src/profiles.py recover_lightbar()/recover_lightbar_stale(); same rules, same tests."""

    def setUp(self):
        d = tempfile.mkdtemp(prefix="z13-stamp-")
        self.stamp = os.path.join(d, "z13-lightbar-reset.stamp")
        self.daemon_stamp = os.path.join(d, "z13-lightbar-daemon-restart.stamp")
        self.slept = []

    def recover(self, now=1000.0):
        return m.recover_lightbar(sleep=self.slept.append, now=lambda: now, stamp=self.stamp)

    def recover_stale(self, now=1000.0):
        return m.recover_lightbar_stale(sleep=self.slept.append, now=lambda: now, stamp=self.daemon_stamp)

    def test_timeout_gets_one_reset_daemon_restart_and_one_retry_keyboard_first(self):
        # The lightbar times out once; the retry after the reset succeeds.
        run = fake_host(self).fail("--device lightbar", times=1,
                                   out="init packet 1: write /dev/hidraw5: connection timed out")
        res = m.set_brightness_per_device("low", recover=self.recover)
        lines = run.lines
        self.assertEqual(len(lines), 5)
        self.assertIn("--device keyboard", lines[0])
        self.assertIn("--device lightbar", lines[1])
        # The reset unit runs as this backend's own user; the daemon restart and
        # the lighting writes as deck (host.run adds the runuser hop under root).
        self.assertEqual([c.as_deck for c in run.calls], [True, True, False, True, True])
        self.assertTrue(lines[2].startswith("systemctl start"))
        self.assertTrue(lines[2].endswith(m.LIGHTBAR_RESET_UNIT))
        self.assertIn("systemctl --user restart", lines[3])
        self.assertTrue(lines[3].endswith(m.Z13CTL_SERVICE))
        self.assertIn("--device lightbar", lines[4])
        self.assertEqual(res["failed"], [])
        self.assertIn("lightbar recovered", res["message"])
        self.assertEqual(self.slept, [m.LIGHTBAR_RESET_SETTLE_S, m.LIGHTBAR_DAEMON_RESTART_WAIT_S])

    def test_stale_gets_one_daemon_restart_and_one_retry_no_usb_reset(self):
        # ENODEV once; the retry after the daemon restart succeeds.
        run = fake_host(self).fail("--device lightbar", times=1,
                                   out="init packet 1: write /dev/hidraw5: no such device")
        res = m.set_brightness_per_device("low", recover_stale=self.recover_stale)
        lines = run.lines
        self.assertEqual(len(lines), 4)
        self.assertIn("--device keyboard", lines[0])
        self.assertIn("--device lightbar", lines[1])
        self.assertIn("systemctl --user restart", lines[2])
        self.assertTrue(lines[2].endswith(m.Z13CTL_SERVICE))
        self.assertIn("--device lightbar", lines[3])
        self.assertFalse(any(line.startswith("systemctl start") for line in lines))
        self.assertEqual(res["failed"], [])
        self.assertIn("lightbar recovered", res["message"])
        self.assertEqual(self.slept, [m.LIGHTBAR_DAEMON_RESTART_WAIT_S])

    def test_unrecognised_failure_no_recovery(self):
        run = fake_host(self, fail_if=("lightbar",), out="permission denied")
        res = m.set_brightness_per_device("low")
        self.assertEqual(len(run.calls), 2)
        self.assertEqual(res["failed"], ["lightbar"])

    def test_still_wedged_after_reset_retries_only_once(self):
        run = fake_host(self, fail_if=("--device lightbar",))
        res = m.set_brightness_per_device(
            "low", recover=self.recover)
        lightbar = [a for a in run.argvs if "lightbar" in a]
        self.assertEqual(len(lightbar), 2)      # first write + exactly one retry
        self.assertEqual(res["failed"], ["lightbar"])
        self.assertIn("still wedged", res["message"])

    def test_still_stale_after_daemon_restart_retries_only_once(self):
        run = fake_host(self, fail_if=("lightbar",), out="write /dev/hidraw5: no such device")
        res = m.set_brightness_per_device(
            "low", recover_stale=self.recover_stale)
        lightbar = [a for a in run.argvs if "lightbar" in a]
        self.assertEqual(len(lightbar), 2)      # first write + exactly one retry
        self.assertEqual(res["failed"], ["lightbar"])
        self.assertIn("still failing after a daemon restart", res["message"])

    def test_rate_limit(self):
        run = fake_host(self)
        self.assertEqual(self.recover(now=1000.0)[0], True)
        os.utime(self.stamp, (1000.0, 1000.0))
        ok, msg = self.recover(now=1000.0 + m.LIGHTBAR_RESET_MIN_GAP_S - 1)
        self.assertFalse(ok)
        self.assertIn("skipped", msg)
        self.assertEqual(len([a for a in run.argvs if "systemctl start" in " ".join(a)]), 1)
        self.assertTrue(self.recover(now=1000.0 + m.LIGHTBAR_RESET_MIN_GAP_S + 1)[0])
        self.assertEqual(len([a for a in run.argvs if "systemctl start" in " ".join(a)]), 2)

    def test_stale_rate_limit(self):
        run = fake_host(self)
        self.assertEqual(self.recover_stale(now=1000.0)[0], True)
        os.utime(self.daemon_stamp, (1000.0, 1000.0))
        ok, msg = self.recover_stale(now=1000.0 + m.LIGHTBAR_RESET_MIN_GAP_S - 1)
        self.assertFalse(ok)
        self.assertIn("skipped", msg)
        self.assertEqual(len(run.calls), 1)
        self.assertTrue(self.recover_stale(now=1000.0 + m.LIGHTBAR_RESET_MIN_GAP_S + 1)[0])
        self.assertEqual(len(run.calls), 2)

    def test_reset_unit_failure_is_reported(self):
        run = fake_host(self, fail_if=("systemctl start",))
        ok, msg = self.recover()
        self.assertFalse(ok)
        self.assertIn("reset failed", msg)
        self.assertEqual(self.slept, [])

    def test_daemon_restart_failure_is_reported_but_reset_still_counts_as_ok(self):
        run = fake_host(self, fail_if=("systemctl --user restart",))
        ok, msg = self.recover()
        self.assertTrue(ok)      # the USB reset itself succeeded
        self.assertIn("daemon restart failed", msg)

    def test_stale_daemon_restart_failure_means_not_ok(self):
        run = fake_host(self, fail_if=("systemctl --user restart",))
        ok, msg = self.recover_stale()
        self.assertFalse(ok)
        self.assertIn("daemon restart failed", msg)

    def test_matches_engine_constants(self):
        src = os.path.join(os.path.dirname(os.path.dirname(HERE)), "src", "profiles.py")
        if not os.path.exists(src):
            self.skipTest("src/profiles.py not found")
        spec = importlib.util.spec_from_file_location("z13_profiles_engine2", src)
        engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(engine)
        for k in ("LIGHTBAR_RESET_UNIT", "LIGHTBAR_RESET_MIN_GAP_S", "LIGHTBAR_RESET_SETTLE_S",
                  "Z13CTL_SERVICE", "LIGHTBAR_DAEMON_RESTART_WAIT_S"):
            self.assertEqual(getattr(m, k), getattr(engine, k), k)
        for out in ("write /dev/hidraw5: connection timed out", "timed out",
                     "write /dev/hidraw5: no such device", "boom", ""):
            self.assertEqual(m.lightbar_timed_out(out), engine.lightbar_timed_out(out))
            self.assertEqual(m.lightbar_failure_kind(out), engine.lightbar_failure_kind(out))


class MaxFansOffTest(unittest.TestCase):
    """OFF must match the desktop app's off(): saved snapshot, else the ACTIVE
    profile's curve, else firmware auto (saved_curve null = profile had no curve)."""

    def test_saved_snapshot_wins(self):
        argv = m.max_fans_off_argv({"saved_curve": [[40, 51], [50, 102]]}, _store("Balanced"))
        self.assertEqual(argv[-3:], ["fancurve", "--set", "40:51,50:102"])

    def test_null_saved_is_firmware_auto_even_with_a_profile_curve(self):
        argv = m.max_fans_off_argv({"enabled": True, "saved_curve": None}, _store("Balanced"))
        self.assertEqual(argv[-2:], ["fancurve", "--reset"])

    def test_missing_saved_uses_active_profile_curve_in_percent(self):
        argv = m.max_fans_off_argv({"enabled": True}, _store("Balanced"))
        self.assertEqual(argv[-2], "--set")
        self.assertTrue(argv[-1].startswith("40:20%,50:35%"))

    def test_no_curve_anywhere_resets_to_firmware_auto(self):
        for store in (_store("Silent"), None):
            argv = m.max_fans_off_argv({"saved_curve": None}, store)
            self.assertEqual(argv[-2:], ["fancurve", "--reset"])

    def test_never_falls_back_to_a_made_up_curve(self):
        self.assertFalse(hasattr(m, "CUSTOM_FALLBACK"))

    def test_off_blocking_writes_state_only_on_success(self):
        saved = {}
        orig = (m._load_max_fans, m._update_max_fans, m._read_store)
        m._load_max_fans = lambda: {"enabled": True, "saved_curve": None}
        m._update_max_fans = lambda **kw: saved.update(kw)
        m._read_store = lambda path=None: _store("Silent")
        try:
            run = fake_host(self, fail_if=("fancurve",))
            self.assertFalse(m.set_max_fans_blocking(False))
            self.assertEqual(saved, {})
            run = fake_host(self)
            self.assertTrue(m.set_max_fans_blocking(False))
            self.assertEqual(run.calls[0][0][-2:], ["fancurve", "--reset"])
            self.assertEqual(saved, {"enabled": False})
        finally:
            m._load_max_fans, m._update_max_fans, m._read_store = orig


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
        self.assertEqual(m.telemetry(), {"apu": 52.5, "gpu": 48.0, "fan1": 2100, "fan2": 2250})

    def test_missing_nodes_are_none(self):
        sensor_reader.HWMON_ROOT = tempfile.mkdtemp(prefix="z13-empty-")
        m._sensors = sensor_reader.SensorReader()
        self.assertEqual(m.telemetry(), {"apu": None, "gpu": None, "fan1": None, "fan2": None})


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
        trace = m.make_fan_trace(path, owner=owner)
        self.assertIsNotNone(trace)
        wd = m.make_fan_watchdog(StalledSensors(), (fake_host(self) and m.DeckyFans()),
                                 may_act=lambda: True, trace=trace)
        for _ in range(fan_watchdog.STALL_TICKS):
            asyncio.run(m.fan_watchdog_tick(wd))
        for h in trace.log.handlers:
            h.flush()
        text = open(path).read()
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


if __name__ == "__main__":
    unittest.main()
