"""Repair plan skeleton shape and empty-plan stub (issue #27)."""

from dataclasses import fields

from repair_plan import RepairPlan, RepairStep, plan_for


def test_plan_for_returns_an_empty_plan_with_repair_step_fields():
    plan = plan_for([{"check_id": "keyring", "detail": "missing"}])
    assert isinstance(plan, RepairPlan)
    assert plan.steps == []
    assert plan.record == {}
    assert [field.name for field in fields(RepairStep)] == [
        "check_id",
        "description",
        "apply",
        "verify",
        "rollback",
    ]
