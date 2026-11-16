# Changelog

Z13-Helper (`io.github.iannitram.Z13Helper`; called Z13 Controller,
`io.github.z13.Controller`, before 0.5.0) and its companion pieces: the host
scripts, `install.sh`, and the Decky plugin (`z13-helper`, was `z13-decky`). Dates are when the change
landed in the repo; "on-device check pending" means it is written and tested off
the device but not yet verified on the Z13. Plan and status: `docs/ROADMAP.md`.

## Pipeline

- 2026-11-12 Design pass (#229): dark and light themes from one token file (`src/design_tokens.json`), following the system colour scheme with a manual choice under View → Theme (`"theme"` in `config.json`). Main window: a status strip (CPU, GPU, fans, battery), cards for Performance Mode and Keyboard & Rear Light, one settings list for screen and battery. Fans + Power: the profile picker on top, cards for Platform Profile, CPU Boost and Power Limits, the fan curve and a two-plot Last Hour chart (no dual axis). Decky panel colours come from the same tokens (`decky/src/tokens.ts`). A test fails on any colour literal outside the token file. `docs/screenshots/` still shows the old look; on-device check pending.
- 2026-11-11 Done (#202): this project's own releases are an update source in `src/update_watch.py` (neutral until v1.0.0 exists or when GitHub is unreachable), with tests for newer, equal, none and offline. ROADMAP §11 row marked done; the on-device Updates panel check is still open.
- 2026-11-11 Docs reconciled with the tracker and the device evidence (#205): PIPELINE notes, SPEC status paragraph (Musts and Won'ts unchanged), ROADMAP status marks and §11 versions, both code-map headers and stale rows, AGENTS.md, tests/README.md. The EPP ADR was renumbered 0002 → 0003 (two files were numbered 0002). Owner decisions 2026-11-11: 1.0 ships from main and the post-1.0 branch merges after the tag; #66 keeps its 10 s daemon-absent backstop.
- 2026-11-11 Docs follow-up (#205): own-release update source (PR #209, #202) and Boondock TDP 28/32/45 W with clamped writes (PR #213, #203) recorded as merged, device checks still owed; ROADMAP §11 row for this project's releases. Post-update armoury fan-attribute check bug filed as #216.
- 2026-11-11 CI on main fixed (#198, PR #206): ten tests no longer depend on the session user or the machine model. #74 and #75 closed with device evidence; issues #199-#205 filed (acceptance, own-release update source, Boondock TDP, release, docs).
- 2026-11-11 Docs: replaced dev-machine paths in AGENTS.md and ROADMAP with placeholders (repo-lint, R01).

- 2026-11-02 Code map (MAP/README.md, MAP/decky.md) refreshed to match main: repair PRs merged, new modules and tooling listed.
- 2026-11-01 Phases 15 (goal control) and 16 (hardware as the interface) added after 1.0 on the owner's pick: ROADMAP §10a, §10b; code map MAP/README.md "Planned (after 1.0)"; issues #123-#135 filed (`after-1.0`). SPEC unchanged.
- 2026-10-26 End-of-night sweep: code map (MAP/README.md, MAP/decky.md) refreshed for PR #49 (`decky/src/profileSwitch.ts`) and PR #3 (`z13_gamemode_report.py`); ROADMAP §1 open-bugs status; PIPELINE notes issue list updated (#16 done, #74-#81 added); #16 closed.
- 2026-10-26 SteamOS 3.9.5 (stable 2026-10-23, kernel 7.2) researched: ROADMAP §9c Phase 12d, §1, §11 and §12 updated; SPEC target OS line; issues #74-#81 filed, comments on #29, #61, #66, #67, #70, #71, #73.
- 2026-10-26 Spec approved (SPEC.md: Phases 7-13 verified on the device, open bugs, #23, #27); code map MAP/README.md; issues #61-#73 filed; stage Dev Loop.
- 2026-10-26 Moved from Parked to Spec on the owner's word; needs a spec conversation.
- 2026-10-26 Normalized to pipeline v2 at Parked (paused); stage in `PIPELINE.md`; the old CLAUDE.md notes moved into AGENTS.md.

## Unreleased

(nothing yet)

## 1.0.0 — 2026-11-16

The first 1.0 release. Evidence for each 1.0 Must: [`docs/releases/1.0.0.md`](docs/releases/1.0.0.md). Version 1.0.0 in the desktop app, the Decky package and the Flatpak AppStream metadata (#204). M9 (Game Mode Quick Access focus and the VRR profile) moved after 1.0 on the owner's word, tracked in #250.

- **Version 0.5.1 to 1.0.0** in `src/versions.py`, `decky/package.json`, the AppStream release list, README, AGENTS.md, PIPELINE.md, SPEC.md, docs/ROADMAP.md, MAP/README.md and MAP/decky.md; crash-report test fixtures follow.
- **Device acceptance pass of 2026-11-16 recorded:** M3, M4 (Repair install), M6, M7 (before and after a reboot, after the #244 and #245 fixes) and M8, plus #122, #30, #235, #203, #80 and #61 (evidence on the issues).
- **Known issues shipping in 1.0:** #243, #86, #80 and #73; #249 is fixed (PR #251); see the release record.

### Changes since 0.5.1

Built from `git log` between the 0.5.1 merge (34a1284) and main; no `v0.5.1` tag exists. "Checked on the device" means the Z13 on 2026-11-16 unless a date says otherwise.

**State, repair and the Musts**
- **One owner for profile and hardware state (M6; #23, PR #153).** App profiles are mirrored into `z13ctl` named profiles and every frontend refreshes on `z13ctl` events instead of polling. Checked on the device 2026-11-16 (#199): sync under 1 s, charger events seen, the app reconnected within 1 s after a daemon restart.
- **Post-update repair (M7; #27, PR #154).** After a SteamOS update the app lists the tasks that need attention and runs the selected ones with a plan, backups and a rollback record; it never reboots by itself. The runner is #110 (PR #150); the recipe format, argument parser, BUILD_ID banner and local recipe loader are #109, #111, #114 and #115 (PRs #138, #140, #144, #145); built-in recipes are #69 and #71 (PRs #156, #157, #170, #174).
- **Post-update amdgpu task handles `GRUB_CMDLINE_LINUX` blocks; already applied counts as success (#244, PR #247).** Found by the first M7 device run.
- **Post-update rocm task provides `libfmt.so.11`, decides compat library files one by one and verifies `rocminfo` loads (#245, PR #248).** Found by the first M7 device run. After both fixes M7 passed on the device 2026-11-16, before and after the reboot (#200).
- **Post-update armoury check tests the fan-curve hwmon node, not fan attributes (PR #218).**
- **Updates panel: a newer installable upstream shows in danger red (#236, PR #240; M4).** Repair install from the panel finished "All green" on the device (#201).
- **This project's own releases are an update source (#202, PR #209);** neutral until a v1.0.0 release exists or when GitHub is unreachable.
- **Lightbar: every lighting write path recovers a stale daemon handle or wedged endpoint once (#237, PR #241; M8).** Checked on the device: one ENODEV recovered and returned ok in 2.6 s.
- **Boondock TDP uses the firmware floors 28/32/45 W and reports only limits that applied (#203, PR #213);** the watchdog and Decky TDP restore clamp to the firmware ranges and no longer read live status (PR #217). Checked on the device: `z13ctl tdp --get` read 28/32/45 W, matching the app.
- **Boondock stops, starts and restores `llama-swap` as a `systemctl --user` service; the Docker container and VM handling are removed (#65, PR #101).** Old `ctr` and `vm` snapshot keys are discarded on restore.
- **`--system-check` no longer reports two false FAILs on SteamOS 3.9.5 (#249, PR #251).**

**Desktop app**
- **Design pass (#229, PR #232):** dark and light themes from one token file, following the system colour scheme with a manual choice under View → Theme; a status strip, cards for Performance Mode and Keyboard & Rear Light, and a two-plot Last Hour chart in Fans + Power. Decky colours come from the same tokens. `docs/screenshots/` still shows the old look.
- **Dashboard scrolls on short windows without clipping its tiles (#235, PR #242).** Checked on the screen.
- **Profile create, rename and delete through the state owner; names starting `z13h-` are refused (#227, PR #230).**
- **Battery readout: charge or drain rate and ETA to the limit or to empty (#228, PR #231).**
- **CPU Boost (EPP) per profile (#64, PRs #155, #188):** a combo in the Performance panel and the Fans window; written by the desktop user to the cpufreq policy nodes via a tmpfiles grant (`docs/adr/0003-epp-write-via-tmpfiles-grant.md`). A write that reaches no EPP node is a failure.
- **Power controls without a stored override show Not set (PR #89);** Armoury power ranges are read on the host and inside Flatpak (PR #92).
- **Telemetry history: temperatures and fan speeds recorded and graphed over time (PR #163);** labeled sensors report unavailable instead of 0 and pick the GPU by device (#25, PR #173).
- **Desktop refresh-rate choice with rollback, Game Mode display notes (#28, PR #167).**
- **Hibernation (SteamOS 3.9, preliminary): state is restored after resume from hibernate (PR #164).**
- **Memory and compute health (Refs #26, 1812326):** File → Memory and compute… on the desktop and a Memory and compute panel in Decky show Linux RAM, reserved VRAM, GTT and the ROCm/HIP and Vulkan runtimes, with an on-request HIP allocation smoke test.
- **Controls are gated by the detected platform capability (Refs #22, cf5a939):** the dashboard and the Fans window disable a control the machine does not expose and give the reason; the desktop and Decky backends make no ASUS writes on unsupported machines.

**Fans, power and resume**
- **Resume restore defers to z13ctl 1.4.2+ when its saved state matches (#80, PR #196).** Checked on the device 2026-11-16: 5/5 suspend/resume cycles on Turbo with Max Fans on.
- **Fan watchdog: the dropped-curve recovery waits 10 s for the z13ctl daemon's own re-apply and no longer fires right after resume (#66, PRs #194, #207, #190).** The daemon-absent backstop stays.
- **Restore works on z13ctl 1.4.2 (#179, PR #183):** built-in profiles use generated lowercase backend names, and undervolt writes are skipped when the daemon reports `undervolt_available: false`.
- **Fallback fan curves are anchored at 20 °C (#86, PR #208).**
- **A goal field in the profile schema (#124, PR #178);** charger-aware autoswitch logic (#24, PR #177) and the lighting signal mapping (#129, PR #171) are in as pure logic with no caller yet.
- **Profile-apply timeout messages name the failed step (#79, PR #141);** the profile enter/exit hook timeout is back to 120 s (PR #121); z13ctl write timeouts are capped at 20 s (PR #88).
- **Code audit 2026-11-04 fixes (PR #166):** a hook timeout no longer suspends Max Fans and a failed apply restores it; the Decky profile switch goes through the profile engine; wrapper TDP values are clamped and partial TDP calls rejected; the battery limit is saved through the profile; the resume script drops its fallback sysfs fan-enable write.

**Game Mode and Decky**
- **Decky repair avoids live file-watcher reload storms (#100, PR #104) and checks the Steam frontend separately (#122, PR #139).** The changed-plugin repair frontend check passed on the device 2026-11-16: loader entry HTTP 200 JavaScript, plugin registered, 99 ok, 0 failures.
- **Decky Loader minimum is 3.3.0 (PR #108);** the Decky build keeps its pnpm settings in `pnpm-workspace.yaml` for pnpm 12 (#233, PR #234); the panel title class lookup is guarded (PR #87).
- **Gamescope 48-180 Hz VRR profile matched to the panel (PR #103) and a read-only VRR verifier (#73, PR #187).** The Game Mode checks on the device move after 1.0 (#250).
- **A local stdio MCP server (#160, PR #172) and a CLI covering every app feature (#159, PR #169).**
- **Machine timeline store with pinned no-follow folders and scrubbed records (#134, PR #175).**

**Crash reporting, tests and docs**
- **Automatic crash reporting (#82, PRs #146-#148, #151):** scrubbed exception and fatal reports from the desktop app and Decky are queued for upload to the shared bug drop, under a daily limit; they sort into the drop's `z13-helper/` folder (PR #197) and survive a failed user lookup (#152, PRs #158, #176). Default on, with a toggle under Help → Error log.
- **Memory spike investigation (#30):** a bounded Decky backend RSS sampler (PR #107) and a log-only RSS guard. A 1-hour soak on the device 2026-11-16 was flat in every process; closed as not reproduced.
- **CI and test fixes:** ten tests no longer depend on the session user or machine model (#198, PR #206); ownership and temp-name tests are portable (#58, PRs #105, #149).
- **Docs:** code audit 2026-11-04 (PR #161), the docs reconciled with the tracker and the device evidence (#205, PR #212), and the 1.0 device-pass status in the ROADMAP (PR #246).

## 0.5.1 — 2026-10-30

### Fixed
- **Power ranges match the backend.** Armoury takes precedence when z13ctl can
  use it, even when legacy nodes coexist. Flatpak probes host permissions and
  per-rail range metadata; it never opens live `current_value` files. Power
  controls without a stored override show **Not set** in both frontends
  (PRs #89, #92; issue #90).
- **Fan-only profiles no longer revive stale power limits.** After selecting
  the platform profile, the engine capability-probes the daemon and clears an
  inactive default `custom` profile's old TDP before activating its curve.
  Failed cleanup blocks curve activation and leaves Max Fans enforcement off after a successful base switch
  (PR #93; issue #91).
- **Built-in curves cover idle temperatures.** Untouched Turbo and Balanced
  curves gain a 20°C anchor while retaining their mathematical response from
  40°C upward. Exact matching protects edited/custom profiles; matching enabled
  Max Fans restore snapshots migrate too. Both original files are backed up;
  read-only loads do not write (PR #95; issue #94).
- **Stale Max Fans recovery is canceled around profile switches.** The shared
  enforcement flag is suspended during apply and ON is published only after a
  successful 100% curve write. Queued and in-flight recoveries recheck intent;
  actual stall recovery remains enabled (PR #97, stacked on #93; issue #96).
- The earlier no-curve profile `NameError` and wrong-owner test fixture fixes
  are included from main (PR #83 / issue #60 and the #58 fixture repair).

### Release identity and documentation
- Desktop, Decky package and Flatpak AppStream metadata now agree on **0.5.1**.
- Current code maps, connection diagrams, component manuals and verification
  status describe the repair snapshot. Historical releases and phase plans
  retain their original dates and versions.

### Verification and limits
- Functional repair snapshot `8c857cd`: **679 pytest tests**, **80 backend
  tests**, five frontend tests, Decky build and **58 installer checks** passed
  on SteamOS 3.9.5 / kernel 7.2, GZ302EA. All five functional PR CI runs passed.
- Installed desktop-handler ON→OFF→ON checks reduced reported fan speeds from
  about 8,700–8,800 to 5,600–6,000 RPM within 15 seconds, holding through one
  minute. Two resident-app CLI Turbo→Balanced→Turbo checks produced about
  3,800–4,200 RPM in Balanced and restored full speed in Turbo, without the
  conflicting watchdog writes. Turbo reported 70/86/86 W configured limits;
  these are not workload power measurements.
- The curve change is a measured mitigation, not a proven firmware/kernel
  diagnosis. Suspend/resume, cold boot, interactive Game Mode and load/soak
  checks remain open in #86. This release does not claim 1.0 completion.

The entries below were previously under Unreleased and are included in 0.5.1.

### Added
- **`z13-gamemode-report`** (`tooling/z13_gamemode_report.py`, installed to `~/.local/bin`). Reads
  the last Game Mode session(s) back from local logs: when it ran, games played, the Decky
  backend's actions, fan-watchdog events, gamescope/Steam/z13 errors and screenshots taken.
  `--screenshot` saves a PNG of the live Game Mode screen through `gamescopectl`. Read-only apart
  from that, local only, respects the logging opt-in. Session reports checked against this Z13's
  logs; `--screenshot` on-device check pending (needs Game Mode).
- **Fan watchdog covers firmware auto mode** (2026-10-19). In auto mode (no custom curve, Max
  Fans off) the firmware once left both fans at 0 RPM while the APU climbed 69 → 75 °C. When the
  APU is at 65 °C or more and both fans read 0 RPM for 20 s, the watchdog (desktop and Decky) now
  rewrites the platform profile, re-applies the active profile's TDP (the rewrite drops it) and
  resets the curve (`tdp --set` re-applies z13ctl's stored one). Profile hooks are not re-run, so
  Boondock's restore snapshot survives. At most once every 2 min. On-device check pending.
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
- **SteamOS updater tool names:** check for `holo-update` first, falling back to
  `steamos-update` on older installs. Both update checks and installs use the
  same shared helper; Flatpak installer runs carry its matching source to the
  host. Hardware setup notes show both readonly-tool names. Updater execution on-device check pending.
- **One host-command seam** (`src/host.py`) for the desktop app, the `z13-profile` CLI and the
  Decky plugin. It replaces five hand-rolled runners and three `_in_flatpak()` copies, and gives
  every Decky child the deck env (incl. the `LD_LIBRARY_PATH` fix) in one place. `install.sh`
  installs it to `~/.local/lib/z13-helper/` (for `z13-profile`) and into Decky's `py_modules/`;
  run `./install.sh --repair` after updating. Host profile, installed desktop and repair paths
  checked on 2026-10-30; interactive Game Mode and resume checks remain pending.

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
- **The installer no longer runs user-writable code as root.** The `z13ctl` binary and the Decky installer script are copied to a root-owned `/etc/z13-helper/`, checked against the hash the release publishes (never a hash of the copied file), and only the root-owned copy is run or extracted with sudo.
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
