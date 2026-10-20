"""Per-rail power-limit ranges (PL1/PL2/PL3) as the kernel interface accepts them.

stdlib only; shared with the Decky plugin (installed root-owned into py_modules/).
The ranges are documented constants (z13ctl `tdp --help`): 5-93 W on the older
asus-nb-wmi interface, PL1 28-80 / PL2 32-92 / PL3 45-93 W on asus-armoury.
Which interface is in use is decided by `stat` alone: nothing here reads a ppt
value or runs z13ctl (ROADMAP principle 5: reading the PPT values can hard-lock
the EC). The profile store keeps validating against the 5-93 W outer envelope;
the range only decides what the sliders offer and what the UI flags.
"""

import os

RAILS = ("pl1", "pl2", "pl3")
DEFAULT_RANGES = {k: (5, 93) for k in RAILS}
ARMOURY_RANGES = {"pl1": (28, 80), "pl2": (32, 92), "pl3": (45, 93)}
ARMOURY_DIR = "/sys/class/firmware-attributes/asus-armoury/attributes"
NBWMI_DIR = "/sys/devices/platform/asus-nb-wmi"


def detect_ranges(armoury_dir=ARMOURY_DIR, nbwmi_dir=NBWMI_DIR):
    """Per-rail (min, max) for the interface in use, by existence checks only.

    The armoury tree can exist while its ppt_* files answer ENODEV and z13ctl uses
    asus-nb-wmi (measured on 6.18), so the asus-nb-wmi ppt files win when present:
    armoury ranges apply only when armoury has the ppt attribute and asus-nb-wmi
    does not.
    """
    if os.path.exists(os.path.join(nbwmi_dir, "ppt_pl1_spl")):
        return dict(DEFAULT_RANGES)
    if os.path.isdir(os.path.join(armoury_dir, "ppt_pl1_spl")):
        return dict(ARMOURY_RANGES)
    return dict(DEFAULT_RANGES)


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
