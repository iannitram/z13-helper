"""install.sh: syntax, and a --dry-run against a fake HOME with PATH stubs.

The dry run must write nothing outside $HOME/.local/state/z13-helper and
must never run sudo for anything but read-only probes. Runs the real script
(conftest's `real_subprocess` opt-in); every external tool it would touch on
the device is a stub that logs its argv.
"""

import os
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "install.sh"

STUB = '#!/bin/sh\necho "$(basename "$0") $*" >> "$STUB_LOG"\n'
STUBS = {
    "sudo": STUB + "exit 1\n",                   # no passwordless sudo
    "curl": STUB + "exit 7\n",                   # no network
    "systemctl": STUB + "exit 3\n",              # nothing active / enabled
    "flatpak": STUB + "exit 1\n",                # app not installed
    "logger": STUB, "pnpm": STUB + "exit 1\n", "ss": STUB, "pgrep": STUB + "exit 1\n",
    "journalctl": STUB,
}


def test_bash_syntax(real_subprocess):
    assert real_subprocess(["bash", "-n", str(SCRIPT)], timeout=10).returncode == 0


def snapshot(root, skip=()):
    out = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if os.path.join(d, x) not in skip and x != ".git"]
        for f in files:
            p = os.path.join(d, f)
            try:
                out[p] = os.stat(p).st_mtime_ns
            except OSError:
                pass
    return out


needs_bash = (
    pytest.mark.skipif(os.geteuid() == 0, reason="install.sh refuses to run as root"),
    pytest.mark.skipif(shutil.which("bash") is None or shutil.which("tee") is None, reason="needs bash"),
)
LOGGING_Q = "Do you want to enable error logging to help with development of the app?"


def dry_run(tmp_path, run, *args, install_json=None):
    """install.sh --dry-run ARGS against a fake HOME with PATH stubs -> output."""
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    if install_json is not None:
        (home / ".config" / "z13-helper").mkdir(parents=True)
        (home / ".config" / "z13-helper" / "install.json").write_text(install_json)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in STUBS.items():
        (stubs / name).write_text(body)
        (stubs / name).chmod(0o755)
    env = {"HOME": str(home), "PATH": f"{stubs}:/usr/bin:/bin", "STUB_LOG": str(tmp_path / "stub.log"),
           "LANG": "C.UTF-8", "https_proxy": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
           "no_proxy": ""}
    p = run(["bash", str(SCRIPT), "--dry-run", *args], env=env, stdin=open(os.devnull),
            capture_output=True, text=True, timeout=240)
    assert p.returncode in (0, 1), p.stdout + p.stderr
    return p.stdout + p.stderr


@needs_bash[0]
@needs_bash[1]
@pytest.mark.parametrize("args, recorded, shown", [
    ((), None, f"(dry run) would ask: {LOGGING_Q} [Y/n]"),          # full install, never asked
    ((), '{"logging": false}', "error logging: off (recorded"),    # already answered: not asked
    ((), '{"mode": "repair"}', f"would ask: {LOGGING_Q}"),         # older record, no key yet
    (("--repair",), None, None),                                   # repair keeps the choice
])
def test_dry_run_logging_question(tmp_path, real_subprocess, args, recorded, shown):
    out = dry_run(tmp_path, real_subprocess, *args, install_json=recorded)
    if shown is None:
        assert "Error logging" not in out and LOGGING_Q not in out
    else:
        assert shown in out
        if "recorded" in shown:
            assert LOGGING_Q not in out


@needs_bash[0]
@needs_bash[1]
def test_dry_run_repair_writes_nothing(tmp_path, real_subprocess):
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    z = home / ".local" / "bin" / "z13ctl"            # installed z13ctl: repair keeps it, no fetch
    z.write_text('#!/bin/sh\necho "z13ctl version 1.4.2"\n')
    z.chmod(0o755)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in STUBS.items():
        (stubs / name).write_text(body)
        (stubs / name).chmod(0o755)
    log = tmp_path / "stub.log"
    repo_skip = {str(REPO / "decky" / "node_modules"), str(REPO / "decky" / "dist")}
    before_repo, before_home = snapshot(REPO, repo_skip), snapshot(home)

    env = {"HOME": str(home), "PATH": f"{stubs}:/usr/bin:/bin", "STUB_LOG": str(log),
           "LANG": "C.UTF-8",
           # urllib in installers.py: fail fast instead of reaching GitHub.
           "https_proxy": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", "no_proxy": ""}
    p = real_subprocess(["bash", str(SCRIPT), "--repair", "--dry-run"], env=env, stdin=open(os.devnull),
                        capture_output=True, text=True, timeout=240)
    out = p.stdout + p.stderr
    assert p.returncode in (0, 1), out
    assert "dry run" in out

    # Nothing in the checkout changed (no __pycache__, no decky build).
    assert snapshot(REPO, repo_skip) == before_repo
    # In $HOME only the state dir (dry-run log) is new.
    new = set(snapshot(home)) - set(before_home)
    state = str(home / ".local" / "state" / "z13-helper")
    assert new and all(f.startswith(state + os.sep) for f in new), sorted(new)
    assert any(f.endswith("-dryrun.log") for f in new)
    assert not (home / ".config" / "z13-helper" / "install.json").exists()

    # sudo only ever probed, never asked to change anything.
    calls = log.read_text().splitlines() if log.exists() else []
    for c in calls:
        if c.startswith("sudo "):
            assert c in ("sudo -n true",) or c.startswith("sudo -n cmp -s "), c
        assert not c.startswith("logger"), c
