"""Lighting signal language (issue #129): pure mapping, priority, rate limit, restore, off switch."""

import hw_signals as hs
import profiles


def test_every_pattern_builds_a_lighting_command():
    for sig in hs.SIGNALS.values():
        argv = profiles.lighting_argv(hs.DEVICE, sig.pattern.setting(), sig.pattern.brightness)
        assert "--mode" in argv and sig.pattern.mode in argv


def test_priorities_are_unique_and_throttling_beats_update_beats_limit_beats_profile():
    p = [hs.signal_for(e).priority for e in (hs.THROTTLING, hs.UPDATE_ATTENTION,
                                              hs.CHARGE_LIMIT, hs.PROFILE_CHANGED)]
    assert p == sorted(p, reverse=True) and len(set(p)) == 4


def test_profile_change_is_one_shot_and_expires():
    assert hs.active_signal({hs.PROFILE_CHANGED: 0.0}, 1.0).event == hs.PROFILE_CHANGED
    assert hs.active_signal({hs.PROFILE_CHANGED: 0.0}, hs.ONE_SHOT_S) is None


def test_sticky_signals_stay_and_highest_priority_wins():
    active = {hs.CHARGE_LIMIT: 0.0, hs.THROTTLING: 5.0, "bogus": 0.0}
    assert hs.active_signal(active, 9999).event == hs.THROTTLING
    assert hs.active_signal({"bogus": 0.0}, 1) is None


def test_play_then_no_repeat_for_same_pattern():
    active = {hs.THROTTLING: 0.0}
    act, pat = hs.next_action(active, None, now=0.0, last_write=None)
    assert (act, pat) == ("play", hs.SIGNALS[hs.THROTTLING].pattern)
    assert hs.next_action(active, pat, now=10.0, last_write=0.0)[0] == "none"


def test_rate_limit_defers_writes():
    active = {hs.THROTTLING: 0.0}
    assert hs.next_action(active, None, now=1.0, last_write=0.5)[0] == "none"
    assert hs.next_action(active, None, now=0.5 + hs.MIN_WRITE_INTERVAL_S,
                          last_write=0.5)[0] == "play"


def test_restore_owner_lighting_when_signal_ends():
    own = hs.Pattern("static", "white", "medium")
    shown = hs.SIGNALS[hs.PROFILE_CHANGED].pattern
    assert hs.next_action({hs.PROFILE_CHANGED: 0.0}, shown, now=5.0, last_write=0.0,
                          restore=own) == ("restore", own)
    # no known lighting to restore: leave it alone
    assert hs.next_action({}, shown, now=5.0, last_write=0.0) == ("none", None)
    # nothing shown, nothing to do
    assert hs.next_action({}, None, now=5.0, last_write=None, restore=own) == ("none", None)


def test_off_switch_never_plays_and_restores_once():
    own = hs.Pattern("static", "white")
    active = {hs.THROTTLING: 0.0}
    assert hs.next_action(active, None, now=9.0, last_write=None, enabled=False,
                          restore=own) == ("none", None)
    shown = hs.SIGNALS[hs.THROTTLING].pattern
    assert hs.next_action(active, shown, now=9.0, last_write=0.0, enabled=False,
                          restore=own) == ("restore", own)
    # already restored: no repeated write
    assert hs.next_action(active, own, now=20.0, last_write=9.0, enabled=False,
                          restore=own) == ("none", None)


def test_goal_not_met_has_its_own_sticky_pattern_between_limit_and_update():
    sig = hs.signal_for(hs.GOAL_NOT_MET)
    assert sig is not None and sig.duration_s is None
    assert hs.signal_for(hs.CHARGE_LIMIT).priority < sig.priority < hs.signal_for(hs.UPDATE_ATTENTION).priority
    assert hs.active_signal({hs.GOAL_NOT_MET: 0.0}, 9999) is sig
    assert hs.active_signal({hs.GOAL_NOT_MET: 0.0, hs.CHARGE_LIMIT: 0.0}, 5) is sig
    assert hs.active_signal({hs.GOAL_NOT_MET: 0.0, hs.THROTTLING: 0.0}, 5).event == hs.THROTTLING
    assert hs.next_action({hs.GOAL_NOT_MET: 0.0}, None, now=0.0, last_write=None) == ("play", sig.pattern)
    patterns = [s.pattern for s in hs.SIGNALS.values()]
    assert len(set(patterns)) == len(patterns)
