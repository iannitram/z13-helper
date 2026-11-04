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


def test_charge_limit_source_fires_only_when_the_firmware_stops_at_the_limit():
    assert hs.charge_limit_reached(80, "Not charging", 80)
    assert hs.charge_limit_reached(100, "Full", 100)
    assert not hs.charge_limit_reached(79, "Charging", 80)
    assert not hs.charge_limit_reached(80, "Discharging", 80)
    assert not hs.charge_limit_reached(None, "Full", 80)


def test_charge_limit_raise_and_clear_reach_the_runner():
    rig = Rig()
    rig.runner.raise_event(hs.CHARGE_LIMIT)
    assert rig.advance(0) == "play"
    rig.runner.clear_event(hs.CHARGE_LIMIT)
    assert rig.advance(hs.MIN_WRITE_INTERVAL_S) == "restore"


def test_update_attention_source_counts_only_installable_updates():
    assert hs.updates_need_attention([{"new": True, "installer": "steamos"}])
    assert hs.updates_need_attention([{"new": True, "installer": "z13ctl"}])
    assert not hs.updates_need_attention([{"new": True}])          # watch-only row
    assert not hs.updates_need_attention([{"new": False, "installer": "z13ctl"}])
    assert not hs.updates_need_attention([])


def test_update_attention_raise_and_clear_reach_the_runner():
    rig = Rig()
    rig.runner.raise_event(hs.UPDATE_ATTENTION)
    assert rig.advance(0) == "play"
    rig.runner.clear_event(hs.UPDATE_ATTENTION)
    assert rig.advance(hs.MIN_WRITE_INTERVAL_S) == "restore"


# --- SignalRunner (issue #130) ---------------------------------------------------------

class Rig:
    """Runner with a fake clock and a fake lightbar; `own` is the owner's lighting."""

    def __init__(self, enabled=True, own=hs.Pattern("static", "white", "medium"), fail=False):
        self.t = 100.0
        self.writes = []
        self.on = enabled
        self.own = own
        self.fail = fail
        self.runner = hs.SignalRunner(self._write, lambda: self.own, lambda: self.on,
                                      clock=lambda: self.t)

    def _write(self, pattern):
        self.writes.append(pattern)
        if self.fail:
            raise RuntimeError("wedged")

    def advance(self, s):
        self.t += s
        return self.runner.tick()


def test_profile_change_flashes_then_owner_lighting_returns():
    rig = Rig()
    rig.runner.note_external_write()
    rig.runner.raise_event(hs.PROFILE_CHANGED)
    assert rig.advance(0) == "none"                     # the apply's own write is too recent
    assert rig.advance(hs.MIN_WRITE_INTERVAL_S) == "play"
    assert rig.writes == [hs.SIGNALS[hs.PROFILE_CHANGED].pattern]
    assert rig.advance(0.5) == "none"                   # still showing, no rewrite
    assert rig.advance(hs.ONE_SHOT_S) == "restore"
    assert rig.writes[-1] == rig.own
    assert rig.advance(30) == "none" and len(rig.writes) == 2


def test_signals_off_means_no_lighting_write_at_all():
    rig = Rig(enabled=False)
    rig.runner.raise_event(hs.PROFILE_CHANGED)
    rig.runner.raise_event(hs.THROTTLING)
    for _ in range(10):
        assert rig.advance(5) == "none"
    assert rig.writes == []


def test_turning_signals_off_mid_signal_restores_once_then_stays_quiet():
    rig = Rig()
    rig.runner.raise_event(hs.THROTTLING)
    assert rig.advance(0) == "play"
    rig.on = False
    assert rig.advance(hs.MIN_WRITE_INTERVAL_S) == "restore"
    assert rig.advance(10) == "none" and rig.advance(10) == "none"
    assert rig.writes[-1] == rig.own and len(rig.writes) == 2


def test_writes_are_rate_limited_across_event_bursts():
    rig = Rig()
    rig.runner.raise_event(hs.CHARGE_LIMIT)
    assert rig.advance(0) == "play"
    rig.runner.raise_event(hs.THROTTLING)               # higher priority, but too soon
    assert rig.advance(0.5) == "none"
    assert rig.advance(hs.MIN_WRITE_INTERVAL_S) == "play"
    assert rig.writes[-1] == hs.SIGNALS[hs.THROTTLING].pattern


def test_failed_write_drops_the_signal_instead_of_retrying():
    rig = Rig(fail=True)
    rig.runner.raise_event(hs.THROTTLING)
    assert rig.advance(0) == "play"
    for _ in range(5):
        assert rig.advance(hs.MIN_WRITE_INTERVAL_S) == "none"
    assert len(rig.writes) == 1


def test_no_known_owner_lighting_leaves_the_bar_alone():
    rig = Rig(own=None)
    rig.runner.raise_event(hs.PROFILE_CHANGED)
    assert rig.advance(0) == "play"
    assert rig.advance(hs.ONE_SHOT_S + 1) == "none" and len(rig.writes) == 1


def test_clearing_a_sticky_event_restores():
    rig = Rig()
    rig.runner.raise_event(hs.CHARGE_LIMIT)
    rig.advance(0)
    rig.runner.clear_event(hs.CHARGE_LIMIT)
    assert rig.advance(hs.MIN_WRITE_INTERVAL_S) == "restore"


def test_submit_defers_the_write_and_one_is_in_flight_at_a_time():
    queued = []
    t = [0.0]
    writes = []
    r = hs.SignalRunner(writes.append, lambda: None, clock=lambda: t[0],
                        submit=lambda label, fn: queued.append((label, fn)))
    r.raise_event(hs.THROTTLING)
    assert r.tick() == "play" and r.tick() == "none"
    assert queued[0][0] == hs.SIGNAL_LABEL and writes == []
    queued[0][1]()
    assert len(writes) == 1
