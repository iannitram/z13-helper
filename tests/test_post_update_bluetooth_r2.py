"""Built-in post-update recipe 5, round 2: MT7925 Bluetooth apply() fixes (issue #71)."""

import re

import pytest

from post_update import driver
from post_update.bluetooth import BT_FILES, KEEP_CONF, bluetooth_recipe
from post_update.builtin import KEEP_DIR

OS_RELEASE = "/etc/os-release"
DMI_PRODUCT = "/sys/class/dmi/id/product_name"
STEAMOS = 'ID=steamos\nVERSION_ID=3.7\nBUILD_ID="20260101"\n'
GZ302 = "ROG Flow Z13 GZ302EA\n"

SCRIPT_DEST = "/etc/systemd/system/mt7925-bt-resume.sh"
UDEV_DEST = "/etc/udev/rules.d/50-mt7925-bt-no-autosuspend.rules"
CYCLE_DEST = "/etc/systemd/system/bt-mt7925-cycle.service"
RESUME_DEST = "/etc/systemd/system/mt7925-bt-resume.service"

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

DAEMON_RELOAD = ["systemctl", "daemon-reload"]
ENABLE = ["systemctl", "enable", "bt-mt7925-cycle.service", "mt7925-bt-resume.service"]
UDEV_RELOAD = ["udevadm", "control", "--reload"]
CHMOD_SCRIPT = ["chmod", "755", SCRIPT_DEST]


class FakeHost:
    def __init__(self, files=None, listing=None, runs=None):
        self.files = dict(files or {})
        self.listing = dict(listing or {})
        self.runs_table = dict(runs or {})
        self.writes = []
        self.commands = []

    def io(self):
        from post_update.builtin import HostIO

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


def make_host(*, files=None, runs=None):
    base = {OS_RELEASE: STEAMOS, DMI_PRODUCT: GZ302}
    base.update(SAMPLE_SOURCES)
    if files:
        base.update(files)
    return FakeHost(files=base, listing={KEEP_DIR: ["z13-bucket1.conf"]}, runs=runs)


def installed_except(*missing):
    """Every workaround file in place (and the keep-list), except the destinations named."""
    installed = {
        UDEV_DEST: SAMPLE_UDEV,
        CYCLE_DEST: SAMPLE_CYCLE_SERVICE,
        RESUME_DEST: SAMPLE_RESUME_SERVICE,
        SCRIPT_DEST: SAMPLE_RESUME_SCRIPT,
    }
    for dest in missing:
        del installed[dest]
    installed[f"{KEEP_DIR}/z13-bucket1.conf"] = "\n".join(installed_paths(installed)) + "\n"
    return installed


def installed_paths(installed):
    return [p for p in (UDEV_DEST, CYCLE_DEST, RESUME_DEST, SCRIPT_DEST) if p in installed]


# 1. KEEP_CONF: a missing newline at the end of the existing text is added before new paths.

def test_keep_list_append_starts_on_its_own_line_when_text_has_no_trailing_newline():
    files = installed_except(CYCLE_DEST, RESUME_DEST)
    files[f"{KEEP_DIR}/z13-bucket1.conf"] = "# Z13 Bucket 1\n" + UDEV_DEST  # no final newline
    h = make_host(files=files)
    bluetooth_recipe(h.io()).apply()
    lines = h.files[KEEP_CONF].splitlines()
    assert UDEV_DEST in lines
    assert CYCLE_DEST in lines
    assert RESUME_DEST in lines
    for line in lines:
        assert line.count("/etc/") <= 1, f"two paths glued on one line: {line!r}"


def test_keep_list_append_is_unchanged_when_text_already_ends_with_newline():
    files = installed_except(CYCLE_DEST)
    files[f"{KEEP_DIR}/z13-bucket1.conf"] = "# Z13 Bucket 1\n" + UDEV_DEST + "\n" + RESUME_DEST + "\n" + SCRIPT_DEST + "\n"
    h = make_host(files=files)
    bluetooth_recipe(h.io()).apply()
    assert h.files[KEEP_CONF] == (
        "# Z13 Bucket 1\n" + UDEV_DEST + "\n" + RESUME_DEST + "\n" + SCRIPT_DEST + "\n" + CYCLE_DEST + "\n"
    )


# 2. The resume script is made executable after it is written.

def test_script_is_chmod_755_after_being_written():
    h = make_host()  # nothing installed: the script gets written
    bluetooth_recipe(h.io()).apply()
    assert (SCRIPT_DEST, SAMPLE_RESUME_SCRIPT) in h.writes
    assert CHMOD_SCRIPT in h.commands


def test_no_chmod_when_script_was_already_current():
    h = make_host(files=installed_except(UDEV_DEST, CYCLE_DEST, RESUME_DEST))
    bluetooth_recipe(h.io()).apply()
    assert h.writes  # other files were written
    assert CHMOD_SCRIPT not in h.commands


# 3. After a write: daemon-reload, enable both units, reload udev, in that order.

def test_fresh_install_runs_chmod_then_reloads_enables_and_reloads_udev_in_order():
    h = make_host()
    bluetooth_recipe(h.io()).apply()
    assert h.commands == [CHMOD_SCRIPT, DAEMON_RELOAD, ENABLE, UDEV_RELOAD]


def test_partial_install_without_script_runs_reload_enable_udev_only():
    h = make_host(files=installed_except(UDEV_DEST, CYCLE_DEST, RESUME_DEST))
    bluetooth_recipe(h.io()).apply()
    assert h.commands == [DAEMON_RELOAD, ENABLE, UDEV_RELOAD]


def test_healthy_host_runs_no_commands_on_apply():
    h = make_host(files=installed_except())
    bluetooth_recipe(h.io()).apply()
    assert h.writes == []
    assert h.commands == []


# 4. A failing command raises RuntimeError naming it, and nothing after it runs.

@pytest.mark.parametrize("argv, named", [
    (CHMOD_SCRIPT, "chmod 755"),
    (DAEMON_RELOAD, "systemctl daemon-reload"),
    (ENABLE, "systemctl enable"),
    (UDEV_RELOAD, "udevadm control --reload"),
])
def test_failing_command_raises_runtimeerror_naming_it(argv, named):
    h = make_host(runs={tuple(argv): (1, "boom")})
    with pytest.raises(RuntimeError, match=re.escape(named)):
        bluetooth_recipe(h.io()).apply()
    position = h.commands.index(list(argv))
    assert h.commands == h.commands[: position + 1], "a command ran after the failing one"


# 5. Privilege: the new root commands go through sudo -n.

@pytest.mark.parametrize("argv", [
    ["chmod", "755", SCRIPT_DEST],
    ["systemctl", "daemon-reload"],
    ["systemctl", "enable", "bt-mt7925-cycle.service", "mt7925-bt-resume.service"],
    ["udevadm", "control", "--reload"],
])
def test_bluetooth_root_commands_need_root(argv):
    assert driver._needs_root(argv) is True


def test_plain_commands_do_not_need_root():
    assert driver._needs_root(["cat", "/etc/os-release"]) is False
