"""Pure mapping from app profiles to z13ctl's named profiles (#23 part B).

Older z13ctl daemons may silently ignore a profile-target field and apply the
setting to the running machine, so callers must probe the socket ``profile-list`` first
and refuse to write when the target is unsupported. Nothing here performs I/O.
"""

import re
from typing import Optional

# z13ctl 1.4.2 ships no Silent/Balanced/Turbo named profiles and rejects
# capitalized names, so no app profile maps to a fixed name. Each one gets a
# generated backend name (Z13CtlClient.backend_profile_name). Only an explicit
# per-profile z13ctl_profile override (e.g. z13ctl's own "custom") is sent as-is.
NAMED_PROFILES: dict = {}

# Firmware base names: a backend name equal to one of these would be ambiguous
# with `profile set=<base>`.
FIRMWARE_BASES = ("quiet", "balanced", "performance")
_BACKEND_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


def named_profile_for(app_profile_name: str) -> Optional[str]:
    """Return the fixed z13ctl named profile for an app profile, or None."""
    return NAMED_PROFILES.get(app_profile_name)


def is_valid_backend_name(name) -> bool:
    """True for a lowercase name z13ctl accepts that is not a firmware base."""
    return (isinstance(name, str) and _BACKEND_NAME.fullmatch(name) is not None
            and name not in FIRMWARE_BASES)


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
