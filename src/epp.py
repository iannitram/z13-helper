import glob
from typing import Optional, Sequence

EPP_CHOICES = ("power", "balance_power", "balance_performance", "performance")
EPP_LABELS = {"power": "Power", "balance_power": "Balance power",
              "balance_performance": "Balance performance", "performance": "Performance"}
# Both the read and the write use the cpufreq policy directories (one per policy, which
# on amd-pstate is usually one per CPU but is not guaranteed to be), never cpu{n}.
EPP_POLICY_GLOB = "/sys/devices/system/cpu/cpufreq/policy*/energy_performance_preference"


def parse_epp(text: str) -> Optional[str]:
    """Parse a single EPP value from text, returning None for invalid values."""
    stripped = text.strip()
    if stripped in EPP_CHOICES:
        return stripped
    return None


def parse_available(text: str) -> list:
    """Parse available EPP preferences, returning only known ones in choice order."""
    if not text:
        return []

    available = text.strip().split()
    return [choice for choice in EPP_CHOICES if choice in available]


def summarize_epp(values: Sequence[Optional[str]]) -> Optional[str]:
    """Summarize EPP values from all CPUs."""
    if not values:
        return None

    # Parse all values
    parsed_values = [parse_epp(val) if val is not None else None for val in values]

    # Check if any are None
    if None in parsed_values:
        return None

    # Check if all values are the same
    first = parsed_values[0]
    if all(val == first for val in parsed_values):
        return first

    # Values differ
    return "mixed"


def policy_paths(pattern: str = EPP_POLICY_GLOB) -> list:
    """The EPP node of every cpufreq policy present, in policy-number order."""
    def key(path):
        digits = "".join(c for c in path.rsplit("policy", 1)[-1].split("/")[0] if c.isdigit())
        return int(digits) if digits else -1
    return sorted(glob.glob(pattern), key=key)


# ----------------------------------------------------------------------
# CPU Boost combo, shared by the dashboard and the Fans window. Duck-typed on the
# QComboBox methods used, so it needs no Qt import and tests can drive it.
# ----------------------------------------------------------------------

def fill_combo(combo, choices) -> None:
    """"Unchanged" (no override) first, then one labelled item per choice."""
    combo.addItem("Unchanged", "")
    for key in choices:
        combo.addItem(EPP_LABELS.get(key, key), key)


def show_profile_epp(combo, profile) -> None:
    """Select the profile's stored EPP without emitting a change."""
    combo.blockSignals(True)
    try:
        value = profile.get("epp") or ""
        index = combo.findData(value)
        if index < 0:  # stored value the kernel does not offer: show it, do not hide it
            combo.addItem(f"{EPP_LABELS.get(value, value)} (unavailable)", value)
            index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))
    finally:
        combo.blockSignals(False)


def now_text(live) -> str:
    """The "Now: ..." readout for the value the CPUs report (None = unreadable)."""
    return f"Now: {EPP_LABELS.get(live, live) or 'unavailable'}"


def profile_with_epp(profile, combo) -> dict:
    """A copy of `profile` carrying the combo's choice ("Unchanged" -> None)."""
    out = dict(profile)
    out["epp"] = combo.currentData() or None
    return out
