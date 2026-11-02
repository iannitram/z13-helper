"""Crash-report file name, report.json and zip (issue #82, part b)."""

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

import crash_report as cr

NOW = datetime(2026, 10, 31, 17, 42, 34, 978000, tzinfo=timezone.utc)
KW = dict(version="0.5.1", commit="34a1284", os_name="SteamOS 3.9.5", kernel="7.2.7-valve2",
          python="3.13.1", now=NOW, install_id="9f2c")


def boom():
    try:
        raise ValueError("bad slider")
    except ValueError as e:
        return e


def test_drop_filename_matches_the_shared_drop_pattern():
    assert cr.drop_filename("linux", NOW, "0a1b2c3d") == "z13-helper-linux-20261031T174234Z-0a1b2c3d.zip"


def test_drop_filename_converts_to_utc_and_strips_platform():
    local = NOW.astimezone(timezone(timedelta(hours=-6)))
    assert cr.drop_filename("Linux x86_64!", local, "0a1b2c3d") == \
        "z13-helper-Linuxx8664-20261031T174234Z-0a1b2c3d.zip"


@pytest.mark.parametrize("args", [("linux", NOW, "ABCDEF12"), ("linux", NOW, "abc"),
                                  ("!!", NOW, "0a1b2c3d"), ("linux", NOW.replace(tzinfo=None), "0a1b2c3d")])
def test_drop_filename_rejects_bad_input(args):
    with pytest.raises(ValueError):
        cr.drop_filename(*args)


def test_log_tail_keeps_the_last_n_lines():
    text = "".join(f"line {i}\n" for i in range(700))
    out = cr.log_tail(text)
    assert out.splitlines()[0] == "line 200" and out.splitlines()[-1] == "line 699"
    assert len(out.splitlines()) == 500
    assert cr.log_tail("a\nb\nc\n", 2) == "b\nc\n"
    assert cr.log_tail("") == ""


def test_build_report_fields():
    rep = cr.build_report(boom(), **KW)
    assert set(rep) == {"app", "version", "commit", "os", "kernel", "python", "exception_type",
                        "message", "traceback", "timestamp", "install_id"}
    assert (rep["app"], rep["version"], rep["commit"], rep["os"]) == ("z13-helper", "0.5.1", "34a1284", "SteamOS 3.9.5")
    assert (rep["kernel"], rep["python"], rep["install_id"]) == ("7.2.7-valve2", "3.13.1", "9f2c")
    assert (rep["exception_type"], rep["message"]) == ("ValueError", "bad slider")
    assert rep["timestamp"] == "2026-10-31T17:42:34Z"
    assert "Traceback" in rep["traceback"] and "ValueError: bad slider" in rep["traceback"]


def test_build_zip_holds_report_json_and_the_log_tail():
    rep = cr.build_report(boom(), **KW)
    log = "".join(f"l{i}\n" for i in range(600))
    with zipfile.ZipFile(io.BytesIO(cr.build_zip(rep, log))) as z:
        assert sorted(z.namelist()) == ["app.log", "report.json"]
        assert json.loads(z.read("report.json")) == rep
        assert z.read("app.log").decode().splitlines() == [f"l{i}" for i in range(100, 600)]
