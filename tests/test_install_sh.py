"""install.sh: syntax, and a --dry-run against a fake HOME with PATH stubs.

The dry run must write nothing outside $HOME/.local/state/z13-helper and
must never run sudo for anything but read-only probes. Runs the real script
(conftest's `real_subprocess` opt-in); every external tool it would touch on
the device is a stub that logs its argv.
"""

import os
import shutil
from pathlib import Path
import shlex

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


@needs_bash[0]
@needs_bash[1]
def test_dry_run_enables_one_battery_writer(tmp_path, real_subprocess):
    out = dry_run(tmp_path, real_subprocess)
    assert "systemctl --user enable z13-restore-profile.service" in out
    assert "systemctl --user enable z13-battery-default.service" not in out


@needs_bash[0]
@needs_bash[1]
def test_dry_run_upgrade_retires_installed_battery_default(tmp_path, real_subprocess):
    home_units = tmp_path / "home" / ".config" / "systemd" / "user"
    home_units.mkdir(parents=True)
    (home_units / "z13-battery-default.service").write_text("[Unit]\n")
    out = dry_run(tmp_path, real_subprocess)
    assert "disable --now z13-battery-default.service" in out
    assert "rm -f" in out and "z13-battery-default.service" in out


def _decky_plugin_harness(tmp_path, *, changed=False, stop_fail=False, install_fail=False,
                          swap_fail=False,
                          old_plugin=False, dry_run=False, first_install=False,
                          backup_fail=False, start_fail=False, rollback_fail=False,
                          journal_cursor=True, journal_fail=False, stop_state="inactive",
                          rollback_rm_fail=False):
    """Run the actual Decky installer functions against temporary paths and a sudo stub."""
    source = SCRIPT.read_text()
    start = source.index("find_pnpm() {")
    end = source.index("# ----------------------------------------------------------------------\n# 5. Flatpak app", start)
    function_file = tmp_path / "decky-functions.sh"
    function_file.write_text(source[start:end])
    source_repo = tmp_path / "source-repo"

    home = tmp_path / "home"
    plugin_dir = home / "homebrew" / "plugins" / "z13-helper"
    old_dir = home / "homebrew" / "plugins" / "z13-decky"
    plugin_files = [
        "plugin.json", "package.json", "main.py", "dist/index.js", "dist/index.js.map",
        "py_modules/fan_watchdog.py", "py_modules/sensor_reader.py", "py_modules/host.py",
        "py_modules/power_range.py",
    ]
    for rel in plugin_files:
        src = (source_repo / "src" / rel.removeprefix("py_modules/")) if rel.startswith("py_modules/") else source_repo / "decky" / rel
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(f"fixture source for {rel}\n")
        if not first_install:
            dest = plugin_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
    if changed and not first_install:
        (plugin_dir / "dist" / "index.js").write_text("stale installed frontend\n")
    if old_plugin:
        old_dir.mkdir(parents=True)
        (old_dir / "plugin.json").write_text("old plugin")

    events = tmp_path / "events.log"
    sudo = tmp_path / "sudo"
    sudo.write_text("""#!/bin/sh
printf 'sudo %s\\n' \"$*\" >> \"$EVENTS\"
case \"$1\" in
  systemctl)
    case \"$2\" in
      stop)
        [ \"$STOP_FAIL\" != 1 ] || exit 1
        printf '%s' \"$STOP_STATE\" > \"$ACTIVE_STATE\" ;;
      start) [ \"$START_FAIL\" != 1 ] || exit 1; printf active > \"$ACTIVE_STATE\"; printf 'z13-helper up\\n' >> \"$JOURNAL\" ;;
      kill|reset-failed) : ;;
      is-active)
        if [ \"$2\" = --quiet ]; then [ \"$(cat \"$ACTIVE_STATE\")\" = active ]
        else cat \"$ACTIVE_STATE\"; [ \"$(cat \"$ACTIVE_STATE\")\" = active ]; fi ;;
    esac ;;
  journalctl)
    [ \"$JOURNAL_FAIL\" != 1 ] || exit 1
    case \"$*\" in
      *--show-cursor*) [ \"$JOURNAL_CURSOR\" != 1 ] || printf '%s\\n' '-- cursor: fixture-cursor' ;;
      *--after-cursor*) cat \"$JOURNAL\" ;;
      *--since*) cat \"$JOURNAL\" ;;
    esac ;;
  mktemp) template=\"$3\"; p=\"${template%XXXXXXXXXX}$$-$RANDOM\"; /bin/mkdir -m 700 \"$p\"; printf '%s\\n' \"$p\" ;;
  rmdir) shift; exec /bin/rmdir \"$@\" ;;
  ss) [ ! -f \"$LISTENER_FILE\" ] || cat \"$LISTENER_FILE\" ;;
  kill) exit 0 ;;
  cmp) shift; exec /usr/bin/cmp \"$@\" ;;
  mv)
    if [ \"$2\" = -T ]; then src=\"$4\"; dest=\"$5\"; else src=\"$2\"; dest=\"$3\"; fi
    if [ -n \"$MOVE_FAIL_DEST\" ] && [ \"$dest\" = \"$MOVE_FAIL_DEST\" ]; then
      case \"$src\" in *stage.*) exit 1 ;; esac
    fi
    if [ \"$ROLLBACK_FAIL\" = 1 ]; then case \"$src\" in *.z13-helper-old.*) exit 1 ;; esac; fi
    shift; exec /usr/bin/mv \"$@\" ;;
  chmod) shift; exec /bin/chmod \"$@\" ;;
  install)
    case \"$*\" in *\"$INSTALL_FAIL_DEST\") exit 1 ;; esac
    shift
    if [ \"$1\" = -d ]; then
      shift
      [ \"$1\" != -m ] || shift 2
      [ \"$1\" != -o ] || shift 2
      [ \"$1\" != -g ] || shift 2
      exec /usr/bin/install -d -m 755 \"$@\"
    fi
    [ \"$1\" != -D ] || shift
    [ \"$1\" != -m ] || shift 2
    [ \"$1\" != -o ] || shift 2
    [ \"$1\" != -g ] || shift 2
    exec /usr/bin/install -D -m 644 \"$@\" ;;
  rm)
    if [ \"$ROLLBACK_RM_FAIL\" = 1 ] && [ \"$3\" = -- ] && [ \"$4\" = \"$HOME/homebrew/plugins/z13-helper\" ]; then exit 1; fi
    shift; exec /usr/bin/rm \"$@\" ;;
  mkdir) shift; exec /bin/mkdir \"$@\" ;;
  tar) [ \"$BACKUP_FAIL\" != 1 ] || exit 1; shift; exec /usr/bin/tar "$@" ;;
  test) exec /usr/bin/test \"$@\" ;;
  cat) exec /usr/bin/cat \"$@\" ;;
  *) exit 0 ;;
esac
""")
    sudo.chmod(0o755)
    listener_file = tmp_path / "listener.txt"
    env = {
        "HOME": str(home), "EVENTS": str(events), "JOURNAL": str(tmp_path / "journal.log"),
        "ACTIVE_STATE": str(tmp_path / "active-state"),
        "STOP_FAIL": "1" if stop_fail else "0",
        "STOP_STATE": stop_state,
        "BACKUP_FAIL": "1" if backup_fail else "0",
        "START_FAIL": "1" if start_fail else "0",
        "ROLLBACK_FAIL": "1" if rollback_fail else "0",
        "ROLLBACK_RM_FAIL": "1" if rollback_rm_fail else "0",
        "JOURNAL_CURSOR": "1" if journal_cursor else "0",
        "JOURNAL_FAIL": "1" if journal_fail else "0",
        "INSTALL_FAIL_DEST": "dist/index.js" if install_fail else "never-match",
        "MOVE_FAIL_DEST": str(home / "homebrew/plugins/z13-helper") if swap_fail else "",
        "LISTENER_FILE": str(listener_file),
        "PATH": f"{tmp_path}:/usr/bin:/bin", "LANG": "C.UTF-8",
    }
    (tmp_path / "active-state").write_text("active")
    vars = {
        "REPO": source_repo, "HOME": home, "STATE": home / ".local/state/z13-helper",
        "BACKUP": home / ".local/state/z13-helper/install-backups/test",
        "STAMP": "test",
        "PLUGIN_DIR": plugin_dir, "OLD_PLUGIN_DIR": old_dir,
        "DRY": int(dry_run),
    }
    (home / ".local/state/z13-helper").mkdir(parents=True)
    lines = ["set -uo pipefail"]
    for key, value in vars.items():
        lines.append(f"{key}={shlex.quote(str(value))}")
    lines += [
        "PLUGIN_FILES=(plugin.json package.json main.py dist/index.js dist/index.js.map py_modules/fan_watchdog.py py_modules/sensor_reader.py py_modules/host.py py_modules/power_range.py)",
        "NET=0 PLUGIN_CHANGED=0", "CHANGED=() FAILED=()",
        f"source {shlex.quote(str(function_file))}",
        "plugin_src() { case $1 in py_modules/*) printf '%s/src/%s\\n' \"$REPO\" \"${1#py_modules/}\" ;; *) printf '%s/decky/%s\\n' \"$REPO\" \"$1\" ;; esac; }",
        "say() { :; }", "info() { printf '%s\\n' \"$*\"; }", "warn() { :; }",
        "fail() { FAILED+=(\"$*\"); printf 'FAIL: %s\\n' \"$*\" >&2; }",
        "act() { if ((DRY)); then printf '(dry run) %s\\n' \"$*\"; else \"$@\"; fi; }",
        "backup() { :; }", "find_pnpm() { return 1; }", "sleep() { :; }",
        "decky_plugin",
    ]
    return "\n".join(lines) + "\n", env, events, plugin_dir, old_dir, source_repo


@needs_bash[1]
def test_decky_plugin_quiesces_before_replacing_files_and_restarts(tmp_path, real_subprocess):
    script, env, events, plugin_dir, _, source_repo = _decky_plugin_harness(tmp_path, changed=True)
    p = real_subprocess(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)
    assert p.returncode == 0, p.stdout + p.stderr
    calls = events.read_text().splitlines()
    stop = calls.index("sudo systemctl stop plugin_loader.service")
    swap = next(i for i, c in enumerate(calls) if c.startswith("sudo mv ") and c.endswith(str(plugin_dir)))
    start = calls.index("sudo systemctl start plugin_loader.service")
    stage_create = next(i for i, c in enumerate(calls) if c.startswith(f"sudo mktemp -d {plugin_dir.parent.parent}/.z13-helper-stage."))
    assert stage_create < stop
    assert stop < swap < start
    assert any("sudo install -D -m 644 -o root -g root" in c for c in calls)
    assert (plugin_dir.stat().st_mode & 0o777) == 0o755
    assert any("journalctl -u plugin_loader.service --after-cursor" in c for c in calls)
    assert not (plugin_dir.parent.parent / "logs").exists()
    assert (plugin_dir / "dist/index.js").read_text() == (source_repo / "decky/dist/index.js").read_text()


@needs_bash[1]
def test_decky_plugin_unchanged_repair_does_not_restart_loader(tmp_path, real_subprocess):
    script, env, events, _, _, _ = _decky_plugin_harness(tmp_path)
    p = real_subprocess(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)
    assert p.returncode == 0, p.stdout + p.stderr
    assert not events.exists() or "systemctl stop" not in events.read_text()


@needs_bash[1]
def test_decky_plugin_aborts_replacement_if_loader_cannot_stop(tmp_path, real_subprocess):
    script, env, events, plugin_dir, _, _ = _decky_plugin_harness(tmp_path, changed=True, stop_fail=True)
    (tmp_path / "listener.txt").write_text("LISTEN 0 128 127.0.0.1:1337 0.0.0.0:* users:((\"loader\",pid=42,fd=3))\n")
    before = (plugin_dir / "dist/index.js").read_text()
    p = real_subprocess(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)
    assert p.returncode == 1
    calls = events.read_text().splitlines()
    assert "sudo systemctl stop plugin_loader.service" in calls
    assert "sudo kill 42" in calls
    assert not any(c.startswith("sudo mv ") for c in calls)
    assert (plugin_dir / "dist/index.js").read_text() == before
    assert "plugin files left unchanged" in p.stderr


@needs_bash[1]
def test_decky_plugin_does_not_trust_empty_port_when_unit_remains_active(tmp_path, real_subprocess):
    script, env, events, plugin_dir, _, _ = _decky_plugin_harness(tmp_path, changed=True, stop_fail=True)
    before = (plugin_dir / "dist/index.js").read_text()
    p = real_subprocess(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)
    assert p.returncode == 1
    calls = events.read_text().splitlines()
    assert "sudo systemctl is-active plugin_loader.service" in calls
    assert not any(c.startswith("sudo mv ") for c in calls)
    assert (plugin_dir / "dist/index.js").read_text() == before
    assert "plugin_loader state is not confirmed stopped (active)" in p.stderr


@needs_bash[1]
@pytest.mark.parametrize("state", ["deactivating", ""])
def test_decky_plugin_requires_confirmed_stopped_unit_state(tmp_path, real_subprocess, state):
    script, env, events, plugin_dir, _, _ = _decky_plugin_harness(
        tmp_path, changed=True, stop_state=state)
    before = (plugin_dir / "dist/index.js").read_text()
    p = real_subprocess(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)
    assert p.returncode == 1
    assert not any(c.startswith("sudo mv ") for c in events.read_text().splitlines())
    assert (plugin_dir / "dist/index.js").read_text() == before
    assert "state is not confirmed stopped" in p.stderr


@needs_bash[1]
def test_decky_plugin_stage_copy_failure_leaves_live_tree_untouched(tmp_path, real_subprocess):
    script, env, events, plugin_dir, _, _ = _decky_plugin_harness(tmp_path, changed=True, install_fail=True)
    before = (plugin_dir / "dist/index.js").read_text()
    p = real_subprocess(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)
    assert p.returncode == 1
    calls = events.read_text().splitlines()
    assert not any("systemctl stop" in c for c in calls)
    assert (plugin_dir / "dist/index.js").read_text() == before
    assert "FAIL: stage plugin dist/index.js; installed plugin left unchanged" in p.stderr


@needs_bash[1]
def test_decky_plugin_failed_swap_rolls_back_and_restarts_loader(tmp_path, real_subprocess):
    script, env, events, plugin_dir, _, _ = _decky_plugin_harness(tmp_path, changed=True, swap_fail=True)
    before = (plugin_dir / "dist/index.js").read_text()
    p = real_subprocess(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)
    assert p.returncode == 1
    calls = events.read_text().splitlines()
    stop = calls.index("sudo systemctl stop plugin_loader.service")
    failed_swap = next(i for i, c in enumerate(calls) if c.startswith("sudo mv ") and c.endswith(str(plugin_dir)))
    start = calls.index("sudo systemctl start plugin_loader.service")
    assert stop < failed_swap < start
    assert (plugin_dir / "dist/index.js").read_text() == before
    assert "FAIL: could not install staged Decky plugin; restoring previous plugin" in p.stderr


@needs_bash[1]
def test_decky_plugin_preserves_old_tree_if_swap_rollback_fails(tmp_path, real_subprocess):
    script, env, events, plugin_dir, _, _ = _decky_plugin_harness(
        tmp_path, changed=True, swap_fail=True, rollback_fail=True)
    before = (plugin_dir / "dist/index.js").read_text()
    p = real_subprocess(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)
    assert p.returncode == 1
    rollback_dirs = list(plugin_dir.parent.parent.glob(".z13-helper-old.*"))
    assert len(rollback_dirs) == 1
    assert (rollback_dirs[0] / "dist/index.js").read_text() == before
    assert not any(c == "sudo systemctl start plugin_loader.service" for c in events.read_text().splitlines())


@needs_bash[1]
def test_decky_plugin_start_failure_restores_previous_tree(tmp_path, real_subprocess):
    script, env, events, plugin_dir, _, _ = _decky_plugin_harness(tmp_path, changed=True, start_fail=True)
    before = (plugin_dir / "dist/index.js").read_text()
    p = real_subprocess(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)
    assert p.returncode == 1
    calls = events.read_text().splitlines()
    assert "sudo systemctl start plugin_loader.service" in calls
    assert (plugin_dir / "dist/index.js").read_text() == before
    assert "restoring previous plugin" in p.stderr


@needs_bash[1]
def test_decky_plugin_preserves_rollback_tree_if_restore_rename_fails(tmp_path, real_subprocess):
    script, env, _, plugin_dir, _, _ = _decky_plugin_harness(
        tmp_path, changed=True, start_fail=True, rollback_fail=True)
    before = (plugin_dir / "dist/index.js").read_text()
    p = real_subprocess(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)
    assert p.returncode == 1
    rollback_dirs = list((plugin_dir.parent.parent).glob(".z13-helper-old.*"))
    assert len(rollback_dirs) == 1
    assert (rollback_dirs[0] / "dist/index.js").read_text() == before
    assert "could not restore previous Decky plugin" in p.stderr


@needs_bash[1]
def test_decky_plugin_keeps_both_trees_if_failed_tree_cannot_be_removed(tmp_path, real_subprocess):
    script, env, _, plugin_dir, _, _ = _decky_plugin_harness(
        tmp_path, changed=True, start_fail=True, rollback_rm_fail=True)
    p = real_subprocess(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)
    assert p.returncode == 1
    rollback_dirs = list(plugin_dir.parent.parent.glob(".z13-helper-old.*"))
    assert len(rollback_dirs) == 1
    assert (plugin_dir / "dist/index.js").read_text() == "fixture source for dist/index.js\n"
    assert (rollback_dirs[0] / "dist/index.js").read_text() == "stale installed frontend\n"
    assert "could not remove failed plugin tree" in p.stderr


@needs_bash[1]
def test_decky_plugin_starts_when_unit_has_no_prior_journal_entries(tmp_path, real_subprocess):
    script, env, events, plugin_dir, _, source_repo = _decky_plugin_harness(
        tmp_path, first_install=True, journal_cursor=False)
    p = real_subprocess(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)
    assert p.returncode == 0, p.stdout + p.stderr
    assert any("journalctl -u plugin_loader.service --since @" in c for c in events.read_text().splitlines())
    assert (plugin_dir / "dist/index.js").read_text() == (source_repo / "decky/dist/index.js").read_text()


@needs_bash[1]
def test_decky_plugin_does_not_start_if_journal_cannot_be_read(tmp_path, real_subprocess):
    script, env, events, _, _, _ = _decky_plugin_harness(
        tmp_path, first_install=True, journal_fail=True)
    p = real_subprocess(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)
    assert p.returncode == 1
    assert "sudo systemctl start plugin_loader.service" not in events.read_text()


@needs_bash[1]
def test_decky_plugin_first_install_stages_and_starts(tmp_path, real_subprocess):
    script, env, events, plugin_dir, _, source_repo = _decky_plugin_harness(tmp_path, first_install=True)
    p = real_subprocess(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)
    assert p.returncode == 0, p.stdout + p.stderr
    calls = events.read_text().splitlines()
    assert calls.index("sudo systemctl stop plugin_loader.service") < calls.index("sudo systemctl start plugin_loader.service")
    assert (plugin_dir / "dist/index.js").read_text() == (source_repo / "decky/dist/index.js").read_text()


@needs_bash[1]
def test_decky_plugin_backup_failure_does_not_move_installed_tree(tmp_path, real_subprocess):
    script, env, events, plugin_dir, _, _ = _decky_plugin_harness(tmp_path, changed=True, backup_fail=True)
    before = (plugin_dir / "dist/index.js").read_text()
    p = real_subprocess(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)
    assert p.returncode == 1
    calls = events.read_text().splitlines()
    assert not any(c.startswith("sudo mv ") for c in calls)
    assert (plugin_dir / "dist/index.js").read_text() == before
    assert "could not back up installed Decky plugin" in p.stderr


@needs_bash[1]
def test_decky_plugin_quiesce_failure_restores_stopped_loader(tmp_path, real_subprocess):
    script, env, events, plugin_dir, _, _ = _decky_plugin_harness(tmp_path, changed=True)
    (tmp_path / "listener.txt").write_text("LISTEN 0 128 127.0.0.1:1337 0.0.0.0:* users:((\"loader\",pid=42,fd=3))\n")
    before = (plugin_dir / "dist/index.js").read_text()
    p = real_subprocess(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)
    assert p.returncode == 1
    calls = events.read_text().splitlines()
    assert "sudo systemctl stop plugin_loader.service" in calls
    assert "sudo systemctl start plugin_loader.service" in calls
    assert not any(c.startswith("sudo mv ") for c in calls)
    assert (plugin_dir / "dist/index.js").read_text() == before
    assert "plugin files left unchanged" in p.stderr


@needs_bash[1]
def test_decky_plugin_removes_legacy_directory_only_after_quiescing(tmp_path, real_subprocess):
    script, env, events, plugin_dir, old_dir, source_repo = _decky_plugin_harness(tmp_path, old_plugin=True)
    p = real_subprocess(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)
    assert p.returncode == 0, p.stdout + p.stderr
    calls = events.read_text().splitlines()
    stop = calls.index("sudo systemctl stop plugin_loader.service")
    remove = calls.index(f"sudo rm -rf {old_dir}")
    swap = next(i for i, c in enumerate(calls) if c.startswith("sudo mv ") and c.endswith(str(plugin_dir)))
    start = calls.index("sudo systemctl start plugin_loader.service")
    assert stop < swap < remove < start
    assert not old_dir.exists()
    assert (plugin_dir / "dist/index.js").read_text() == (source_repo / "decky/dist/index.js").read_text()


@needs_bash[1]
def test_decky_plugin_dry_run_only_prints_loader_lifecycle(tmp_path, real_subprocess):
    script, env, events, _, _, _ = _decky_plugin_harness(tmp_path, changed=True, dry_run=True)
    p = real_subprocess(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)
    assert p.returncode == 0, p.stdout + p.stderr
    assert not events.exists()
    assert "(dry run) sudo systemctl stop/kill plugin_loader.service" in p.stdout
    assert "(dry run) sudo systemctl start plugin_loader.service" in p.stdout
