# Changelog

Z13-Helper (`io.github.iannitram.Z13Helper`; called Z13 Controller,
`io.github.z13.Controller`, before 0.5.0) and its companion pieces: the host
scripts, `install.sh`, and the Decky plugin (`z13-helper`, was `z13-decky`). Dates are when the change
landed in the repo; "on-device check pending" means it is written and tested off
the device but not yet verified on the Z13. Plan and status: `docs/ROADMAP.md`.

## Unreleased

### Added
- **`z13-gamemode-report`** (`tooling/z13_gamemode_report.py`, installed to `~/.local/bin`). Reads
  the last Game Mode session(s) back from local logs: when it ran, games played, the Decky
  backend's actions, fan-watchdog events, gamescope/Steam/z13 errors and screenshots taken.
  `--screenshot` saves a PNG of the live Game Mode screen through `gamescopectl`. Read-only apart
  from that, local only, respects the logging opt-in. Session reports checked against this Z13's
  logs; `--screenshot` on-device check pending (needs Game Mode).
- **Logging is opt-in** (ROADMAP §10 0c). A full `./install.sh` asks once "Do you want to enable
  error logging to help with development of the app?" (`--yes` answers yes, `--repair` keeps the
  recorded answer, `--dry-run` shows the question) and records `"logging": true|false` in
  `~/.config/z13-helper/install.json`. Help → Error log gets a **Save logs to disk** checkbox that
  writes the same key and applies at once. With logging off the app writes no `app.log`, crash
  files, `fatal.log` or desktop fan trace, and the Decky plugin no Game Mode fan trace (read on its
  next start); crash reports stay in memory for the Error log window, warnings and hard-crash
  tracebacks still reach stderr/the journal. A missing key means on, so existing installs keep
  logging. On-device check pending.

### Changed
- **One host-command seam** (`src/host.py`) for the desktop app, the `z13-profile` CLI and the
  Decky plugin. It replaces five hand-rolled runners and three `_in_flatpak()` copies, and gives
  every Decky child the deck env (incl. the `LD_LIBRARY_PATH` fix) in one place. `install.sh`
  installs it to `~/.local/lib/z13-helper/` (for `z13-profile`) and into Decky's `py_modules/`;
  run `./install.sh --repair` after updating. On-device check pending.

## 0.5.0 — 2026-10-15

### Changed
- **Renamed to Z13-Helper** (after G-Helper). New Flatpak ID
  `io.github.iannitram.Z13Helper` (Flathub's convention for github.com/iannitram/z13-helper),
  user unit `z13-helper.service`, folders `~/.config/z13-helper` and
  `~/.local/state/z13-helper`, Decky plugin `~/homebrew/plugins/z13-helper`. `install.sh`
  (and `--repair`) migrates: stops and removes the old unit, moves both folders and leaves
  symlinks at the old paths, removes the old Decky plugin (backed up) and uninstalls the old
  Flatpak once the new one is installed. `install_check` warns about any leftovers.
- **One fan watchdog** (`src/fan_watchdog.py`) for the desktop app and the Decky plugin; Decky
  imports root-owned copies from its `py_modules/` (ADR 0001) and ticks it under its write lock.
- The Decky backend runs as deck (its `"_root"` flag isn't Decky's `"root"`); the profile engine
  call no longer goes through `runuser`, which refuses to run as deck, so Game Mode's profile
  list and apply work.

### Fixed
- **The desktop app is running after every switch to Desktop Mode.** Plasma's session restore
  raced the unit, and the unit's ExecStop then killed both copies. The unit now takes over
  (`--resident`), the app opts out of session restore, a side-button press starts a missing app,
  and the Decky plugin restarts it after a minute (never after a deliberate Quit).

### Added
- **Fan trace** (`fan-trace-desktop.log`, `fan-trace-gamemode.log`; Help → Error log → Fan
  trace): what both watchdogs read and did, with the 2 minutes before and the minute after any
  recovery.
- **Screen Brightness slider** in the main window, between Keyboard & Rear Light and
  Battery (`src/screen_brightness.py`). Moves the display backlight live as you drag
  (1–100 %, floored so it can't black the panel out) and follows changes made
  elsewhere (Fn keys, Plasma's slider, dim-on-idle) while the window is open. Goes
  through PowerDevil's `org.kde.ScreenBrightness` D-Bus API so Plasma stays in step;
  falls back to writing `/sys/class/backlight/amdgpu_bl0` on the host when PowerDevil
  isn't there. Desktop Mode only (Game Mode has Steam's own slider).

- The Flatpak may talk to `org.kde.ScreenBrightness` (needs a rebuild; until then the
  slider uses the sysfs fallback).

## 0.4.1 — 2026-10-12

### Added
- **Crash and error log** (`src/app_log.py`, `src/log_dialog.py`). Everything the app
  logs goes to `~/.local/state/z13-controller/app.log` (rotated, 1 MB × 4). Uncaught
  exceptions on the main thread, in Qt slots and in worker threads are saved as crash
  reports (`crashes/crash-*.txt`: traceback, versions, last 200 log lines) and the app
  keeps running; hard crashes (segfault/abort) are caught by `faulthandler` and
  recovered as `crashes/fatal-*.txt` on the next start. Qt's own warnings, failed
  profile steps and fan-watchdog recoveries are logged too.
- **Help → Error log…** (Ctrl+L): live log with a level filter, crash reports, Copy,
  Open folder. A red **⚠ N errors** badge in the status bar and a tray notice on a crash.
- `install_check.py verify` flags crash reports from the last 7 days.

### Changed
- Lightbar recovery also restarts the z13ctl daemon (it caches the hidraw path the
  USB reset invalidates) and recovers a stale handle on its own.
- The Flatpak may write `~/.local/state/z13-controller` (logs only).

## 0.4.0 — 2026-10-12

### Added
- **G-Helper layout** (ROADMAP §5): stacked main window (Silent / Balanced / Turbo /
  Fans + Power tiles, profile dropdown, Keyboard & Rear Light, Battery Charge Limit,
  Run on Startup, Version, Updates / Quit) and a **Fans + Power** window with a
  draggable 8-point curve and SPL / sPPT / fPPT sliders.
- **Profiles engine** `src/profiles.py`, also the host CLI `z13-profile`
  (`list / active / show / apply / restore`). Built-ins Silent, Balanced, Turbo,
  Boondock (Boondock's screen/service effects run as profile hooks); custom profiles
  via ＋ / Rename / Remove. Login and resume restore the active profile.
- **Per-profile Max Fans**: switching to a `max_fans` profile turns Max Fans on,
  leaving it turns it off; Turbo ships with it on.
- **Fan watchdog** rules (app and Decky): re-pin a dropped Max Fans curve, recover
  0-RPM stalls and a frozen tach (one fan's reading unchanged for 90 s), always by
  platform-profile rewrite followed by the curve.
- **Updates panel**: upstream watch (z13ctl, Decky Loader, kernel, SteamOS, asusctl,
  InputPlumber, G-Helper, community repos) with installers for z13ctl
  (sha256-verified, daemon `--no-button`), Decky Loader and SteamOS.
- **`install.sh` / `--repair` / `--dry-run`** with `tooling/install_check.py verify`
  (green/yellow/red report) and `src/installers.py repair`.
- **Repair install** button in the Updates panel, plus "Last install/repair" from
  `install.json` (which now records `repo_path`). On-device check pending.
- **Wedged rear-lightbar recovery**: `z13-lightbar-reset.service` (root oneshot,
  USB `0b05:18c6` re-authorize) + a polkit rule that lets deck start only that
  unit; used automatically after a lightbar write times out (at most every 2 min,
  one retry). On-device check pending.
- **Decky parity** (ROADMAP §7): profile dropdown from `profiles.json`, TDP sliders,
  charge-limit slider, per-device brightness, telemetry. On-device check pending.
- pytest suite (no hardware, no PyQt6) and CI.

### Changed
- Backend: **z13ctl 1.4.2** with its user daemon (`--no-button`), was 1.1.6.
- Brightness is written per device (keyboard first), so a wedged lightbar no longer
  blocks the keyboard.
- Side button read from evdev on the host (`KEY_PROG3` = 202) instead of
  InputPlumber D-Bus; the app is single-instance.

### Fixed
- `install.sh`: no-terminal detection (`-r /dev/tty` is always true), logging survives
  a vanished stdout, dry run no longer leaves `__pycache__` in the checkout, a failed
  plugin build no longer installs a stale `dist/`, keep-list append failures reported.
- z13ctl calls from the app now have a timeout (they could hang the apply queue).
- Deleting the active profile now also updates the legacy `current-profile` file.
- `profiles.validate` raises `ValueError` (not `AttributeError`/`TypeError`) for a
  non-string name, a non-object TDP or a malformed curve point.
- Fan watchdog (app and Decky): a sensor dropout resets the stall counter.
- `installers._fetch_release` removes its temp dir when the version check fails;
  a hung `z13ctl --version` counts as "not runnable"; systemctl probes have timeouts.
- Decky Max Fans OFF restores the saved snapshot, else the active profile's curve,
  else firmware auto (it used to fall back to a hard-coded aggressive curve).
- Decky: profile apply / Max Fans / platform profile run off the loader's event
  loop, serialised; a profile apply is no longer killed after 15 s.
- Fans + Power window no longer calls `z13ctl tdp --get` (principle 5) on refresh.
- `config.json` writes are atomic and thread-safe.
- Removing a non-active profile no longer re-applies the active one (fan dip).
- The fan watchdog stands down while an apply is queued or running.
- `boondock` writes the platform profile and TDP through z13ctl (no sysfs, no sudo).
- `z13-button-watcher`: the Game Mode probe has a timeout and can't kill the loop.

## 0.1.0 — 2026-05-03
- First Flatpak: Dashboard tab (profile, battery, brightness, telemetry), Advanced
  tab (TDP, fan curve, undervolt), system tray, side-button toggle.
