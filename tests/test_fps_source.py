"""fps_source.read(): MangoHud CSV tail, staleness, bounded and read-only."""

import os
import time

import fps_source

HEADER = "os,cpu,gpu,ram,kernel,driver,cpuscheduler\nSteamOS,APU,APU,32,7.2,radv,none\n"
COLS = "fps,frametime,cpu_load,gpu_load\n"


def log(tmp_path, rows, name="game.csv", header=HEADER + COLS, end="\n", age=0.0):
    p = tmp_path / name
    p.write_text(header + "\n".join(rows) + end)
    t = time.time() - age
    os.utime(p, (t, t))
    return p


def test_reads_last_complete_row(tmp_path):
    log(tmp_path, ["60.0,16.6,10,50", "58.5,17.1,11,52"])
    assert fps_source.read(log_dirs=[str(tmp_path)]) == 58.5


def test_half_written_last_row_is_ignored(tmp_path):
    log(tmp_path, ["60.0,16.6,10,50", "58.5,17.1"], end="")
    assert fps_source.read(log_dirs=[str(tmp_path)]) == 60.0


def test_stale_log_means_no_game(tmp_path):
    log(tmp_path, ["60.0,16.6,10,50"], age=30)
    assert fps_source.read(log_dirs=[str(tmp_path)]) is None


def test_no_logs_or_missing_dir_is_none(tmp_path):
    assert fps_source.read(log_dirs=[str(tmp_path)]) is None
    assert fps_source.read(log_dirs=[str(tmp_path / "nope")]) is None


def test_newest_log_wins(tmp_path):
    log(tmp_path, ["30.0,33,1,1"], name="old.csv", age=1.0)
    log(tmp_path, ["90.0,11,1,1"], name="new.csv")
    assert fps_source.read(log_dirs=[str(tmp_path)]) == 90.0


def test_garbage_and_header_only_are_none(tmp_path):
    p = log(tmp_path, ["abc,def"])
    assert fps_source.read(log_dirs=[str(tmp_path)]) is None
    p.write_text("not a mangohud log\n")
    assert fps_source.read(log_dirs=[str(tmp_path)]) is None
    p.write_text(HEADER + COLS)
    assert fps_source.read(log_dirs=[str(tmp_path)]) is None


def test_absurd_value_is_none(tmp_path):
    log(tmp_path, ["99999,1,1,1"])
    assert fps_source.read(log_dirs=[str(tmp_path)]) is None


def test_big_log_reads_tail_only_and_fast(tmp_path):
    rows = [f"{50 + i % 10}.0,20,1,1" for i in range(50000)] + ["72.0,13.9,1,1"]
    log(tmp_path, rows)
    t0 = time.monotonic()
    assert fps_source.read(log_dirs=[str(tmp_path)]) == 72.0
    assert time.monotonic() - t0 < fps_source.DEADLINE_S


def test_deadline_gives_none(tmp_path, monkeypatch):
    log(tmp_path, ["60.0,16.6,10,50"])
    monkeypatch.setattr(fps_source, "DEADLINE_S", -1.0)
    assert fps_source.read(log_dirs=[str(tmp_path)]) is None


def test_read_only_and_no_subprocess(tmp_path):
    src = open(fps_source.__file__).read()
    assert "subprocess" not in src and "host.run" not in src
    assert "ppt_" not in src and "current_value" not in src.replace("Armoury", "")
