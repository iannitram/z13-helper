"""Pure mapping from app profiles to z13ctl's named profiles (#23 part B).

Older z13ctl daemons may silently ignore a profile-target field and apply the
setting to the running machine, so callers must probe ``profile --list`` first
and refuse to write when the target is unsupported. Nothing here performs I/O.
"""

import re
from typing import Optional

# Only names we know z13ctl exposes. Unknown names keep the existing apply path
# instead of guessing a target.
NAMED_PROFILES = {
    "Silent": "Silent",
    "Balanced": "Balanced",
    "Turbo": "Turbo",
}

_FIRMWARE_NAMES = re.compile(r"^\s*Firmware profiles:\s*\S", re.IGNORECASE)
_CUSTOM_HEADING = re.compile(r"^\s*Custom profiles:\s*$", re.IGNORECASE)
# A custom row is padded: optional active marker, name, two or more spaces, then
# a description (for example "   custom              fan curve, 20W").
_PROFILE_ROW = re.compile(r"^(?:[ \t]+(?:\*[ \t]*)?|\*[ \t]*)[^\s*]\S*.*?[ \t]{2,}\S")


def named_profile_for(app_profile_name: str) -> Optional[str]:
    """Return the z13ctl named profile for an app profile, or None."""
    return NAMED_PROFILES.get(app_profile_name)


def profile_target_supported(list_output: str) -> bool:
    """Whether ``z13ctl profile --list`` shows at least one profile.

    Empty, heading-only and unparseable output all mean "not proven", so the
    caller must not target a named profile.
    """
    if not isinstance(list_output, str) or not list_output.strip():
        return False
    for line in list_output.splitlines():
        if _FIRMWARE_NAMES.match(line):
            return True
        if _CUSTOM_HEADING.match(line):
            continue
        if _PROFILE_ROW.match(line):
            return True
    return False


def apply_plan(app_profile_name: str, target_supported: bool) -> str:
    """Choose the apply path: "named", "refuse" or "legacy"."""
    if named_profile_for(app_profile_name) is None:
        return "legacy"
    if not target_supported:
        return "refuse"
    return "named"
