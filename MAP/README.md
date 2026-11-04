# Code map

This map describes the functional source tree on `main` at `e575835` (2026-11-02): the 0.5.1 release, the fan and power repair PRs (#89, #92, #93, #95, #97) and the Decky, installer and profile changes after them are merged. Open work is tracked in `PIPELINE.md` and the issues. Device verification remains separate from automated tests; the [release record](../docs/releases/0.5.1.md) records which Desktop checks have passed and which Game Mode, resume and cold-boot checks remain pending. The Decky flow has its own map in [`decky.md`](decky.md).

## System connections

```mermaid
flowchart TD
    Main[main.py] --> Dash[src/dashboard_ui.py]
    Dash --> PC[src/profile_control.py]
    Dash --> FW[src/fans_window.py]
    PC --> Worker[src/apply_worker.py]
    PC --> Prof[src/profiles.py]
    PC --> HC[src/hardware_controller.py]
    Prof --> Authority[src/state_authority.py]
    HC --> Authority
    Authority --> Host[src/host.py]
    Authority --> Socket[src/z13ctl_client.py]
    Socket --> CLI
    Host -->|Flatpak: flatpak-spawn --host| CLI[z13ctl CLI / daemon]
    Prof -->|z13-profile host CLI| CLI
    PC -->|reads/writes| ProfileStore[(profiles.json)]
    PC -->|reads/writes| MaxStore[(max-fans.json)]
    Watch[src/fan_watchdog.py] -->|reads sensors and shared intent| MaxStore
    Watch -->|recovery transaction| Authority
    Sensors[src/sensor_reader.py] -->|hwmon reads only| HW[Kernel hwmon]
    PowerRange[src/power_range.py] -->|range metadata only| Armoury[Armoury attributes]
    PowerRange -->|Flatpak metadata probe via host.py| Host

    DeckyUI[decky/src React panel] --> Callable[decky/src/backend.ts]
    Callable --> Backend[decky/main.py backend, runs as deck]
    Backend -->|profile apply as deck| Prof
    Backend --> Authority
    Backend -->|reads/writes| ProfileStore
    Backend -->|reads/writes| MaxStore
    Install[install.sh / tooling/install_check.py] --> Copies[host CLI and Decky py_modules copies]
```

The desktop app, tray, host profile CLI, Decky plugin and fan recovery all route writes
through `src/state_authority.py`. It holds a shared hardware transaction lock across
profile applies/recoveries and validates queued field edits against fresh backend
selection. `z13ctl` owns named performance settings; `profiles.json` mirrors that state
and retains app-only hooks, base, lighting and EPP intent. `max-fans.json` retains the
restore snapshot and confirmed intent. No second app daemon is introduced.

A host `z13-profile watch` subscribes before its first snapshot and resnapshots after
reconnect, reconciling backend selection/settings without a hardware write. The shared
`state_stream.py` reader delivers those records to the desktop Qt signal and Decky
`profile_state` event. Both frontends refresh from those events; the desktop profile
poll has been removed. Telemetry, fan safety checks and screen brightness keep their
independent sampling. Older daemons retain the bounded legacy command path; they
cannot provide named-profile identity or field targeting.

`src/host.py` selects its execution mode once per process. Flatpak calls use `flatpak-spawn --host`; the host `z13-profile` CLI runs directly as the invoking user; Decky runs as the `deck` user with that session's runtime environment. `runuser -u deck` is used only for a root process that explicitly requests `as_deck=True`, not for every Decky command. The Decky plugin includes root-owned copies of shared Python helpers under `py_modules/`; the backend itself runs as `deck`.

## Desktop app and shared engine

| Module | Responsibility and connections |
|---|---|
| `main.py` | Process entry point, logging, single-instance guard and Qt application. |
| `src/dashboard_ui.py` | Main window, profile picker, Max Fans control, telemetry and tray behavior; opens Fans + Power. |
| `src/fans_window.py`, `src/fan_curve_widget.py` | TDP/PL1-PL3 and Curve Optimizer controls, fan curve editor, and range-aware power sliders. |
| `src/profile_control.py` | Desktop adapter: profile editor, worker submissions, and Qt event dispatch for both windows and tray; delegates hardware edits to the shared authority. |
| `src/state_authority.py` | Shared transaction/write owner: named API probe, explicit thermal targets, field edits, Max Fans, platform-profile switch, lighting, sysfs screen-backlight write (`set_screen_backlight`) and named fan recovery. Edits to an inactive profile are stored and mirrored without a live push; `apply_profile` runs the GUI switch/re-apply as one transaction. Never retries failed socket writes through the CLI. |
| `src/z13ctl_client.py` | Stdlib newline-JSON socket client, Flatpak/root host bridge, named-profile mirroring and cancellable subscription/reconnect loop. |
| `src/state_events.py` | PyQt-free event action/status table and callback dispatch; used by desktop signals and Decky event forwarding. |
| `src/z13ctl_profiles.py` | Pure built-in named-profile mapping and strict socket JSON capability decision (`profile-list` well-formed rows only); never treats firmware listing text as target support. Proven: the daemon answers `profile-list` with well-formed rows. Not proven: that it honours `request.profile` (inferred from the two shipping together upstream; unverifiable without the device, see issue #23). |
| `src/state_stream.py` | Shared cancellable host `z13-profile watch` reader; owns child termination/reaping and forwards records to frontend callbacks. |
| `src/apply_worker.py` | Serial background queue for desktop hardware work, including profile applies and watchdog recovery. |
| `src/profiles.py` | Profile schema and JSON store, built-ins and migrations, `z13-profile` CLI, ordered profile apply, and lightbar recovery. The legacy TDP cleanup before a curve write runs only after the socket JSON profile-list probe succeeds, never on CLI listing headers. Each profile may also carry per-device lighting intent (`LIGHTING_MODES`, `LIGHTING_COLORS`, `set_profile_lighting`), and enter/exit hooks are bounded by `HOOK_TIMEOUT_S` (120 s). Used by the app and installed host CLI; Decky invokes that same CLI for app-profile changes. |
| `src/max_fans_state.py` | Shared Max Fans intent and saved-curve snapshot in `max-fans.json`, with inter-process locking and atomic writes. |
| `src/fan_watchdog.py` | Shared Desktop/Game Mode recovery rules. In the desktop app, recoveries use the worker queue; stale dropped-curve recoveries are canceled if the queued profile operation has already changed Max Fans intent or restored a curve. |
| `src/sensor_reader.py` | Read-only hwmon temperatures, fan RPM, custom-curve mode and points. It does not write hardware. |
| `src/power_range.py` | Determines per-rail slider bounds from kernel min/max metadata and z13ctl's backend selection rules. It never reads a live PPT `current_value`. |
| `src/hardware_controller.py` | z13ctl command wrappers for profile, fan, power, battery, lighting and Curve Optimizer operations. Every write is bounded by `host.Z13CTL_WRITE_TIMEOUT_S` (20 s); a timeout is reported, not retried. |
| `src/host.py` | Shared command seam for Flatpak, host, and Decky contexts; owns `Z13CTL_WRITE_TIMEOUT_S`. |
| `src/config_manager.py`, `src/install_record.py`, `src/single_instance.py`, `src/theme.py` | App preferences, install metadata/logging choice, one-process coordination and styling (including the performance-mode tile icons). |
| `src/app_log.py`, `src/log_dialog.py` | Rotating app log, crash and fatal reports and the fan-trace files under `~/.local/state/z13-helper/`; the Help -> Error log window that shows them. The Decky backend writes its fan trace through the same `FanTrace`. |
| `src/crash_report.py`, `src/crash_scrub.py`, `src/crash_queue.py` | Shared automatic crash reporting: startup hooks, scrubbed ZIPs, bounded offline queue and daily upload limit. Build configuration and the default-on Error log toggle are described in [`docs/crash-reporting.md`](../docs/crash-reporting.md); live delivery verification remains pending (#82). |
| `src/versions.py` | Shared version floor (`DECKY_MIN`, 3.3.0) and `older_than`; used by `installers.py` and `update_watch.py`. |
| `src/update_watch.py`, `src/updates_dialog.py`, `src/installers.py` | Upstream checks, updates/repair UI and installation actions. |
| `src/screen_brightness.py`, `src/evdev_listener.py` | Built-in display brightness (reads/KDE calls local; the sysfs backlight write goes through `state_authority.set_screen_backlight`) and the Desktop Mode side-button reader. |

### Profile apply and Max Fans ordering

Profile state has two related but distinct pieces: `profiles.json` describes each profile's requested curve, while `max-fans.json` records the current Max Fans toggle and the curve to restore when it is turned off. When an apply begins with Max Fans enabled, it temporarily publishes the shared flag as off so the watchdog cannot pin the old 100% curve over the transition. It then writes the base platform profile, applies power settings, and writes the selected curve (or the 100% curve). For a Max Fans target, the flag becomes on only after the 100% curve command succeeds. If the base profile changed but power cleanup or curve application fails, the flag remains off; if the base command itself fails, the prior enabled intent is preserved. The shared recovery code also rechecks intent before reapplying a dropped curve and before the final 100% write.

The built-in Balanced and Turbo curve migration only changes a profile when name, built-in marker, base profile and full old curve all match. An edited or custom profile is preserved. If Max Fans is enabled and its saved raw-PWM snapshot exactly matches the active built-in's old curve, the matching snapshot is migrated too; unrelated, absent, disabled or `null` snapshots remain as they were. Curve points from 40 °C upward preserve the old interpolation; a duplicate low-temperature anchor defines the first plateau below 40 °C. This is a mitigation for the observed low-temperature behavior, not a proven firmware root cause.

## Power-range selection and safety

`power_range.detect_ranges()` mirrors z13ctl 1.4.2 backend selection: Armoury is selected when PL1's `current_value` node exists, is writable from the host, and has sane positive min/max metadata. It reads only `min_value` and `max_value` for each available rail (`ppt_pl1_spl`, `ppt_pl2_sppt`, `ppt_pl3_fppt`); it never reads the value stored in `current_value`. If called inside Flatpak, a stdlib-only probe runs through `host.run()` on the host because sandbox permission checks on sysfs can falsely report that Armoury is not writable. Per-rail missing or malformed metadata uses the broad 5–93 W UI fallback because the slider API requires a bound for every rail. The legacy `asus-nb-wmi` path uses that same 5–93 W bound.

The desktop Fans + Power window and Decky `power_state()` display stored profile overrides clamped for the selected kernel range without rewriting the saved profile. A profile value outside that range produces a note; moving a slider saves the shown values. With no profile override, frontends show an explicit unset/default state rather than implying that fallback slider numbers are active hardware limits. z13ctl remains the hardware writer. Avoid polling Armoury `current_value` for telemetry or UI display: the EC access can hang on affected resume paths.

## Decky/Game Mode

`decky/main.py` exposes callable methods to the QAM panel and imports shared `fan_watchdog.py`, `sensor_reader.py`, `host.py` and `power_range.py` from the plugin's installed `py_modules/` copies. It serializes its writes with an asyncio lock and runs blocking host work in threads. In Game Mode it runs the shared fan watchdog; in Desktop Mode it stands down while the resident PyQt app owns that watchdog. App-profile selection invokes the installed `z13-profile` CLI, which is the same engine used by the desktop app. Direct performance-profile, TDP, charge-limit, brightness and telemetry calls remain separate Decky API methods. See [`decky.md`](decky.md) for the flow and persisted state details.

## Planned (after 1.0)

Modules planned by Phases 15 and 16 (`docs/ROADMAP.md` §10a, §10b). Each moves from planned to present in the PR that builds it. None writes hardware except through `z13ctl` (via `hardware_controller.py`).

| Module | Status | Responsibility and connections | Issue |
|---|---|---|---|
| `src/goal_control.py` | planned | Pure goal decision: goal + hwmon/fps readings + current setpoints → next TDP and curve, bounded by `power_range.py`. No I/O. | #123 |
| `src/profiles.py` (`goal` field) | present | Optional per-profile `goal` (`quiet`/`comfort`/`fps`/`max` + target; `validate_goal`, `goal_kind`); written only when set. `max_fans` migrates to goal `max` on load and stays as the back-compat mirror; until `goal_runner.py` exists every goal applies its fixed values. | #124 |
| `src/goal_runner.py` | planned | The one curve/TDP writer while a goal is active; reads `sensor_reader.py` and `fps_source.py`, writes through `apply_worker.py` (desktop) or `decky/main.py` (Game Mode), coordinated by the #23 state owner; takes over `fan_watchdog.py`'s curve-drop recovery. | #125 |
| `src/fps_source.py` | planned | Read-only frame-rate reading in Game Mode (source chosen by the probe). | #126 |
| `src/hw_signals.py` | planned | Event → lightbar pattern mapping with priority, rate limit and restore; played through `hardware_controller.py`. | #129, #130 |
| `src/evdev_listener.py` (gestures) | planned | Hold or double-press on the side button cycles profiles/goals. | #131 |
| shared touch UI | planned | Desktop window and QAM from one set of parts; shape decided in `docs/adr/0002-shared-touch-ui.md`. | #132, #133 |
| `src/timeline.py` | planned | Append-only `~/.local/state/z13-helper/timeline.jsonl` of hardware and host changes with Phase 12c rollback records; written by `profiles.py`, `goal_runner.py`, `install.sh`. | #134 |
| timeline view | planned | Compare and roll back, beside `updates_dialog.py` and in the QAM. | #135 |

```mermaid
flowchart LR
    Sensors[sensor_reader.py] --> Goal[goal_control.py]
    FPS[fps_source.py] --> Goal
    Goal --> Runner[goal_runner.py]
    Owner["#23 state owner"] --> Runner
    Runner --> HC[hardware_controller.py] --> CLI[z13ctl]
    Events[app events] --> Sig[hw_signals.py] --> HC
    Button[evdev_listener.py gestures] --> Runner
    Runner --> TL[(timeline.jsonl)]
    Prof[profiles.py] --> TL
```

## Host installation, tests and references

| Path | Responsibility |
|---|---|
| `install.sh`, `tooling/install_check.py` | Manifest-driven host install/repair and verification. The install copies `src/profiles.py` as `z13-profile`, `src/host.py` beside it, and shared helpers into Decky's `py_modules/`. |
| `packaging/host/` | Reference systemd, polkit and udev files; the z13ctl setup owns its permissions unit and udev rule. The fan-resume and lightbar-reset units each run a small `.sh` beside them. `gamescope/displays/asus.rogflowz13.lcd.lua` is the Gamescope display script for the panel's 48-180 Hz VRR range, installed by `tooling/install_check.py`'s manifest (device acceptance pending). |
| `tooling/profiles/`, `tooling/boondock` | Compatibility wrappers and Boondock's hardware/session commands. Built-in profile hooks use its session-only commands. |
| `tooling/diag/`, `tooling/z13_gamemode_report.py`, `tooling/z13_fanlog.sh` | Read-only or explicitly invoked diagnostics, Game Mode reporting and the fan/thermal logger. |
| `tooling/z13_cdp.py`, `tooling/z13_qam.py`, `tooling/spike1_qam_test.py` | Dependency-free CDP bridge to Steam's Game Mode UI (opens the side menus without input emulation) used by `z13-button-watcher`; `z13_qam.py` is its back-compat alias and `spike1_qam_test.py` is the by-hand Game Mode check. |
| `tooling/rss_watch.py`, `tooling/screenshots.py` | Bounded RSS sampler for one identified process (used on the Decky backend); renders the README screenshots offscreen from the real app code inside the Flatpak. |
| `tooling/post_update/recipe.py` | Post-update recipe format: ordering (keyring first, reboot last), keyring insertion and plan text. Pure functions; never runs a recipe's `check` or `apply`. |
| `tooling/post_update/builtin.py` | Built-in recipes 1-3 (amdgpu args in `grub-steamos` + `update-grub`, pacman keyring, carry-forward list health). Reads and runs only through the injected `HostIO`; checks are `na` off SteamOS on a GZ302 and applies refuse there. |
| `tooling/post_update/builtin_4_6.py` | Built-in recipes 4 and 6 (SteamOS `/etc` snapshot copied to `~/.local/share/z13-helper/etc-backups`, ROCm pack). Host access only through the injected `Sys`. |
| `tooling/post_update/local.py` | Local recipe loader: `parse_local` turns one TOML recipe into a plain dict (checked fields, ids and flags); `load_dir` reads every `*.toml` in a folder, in file-name order, and returns `(recipes, errors)` so a bad or duplicate file is an error row and the rest still load. Pure reading; it never runs a recipe's `check` or `apply`. |
| `tests/`, `decky/tests/` | Pytest logic tests, Decky `unittest` tests, and the separate Node test for `profileSwitch.ts`. `tests/conftest.py` blocks subprocess and network calls and `tests/fake_host.py` fakes `host.run`; the per-module test files are listed in `tests/README.md`. |
| `.github/workflows/tests.yml` | CI on every push and PR: pytest and the Decky unittest suite on Linux; it does not run the Node test. |
| `decky/rollup.config.js`, `decky/src/` | Decky bundle build; `index.tsx`, `hooks.ts` and `components/` (`PowerLimits`, `BatteryLimit`, `KeyboardBrightness`, `Telemetry`) are the QAM panel, mapped in [`decky.md`](decky.md). |
| `flatpak/` | Flatpak manifest (`io.github.iannitram.Z13Helper.yml`), `build.sh`, the in-sandbox `launch.sh`, desktop file and AppStream metadata. |

`z13ctl` is the sole hardware command backend. Decky Loader provides the Game Mode QAM surface; G-Helper informs the desktop layout and interaction model. The device verification checklist is in [`docs/ROADMAP.md`](../docs/ROADMAP.md); automated tests do not replace pending on-device checks.

Decky's file watcher must be stopped before changing installed plugin files: otherwise every
individual copy can trigger a reload against a partial bundle (#100). `install.sh` quiesces the loader, stages and byte-verifies the files, then restarts it (`quiesce_loader`, `start_loader`, `resume_loader`). It leaves an
unchanged plugin running and starts the loader after changed files are installed. It also enforces the Decky Loader minimum from `src/versions.py`. Its backend
startup log alone is not frontend evidence. After a changed-plugin install/repair (#122),
`decky_frontend_check` runs `tooling/z13_cdp.py --decky-frontend-check --since <repair timestamp>`:
the loader's `/plugins/Z13-Helper/dist/index.js` must serve HTTP 200 JavaScript (the ES-module
route is auth-exempt), and Steam's `SharedJSContext` must have the real plugin registered in
`window.DeckyPluginLoader.plugins`, with no pending reload or new buffered CEF dynamic-import
errors since before quiescence. Entry/registration errors fail the installer separately from
backend startup; absent Steam UI/debug access reports **not checked** with a reason. The probe
does not import or reload the plugin. An unchanged repair skips it; a dry run reports not checked.
The route and registry assumptions follow Decky's upstream
[`loader.py`](https://github.com/SteamDeckHomebrew/decky-loader/blob/main/backend/decky_loader/loader.py)
and [`plugin-loader.tsx`](https://github.com/SteamDeckHomebrew/decky-loader/blob/main/frontend/src/plugin-loader.tsx).
The route, registry/export shape and buffered error replay still need confirmation on the device,
including one changed-plugin repair passing with Steam's gamepad UI running.

The named-profile capability contract is the JSON value returned by socket
`profile-list`; a human-readable `profile --list` firmware line is insufficient.
Upstream added `profile-list` and `request.profile` together (see
[api/client.go](https://github.com/dahui/z13ctl/blob/main/api/client.go) and
[daemon/server.go](https://github.com/dahui/z13ctl/blob/main/internal/daemon/server.go)).
Malformed successful replies fail closed; only missing/old daemons select legacy
behavior. Custom names use stable hashed identifiers; built-ins reuse
`z13ctl_profiles.named_profile_for`. No speculative write is used as a probe.

`host.open_stream` preserves the same host environment as `host.run`; callers own
the process lifecycle. Installer manifests deploy every authority/client/stream
and controller dependency for the host CLI and root-owned Decky copies. Regression
coverage includes `tests/test_state_authority.py`, `tests/test_state_stream.py`,
`tests/test_z13ctl_client.py` and PyQt-free dispatch tests. Live firmware behavior,
mode switches and subsecond convergence remain device acceptance checks.

Profiles without thermal overrides are mirrored as empty backend definitions and
select their firmware base, because z13ctl refuses to activate an empty custom
profile. Reconciliation retains the app alias; selecting it by app name uses
`z13-profile apply`. Snapshots also read the cached platform-profile metadata once
per event, since the daemon custom-profile name does not encode that base.
