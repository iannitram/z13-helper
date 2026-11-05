"""Qt-free logic behind `python3 -m post_update repair|checks|bundle|suspend` (#27).

The driver, the tray's Updates tab and Decky all go through here: collect the inventory,
run the checks, build the RepairPlan, show it, and run it only for steps the user confirmed.
`read` and `io` are the seams (real ones in driver.py); nothing here shells out itself.
"""

import json
import re
from collections.abc import Callable, Sequence

import post_update_checks
import repair_plan
import support_bundle
import suspend_diagnostic
import system_inventory

STEP_ID = re.compile(r"[A-Za-z0-9@:._/-]+\Z")


def failing(read) -> list:
    """The failing CheckResult rows for this machine (read-only)."""
    rows = post_update_checks.run_checks(system_inventory.collect_inventory(read))
    return [r for r in rows if not r.passed]


def build_plan(read, io) -> "repair_plan.RepairPlan":
    return repair_plan.plan_for(failing(read), io)


def plan_dict(plan) -> dict:
    """What the UIs draw: one entry per step with its exact commands, plus the report lines."""
    return {"steps": [{"id": s.check_id, "description": s.description,
                       "needs_root": s.needs_root, "guided": s.guided, "changes": s.changes,
                       "commands": list(s.commands), "undo": s.rollback_note} for s in plan.steps],
            "unplanned": list(plan.record.get("unplanned", [])),
            "lines": plan.describe()}


def run_dict(plan, ids: "Sequence[str] | None", save: Callable[[dict], None]) -> dict:
    """Run the steps in `ids` (None = all); the rest are left out. Saves the record before and after."""
    chosen = set(ids) if ids is not None else None
    steps = [s for s in plan.steps if chosen is None or s.check_id in chosen]
    sub = repair_plan.RepairPlan(steps=steps, record=plan.record)
    save(plan.record)
    results = sub.run(confirm=lambda step: True)   # the caller got the user's yes for these ids
    save(plan.record)
    return {"results": [list(r) for r in results], "record": plan.record}


def _failed(results) -> bool:
    return any(status not in ("applied",) for _, status in results)


def repair_main(argv: Sequence[str], read, io, save: Callable[[dict], None],
                input_fn=input, out=print) -> int:
    """repair [--json] [--plan-only] [--run ID,ID | --yes]. 0 = nothing failed, 1 = a step did not apply."""
    argv = list(argv)
    as_json = "--json" in argv
    ids = None
    if "--run" in argv:
        i = argv.index("--run")
        value = argv[i + 1] if i + 1 < len(argv) else ""
        ids = [v for v in value.split(",") if v]
        if not ids or not all(STEP_ID.fullmatch(v) for v in ids):
            out("post-update: --run needs a comma separated list of step ids")
            return 2
    plan = build_plan(read, io)
    if as_json and ids is None:
        out(json.dumps(plan_dict(plan)))
        return 0
    if ids is None:
        for line in plan.describe():
            out(line)
        if not plan.steps:
            out("Nothing to repair.")
            return 0
        if "--plan-only" in argv:
            return 0
        if "--yes" not in argv:
            answer = input_fn(f"Run these {len(plan.steps)} step(s)? Root steps use sudo -n. [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                out("Nothing was run.")
                return 0
    unknown = [i for i in ids or () if i not in {s.check_id for s in plan.steps}]
    if unknown:
        out(f"post-update: no repair step for: {', '.join(unknown)}")
        return 2
    result = run_dict(plan, ids, save)
    if as_json:
        out(json.dumps(result))
    else:
        for check_id, status in result["results"]:
            out(f"{check_id}: {status}")
    return 1 if _failed(result["results"]) else 0


def checks_main(read, out=print) -> int:
    rows = post_update_checks.run_checks(system_inventory.collect_inventory(read))
    for r in rows:
        out(f"{'ok  ' if r.passed else 'FAIL'} {r.check_id}: {r.detail}")
    return 0 if all(r.passed for r in rows) else 1


def suspend_main(read, out=print) -> int:
    """The suspend/resume diagnostic: reads only, offers no repair."""
    rows = suspend_diagnostic.diagnose(read)
    for r in rows:
        out(f"{'ok  ' if r.passed else 'FAIL'} {r.check_id}: {r.detail}")
    return 0


def bundle_main(argv: Sequence[str], read, write_file: Callable[[str, str], None],
                input_fn=input, out=print) -> int:
    """Print the redacted bundle. `--out FILE` writes exactly the text shown, after a yes
    (`--yes` skips the question; the text is still printed first)."""
    argv = list(argv)
    bundle = support_bundle.build_bundle(system_inventory.collect_inventory(read), [])
    shown = support_bundle.preview(bundle)
    out(shown)
    if "--out" not in argv:
        return 0
    i = argv.index("--out")
    if i + 1 >= len(argv):
        out("post-update: --out needs a file name")
        return 2
    target = argv[i + 1]
    if "--yes" not in argv and input_fn(f"Write exactly this to {target}? [y/N] ").strip().lower() not in ("y", "yes"):
        out("Nothing was written.")
        return 0
    support_bundle.share(bundle, shown, lambda text: write_file(target, text))
    out(f"Wrote {target}")
    return 0
