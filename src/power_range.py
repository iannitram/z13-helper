"""Per-rail power-limit ranges (PL1/PL2/PL3) from the active kernel interface.

Shared with the Decky plugin (installed root-owned into py_modules/). Backend
detection and Armoury bounds mirror z13ctl 1.4.2. In Flatpak, a metadata-only
probe runs on the host because the sandbox does not have the host's write-access
view. It reads each rail's min/max metadata, never a live ``current_value``
(ROADMAP principle 5: reading Armoury PPT values can hard-lock the EC). The
profile store keeps validating against the 5-93 W outer envelope; these ranges
only decide what the sliders offer and what the UI flags.
"""

import json
import os

import host

RAILS = ("pl1", "pl2", "pl3")
DEFAULT_RANGES = {k: (5, 93) for k in RAILS}
# The documented GZ302EA AC ranges, retained as a named reference for tests and
# callers; detected bounds use the kernel's per-rail metadata below.
ARMOURY_RANGES = {"pl1": (28, 80), "pl2": (32, 92), "pl3": (45, 93)}
ARMOURY_DIR = "/sys/class/firmware-attributes/asus-armoury/attributes"
NBWMI_DIR = "/sys/devices/platform/asus-nb-wmi"
ARMOURY_ATTRIBUTES = {
    "pl1": "ppt_pl1_spl",
    "pl2": "ppt_pl2_sppt",
    "pl3": "ppt_pl3_fppt",
}

# `python3 -c` program sent through host.run() when called inside Flatpak. Keep
# it stdlib-only and in sync with _probe_sysfs_local below. The PL1 metadata
# determines whether z13ctl can use Armoury; each present rail contributes its
# own actual min/max limits.
_HOST_PROBE = r'''
import json, os, sys
armoury_dir, nbwmi_dir = sys.argv[1:3]
attrs = {"pl1": "ppt_pl1_spl", "pl2": "ppt_pl2_sppt", "pl3": "ppt_pl3_fppt"}
defaults = {k: [5, 93] for k in attrs}
def bounds(attr_dir):
    try:
        with open(os.path.join(attr_dir, "min_value"), encoding="ascii") as f:
            lo = int(f.read().strip())
        with open(os.path.join(attr_dir, "max_value"), encoding="ascii") as f:
            hi = int(f.read().strip())
    except (OSError, ValueError):
        return None
    return (lo, hi) if lo > 0 and hi >= lo else None
pl1 = os.path.join(armoury_dir, attrs["pl1"])
current = os.path.join(pl1, "current_value")
if os.path.exists(current) and os.access(current, os.W_OK) and bounds(pl1):
    ranges = dict(defaults)
    for rail, attr in attrs.items():
        attr_dir = os.path.join(armoury_dir, attr)
        if os.path.exists(os.path.join(attr_dir, "current_value")):
            rail_bounds = bounds(attr_dir)
            if rail_bounds:
                ranges[rail] = list(rail_bounds)
else:
    ranges = defaults
print(json.dumps(ranges))
'''.strip()


def _read_bounds(attr_dir):
    """Read the kernel's range metadata, never its live current value."""
    try:
        with open(os.path.join(attr_dir, "min_value"), encoding="ascii") as f:
            lo = int(f.read().strip())
        with open(os.path.join(attr_dir, "max_value"), encoding="ascii") as f:
            hi = int(f.read().strip())
    except (OSError, ValueError):
        return None
    return (lo, hi) if lo > 0 and hi >= lo else None


def _probe_sysfs_local(armoury_dir, nbwmi_dir):
    """Probe paths visible to this process (host, Decky, or a test tree)."""
    pl1 = os.path.join(armoury_dir, ARMOURY_ATTRIBUTES["pl1"])
    current = os.path.join(pl1, "current_value")
    if os.path.exists(current) and os.access(current, os.W_OK) and _read_bounds(pl1):
        ranges = dict(DEFAULT_RANGES)
        for rail, attr in ARMOURY_ATTRIBUTES.items():
            attr_dir = os.path.join(armoury_dir, attr)
            if os.path.exists(os.path.join(attr_dir, "current_value")):
                rail_bounds = _read_bounds(attr_dir)
                if rail_bounds:
                    ranges[rail] = rail_bounds
        return ranges
    # asus-nb-wmi is z13ctl's fallback. Its documented range is 5-93 W.
    return dict(DEFAULT_RANGES)


def detect_ranges(armoury_dir=ARMOURY_DIR, nbwmi_dir=NBWMI_DIR):
    """Return per-rail bounds for the backend z13ctl will select.

    z13ctl prefers Armoury when PL1's current-value node is writable and its
    min/max metadata are sane; legacy PPT nodes can coexist. In Flatpak, run the
    same metadata-only check on the host because sandbox ``os.access`` reports
    a false negative for sysfs write permissions. Armoury PL3 is named
    ``ppt_pl3_fppt`` (the legacy interface calls it ``ppt_fppt``).

    Missing or malformed per-rail Armoury metadata falls back to 5-93 W because
    this map has no representation for a rail that the backend omits.
    """
    # Custom roots are used by tests; only production paths should cross the
    # Flatpak boundary.
    if host.in_flatpak() and armoury_dir == ARMOURY_DIR and nbwmi_dir == NBWMI_DIR:
        result = host.run(["python3", "-c", _HOST_PROBE, armoury_dir, nbwmi_dir], timeout=5)
        if result.rc == 0:
            try:
                ranges = json.loads(result.out)
                return {rail: tuple(map(int, ranges[rail])) for rail in RAILS}
            except (ValueError, KeyError, TypeError):
                pass
        return dict(DEFAULT_RANGES)
    return _probe_sysfs_local(armoury_dir, nbwmi_dir)


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
