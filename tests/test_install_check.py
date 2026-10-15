"""tooling/install_check.py: the manifest install.sh consumes, flatpak_diff, same()."""

import os
import shutil
from pathlib import Path

import pytest

import install_check as ic

REPO = Path(ic.REPO)


def test_repo_root_is_this_checkout():
    assert (REPO / "tooling" / "install_check.py").is_file()
    assert (REPO / "src" / "profiles.py").is_file()


# ----------------------------------------------------------------------
# manifest / keep-list (install.sh parses these with `IFS=$'\t' read`)
# ----------------------------------------------------------------------

def manifest_rows(capsys):
    assert ic.main(["manifest"]) == 0
    return [line.split("\t") for line in capsys.readouterr().out.splitlines()]


def test_manifest_format(capsys):
    rows = manifest_rows(capsys)
    assert len(rows) == len(ic.MANIFEST) > 0
    for row in rows:
        assert len(row) == 4, row
        rel, dest, mode, root = row
        assert not os.path.isabs(rel) and ".." not in rel.split("/")
        assert os.path.isabs(dest)
        assert mode in ("644", "755")
        assert root in ("0", "1")
        assert all(f.strip() == f and f for f in row)       # no stray whitespace


def test_manifest_repo_paths_exist(capsys):
    missing = [rel for rel, *_ in manifest_rows(capsys) if not (REPO / rel).is_file()]
    assert missing == []


def test_manifest_destinations_unique(capsys):
    dests = [dest for _, dest, _, _ in manifest_rows(capsys)]
    assert len(dests) == len(set(dests))


def test_manifest_root_flag_matches_destination(capsys):
    for rel, dest, mode, root in manifest_rows(capsys):
        under_home = dest.startswith(ic.HOME + os.sep)
        assert (root == "0") == under_home, (rel, dest, root)


def test_manifest_profile_cli_is_profiles_py(capsys):
    rows = {dest: rel for rel, dest, _, _ in manifest_rows(capsys)}
    assert rows[os.path.join(ic.BIN, "z13-profile")] == "src/profiles.py"


def test_manifest_modes(capsys):
    for rel, dest, mode, _ in manifest_rows(capsys):
        if dest.endswith((".service", ".py")):
            assert mode == "644", dest
        if dest.endswith(".sh") or os.path.dirname(dest) == ic.BIN and "." not in os.path.basename(dest):
            assert mode == "755", dest


def test_keep_list(capsys):
    assert ic.main(["keep-list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == ic.KEEP_LIST
    assert len(lines) == 7
    assert all(p.startswith("/etc/") for p in lines)


def test_root_files_are_on_keep_list():
    """Anything we put in /etc must survive an atomic SteamOS update."""
    for _rel, dest, _mode, root in ic.MANIFEST:
        if root:
            assert dest in ic.KEEP_LIST, dest


def test_usage_on_unknown_command(capsys):
    assert ic.main([]) == 2
    assert ic.main(["frobnicate"]) == 2
    assert "manifest" in capsys.readouterr().err


# ----------------------------------------------------------------------
# same()
# ----------------------------------------------------------------------

def test_same(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    a.write_bytes(b"x\n")
    b.write_bytes(b"x\n")
    c.write_bytes(b"x")
    assert ic.same(a, b)
    assert not ic.same(a, c)
    assert not ic.same(a, tmp_path / "missing")
    assert not ic.same(tmp_path / "missing", tmp_path / "missing2")
    assert not ic.same(a, tmp_path)                  # directory -> OSError -> False


# ----------------------------------------------------------------------
# flatpak_diff()
# ----------------------------------------------------------------------

@pytest.fixture
def installed(tmp_path):
    """A fake `flatpak info --show-location` tree identical to the repo."""
    loc = tmp_path / "flatpak-app" / "current" / "active"
    tree = loc / "files" / "share" / "z13-helper"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(REPO / "src", tree / "src", ignore=ignore)
    shutil.copytree(REPO / "assets", tree / "assets", ignore=ignore)
    shutil.copy2(REPO / "main.py", tree / "main.py")
    (loc / "files" / "bin").mkdir(parents=True)
    shutil.copy2(REPO / "flatpak" / "launch.sh", loc / "files" / "bin" / "z13-helper-flatpak")
    return loc


def test_flatpak_diff_identical(installed):
    assert ic.flatpak_diff(str(installed)) == []


def test_flatpak_diff_reports_changed_and_missing(installed):
    tree = installed / "files" / "share" / "z13-helper"
    (tree / "src" / "profiles.py").write_text("# stale build\n")
    (tree / "main.py").unlink()
    (installed / "files" / "bin" / "z13-helper-flatpak").write_text("old launcher\n")
    assert sorted(ic.flatpak_diff(str(installed))) == sorted(
        ["src/profiles.py", "main.py", "flatpak/launch.sh"])


def test_flatpak_diff_ignores_extra_installed_files_and_pyc(installed):
    tree = installed / "files" / "share" / "z13-helper"
    (tree / "src" / "__pycache__").mkdir()
    (tree / "src" / "__pycache__" / "profiles.cpython-312.pyc").write_bytes(b"\0")
    (tree / "src" / "removed_module.py").write_text("")
    assert ic.flatpak_diff(str(installed)) == []


def test_flatpak_diff_not_installed_tree(tmp_path):
    d = ic.flatpak_diff(str(tmp_path / "nothing"))
    assert "main.py" in d and "src/profiles.py" in d


def test_cli_flatpak_diff(installed, monkeypatch, capsys):
    monkeypatch.setattr(ic, "flatpak_location", lambda: None)
    assert ic.main(["flatpak-diff"]) == 2
    assert capsys.readouterr().out.strip() == "not installed"

    monkeypatch.setattr(ic, "flatpak_location", lambda: str(installed))
    assert ic.main(["flatpak-diff"]) == 0
    capsys.readouterr()
    (installed / "files" / "share" / "z13-helper" / "src" / "theme.py").write_text("x")
    assert ic.main(["flatpak-diff"]) == 1
    assert capsys.readouterr().out.strip() == "src/theme.py"


# ----------------------------------------------------------------------
# Report-producing checks that need no hardware
# ----------------------------------------------------------------------

def statuses(report):
    return {name: (status, detail) for status, name, detail in report.rows if status}


def test_check_files(tmp_path, monkeypatch):
    good, bad, gone = tmp_path / "good", tmp_path / "bad", tmp_path / "gone"
    shutil.copy2(REPO / "src" / "profiles.py", good)
    bad.write_text("old\n")
    monkeypatch.setattr(ic, "MANIFEST", [
        ("src/profiles.py", str(good), "755", False),
        ("src/profiles.py", str(bad), "755", False),
        ("src/profiles.py", str(gone), "755", False),
    ])
    r = ic.Report()
    ic.check_files(r)
    st = statuses(r)
    assert st["good"][0] == ic.GREEN
    assert st["bad"] == (ic.RED, "differs from src/profiles.py")
    assert st["gone"][0] == ic.RED and "missing" in st["gone"][1]


def test_lightbar_reset_files_in_manifest_and_keep_list():
    rows = {rel: (dest, mode, root) for rel, dest, mode, root in ic.MANIFEST}
    assert rows["packaging/host/systemd/system/z13-lightbar-reset.sh"] == \
        ("/etc/systemd/system/z13-lightbar-reset.sh", "755", True)
    assert rows["packaging/host/systemd/system/z13-lightbar-reset.service"] == \
        ("/etc/systemd/system/z13-lightbar-reset.service", "644", True)
    assert rows["packaging/host/polkit/50-z13-lightbar-reset.rules"] == \
        ("/etc/polkit-1/rules.d/50-z13-lightbar-reset.rules", "644", True)
    for rel, (dest, _, _) in rows.items():
        if "lightbar" in rel:
            assert dest in ic.KEEP_LIST
            assert (REPO / rel).is_file()


def test_lightbar_unit_is_not_enabled_and_runs_the_script():
    unit = (REPO / "packaging/host/systemd/system/z13-lightbar-reset.service").read_text()
    assert "[Install]" not in unit            # started on demand only
    assert "Type=oneshot" in unit
    assert "ExecStart=/etc/systemd/system/z13-lightbar-reset.sh" in unit


def test_polkit_rule_scope():
    rule = (REPO / "packaging/host/polkit/50-z13-lightbar-reset.rules").read_text()
    assert 'action.id == "org.freedesktop.systemd1.manage-units"' in rule
    assert 'action.lookup("unit") == "z13-lightbar-reset.service"' in rule
    assert 'action.lookup("verb") == "start"' in rule
    assert 'subject.user == "deck"' in rule
    assert rule.count("polkit.Result.YES") == 1
    assert "restart" not in rule and "stop" not in rule


def test_same_root_unreadable_falls_back_to_sudo_cmp(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.write_text("x")
    calls = []
    monkeypatch.setattr(ic.os, "stat", lambda p: (_ for _ in ()).throw(PermissionError(p)))
    monkeypatch.setattr(ic, "sh", lambda argv, timeout=60: calls.append(argv) or (0, ""))
    assert ic.same_root(str(src), "/etc/polkit-1/rules.d/x.rules") is True
    assert calls == [["sudo", "-n", "true"],
                     ["sudo", "-n", "cmp", "-s", str(src), "/etc/polkit-1/rules.d/x.rules"]]
    monkeypatch.setattr(ic, "sh", lambda argv, timeout=60: (0, "") if "true" in argv else (1, ""))
    assert ic.same_root(str(src), "/x") is False
    # No passwordless sudo: can't tell (not "differs").
    monkeypatch.setattr(ic, "sh", lambda argv, timeout=60: (1, "sudo: a password is required"))
    assert ic.same_root(str(src), "/x") is None


def test_same_root_readable_and_missing(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("1")
    b.write_text("1")
    assert ic.same_root(str(a), str(b)) is True
    assert ic.same_root(str(a), str(tmp_path / "nope")) is False


def test_check_files_root_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(ic, "MANIFEST", [
        ("src/profiles.py", "/etc/a", "644", True),
        ("src/profiles.py", "/etc/b", "644", True),
        ("src/profiles.py", "/etc/c", "644", True),
    ])
    monkeypatch.setattr(ic, "same_root", lambda s, d: {"/etc/a": True, "/etc/b": False, "/etc/c": None}[d])
    r = ic.Report()
    ic.check_files(r)
    st = statuses(r)
    assert st["a"][0] == ic.GREEN and st["b"][0] == ic.RED and st["c"][0] == ic.YELLOW


def test_check_units_lightbar_is_read_only(monkeypatch, tmp_path):
    seen = []

    def sh(argv, timeout=60):
        seen.append(argv)
        return 0, "enabled" if "is-enabled" in argv else "active"
    monkeypatch.setattr(ic, "sh", sh)
    monkeypatch.setattr(ic, "KEEP_LIST_FILE", str(tmp_path / "none"))
    r = ic.Report()
    ic.check_units(r)
    assert ["systemctl", "cat", "z13-lightbar-reset.service"] in seen
    assert not any("start" in a or "restart" in a for a in seen if "z13-lightbar-reset.service" in a)
    assert statuses(r)["lightbar reset unit"][0] == ic.GREEN


def test_check_restore_grades_z13_profile_output(monkeypatch, capsys):
    """Feed check_restore real `z13-profile restore` output (profiles._print_results)."""
    import profiles
    print("Applying profile: Silent")
    profiles._print_results([
        ("profile", True, "quiet"),
        ("fans", True, "firmware auto"),
        ("undervolt", False, "unavailable (ryzen_smu not loaded)"),
        ("brightness (keyboard)", True, "low"),
        ("brightness (lightbar)", False, "timed out"),
        ("battery limit", False, "permission denied"),
    ])
    out = capsys.readouterr().out
    monkeypatch.setattr(ic, "sh", lambda argv, timeout=60: (1, out))
    r = ic.Report()
    ic.check_restore(r)
    st = statuses(r)
    assert st["profile"] == (ic.GREEN, "quiet")
    assert st["fans"][0] == ic.GREEN
    assert st["undervolt"][0] == ic.YELLOW
    assert st["brightness (keyboard)"][0] == ic.GREEN
    assert st["brightness (lightbar)"][0] == ic.YELLOW
    assert st["battery limit"] == (ic.RED, "permission denied")


def test_check_restore_no_output_is_red(monkeypatch):
    monkeypatch.setattr(ic, "sh", lambda argv, timeout=60: (127, "No such file or directory"))
    r = ic.Report()
    ic.check_restore(r)
    assert statuses(r)["z13-profile restore"][0] == ic.RED


def test_report_exit_code(capsys):
    r = ic.Report()
    r.head("x")
    r.add(ic.GREEN, "a")
    r.add(ic.YELLOW, "b")
    assert r.print() == 0
    r.add(ic.RED, "c")
    assert r.print() == 1
    assert "1 ok, 1 warning(s), 1 failure(s)" in capsys.readouterr().out
