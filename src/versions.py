"""Shared version constraints and comparisons for host and UI checks."""

DECKY_MIN = "3.3.0"


def older_than(have, minimum):
    """Return whether ``have`` is missing, malformed, or below ``minimum``.

    Decky release versions are dotted integers. A leading ``v`` is accepted;
    malformed values are treated as needing repair so an unknown version cannot
    silently pass a safety floor.
    """
    if not have:
        return True
    try:
        current = have.strip()
        floor = minimum.strip()
        if current[:1].lower() == "v":
            current = current[1:]
        if floor[:1].lower() == "v":
            floor = floor[1:]
        have_parts = tuple(int(part) for part in current.split("."))
        minimum_parts = tuple(int(part) for part in floor.split("."))
        if not have_parts or not minimum_parts:
            return True
    except (AttributeError, TypeError, ValueError):
        return True
    return have_parts < minimum_parts
