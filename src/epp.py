from typing import Optional, Sequence

EPP_CHOICES = ("power", "balance_power", "balance_performance", "performance")
EPP_PATH = "/sys/devices/system/cpu/cpu{n}/cpufreq/energy_performance_preference"


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


def epp_paths(cpu_count: int) -> list:
    """Generate EPP sysfs paths for the given number of CPUs."""
    return [
        EPP_PATH.format(n=i)
        for i in range(cpu_count)
    ]
