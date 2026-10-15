> **Historical (frozen 2026-10-09).** Superseded by [`docs/ROADMAP.md`](../ROADMAP.md); kept as a record of how the project got here.

# Phase 6: Side Button in Game Mode → open the QAM

## Goal
In **Game Mode**, a side-button press opens the Steam **Quick Access Menu** (right-side QAM) → the
user taps through to Decky → the Z13 plugin (Phase 5). In **Desktop Mode** the same button keeps
toggling the PyQt window (already working).

> ⚠️ Not polish — a **hard dependency.** The right-edge touchscreen swipe → QAM does **not** work on
> this Z13 (confirmed by Ian; the Deck gesture depends on the `deck-uhid` controller context we
> removed to fix games). So a button/JS path is the **only** touch-friendly way into the plugin.

## ✅ SHIPPED (2026-07-15)

Built as a **general controller-free Game-Mode navigation** control (Ian's steer: the button is *not*
plugin-specific — it's a system nav entry point for using the Z13 in Game Mode without a controller).

**Gestures** (the button is a *momentary pulse* — see the evtest finding below — so gestures are by
count, not hold):

| Gesture | Action |
|---|---|
| **single tap** | Quick Access Menu (right). If a side menu is already open, it *closes* it (controller-free dismiss). |
| **double tap** | Steam menu (left): app switching / library / power. |

Fired instantly per press (no debounce), so single-tap QAM is instant; a double-tap therefore flashes
the QAM (1st tap) then flips to the Steam menu (2nd tap) — Ian's chosen trade-off over adding latency.

**Components** (all new, in the repo):
- `tooling/z13_cdp.py` — **dependency-free** (stdlib-only) CDP client + menu actions. Replaces the
  `websocket-client` transport so the always-on service can't break on a SteamOS python bump.
  `tooling/z13_qam.py` is now a thin back-compat shim over it.
- `tooling/z13-button-watcher` — the daemon: finds the WMI-hotkeys node, reads `KEY_PROG3`=202
  presses, does double-tap detection (`DOUBLE_TAP_S=0.35`), gates on Game Mode, dispatches to
  `z13_cdp`. Non-exclusive read (no `EVIOCGRAB`) so it coexists with the desktop app's reader.
- `packaging/host/systemd/user/z13-button-watcher.service` — `--user`, `WantedBy=graphical-session.target`.
  Installed to `~/.local/bin` + `~/.config/systemd/user`, `enable --now`.

**Verified JS (raw path primary, DFL fallback):** `SteamUIStore.WindowStore.GamepadUIMainWindowInstance`
`.MenuStore.OpenSideMenu(2)` = QAM (right), `.OpenSideMenu(1)` = Steam menu (left), `.CloseSideMenus()`,
`.IsAnySideMenuVisible()`. `window.DFL.Navigation.{OpenQuickAccessMenu,OpenMainMenu,CloseSideMenus}` is
the fallback (works without Decky's `window.DFL`). `ESideMenu` enum: None=0, Main=1, QuickAccess=2.

**evtest finding (RESOLVED):** the `Asus WMI hotkeys` side button emits an **instant DOWN+UP pulse**
per press — no delayed key-up, no autorepeat, regardless of physical hold. So **long-hold is
impossible**; double-tap is the substitute for the second action.

**Session gate (kept from plan):** `systemctl --user is-active --quiet gamescope-session.target`.
Desktop Mode is a no-op in the watcher → the PyQt app's in-app reader still owns the desktop toggle
(unchanged). Did NOT centralize desktop ownership / D-Bus Toggle / EVIOCGRAB — deferred (see Tasks).

## ✅ Mechanism (resolved + proven live, 2026-07-14)
Open the QAM with a **pure JavaScript call** — no input emulation, no virtual controller, **zero**
controller-list pollution (categorically — it can't recreate the `deck-uhid` phantom):

```js
window.DFL.Navigation.OpenQuickAccessMenu()   // dismiss: window.DFL.Navigation.CloseSideMenus()
// OpenQuickAccessMenu(QuickAccessTab.Decky /*=999*/) lands on the Decky tab
```

Steam's Game-Mode UI is a CEF/React app; Decky injects `decky-frontend-lib` (`@decky/ui`) globally as
`window.DFL`. **Verified on this machine**: CDP `Runtime.evaluate` against the `SharedJSContext`
target opened and closed the QAM; `typeof window.DFL.Navigation.OpenQuickAccessMenu === "function"`.
10+ installed plugins already call it.

**Two ways to fire it** (same API):
- **In-plugin (preferred, cleanest):** Decky backend reads `event5`/202 → `decky.emit("open_qam")` →
  frontend listener calls `Navigation.OpenQuickAccessMenu()`. The plugin frontend is always mounted
  in `SharedJSContext`, so the listener works even when our panel isn't open.
- **Host watcher via CDP (Mechanism #2):** a host process injects the same JS over Steam's CEF debug
  socket `ws://127.0.0.1:8080` (`SharedJSContext` target). Chosen when the button watcher already
  lives on the host (it must, for the Desktop-mode toggle). Prereqs present:
  `~/.steam/steam/.cef-enable-remote-debugging`, steamwebhelper LISTEN on 127.0.0.1:8080,
  `websocket-client` installed. Slightly more fragile (re-enumerate `/json` each press).

### Rejected / fallback
- **uinput Ctrl+2 keyboard** — *clean fallback only.* Ctrl+2 is gamescope's legacy QAM inject, but
  gamescope only forwards it to the **focused** window, so it's ignored while a game is focused
  ([SteamOS #1050](https://github.com/ValveSoftware/SteamOS/issues/1050),
  [gamescope #1357](https://github.com/ValveSoftware/gamescope/issues/1357)). Pollution risk: none
  (EV_KEY-only device isn't a controller). Efficacy in-game: unproven → last resort.
- **Virtual gamepad — excluded entirely.** Exact device class that made the `deck-uhid` phantom.

## Architecture: one context-aware host watcher
`z13-button-watcher.service` (systemd `--user`, `After/WantedBy=graphical-session.target` so it spans
both sessions) is the **single owner** of `event5` (opens it with `EVIOCGRAB`):
- Reader: reuse `src/evdev_listener.py` (`find_side_button_device()` + raw-struct loop; no deps).
- **Session detection (per press):** `systemctl --user is-active --quiet gamescope-session.target`
  → `game`; else `desktop`. (Do NOT use env vars — captured once at service start, stale after a mode
  switch.)
- Dispatch: **desktop** → toggle the PyQt app; **game** → `open_qam_via_cdp()`.

### Reconcile with the existing desktop reader (do this only after in-game validation passes)
- The app currently reads the button itself (`src/dashboard_ui.py` `_start_host_side_button_reader`).
  Move button ownership entirely into the watcher so nothing double-handles `event5`.
- Drive the desktop toggle over the **session D-Bus**: app registers `io.github.z13.Controller` with a
  `Toggle` method; watcher calls it via `gdbus`. (Session bus is shared host↔Flatpak for your own
  app-id.) Fallbacks: single-instance `flatpak run … --toggle`; SIGUSR1 (less reliable through bwrap).
- If z13ctl's daemon is ever enabled, run it `--no-button` so its built-in watcher doesn't compete.

## Tasks
- [x] **evtest the button** — done live: instant DOWN+UP pulse, no key-up-after-hold, no autorepeat →
      long-hold impossible → double-tap chosen. (Device node is `event3` here, resolved by name.)
- [x] Build the host watcher (`z13-button-watcher`) with a stdlib CDP bridge (`z13_cdp.py`) and
      session-aware dispatch; installed + `enable --now`; single/double tap confirmed via journal.
- [ ] **Spike 1 focus test (USER, in Game Mode):** `tooling/spike1_qam_test.py` — launch a game, fire
      the trigger, confirm the QAM opens **and takes input focus** over the running game, pollution
      guardrail unchanged. (Menu-open + dispatch proven; in-game focus is the last thing to eyeball.)
- [ ] *(Deferred)* Centralize desktop ownership in the watcher (remove the in-app reader; D-Bus
      `Toggle`; EVIOCGRAB). Not needed for Game-Mode nav; desktop still works via the app's own reader.

## ⚠️ Last thing only the user can verify (needs Game Mode with a game running)
QAM/Steam-menu opens **and grabs input focus** over a *running game* (the menu-open + double-tap
dispatch are proven; in-game focus routing is the one scenario not checkable from the desktop side).
