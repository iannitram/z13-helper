"""Rollback records for applied recipes, repair subcommands and the real Env (issue #27)."""

import json
import os

import pytest

from post_update import driver, repair, tab
from post_update.recipe import Recipe
from post_update.runner import Env, run_plan
from repair_plan import RepairIO
from test_repair_plan import GRUB, Machine
from post_update.builtin import GRUB_FILE


def recipe(id, apply=lambda: None, **kw):
    return Recipe(id=id, title=id, fixes=id, check=lambda: "needs", apply=apply, **kw)


class Rec:
    def __init__(self, present=()):
        self.present, self.entries, self.restored = set(present), [], []

    def env(self):
        return Env(backup=lambda p: ("/bk" + p) if p in self.present else None,
                   readonly_disable=lambda: None, readonly_enable=lambda: None,
                   restore=lambda p, b: self.restored.append((p, b)), record=self.entries.append)


def test_every_applied_recipe_gets_a_record_with_backup_and_undo():
    r = Rec(present={"/etc/a"})
    assert run_plan([recipe("x", replaces=("/etc/a", "/etc/new"))], r.env()) == [("x", "done")]
    e = r.entries[0]
    assert e["id"] == "x" and e["status"] == "done"
    assert e["backups"] == {"/etc/a": "/bk/etc/a", "/etc/new": None}
    assert e["created"] == ["/etc/new"]                      # absent before: undo removes it
    assert "copy /bk/etc/a back to /etc/a" in e["undo"] and "remove /etc/new" in e["undo"]


def test_creates_and_undo_note_are_recorded():
    r = Rec()
    run_plan([recipe("x", creates=lambda: ["/usr/lib/pkgconfig/a.pc"], undo="packages stay")], r.env())
    assert r.entries[0]["created"] == ["/usr/lib/pkgconfig/a.pc"]
    assert "remove /usr/lib/pkgconfig/a.pc" in r.entries[0]["undo"] and "packages stay" in r.entries[0]["undo"]


def test_recipe_with_no_files_says_it_has_no_automatic_undo():
    r = Rec()
    run_plan([recipe("x")], r.env())
    assert "no automatic undo" in r.entries[0]["undo"]


def test_failed_recipe_is_rolled_back_from_its_backups():
    r = Rec(present={"/etc/a"})

    def boom():
        raise RuntimeError("boom")
    assert run_plan([recipe("x", apply=boom, replaces=("/etc/a", "/etc/new"))], r.env()) == [("x", "failed")]
    assert r.restored == [("/etc/a", "/bk/etc/a"), ("/etc/new", None)]
    e = r.entries[0]
    assert e["status"] == "failed" and e["rollback"] == "rolled back" and e["error"] == "boom"


def test_failure_before_anything_changed_is_not_a_rollback():
    r = Rec()

    def boom():
        raise RuntimeError("boom")
    run_plan([recipe("x", apply=boom)], r.env())
    assert r.restored == [] and r.entries[0]["rollback"] == ""


def test_rollback_that_fails_is_recorded():
    r = Rec(present={"/etc/a"})
    env = Env(backup=r.env().backup, readonly_disable=lambda: None, readonly_enable=lambda: None,
              restore=lambda p, b: (_ for _ in ()).throw(RuntimeError("no sudo")), record=r.entries.append)

    def boom():
        raise RuntimeError("boom")
    run_plan([recipe("x", apply=boom, replaces=("/etc/a",))], env)
    assert r.entries[0]["rollback"] == "rollback failed" and "no sudo" in r.entries[0]["rollback_error"]


def test_real_env_backs_up_folders_and_files_records_and_restores(tmp_path):
    calls = []
    env = driver.make_env(str(tmp_path), run=lambda a: calls.append(a) or (0, ""),
                          tool_name=lambda n: n, stamp="S")
    src = tmp_path / "etc" / "grub"
    src.parent.mkdir()
    src.write_text("old")
    dest = env.backup(str(src))
    assert open(dest).read() == "old" and env.backup(str(tmp_path / "gone")) is None
    folder = tmp_path / "gnupg"
    folder.mkdir()
    folder_copy = env.backup(str(folder))
    assert folder_copy.endswith(str(folder).lstrip("/"))
    assert any(c[-4:-1] == ["cp", "-a", "--"] or "cp" in c for c in calls)   # folder copied through cp -a
    env.restore("/etc/x/y", None)
    assert calls[-1][-4:] == ["rm", "-rf", "--", "/etc/x/y"]
    env.restore("/etc/x/y", dest)
    assert calls[-1][-3:] == ["--", dest, "/etc/x/y"]
    with pytest.raises(RuntimeError, match="refusing"):
        env.restore("/etc", None)
    env.record({"id": "a"})
    env.record({"id": "b"})
    path = tmp_path / driver.BACKUP_ROOT / "S" / driver.RECORD_NAME
    assert [e["id"] for e in json.loads(path.read_text())] == ["a", "b"]


def test_real_env_restore_uses_sudo_unless_root(tmp_path, monkeypatch):
    calls = []
    env = driver.make_env(str(tmp_path), run=lambda a: calls.append(a) or (0, ""), tool_name=lambda n: n)
    monkeypatch.setattr(driver.os, "geteuid", lambda: 1000)
    env.restore("/etc/a/b", None)
    assert calls[-1][:2] == ["sudo", "-n"]
    monkeypatch.setattr(driver.os, "geteuid", lambda: 0)
    env.restore("/etc/a/b", None)
    assert calls[-1][0] == "rm"


def test_keyring_and_packs_declare_what_they_replace_or_create():
    from post_update.builtin import KEYRING_DIR, builtin_recipes
    from post_update.packs import pack_recipes
    io = driver.HostIO(read=lambda p: None, write=lambda p, t: None, exists=lambda p: False,
                       listdir=lambda p: [], run=lambda a: (1, ""))
    assert KEYRING_DIR in {r.id: r for r in builtin_recipes(io)}["keyring"].replaces
    packs = {r.id: r for r in pack_recipes(io)}
    assert packs["dev-headers"].creates is not None and packs["dev-headers"].undo
    assert packs["coredump-cap"].replaces


def test_run_writes_a_reboot_marker_that_clears_after_a_reboot(tmp_path, monkeypatch):
    boot = tmp_path / "boot_id"
    boot.write_text("abc\n")
    monkeypatch.setattr(tab, "BOOT_ID", str(boot))
    tab.mark_reboot(str(tmp_path), ["amdgpu"])
    io = driver.make_hostio(False)
    assert tab.reboot_ids(str(tmp_path), io) == {"amdgpu"}
    boot.write_text("def\n")
    assert tab.reboot_ids(str(tmp_path), io) == set()
    rows = tab.build_rows([recipe("amdgpu")], reboot={"amdgpu"})
    assert rows[0]["state"] == "reboot"


def test_banner_counts_tasks_and_unknown_rows_show_their_reason():
    rows = [{"state": "needs"}, {"state": "unknown"}, {"state": "fine"}]
    assert tab.banner_text(rows) == "SteamOS updated: 2 tasks"
    assert tab.banner_text(rows[:1], "kernel 7.x to 8.x") == "SteamOS updated: 1 task (kernel 7.x to 8.x)"


# ---- repair / checks / bundle / suspend subcommands
def read_seam(files=None):
    from test_system_inventory import GOOD, fake
    return fake(dict(GOOD, **(files or {})))


def saved():
    box = []
    return box, box.append


def test_repair_prints_plan_asks_and_runs_on_yes():
    m = Machine({GRUB_FILE: GRUB})
    out, asked = [], []
    box, save = saved()
    rc = repair.repair_main([], lambda t: None, m.io(), save,
                            input_fn=lambda q: asked.append(q) or "y", out=out.append)
    text = "\n".join(out)
    assert len(asked) == 1 and "sudo -n" in asked[0]
    assert "$ sudo -n update-grub" in text and "kernel_args: applied" in text
    assert m.root_writes == [GRUB_FILE] and box and rc == 1      # other steps could not be fixed here


def test_repair_answer_no_runs_nothing():
    m = Machine({GRUB_FILE: GRUB})
    out = []
    box, save = saved()
    rc = repair.repair_main([], lambda t: None, m.io(), save, input_fn=lambda q: "n", out=out.append)
    assert rc == 0 and "Nothing was run." in out and m.calls == [] and box == []


def test_repair_yes_runs_without_asking_and_saves_the_record():
    m = Machine({GRUB_FILE: GRUB})
    box, save = saved()
    out = []

    def no_input(q):
        raise AssertionError("must not ask")
    rc = repair.repair_main(["--yes"], lambda t: None, m.io(), save, input_fn=no_input, out=out.append)
    assert m.root_writes == [GRUB_FILE]             # a root step ran, only because --yes confirmed
    assert box and box[-1]["kernel_args"]["status"] == "applied" and rc == 1


def test_repair_json_lists_the_plan_and_changes_nothing():
    m = Machine({GRUB_FILE: GRUB})
    out = []
    box, save = saved()
    assert repair.repair_main(["--json"], lambda t: None, m.io(), save, out=out.append) == 0
    plan = json.loads(out[0])
    ids = [s["id"] for s in plan["steps"]]
    assert "kernel_args" in ids and plan["unplanned"] == []
    s = next(s for s in plan["steps"] if s["id"] == "kernel_args")
    assert s["needs_root"] and s["commands"] and s["undo"]
    assert m.calls == [] and m.writes == [] and box == []


def test_repair_run_only_the_confirmed_ids():
    m = Machine({GRUB_FILE: GRUB}, stats={})
    out = []
    box, save = saved()
    rc = repair.repair_main(["--json", "--run", "kernel_args"], lambda t: None, m.io(), save, out=out.append)
    res = json.loads(out[0])
    assert res["results"] == [["kernel_args", "applied"]] and rc == 0
    assert m.root_writes == [GRUB_FILE] and len(box) == 2        # record saved before and after
    assert repair.repair_main(["--run", "nope"], lambda t: None, m.io(), save, out=out.append) == 2
    assert repair.repair_main(["--run", "a b"], lambda t: None, m.io(), save, out=out.append) == 2


def test_repair_with_nothing_failing_says_so(monkeypatch):
    monkeypatch.setattr(repair, "failing", lambda read: [])
    out = []
    m = Machine()
    assert repair.repair_main([], lambda t: None, m.io(), lambda r: None, out=out.append) == 0
    assert out == ["Nothing to repair."] and m.calls == []


def test_checks_suspend_and_bundle_are_read_only_commands(tmp_path):
    read = read_seam()
    out = []
    assert repair.checks_main(read, out.append) in (0, 1) and any("FAIL" in l or "ok" in l for l in out)
    out.clear()
    assert repair.suspend_main(read, out.append) == 0 and len(out) == 3
    out.clear()
    written = {}
    assert repair.bundle_main([], read, lambda p, t: written.update({p: t}), out=out.append) == 0
    assert written == {} and "excluded" in out[0]                 # preview only
    target = str(tmp_path / "b.json")
    assert repair.bundle_main(["--out", target], read, lambda p, t: written.update({p: t}),
                              input_fn=lambda q: "n", out=out.append) == 0
    assert written == {}
    assert repair.bundle_main(["--out", target, "--yes"], read, lambda p, t: written.update({p: t}),
                              out=out.append) == 0
    assert written[target] == out[0]                              # exactly the text that was shown
    assert repair.bundle_main(["--out"], read, lambda p, t: None, out=out.append) == 2


def test_driver_dispatches_subcommands_and_ignores_other_args():
    assert driver.subcommand(["--list-pending"]) is None and driver.subcommand([]) is None
    out = []
    assert driver.subcommand(["suspend"], out=out.append, read=read_seam()) == 0 and out
    m = Machine({GRUB_FILE: GRUB})
    assert driver.subcommand(["repair", "--json"], out=out.append, io=m.io(), read=lambda t: None) == 0


def test_plan_only_prints_and_never_asks_or_runs():
    m = Machine({GRUB_FILE: GRUB})
    out = []
    assert repair.repair_main(["--plan-only"], lambda t: None, m.io(), lambda r: None,
                              input_fn=lambda q: 1 / 0, out=out.append) == 0
    assert any("kernel_args" in l for l in out) and m.calls == []


def test_install_sh_wires_the_system_check_flag_to_the_driver_subcommands():
    text = open(os.path.join(os.path.dirname(__file__), "..", "install.sh"), encoding="utf-8").read()
    assert "--system-check)" in text and "checks|repair|suspend|bundle" in text
    assert 'post_update_py "${sc[@]}"' in text and "--plan-only" in text
