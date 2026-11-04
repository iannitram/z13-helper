"""Pure mapping from app profiles to z13ctl's named profiles (#23 part B).

Older z13ctl daemons may silently ignore a profile-target field and apply the
setting to the running machine, so callers must probe the socket ``profile-list`` first
and refuse to write when the target is unsupported. Nothing here performs I/O.
"""

from typing import Optional

# Only names we know z13ctl exposes. Unknown names keep the existing apply path
# instead of guessing a target.
NAMED_PROFILES = {
    "Silent": "Silent",
    "Balanced": "Balanced",
    "Turbo": "Turbo",
}

def named_profile_for(app_profile_name: str) -> Optional[str]:
    """Return the z13ctl named profile for an app profile, or None."""
    return NAMED_PROFILES.get(app_profile_name)


def profile_target_supported(list_output: str) -> bool:
    """Recognize the JSON value of the socket ``profile-list`` command.

    The upstream named-profile API introduced profile-list and request.profile
    together (api/client.go SendProfileList / SendTdpSetFor). The older daemon
    rejects profile-list. A CLI firmware listing proves neither capability.
    This is protocol detection, not an experimental setting write: probing a
    write on an old daemon could change live hardware when it ignores profile.

    Limit of this check: it proves only that the daemon answers ``profile-list``
    with well-formed rows. It does NOT prove ``request.profile`` is honoured;
    that is inferred from the two shipping together upstream and is unverified
    without hardware (see the MAP/README.md row for this module and issue #23).
    """
    import json
    try:
        rows = json.loads(list_output)
    except (TypeError, ValueError):
        return False
    return isinstance(rows, list) and all(
        isinstance(row, dict) and isinstance(row.get("name"), str) and row["name"]
        for row in rows)


def apply_plan(app_profile_name: str, target_supported: bool) -> str:
    """Choose the apply path: "named", "refuse" or "legacy"."""
    if named_profile_for(app_profile_name) is None:
        return "legacy"
    if not target_supported:
        return "refuse"
    return "named"
