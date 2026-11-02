"""Post-update recipes (issue #67, part a): ordering, keyring insertion, plan text."""

from post_update.recipe import Recipe, order_tasks, plan_text, with_keyring


def r(id, **flags):
    return Recipe(id=id, title=f"T-{id}", fixes=f"fixes {id}",
                  check=lambda: "ok", apply=lambda: None, **flags)


KEYRING = r("keyring", sudo=True)
AMDGPU = r("amdgpu", sudo=True, reboot=True)
ROCM = r("rocm", sudo=True, network=True, pacman=True)
BT = r("bt", sudo=True)


def ids(rs):
    return [x.id for x in rs]


def test_order_keyring_first_reboot_last_rest_stable():
    given = (AMDGPU, ROCM, KEYRING, BT)
    assert ids(order_tasks(given)) == ["keyring", "rocm", "bt", "amdgpu"]


def test_order_keeps_given_order_inside_a_group():
    a, b, c = r("a"), r("b"), r("c", reboot=True)
    d = r("d", reboot=True)
    assert ids(order_tasks((d, b, c, a))) == ["b", "a", "d", "c"]


def test_order_returns_new_list_and_handles_empty():
    given = (BT,)
    out = order_tasks(given)
    assert isinstance(out, list) and out == [BT]
    assert order_tasks(()) == []


def test_with_keyring_adds_it_in_front_when_a_pacman_task_is_selected():
    out = with_keyring((ROCM, BT), (BT, KEYRING, ROCM))
    assert ids(out) == ["keyring", "rocm", "bt"]


def test_with_keyring_unchanged_when_not_needed():
    assert ids(with_keyring((BT,), (BT, KEYRING, ROCM))) == ["bt"]
    assert ids(with_keyring((KEYRING, ROCM), (KEYRING, ROCM))) == ["keyring", "rocm"]
    assert ids(with_keyring((ROCM,), (ROCM, BT))) == ["rocm"]


def test_plan_text_nothing_selected():
    assert plan_text(()) == "Plan: nothing selected"


def test_plan_text_one_task_no_flags():
    assert plan_text((r("x"),)) == "Plan: 1 task\n1. x: T-x - fixes x"


def test_plan_text_flags_in_fixed_order_and_reboot_note():
    text = plan_text((KEYRING, ROCM, AMDGPU))
    assert text == (
        "Plan: 3 tasks\n"
        "1. keyring: T-keyring - fixes keyring\n"
        "   needs: sudo\n"
        "2. rocm: T-rocm - fixes rocm\n"
        "   needs: sudo, network, pacman\n"
        "3. amdgpu: T-amdgpu - fixes amdgpu\n"
        "   needs: sudo, reboot\n"
        "A reboot is needed after this run. The app never reboots by itself."
    )
