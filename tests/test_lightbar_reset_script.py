"""packaging/host/systemd/system/z13-lightbar-reset.sh against a fake sysfs tree.

The one place the suite runs a real process: the POSIX script itself, with
Z13_LIGHTBAR_SYSFS pointing at tmp_path and `logger`/`sleep` stubbed on PATH
(conftest's `real_subprocess` opt-in). Nothing outside tmp_path is read or written.
"""

import os
import shutil
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "packaging/host/systemd/system/z13-lightbar-reset.sh"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs /bin/sh")


def usb_dev(root, port, vid, pid, authorized="1"):
    d = root / "bus" / "usb" / "devices" / port
    d.mkdir(parents=True)
    (d / "idVendor").write_text(vid + "\n")
    (d / "idProduct").write_text(pid + "\n")
    (d / "authorized").write_text(authorized + "\n")
    return d


@pytest.fixture
def env(tmp_path, real_subprocess):
    sysfs = tmp_path / "sys"
    (sysfs / "bus" / "usb" / "devices").mkdir(parents=True)
    stubs = tmp_path / "bin"
    stubs.mkdir()
    log = tmp_path / "logger.txt"
    trace = tmp_path / "during-sleep.txt"
    (stubs / "logger").write_text(f'#!/bin/sh\necho "$*" >> "{log}"\n')
    # `sleep` runs between the 0 and the 1 write: record what authorized held then.
    (stubs / "sleep").write_text(
        f'#!/bin/sh\nfor f in "{sysfs}"/bus/usb/devices/*/authorized; do '
        f'echo "$f=$(cat "$f")" >> "{trace}"; done\n')
    for f in stubs.iterdir():
        f.chmod(0o755)

    def run():
        e = {"PATH": f"{stubs}:{os.environ.get('PATH', '/usr/bin:/bin')}",
             "Z13_LIGHTBAR_SYSFS": str(sysfs)}
        return real_subprocess(["sh", str(SCRIPT)], env=e, capture_output=True, text=True, timeout=20)

    run.sysfs, run.log, run.trace = sysfs, log, trace
    return run


def test_syntax(real_subprocess):
    assert real_subprocess(["sh", "-n", str(SCRIPT)], timeout=10).returncode == 0


def test_resets_only_the_lightbar(env):
    keyboard = usb_dev(env.sysfs, "1-2", "0b05", "1a30")
    lightbar = usb_dev(env.sysfs, "3-5", "0b05", "18c6")
    (env.sysfs / "bus/usb/devices/3-5:1.0").mkdir()          # interface dir: no IDs
    other = usb_dev(env.sysfs, "3-6", "18c6", "0b05")         # swapped IDs: not it
    p = env()
    assert p.returncode == 0, p.stderr
    assert (lightbar / "authorized").read_text().strip() == "1"
    trace = env.trace.read_text()
    assert f"{lightbar}/authorized=0" in trace                 # de-authorized during the pause
    assert f"{keyboard}/authorized=1" in trace and f"{other}/authorized=1" in trace
    assert (keyboard / "authorized").read_text() == "1\n"      # untouched
    assert (other / "authorized").read_text() == "1\n"
    assert "-t z13-lightbar-reset reset lightbar 3-5 (0b05:18c6)" in env.log.read_text()


def test_port_is_discovered_not_hard_coded(env):
    lightbar = usb_dev(env.sysfs, "1-4", "0b05", "18c6", authorized="1")
    assert env().returncode == 0
    assert "reset lightbar 1-4" in env.log.read_text()
    assert (lightbar / "authorized").read_text().strip() == "1"


def test_not_found_exits_nonzero(env):
    usb_dev(env.sysfs, "1-2", "0b05", "1a30")
    p = env()
    assert p.returncode == 1
    assert "not found" in env.log.read_text()
    assert not env.trace.exists()


def test_two_matches_refused(env):
    a = usb_dev(env.sysfs, "3-5", "0b05", "18c6")
    b = usb_dev(env.sysfs, "3-7", "0b05", "18c6")
    p = env()
    assert p.returncode == 2
    assert (a / "authorized").read_text() == "1\n" and (b / "authorized").read_text() == "1\n"


def test_unwritable_authorized_fails(env):
    d = usb_dev(env.sysfs, "3-5", "0b05", "18c6")
    (d / "authorized").unlink()
    (d / "authorized").mkdir()           # a write fails even as root
    assert env().returncode == 3
    assert "could not de-authorize 3-5" in env.log.read_text()
