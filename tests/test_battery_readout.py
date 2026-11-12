"""battery_readout: sysfs BAT0 reader, EMA rate smoothing, ETA to the charge limit.

Everything runs against a fake power_supply tree in tmp_path; nothing touches
/sys or the real battery.
"""

from datetime import datetime

import pytest

import battery_readout as br

NOW = datetime(2026, 11, 12, 9, 0, 0)


def make_bat(root, **files):
    root.mkdir(parents=True, exist_ok=True)
    for name, value in files.items():
        (root / name).write_text(f"{value}\n")
    return root


@pytest.fixture
def bat(tmp_path):
    return tmp_path / "BAT0"


# --- reader ----------------------------------------------------------------

def test_read_sample_energy_files(bat):
    make_bat(bat, status="Discharging", capacity="57", power_now="18200000",
             energy_now="40000000", energy_full="70000000")
    s = br.read_sample(str(bat))
    assert s["status"] == "Discharging"
    assert s["capacity"] == 57
    assert s["power_w"] == pytest.approx(18.2)
    assert s["energy_now_wh"] == pytest.approx(40.0)
    assert s["energy_full_wh"] == pytest.approx(70.0)


def test_read_sample_charge_files_use_voltage(bat):
    make_bat(bat, status="Charging", capacity="30", power_now="0",
             charge_now="5000000", charge_full="5800000", voltage_now="12000000")
    s = br.read_sample(str(bat))
    assert s["energy_now_wh"] == pytest.approx(60.0)
    assert s["energy_full_wh"] == pytest.approx(69.6)


def test_read_sample_power_falls_back_to_current_times_voltage(bat):
    make_bat(bat, status="Discharging", power_now="0", current_now="2000000",
             voltage_now="12000000", energy_now="40000000", energy_full="70000000")
    assert br.read_sample(str(bat))["power_w"] == pytest.approx(24.0)


def test_read_sample_missing_battery_is_none(tmp_path):
    assert br.read_sample(str(tmp_path / "nope")) is None


# --- signed rate -----------------------------------------------------------

@pytest.mark.parametrize("status, expected", [
    ("Discharging", -18.2),
    ("Charging", 6.4),
    ("Full", 0.0),
    ("Not charging", 0.0),
])
def test_signed_rate_follows_status(bat, status, expected):
    power = "18200000" if status == "Discharging" else "6400000"
    make_bat(bat, status=status, power_now=power, energy_now="40000000", energy_full="70000000")
    if status in ("Full", "Not charging"):
        assert br.signed_rate_w(br.read_sample(str(bat))) == expected
    else:
        assert br.signed_rate_w(br.read_sample(str(bat))) == pytest.approx(expected)


# --- EMA smoothing ---------------------------------------------------------

def test_smoother_seeds_then_moves_gradually():
    sm = br.RateSmoother(tau_s=45.0)
    assert sm.update(-20.0, 0.0, "Discharging") == pytest.approx(-20.0)
    # a load spike to -4 W: after 3 s it has moved only a little toward it
    first = sm.update(-4.0, 3.0, "Discharging")
    assert -20.0 < first < -18.0
    # sustained -4 W for 6 minutes (~8 time constants): converged
    value = first
    for t in range(6, 361, 3):
        value = sm.update(-4.0, float(t), "Discharging")
    assert value == pytest.approx(-4.0, abs=0.2)


def test_smoother_resets_when_status_changes():
    sm = br.RateSmoother(tau_s=45.0)
    sm.update(-20.0, 0.0, "Discharging")
    # plugged in: no lag through the old sign
    assert sm.update(6.0, 3.0, "Charging") == pytest.approx(6.0)


# --- ETA and state ---------------------------------------------------------

def test_draining_eta_to_empty(bat):
    make_bat(bat, status="Discharging", power_now="20000000",
             energy_now="40000000", energy_full="70000000")
    s = br.read_sample(str(bat))
    r = br.readout(s, br.signed_rate_w(s), None, NOW)
    assert r["state"] == "Draining"
    assert r["rate_w"] == pytest.approx(-20.0)
    assert r["text"] == "Draining -20.0 W · Empty at 11:00am, in 2 h 0 m"


def test_charging_eta_targets_limit_not_100(bat):
    make_bat(bat, status="Charging", power_now="26000000",
             energy_now="30000000", energy_full="70000000")
    s = br.read_sample(str(bat))
    r = br.readout(s, br.signed_rate_w(s), 80, NOW)
    # target 80% of 70 Wh = 56 Wh; need 26 Wh at +26 W = 1 h
    assert r["state"] == "Charging"
    assert r["text"] == "Charging +26.0 W · Full (80%) at 10:00am, in 1 h 0 m"


def test_at_limit_is_holding_with_no_eta(bat):
    make_bat(bat, status="Charging", power_now="3000000",
             energy_now="57000000", energy_full="70000000")
    s = br.read_sample(str(bat))
    r = br.readout(s, br.signed_rate_w(s), 80, NOW)
    assert r["state"] == "Holding"
    assert r["eta_text"] is None
    assert "80%" in r["text"]


def test_near_zero_rate_says_so_without_eta(bat):
    make_bat(bat, status="Discharging", power_now="400000",
             energy_now="40000000", energy_full="70000000")
    s = br.read_sample(str(bat))
    r = br.readout(s, br.signed_rate_w(s), None, NOW)
    assert r["state"] == "Holding"
    assert r["eta_text"] is None
    assert "near zero" in r["text"]
    assert "Empty" not in r["text"]


def test_eta_over_a_day_is_not_shown(bat):
    make_bat(bat, status="Discharging", power_now="1500000",
             energy_now="60000000", energy_full="70000000")
    s = br.read_sample(str(bat))
    r = br.readout(s, br.signed_rate_w(s), None, NOW)
    assert r["eta_text"] is None
    assert "over 24 h" in r["text"]


def test_unavailable_when_no_sample():
    r = br.readout(None, 0.0, None, NOW)
    assert r["state"] == "Unavailable"
    assert r["text"] == "Battery readout unavailable"


# --- formatting ------------------------------------------------------------

@pytest.mark.parametrize("hour, minute, text", [
    (7, 0, "7:00am"),
    (16, 45, "4:45pm"),
    (0, 30, "12:30am"),
    (12, 5, "12:05pm"),
])
def test_clock_is_twelve_hour(hour, minute, text):
    assert br.format_clock(datetime(2026, 11, 12, hour, minute)) == text


@pytest.mark.parametrize("seconds, text", [
    (30, "<1 m"),
    (2280, "38 m"),
    (5820, "1 h 37 m"),
    (3600, "1 h 0 m"),
])
def test_duration_text(seconds, text):
    assert br.format_duration(seconds) == text
