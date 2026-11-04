"""Timeline view logic (issue #135): newest first, compare, roll back, why not."""

import pytest

import timeline
import timeline_view as tv


def entry(i, ts, summary, **kw):
    e = {"id": i, "ts": ts, "kind": "profile", "summary": summary, "ok": True,
         "before": {}, "after": {}, "changed": [], "rollback": None,
         "no_rollback_reason": "Re-applied the already active profile"}
    e.update(kw)
    return e


def switch(i, ts, prev, cur):
    return entry(i, ts, f"Profile {prev} -> {cur}",
                 before={"name": prev, "tdp": {"pl1": 20}}, after={"name": cur, "tdp": {"pl1": 40}},
                 rollback={"kind": "profile", "profile": prev}, no_rollback_reason=None)


def test_rows_newest_first_with_rollback_state_and_reason():
    es = [switch("a", "2026-11-01T10:00:00", "Quiet", "Turbo"),
          entry("b", "2026-11-02T10:00:00", "Profile Turbo applied")]
    r = tv.rows(es)
    assert [x["id"] for x in r] == ["b", "a"]
    assert r[0]["can_roll_back"] is False and "already active" in r[0]["why_not"]
    assert r[1]["can_roll_back"] is True and r[1]["why_not"] is None


def test_steamos_update_is_one_row_listing_what_changed_and_says_why_not():
    e = entry("s", "2026-11-03T10:00:00", "SteamOS updated (1 -> 2)", kind="steamos_update",
              changed=["BUILD_ID 1 -> 2", "kernel: 6.1 -> 6.2"],
              no_rollback_reason="SteamOS OS updates switch A/B image slots")
    (row,) = tv.rows([e])
    assert row["changed"] == ["BUILD_ID 1 -> 2", "kernel: 6.1 -> 6.2"]
    assert "A/B" in row["why_not"]
    assert "kernel: 6.1 -> 6.2" in tv.describe(e)


def test_unsupported_kind_and_missing_reason_still_explain():
    e = entry("g", "t", "Goal a -> b", kind="goal", rollback={"kind": "goal", "goal": "a"})
    assert "not built into the app" in tv.why_not(e)
    assert tv.why_not(entry("x", "t", "s", no_rollback_reason=None)) == "Not reversible"


def test_compare_lists_only_differing_keys_across_nested_state():
    a = switch("a", "t1", "Quiet", "Turbo")
    b = switch("b", "t2", "Turbo", "Quiet")
    b["after"] = {"name": "Quiet", "tdp": {"pl1": 20, "pl2": 25}}
    diff = tv.compare(a, b)
    assert diff == [("name", "Turbo", "Quiet"), ("tdp.pl1", 40, 20), ("tdp.pl2", None, 25)]
    assert tv.compare(a, a) == []
    assert "same" in tv.format_compare(a, a)
    assert "tdp.pl1" in tv.format_compare(a, b)


def test_roll_back_runs_record_and_adds_its_own_entry(tmp_path):
    path = str(tmp_path / "timeline.jsonl")
    e = switch("a", "t1", "Quiet", "Turbo")
    calls = []

    def apply(name):
        calls.append(name)
        return [("profile", True, "ok"), ("tdp", True, "20 W")]

    new = tv.roll_back(e, [e], apply_profile=apply, path=path)
    assert calls == ["Quiet"]
    assert new["summary"] == "Rolled back: Profile Quiet -> Turbo" and new["rollback_of"] == "a"
    assert new["after"] == e["before"] and new["before"] == e["after"]
    stored = timeline.read_entries(path)
    assert [x["id"] for x in stored] == [new["id"]] and stored[0]["ok"] is True


def test_rollback_cannot_run_twice_but_a_failed_one_can_be_retried(tmp_path):
    path = str(tmp_path / "t.jsonl")
    e = switch("a", "t1", "Quiet", "Turbo")
    failed = tv.roll_back(e, [e], apply_profile=lambda n: [("profile", False, "boom")], path=path)
    assert failed["ok"] is False
    assert {r["id"]: r for r in tv.rows([e, failed])}["a"]["can_roll_back"] is True
    done = tv.roll_back(e, [e, failed], apply_profile=lambda n: [("profile", True, "")], path=path)
    with pytest.raises(ValueError, match="Already rolled back"):
        tv.roll_back(e, [e, failed, done], apply_profile=lambda n: [], path=path)


def test_roll_back_refuses_an_entry_without_a_record_and_does_not_apply(tmp_path):
    called = []
    e = entry("x", "t", "Profile Turbo applied")
    with pytest.raises(ValueError, match="already active"):
        tv.roll_back(e, [e], apply_profile=lambda n: called.append(n), path=str(tmp_path / "t"))
    assert called == []
    assert timeline.read_entries(str(tmp_path / "t")) == []


def test_rollback_entry_is_not_itself_rollbackable(tmp_path):
    e = switch("a", "t1", "Quiet", "Turbo")
    new = tv.roll_back(e, [e], apply_profile=lambda n: [("profile", True, "")], path=str(tmp_path / "t"))
    assert tv.why_not(new) and "itself a rollback" in tv.why_not(new)


def test_apply_without_record_skips_the_timeline(monkeypatch):
    import profiles
    seen = []
    monkeypatch.setattr(profiles, "_apply_steps", lambda *a, **k: [("profile", True, "ok")])
    monkeypatch.setattr(profiles, "_max_fans_state", lambda: {})
    monkeypatch.setattr(profiles.timeline, "record_profile_apply", lambda **k: seen.append(k))
    profiles.apply({"name": "A", "base": "balanced"}, record=False)
    assert seen == []
    profiles.apply({"name": "A", "base": "balanced"})
    assert len(seen) == 1
