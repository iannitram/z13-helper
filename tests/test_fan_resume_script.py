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
    ctl_log = tmp_path / "z13ctl.txt"
    (stubs / "runuser").write_text(RUNUSER_STUB)
    (stubs / "logger").write_text(f'#!/bin/sh\necho "$*" >> "{logger_log}"\n')
    (stubs / "sleep").write_text("#!/bin/sh\nexit 0\n")
    for f in stubs.iterdir():
        f.chmod(0o755)

    def write_ctl(rc=0, log=ctl_log):
        p = home / ".local" / "bin" / "z13ctl"
        p.write_text(executable_stub("Z13CTL_LOG", "z13ctl") + f"exit {rc}\n")
        p.chmod(0o755)
        return p

    def write_profile(rc=0, log=None):
        p = home / ".local" / "bin" / "z13-profile"
        p.write_text(executable_stub("PROFILE_LOG", "z13-profile") + f"exit {rc}\n")
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
    assert "re-applied active profile" in env.logger_log.read_text()


def test_fallback_curve_set_runs_as_deck_not_root(env):
    # No z13-profile at all: forces the fallback path (the one issue #5 found unsafe).
    env.write_ctl(rc=0)
    p = env()
    assert p.returncode == 0, p.stderr
    log = env.ctl_log.read_text()
    assert "ran as deck via runuser" in log
    assert "ran DIRECTLY" not in log
    assert "fancurve --set" in log
    assert "profile restore unavailable; re-applied curve" in env.logger_log.read_text()


def test_fallback_reset_runs_as_deck_not_root(env):
    # z13ctl present but --set fails -> the reset call is the one issue #5 flagged
    # as still unconditional (no -x/runuser guard) even after a first-pass fix.
    env.write_ctl(rc=1)
    p = env()
    assert p.returncode == 0, p.stderr
    log = env.ctl_log.read_text()
    assert log.count("ran as deck via runuser") == 2          # --set attempt, then --reset
    assert "ran DIRECTLY" not in log
    assert "fancurve --reset" in log
    assert "WARN curve re-apply FAILED" in env.logger_log.read_text()


def test_missing_z13ctl_fallback_does_not_crash(env):
    # Neither profile nor z13ctl installed: must fall through to the hwmon branch
    # (root-owned sysfs writes, not a user-writable executable) without erroring.
    p = env()
    assert p.returncode == 0, p.stderr
    assert "WARN curve re-apply FAILED" in env.logger_log.read_text()
