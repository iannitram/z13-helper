"""Boondock's profile hooks must not redo the hardware the profile engine just set.

The engine applies base profile, TDP, fans etc. and THEN runs the enter hook, so a
hook that selects a firmware profile or writes TDP again overwrites the profile's
own settings. These tests run the built-in Boondock profile's real hook command
against the real tooling/boondock script with PATH stubs and assert it issues no
z13ctl profile/tdp/fancurve writes and reads no PPT / platform_profile values.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import profiles

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tooling" / "boondock"

LOGGED = '#!/bin/sh\necho "$(basename "$0") $*" >> "$STUB_LOG"\n'
STUBS = {
    "z13ctl": LOGGED,
    "sudo": LOGGED + 'case "$1" in tee) cat >/dev/null;; esac\nexit 0\n',
    "systemctl": LOGGED + "exit 3\n",          # nothing active
    "docker": LOGGED + "exit 0\n",
    "virsh": LOGGED + "exit 1\n",
    "cat": '#!/bin/sh\necho "cat $*" >> "$STUB_LOG"\nexec /usr/bin/cat "$@"\n',
}

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or not os.path.exists("/usr/bin/cat"), reason="needs bash")


@pytest.fixture
def env(tmp_path):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in STUBS.items():
        (stubs / name).write_text(body)
        (stubs / name).chmod(0o755)
    bl = tmp_path / "bl"
    bl.mkdir()
    (bl / "max_brightness").write_text("1000\n")
    (bl / "brightness").write_text("800\n")
    ppt = tmp_path / "ppt"
    ppt.mkdir()
    for f in ("ppt_pl1_spl", "ppt_pl2_sppt", "ppt_fppt"):
        (ppt / f).write_text("30\n")
    (tmp_path / "platform_profile").write_text("balanced\n")
    return {
        "HOME": str(tmp_path), "PATH": f"{stubs}:/usr/bin:/bin", "LANG": "C.UTF-8",
        "STUB_LOG": str(tmp_path / "stub.log"),
        "BOONDOCK_Z13CTL": str(stubs / "z13ctl"),
        "BOONDOCK_STATE": str(tmp_path / "boondock.state"),
        "BOONDOCK_BACKLIGHT": str(bl),
        "BOONDOCK_PPT_DIR": str(ppt),
        "BOONDOCK_PROFILE_SYSFS": str(tmp_path / "platform_profile"),
    }


def run_hook(real_subprocess, env, which):
    """Run the built-in Boondock profile's enter/exit hook with the script swapped in."""
    boondock = next(p for p in profiles.default_profiles() if p["name"] == "Boondock")
    cmd = boondock["hooks"][which].replace(profiles.BOONDOCK_BIN, f"bash {SCRIPT}")
    p = real_subprocess(["bash", "-c", cmd], env=env, capture_output=True, text=True, timeout=30,
                        stdin=subprocess.DEVNULL)
    log = Path(env["STUB_LOG"])
    return p, (log.read_text().splitlines() if log.exists() else [])


def hardware_touches(log):
    bad = [l for l in log if l.startswith("z13ctl ") and any(
        w in l.split()[1:2] for w in ("profile", "tdp", "fancurve", "undervolt"))]
    bad += [l for l in log if l.startswith("cat ") and ("ppt_" in l or "platform_profile" in l)]
    return bad


def test_enter_hook_touches_no_power_hardware(real_subprocess, env):
    p, log = run_hook(real_subprocess, env, "enter")
    assert p.returncode == 0, p.stdout + p.stderr
    assert hardware_touches(log) == []


def test_exit_hook_touches_no_power_hardware(real_subprocess, env):
    run_hook(real_subprocess, env, "enter")
    Path(env["STUB_LOG"]).unlink()
    p, log = run_hook(real_subprocess, env, "exit")
    assert p.returncode == 0, p.stdout + p.stderr
    assert hardware_touches(log) == []


def test_hook_still_dims_screen_and_exit_restores(real_subprocess, env):
    run_hook(real_subprocess, env, "enter")
    log = Path(env["STUB_LOG"]).read_text()
    assert "sudo tee" in log                         # brightness write
    run_hook(real_subprocess, env, "exit")
    assert not Path(env["BOONDOCK_STATE"]).exists()  # snapshot consumed


def test_manual_on_off_still_does_hardware(real_subprocess, env):
    """`boondock on` / `off` by hand: snapshot the active app profile, not live PPT."""
    profile = Path(env["BOONDOCK_STATE"]).parent / "z13-profile"
    profile.write_text(
        "#!/bin/sh\n"
        'echo "z13-profile $*" >> "$STUB_LOG"\n'
        'case "$1" in\n'
        "  active) echo Balanced ;;\n"
        '  show) if [ "$2" = Boondock ]; then\n'
        '    printf \'{"name":"Boondock","tdp":{"pl1":28,"pl2":32,"pl3":45}}\'\n'
        "  else\n"
        '    printf \'{"name":"Balanced","tdp":{"pl1":30,"pl2":30,"pl3":30}}\'\n'
        "  fi ;;\n"
        "esac\n"
    )
    profile.chmod(0o755)
    env = {**env, "BOONDOCK_Z13_PROFILE": str(profile)}

    def manual(arg):
        return real_subprocess(["bash", str(SCRIPT), arg], env=env, capture_output=True, text=True,
                               timeout=30, stdin=subprocess.DEVNULL)
    assert manual("on").returncode == 0
    log = Path(env["STUB_LOG"]).read_text()
    assert "z13ctl profile --set quiet" in log
    assert "z13ctl tdp --set 28 --pl2 32 --pl3 45" in log
    snapshot = Path(env["BOONDOCK_STATE"]).read_text()
    assert "profile=Balanced" in snapshot
    assert "pl1=30" in snapshot
    Path(env["STUB_LOG"]).unlink()
    assert manual("off").returncode == 0
    log = Path(env["STUB_LOG"]).read_text()
    assert "z13-profile apply Balanced" in log  # restored snapshot
    assert "z13ctl profile --set balanced" not in log


def test_off_reports_a_failed_restore_and_keeps_the_snapshot(real_subprocess, env):
    state = Path(env["BOONDOCK_STATE"])
    state.write_text("profile=Balanced\npl1=30\npl2=30\npl3=30\n")
    profile = state.parent / "z13-profile"
    profile.write_text("#!/bin/sh\nexit 1\n")
    profile.chmod(0o755)
    env = {**env, "BOONDOCK_Z13_PROFILE": str(profile)}
    p = real_subprocess(
        ["bash", str(SCRIPT), "hardware-off"],
        env=env, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
    )
    assert p.returncode != 0
    assert "profile restore failed" in p.stdout
    assert "profile=Balanced" in state.read_text()   # a retry can still restore it


def test_stored_old_hook_is_migrated(profiles_env):
    store = profiles.load_store()
    b = next(p for p in store["profiles"] if p["name"] == "Boondock")
    b["hooks"] = {"enter": f"{profiles.BOONDOCK_BIN} on", "exit": f"{profiles.BOONDOCK_BIN} off"}
    profiles.save_store(store)
    got = next(p for p in profiles.load_store()["profiles"] if p["name"] == "Boondock")
    assert got["hooks"] == next(p for p in profiles.default_profiles()
                                if p["name"] == "Boondock")["hooks"]


def test_user_edited_boondock_hook_is_left_alone(profiles_env):
    store = profiles.load_store()
    b = next(p for p in store["profiles"] if p["name"] == "Boondock")
    b["hooks"] = {"enter": "echo mine", "exit": "echo bye"}
    profiles.save_store(store)
    got = next(p for p in profiles.load_store()["profiles"] if p["name"] == "Boondock")
    assert got["hooks"] == {"enter": "echo mine", "exit": "echo bye"}


def test_session_off_clears_old_combined_snapshot(real_subprocess, env):
    """A snapshot written by the pre-split `boondock on` holds hardware keys too: the
    session-off hook restores the session part, writes no power, and clears the file."""
    state = Path(env["BOONDOCK_STATE"])
    state.write_text("brightness=800\nprofile=balanced\npl1=30\npl2=30\npl3=30\n")
    p = real_subprocess(["bash", str(SCRIPT), "session-off"], env=env, capture_output=True,
                        text=True, timeout=30, stdin=subprocess.DEVNULL)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "sudo tee" in Path(env["STUB_LOG"]).read_text()      # brightness restored
    assert hardware_touches(Path(env["STUB_LOG"]).read_text().splitlines()) == []
    assert not state.exists()
    st = real_subprocess(["bash", str(SCRIPT), "status"], env=env, capture_output=True,
                         text=True, timeout=30, stdin=subprocess.DEVNULL)
    assert "MODE: normal" in st.stdout
