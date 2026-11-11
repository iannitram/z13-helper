"""Decky Plugin.rollback_timeline_entry / get_timeline (#135): no Decky, no z13ctl, no hardware.

Run from the repo root:  python3 -m unittest discover -s decky/tests -v
"""

import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

import test_backend as tb   # loads decky/main.py with scratch paths; shared helpers

import max_fans_state
import profiles
import timeline

m = tb.m


def run(coro):
    return asyncio.run(coro)


def _entry(kind, **rb):
    return {"id": f"old-{kind}", "kind": kind, "rollback": {"kind": kind, **rb}}


def _seed(path, *entries):
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


class RollbackBase(unittest.TestCase):
    def setUp(self):
        gate = mock.patch.object(m, "_writes_allowed", return_value=True)
        gate.start()
        self.addCleanup(gate.stop)
        self.dir = tempfile.mkdtemp(prefix="z13-rollback-")
        self.timeline_path = os.path.join(self.dir, "timeline.jsonl")
        for attr, value in (("TIMELINE_PATH", self.timeline_path),
                            ("PROFILES_STORE", os.path.join(self.dir, "profiles.json")),
                            ("MAX_FANS_STATE", os.path.join(self.dir, "max-fans.json"))):
            p = mock.patch.object(m, attr, value)
            p.start()
            self.addCleanup(p.stop)
        # authority and profiles record to the default path; in production it is the same file.
        p = mock.patch.object(timeline, "TIMELINE_PATH", self.timeline_path)
        p.start()
        self.addCleanup(p.stop)
        for attr, value in (("STORE_PATH", os.path.join(self.dir, "profiles.json")),
                            ("MAX_FANS_PATH", os.path.join(self.dir, "max-fans.json"))):
            p = mock.patch.object(profiles, attr, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(max_fans_state, "STATE_PATH", os.path.join(self.dir, "max-fans.json"))
        p.start()
        self.addCleanup(p.stop)
        self.fh = tb.fake_host(self)
        store = tb._store("Balanced")
        store["profiles"][1]["tdp"] = {"pl1": 20, "pl2": 30, "pl3": 40, "force": False}
        profiles.save_store(store)
        self.plugin = m.Plugin()

    def entries(self):
        timeline.flush(10)
        return timeline.read_entries(self.timeline_path)

    def rollback(self, entry):
        _seed(self.timeline_path, entry)
        return run(self.plugin.rollback_timeline_entry(entry["id"]))


class RollbackEntryTest(RollbackBase):
    def test_each_setting_kind_adds_its_own_entry(self):
        for kind, value, check in (
                ("battery_limit", 85, lambda e: e["after"] == {"battery_limit": 85}),
                ("brightness", "low", lambda e: e["after"] == {"brightness": "low"}),
                ("tdp", {"pl1": 15, "pl2": 25, "pl3": 35, "force": False},
                 lambda e: e["after"]["tdp"]["pl1"] == 15)):
            with self.subTest(kind=kind):
                res = self.rollback(_entry(kind, state={kind: value}))
                self.assertTrue(res["ok"], res)
                new = self.entries()[1:]   # the seeded entry comes first (each rollback reseeds the file)
                self.assertEqual(len(new), 1, new)
                self.assertEqual(new[0]["kind"], kind)
                self.assertTrue(check(new[0]), new[0])

    def test_goal_adds_its_own_entry(self):
        res = self.rollback(_entry("goal", goal="max", state={"goal": "max"}))
        self.assertEqual(res, {"ok": True, "message": "Saved fan goal restored."})
        new = self.entries()[1:]
        self.assertEqual([e["kind"] for e in new], ["goal"])
        self.assertEqual(new[0]["after"]["goal"], "max")

    def test_profile_applies_without_hooks_and_adds_its_own_entry(self):
        calls = []

        def fake_run(argv, timeout, as_deck=False):
            calls.append(argv)
            # What z13-profile does on a real apply: apply, record, set active.
            profiles.apply_by_name(argv[argv.index("apply") + 1], skip_hooks=True)
            timeline.flush(10)
            return 0, "  ok   profile  applied"

        with mock.patch.object(m, "_run", fake_run):
            res = self.rollback(_entry("profile", profile="Silent", snapshot={"name": "Silent"}))
        self.assertTrue(res["ok"], res)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-3:], ["apply", "Silent", "--no-hooks"])
        new = self.entries()[1:]
        self.assertEqual([e["kind"] for e in new], ["profile"])

    def test_profile_failure_returns_ok_false_with_a_message(self):
        with mock.patch.object(m, "_run", lambda *a, **k: (1, "ERR  profile  boom")):
            res = self.rollback(_entry("profile", profile="Silent", snapshot={"name": "Silent"}))
        self.assertFalse(res["ok"])
        self.assertIn("Profile restore failed", res["message"])

    def test_setting_failure_returns_ok_false_with_a_message_and_a_failed_entry(self):
        self.fh.fail("battery", out="write /dev/hidraw5: connection timed out")
        res = self.rollback(_entry("battery_limit", state={"battery_limit": 85}))
        self.assertFalse(res["ok"])
        self.assertTrue(res["message"])
        last = self.entries()[-1]
        self.assertFalse(last["ok"])

    def test_goal_failure_returns_ok_false_with_a_message(self):
        with mock.patch.object(m, "_set_max_fans_recorded", side_effect=RuntimeError("no daemon")):
            res = self.rollback(_entry("goal", goal="max", state={"goal": "max"}))
        self.assertEqual(res["ok"], False)
        self.assertIn("no daemon", res["message"])

    def test_unsupported_or_unknown_records_write_nothing(self):
        bad = {"id": "x", "rollback": {"kind": "shell", "cmd": "reboot"}}
        self.assertFalse(self.rollback(bad)["ok"])
        self.assertFalse(run(self.plugin.rollback_timeline_entry("missing"))["ok"])
        self.assertFalse(run(self.plugin.rollback_timeline_entry(""))["ok"])
        self.assertFalse(run(self.plugin.rollback_timeline_entry(None))["ok"])
        self.assertEqual(self.fh.calls, [])

    def test_unsupported_machine_is_refused_before_anything_runs(self):
        with mock.patch.object(m, "_writes_allowed", return_value=False):
            res = self.rollback(_entry("battery_limit", state={"battery_limit": 85}))
        self.assertEqual(res, {"ok": False, "message": m.UNSUPPORTED_MESSAGE})
        self.assertEqual(self.fh.calls, [])

    def test_the_timeline_is_read_outside_the_write_lock(self):
        """A slow read must not hold the lock that serialises hardware writes."""
        seen = []
        real = timeline.read_entries

        def spy(*a, **k):
            seen.append(m._lock().locked())
            return real(*a, **k)

        with mock.patch.object(timeline, "read_entries", spy):
            self.rollback(_entry("battery_limit", state={"battery_limit": 85}))
        self.assertEqual(seen[:1], [False])


class GetTimelineTest(RollbackBase):
    def test_rows_carry_rollback_supported(self):
        _seed(self.timeline_path,
              _entry("battery_limit", state={"battery_limit": 85}),
              {"id": "b", "kind": "install", "rollback": None})
        rows = run(self.plugin.get_timeline(10))
        self.assertEqual([r["rollback_supported"] for r in rows], [False, True])   # newest first


if __name__ == "__main__":
    unittest.main()
