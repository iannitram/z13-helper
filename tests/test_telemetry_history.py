"""telemetry_history.TelemetryHistory: bounded, in memory, gaps kept as None (#31)."""

import pytest

from telemetry_history import FIELDS, MIN_INTERVAL_S, TelemetryHistory, WINDOW_S


def test_records_and_returns_series_oldest_first():
    h = TelemetryHistory()
    h.add(0, cpu=40, gpu=35, fan1=2000, fan2=2100)
    h.add(2, cpu=42, gpu=36, fan1=2100, fan2=2200)
    assert h.series("cpu") == [(2, 40), (0, 42)]            # seconds ago, oldest first
    assert h.series("fan2") == [(2, 2100), (0, 2200)]
    assert h.span_s() == 2


def test_missing_values_stay_none_not_zero():
    h = TelemetryHistory()
    h.add(0, cpu=None, gpu=35, fan1=None, fan2=None)
    assert h.series("cpu") == [(0, None)] and h.series("fan1") == [(0, None)]


def test_samples_closer_than_min_interval_are_skipped():
    h = TelemetryHistory()
    assert h.add(0, cpu=1)
    assert not h.add(MIN_INTERVAL_S - 0.1, cpu=2)
    assert h.add(MIN_INTERVAL_S, cpu=3)
    assert len(h) == 2


def test_old_samples_age_out_of_the_window():
    h = TelemetryHistory(window_s=100, min_interval_s=1)
    for t in range(0, 301, 2):
        h.add(t, cpu=t)
    ages = [a for a, _ in h.series("cpu")]
    assert max(ages) <= 100 and len(h) <= 51


def test_memory_stays_bounded_with_a_hostile_clock():
    h = TelemetryHistory(window_s=60, min_interval_s=1)
    for i in range(10_000):
        h.add(i * 1.0, cpu=1)
        h.add(5.0, cpu=1)                                     # clock stuck in the past: never grows
    assert len(h) <= int(60 / 1) + 16


def test_default_hour_is_about_2400_samples_at_most():
    h = TelemetryHistory()
    for i in range(5000):
        h.add(i * MIN_INTERVAL_S, cpu=i)
    assert len(h) <= int(WINDOW_S / MIN_INTERVAL_S) + 16
    assert h.span_s() <= WINDOW_S


def test_empty_history():
    h = TelemetryHistory()
    assert h.series("cpu") == [] and h.span_s() == 0.0 and len(h) == 0


def test_unknown_field_is_an_error():
    with pytest.raises(ValueError):
        TelemetryHistory().series("nope")


def test_fields_match_add_signature():
    assert FIELDS == ("cpu", "gpu", "fan1", "fan2")


def test_nothing_is_written_to_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    h = TelemetryHistory()
    for i in range(20):
        h.add(i * 2.0, cpu=i, gpu=i, fan1=i, fan2=i)
    assert list(tmp_path.iterdir()) == []
