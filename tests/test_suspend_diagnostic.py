"""Suspend/resume diagnostic: read-only, journal and sysfs through the seam (issue #27)."""

from suspend_diagnostic import JOURNAL, MEM_SLEEP, diagnose
from repair_plan import plan_for

HEALTHY = "PM: suspend entry (s2idle)\nPM: suspend exit\n"


def run(mem="[s2idle] deep\n", log=HEALTHY):
    seen = []

    def read(target):
        seen.append(target)
        return {MEM_SLEEP: mem, JOURNAL: log}.get(target)
    return {r.check_id: r for r in diagnose(read)}, seen


def test_healthy_cycle_passes():
    rows, _ = run()
    assert all(r.passed for r in rows.values())
    assert rows["suspend_mode"].detail == "sleep mode: s2idle"


def test_missing_resume_and_error_lines_fail():
    rows, _ = run(log=HEALTHY + "PM: suspend entry (s2idle)\namdgpu 0000:c3:00.0: ring gfx timeout\n")
    assert not rows["suspend_cycles"].passed and "resume is missing" in rows["suspend_cycles"].detail
    assert not rows["suspend_errors"].passed


def test_unreadable_sources_fail_not_pass():
    def nothing(target):
        return None
    rows = {r.check_id: r for r in diagnose(nothing)}
    assert not any(r.passed for r in rows.values())


def test_only_reads_and_never_a_repair_step():
    rows, seen = run(log="PM: Some devices failed to suspend\n")
    assert seen == [MEM_SLEEP, JOURNAL] and JOURNAL[0] == "journalctl"
    assert plan_for(list(rows.values()), None).steps == []
