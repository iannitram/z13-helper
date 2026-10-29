"""profiles.apply() on a profile with no fan curve and Max Fans off (issue #60: NameError on `tdp`)."""

import copy

import profiles

Z = "z13ctl"


def _profile(tdp, **over):
    p = {"name": "NoCurve", "base": "balanced", "tdp": tdp, "fan_curve": None, "undervolt": None,
         "brightness": None, "battery_limit": None, "hooks": {"enter": "", "exit": ""}}
    p.update(over)
    return p


def test_tdp_dict_runs_profile_then_tdp_then_fancurve_reset(profiles_env, fake_host):
    p = _profile({"pl1": 50, "pl2": 60, "pl3": 70, "force": False})
    before = copy.deepcopy(p)
    results = profiles.apply(copy.deepcopy(p), z13ctl=Z)
    assert fake_host.argvs == [
        [Z, "profile", "--set", "balanced"],
        [Z, "tdp", "--set", "50", "--pl2", "60", "--pl3", "70"],
        [Z, "fancurve", "--reset"],
    ]
    assert ("fans", True, "firmware auto") in results
    assert p == before


def test_tdp_none_runs_no_tdp_and_no_fancurve_reset(profiles_env, fake_host):
    results = profiles.apply(_profile(None), z13ctl=Z)
    assert fake_host.argvs == [[Z, "profile", "--set", "balanced"]]
    assert ("fans", True, "firmware auto") in results


def test_tdp_key_missing_runs_no_tdp_and_no_fancurve_reset(profiles_env, fake_host):
    p = _profile(None)
    del p["tdp"]
    results = profiles.apply(p, z13ctl=Z)
    assert fake_host.argvs == [[Z, "profile", "--set", "balanced"]]
    assert ("fans", True, "firmware auto") in results


def test_leaving_max_fans_profile_resets_fans_with_release_message(profiles_env, fake_host):
    import json
    prev = _profile(None, name="Turbo", max_fans=True)
    nxt = _profile(None)
    # Max Fans is ON (set by the previous profile); the next profile has no max_fans flag.
    profiles_env.max_fans.parent.mkdir(parents=True)
    profiles_env.max_fans.write_text(json.dumps({"enabled": True}))
    results = profiles.apply(nxt, previous=prev, z13ctl=Z)
    assert [Z, "fancurve", "--reset"] in fake_host.argvs
    assert ("fans", True, "Max Fans OFF, firmware auto") in results
