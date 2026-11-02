"""Post-update runner (issue #67, part b): check-never-writes, backup-before-replace, readonly re-enable."""

import pytest

from post_update.recipe import Recipe
from post_update.runner import Env, check_all, run_plan


class Log(list):
    def env(self):
        return Env(backup=lambda p: self.append(f"backup:{p}"),
                   readonly_disable=lambda: self.append("ro-off"),
                   readonly_enable=lambda: self.append("ro-on"))

    def recipe(self, id, check="needs", fail=False, **flags):
        def apply():
            self.append(f"apply:{id}")
            if fail:
                raise RuntimeError("boom")
        return Recipe(id=id, title=id, fixes=id, apply=apply,
                      check=lambda: self.append(f"check:{id}") or check, **flags)


def test_check_all_returns_results_and_never_applies_or_touches_env():
    log = Log()
    rs = (log.recipe("a", check="ok", pacman=True, replaces=("/etc/a",)), log.recipe("b", check="info"))
    assert check_all(rs) == {"a": "ok", "b": "info"}
    assert log == ["check:a", "check:b"]


def test_backup_every_replaced_file_before_apply():
    log = Log()
    rs = (log.recipe("a", replaces=("/etc/one", "/etc/two")),)
    assert run_plan(rs, log.env()) == [("a", "done")]
    assert [x for x in log if not x.startswith("check")] == [
        "backup:/etc/one", "backup:/etc/two", "apply:a"]


def test_readonly_disabled_once_for_pacman_tasks_and_enabled_after():
    log = Log()
    rs = (log.recipe("p1", pacman=True), log.recipe("plain"), log.recipe("p2", pacman=True))
    assert run_plan(rs, log.env()) == [("p1", "done"), ("plain", "done"), ("p2", "done")]
    assert [x for x in log if not x.startswith("check")] == [
        "ro-off", "apply:p1", "apply:plain", "apply:p2", "ro-on"]


def test_readonly_untouched_without_pacman_tasks():
    log = Log()
    run_plan((log.recipe("plain"),), log.env())
    assert "ro-off" not in log and "ro-on" not in log


def test_readonly_re_enabled_after_failure_and_rest_skipped():
    log = Log()
    rs = (log.recipe("p1", pacman=True, fail=True), log.recipe("after", replaces=("/etc/x",)))
    assert run_plan(rs, log.env()) == [("p1", "failed"), ("after", "skipped")]
    assert log[-1] == "ro-on"
    assert "apply:after" not in log and "backup:/etc/x" not in log


def test_readonly_re_enabled_when_disable_itself_fails():
    log = Log()
    env = Env(backup=lambda p: None,
              readonly_disable=lambda: (log.append("ro-off"), (_ for _ in ()).throw(OSError("x")))[0],
              readonly_enable=lambda: log.append("ro-on"))
    assert run_plan((log.recipe("p1", pacman=True),), env) == [("p1", "failed")]
    assert "apply:p1" not in log and log[-1] == "ro-on"


def test_not_applicable_task_is_skipped_without_apply_or_backup():
    log = Log()
    rs = (log.recipe("n", check="na", replaces=("/etc/n",), pacman=True),)
    assert run_plan(rs, log.env()) == [("n", "skipped")]
    assert all(x.startswith("check") for x in log)


def test_run_plan_never_raises_on_failure():
    log = Log()
    assert run_plan((log.recipe("f", fail=True),), log.env()) == [("f", "failed")]
