> **Historical (frozen 2026-10-09).** Superseded by [`docs/ROADMAP.md`](../ROADMAP.md); kept as a record of how the project got here.

# Phase 3: GUI Development (PyQt/PySide)

## Goals
- Build a user-friendly interface for SteamOS Desktop and Game Mode.

## Tasks
- [x] Design the Main Dashboard with real-time APU/Fan telemetry.
- [x] Implement Profile Selector (Balanced, Performance, Quiet).
- [x] Add Battery Limit slider/input.
- [x] Add Keyboard Brightness controls.
- [ ] Create a Fan Curve visualization component.
- [x] Ensure the GUI is lightweight and responsive for SteamOS environments.

## Launch
```bash
cd "/home/deck/z13/code/opencode/z13 Controller"
source venv/bin/activate
python3 main.py
```

## Remaining
- Fan Curve visualization (deferred to Phase 4 / future iteration)
- ~~Side-button integration in GUI~~ — **DONE 2026-07-14.** Wired `EvdevListener` (host-side via
  `flatpak-spawn --host`) → `_toggle_window`. Root cause of it never working: the button emits
  `KEY_PROG3` = keycode **202**, not 579. See [`../BUTTON-AND-INPUTPLUMBER.md`](../BUTTON-AND-INPUTPLUMBER.md).

## Update — 2026-07-14
Desktop Mode app is complete and shipping: side button opens the window, and a **Max Fans** toggle
was added (100% flat curve ↔ custom aggressive curve). Next work targets **Game Mode** — see
Phase 5 (Decky plugin) and Phase 6 (side-button → QAM).
