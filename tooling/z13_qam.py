"""CDP bridge to Steam's Game-Mode UI — back-compat shim.

This module used to carry its own `websocket-client`-based CDP client. That transport now
lives in the dependency-free `z13_cdp` (stdlib-only, so it survives SteamOS interpreter
bumps — see that module's header). This file remains as a thin, stable alias so existing
callers (e.g. `spike1_qam_test.py`) keep working.

Opens the Steam side menus with a pure-JS `MenuStore` call over Steam's CEF remote-debug
socket — no input emulation, so it cannot repollute the controller list (the deck-uhid
phantom failure mode). Proven live on the Z13 (2026-07-15).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from z13_cdp import (  # noqa: E402,F401  (re-exported for back-compat)
    eval_js,
    tap,
    open_qam,
    open_main_menu,
    close_side_menus,
    focused_window_present,
)


def open_qam_decky(timeout=3.0):
    """Open the QAM on the Decky tab (QuickAccessTab.Decky == 999)."""
    return eval_js(
        "(function(){var S=window.SteamUIStore,W=S&&S.WindowStore,"
        "i=(W&&W.GamepadUIMainWindowInstance)||(S&&S.ActiveWindowInstance),m=i&&i.MenuStore;"
        "if(m&&typeof m.OpenQuickAccessMenu==='function'){m.OpenQuickAccessMenu(999);return 'decky';}"
        "if(window.DFL&&window.DFL.Navigation){window.DFL.Navigation.OpenQuickAccessMenu(999);return 'decky-dfl';}"
        "return 'unavailable';})()",
        timeout,
    )


if __name__ == "__main__":
    actions = {
        "open": open_qam, "tap": tap, "decky": open_qam_decky,
        "menu": open_main_menu, "close": close_side_menus, "focus": focused_window_present,
    }
    name = sys.argv[1] if len(sys.argv) > 1 else "open"
    fn = actions.get(name, open_qam)
    print(f"{name} -> {fn()}")
