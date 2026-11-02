from typing import Optional


def read_build_id(os_release_text: str) -> Optional[str]:
    """Extract the BUILD_ID from /etc/os-release text, removing quotes if present."""
    for line in os_release_text.splitlines():
        if line.startswith("BUILD_ID="):
            # Remove the BUILD_ID= prefix
            value = line[9:]  # Remove "BUILD_ID="
            # Remove surrounding quotes if present
            if value.startswith('"') and value.endswith('"'):
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
