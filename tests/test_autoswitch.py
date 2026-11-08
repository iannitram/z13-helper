"""Charger-aware autoswitch intent (issue #24): pure decision, hold and argv."""

import autoswitch


NAMES = ["Silent", "Balanced", "Turbo"]


def test_normalize_keeps_known_targets_and_drops_unknown_names():
    cfg = autoswitch.normalize(
        {"ac": "Turbo", "battery": "Missing", "hold": True}, NAMES
    )
    assert cfg["ac"] == "Turbo"
    assert cfg["battery"] is None
    assert cfg["hold"] is True
    assert cfg["enabled"] is False


def test_normalize_keeps_enabled_as_the_source_of_truth():
    assert autoswitch.normalize({}, NAMES)["enabled"] is False
    assert autoswitch.normalize({"enabled": True}, NAMES)["enabled"] is True
    assert autoswitch.normalize({"enabled": False}, NAMES)["enabled"] is False
    # Targets alone never enable or disable it.
    assert autoswitch.normalize({"ac": "Turbo"}, NAMES)["enabled"] is False
    assert (
        autoswitch.normalize({"ac": "Turbo", "enabled": False}, NAMES)["enabled"]
        is False
    )
    assert (
        autoswitch.normalize({"ac": "Turbo", "enabled": True}, NAMES)["enabled"] is True
    )


def test_filling_default_targets_does_not_enable_autoswitch():
    cfg = autoswitch.default_config()
    cfg.update(ac="Turbo", battery="Silent")
    targets = {"Turbo": "z13h-turbo-1", "Silent": "quiet"}

    for state in (cfg, autoswitch.normalize(cfg, NAMES)):
        assert state == {
            "ac": "Turbo", "battery": "Silent", "enabled": False, "hold": False
        }
        assert autoswitch.decision(state, True) is None
        assert autoswitch.decision(state, False) is None
        assert autoswitch.backend_argv(state, targets) == [
            "autoswitch", "--ac", "z13h-turbo-1", "--battery", "quiet", "--off"
        ]
        assert autoswitch.describe(state) == "Autoswitch off"


def test_decision_picks_the_source_profile():
    cfg = autoswitch.normalize(
        {"ac": "Turbo", "battery": "Silent", "enabled": True}, NAMES
    )
    assert autoswitch.decision(cfg, True) == "Turbo"
    assert autoswitch.decision(cfg, False) == "Silent"


def test_hold_refuses_to_switch_but_keeps_both_choices():
    cfg = autoswitch.normalize(
        {"ac": "Turbo", "battery": "Silent", "hold": True, "enabled": True}, NAMES
    )
    assert autoswitch.decision(cfg, True) is None
    assert autoswitch.decision(cfg, False) is None


def test_decision_none_for_missing_source_or_disabled_config():
    assert autoswitch.decision({}, True) is None
    cfg = autoswitch.normalize({"ac": "Turbo", "enabled": True}, NAMES)
    assert autoswitch.decision(cfg, False) is None
    assert autoswitch.decision({"enabled": False, "ac": "Turbo"}, True) is None
    assert autoswitch.decision({"enabled": True, "ac": "Turbo"}, True) == "Turbo"


def test_hardware_overrides_are_the_named_profile_fields():
    assert autoswitch.has_hardware_overrides({"tdp": {"pl1": 20}})
    assert autoswitch.has_hardware_overrides({"fan_curve": [[20, 20]]})
    assert autoswitch.has_hardware_overrides({"undervolt": -20})
    assert not autoswitch.has_hardware_overrides({"epp": "power", "brightness": "low"})
    assert not autoswitch.has_hardware_overrides(None)


def test_backend_argv_keeps_targets_while_held_and_enables_when_released():
    cfg = autoswitch.normalize(
        {"ac": "Turbo", "battery": "Silent", "enabled": True}, NAMES
    )
    targets = {"Turbo": "z13h-turbo-1", "Silent": "quiet"}
    assert autoswitch.backend_argv(cfg, targets) == [
        "autoswitch",
        "--ac",
        "z13h-turbo-1",
        "--battery",
        "quiet",
        "--on",
    ]
    held = autoswitch.normalize(
        {"ac": "Turbo", "battery": "Silent", "hold": True, "enabled": True}, NAMES
    )
    assert autoswitch.backend_argv(held, targets)[-1] == "--off"


def test_backend_argv_emits_off_when_disabled():
    targets = {"Turbo": "z13h-turbo-1", "Silent": "quiet"}
    cfg = autoswitch.normalize(
        {"ac": "Turbo", "battery": "Silent", "enabled": False}, NAMES
    )
    assert autoswitch.backend_argv(cfg, targets) == [
        "autoswitch",
        "--ac",
        "z13h-turbo-1",
        "--battery",
        "quiet",
        "--off",
    ]


def test_backend_argv_clears_when_no_targets_remain():
    cfg = autoswitch.normalize({"ac": "", "battery": "", "enabled": True}, NAMES)
    assert autoswitch.backend_argv(cfg, {}) == ["autoswitch", "--clear"]


def test_describe_names_both_sides_and_hold():
    cfg = autoswitch.normalize(
        {"ac": "Turbo", "battery": "Silent", "enabled": True}, NAMES
    )
    assert autoswitch.describe(cfg) == "Autoswitch: Turbo on AC, Silent on battery"
    cfg = autoswitch.normalize(
        {"ac": "Turbo", "battery": "Silent", "hold": True, "enabled": True}, NAMES
    )
    assert autoswitch.describe(cfg).endswith("(held)")


def test_describe_reports_disabled_as_off():
    cfg = autoswitch.normalize(
        {"ac": "Turbo", "battery": "Silent", "enabled": False}, NAMES
    )
    assert autoswitch.describe(cfg) == "Autoswitch off"
    assert autoswitch.describe({}) == "Autoswitch off"
    assert autoswitch.describe({"enabled": True}) == "Autoswitch off"


def test_decision_unknown_power_state_stays_put():
    # None means "unknown", never "battery": the battery profile must not apply.
    cfg = autoswitch.normalize(
        {"ac": "Turbo", "battery": "Silent", "enabled": True}, NAMES
    )
    assert autoswitch.decision(cfg, None) is None
    assert autoswitch.decision({}, None) is None


def test_decision_non_bool_power_state_stays_put():
    cfg = autoswitch.normalize(
        {"ac": "Turbo", "battery": "Silent", "enabled": True}, NAMES
    )
    for value in ("yes", 1, 0, "", [], 2.0):
        assert autoswitch.decision(cfg, value) is None, repr(value)


def test_backend_argv_tolerates_none_inputs():
    # Matches the empty-config result: nothing to send means clear.
    assert autoswitch.backend_argv(None, {}) == ["autoswitch", "--clear"]
    assert autoswitch.backend_argv({}, None) == ["autoswitch", "--clear"]
    assert autoswitch.backend_argv(None, None) == ["autoswitch", "--clear"]
    assert autoswitch.backend_argv({}, {}) == ["autoswitch", "--clear"]


def test_string_flags_do_not_enable_or_hold_autoswitch():
    # A hand-edited JSON "false" is truthy in Python; it must not enable switching.
    cfg = autoswitch.normalize(
        {"ac": "Turbo", "battery": "Silent", "enabled": "false", "hold": "false"}, NAMES
    )
    assert cfg["enabled"] is False
    assert cfg["hold"] is False
    assert autoswitch.decision(cfg, True) is None
    assert autoswitch.backend_argv(cfg, {"Turbo": "t", "Silent": "s"})[-1] == "--off"
    raw = {"ac": "Turbo", "enabled": "true"}
    assert autoswitch.decision(raw, True) is None
    assert autoswitch.describe(raw) == "Autoswitch off"


def test_backend_argv_drops_non_string_or_empty_targets():
    # Non-str daemon targets must never reach argv (host.run expects strings).
    targets = {"Turbo": 5, "Silent": {"x": 1}, "Balanced": ""}
    cfg = {"ac": "Turbo", "battery": "Silent", "enabled": True}
    assert autoswitch.backend_argv(cfg, targets) == ["autoswitch", "--clear"]
    cfg = {"ac": "Turbo", "battery": "Balanced", "enabled": True}
    argv = autoswitch.backend_argv(cfg, {**targets, "Turbo": "z13h-turbo-1"})
    assert all(isinstance(arg, str) for arg in argv)
    assert argv[:3] == ["autoswitch", "--ac", "z13h-turbo-1"]
    assert "--battery" in argv and argv[argv.index("--battery") + 1] == ""
