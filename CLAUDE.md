# CLAUDE.md — z13-helper

Cold-start orientation for Claude Code working in this repo. Read this first.

**Renamed 2026-10-15 (v0.5.0):** Z13 Controller → **Z13-Helper** (after G-Helper). Flatpak
`io.github.z13.Controller` → `io.github.iannitram.Z13Helper`, `z13-controller.service` →
`z13-helper.service`, `~/.config` + `~/.local/state` folders `z13-controller` → `z13-helper`
(symlinks remain at the old paths), Decky plugin `z13-decky` → `z13-helper`, repo
`~/dev/z13-controller` → `~/dev/z13-helper` (old path is a symlink), GitHub
`iannitram/z13-controller` → `iannitram/z13-helper` (published with fresh history the same day).
Older docs (`docs/plan/`, past CHANGELOG entries) keep the old name.

## What this is

A **PyQt6 system-tray GUI** (`io.github.iannitram.Z13Helper`, a Flatpak) that controls the **ASUS ROG
Flow Z13 (GZ302EA, 2025, Strix Halo)** on SteamOS — profiles, TDP, fan curve, battery limit,
keyboard brightness, telemetry, **profiles** (Boondock is one), upstream **updates**, and the
**side-button toggle**. UI follows **G-Helper**'s layout (main window + Fans + Power window). It's a
thin front-end over the `z13ctl` CLI (`~/.local/bin/z13ctl`, **1.4.2 with its daemon** since
2026-10-09), which does all privileged hardware access; the app tunnels to it with `flatpak-spawn --host`.

**Start with [`docs/ROADMAP.md`](docs/ROADMAP.md)** — the only forward-looking doc: status, principles,
open phases, upstream watch, verification protocol. §9a (one-shot `install.sh` / `--repair`) shipped 2026-10-11;
the Updates panel's **Repair install** button and the wedged-lightbar auto-reset (§3) were added 2026-10-12 (v0.4.0).

- Entry point: `main.py` → `src/dashboard_ui.py` (main window/tray) + `hardware_controller.py`
  (z13ctl wrapper) + `sensor_reader.py` (hwmon sysfs) + `fans_window.py` (Fans + Power: TDP/fan curve/undervolt) + `profiles.py` (profile engine, also the host `z13-profile` CLI)
  + `config_manager.py` + `evdev_listener.py` (side-button reader). Plan: `docs/ROADMAP.md`.
- **Every host command goes through `src/host.py`** (stdlib only): `host.run(argv, timeout=,
  as_deck=, input=)` returns `Result(rc, out, timed_out)` and never raises (timeout → rc 124, missing
  binary → 127). It picks its mode once: Flatpak (prefix `flatpak-spawn --host`), plain host user,
  Decky as deck, or root. Under Decky/root children get the deck env (`/run/user/1000`, deck's session
  bus, and the host's `LD_LIBRARY_PATH` back from `LD_LIBRARY_PATH_ORIG`; the loader's own broke every
  `systemctl`), and `as_deck=True` adds `runuser -u deck -- env HOME=/home/deck …` under root. Don't
  add `subprocess` calls to app/Decky modules; the exceptions are the stdin-fed host Python runners
  (`updates_dialog`, the side-button reader in `dashboard_ui`). Three copies on disk: the Flatpak's,
  `~/.local/lib/z13-helper/host.py` (for `z13-profile`; profiles.py adds that folder to `sys.path`),
  and Decky's root-owned `py_modules/host.py` (ADR 0001). Tests: the `fake_host` fixture
  (`tests/fake_host.py`, shared with `decky/tests`) records argv and scripts results.

## Repo layout (post-consolidation 2026-07-14)

```
main.py, src/, assets/, flatpak/   ← the app (canonical, current)
README.md                          ← what the app does, install, how it works, credits
docs/
  BUTTON-AND-INPUTPLUMBER.md       ← READ THIS for the side-button bug + fix (synthesis)
  ROADMAP.md                       ← THE plan (rewritten 2026-10-09)
  plan/ (Phase_1-6, frozen history), hardware-enablement.md
tooling/                           ← reference copies of host scripts (boondock, z13-* profiles,
                                     z13_fanlog.sh, z13ctl-install/); live copies in ~/.local/bin
packaging/host/                    ← the system files the app depends on (install.sh installs the
  systemd/{user,system}/, polkit/,    ones in tooling/install_check.py's MANIFEST; udev rules + perms
  udev/, asusd/, xdg/                 unit are z13ctl setup's, reference only here)
CHANGELOG.md                       ← per-release changes (app version: APP_VERSION in src/dashboard_ui.py)
tests/                             ← pytest suite (`python3 -m pytest`); decky/tests is unittest
decky/                             ← Game Mode QAM plugin (installs to ~/homebrew/plugins/z13-helper)
```

## Fans: what the firmware actually does (learned 2026-10-09, cost a day)

- Every curve **change** stops the fans ~5–10 s while the firmware re-ramps. Normal.
- Below the curve's **first point** the fans idle (with hysteresis) — so Max Fans' curve starts at 20 °C.
- The firmware can ignore a bare curve write (fans at 0 RPM, curve "enabled"); only **platform-profile
  rewrite + curve** reliably recovers. All watchdog recovery uses that.
- **The tach can lie:** a stopped fan can keep reporting its last RPM (left fan, 8700, frozen). Healthy
  readings jitter every tick; one fan's reading unchanged 90 s ⇒ re-assert. Logic: `src/fan_watchdog.py`, the
  only copy: Decky imports it from its `py_modules/` (installed root-owned by `install.sh`; see
  `docs/adr/0001-decky-imports-root-owned-copies.md`). Domain terms: `GLOSSARY.md`.
- `z13ctl profile --get` says `custom` under 1.4.2; read the firmware profile from
  `/sys/firmware/acpi/platform_profile`.

## The side-button issue (the headline bug)

**Symptom:** the small side button (Armoury Crate button) should pop the app open; it doesn't.

**Root cause (historical):** the app used to bind the button via **InputPlumber D-Bus** (`ui_quick`), but
**InputPlumber is permanently `mask`ed** on this machine (since 2026-05-06, to kill a phantom
game-controller). So the app's only button pathway is dead.

**Fix (shipped in this repo):** read the button **directly via evdev** on the `Asus WMI hotkeys`
device (`KEY_PROG3` = **202** / 0xca — the wiki's "579" was wrong, which was the actual bug). Because the Flatpak sandbox drops the `users`/`input` groups, the read
runs **on the host** via `flatpak-spawn --host` and streams `PRESS` events back to a thread-safe Qt
signal. Full explanation + the two other preconditions (app must be resident; Flatpak must be
rebuilt) are in **[`docs/BUTTON-AND-INPUTPLUMBER.md`](docs/BUTTON-AND-INPUTPLUMBER.md)**.

**Do NOT unmask InputPlumber to "fix" this** — the mask is load-bearing for game controllers.
Diagnostic: `python3 src/evdev_listener.py` (prints every keycode, marks the side button).

**Game Mode is a separate button pathway (shipped 2026-07-15).** The PyQt app can't run in gamescope,
so a standalone `--user` service `z13-button-watcher` (`tooling/z13-button-watcher` + the stdlib CDP
bridge `tooling/z13_cdp.py`; unit in `packaging/host/systemd/user/`) owns the button in Game Mode:
**single tap → Quick Access Menu** (right; tap again to close), **double tap → Steam menu** (left,
app switching). It opens menus with a pure-JS `MenuStore.OpenSideMenu(2|1)` call over Steam's CEF
socket (no input emulation → no controller-list pollution). The button is a *momentary pulse* (no
key-up-after-hold), so long-hold is impossible — hence double-tap. In Desktop Mode the in-app reader
toggles the window; the watcher only steps in when the app isn't running (a press starts
`z13-helper.service` and shows it). The Decky plugin also restarts a missing desktop app in Desktop
Mode (not after a deliberate Quit). Note the Decky backend runs as **deck**, not root (`"_root"` in
`plugin.json` isn't Decky's `"root"` flag). Full write-up: [`docs/plan/Phase_6.md`](docs/plan/Phase_6.md).

## Deploy + verify

- **Everything at once:** `./install.sh` (asks first; fetches latest z13ctl + Decky Loader) or
  `./install.sh --repair` (after a SteamOS update; no prompts, only re-applies what differs, keeps the
  z13ctl version). `--dry-run` writes nothing. Health check alone: `python3 tooling/install_check.py verify`
  (`--no-restore` = read-only). The host-file manifest lives in `tooling/install_check.py`; add new
  host scripts/units there. `z13ctl setup` owns the udev rule + `z13ctl-perms.service`; the copies in
  `packaging/host/` are reference only.
- **Logs and crashes:** the app logs to `~/.local/state/z13-helper/app.log`; uncaught exceptions
  and hard crashes land in `.../crashes/` (`src/app_log.py`; in-app: Help → Error log, Ctrl+L).
  Start there when the owner reports misbehaviour, together with `journalctl --user -u z13ctl`.
  Logging is **opt-in**: `"logging": false` in `~/.config/z13-helper/install.json` (asked by
  `install.sh`, toggled in Help → Error log; missing = on) means no files there at all, only the
  in-memory Error log and the journal (`src/install_record.py`, mirrored in `decky/main.py`).
- **Game Mode (you can't see it):** run `z13-gamemode-report` (`tooling/z13_gamemode_report.py`)
  instead of asking Ian what happened: last session's window (`gamescope-session.service` in
  `journalctl --user`), games (Steam's `logs/gameprocess_log.txt`), the Decky backend's lines
  (`journalctl -u plugin_loader`; Decky writes no `~/homebrew/logs/z13-helper/`), fan-watchdog
  events from `fan-trace-gamemode.log`, errors and screenshots. `--last N` for more. While Game
  Mode runs (a `gamescope` process; the report says "RUNNING NOW"), `z13-gamemode-report --screenshot` saves a PNG
  under `~/.local/state/z13-helper/screenshots/` that you can Read. Read-only apart from that.
- **Rebuild/install the Flatpak:** `bash flatpak/build.sh` (uses `org.flatpak.Builder` +
  `org.kde.Platform//6.10`; pulls PyQt6 from PyPI, needs network). Then restart the app.
- **Run for debugging without a rebuild:** the sandbox has PyQt6 under `/app`, so you can run the
  repo's fixed source inside it:
  `flatpak run --filesystem=/home/deck/dev/z13-helper --command=python3 io.github.iannitram.Z13Helper /home/deck/dev/z13-helper/main.py --show`
- **Resident instance:** `systemctl --user {enable,start} z13-helper.service` (unit runs the
  Flatpak hidden; the side button/tray opens it). Fixed unit lives in `packaging/host/systemd/user/`.
- **Single instance:** Flatpak moves the app out of the unit's cgroup (into `app-flatpak-*.scope`),
  so plain systemd stop can't kill it. The unit therefore uses `ExecStop=... --quit` (graceful)
  plus `flatpak kill` / host-reader `pkill` in ExecStartPre and ExecStopPost, and the app is
  single-instance (`src/single_instance.py`: flock + Unix socket in `$XDG_RUNTIME_DIR/app/$FLATPAK_ID`).
  A second launch just raises the window (`--show`) or exits. To count instances without matching
  your own shell: `pgrep -af '[/]app/share/z13-helper/main[.]py'`.

## Maintainer notes

The maintainer keeps machine-specific notes (a private wiki of how this Z13 and its SteamOS install
are set up) outside the repo; on that machine they load from the untracked `CLAUDE.local.md`.

## Hardware quick facts

ASUS ROG Flow Z13 GZ302EA · SteamOS (Steam Deck image on non-Deck hw) · Strix Halo Ryzen AI Max+ 395.
Side button = `Asus WMI hotkeys` `KEY_PROG3`/**202** (0xca; the docs' "579" was wrong). Sensors via hwmon by name (`asus`=fans,
`acpitz`=APU, `amdgpu`=GPU). Battery limit resets to 100% on reboot (firmware) — app re-applies.
`ryzen_smu` absent on stock SteamOS → undervolt disabled. Wedged rear lightbar: a USB reset alone
isn't enough — it re-enumerates under new hidraw nodes and the `z13ctl.service` user daemon caches
the old path, so `profiles.recover_lightbar()` restarts that daemon too after the reset (and
`recover_lightbar_stale()` restarts it alone, no reset, when the daemon's cached node goes stale
on its own — ENODEV without a timeout).
