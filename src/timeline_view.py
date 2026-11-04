"""Timeline view logic (Phase 16g, #135): rows, compare and roll back. No Qt, stdlib + timeline.

The desktop dialog (timeline_dialog.py) renders `rows()` / `compare()` and calls `roll_back()`;
the Decky QAM list reads the same entries through its `get_timeline` callable.
"""

import timeline

# Entry kinds whose rollback record `roll_back` can run here. Others show why they can't.
ROLLBACK_KINDS = ("profile",)


def _rolled_back_ids(entries):
    # A failed rollback does not count: the user may try again.
    return {e.get("rollback_of") for e in entries if e.get("rollback_of") and e.get("ok", True)}


def why_not(entry, rolled_back=()):
    """None when `entry` can be rolled back, else the sentence that says why not."""
    rb = entry.get("rollback")
    if not isinstance(rb, dict) or not rb:
        return entry.get("no_rollback_reason") or "Not reversible"
    if entry.get("id") in rolled_back:
        return "Already rolled back"
    if rb.get("kind") not in ROLLBACK_KINDS:
        return f"Rolling back a {rb.get('kind', 'this')} record is not built into the app yet"
    if not rb.get("profile"):
        return "The rollback record has no profile to return to"
    return None


def rows(entries):
    """List rows, newest first: what the user sees, plus whether and why not to roll back."""
    done = _rolled_back_ids(entries)
    out = []
    for e in sorted(entries, key=lambda x: str(x.get("ts", "")), reverse=True):
        reason = why_not(e, done)
        out.append({"id": e.get("id"), "ts": e.get("ts"), "kind": e.get("kind"),
                    "summary": e.get("summary"), "ok": e.get("ok", True),
                    "changed": list(e.get("changed") or []),
                    "can_roll_back": reason is None, "why_not": reason})
    return out


def _flatten(value, prefix=""):
    if isinstance(value, dict):
        flat = {}
        for k, v in value.items():
            flat.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return flat
    return {prefix: value}


def compare(a, b):
    """Differences between the states two entries left behind (their `after` snapshots).
    Returns [(key, value_in_a, value_in_b)] sorted by key; empty when the states match."""
    fa, fb = _flatten(a.get("after") or {}), _flatten(b.get("after") or {})
    return [(k, fa.get(k), fb.get(k)) for k in sorted(set(fa) | set(fb)) if fa.get(k) != fb.get(k)]


def describe(entry):
    """Detail text for one entry: summary, what changed (a SteamOS update lists its changes)."""
    lines = [f"{entry.get('ts', '?')}  {entry.get('summary', '')}"]
    lines += [f"  • {c}" for c in entry.get("changed") or []]
    return "\n".join(lines)


def format_compare(a, b):
    diff = compare(a, b)
    head = f"{a.get('summary')}  vs  {b.get('summary')}"
    if not diff:
        return head + "\nThe two states are the same."
    return head + "\n" + "\n".join(f"{k}: {va!r} → {vb!r}" for k, va, vb in diff)


def roll_back(entry, entries, *, apply_profile=None, path=None):
    """Run `entry`'s rollback record and append its own timeline entry (returned).

    `entries` is the timeline as read (to refuse a second rollback of the same entry).
    `apply_profile(name)` returns profiles.apply's [(step, ok, message)] and defaults to
    profiles.apply_by_name without its own timeline entry. Raises ValueError when the entry
    cannot be rolled back (the message is `why_not`)."""
    reason = why_not(entry, _rolled_back_ids(entries))
    if reason:
        raise ValueError(reason)
    if apply_profile is None:
        import profiles
        apply_profile = lambda name: profiles.apply_by_name(name, record=False)  # noqa: E731
    name = entry["rollback"]["profile"]
    results = apply_profile(name)
    ok = bool(results) and all(step_ok for _s, step_ok, _m in results)
    return timeline.append({
        "kind": entry.get("kind", "profile"),
        "summary": f"Rolled back: {entry.get('summary')}",
        "ok": ok,
        "before": entry.get("after") or {},
        "after": entry.get("before") or {},
        "changed": [str(s) for s, step_ok, _m in results if step_ok],
        "steps": [{"step": str(s), "ok": bool(step_ok), "message": str(m)} for s, step_ok, m in results],
        "rollback_of": entry.get("id"),
        "rollback": None,
        "no_rollback_reason": "This entry is itself a rollback; apply the profile you want instead",
    }, path=path, journal=False)
