"""Decky signal runner (#130): off switch, restore, shared rate limit, one runner per event.

No Decky, no z13ctl, no hardware.  Run from the repo root:
python3 -m unittest discover -s decky/tests -v
"""

import asyncio
import types
import unittest
from unittest import mock

import test_backend as tb   # loads decky/main.py with scratch paths

import hw_signals as hs

m = tb.m
SAVED = {"mode": "static", "color": "blue"}


class FakeController:
    def __init__(self):
        self.writes = []

    def set_lighting(self, device, setting, brightness):
        self.writes.append((setting["mode"], setting["color"], brightness))


def make_plugin(enabled=True, lighting=SAVED):
    p = m.Plugin.__new__(m.Plugin)
    p._signals_active = {}
    p._signals_shown = None
    p._signals_shown_at = {}
    p._signals_last_write = None
    p._signals_wakeup = asyncio.Event()
    p._signal_controller = FakeController()
    p._enabled = enabled
    p._lighting = lighting
    return p


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class SignalRunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = Clock()
        self.cur = None
        patches = [
            mock.patch.object(m, "time", types.SimpleNamespace(monotonic=self.clock)),   # not the global clock: asyncio uses it
            mock.patch.object(m, "_read_store", return_value={}),
            mock.patch.object(m, "_shared_config", side_effect=lambda: {
                "hardware_signals_enabled": self.cur._enabled}),
            mock.patch.object(m, "active_profile", side_effect=lambda store: {
                "lighting": {hs.DEVICE: self.cur._lighting} if self.cur._lighting else {},
                "brightness": "high"}),
        ]
        patches.append(mock.patch.object(m, "_write_lock", None))   # a lock per test loop
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def plug(self, **kw):
        self.cur = make_plugin(**kw)
        return self.cur

    async def step(self, p):
        """One pass of the runner loop, then stop it at its wait."""
        task = asyncio.create_task(m.Plugin._run_hardware_signals(p))
        await asyncio.sleep(0.15)   # the runner's reads and write run in threads
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_profile_change_flashes_then_restores(self):
        p = self.plug()
        p._signal_profile_changed()
        await self.step(p)
        self.assertEqual(p._signal_controller.writes, [("static", "green", "high")])
        self.clock.now += hs.ONE_SHOT_S + 0.1
        await self.step(p)
        self.assertEqual(p._signal_controller.writes[-1], ("static", "blue", "high"))
        self.assertEqual(len(p._signal_controller.writes), 2)

    async def test_off_switch_means_zero_lightbar_writes(self):
        p = self.plug(enabled=False)
        p._signal_profile_changed()
        self.clock.now += 10
        await self.step(p)
        self.assertEqual(p._signal_controller.writes, [])

    async def test_turning_off_mid_signal_restores_once(self):
        p = self.plug()
        p._signal_profile_changed()
        await self.step(p)
        p._enabled = False
        self.clock.now += hs.MIN_WRITE_INTERVAL_S + 0.1
        await self.step(p)
        await self.step(p)
        self.assertEqual(p._signal_controller.writes,
                         [("static", "green", "high"), ("static", "blue", "high")])

    async def test_rate_limit_counts_user_lightbar_writes(self):
        p = self.plug()
        p._signals_last_write = self.clock.now   # set_brightness / a profile apply just wrote
        p._signal_profile_changed()
        self.clock.now += hs.MIN_WRITE_INTERVAL_S - 0.5
        await self.step(p)
        self.assertEqual(p._signal_controller.writes, [])
        self.clock.now += 1.0
        await self.step(p)
        self.assertEqual(p._signal_controller.writes, [("static", "green", "high")])

    async def test_only_decky_applies_raise_the_decky_signal(self):
        """The desktop app raises its own signal for its own applies, so one profile change
        is flashed by one runner: Decky's set_app_profile is the only Decky caller."""
        p = self.plug()
        with mock.patch.object(m, "_writes_allowed", return_value=True), \
                mock.patch.object(m, "_run", return_value=(0, "OK")), \
                mock.patch.object(m, "Z13_PROFILE", ["z13-profile"]):
            res = await m.Plugin.set_app_profile(p, "Turbo")
        self.assertTrue(res["ok"])
        self.assertIn(hs.PROFILE_CHANGED, p._signals_active)
        self.assertEqual(list(p._signals_active), [hs.PROFILE_CHANGED])


if __name__ == "__main__":
    unittest.main()
