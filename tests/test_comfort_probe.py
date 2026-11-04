"""Phase 15e probe tooling: candidate readings are read-only, the fit ranks sensors."""

import z13_comfort_probe as probe
from sensor_reader import SensorReader


def make_reader(tmp_path, monkeypatch):
    import sensor_reader
    root = tmp_path / "hwmon"
    for i, (name, temp) in enumerate([("k10temp", 61500), ("acpitz", 48000), ("nvme", 39850)]):
        d = root / f"hwmon{i}"
        d.mkdir(parents=True)
        (d / "name").write_text(name + "\n")
        (d / "temp1_input").write_text(f"{temp}\n")
    monkeypatch.setattr(sensor_reader, "HWMON_ROOT", str(root))
    return SensorReader()


def test_candidates_read_hwmon_and_missing_is_none(tmp_path, monkeypatch):
    got = make_reader(tmp_path, monkeypatch).get_comfort_candidates()
    assert got["tctl"] == 61.5 and got["acpitz"] == 48.0 and got["nvme"] == 39.85
    assert got["gpu_edge"] is None
    assert set(got) == set(probe.CANDIDATES)


def test_sensor_reader_never_names_armoury_files():
    src = open(__import__("sensor_reader").__file__).read()
    assert "ppt_" not in src and "current_value" not in src


def test_sample_appends_rows_with_header_once(tmp_path):
    out = str(tmp_path / "x" / "p.csv")
    probe.append_row(out, "idle", 31.0, {"tctl": 45.0, "acpitz": None}, now=100)
    probe.append_row(out, "game", 38.5, {"tctl": 70.0}, now=200)
    rows = probe.read_rows(out)
    assert [r["phase"] for r in rows] == ["idle", "game"]
    assert rows[0]["acpitz"] == "" and rows[1]["tctl"] == "70.0"


def test_fit_recovers_offset_and_ranks_by_rmse():
    rows = [{"external_c": str(20 + 0.4 * t), "tctl": str(t),
             "acpitz": str(t + (5 if i % 2 else -5))}
            for i, t in enumerate([40, 50, 60, 70, 80])]
    ranked = probe.fit(rows)
    best = ranked[0]
    assert best[0] == "tctl"
    assert abs(best[1] - 20) < 1e-6 and abs(best[2] - 0.4) < 1e-6 and best[3] < 1e-6
    assert ranked[1][0] == "acpitz" and ranked[1][3] > 1


def test_fit_skips_thin_or_flat_candidates():
    rows = [{"external_c": "30", "tctl": "50"}] * 5 + [{"external_c": "31", "nvme": "40"}]
    assert probe.fit(rows) == []
