> **Historical (frozen 2026-10-09).** Superseded by [`docs/ROADMAP.md`](../ROADMAP.md); kept as a record of how the project got here.

# Phase 5: Game Mode Support via Decky Plugin

## Goal
Make every Z13 Controller feature available in **Game Mode** (Gamescope), where the Desktop PyQt
window can't sensibly live. Do it the native way: a **Decky Loader plugin** that renders in the
Quick Access Menu (QAM).

## Why Decky (not the PyQt app in Game Mode)
Gamescope is a single-app compositor — a floating desktop window fights it. The QAM is the native
home for system controls, and **Decky Loader is already installed and running** on this machine
(`plugin_loader.service`; `~/homebrew/plugins/` has tailscale-control, TabMaster, …). The local
`tailscale-control` plugin is a working template for exactly our shape: a Python backend that
shells to a CLI + a React switch that runs in Game Mode.

## Architecture — one backend, two thin frontends
- **Backend contract = `z13ctl` (+ `~/.local/bin/boondock`)** — already the single source of hardware
  truth. Both frontends only ever call it.
- **Desktop Mode** → existing PyQt Flatpak (Phase 1–4, complete).
- **Game Mode** → this Decky plugin: React QAM panel + Python `main.py` backend shelling to `z13ctl`.
- **Share the contract, not the UI.** Qt and React don't share code — so pull the shared semantics
  (profile label→arg map, the aggressive fan curve, the Max Fans 100%↔custom rule, boondock meaning)
  into one small spec both frontends read, to prevent drift.
- Bonus: a Decky backend runs **as root** under the loader, so in Game Mode it calls `z13ctl`
  directly — no `flatpak-spawn --host` dance needed.

## Feature parity target (all via z13ctl)
- [ ] Performance profile: Balanced / Performance / Quiet (`z13ctl profile --set`)
- [ ] **Max Fans** toggle: 100% flat curve ↔ restore custom aggressive curve (mirrors the desktop app)
- [ ] TDP: PL1 / PL2 / PL3 (`z13ctl tdp --set … [--force]`)
- [ ] 8-point fan curve editor (`z13ctl fancurve --set`)
- [ ] Battery limit 40–100 (`z13ctl batterylimit --set`)
- [ ] Keyboard / lightbar brightness (`z13ctl brightness`)
- [ ] Boondock off-grid saver toggle (`~/.local/bin/boondock on|off|status`)
- [ ] Live telemetry (APU/GPU temp, fan RPM) — read hwmon sysfs from the backend

## Tasks
- [ ] **Spike 2 first** (see task list): scaffold a minimal one-control plugin (profile switch or Max
      Fans) modeled on `tailscale-control`; prove it renders in the QAM and controls hardware.
- [ ] Confirm the current Decky frontend API (`@decky/api` / `@decky/ui` vs legacy
      `decky-frontend-lib`) and backend `decky` module shape — from the research spike.
- [ ] Build out full feature parity panel by panel.
- [ ] Extract the shared semantics spec consumed by both the plugin and the PyQt app.
- [ ] Local install/reload loop for testing (copy to `~/homebrew/plugins`, restart `plugin_loader`,
      CEF debug).

## Notes / open questions (resolved by the Phase 5/6 research spike)
- Exact current Decky plugin toolchain + APIs (2026).
- Whether a plugin can be **deep-linked** (side button → straight to our plugin) or the user must go
  QAM → Decky tab → plugin. (Feeds Phase 6.)
- The QAM entry gesture is handled in **Phase 6**. ⚠️ **Correction (2026-07-14):** the right-edge
  touchscreen swipe → QAM does **NOT** work on this Z13 (confirmed by Ian) — that Deck gesture appears
  to depend on the `deck-uhid` controller context we removed. So there is likely **no touch path to
  the QAM** here, which promotes Phase 6 from "polish" to a **hard dependency** for a usable
  touch-driven Game-Mode experience.

## ✅ Resolved by research (2026-07-14) — locked decisions
- **Toolchain: the MODERN Decky stack** — `@decky/api ^1.1.3` + `@decky/ui ^4.11.0` + `@decky/rollup
  ^1.0.2`, built with **pnpm** → `dist/index.js`; backend uses `import decky` (not `decky_plugin`).
  Exemplar = `~/homebrew/plugins/decky-lsfg-vk`. **Do NOT** copy `tailscale-control` — legacy pattern.
- **Backend runs as root** via the `"_root"` flag → calls `~/.local/bin/z13ctl` / `boondock` and
  touches sysfs/hidraw directly, **no `flatpak-spawn`**.
- **HID subtlety:** for `brightness` (hidraw), run z13ctl with `XDG_RUNTIME_DIR=/run/user/1000` so it
  uses the deck user daemon's socket; sysfs controls (profile/tdp/fancurve/battery) are unaffected.
- **Max Fans in the plugin** mirrors the desktop app: ON → 8×100% curve; OFF → restore the custom
  aggressive curve (snapshot on engage; fall back to shared config `fan_curve_saved`, then auto).
- **Install/reload:** plugins dir is root-owned → `sudo cp -r decky ~/homebrew/plugins/z13-decky`
  + `sudo systemctl restart plugin_loader.service`; or Decky → Developer mode → Reload. Backend logs
  at `~/homebrew/logs/z13-decky/`; frontend via CEF inspector `:8080`.
- **No per-plugin QAM deep-link:** all plugins share Decky's QAM tab (id 999). Entry is QAM → Decky
  tab → Z13 Controller (or `OpenQuickAccessMenu(QuickAccessTab.Decky)`).
- **Plugin source lives in the repo at `decky/`**, installs as `~/homebrew/plugins/z13-decky`.
