"""Resume restore skips only when daemon state and live fan state are complete."""

import json

import profiles


class FakeClient:
    def __init__(self, state=None, error=None):
        self.state = state
        self.error = error
        self.reads = 0

    def get_state(self):
        self.reads += 1
        if self.error:
            raise self.error
        return self.state


def setup_restore(profiles_env, monkeypatch, *, backend_profile=None, mode="manual",
                  max_fans=False, points=None, state_error=None):
    profile = next(p for p in profiles.default_profiles() if p["name"] == "Balanced")
    profiles.save_store({"version": 1, "active": "Balanced", "profiles": [profile]})
    profiles_env.max_fans.write_text(json.dumps({"enabled": max_fans}))
    client = FakeClient({"profile": ""}, state_error)
    # Match through the public mapping helper used by the production guard.
    from z13ctl_client import Z13CtlClient
    active = profiles.get_profile("Balanced")
    client.state["profile"] = backend_profile or Z13CtlClient.mapped_profile_name(active)
    monkeypatch.setattr(profiles.authority, "named_client", lambda: client)

    if points is None:
        points = [(t, 255, 100) for t in range(8)]
    monkeypatch.setattr("hardware_controller.HardwareController.get_fancurve",
                        lambda self: {"mode": mode, "points": points})
    applies = []
    monkeypatch.setattr(profiles, "apply_by_name", lambda name, **kwargs: applies.append(name) or [])
    return client, applies


def test_restore_if_needed_skips_when_all_guards_hold(profiles_env, monkeypatch, capsys):
    client, applies = setup_restore(profiles_env, monkeypatch)

    assert profiles.main(["restore", "--if-needed"]) == 0

    assert client.reads == 1
    assert applies == []
    assert "already restored by z13ctl; skipped" in capsys.readouterr().out


def test_restore_if_needed_applies_when_named_profile_differs(profiles_env, monkeypatch):
    _, applies = setup_restore(profiles_env, monkeypatch, backend_profile="z13h-some-other-profile")

    assert profiles.main(["restore", "--if-needed"]) == 0

    assert applies == ["Balanced"]


def test_restore_if_needed_applies_when_curve_is_auto(profiles_env, monkeypatch):
    _, applies = setup_restore(profiles_env, monkeypatch, mode="auto")

    assert profiles.main(["restore", "--if-needed"]) == 0

    assert applies == ["Balanced"]


def test_restore_if_needed_applies_when_max_fans_curve_is_not_full(profiles_env, monkeypatch):
    points = [(t, 254 if i == 4 else 255, 99 if i == 4 else 100)
              for i, t in enumerate(range(8))]
    _, applies = setup_restore(profiles_env, monkeypatch, max_fans=True, points=points)

    assert profiles.main(["restore", "--if-needed"]) == 0

    assert applies == ["Balanced"]


def test_restore_if_needed_applies_when_daemon_state_cannot_be_read(profiles_env, monkeypatch):
    _, applies = setup_restore(profiles_env, monkeypatch, state_error=OSError("offline"))

    assert profiles.main(["restore", "--if-needed"]) == 0

    assert applies == ["Balanced"]


def test_restore_if_needed_restores_when_max_fans_state_is_unreadable(profiles_env, monkeypatch):
    _, applies = setup_restore(profiles_env, monkeypatch)
    profiles_env.max_fans.unlink()

    assert profiles.main(["restore", "--if-needed"]) == 0

    assert applies == ["Balanced"]
