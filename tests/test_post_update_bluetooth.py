"""Built-in post-update recipe 5: MT7925 Bluetooth workarounds (issue #71)."""

import pytest

from post_update.bluetooth import (
    BT_FILES,
    KEEP_CONF,
    bluetooth_recipe,
)
from post_update.builtin import HostIO, KEEP_DIR
from post_update.runner import Env, run_plan

OS_RELEASE = "/etc/os-release"
DMI_PRODUCT = "/sys/class/dmi/id/product_name"
STEAMOS = 'ID=steamos\nVERSION_ID=3.7\nBUILD_ID="20260101"\n'
GZ302 = "ROG Flow Z13 GZ302EA\n"

SAMPLE_UDEV = 'ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="13d3", ATTR{idProduct}=="3608", ATTR{power/control}="on"\n'
SAMPLE_CYCLE_SERVICE = "[Unit]\nDescription=Cycle BT\n[Service]\nExecStart=/bin/sh\n"
SAMPLE_RESUME_SERVICE = "[Unit]\nDescription=BT Resume\n[Service]\nExecStart=/etc/systemd/system/mt7925-bt-resume.sh\n"
SAMPLE_RESUME_SCRIPT = "#!/bin/sh\necho on > /sys/bus/usb/devices/3-3/power/control\n"

SAMPLE_SOURCES = {
    "packaging/host/udev/50-mt7925-bt-no-autosuspend.rules": SAMPLE_UDEV,
    "packaging/host/systemd/system/bt-mt7925-cycle.service": SAMPLE_CYCLE_SERVICE,
    "packaging/host/systemd/system/mt7925-bt-resume.service": SAMPLE_RESUME_SERVICE,
    "packaging/host/systemd/system/mt7925-bt-resume.sh": SAMPLE_RESUME_SCRIPT,
}

SAMPLE_KEEP_CONF = (
    "# Z13 Bucket 1\n"
    "/etc/udev/rules.d/50-mt7925-bt-no-autosuspend.rules\n"
    "/etc/systemd/system/bt-mt7925-cycle.service\n"
    "/etc/systemd/system/mt7925-bt-resume.service\n"
    "/etc/systemd/system/mt7925-bt-resume.sh\n"
)


class FakeHost:
    def __init__(self, files=None, listing=None, runs=None):
        self.files = dict(files or {})
        self.listing = dict(listing or {})
        self.runs_table = dict(runs or {})
        self.writes = []
        self.commands = []

    def io(self):
        def read(path):
            return self.files.get(path)

        def write(path, text):
            self.writes.append((path, text))
            self.files[path] = text

        def run(argv):
            self.commands.append(list(argv))
            return self.runs_table.get(tuple(argv), (0, ""))

        return HostIO(
            read=read,
            write=write,
            exists=lambda p: p in self.files,
            listdir=lambda d: self.listing.get(d, []),
            run=run,
        )


def make_host(*, steamos=True, gz302=True, files=None, listing=None):
    base = {
        OS_RELEASE: STEAMOS if steamos else "ID=arch\n",
        DMI_PRODUCT: GZ302 if gz302 else "Standard PC\n",
    }
    base.update(SAMPLE_SOURCES)
    if files:
        base.update(files)
    list_map = {KEEP_DIR: ["z13-bucket1.conf"]}
    if listing:
        list_map.update(listing)
    return FakeHost(files=base, listing=list_map)


def healthy_host():
    installed = {
        "/etc/udev/rules.d/50-mt7925-bt-no-autosuspend.rules": SAMPLE_UDEV,
        "/etc/systemd/system/bt-mt7925-cycle.service": SAMPLE_CYCLE_SERVICE,
        "/etc/systemd/system/mt7925-bt-resume.service": SAMPLE_RESUME_SERVICE,
        "/etc/systemd/system/mt7925-bt-resume.sh": SAMPLE_RESUME_SCRIPT,
        f"{KEEP_DIR}/z13-bucket1.conf": SAMPLE_KEEP_CONF,
    }
    return make_host(files=installed)


def test_bluetooth_recipe_structure():
    h = make_host()
    r = bluetooth_recipe(h.io())
    assert r.id == "mt7925-bt"
    assert r.title == "MT7925 Bluetooth workarounds"
    assert r.fixes == "A2DP and HID drops, connect-time race after resume"
    assert r.sudo is True
    assert r.default_on is True
    assert (r.network, r.reboot, r.pacman) == (False, False, False)
    assert r.replaces == tuple(dest for _, dest in BT_FILES)


def test_check_returns_na_when_not_supported_device():
    h = make_host(gz302=False)
    assert bluetooth_recipe(h.io()).check() == "na"
    assert h.writes == [] and h.commands == []


def test_check_returns_ok_when_all_files_match_and_registered():
    h = healthy_host()
    assert bluetooth_recipe(h.io()).check() == "ok"
    assert h.writes == [] and h.commands == []


@pytest.mark.parametrize("missing_dest", [dest for _, dest in BT_FILES])
def test_check_returns_needs_when_destination_file_missing(missing_dest):
    h = healthy_host()
    del h.files[missing_dest]
    assert bluetooth_recipe(h.io()).check() == "needs"


def test_check_returns_needs_when_destination_file_differs():
    h = healthy_host()
    h.files["/etc/systemd/system/bt-mt7925-cycle.service"] = "# stale config\n"
    assert bluetooth_recipe(h.io()).check() == "needs"


def test_check_returns_needs_when_keep_list_registration_missing():
    h = healthy_host()
    h.files[f"{KEEP_DIR}/z13-bucket1.conf"] = "# Empty keep list\n"
    assert bluetooth_recipe(h.io()).check() == "needs"


def test_check_is_strictly_read_only():
    h = make_host()  # missing all installed files
    res = bluetooth_recipe(h.io()).check()
    assert res == "needs"
    assert h.writes == []
    assert h.commands == []


def test_apply_refuses_when_not_supported_device():
    h = make_host(steamos=False)
    with pytest.raises(RuntimeError, match="not SteamOS on a GZ302"):
        bluetooth_recipe(h.io()).apply()
    assert h.writes == []


def test_apply_copies_missing_and_differing_files():
    h = make_host()  # No files installed yet
    r = bluetooth_recipe(h.io())
    r.apply()
    for src, dest in BT_FILES:
        assert h.files.get(dest) == SAMPLE_SOURCES[src]


def test_apply_registers_missing_paths_in_keep_list():
    h = make_host()
    r = bluetooth_recipe(h.io())
    r.apply()
    keep_text = h.files.get(f"{KEEP_DIR}/z13-bucket1.conf", "")
    for _, dest in BT_FILES:
        assert dest in keep_text


def test_apply_is_idempotent():
    h = healthy_host()
    r = bluetooth_recipe(h.io())
    r.apply()
    assert h.writes == []
    assert h.commands == []


def test_apply_runs_cleanly_through_runner():
    h = make_host()
    backed = []
    env = Env(
        backup=backed.append,
        readonly_disable=lambda: None,
        readonly_enable=lambda: None,
    )
    result = run_plan([bluetooth_recipe(h.io())], env)
    assert result == [("mt7925-bt", "done")]
    for src, dest in BT_FILES:
        assert h.files.get(dest) == SAMPLE_SOURCES[src]
