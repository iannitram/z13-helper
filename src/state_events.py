from typing import Sequence

def event_action(name: str, gui_toggle_selected: bool) -> str:
    """Determine the action to take for a z13ctl event.

    Args:
        name: The event name from z13ctl
        gui_toggle_selected: Whether the GUI toggle is selected as the button path

    Returns:
        "refresh" for "state-changed" and "power-source" (regardless of gui_toggle_selected)
        "toggle-window" for "gui-toggle" only when gui_toggle_selected is True
        "ignore" for everything else, including "gui-toggle" when not selected
    """
    if name in ("state-changed", "power-source"):
        return "refresh"
    elif name == "gui-toggle" and gui_toggle_selected:
        return "toggle-window"
    else:
        return "ignore"


def op_status(saved: bool, applied: Sequence[bool]) -> str:
    """Determine the operation status based on save and apply results.

    Args:
        saved: Whether the operation was saved
        applied: Sequence of boolean results for each apply step

    Returns:
        "applied" if all steps were successful and saved
        "saved" if no steps were attempted but saved
        "failed" if no steps were attempted and not saved
        "saved-not-applied" if no steps were successful but saved
        "partial" for mixed success/failure or when applied steps exist but not all succeeded
    """
    if not applied:
        return "saved" if saved else "failed"

    all_true = all(applied)
    any_true = any(applied)

    if all_true:
        return "applied" if saved else "partial"
    elif not any_true:
        return "saved-not-applied" if saved else "failed"
    else:
        return "partial"
