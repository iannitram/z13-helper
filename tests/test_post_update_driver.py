"""Post-update driver: readonly tool choice, backups, argument handling (issues #67, #68)."""

import pytest

from post_update import driver
from post_update.recipe import Recipe


def make_recipes(log, check="needs"):
    def mk(id, **kw):
        return Recipe(id=id, title=id, fixes=id, check=lambda: check,
                      apply=lambda: log.append(f"apply:{id}"), **kw)
    return [mk("keyring", pacman=True), mk("amdgpu"), mk("rocm", pacman=True)]


@pytest.mark.parametrize("chosen", ["holo-readonly", "steamos-readonly"])
def test_env_uses_the_chosen_readonly_tool_and_logs_it(tmp_path, caplog, chosen):
    calls = []
    caplog.set_level("INFO")
    env = driver.make_env(str(tmp_path), run=lambda argv: calls.append(argv) or (0, ""),
                          tool_name=lambda name: chosen if name == "readonly" else name)
    env.readonly_disable()
    env.readonly_enable()
    assert calls == [["sudo", "-n", chosen, "disable"], ["sudo", "-n", chosen, "enable"]]
    assert chosen in caplog.text


def test_readonly_failure_raises(tmp_path):
    env = driver.make_env(str(tmp_path), run=lambda argv: (1, "nope"),
                          tool_name=lambda n: "steamos-" + n)
    with pytest.raises(RuntimeError, match="steamos-readonly disable failed"):
        env.readonly_disable()


def test_backup_copies_file_under_state_and_skips_missing(tmp_path):
    src = tmp_path / "etc" / "grub"
    src.parent.mkdir()
    src.write_text("old")
    env = driver.make_env(str(tmp_path), run=lambda a: (0, ""), tool_name=lambda n: n, stamp="S")
    env.backup(str(src))
    env.backup(str(tmp_path / "missing"))
    copy = tmp_path / driver.BACKUP_ROOT / "S" / str(src).lstrip("/")
    assert copy.read_text() == "old"


def test_only_runs_just_those_ids_and_adds_keyring_for_pacman(tmp_path):
    log = []
    env = driver.make_env(str(tmp_path), run=lambda a: (0, ""), tool_name=lambda n: n)
    rc = driver.main(["--post-update", "--only", "amdgpu"], recipes=make_recipes(log), env=env)
    assert rc == 0 and log == ["apply:amdgpu"]
    log.clear()
    driver.main(["--post-update", "--only", "rocm"], recipes=make_recipes(log), env=env)
    assert log == ["apply:keyring", "apply:rocm"]


def test_unknown_id_and_bad_args_run_nothing(capsys):
    log = []
    assert driver.main(["--post-update", "--only", "nope"], recipes=make_recipes(log)) == 2
    assert driver.main(["--only", "amdgpu"], recipes=make_recipes(log)) == 2
    assert log == []


def test_pick_from_a_list(tmp_path):
    log = []
    env = driver.make_env(str(tmp_path), run=lambda a: (0, ""), tool_name=lambda n: n)
    driver.main(["--post-update"], recipes=make_recipes(log), env=env, input_fn=lambda _: "2")
    assert log == ["apply:amdgpu"]


def test_list_pending_changes_nothing(capsys):
    log = []
    assert driver.main(["--list-pending"], recipes=make_recipes(log)) == 0
    assert "keyring, amdgpu, rocm" in capsys.readouterr().out and log == []
    driver.main(["--list-pending"], recipes=make_recipes(log, check="ok"))
    assert "none" in capsys.readouterr().out


def test_failed_task_gives_exit_1(tmp_path):
    def bad():
        raise RuntimeError("x")
    r = Recipe(id="a", title="a", fixes="a", check=lambda: "needs", apply=bad)
    env = driver.make_env(str(tmp_path), run=lambda a: (0, ""), tool_name=lambda n: n)
    assert driver.main(["--post-update", "--only", "a"], recipes=[r], env=env) == 1


def test_listing_never_escalates_to_root_and_skips_checks_that_need_it():
    io = driver.make_hostio(escalate=False)
    with pytest.raises(PermissionError):
        io.run(["pacman-key", "--list-keys"])

    def needs_root():
        raise PermissionError("root")
    rs = [Recipe(id="k", title="k", fixes="k", check=needs_root, apply=lambda: None),
          Recipe(id="a", title="a", fixes="a", check=lambda: "needs", apply=lambda: None)]
    needs, unchecked = driver.classify(rs)
    assert [r.id for r in needs] == ["a"] and [r.id for r in unchecked] == ["k"]
    assert [r.id for r in driver.pending(rs)] == ["a", "k"]   # never silently dropped


def test_list_pending_says_which_tasks_could_not_be_checked(capsys):
    def needs_root():
        raise PermissionError("pacman-key needs root")
    rs = [Recipe(id="keyring", title="k", fixes="k", check=needs_root, apply=lambda: None)]
    assert driver.main(["--list-pending"], recipes=rs) == 0
    out = capsys.readouterr().out
    assert "pending: none" in out and "Could not check without root" in out and "keyring" in out
