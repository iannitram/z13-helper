"""Machine timeline store (`src/timeline.py`, Phase 16f, #134).

Covers append-only writes, size-capped rotation, reading a partly written last
line, Principle 7's journal line from the same call, root-safe symlink refusal,
and the five write paths (profile applies, goal changes, installer runs,
post-update repairs, SteamOS BUILD_ID changes).
"""

import ast
import json
import os
import pwd
import types
from pathlib import Path

import pytest

import host
import install_record
import installers
import post_update_state
import profiles
import timeline
import update_watch
from post_update.recipe import Recipe
from post_update.runner import Env, make_timeline_recorder, run_plan

REPO = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO / "install.sh"


# ----------------------------------------------------------------------
# 1. Core store: append-only, rotation, partly written last line
# ----------------------------------------------------------------------

def test_append_is_strictly_append_only_and_populates_schema(tmp_path):
    p = tmp_path / "timeline.jsonl"
    e1 = timeline.append(
        {
            "kind": "profile",
            "summary": "Profile Balanced -> Turbo",
            "before": {"name": "Balanced"},
            "after": {"name": "Turbo"},
            "changed": ["profile", "fans"],
            "rollback": {"kind": "profile", "profile": "Balanced"},
        },
        path=str(p),
    )
    first_bytes = p.read_bytes()
    assert first_bytes.endswith(b"\n")
    assert first_bytes.count(b"\n") == 1

    e2 = timeline.append(
        {
            "kind": "goal",
            "summary": "Goal max",
            "before": {"goal": "max"},
            "after": {"goal": "max"},
            "rollback": None,
            "no_rollback_reason": "Goal unchanged",
        },
        path=str(p),
    )
    second_bytes = p.read_bytes()
    # Earlier bytes are never rewritten or truncated.
    assert second_bytes.startswith(first_bytes)
    assert second_bytes.count(b"\n") == 2

    got = timeline.read_entries(str(p))
    assert [x["id"] for x in got] == [e1["id"], e2["id"]]
    assert got[0]["v"] == timeline.SCHEMA_VERSION
    assert got[0]["rollback"] == {"kind": "profile", "profile": "Balanced"}
    assert got[0]["no_rollback_reason"] is None
    assert got[1]["rollback"] is None
    assert got[1]["no_rollback_reason"] == "Goal unchanged"


def test_size_capped_rotation_bounds_files_and_reads_across_segments(tmp_path):
    p = tmp_path / "timeline.jsonl"
    ids = []
    # Each JSONL line is ~190-220 bytes; max_bytes=450 fits ~2 entries per segment.
    for idx in range(10):
        rec = timeline.append(
            {
                "id": f"e-{idx:02d}",
                "kind": "profile",
                "summary": f"Switch #{idx}",
                "before": {"idx": idx - 1},
                "after": {"idx": idx},
                "changed": ["profile"],
                "no_rollback_reason": "test",
            },
            path=str(p),
            max_bytes=450,
            backups=2,
        )
        ids.append(rec["id"])

    assert p.exists()
    assert (tmp_path / "timeline.jsonl.1").exists()
    assert (tmp_path / "timeline.jsonl.2").exists()
    assert not (tmp_path / "timeline.jsonl.3").exists()
    assert p.stat().st_size <= 450

    chronological = [e["id"] for e in timeline.read_entries(str(p), backups=2)]
    # Oldest entries rotated past .2 were dropped; surviving suffix stays ordered.
    assert len(chronological) < len(ids)
    assert chronological == ids[-len(chronological):]

    newest = [e["id"] for e in timeline.read_entries(str(p), limit=3, newest_first=True, backups=2)]
    assert newest == list(reversed(ids[-3:]))

    active_only = [e["id"] for e in timeline.read_entries(str(p), include_rotated=False)]
    assert active_only == ids[-len(active_only):]
    assert len(active_only) < len(chronological)


def test_read_entries_skips_partly_written_last_line_and_recovers_on_next_append(tmp_path):
    p = tmp_path / "timeline.jsonl"
    e1 = timeline.append({"id": "ok-1", "kind": "profile", "summary": "one"}, path=str(p))
    e2 = timeline.append({"id": "ok-2", "kind": "goal", "summary": "two"}, path=str(p))

    # Simulate an interrupted write at EOF: cut mid-JSON with no trailing newline.
    with open(p, "ab") as f:
        f.write(b'{"v":1,"id":"cut-mid","kind":"profile","summary":"unfinished')

    got = timeline.read_entries(str(p))
    assert [e["id"] for e in got] == [e1["id"], e2["id"]]
    assert [e["id"] for e in timeline.read_entries(str(p), newest_first=True)] == [e2["id"], e1["id"]]

    # Even if a truncated byte stream happens to end with '}' without '\n', it is incomplete.
    p2 = tmp_path / "brace_no_nl.jsonl"
    p2.write_bytes(
        json.dumps({"id": "good", "kind": "goal"}).encode() + b"\n"
        + b'{"id":"no-newline","kind":"goal"}'
    )
    assert [e["id"] for e in timeline.read_entries(str(p2))] == ["good"]

    # Truncated multi-byte UTF-8 sequence at EOF is also skipped cleanly.
    p3 = tmp_path / "bad_utf8.jsonl"
    p3.write_bytes(
        json.dumps({"id": "utf8-ok", "kind": "goal"}).encode() + b"\n"
        + b'{"id":"bad","summary":"\xc3'
    )
    assert [e["id"] for e in timeline.read_entries(str(p3))] == ["utf8-ok"]

    # Appending after a partly written last line preserves prior bytes and starts cleanly on a new line.
    raw_before_recovery = p.read_bytes()
    e3 = timeline.append({"id": "ok-3", "kind": "profile", "summary": "three"}, path=str(p))
    raw_after_recovery = p.read_bytes()
    assert raw_after_recovery.startswith(raw_before_recovery)
    assert [e["id"] for e in timeline.read_entries(str(p))] == [e1["id"], e2["id"], e3["id"]]


# ----------------------------------------------------------------------
# 2. Principle 7 journal line & root-safe file handling
# ----------------------------------------------------------------------

def test_principle_7_journal_line_emitted_from_same_call_for_host_changes_only(tmp_path, fake_host):
    p = tmp_path / "timeline.jsonl"
    timeline.append({"id": "hw-1", "kind": "profile", "summary": "Balanced"}, path=str(p))
    timeline.append({"id": "hw-2", "kind": "goal", "summary": "Max"}, path=str(p))
    assert fake_host.argvs == []

    rec = timeline.record_install(
        mode="repair",
        target="host",
        before={"z13ctl": "1.4.2"},
        after={"z13ctl": "1.4.2"},
        changed=["/etc/systemd/system/z13-fan-resume.service"],
        backup_dir=str(tmp_path / "bak"),
        path=str(p),
    )
    assert len(fake_host.argvs) == 1
    argv = fake_host.argvs[0]
    assert argv[:3] == ["logger", "-t", "z13-install"]
    assert rec["id"] in argv[3]
    assert "/etc/systemd/system/z13-fan-resume.service" in argv[3]


def test_refuses_symlinks_and_fchowns_when_running_as_root(tmp_path, monkeypatch):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    sym = tmp_path / "timeline.jsonl"
    sym.symlink_to(victim)

    with pytest.raises(timeline.TimelineError):
        timeline.append({"kind": "profile", "summary": "x"}, path=str(sym))
    assert victim.read_text() == "keep me"
    assert timeline.read_entries(str(sym)) == []

    # Pre-planted symlink at a rotation slot is also refused.
    real_tl = tmp_path / "real" / "timeline.jsonl"
    timeline.append({"kind": "profile", "summary": "seed" * 40}, path=str(real_tl))
    (tmp_path / "real" / "timeline.jsonl.1").symlink_to(victim)
    with pytest.raises(timeline.TimelineError):
        timeline.append({"kind": "profile", "summary": "rotate" * 40}, path=str(real_tl), max_bytes=50)
    assert victim.read_text() == "keep me"

    # Simulated root: new lock and timeline files are handed to the deck owner.
    root_tl = tmp_path / "as_root" / "timeline.jsonl"
    fchowns = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: fchowns.append((uid, gid)))
    monkeypatch.setattr(os, "chown", lambda path, uid, gid: None)
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: types.SimpleNamespace(pw_uid=1000, pw_gid=1000))
    timeline.append({"kind": "profile", "summary": "root write"}, path=str(root_tl))
    assert (1000, 1000) in fchowns


# ----------------------------------------------------------------------
# 3. Five write paths
# ----------------------------------------------------------------------

def test_profile_apply_records_reversible_switch_and_non_reversible_restore(profiles_env, fake_host):
    profiles.load_store()  # seeds Balanced as active
    # 1. Switch Balanced -> Turbo: reversible rollback to Balanced, hooks scrubbed.
    boondock = profiles.get_profile("Boondock")
    assert (boondock.get("hooks") or {}).get("enter")
    profiles.apply_by_name("Boondock", skip_hooks=True)

    entries = timeline.read_entries(str(profiles_env.timeline))
    assert len(entries) == 1
    sw = entries[0]
    assert sw["kind"] == "profile"
    assert sw["ok"] is True
    assert sw["before"]["name"] == "Balanced"
    assert sw["after"]["name"] == "Boondock"
    assert sw["after"]["has_hooks"] is True
    assert "hooks" not in sw["after"]  # raw shell commands never stored
    assert sw["rollback"]["kind"] == "profile"
    assert sw["rollback"]["profile"] == "Balanced"
    assert sw["no_rollback_reason"] is None
    assert not any(a[0] == "logger" for a in fake_host.argvs)

    # 2. Re-applying already-active Boondock (login/resume restore): no rollback needed.
    profiles.apply_by_name("Boondock", skip_hooks=True, login_battery_default=80)
    entries = timeline.read_entries(str(profiles_env.timeline))
    assert len(entries) == 2
    reapply = entries[1]
    assert reapply["rollback"] is None
    assert reapply["no_rollback_reason"] == "Re-applied the already active profile"

    # 3. Timed-out apply still records the failed hardware attempt.
    fake_host.fail("profile --set", rc=124, out="timed out", timed_out=True, times=1)
    profiles.apply_by_name("Silent", skip_hooks=True)
    entries = timeline.read_entries(str(profiles_env.timeline))
    assert len(entries) == 3
    assert entries[2]["ok"] is False
    assert entries[2]["after"]["name"] == "Silent"


def test_goal_change_records_before_after_and_rollback(tmp_path, fake_host):
    p = tmp_path / "timeline.jsonl"
    saved_curve = [[40, 80], [50, 100], [60, 140], [70, 190], [75, 220], [80, 255], [85, 255], [90, 255]]
    rec = timeline.record_goal_change(
        goal="max",
        previous_goal="custom",
        before={"goal": "custom", "max_fans": False, "saved_curve": saved_curve},
        after={"goal": "max", "max_fans": True, "saved_curve": saved_curve},
        profile="Balanced",
        path=str(p),
    )
    assert rec["kind"] == "goal"
    assert rec["changed"] == ["goal", "max_fans"]
    assert rec["rollback"] == {
        "kind": "goal",
        "goal": "custom",
        "state": {"goal": "custom", "max_fans": False, "saved_curve": saved_curve, "profile": "Balanced"},
    }
    assert fake_host.argvs == []


def test_post_update_runner_records_phase_12c_rollback_and_journal(tmp_path, fake_host):
    tl = tmp_path / "timeline.jsonl"
    bak_dir = tmp_path / "backups"
    bak_dir.mkdir()

    def fake_backup(dest):
        copy = bak_dir / dest.lstrip("/").replace("/", "_")
        copy.write_text("original grub\n")
        return str(copy)

    r_keyring = Recipe(id="keyring", title="Keyring", fixes="keys", check=lambda: "needs",
                       apply=lambda: None, pacman=True)
    r_grub = Recipe(id="amdgpu", title="AMDGPU", fixes="display", check=lambda: "needs",
                    apply=lambda: None, reboot=True, replaces=("/etc/default/grub-steamos",))

    env = Env(
        backup=fake_backup,
        readonly_disable=lambda: None,
        readonly_enable=lambda: None,
        record=make_timeline_recorder(backup_dir=str(bak_dir), path=str(tl)),
    )
    res = run_plan((r_keyring, r_grub), env)
    assert res == [("keyring", "done"), ("amdgpu", "done")]

    entries = timeline.read_entries(str(tl))
    assert len(entries) == 1
    e = entries[0]
    assert e["kind"] == "post_update"
    assert e["ok"] is True
    assert e["before"] == {"checks": {"keyring": "needs", "amdgpu": "needs"}}
    assert e["after"]["results"] == {"keyring": "done", "amdgpu": "done"}
    assert e["rollback"] == {
        "kind": "post_update_backup",
        "files": {"/etc/default/grub-steamos": str(bak_dir / "etc_default_grub-steamos")},
        "recipes": ["amdgpu"],
        "reboot_needed": True,
        "unbacked_recipes": ["keyring"],
        "backup_dir": str(bak_dir),
    }
    assert len(fake_host.argvs) == 1
    assert fake_host.argvs[0][:3] == ["logger", "-t", "z13-post-update"]
    assert e["id"] in fake_host.argvs[0][3]


def test_installers_main_records_entry_and_skips_when_parent_is_install_sh(tmp_path, monkeypatch):
    tl = tmp_path / "timeline.jsonl"
    monkeypatch.setattr(timeline, "TIMELINE_PATH", str(tl))
    monkeypatch.setattr(installers, "DRY_RUN", False)
    z13 = tmp_path / "bin" / "z13ctl"
    z13.parent.mkdir(parents=True)
    z13.write_bytes(b"OLD")
    monkeypatch.setattr(installers, "Z13CTL", str(z13))

    ext = tmp_path / "ext"
    (ext / "contrib" / "systemd" / "user").mkdir(parents=True)
    (ext / "z13ctl").write_bytes(b"NEW")
    monkeypatch.setattr(installers, "_z13ctl_installed_version", lambda path=None: "1.4.1")
    monkeypatch.setattr(installers, "fetch", lambda url, dest=None, timeout=120: json.dumps(
        {"tag_name": "v1.4.2", "published_at": "2026-10-09"}).encode())
    monkeypatch.setattr(installers, "_fetch_release", lambda tag=None: ("1.4.2", str(ext)))
    monkeypatch.setattr(installers, "_setup_perms", lambda perms_only=False: None)
    monkeypatch.setattr(installers, "_install_daemon_units", lambda d: None)
    monkeypatch.setattr(installers, "_start_daemon", lambda restart=True: 0)
    monkeypatch.setattr(installers.shutil, "rmtree", lambda *a, **k: None)

    logged = []
    monkeypatch.setattr(installers, "run", lambda argv, **k: (logged.append(list(argv)) or 0, ""))
    monkeypatch.delenv("Z13_INSTALL_PARENT", raising=False)

    assert installers.main(["install", "z13ctl"]) == 0
    entries = timeline.read_entries(str(tl))
    assert len(entries) == 1
    assert entries[0]["kind"] == "install"
    assert entries[0]["target"] == "z13ctl"
    assert entries[0]["before"] == {"z13ctl": "1.4.1"}
    assert entries[0]["after"] == {"z13ctl": "1.4.2"}
    assert entries[0]["rollback"]["files"] == {str(z13): f"{z13}.bak-1.4.1"}
    assert any(c[:3] == ["logger", "-t", "z13-install"] for c in logged)

    # When invoked under install.sh, install.sh owns the single consolidated entry.
    monkeypatch.setenv("Z13_INSTALL_PARENT", "install.sh")
    assert installers.main(["install", "z13ctl"]) == 0
    assert len(timeline.read_entries(str(tl))) == 1


def test_install_sh_record_writes_install_and_steamos_update_with_journal(tmp_path, real_subprocess):
    src = INSTALL_SH.read_text()
    start = src.index("record() {")
    end = src.index("\n# ----------------------------------------------------------------------\nmain()", start)
    record_fn = tmp_path / "record.sh"
    record_fn.write_text(src[start:end] + "\n")

    home = tmp_path / "home"
    cfg = home / ".config" / "z13-helper"
    cfg.mkdir(parents=True)
    (cfg / "install.json").write_text(json.dumps({
        "z13ctl": "1.4.1",
        "decky_loader": "3.2.10",
        "steamos": "3.8.28 (20260922.1)",
        "kernel": "6.18.50-valve2",
    }))

    os_rel = tmp_path / "os-release"
    os_rel.write_text('VERSION_ID="3.9.5"\nBUILD_ID="20261023.100"\n')

    backup = home / ".local" / "state" / "z13-helper" / "install-backups" / "20261104-120000"
    polkit_bak = backup / "etc" / "polkit-1" / "rules.d" / "50-z13-lightbar-reset.rules"
    polkit_bak.parent.mkdir(parents=True)
    polkit_bak.write_text("// old rule\n")
    (backup / "z13-helper-plugin.tar").write_bytes(b"tarball")

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    stub_log = tmp_path / "stub.log"
    for tool, body in {
        "logger": '#!/bin/sh\nprintf "logger %s\\n" "$*" >> "$STUB_LOG"\n',
        "flatpak": '#!/bin/sh\necho "Commit: abcdef123"\n',
    }.items():
        f = stubs / tool
        f.write_text(body)
        f.chmod(0o755)
    z13_bin = home / ".local" / "bin" / "z13ctl"
    z13_bin.parent.mkdir(parents=True)
    z13_bin.write_text('#!/bin/sh\necho "z13ctl 1.4.2"\n')
    z13_bin.chmod(0o755)

    script = f"""
set -uo pipefail
DRY=0 MODE=repair PY=python3 REPO={str(REPO)!r}
LOG={str(home / '.local/state/z13-helper/install-test.log')!r}
LOGGING="" BACKUP={str(backup)!r}
CHANGED=("/etc/polkit-1/rules.d/50-z13-lightbar-reset.rules" "decky plugin (main.py)")
FAILED=()
warn() {{ printf 'warn: %s\\n' "$*" >&2; }}
source {str(record_fn)!r}
record 0
"""
    env = {
        "HOME": str(home),
        "PATH": f"{stubs}:/usr/bin:/bin",
        "STUB_LOG": str(stub_log),
        "Z13_OS_RELEASE": str(os_rel),
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": "C.UTF-8",
    }
    res = real_subprocess(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=20)
    assert res.returncode == 0, res.stdout + res.stderr

    tl_path = home / ".local" / "state" / "z13-helper" / "timeline.jsonl"
    entries = timeline.read_entries(str(tl_path))
    assert [e["kind"] for e in entries] == ["steamos_update", "install"]

    su, inst = entries
    assert su["before"]["build_id"] == "20260922.1"
    assert su["after"]["build_id"] == "20261023.100"
    assert su["rollback"] is None
    assert "boot menu" in su["no_rollback_reason"]

    assert inst["ok"] is True
    assert inst["before"]["z13ctl"] == "1.4.1"
    assert inst["after"]["z13ctl"] == "1.4.2"
    assert inst["rollback"]["files"] == {
        "/etc/polkit-1/rules.d/50-z13-lightbar-reset.rules": str(polkit_bak)
    }
    assert inst["rollback"]["archives"] == [str(backup / "z13-helper-plugin.tar")]

    log_lines = stub_log.read_text().splitlines()
    assert any("logger -t z13-steamos-update" in line and su["id"] in line for line in log_lines)
    assert any("logger -t z13-install" in line and inst["id"] in line for line in log_lines)


def test_steamos_build_id_detection_deduplicates_across_callers(tmp_path, monkeypatch, fake_host):
    tl = tmp_path / "timeline.jsonl"
    monkeypatch.setattr(timeline, "TIMELINE_PATH", str(tl))
    Path(install_record.INSTALL_JSON).write_text(json.dumps({
        "steamos": "3.8.28 (20260922.1)",
        "kernel": "6.18.50-valve2",
    }))
    monkeypatch.setattr(
        update_watch,
        "host_read",
        lambda p: 'VERSION_ID="3.9.5"\nBUILD_ID="20261023.100"\n' if p == "/etc/os-release" else None,
    )

    assert update_watch._steamos_version() == "3.9.5"
    # Second check on the same BUILD_ID must not add a second entry or journal line.
    assert update_watch._steamos_version() == "3.9.5"
    post_update_state.record_if_build_id_changed("20260922.1", "20261023.100")

    entries = timeline.read_entries(str(tl))
    assert len(entries) == 1
    assert entries[0]["kind"] == "steamos_update"
    assert any("BUILD_ID 20260922.1 -> 20261023.100" in c for c in entries[0]["changed"])
    logger_calls = [a for a in fake_host.argvs if a[0] == "logger"]
    assert len(logger_calls) == 1
    assert logger_calls[0][:3] == ["logger", "-t", "z13-steamos-update"]


def test_manifest_and_stdin_installer_ship_timeline_module(tmp_path, real_subprocess):
    import install_check

    manifest_srcs = {rel for rel, *_ in install_check.MANIFEST}
    assert "src/timeline.py" in manifest_srcs
    assert "py_modules/timeline.py" in install_check.DECKY_FILES
    assert "py_modules/timeline.py" in INSTALL_SH.read_text()

    tree = ast.parse((REPO / "src" / "updates_dialog.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_installer_source")
    ns = {
        "host": types.SimpleNamespace(__file__=str(REPO / "src" / "host.py")),
        "_INSTALLERS_SRC": str(REPO / "src" / "installers.py"),
        "_VERSIONS_SRC": str(REPO / "src" / "versions.py"),
    }
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "updates_dialog.py", "exec"), ns)
    src = ns["_installer_source"]()
    assert "sys.modules['timeline']" in src
