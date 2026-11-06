"""packaging/host/systemd/system/z13-fan-resume.sh: issue #5 regression.

The service runs this script as root with no User=. Every stand-in for a
deck-owned executable (z13-profile, z13ctl) only sets ROOT_DROPPED when it
is invoked through the `runuser` stub, so a reintroduced direct call (root
executing a user-writable binary) makes these tests fail loudly instead of
silently passing. Z13_FAN_RESUME_DECK_HOME/_DECK_USER point the script at a
fake tree (conftest's `real_subprocess` opt-in); nothing outside tmp_path or
the stubbed PATH is read, written, or executed.
"""

import os
import shutil
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "packaging/host/systemd/system/z13-fan-resume.sh"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs /bin/sh")

RUNUSER_STUB = """#!/bin/sh
# Mimics real runuser's argv shape for this script's one call site:
#   runuser -u <user> -- env HOME=<home> XDG_RUNTIME_DIR=<rt> <program> <args...>
echo "runuser $*" >> "$RUNUSER_LOG"
user="$2"
shift 3
ROOT_DROPPED="$user" exec "$@"
"""


def executable_stub(log_var, name):
    return (
        "#!/bin/sh\n"
        f'if [ "$ROOT_DROPPED" = "deck" ]; then\n'
        f'    echo "{name} ran as deck via runuser: $*" >> "${log_var}"\n'
        "else\n"
        f'    echo "{name} ran DIRECTLY (uid $(id -u), ROOT_DROPPED=${{ROOT_DROPPED:-unset}}): $*" >> "${log_var}"\n'
        "fi\n"
    )


@pytest.fixture
def env(tmp_path, real_subprocess):
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".config" / "z13-helper").mkdir(parents=True)
    stubs = tmp_path / "bin"
    stubs.mkdir()
    logger_log = tmp_path / "logger.txt"
    sleep_log = tmp_path / "sleep.txt"
    ctl_log = tmp_path / "z13ctl.txt"
    (stubs / "runuser").write_text(RUNUSER_STUB)
    (stubs / "logger").write_text(f'#!/bin/sh\necho "$*" >> "{logger_log}"\n')
    (stubs / "sleep").write_text(f'#!/bin/sh\necho "$1" >> "{sleep_log}"\nexit 0\n')
    for f in stubs.iterdir():
        f.chmod(0o755)

    def write_ctl(rc=0, log=ctl_log):
        p = home / ".local" / "bin" / "z13ctl"
        p.write_text(executable_stub("Z13CTL_LOG", "z13ctl") + f"exit {rc}\n")
        p.chmod(0o755)
        return p

    def write_profile(rc=0, log=None, old_cli=False, output="", skip=False):
        p = home / ".local" / "bin" / "z13-profile"
        old = 'case "$*" in *--check-only*) exit 2;; esac\n' if old_cli else ""
        printed = f'printf "%s\\n" {output!r}\n' if output else ""
        check = 'case "$*" in *--check-only*) exit 10;; esac\n' if skip else ""
        p.write_text(executable_stub("PROFILE_LOG", "z13-profile") + old + check + printed + f"exit {rc}\n")
        p.chmod(0o755)
        (home / ".config" / "z13-helper" / "profiles.json").write_text("{}")
        return p

    def run():
        e = {
            "PATH": f"{stubs}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "Z13_FAN_RESUME_DECK_HOME": str(home),
            "Z13_FAN_RESUME_DECK_USER": "deck",
            "RUNUSER_LOG": str(tmp_path / "runuser.txt"),
            "Z13CTL_LOG": str(ctl_log),
            "PROFILE_LOG": str(tmp_path / "profile.txt"),
        }
        return real_subprocess(["sh", str(SCRIPT)], env=e, capture_output=True, text=True, timeout=20)

    run.home, run.stubs = home, stubs
    run.write_ctl, run.write_profile = write_ctl, write_profile
    run.logger_log = logger_log
    run.sleep_log = sleep_log
    run.runuser_log = tmp_path / "runuser.txt"
    run.ctl_log, run.profile_log = ctl_log, tmp_path / "profile.txt"
    return run


def test_syntax(real_subprocess):
    assert real_subprocess(["sh", "-n", str(SCRIPT)], timeout=10).returncode == 0


def test_profile_restore_runs_as_deck_not_root(env):
    env.write_profile(rc=0)
    p = env()
    assert p.returncode == 0, p.stderr
    assert "ran as deck via runuser" in env.profile_log.read_text()
    assert "ran DIRECTLY" not in env.profile_log.read_text()
    assert "hook re-applied the active profile" in env.logger_log.read_text()
    assert "restore --if-needed --check-only" in env.profile_log.read_text()
    assert "restore\n" in env.profile_log.read_text()


def test_fallback_never_writes_pwm_enable_sysfs():
    # Principle 1: the last-resort fallback goes through z13ctl, never sysfs.
    text = SCRIPT.read_text()
    assert "pwm*_enable" not in text
    assert "echo 2 >" not in text


def test_failed_restore_does_not_write_a_curve(env):
    # restore ran (rc=1, e.g. a wedged lightbar) but one step failed: the profile
    # curve it applied must stand. Only a non-running restore uses the fallback.
    env.write_profile(rc=1)
    env.write_ctl(rc=0)
    p = env()
    assert p.returncode == 0, p.stderr
    log = env.ctl_log.read_text()
    assert "fancurve --set" not in log
    assert "fancurve --reset" not in log
    assert "fancurve --get" in log
    assert "ran DIRECTLY" not in log
    assert "profile restore ran with a failed step" in env.logger_log.read_text()


def test_fallback_curve_set_runs_as_deck_not_root(env):
    # No z13-profile at all: forces the fallback path (the one issue #5 found unsafe).
    env.write_ctl(rc=0)
    p = env()
    assert p.returncode == 0, p.stderr
    log = env.ctl_log.read_text()
    assert "ran as deck via runuser" in log
    assert "ran DIRECTLY" not in log
    assert "fancurve --set" in log
    assert "20:100%,30:100%,40:100%,50:100%,60:100%,70:100%,80:100%,90:100%" in log
    assert "profile restore unavailable; re-applied curve" in env.logger_log.read_text()


def test_fallback_set_failure_never_resets_firmware_auto(env):
    # Unknown profile/Max Fans state must not fall through to firmware auto.
    env.write_ctl(rc=1)
    p = env()
    assert p.returncode == 0, p.stderr
    log = env.ctl_log.read_text()
    assert log.count("ran as deck via runuser") == 1
    assert "ran DIRECTLY" not in log
    assert "fancurve --reset" not in log
    assert "WARN protective curve re-apply FAILED; left curve state untouched" in env.logger_log.read_text()


def test_missing_z13ctl_fallback_does_not_crash(env):
    # Neither profile nor z13ctl installed: must fall through to the hwmon branch
    # (root-owned sysfs writes, not a user-writable executable) without erroring.
    p = env()
    assert p.returncode == 0, p.stderr
    assert "WARN protective curve re-apply FAILED; left curve state untouched" in env.logger_log.read_text()


def test_daemon_skip_is_logged_without_plain_restore(env):
    env.write_profile(skip=True)
    env.profile_log.write_text("")
    p = env()
    assert p.returncode == 0, p.stderr
    assert "restore --if-needed --check-only" in env.profile_log.read_text()
    assert "restore\n" not in env.profile_log.read_text()
    assert "z13ctl daemon restored the profile; hook skipped (exit 10)" in env.logger_log.read_text()


def test_poll_is_bounded_to_about_eight_seconds(env):
    env.write_profile()
    p = env()
    assert p.returncode == 0, p.stderr
    assert env.profile_log.read_text().count("restore --if-needed --check-only") == 5
    assert env.sleep_log.read_text().splitlines() == ["4", "1", "1", "1", "1"]


def test_old_profile_cli_falls_back_to_plain_restore(env):
    env.write_profile(rc=0, old_cli=True)
    p = env()
    assert p.returncode == 0, p.stderr
    log = env.profile_log.read_text()
    assert "restore --if-needed --check-only" in log
    assert "restore\n" in log
    assert "hook re-applied the active profile" in env.logger_log.read_text()
