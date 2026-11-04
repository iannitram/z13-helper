"""Tab rows and status after a SteamOS update (issue #70)."""

import json

from post_update import driver, tab
from post_update.builtin import HostIO
from post_update.recipe import Recipe

def make_io(files=None, run=None, listing=None):
    files = files if files is not None else {}
    return HostIO(read=files.get, write=lambda p, t: files.__setitem__(p, t),
                  exists=lambda p: p in files, listdir=lambda p: (listing or {}).get(p, []),
                  run=run or (lambda argv: (0, "")))


SUPPORTED = {"/etc/os-release": "ID=steamos\nBUILD_ID=20261101.1\n",
             "/sys/class/dmi/id/product_name": "GZ302EA\n",
             "/proc/sys/kernel/osrelease": "7.2.7\n"}


def test_rows_show_state_flags_error_rows_and_survive_a_failing_check():
    def boom():
        raise PermissionError("needs root")
    rs = [Recipe("a", "A", "fa", lambda: "needs", lambda: None, sudo=True, reboot=True),
          Recipe("b", "B", "fb", boom, lambda: None),
          Recipe("c", "C", "fc", lambda: "ok", lambda: None)]
    rows = tab.build_rows(rs, ["x.toml: invalid TOML: oops"])
    assert [(r["id"], r["state"]) for r in rows] == [("a", "needs"), ("b", "unknown"), ("c", "fine"), ("", "error")]
    assert rows[0]["flags"] == ["sudo", "reboot"] and "needs root" in rows[1]["detail"]
    assert rows[3]["title"] == "x.toml" and rows[3]["detail"] == "invalid TOML: oops"


def test_status_after_build_id_change_shows_banner_and_armoury(tmp_path):
    home = str(tmp_path)
    arm = {"/sys/class/firmware-attributes/asus-armoury/attributes": ["ppt_pl1_spl"]}
    io = make_io(dict(SUPPORTED, **{"/etc/os-release": "BUILD_ID=1\n", "/proc/sys/kernel/osrelease": "7.2\n"}),
                 listing=arm)
    first = tab.status(home, io, [])
    assert first["updated"] is False and first["banner"] is None and first["armoury"] is None
    io2 = make_io(dict(SUPPORTED, **{"/etc/os-release": "BUILD_ID=2\n"}), listing=arm)
    s = tab.status(home, io2, [])
    assert s["updated"] and s["banner"] == "SteamOS updated: 0 tasks (BUILD_ID 1 to 2)"
    assert s["armoury"]["state"] == "needs" and "ppt_pl2_sppt" in s["armoury"]["missing"]
    assert tab.status(home, io2, [])["updated"] is True        # remembered
    assert tab.status(home, io2, [], ack=True)["updated"] is False
    assert tab.status(home, io2, [])["updated"] is False


def test_status_kernel_major_trigger(tmp_path):
    home = str(tmp_path)
    tab.status(home, make_io({"/etc/os-release": "BUILD_ID=1\n", "/proc/sys/kernel/osrelease": "6.18.0\n"}), [])
    s = tab.status(home, make_io({"/etc/os-release": "BUILD_ID=1\n", "/proc/sys/kernel/osrelease": "7.2.7\n"}), [])
    assert s["updated"] and "kernel 6.x to 7.x" in s["reason"]


def test_tab_json_prints_json_and_changes_nothing_else(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    rs = [Recipe("a", "A", "fa", lambda: "needs", lambda: (_ for _ in ()).throw(AssertionError("ran")))]
    monkeypatch.setattr(driver, "make_hostio", lambda escalate=True: make_io(dict(SUPPORTED)))
    assert driver.main(["--tab-json"], recipes=rs) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["rows"][0]["id"] == "a" and data["rows"][0]["state"] == "needs"
