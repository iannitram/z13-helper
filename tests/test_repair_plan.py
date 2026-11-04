"""Repair plan: steps with apply/verify/rollback and a rollback record (issue #27)."""

from dataclasses import fields

from post_update.builtin import AMDGPU_ARGS, GRUB_FILE
from post_update_checks import CheckResult
from repair_plan import RepairIO, RepairPlan, RepairStep, plan_for
from post_update_checks import run_checks
from system_inventory import PERMISSION_PATHS, collect_inventory
from test_system_inventory import fake

GRUB = 'GRUB_TIMEOUT=0\nGRUB_CMDLINE_LINUX_DEFAULT="quiet splash"\n'


ALWAYS = lambda step: True
RULE = PERMISSION_PATHS[1]


class Machine:
    """Fake filesystem and command seam. `root` records what ran with root; nothing real runs."""

    def __init__(self, files=None, fail=(), stats=None, installed=("mesa",)):
        self.files = dict(files or {})
        self.fail = set(fail)          # first word of a command (or "pacman -S") that returns 1
        self.calls, self.root, self.writes, self.root_writes = [], [], [], []
        self.enabled = set()
        self.stats = dict(stats or {})
        self.installed = set(installed)
        self.readonly = True

    def read(self, path):
        return self.files.get(path)

    def write(self, path, text):
        self.writes.append(path)
        self.files[path] = text

    def write_root(self, path, text):
        self.root_writes.append(path)
        self.files[path] = text

    def run(self, argv, root=False):
        argv = list(argv)
        self.calls.append(argv)
        if root:
            self.root.append(argv)
        key = " ".join(argv[:2])
        if argv[0] in self.fail or key in self.fail:
            return 1, "failed"
        if argv[0] == "steamos-readonly":
            self.readonly = argv[1] == "enable"
        if argv[:1] == ["systemctl"]:
            user = argv[1] == "--user"
            verb, unit = argv[2 if user else 1], argv[-1]
            name = ("u:" if user else "s:") + unit
            if verb == "is-enabled":
                return (0, "enabled") if name in self.enabled else (1, "disabled")
            if verb == "enable":
                self.enabled.add(name)
            if verb == "disable":
                self.enabled.discard(name)
        if argv[:2] == ["pacman", "-Q"]:
            return (0, argv[2] + " 1") if argv[2] in self.installed else (1, "not found")
        if argv[:2] == ["pacman", "-S"]:
            self.installed.update(a for a in argv[2:] if not a.startswith("-"))
        if argv[:2] == ["pacman", "-R"]:
            self.installed.difference_update(argv[3:])
        if argv[:1] == ["stat"]:
            return (0, self.stats[argv[-1]]) if argv[-1] in self.stats else (1, "no such file")
        if argv[:1] == ["chmod"]:
            self.stats[argv[-1]] = argv[1] + " " + self.stats[argv[-1]].split()[1]
        if argv[:1] == ["chown"]:
            self.stats[argv[-1]] = self.stats[argv[-1]].split()[0] + " " + argv[1]
        return 0, ""

    def io(self):
        return RepairIO(self.read, self.write, self.run, "/bk",
                        run_root=lambda a: self.run(a, root=True), write_root=self.write_root,
                        readonly_tool="steamos-readonly")


def one(m, check_id):
    plan = plan_for([CheckResult(check_id, False, "")], m.io())
    return plan, plan.run(ALWAYS)


def test_step_fields_and_no_failures_means_no_steps():
    assert [f.name for f in fields(RepairStep)] == [
        "check_id", "description", "apply", "verify", "rollback", "needs_root", "guided",
        "changes", "commands", "rollback_note"]
    plan = plan_for([CheckResult("kernel_args", True, "fine")], Machine().io())
    assert isinstance(plan, RepairPlan) and plan.steps == []


def test_building_a_plan_touches_nothing_and_only_unknown_ids_are_unplanned():
    m = Machine({GRUB_FILE: GRUB})
    plan = plan_for([{"check_id": "kernel_args", "detail": "x"},
                     {"check_id": "firmware", "detail": "x"},
                     {"check_id": "made_up", "detail": "x"}], m.io())
    assert [s.check_id for s in plan.steps] == ["kernel_args", "firmware"]
    assert plan.record["unplanned"] == ["made_up"]
    assert plan.record["kernel_args"]["status"] == "planned"
    assert m.writes == [] and m.calls == [] and m.root_writes == []


def test_every_check_the_inventory_can_fail_has_a_step_or_guided_action():
    rows = run_checks(collect_inventory(fake({})))       # nothing readable: every check fails
    plan = plan_for(rows, Machine().io())
    assert plan.record["unplanned"] == []
    assert {s.check_id for s in plan.steps} == {r.check_id for r in rows}
    assert all(s.commands and s.rollback_note for s in plan.steps)


def test_nothing_with_root_or_guided_runs_without_a_yes():
    m = Machine({GRUB_FILE: GRUB})
    plan = plan_for([CheckResult("kernel_args", False, ""),
                     CheckResult("unit:system:z13-fan-resume.service", False, "")], m.io())
    assert plan.run() == [("kernel_args", "not confirmed"),
                          ("unit:system:z13-fan-resume.service", "not confirmed")]
    assert plan.run(lambda step: False)[0][1] == "not confirmed"
    assert m.calls == [] and m.writes == [] and m.root_writes == []


def test_describe_lists_commands_root_and_undo():
    plan = plan_for([CheckResult("kernel_args", False, ""), CheckResult("made_up", False, "")],
                    Machine().io())
    text = "\n".join(plan.describe())
    assert "needs root (sudo -n)" in text and "$ sudo -n update-grub" in text
    assert "undo: copy /bk/etc/default/grub-steamos back" in text and "made_up: no repair known" in text


# ---- kernel_args
def test_kernel_args_apply_verify_backup_before_replace_root_and_readonly():
    m = Machine({GRUB_FILE: GRUB})
    plan, results = one(m, "kernel_args")
    assert results == [("kernel_args", "applied")]
    assert m.writes == ["/bk/etc/default/grub-steamos"] and m.files["/bk/etc/default/grub-steamos"] == GRUB
    assert m.root_writes == [GRUB_FILE] and all(a in m.files[GRUB_FILE] for a in AMDGPU_ARGS)
    assert ["update-grub"] in m.root and m.readonly is True      # switched off, then on again
    assert [c for c in m.root if c[0] == "steamos-readonly"] == [
        ["steamos-readonly", "disable"], ["steamos-readonly", "enable"]]
    entry = plan.record["kernel_args"]
    assert entry["changed"] == [GRUB_FILE] and entry["backup"] == {GRUB_FILE: "/bk/etc/default/grub-steamos"}
    assert "update-grub" in entry["undo"]


def test_failed_update_grub_rolls_back_from_the_backup():
    m = Machine({GRUB_FILE: GRUB})
    calls = {"n": 0}
    real = m.run

    def run(argv, root=False):
        if list(argv) == ["update-grub"]:
            calls["n"] += 1
            if calls["n"] == 1:
                return 1, "failed"
        return real(argv, root)
    io = m.io()
    io = RepairIO(io.read, io.write, io.run, io.backup_dir, lambda a: run(a, True), io.write_root, "steamos-readonly")
    plan = plan_for([CheckResult("kernel_args", False, "")], io)
    assert plan.run(ALWAYS) == [("kernel_args", "rolled back")]
    assert m.files[GRUB_FILE] == GRUB and m.readonly is True
    assert "update-grub failed" in plan.record["kernel_args"]["error"]


def test_rollback_that_cannot_finish_is_recorded_not_hidden():
    m = Machine({GRUB_FILE: GRUB}, fail=["update-grub"])
    plan, results = one(m, "kernel_args")
    assert results == [("kernel_args", "rollback failed")]
    assert m.files[GRUB_FILE] == GRUB          # the file was still restored
    assert "update-grub failed" in plan.record["kernel_args"]["rollback_error"]


def test_unreadable_grub_file_writes_nothing_and_is_not_a_failed_rollback():
    m = Machine()
    plan, results = one(m, "kernel_args")
    assert results == [("kernel_args", "failed (nothing changed)")]
    assert m.writes == [] and m.root_writes == [] and m.root == []
    assert "cannot read" in plan.record["kernel_args"]["error"]
    assert "rollback_error" not in plan.record["kernel_args"]


# ---- user and system units
def test_user_unit_apply_verify_and_prior_state_recorded():
    m = Machine()
    plan, results = one(m, "unit:user:z13-helper.service")
    assert results == [("unit:user:z13-helper.service", "applied")]
    assert "u:z13-helper.service" in m.enabled and m.root == []     # no root for a user unit
    assert plan.record["unit:user:z13-helper.service"]["was_enabled"] is False


def test_user_unit_failure_changed_nothing_so_nothing_to_roll_back():
    m = Machine(fail=["systemctl"])
    plan, results = one(m, "unit:user:z13ctl.socket")
    assert results == [("unit:user:z13ctl.socket", "failed (nothing changed)")] and not m.enabled


def test_system_unit_is_a_guided_root_step_with_apply_verify_and_rollback():
    m = Machine()
    plan = plan_for([CheckResult("unit:system:z13-fan-resume.service", False, "")], m.io())
    step = plan.steps[0]
    assert step.guided and step.needs_root
    assert step.commands == ("sudo -n systemctl enable --now z13-fan-resume.service",)
    assert plan.run(ALWAYS) == [(step.check_id, "applied")]
    assert "s:z13-fan-resume.service" in m.enabled
    assert ["systemctl", "enable", "--now", "z13-fan-resume.service"] in m.root
    step.rollback()                                                  # it was off before: off again
    assert "s:z13-fan-resume.service" not in m.enabled
    assert plan.record[step.check_id]["was_enabled"] is False


def test_system_unit_that_stays_off_is_rolled_back():
    m = Machine()
    real = m.run

    def run(argv, root=False):
        if list(argv)[:2] == ["systemctl", "is-enabled"]:
            return 1, "disabled"           # enable "worked" but verify still sees it off
        return real(argv, root)
    io = m.io()
    io = RepairIO(io.read, io.write, run, "/bk", lambda a: run(a, True), io.write_root)
    plan = plan_for([CheckResult("unit:system:z13-lightbar-reset.service", False, "")], io)
    assert plan.run(ALWAYS)[0][1] == "rolled back"
    assert ["systemctl", "disable", "--now", "z13-lightbar-reset.service"] in m.root


# ---- firmware and Mesa/Vulkan
def test_firmware_package_apply_verify_and_readonly_window():
    m = Machine(installed=())
    plan, results = one(m, "firmware")
    assert results == [("firmware", "applied")] and "linux-firmware" in m.installed
    assert ["pacman", "-S", "--needed", "--noconfirm", "linux-firmware"] in m.root
    assert m.readonly is True and m.root[0] == ["steamos-readonly", "disable"]
    assert plan.steps[0].guided and "pacman -S --needed" in plan.steps[0].commands[1]


def test_firmware_install_failure_rolls_back_and_restores_readonly():
    m = Machine(installed=(), fail=["pacman -S"])
    plan, results = one(m, "firmware")
    assert results == [("firmware", "rolled back")] and m.readonly is True
    assert "pacman -S failed" in plan.record["firmware"]["error"]


def test_mesa_vulkan_rollback_removes_only_what_the_step_installed():
    m = Machine(installed=("mesa",))                  # vulkan-radeon is the missing one
    plan = plan_for([CheckResult("mesa_vulkan", False, "")], m.io())
    plan.steps[0].apply()
    assert plan.steps[0].verify() and "vulkan-radeon" in m.installed
    plan.steps[0].rollback()
    assert m.installed == {"mesa"} and ["pacman", "-R", "--noconfirm", "vulkan-radeon"] in m.root
    assert m.readonly is True


def test_installed_package_is_not_reinstalled_over_a_partial_upgrade():
    m = Machine(installed=("mesa", "vulkan-radeon"))
    plan, results = one(m, "mesa_vulkan")
    assert results == [("mesa_vulkan", "failed (nothing changed)")]
    assert not any(c[:2] == ["pacman", "-S"] for c in m.calls)


# ---- permissions
def test_permission_apply_verify_and_rollback_restore_the_recorded_mode():
    m = Machine(stats={RULE: "666 deck:deck"})
    plan, results = one(m, "permission:" + RULE)
    assert results == [("permission:" + RULE, "applied")] and m.stats[RULE] == "644 root:root"
    entry = plan.record["permission:" + RULE]
    assert entry["before"] == "666 deck:deck" and "chmod 666" in entry["undo"]
    plan.steps[0].rollback()
    assert m.stats[RULE] == "666 deck:deck"


def test_permission_failure_after_chmod_is_rolled_back():
    m = Machine(stats={RULE: "666 deck:deck"}, fail=["chown"])
    plan, results = one(m, "permission:" + RULE)
    assert results == [("permission:" + RULE, "rollback failed")]    # the owner could not be put back
    assert m.stats[RULE].startswith("666") and "chown" in plan.record["permission:" + RULE]["rollback_error"]


def test_permission_on_a_missing_file_changes_nothing():
    m = Machine()
    plan, results = one(m, "permission:" + RULE)
    assert results[0][1] == "failed (nothing changed)" and not m.root


# ---- version reads: guided, read-only diagnostics
def test_version_read_steps_only_read_and_never_write():
    m = Machine(fail=["z13ctl"])
    for check_id in ("steamos_build", "kernel", "bios_ec", "backend_version"):
        plan = plan_for([CheckResult(check_id, False, "")], m.io())
        step = plan.steps[0]
        assert step.guided and not step.needs_root and not step.changes and step.commands
        assert plan.run(ALWAYS) == [(check_id, "not fixed")]    # nothing to fix, nothing changed
        step.rollback()                                         # a no-op, but callable
    assert m.writes == [] and m.root_writes == [] and m.root == []


def test_version_read_that_now_works_is_applied():
    m = Machine({"/proc/sys/kernel/osrelease": "7.2\n"})
    plan, results = one(m, "kernel")
    assert results == [("kernel", "applied")]


def test_suspend_rows_are_not_repaired():
    plan = plan_for([CheckResult("suspend_cycles", False, "")], Machine().io())
    assert plan.steps == [] and plan.record["unplanned"] == ["suspend_cycles"]
