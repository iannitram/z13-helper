"""Reviewer checks for post-update recipes (issue #109): behaviour the locked test does not cover."""

from post_update.recipe import Recipe, order_tasks, plan_text, with_keyring


def boom():
    raise AssertionError("check/apply must not run in the pure helpers")


def r(id, **flags):
    return Recipe(id=id, title=f"T-{id}", fixes=f"fixes {id}",
                  check=boom, apply=boom, **flags)


def ids(rs):
    return [x.id for x in rs]


def test_order_keeps_every_keyring_entry_in_front():
    k1, k2 = r("keyring", sudo=True), r("keyring", sudo=True)
    bt, amd = r("bt"), r("amdgpu", reboot=True)
    assert order_tasks((amd, k1, bt, k2)) == [k1, k2, bt, amd]


def test_order_does_not_mutate_input():
    given = [r("amdgpu", reboot=True), r("keyring"), r("bt")]
    snapshot = list(given)
    order_tasks(given)
    assert given == snapshot


def test_with_keyring_does_not_mutate_input_and_skips_missing_keyring():
    selected = [r("rocm", pacman=True)]
    available = [r("bt")]
    out = with_keyring(selected, available)
    assert out == selected and out is not selected
    assert ids(available) == ["bt"]


def test_helpers_never_call_check_or_apply():
    recipes = [r("keyring"), r("rocm", pacman=True, reboot=True), r("bt")]
    order_tasks(recipes)
    with_keyring(recipes, recipes)
    plan_text(recipes)
