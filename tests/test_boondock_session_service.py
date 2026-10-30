"""Boondock owns only the user llama-swap service during a session."""

import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tooling" / "boondock"


@pytest.fixture
def session_env(tmp_path):
    stubs = tmp_path / "bin"
    stubs.mkdir()
    systemctl = stubs / "systemctl"
    systemctl.write_text("""#!/usr/bin/env python3
import os, pathlib, sys
args = sys.argv[1:]
pathlib.Path(os.environ['SYSTEMCTL_LOG']).open('a').write(' '.join(args) + '\\n')
if args[:2] != ['--user', 'is-active'] and args[:2] != ['--user', 'stop'] and args[:2] != ['--user', 'start']:
    sys.exit(2)
state = pathlib.Path(os.environ['SERVICE_STATE'])
if args[1] == 'is-active':
    sys.exit(0 if state.read_text().strip() == 'active' else 3)
state.write_text('active' if args[1] == 'start' else 'inactive')
""")
    systemctl.chmod(0o755)
    sudo = stubs / "sudo"
    sudo.write_text("#!/bin/sh\nexec \"$@\"\n")
    sudo.chmod(0o755)
    backlight = tmp_path / "backlight"
    backlight.mkdir()
    (backlight / "brightness").write_text("80\n")
    (backlight / "max_brightness").write_text("100\n")
    return {
        **os.environ,
        "PATH": f"{stubs}:/usr/bin:/bin",
        "BOONDOCK_STATE": str(tmp_path / "state"),
        "BOONDOCK_BACKLIGHT": str(backlight),
        "BOONDOCK_PPT_DIR": str(tmp_path / "unused-ppt"),
        "BOONDOCK_PROFILE_SYSFS": str(tmp_path / "unused-profile"),
        "SYSTEMCTL_LOG": str(tmp_path / "systemctl.log"),
        "SERVICE_STATE": str(tmp_path / "service-state"),
    }


def run(real_subprocess, env, command):
    return real_subprocess(["bash", str(SCRIPT), command], env=env, capture_output=True,
                           text=True, timeout=10, check=True)


def test_active_user_service_is_snapshotted_stopped_and_restored(real_subprocess, session_env):
    env = session_env
    Path(env["SERVICE_STATE"]).write_text("active\n")

    run(real_subprocess, env, "session-on")
    snapshot = Path(env["BOONDOCK_STATE"]).read_text()
    assert "svc=llama-swap" in snapshot
    assert Path(env["SERVICE_STATE"]).read_text().strip() == "inactive"

    run(real_subprocess, env, "session-off")
    assert Path(env["SERVICE_STATE"]).read_text() == "active"
    calls = Path(env["SYSTEMCTL_LOG"]).read_text().splitlines()
    assert "--user stop llama-swap" in calls
    assert "--user start llama-swap" in calls
    assert not Path(env["BOONDOCK_STATE"]).exists()


def test_inactive_service_is_not_snapshotted_or_started_on_restore(real_subprocess, session_env):
    env = session_env
    Path(env["SERVICE_STATE"]).write_text("inactive\n")

    run(real_subprocess, env, "session-on")
    assert "svc=" not in Path(env["BOONDOCK_STATE"]).read_text()
    assert Path(env["SERVICE_STATE"]).read_text().strip() == "inactive"

    run(real_subprocess, env, "session-off")
    assert Path(env["SERVICE_STATE"]).read_text().strip() == "inactive"
    calls = Path(env["SYSTEMCTL_LOG"]).read_text().splitlines()
    assert "--user stop llama-swap" not in calls
    assert "--user start llama-swap" not in calls


def test_legacy_container_and_vm_snapshot_keys_are_discarded_without_actions(real_subprocess, session_env):
    env = session_env
    Path(env["BOONDOCK_STATE"]).write_text(
        "brightness=80\nsvc=llama-swap\nctr=old-container\nvm=old-vm\n"
    )
    Path(env["SERVICE_STATE"]).write_text("inactive\n")

    run(real_subprocess, env, "session-off")

    assert not Path(env["BOONDOCK_STATE"]).exists()
    calls = Path(env["SYSTEMCTL_LOG"]).read_text().splitlines()
    assert "--user start llama-swap" in calls
    assert all("docker" not in call and "virsh" not in call for call in calls)
