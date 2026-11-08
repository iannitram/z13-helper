import re
from typing import Optional

try:
    import timeline
except ImportError:
    timeline = None

_STEAMOS_PARENS = re.compile(r"\(([^()?]+)\)\s*$")


def read_build_id(os_release_text: str) -> Optional[str]:
    """Extract the BUILD_ID from /etc/os-release text, removing quotes if present."""
    for line in os_release_text.splitlines():
        if line.startswith("BUILD_ID="):
            # Remove the BUILD_ID= prefix
            value = line[9:]  # Remove "BUILD_ID="
            value = value.strip()
            # Remove a matching pair of surrounding quotes (os-release allows "x" and 'x')
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            # Return None if value is empty
            return value if value else None
    return None


def needs_attention(recorded: Optional[str], current: Optional[str]) -> bool:
    """Return True only when both recorded and current are non-empty and differ."""
    return bool(recorded and current and recorded != current)


def banner(recorded: Optional[str], current: Optional[str], pending: int) -> Optional[str]:
    """Return banner text when attention is needed and there are pending tasks."""
    if needs_attention(recorded, current) and pending > 0:
        if pending == 1:
            return "SteamOS updated: 1 post-update task needs attention"
        else:
            return f"SteamOS updated: {pending} post-update tasks need attention"
    return None


def row_state(check: str, reboot_pending: bool, applicable: bool) -> str:
    """Determine the state of a tab row based on check result and conditions."""
    if not applicable or check == "na":
        return "na"
    if check == "info":
        return "info"
    if reboot_pending:
        return "reboot"
    if check == "ok":
        return "fine"
    if check == "needs":
        return "needs"
    return "na"


def recorded_build_id(install_rec: Optional[dict]) -> Optional[str]:
    """Extract the recorded SteamOS BUILD_ID from an `install.json` dict."""
    if not isinstance(install_rec, dict):
        return None
    direct = install_rec.get("build_id")
    if isinstance(direct, str) and direct.strip() and direct.strip() != "?":
        return direct.strip()
    steamos = install_rec.get("steamos")
    if isinstance(steamos, str):
        m = _STEAMOS_PARENS.search(steamos.strip())
        if m and m.group(1).strip():
            return m.group(1).strip()
    return None


def record_if_build_id_changed(
    recorded: Optional[str],
    current: Optional[str],
    *,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    changed=(),
    path: Optional[str] = None,
    journal_fn=None,
) -> Optional[dict]:
    """Record a single `steamos_update` timeline entry when `BUILD_ID` changed."""
    if not needs_attention(recorded, current) or timeline is None:
        return None
    return timeline.record_steamos_update(
        before_build_id=recorded,
        after_build_id=current,
        before=before,
        after=after,
        changed=changed,
        path=path,
        journal_fn=journal_fn,
    )
