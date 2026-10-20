"""Per-rail power-limit ranges (PL1/PL2/PL3) as the kernel interface accepts them.

stdlib only; shared with the Decky plugin (installed root-owned into py_modules/).
`z13ctl tdp --get` prints the range the kernel accepts for each rail: 5-93 W on
the older asus-nb-wmi interface, PL1 28-80 / PL2 32-92 / PL3 45-93 W on
asus-armoury. If the output cannot be parsed, DEFAULT_RANGES (5-93 W) apply.
The profile store keeps validating against that outer envelope; the current
range only decides what the sliders offer and what the UI flags.
"""

import re

RAILS = ("pl1", "pl2", "pl3")
DEFAULT_RANGES = {k: (5, 93) for k in RAILS}

_RE_RAIL = re.compile(r"PL([123])\s+\([^)]+\):\s*(.*)")
_RE_RANGE = re.compile(r"(\d+)\s*(?:W\s*)?(?:-|–|—|\.\.+|to)\s*(\d+)")


def parse_tdp_ranges(text):
    """{pl1,pl2,pl3: (min, max)} from `tdp --get` output; rails it can't read get 5-93."""
    out = dict(DEFAULT_RANGES)
    for line in (text or "").splitlines():
        m = _RE_RAIL.search(line)
        if not m:
            continue
        found = _RE_RANGE.findall(m.group(2))
        if not found:
            continue
        lo, hi = (int(x) for x in found[-1])
        if 0 < lo <= hi:
            out[f"pl{m.group(1)}"] = (lo, hi)
    return out


def tdp_status(tdp, ranges):
    """Requested vs accepted values for a stored profile `tdp` dict.

    Never changes the stored values: `accepted` is what the sliders show (each rail
    clamped into its range), `adjusted` the rails that differ, `note` a one-line
    explanation for the UI ("" when everything is in range).
    """
    requested = {k: int(tdp[k]) for k in RAILS}
    accepted, adjusted, parts = {}, [], []
    for k in RAILS:
        lo, hi = ranges[k]
        accepted[k] = max(lo, min(hi, requested[k]))
        if accepted[k] != requested[k]:
            adjusted.append(k)
            parts.append(f"{k.upper()} {requested[k]} W is outside {lo}-{hi} W "
                         f"(showing {accepted[k]} W)")
    note = ""
    if parts:
        note = ("Stored limits don't fit this kernel: " + "; ".join(parts)
                + ". Move a slider to save the shown values.")
    return {"requested": requested, "accepted": accepted, "adjusted": adjusted,
            "needs_attention": bool(adjusted), "note": note}
