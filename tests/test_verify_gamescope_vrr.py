import json

import pytest

import verify_gamescope_vrr as verify


def edid(vendor="TMA", product=0x0803, model="TL134ADXP03"):
    data = bytearray(128)
    data[:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    code = sum((ord(char) - 64) << shift for char, shift in zip(vendor, (10, 5, 0)))
    data[8:10] = code.to_bytes(2, "big")
    data[10:12] = product.to_bytes(2, "little")
    data[54:72] = b"\x00\x00\x00\xfc\x00" + (model + "\n").encode().ljust(13, b" ")
    data[127] = -sum(data[:127]) % 256
    return bytes(data)


@pytest.fixture
def device(tmp_path):
    config = tmp_path / "home" / ".config"
    installed = config / verify.PROFILE
    installed.parent.mkdir(parents=True)
    installed.write_bytes(verify.SOURCE.read_bytes())
    drm = tmp_path / "drm"
    connector = drm / "card1-eDP-1"
    connector.mkdir(parents=True)
    (connector / "status").write_text("connected\n")
    (connector / "edid").write_bytes(edid())
    os_release = tmp_path / "os-release"
    os_release.write_text('ID=steamos\nVERSION_ID="3.9.5"\nBUILD_ID=example\n')
    proc = tmp_path / "proc"
    (proc / "12").mkdir(parents=True)
    (proc / "12" / "comm").write_text("gamescope\n")
    return dict(config_home=config, drm=drm, os_release=os_release, proc=proc,
                gamescope_version=verify.GAMESCOPE_VERSION)


def test_preflight_never_signs_off_physical_acceptance(device):
    report = verify.collect(**device)
    assert report["preflight_blockers"] == []
    assert report["installed_profile"] == "matches"
    assert report["panels"][0]["matches_profile"]
    assert report["device_acceptance"] == "pending owner-device verification"
    checks = report["manual_checks"]
    assert {(row["disable_frame_limiter"], row["refresh_hz"]) for row in checks} == {
        (disabled, hz) for disabled in (False, True) for hz in (48, 60, 120, 180)}
    assert all(row["result"] == "pending" and row["vrr"] == "on" for row in checks)
    assert all(row[k] is None for row in checks
               for k in ("slider_reach", "panel_stable", "measured_fps"))
    assert [row["requested_limit_hz"] for row in checks] == [
        None if row["disable_frame_limiter"] else row["refresh_hz"] for row in checks]
    assert "serial" not in json.dumps(report).lower().replace("serial_number", "")
    assert "serial" not in json.dumps(report["panels"]).lower()


@pytest.mark.parametrize("vendor,product,model", [
    ("ABC", 0x0803, "TL134ADXP03"), ("TMA", 0x0804, "TL134ADXP03"),
    ("TMA", 0x0803, "other"),
])
def test_wrong_panel_blocks_preflight(device, vendor, product, model):
    (device["drm"] / "card1-eDP-1/edid").write_bytes(edid(vendor, product, model))
    report = verify.collect(**device)
    assert not report["panels"][0]["matches_profile"]
    assert report["preflight_blockers"]


@pytest.mark.parametrize("data", [b"", edid()[:100], b"x" * 128, edid()[:-1] + b"\0"])
def test_invalid_edid_is_not_a_match(data):
    assert verify.panel_identity(data) is None


def test_missing_context_and_drift_are_reported(device):
    (device["config_home"] / verify.PROFILE).write_text("stale script")
    (device["os_release"]).write_text("ID=steamos\nVERSION_ID=3.8.28\n")
    (device["proc"] / "12/comm").unlink()
    (device["drm"] / "card1-eDP-1/status").write_text("disconnected")
    report = verify.collect(**device)
    assert report["installed_profile"] == "differs"
    assert len(report["preflight_blockers"]) == 4
    assert "expected SteamOS 3.9.5, found steamos 3.8.28" in report["preflight_blockers"]
    (device["config_home"] / verify.PROFILE).unlink()
    assert verify.collect(**device)["installed_profile"] == "unreadable or missing"


def test_cli_reports_blockers_without_writing(monkeypatch, capsys, device):
    home = device["config_home"].parent
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "elsewhere"))
    collect = verify.collect
    seen = []
    def fake_collect(config, gamescope_version=None):
        seen.append((config, gamescope_version))
        return collect(**{**device, "config_home": config,
                          "gamescope_version": gamescope_version})

    monkeypatch.setattr(verify, "collect", fake_collect)
    monkeypatch.setattr(verify, "installed_gamescope_version", lambda: verify.GAMESCOPE_VERSION)
    before = sorted(home.parent.rglob("*"))
    assert verify.main([]) == 0
    assert seen == [(device["config_home"], verify.GAMESCOPE_VERSION)]
    assert json.loads(capsys.readouterr().out)["device_acceptance"].startswith("pending")
    (device["config_home"] / verify.PROFILE).unlink()
    assert verify.main([]) == 1
    assert json.loads(capsys.readouterr().out)["preflight_blockers"]
    assert sorted(home.parent.rglob("*")) == [p for p in before if p.exists()]


def test_newer_steamos_blocks(device):
    device["os_release"].write_text('ID=steamos\nVERSION_ID="3.10.0"\n')
    report = verify.collect(**device)
    assert any("found newer steamos 3.10.0" in b for b in report["preflight_blockers"])


@pytest.mark.parametrize("version", ["3.16.30.1-3", None])
def test_gamescope_version_mismatch_blocks(device, version):
    device["gamescope_version"] = version
    assert any("Gamescope" in b for b in verify.collect(**device)["preflight_blockers"])


def test_missing_checkout_source_is_not_blamed_on_installed(device, tmp_path):
    report = verify.collect(**device, source=tmp_path / "nope.lua")
    assert report["installed_profile"] == "checkout source missing"
    assert "Installed display script does not match this checkout" not in report["preflight_blockers"]


@pytest.mark.parametrize("value,blocked", [
    ("3.9.5-preview", False), ("3.9", True), ("3.10", True), ("junk", True)])
def test_version_suffix_and_short_forms(device, value, blocked):
    device["os_release"].write_text(f"ID=steamos\nVERSION_ID={value}\n")
    report = verify.collect(**device)
    assert bool(report["preflight_blockers"]) == blocked
