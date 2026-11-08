"""File-only ownership decision for the system resume hook."""

import copy
import json
import socket
import subprocess

import host
import profiles


def _fixture(profiles_env, monkeypatch, *, max_on=False, tdp=None):
    app = copy.deepcopy(profiles.default_profiles()[2])
    app.update({"name": "Resume Turbo", "tdp": tdp, "battery_limit": None,
                "epp": None, "max_fans": max_on})
    store = {"version": 1, "active": app["name"], "profiles": [app]}
    profiles_env.store.parent.mkdir(parents=True, exist_ok=True)
    profiles_env.store.write_text(json.dumps(store))
    profiles_env.max_fans.write_text(json.dumps({"enabled": max_on}))

    from z13ctl_client import Z13CtlClient
    name = Z13CtlClient.mapped_profile_name(app)
    points = ([{"temp": t, "pwm": 255} for t in (20, 30, 40, 50, 60, 70, 80, 90)]
              if max_on else [{"temp": t, "pwm": round(p * 255 / 100)} for t, p in app["fan_curve"]])
    named = {"name": name, "fan_curve": {"mode": 1, "points": points}}
    if tdp is not None:
        named["tdp"] = {"pl1_spl": tdp["pl1"], "pl2_sppt": tdp["pl2"],
                        "fppt": tdp["pl3"], "apu_sppt": tdp["pl1"]}
    state_home = profiles_env.store.parents[2] / "xdg-state"
    state_dir = state_home / "z13ctl"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "state.json").write_text(json.dumps({
        "profile": name, "battery_limit": 80, "custom_profiles": {name: named}}))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    return app, named, state_dir / "state.json"


def _check():
    return profiles.main(["resume-owner"])


def test_resume_owner_baseline(profiles_env, monkeypatch):
    _fixture(profiles_env, monkeypatch)
    assert _check() == 10


def test_resume_owner_real_shape_profile_without_epp_defers(profiles_env, monkeypatch):
    defaults = profiles.default_profiles()
    turbo = copy.deepcopy(next(p for p in defaults if p["name"] == "Turbo"))
    assert "epp" not in turbo
    store = {"version": 1, "active": turbo["name"], "profiles": defaults}
    profiles_env.store.parent.mkdir(parents=True, exist_ok=True)
    profiles_env.store.write_text(json.dumps(store))
    profiles_env.max_fans.write_text(json.dumps({"enabled": False}))

    from z13ctl_client import Z13CtlClient
    name = Z13CtlClient.mapped_profile_name(turbo)
    points = [{"temp": t, "pwm": round(p * 255 / 100)} for t, p in turbo["fan_curve"]]
    named = {"name": name, "fan_curve": {"mode": 1, "points": points}}
    state_home = profiles_env.store.parents[2] / "xdg-state"
    state_dir = state_home / "z13ctl"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "state.json").write_text(json.dumps({
        "profile": name, "battery_limit": 80, "custom_profiles": {name: named}}))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    assert _check() == 10


def test_resume_owner_rejects_each_profile_state_guard(profiles_env, monkeypatch):
    # Rebuild the same passing baseline for each single-field mutation.
    for mutation in ("profile", "curve", "tdp", "epp", "battery"):
        app, named, state_path = _fixture(profiles_env, monkeypatch, tdp={"pl1": 60, "pl2": 65, "pl3": 70})
        state = json.loads(state_path.read_text())
        backend_name = next(iter(state["custom_profiles"]))
        if mutation == "profile":
            state["profile"] = "other"
        elif mutation == "curve":
            state["custom_profiles"][backend_name]["fan_curve"]["mode"] = 0
        elif mutation == "tdp":
            state["custom_profiles"][backend_name]["tdp"]["pl1_spl"] = 59
        elif mutation == "epp":
            app["epp"] = "performance"
        else:
            state["battery_limit"] = 79
        profiles_env.store.write_text(json.dumps({"version": 1, "active": app["name"], "profiles": [app]}))
        state_path.write_text(json.dumps(state))
        assert _check() == 0


def test_resume_owner_requires_curve_and_max_fans_details(profiles_env, monkeypatch):
    _fixture(profiles_env, monkeypatch, max_on=True)
    state_path = profiles_env.store.parents[2] / "xdg-state" / "z13ctl" / "state.json"
    baseline = json.loads(state_path.read_text())
    named_key = next(iter(baseline["custom_profiles"]))
    curve = baseline["custom_profiles"][named_key]["fan_curve"]
    profiles_env.max_fans.write_text('{"enabled":"true"}')
    assert _check() == 0
    profiles_env.max_fans.write_text('{"enabled":1}')
    assert _check() == 0
    profiles_env.max_fans.write_text('{"enabled":true}')

    curve["points"][0]["pwm"] = 254
    state_path.write_text(json.dumps(baseline))
    assert _check() == 0


def test_resume_owner_rejects_bad_points(profiles_env, monkeypatch):
    _fixture(profiles_env, monkeypatch)
    state_path = profiles_env.store.parents[2] / "xdg-state" / "z13ctl" / "state.json"
    baseline = json.loads(state_path.read_text())
    named_key = next(iter(baseline["custom_profiles"]))
    points = baseline["custom_profiles"][named_key]["fan_curve"]["points"]

    for index in (0, 2):
        state = copy.deepcopy(baseline)
        state["custom_profiles"][named_key]["fan_curve"]["points"][index]["pwm"] += 2
        state_path.write_text(json.dumps(state))
        assert _check() == 0
    for count in (7, 9):
        state = copy.deepcopy(baseline)
        state["custom_profiles"][named_key]["fan_curve"]["points"] = points[:count]
        if count == 9:
            state["custom_profiles"][named_key]["fan_curve"]["points"].append({"temp": 100, "pwm": 255})
        state_path.write_text(json.dumps(state))
        assert _check() == 0
    state = copy.deepcopy(baseline)
    state["custom_profiles"][named_key]["fan_curve"]["points"][1]["temp"] = points[0]["temp"]
    state_path.write_text(json.dumps(state))
    assert _check() == 0


def test_resume_owner_missing_or_corrupt_files_fall_back(profiles_env, monkeypatch):
    _fixture(profiles_env, monkeypatch)
    state_path = profiles_env.store.parents[2] / "xdg-state" / "z13ctl" / "state.json"
    files = (profiles_env.store, profiles_env.max_fans, state_path)
    originals = [p.read_text() for p in files]
    for path, original in zip(files, originals):
        path.unlink()
        assert _check() == 0
        path.write_text("{")
        assert _check() == 0
        path.write_text(original)


def test_resume_owner_tdp_present_only_in_z13ctl(profiles_env, monkeypatch):
    app, named, state_path = _fixture(profiles_env, monkeypatch, tdp=None)
    assert app.get("tdp") is None
    state = json.loads(state_path.read_text())
    backend_name = next(iter(state["custom_profiles"]))
    state["custom_profiles"][backend_name]["tdp"] = {
        "pl1_spl": 45, "pl2_sppt": 55, "fppt": 65, "apu_sppt": 45,
    }
    state_path.write_text(json.dumps(state))
    assert _check() == 0


def test_resume_owner_max_fans_points_count_rejects(profiles_env, monkeypatch):
    _fixture(profiles_env, monkeypatch, max_on=True)
    state_path = profiles_env.store.parents[2] / "xdg-state" / "z13ctl" / "state.json"
    baseline = json.loads(state_path.read_text())
    named_key = next(iter(baseline["custom_profiles"]))
    for temps in (
        (20, 30, 40, 50, 60, 70, 80),
        (20, 30, 40, 50, 60, 70, 80, 85, 90),
    ):
        state = copy.deepcopy(baseline)
        state["custom_profiles"][named_key]["fan_curve"]["points"] = [
            {"temp": t, "pwm": 255} for t in temps
        ]
        state_path.write_text(json.dumps(state))
        assert _check() == 0


def test_resume_owner_explicit_battery_limit(profiles_env, monkeypatch):
    app, named, state_path = _fixture(profiles_env, monkeypatch)
    app["battery_limit"] = 60
    profiles_env.store.write_text(json.dumps({"version": 1, "active": app["name"], "profiles": [app]}))

    state = json.loads(state_path.read_text())
    state["battery_limit"] = 60
    state_path.write_text(json.dumps(state))
    assert _check() == 10

    state["battery_limit"] = 80
    state_path.write_text(json.dumps(state))
    assert _check() == 0


def test_resume_owner_epp_setting(profiles_env, monkeypatch):
    app, named, state_path = _fixture(profiles_env, monkeypatch)
    app["epp"] = "performance"
    profiles_env.store.write_text(json.dumps({"version": 1, "active": app["name"], "profiles": [app]}))
    assert _check() == 0

    app["epp"] = None
    profiles_env.store.write_text(json.dumps({"version": 1, "active": app["name"], "profiles": [app]}))
    assert _check() == 10


def test_resume_owner_does_not_use_socket_subprocess_or_host_commands(profiles_env, monkeypatch):
    _fixture(profiles_env, monkeypatch)
    def forbidden(*args, **kwargs):
        raise AssertionError("resume-owner attempted external I/O")
    monkeypatch.setattr(socket, "socket", forbidden)
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        if hasattr(subprocess, name):
            monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(host, "run", forbidden)
    assert _check() == 10


def _tdp_fixture(profiles_env, monkeypatch):
    app, named, state_path = _fixture(
        profiles_env, monkeypatch, tdp={"pl1": 60, "pl2": 65, "pl3": 70}
    )
    state = json.loads(state_path.read_text())
    backend_name = next(iter(state["custom_profiles"]))
    state["custom_profiles"][backend_name]["tdp"] = {
        "pl1_spl": 60,
        "pl2_sppt": 65,
        "fppt": 70,
        "apu_sppt": 45,
        "platform_sppt": 55,
    }
    state_path.write_text(json.dumps(state))
    return app, state_path, backend_name


def test_resume_owner_tdp_positive(profiles_env, monkeypatch):
    _tdp_fixture(profiles_env, monkeypatch)
    assert _check() == 10


def test_resume_owner_tdp_negative_pl1_spl(profiles_env, monkeypatch):
    app, state_path, backend_name = _tdp_fixture(profiles_env, monkeypatch)
    state = json.loads(state_path.read_text())
    state["custom_profiles"][backend_name]["tdp"]["pl1_spl"] = 61
    state_path.write_text(json.dumps(state))
    assert _check() == 0


def test_resume_owner_tdp_negative_pl2_sppt(profiles_env, monkeypatch):
    app, state_path, backend_name = _tdp_fixture(profiles_env, monkeypatch)
    state = json.loads(state_path.read_text())
    state["custom_profiles"][backend_name]["tdp"]["pl2_sppt"] = 66
    state_path.write_text(json.dumps(state))
    assert _check() == 0


def test_resume_owner_tdp_negative_fppt(profiles_env, monkeypatch):
    app, state_path, backend_name = _tdp_fixture(profiles_env, monkeypatch)
    state = json.loads(state_path.read_text())
    state["custom_profiles"][backend_name]["tdp"]["fppt"] = 71
    state_path.write_text(json.dumps(state))
    assert _check() == 0


def test_resume_owner_tdp_negative_values_swapped(profiles_env, monkeypatch):
    app, state_path, backend_name = _tdp_fixture(profiles_env, monkeypatch)
    state = json.loads(state_path.read_text())
    state["custom_profiles"][backend_name]["tdp"]["pl1_spl"] = 65
    state["custom_profiles"][backend_name]["tdp"]["pl2_sppt"] = 60
    state_path.write_text(json.dumps(state))
    assert _check() == 0


def test_resume_owner_tdp_negative_non_dict(profiles_env, monkeypatch):
    app, state_path, backend_name = _tdp_fixture(profiles_env, monkeypatch)
    state = json.loads(state_path.read_text())
    state["custom_profiles"][backend_name]["tdp"] = [60, 65, 70]
    state_path.write_text(json.dumps(state))
    assert _check() == 0

    state["custom_profiles"][backend_name]["tdp"] = "60"
    state_path.write_text(json.dumps(state))
    assert _check() == 0


def test_resume_owner_tdp_negative_backend_none(profiles_env, monkeypatch):
    app, state_path, backend_name = _tdp_fixture(profiles_env, monkeypatch)
    state = json.loads(state_path.read_text())
    state["custom_profiles"][backend_name]["tdp"] = None
    state_path.write_text(json.dumps(state))
    assert _check() == 0


def test_resume_owner_missing_app_curve_max_fans_on(profiles_env, monkeypatch):
    for missing_mode in ("none", "absent"):
        app, named, state_path = _fixture(profiles_env, monkeypatch, max_on=True)
        if missing_mode == "none":
            app["fan_curve"] = None
        else:
            del app["fan_curve"]
        profiles_env.store.write_text(
            json.dumps({"version": 1, "active": app["name"], "profiles": [app]})
        )
        assert _check() == 0


def test_resume_owner_missing_app_curve_max_fans_off(profiles_env, monkeypatch):
    for missing_mode in ("none", "absent"):
        app, named, state_path = _fixture(profiles_env, monkeypatch, max_on=False)
        if missing_mode == "none":
            app["fan_curve"] = None
        else:
            del app["fan_curve"]
        profiles_env.store.write_text(
            json.dumps({"version": 1, "active": app["name"], "profiles": [app]})
        )
        assert _check() == 0


def test_resume_owner_fan_curve_pwm_pm1_boundary(profiles_env, monkeypatch):
    app, named, state_path = _fixture(profiles_env, monkeypatch, max_on=False)
    baseline = json.loads(state_path.read_text())
    backend_name = next(iter(baseline["custom_profiles"]))

    for delta, expected_rc in ((1, 10), (-1, 10), (2, 0)):
        state = copy.deepcopy(baseline)
        state["custom_profiles"][backend_name]["fan_curve"]["points"][0]["pwm"] += delta
        state_path.write_text(json.dumps(state))
        assert _check() == expected_rc

