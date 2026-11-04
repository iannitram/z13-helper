"""Repair plan: steps with apply/verify/rollback and a rollback record (issue #27)."""

from dataclasses import fields

from post_update.builtin import AMDGPU_ARGS, GRUB_FILE
from post_update_checks import CheckResult
from repair_plan import RepairIO, RepairPlan, RepairStep, plan_for

GRUB = 'GRUB_TIMEOUT=0\nGRUB_CMDLINE_LINUX_DEFAULT="quiet splash"\n'


class Machine:
    def __init__(self, files=None, rc=None, once=()):
        self.once = set(once)
        self.files = dict(files or {})
        self.calls = []
        self.writes = []
        self.rc = rc or {}
        self.enabled = set()

    def read(self, path):
        return self.files.get(path)

    def write(self, path, text):
        self.writes.append(path)
        self.files[path] = text

    def run(self, argv):
        self.calls.append(list(argv))
        if argv[:3] == ["systemctl", "--user", "is-enabled"]:
            return (0, "enabled") if argv[3] in self.enabled else (1, "disabled")
        if argv[:3] == ["systemctl", "--user", "enable"]:
            if self.rc.get("enable"):
                return 1, "denied"
            self.enabled.add(argv[-1])
        if argv[:3] == ["systemctl", "--user", "disable"]:
            self.enabled.discard(argv[-1])
        if argv[0] in self.once:
            self.once.discard(argv[0])
            return 1, "failed"
        return self.rc.get(argv[0], 0), ""

    def io(self):
        return RepairIO(self.read, self.write, self.run, "/bk")


def test_step_fields_and_no_failures_means_no_steps():
    assert [f.name for f in fields(RepairStep)] == [
        "check_id", "description", "apply", "verify", "rollback"]
    plan = plan_for([CheckResult("kernel_args", True, "fine")], Machine().io())
    assert isinstance(plan, RepairPlan) and plan.steps == []


def test_building_a_plan_touches_nothing_and_unknown_ids_are_listed():
    m = Machine({GRUB_FILE: GRUB})
    plan = plan_for([{"check_id": "kernel_args", "detail": "x"},
                     {"check_id": "firmware", "detail": "x"}], m.io())
    assert [s.check_id for s in plan.steps] == ["kernel_args"]
    assert plan.record["unplanned"] == ["firmware"]
    assert plan.record["kernel_args"]["status"] == "planned"
    assert m.writes == [] and m.calls == []


def test_kernel_args_backup_before_replace_and_record():
    m = Machine({GRUB_FILE: GRUB})
    plan = plan_for([CheckResult("kernel_args", False, "missing")], m.io())
    assert plan.run() == [("kernel_args", "applied")]
    assert m.writes[0] == "/bk/etc/default/grub-steamos"      # backup first
    assert m.files["/bk/etc/default/grub-steamos"] == GRUB
    assert all(a in m.files[GRUB_FILE] for a in AMDGPU_ARGS)
    entry = plan.record["kernel_args"]
    assert entry["changed"] == [GRUB_FILE]
    assert entry["backup"] == {GRUB_FILE: "/bk/etc/default/grub-steamos"}
    assert "update-grub" in entry["undo"]


def test_failed_update_grub_rolls_back_from_the_backup():
    m = Machine({GRUB_FILE: GRUB}, once=["update-grub"])
    plan = plan_for([CheckResult("kernel_args", False, "")], m.io())
    assert plan.run() == [("kernel_args", "rolled back")]
    assert m.files[GRUB_FILE] == GRUB
    assert "update-grub failed" in plan.record["kernel_args"]["error"]


def test_rollback_that_cannot_finish_is_recorded_not_hidden():
    m = Machine({GRUB_FILE: GRUB}, rc={"update-grub": 1})
    plan = plan_for([CheckResult("kernel_args", False, "")], m.io())
    assert plan.run() == [("kernel_args", "rollback failed")]
    assert m.files[GRUB_FILE] == GRUB          # the file was still restored
    assert "update-grub failed" in plan.record["kernel_args"]["rollback_error"]


def test_unreadable_grub_file_writes_nothing():
    m = Machine()
    plan = plan_for([CheckResult("kernel_args", False, "")], m.io())
    assert plan.run()[0][0] == "kernel_args"
    assert m.writes == []
    assert "cannot read" in plan.record["kernel_args"]["error"]


def test_unit_repair_enables_and_records_prior_state():
    m = Machine()
    plan = plan_for([CheckResult("unit:user:z13-helper.service", False, "")], m.io())
    assert plan.run() == [("unit:user:z13-helper.service", "applied")]
    assert "z13-helper.service" in m.enabled
    assert plan.record["unit:user:z13-helper.service"]["was_enabled"] is False


def test_unit_repair_failure_is_undone():
    m = Machine(rc={"enable": 1})
    plan = plan_for([CheckResult("unit:user:z13ctl.socket", False, "")], m.io())
    assert plan.run() == [("unit:user:z13ctl.socket", "rolled back")]
    assert not m.enabled


def test_system_units_and_suspend_rows_get_no_step():
    plan = plan_for([CheckResult("unit:system:z13-fan-resume.service", False, ""),
                     CheckResult("suspend_cycles", False, "")], Machine().io())
    assert plan.steps == []
    assert plan.record["unplanned"] == ["unit:system:z13-fan-resume.service", "suspend_cycles"]
