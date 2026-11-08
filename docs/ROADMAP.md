# Z13-Helper — Roadmap

**Rewritten 2026-10-09.** This is the single plan for the project. It replaces the per-phase files
in `docs/plan/` (kept as history, see [Doc map](#doc-map)). Read `CLAUDE.md` first for orientation;
the maintainer's notes (outside the repo) stay the reference for *how the machine works*; this file is
*what we are building next and why*.

Status marks: ✅ shipped · 🔧 in progress · ⏳ planned · 🚧 gated (needs a decision or a precondition) · 💤 backlog

**1.0 scope (2026-10-26, [SPEC.md](../SPEC.md)):** Phases 7-13 below, every "on-device check pending" item verified on the Z13, the open bugs, plus issues #23 (one state owner, events) and #27 (reversible post-update repair). Since 2026-10-26 the checks run on SteamOS 3.9.5, so Phase 12d (§9c, #74-#80) is in 1.0 too. Out of 1.0: Phase 14, the Laptop Screen row of Phase 9 (#28) and Steam's own QAM controls in Phase 13 (#29). Code map: [`MAP/README.md`](../MAP/README.md).

---

## 1. Where we are (reconciled 2026-11-07, `main` 84f0b1e)

Release **0.5.1** remains the latest tagged repair release; the current `main` includes substantial
unreleased implementation work since that snapshot. The 1.0 spec is **not complete**: code and CI
coverage do not substitute for the Z13 acceptance checks below. No pull requests are currently open.
The [code map](../MAP/README.md) describes the current implementation; the [Nov 7 audit
reconciliation](audit/2026-11-07-plan-reconciliation.md) records what changed since the Nov 4 audit.

### Implemented on current `main`

| Area | Current state |
|---|---|
| Fan/power repair release | ✅ 0.5.1 repairs are integrated; the [release record](releases/0.5.1.md) is historical evidence for that snapshot, not a full 1.0 sign-off. |
| Shared state owner and event refresh (#23) | ✅ `state_authority.py` serializes profile/hardware writes across the desktop app, CLI, Decky and recovery; `state_stream.py` refreshes both frontends. Issue #23 is closed. Live daemon and frontend convergence still need device verification. |
| Post-update checks and reversible repair (#27) | ✅ Desktop and Decky surfaces, inventory, repair planning, rollback records and support-bundle preview are implemented. Issue #27 is closed. A real update/repair/rollback cycle remains to be verified on-device. |
| CPU Boost / EPP (#64) | ✅ Control and profile persistence are implemented; on-device write/read verification remains. |
| Game Mode VRR (#73) | ✅ Panel-specific script and read-only preflight are implemented; actual Game Mode range and panel acceptance remain unverified. |
| Resume ownership (#80) | ✅ Resume hook defers to z13ctl 1.4.2+ only when a file-only ownership check proves the saved profile matches; uncertain cases keep the full restore. Five-cycle device check remains open. |
| Fan watchdog curve-drop (#66) | 🔧 Recovery now waits 10 seconds for the daemon and avoids immediate post-resume recovery. Retirement remains gated on sustained device evidence. |
| Decky install/frontend (#100, #122) | ✅ Staged plugin replacement and separate frontend checks are implemented. One changed-plugin repair with Steam's gamepad UI is still pending device verification. |

### Remaining 1.0 work

- **Device acceptance:** #61 is open for the remaining cross-mode checks; #74 and #75 cover SteamOS 3.9.5 hardware and session behavior. Include cold boot, suspend/resume, fan response after settling, Decky interaction with a running game, installer/repair UI, lightbar recovery, EPP, VRR and a real post-update repair/rollback. Record each result or file a focused issue.
- **Fan/kernel investigation:** #86 remains open. The shipped fan/power changes are mitigations; firmware/kernel causality and cold-boot/resume/Game Mode response are not established.
- **Memory:** #30 remains open until the 24-hour current-identity RSS run is interpreted. Source bounds and safeguards do not answer whether the original report was this plugin.
- **Watchdog retirement:** #66 remains open until daemon-only curve restoration and the resume matrix prove that the curve-drop branch can be removed safely.
- **VRR:** #73 remains open until the on-device 48, 60, 120 and 180 Hz and frame-limiter matrix is recorded.
- **Phase 7 cleanup:** inspect the current Boondock script before changing this item. The script no longer contains Docker/VM steps and does not read live PPT values; its remaining value, legacy power path and user-service behavior still need review and current-device verification.

Issues #23 and #27 are closed because their code is integrated; that does not close their real-device acceptance. #58, #60, #64, #76-#79, #82, #100, #122 and #179 are closed. The live tracker is authoritative for issue state; the status above separates merged implementation from outstanding evidence.

### Shipped and working
| Area | State |
|---|---|
| Desktop app (PyQt6 Flatpak `io.github.iannitram.Z13Helper`) | ✅ profiles, TDP, 8-point curve (spinboxes), battery limit, brightness, telemetry, tray, single-instance, resident service |
| Side button | ✅ Desktop: host evdev reader → window toggle. Game Mode: `z13-button-watcher` → QAM / Steam menu |
| Max Fans | ✅ shared `max-fans.json`, watchdog in app + Decky, re-pinned at login/resume |
| Decky plugin `z13-helper` | ✅ profile dropdown, Max Fans, TDP sliders, charge limit, per-device brightness, telemetry (§7; on-device check pending) |
| Host profiles | ✅ profiles engine (`z13-profile`, §4); `z13-restore-profile.service` at login runs `z13-profile restore` |
| Installer | ✅ `install.sh` / `--repair` + `install_check.py verify` (§9a); Repair button in the Updates panel (on-device check pending) |
| Lightbar wedge | ✅ auto-recovery via `z13-lightbar-reset.service` + polkit rule (§3; on-device check pending) |
| App version | **0.5.1** (2026-10-30; `CHANGELOG.md`, desktop/Decky/AppStream aligned) |
| Boondock | ✅ Docker/VM paths removed; hooks use the user `llama-swap` unit and never touch PPT nodes. Standalone 20/30/40 W defaults and current-device behavior still need review. |

### Issue status snapshot (2026-11-07)
- **Closed:** #16, #58, #60, #64, #76-#79, #82, #100, #122 and #179. Their current code does not itself establish any outstanding device acceptance.
- **Open:** #30 (24-hour RSS evidence), #61 (device-check tracker), #66 (watchdog retirement evidence), #73 (VRR acceptance), #74-#75 (SteamOS 3.9.5 device passes), #80 (resume-cycle evidence) and #86 (fan/power regression and causality).
- **#86**'s issue report and the 0.5.1 record document observed behavior; the shipped fixes are not proof of a general firmware/kernel cause or full mode/resume coverage.
- `tooling/z13_gamemode_report.py` is already part of the repo and reads prior Game Mode sessions from local logs.

### Fixed today (2026-10-09)
- **Keyboard brightness didn't change.** z13ctl sends each packet to the keyboard *then* the
  lightbar and aborts on the first write error; the lightbar's USB endpoint (`0b05:18c6`, hidraw5)
  had stalled (`ETIMEDOUT`), so the keyboard never got its packet. Fix: app and `z13-*` scripts now
  set brightness **per device**; the keyboard works even when the lightbar is wedged. Recovery for a
  wedged lightbar: `echo 0 > /sys/bus/usb/devices/3-5/authorized; echo 1 > …/authorized`.
- **Max Fans ON, fans at 0 RPM.** After a Desktop↔Game Mode switch the kernel still reported the
  100 % curve as *enabled*, but the firmware had stopped honouring it. Re-writing or resetting the
  curve did nothing; **re-writing the platform profile (same value) then the curve** brought the fans
  back. The watchdog (`src/fan_watchdog.py`, shared by the app and Decky since 2026-10-15) now also uses **RPM truth**: if the
  curve asks for ≥ 50 % and both fans read 0 RPM for two ticks, it rewrites profile + curve.
- **Dashboard clipped** (System Status / Boondock text cut off) → the tab scrolls.
- **Second stall (20:35):** no profile write involved — the z13ctl daemon re-applied the curve twice
  (bare writes, after a firmware-side fan release) and the fans sat at 0 RPM until our watchdog's
  profile-rewrite recovery. Both watchdog branches (curve dropped / fans stalled) now recover with
  **profile rewrite + curve**; a bare curve write is never used for recovery. Verified: an external
  same-value `platform_profile` write is absorbed by the 1.4.2 daemon with no RPM dip.
- **The tach can lie (21:00, observed on the device):** the *left* fan physically stopped while `fan1_input`
  read 8 700 on every sample and the other fan's reading kept jittering; toggling Max Fans (a curve
  change → firmware re-ramp) brought it back. So "RPM > 0" is not proof. Third watchdog rule: **one
  fan's reading bit-identical for 90 s** while the curve asks ≥ 50 % → stale tach → profile rewrite +
  curve. Also learned: every curve *change* makes the firmware stop the fans for ~5–10 s and re-ramp
  (that's the normal dip after a toggle), and below the curve's first point the fans idle (hence Max
  Fans now starts at 20 °C).
- **Decky watchdog never worked in Game Mode:** the loader is a PyInstaller bundle whose
  `LD_LIBRARY_PATH` broke every `systemctl` call (`OPENSSL_3.4.0 not found`), so `_in_gamemode()` was
  always false. The backend now restores the host library path for child processes.

### Audit findings (docs)
| Doc | Verdict |
|---|---|
| `docs/plan/Phase_1…6.md` | Historical. Phases 1–3, 5 (spike), 6 are done; Phase 4 (testing) was never run as written; Phase 5 "full parity" never happened. **Superseded by this file.** |
| `docs/README-landing.md` | Stale vault landing page (code at `C:/dev`, keycode 579, InputPlumber D-Bus as the button path). **Deleted**; its history table lives in this section. |
| `README.md` | Refreshed 2026-10-09 for the redesign (features, layout, restore path). |
| `docs/BUTTON-AND-INPUTPLUMBER.md`, `docs/hardware-enablement.md` | Accurate; keep. |
| Maintainer's notes (outside the repo) | Authoritative for machine behaviour; need a "profiles engine" and "fan stall" section once Phase 8/7 land. |

### Upstream drift (why the plan changed)
*(Written 2026-10-09 before the upgrade; 1.4.2 is installed since then, see §8.)* We pinned **z13ctl 1.1.6** (2026-04). Upstream is at **1.4.2** (2026-10-09) and has since added the
things we were about to build ourselves: **named custom profiles** (fan curve + TDP + Curve Optimizer),
**AC/battery autoswitch**, **`subscribe` events** instead of polling, re-apply on resume, and a run of
**suspend/resume hard-lock fixes** (1.3.2 → 1.4.1). It also fixed two bugs we live with: custom curves
silently dropped by the kernel (1.2.2) and a custom TDP ≤ 75 W being silently undone (1.4.0 — this
likely means Boondock's 20 W cap only holds while a custom curve is active). See §8 and §11.

---

## 2. Principles (don't break these)
1. **`z13ctl` is the only thing that writes hardware.** The app, the Decky plugin, the host scripts and
   the hooks all go through it. No direct sysfs writes from the UI.
2. **InputPlumber stays masked.** It is load-bearing for game controllers (`docs/BUTTON-AND-INPUTPLUMBER.md`).
3. **One state, two frontends.** Desktop app and Decky share files under `~/.config/z13-helper/`
   (`config.json`, `max-fans.json`, and now `profiles.json`). Shared saved state is re-read;
   widgets retain pending edits and the desktop power window may cache ranges for its lifetime.
   z13ctl's own named-profile state is still separate; the single-owner/event goal remains #23.
4. **Order of writes:** platform profile → TDP → fan curve → undervolt → lighting → battery.
   A platform-profile write drops the custom curve and lets the firmware re-apply its own power limits,
   so it always goes first. (`src/profiles.py`) Max Fans enforcement is held off before these writes,
   then enabled only after the intended full-speed curve succeeds. Fan-only applies probe backend
   support and clear stale default-`custom` TDP before curve activation; unsafe cleanup stops that step.
5. **Never poll the asus-armoury `ppt_*` files.** Reading them reaches the EC and can hard-lock a machine
   whose EC is slow after resume (z13ctl 1.4.1 notes). ENODEV was observed on the old 6.18 kernel.
   Range metadata (`min_value`/`max_value`) and host permission checks are permitted; no live
   `current_value` is opened. Flatpak runs the probe on the host, since sandbox `/sys` is read-only.
6. **Max Fans wins.** While ON, nothing else writes a different curve; profiles store their curve as the
   one to restore when it's switched OFF. During an apply the flag represents enforcement/applied
   status, so it is temporarily off. Queued recovery rechecks live intent and curve mode; it must not
   resurrect an obsolete Max Fans request. These guards are not a cross-process transaction lock.
7. **Host changes are deliberate.** Anything under `/etc`, the z13ctl binary, or systemd units gets a
   backup, a journal line, and a wiki note.

---

## 3. Phase 7 — Stability ✅ (Boondock device/path review remains ⏳)
Shipped today: per-device brightness, fan-stall watchdog, dashboard scroll (above).

Remaining:
- ✅ **Resume hook vs. profiles** (2026-10-09). `/etc/systemd/system/z13-fan-resume.sh` now waits 3 s for
  the EC, then runs `z13-profile restore` as deck (active profile: platform profile, TDP, curve, Max Fans
  pinned if ON) and falls back to the old curve logic. Backup: `z13-fan-resume.sh.bak-20261009`.
  Repo copy: `packaging/host/systemd/system/z13-fan-resume.sh`.
- ✅ **Boondock cleanup, source state checked 2026-11-07.** `tooling/boondock` has no Docker/VM steps,
  uses the user `llama-swap` unit, and its session hooks do not write power hardware. Standalone
  hardware commands remain a legacy path with 20/30/40 W defaults and need range/current-profile
  review plus device verification before this phase is fully accepted.
- ✅ written, on-device check pending (2026-10-12) **Lightbar wedge auto-recovery.** Root oneshot
  `z13-lightbar-reset.service` → `/etc/systemd/system/z13-lightbar-reset.sh` finds the USB device by
  ID `0b05:18c6` (never by port) and writes `authorized` 0 → 1; polkit rule
  `/etc/polkit-1/rules.d/50-z13-lightbar-reset.rules` lets deck **start** that one unit, nothing else.
  `profiles.recover_lightbar()` (used by `profiles.apply`, the desktop brightness buttons and — as a
  copy — the Decky backend): only when a lightbar write fails with a *timeout*, keyboard already
  written, at most one reset per 120 s (stamp in `$XDG_RUNTIME_DIR`), wait 3 s, retry that one write
  once. Installed by `install.sh` (asks first; in MANIFEST + keep-list); `verify` only runs
  `systemctl cat` on it. Manual: `systemctl start z13-lightbar-reset.service`;
  log: `journalctl -t z13-lightbar-reset`.
- ✅ **Lightbar auto-recovery, as-built (found on-device 2026-10-11).** The reset above worked but the
  retry still hit `write /dev/hidrawN: no such device`: the lightbar re-enumerates under *new* hidraw
  nodes, and the socket-activated `z13ctl.service` user daemon caches the old path. Fix: after the
  reset unit + its settle wait, `recover_lightbar()` now also runs `systemctl --user restart
  z13ctl.service` (the daemon re-applies its own saved state on the way back up) before the one retry;
  a lightbar write that fails with ENODEV *without* a prior reset (a stale handle on its own) gets the
  daemon restart alone, via `recover_lightbar_stale()` — `lightbar_failure_kind()` tells the two apart.
  Same logic in `HardwareController.set_brightness` and the Decky backend (as deck, with
  `DBUS_SESSION_BUS_ADDRESS` set explicitly since the backend runs as root).

---

## 4. Phase 8 — Profiles engine ✅ (z13ctl state mirror and event path implemented; device check pending)
**Replaces the Boondock button with a Profile dropdown** (2026-10-09). A profile bundles what
G-Helper calls a "mode": base platform profile + TDP + fan curve + undervolt + brightness + battery
limit + an enter/exit hook. Boondock becomes a profile whose hooks run the script for the
non-hardware effects.

- **Module:** `src/profiles.py` ✅ written — stdlib-only, importable in the Flatpak, runnable on the host
  as the `z13-profile` CLI, shell-able from the Decky backend. Store: `~/.config/z13-helper/profiles.json`.
- **Built-ins** (G-Helper names; mirror the host scripts so nothing regresses): Silent (= `z13-cool`), Balanced
  (= `z13-balanced`, longevity curve), Turbo (= `z13-max`, aggressive curve), Boondock (quiet,
  20/30/40 W, hooks `boondock on|off`). Built-ins can be edited (curve/TDP/…) but not deleted.
- **Apply engine** = principle 4; each step independent; results surface per step in the status bar.
  Max Fans ON → the profile's curve is saved as `saved_curve`, 100 % stays pinned.
- **Migration:** first run seeds the store and takes the active name from
  `~/.config/z13-profiles/current-profile`; `set_active` keeps that legacy file in sync so the old
  restore path still works until it is replaced.
- ✅ **Host wiring:** `src/profiles.py` installed as `~/.local/bin/z13-profile`
  (`install -m755 src/profiles.py ~/.local/bin/z13-profile` after every change);
  `z13-restore-profile` → `z13-profile restore` (hooks skipped at login on purpose).
  ✅ `z13-fan-resume.sh` → `runuser -u deck -- … z13-profile restore` (2026-10-09, see Phase 7).
  Keep `z13-max/balanced/cool` as thin wrappers (`z13-profile apply Performance` …).
- ✅ **Desktop UI:** dropdown in the Performance panel + "＋ / Rename / Remove" in the Fans + Power
  window (Phase 9); the Fans + Power window always edits *the selected profile*.
- ✅ **Decky:** dropdown lists `profiles.json`; `set_profile` invokes `z13-profile apply <name>` through
  `host.run(as_deck=True)`. Only a root process adds `runuser`; the installed backend runs as deck.
- ✅ **z13ctl named-profile mirror and event stream (#23, implemented on current main).**
  `state_authority.py` mirrors supported app profiles and directs targeted edits to an explicit
  profile; `z13-profile watch` and `state_stream.py` reconcile daemon events into Desktop and Decky.
  Device checks for daemon behavior, cross-frontend convergence and reconnect remain open (#61).

- ✅ **Per-profile Max Fans** (2026-10-11): profile field `max_fans`; Turbo ships with it ON.
  Switching *to* such a profile turns Max Fans ON, switching *away* turns it OFF (and resets to
  firmware auto if the new profile has no curve); `restore` (login/resume) leaves the user's toggle
  alone. Checkbox "Max Fans" in the Fans + Power window. Older stores get the shipped value on load.

**Done when:** every built-in applies from the dropdown and from `z13-profile apply`; a login restores
the active profile; Max Fans survives a profile change; Decky shows and applies the same list.

---

## 5. Phase 9 — G-Helper layout port ✅ (in-scope controls implemented; device acceptance pending)
Lift G-Helper's layout (seerge/g-helper, `app/Settings*.cs`, `app/Fans*.cs`) as closely as the Z13 +
Linux allow. Research spec (2026-10-09) is summarised here; the port keeps its names.

### Main window (single stacked column, ~420 px wide, docks bottom-right on show)
Each panel: 20 px title row (16 px icon · bold title · grey right-aligned live sensor) over one row
of equal-width tiles.

| # | Panel | Controls | Z13 notes |
|---|---|---|---|
| 1 | **Performance Mode: &lt;profile&gt;[+][ &lt;W&gt;W]** · right: `CPU 44°C  Fan 2100RPM` | tiles **Silent / Balanced / Turbo / Fans + Power** (94×60, icon bottom-centre) + **profile dropdown** | Silent/Balanced/Turbo = the three built-ins; any other profile highlights **Fans + Power** in the base's colour (G-Helper's custom-mode rule). "+" = custom curve applied, "20W" = custom SPL. |
| 2 | GPU Mode | — | **hidden** (`NoGpu()` is true for GZ302 in G-Helper too) |
| 3 | Laptop Screen | Auto / 60 Hz / 120 Hz + OD | ⏳ later: refresh rate via KScreen/`kscreen-doctor`, overdrive via `z13ctl paneloverdrive`. Panel is 48–180 Hz; see Phase 13. |
| 4 | **Rear Light** | mode combo · colour | Z13-only in G-Helper (`HasRearLight() == IsZ13()`); maps to `z13ctl apply --device lightbar` |
| 5 | **Laptop Keyboard** | brightness **Off / Low / Medium / High** · lighting mode combo · colour · Extra (gear) | per-device writes (today's fix) |
| 6 | **Battery Charge Limit: 80 %** · right: `Charge: 86 %` | slider + **100 %** pill | existing semantics (apply on release) |
| 7 | Run on Startup · right: `Charge: 70.8%` | checkbox → `systemctl --user enable z13-helper` | |
| 8 | Version: x.y.z (red when an update exists) | | feeds Phase 10 |
| 9 | Footer | **Updates / Quit** (Donate dropped) | |

Theme (both from G-Helper): dark form `#1C1C1C`, primary tile `#2E2E2E`, secondary `#242424`, text
`#F0F0F0`, borders `#373737`; accents Silent `#06B48A`, Balanced `#3AAEEF`, Turbo `#FF2020`, Custom
`#FF8000`. Active tile = 2 px accent border + faint top tint, radius 5. Font: system UI font, bold titles.

### Fans + Power window (opens left of the main window, same height)
- **Left column (CPU page):** *Platform profile* combo (G-Helper's "Windows Power Mode" slot; quiet /
  balanced / performance); *CPU Boost* combo (amd-pstate EPP — `power/balance_power/balance_performance/performance`; implemented under #64, device check pending);
  **Power Limits**: **SPL (CPU sustained) / sPPT (CPU long boost) / fPPT (CPU short boost)** sliders
  per-rail host metadata bounds (5…93 W fallback), constraint sPPT ≥ SPL, values written on release; checkbox
  **Apply Power Limits** (= profile has a TDP). *Advanced page* (CPU UV −40…0, iGPU UV) only when
  `ryzen_smu` exists → hidden on stock SteamOS, same as G-Helper.
- **Right region:** title **BIOS Fan Curves** with **mode combo + ＋ / Rename / Remove** (Remove only for
  non-built-ins); **Clamp to Grid** checkbox; one chart **"CPU Fan Profile, RPM/°C"** (the Z13's two fans
  share one curve — z13ctl writes both; a second chart would lie). Chart: X 20…110 °C, Y 0…100 % with
  Y-axis click toggling RPM labels; **8 draggable points**, monotonic push in both axes, BIOS safety
  floor `y ≥ (x−70)·1.2`, Shift-drag moves all, arrow keys nudge, floating `65°C, 3600 RPM` label;
  series blue (Balanced) / green / red by base, grey when the curve isn't applied. Bottom: **Factory
  Defaults** · **Apply Custom Fan Curve** checkbox (= profile has a curve). Live temp/RPM stay in the
  main window title rows and tray tooltip (G-Helper doesn't draw them on the chart).
- Widget: `src/fan_curve_widget.py` (QPainter, no extra deps).

### Tray
Header "Performance Mode", one checkable item per profile, separator, Quit. Tooltip `CPU 44°C Fan 2100RPM`.

**Done when:** every control in the old Dashboard/Advanced tabs exists in the new layout; a screenshot
matches the G-Helper proportions; the Flatpak is rebuilt and the resident service restarted.

---

## 6. Phase 10 — Updates watch ✅ (2026-10-09; the "update section")
In-app: `src/update_watch.py` checks the repos in §11 once a day (and on "Check now"), compares the
latest release/commit with what's installed where we can read it (`z13ctl --version`,
`~/homebrew/services/.loader.version`, `uname -r`, `/etc/os-release`, `steamos-update check`), and:
- turns the **Version** row red + puts a count on the **Updates** footer button;
- shows a tray notification once per new version;
- opens an **Updates** dialog: *component · installed · latest · date · what's in it (first lines) · link*,
  with **Mark seen**.

Mechanics: GitHub REST (`/releases/latest`, or `/commits?per_page=1` for untagged projects), ETag
cache in `~/.config/z13-helper/updates.json`, unauthenticated (60 req/h is plenty), network via
`flatpak-spawn --host curl` (the sandbox has no network at runtime — keep it that way).
**Install / Update** (added 2026-10-09, the owner's ask): rows with an installer can be installed from the
dialog — it shows the plan, asks, streams the log (`src/installers.py`, runs on the host):
- **z13ctl**: release tarball, sha256 checked against `checksums.txt`, backup `z13ctl.bak-<old>`,
  `sudo z13ctl setup`, daemon user units with a `--no-button` drop-in, `enable --now`.
- **Decky Loader**: the official installer script, then the stale-loader (:1337) health check.
- **SteamOS**: `holo-update` first, `steamos-update` compatibility fallback; reboot to finish.
Everything else stays reference-only on purpose (asusctl/InputPlumber/hhd would fight z13ctl or the
controller fix; the kernel comes with SteamOS; G-Helper is Windows).

---

## 7. Phase 11 — Decky parity ✅ written 2026-10-11, on-device check pending (keep it working while everything above changes)
Contracts the plugin depends on (don't change silently): `max-fans.json` schema, `profiles.json`
schema + `z13-profile` CLI, z13ctl output formats parsed by `main.py`.
- ✅ **Profile dropdown** from `profiles.json` (replaces the raw platform-profile list; falls back to it
  if the store is missing). ✅ A failed switch now reverts to the backend's active profile and Max Fans is re-read after every switch (#16, PR #49, 2026-10-26; on-device check pending, #61).
- ✅ fan-stall watchdog (today).
- ✅ written, on-device check pending (2026-10-11, branch `claude/decky-parity`): **TDP sliders**
  SPL/sPPT/fPPT per-rail kernel bounds, 5…93 W fallback (sPPT ≥ SPL, fPPT ≥ sPPT, written on release),
  **Not set** when absent, + "Apply power limits" →
  active profile's `tdp` in `profiles.json`, then `z13ctl tdp --set` as deck (off →
  `z13-profile apply <active> --no-hooks`, same as the desktop window); **charge-limit slider**
  40…100 step 5 → `batterylimit --set` + profile `battery_limit`; **brightness Off/Low/Med/High**
  per device (lightbar failure shown, keyboard still set); **telemetry row** APU/GPU °C + both fan
  RPMs (hwmon by name, 2 s poll only while the QAM is open). Never reads `ppt_*` (no `tdp --get`).
  Backend helper tests: `python3 -m unittest discover -s decky/tests`.
- Build/install loop (unchanged): `pnpm run build` → `sudo cp plugin.json package.json main.py dist/ ~/homebrew/plugins/z13-helper/`
  → `sudo systemctl restart plugin_loader`. **Gotcha found today:** restarting the unit can leave the old
  loader alive holding :1337 ("address already in use"); check `sudo ss -ltnp | grep 1337` and kill the
  stale one before restarting.
- **Done when:** QAM panel shows the same profile list as the desktop app and applying one in Game Mode
  is visible in the desktop app afterwards (shared store).

---

## 8. Phase 12 — z13ctl 1.4.2 integration ✅ (device acceptance and watchdog retirement remain open)

The daemon is the hardware state backend. Current `main` mirrors app profiles into z13ctl named
profiles, routes thermal writes through the shared authority, and consumes the daemon's subscription
stream for UI state. The system resume hook uses `z13-profile resume-owner` to defer only when the
daemon version and saved state prove it owns the active profile; otherwise it performs the full
restore. The old 1.1.6 rollback notes and adoption checklist above have expired and are removed.

**#66 retirement gate:** retain curve-drop recovery for now. The 0.5.1 Desktop fan/power
checks exercised z13ctl 1.4.2 on SteamOS 3.9.5 / kernel 7.2, but that bounded test is not
the daemon-only curve restoration and cross-mode resume evidence needed to remove recovery.
Before retiring this branch, record #74 step 3 and #80 results: daemon-only curve
restoration after platform-profile writes and three suspend/resume cycles in
Desktop Mode and Game Mode, including Valve's ASUS fan-after-wake workaround.
Correlate restored curve points with sustained fan response and temperatures
(§12); keep stall, frozen-tach and auto-stall recovery independent of that decision.
Until then, both inline and queued dropped-curve recoveries recheck intent and
curve mode before writing, skipping recovery if the daemon has already restored
the curve or the mode can no longer be read.

---

## 9. Phase 13 — Game Mode polish 🔧 (VRR tooling implemented; device acceptance pending)
- **Steam's own TDP / charge-limit / profile controls (#29, after 1.0).** steamos-manager reports `DeviceModel "unknown"`
  here, so the QAM Performance tab lacks them. A small system-bus service implementing
  `com.steampowered.SteamOSManager1.{TdpLimit1,BatteryChargeLimit1,PerformanceProfile1}` registered
  via `/etc/steamos-manager/remotes.d/*.toml` (the ROG Ally ships one) would bring them back. Must call
  z13ctl, not sysfs (principle 1).
- **48–180 Hz VRR profile for gamescope** (`~/.config/gamescope/scripts/`, modelled on
  `asus.rogally.lcd.lua`). The repository now includes a Gamescope display script matched only
  to EDID vendor `TMA`, product `0x0803`, model `TL134ADXP03`; it derives 48–180 Hz fixed-clock
  front-porch modes from the panel's advertised 2560×1600 timing and the script is in the
  installer manifest. The panel EDID advertises Adaptive-Sync 48–180 Hz and DRM reports
  `vrr_capable=1`, but actual Game Mode slider reach and panel response to generated timings
  remain **on-device verification pending**. Do not describe this as hardware-accepted until
  tested in Game Mode. Run `python tooling/verify_gamescope_vrr.py` from the checkout for
  a read-only installed-script/EDID/OS/Game Mode preflight and a pending test matrix.
  With VRR on, check 48, 60, 120 and 180 Hz with **Disable Frame Limiter** both off and on
  on SteamOS 3.9.5. Record slider reach, panel stability and measured FPS versus the
  requested limit; a clean preflight does not establish timing acceptance or limiter behavior.
- **Open since Phase 6:** QAM focus test with a game running (only the owner can do it).

---

## 9a. Phase 12b — One-shot installer / repair script ✅ (shipped 2026-10-11; Updates-panel button written 2026-10-12, on-device check pending)

**As built (2026-10-11):** `install.sh` (repo root) + `tooling/install_check.py` (host-file manifest,
keep-list entries, `flatpak-diff`, and `verify` = §12 as code) + `src/installers.py repair z13ctl|decky`.
- `./install.sh` asks "Install dependencies?" → `installers.py install z13ctl` then `repair z13ctl`
  (an "already up to date" install would otherwise skip setup) → `install decky` (always, per the owner).
  Declining = repair the installed ones. `/etc` changes are listed and asked once.
- `./install.sh --repair`: no prompts; **keeps the installed z13ctl version** (the 1.4.x pin is
  deliberate; installs latest only if the binary is missing), `sudo z13ctl setup --perms-only` (full
  `setup` if the rules or perms unit are gone), daemon units + `--no-button` override restored if missing;
  Decky Loader reinstalled only if missing / inactive / behind latest.
- Plugin: pnpm found on PATH or under `~/.nvm` (falls back to the existing `decky/dist`); installed only
  when a file differs, then stop → `systemctl kill` → (kill a stale :1337 holder by PID) → start, and
  waits for "z13-helper up" in a log newer than the restart.
- Flatpak: rebuilt only when the installed tree (`flatpak info --show-location`) differs from the repo
  (`--rebuild` forces); app restarted only in a Desktop session.
- `z13ctl setup` owns `99-z13ctl.rules` and `z13ctl-perms.service`; the installer never copies the
  `packaging/host` references over them (those were refreshed from the 1.4.2 output the same day).
- Not installed on purpose: the xdg autostart entry (races the resident unit), asusd config,
  `z13_fanlog.sh`. Report-only: `users` group, InputPlumber mask (red if unmasked; never touched).
- Verified live 2026-10-11: dry runs against the real and an empty `$HOME`; `--repair` rebuilt the
  Flatpak and came back **52 ok / 1 warn (wedged lightbar) / 0 fail**; a second run changed nothing;
  the loader restart path exercised on its own ("up" in 1 s, one listener on :1337).
- ✅ written, on-device check pending (2026-10-12): **"Repair install"** button in the Updates panel.
  `install.sh` records `repo_path` (and `log`) in `install.json` (existing keys kept); the dialog shows
  "Last install/repair: <date> (<mode>, green/red)", the plan, asks, then streams
  `flatpak-spawn --host bash <repo>/install.sh --repair` with the final green/red line. Disabled (with
  a tooltip) until a run has recorded `repo_path`. Needs passwordless sudo. If the repair restarts the
  app, the window closes but the repair carries on (`tee -p`); the report stays in the printed log.

### Original design (2026-10-09)
**Ask:** after a SteamOS update, run one command instead of a Claude session to put everything back.
It must *fetch* the latest dependencies, not bundle them (they go stale), and ask first:
"Do you want to install the dependencies?"

**Design:** `install.sh` at the repo root (bash + the existing Python pieces), idempotent, safe to
re-run any time:
1. **Preflight** — SteamOS version, kernel, `users` group membership, `sudo -n` works, network.
2. **Dependencies (prompted)** — z13ctl latest via `src/installers.py install z13ctl` (sha256, backup,
   `sudo z13ctl setup` = udev rules + `z13ctl-perms.service`, daemon units `--no-button`);
   **Decky Loader latest** via `installers.py install decky` (official installer script, stale-loader
   health check) — always, not only when missing (2026-10-09); Flatpak runtime
   `org.kde.Platform//6.10`; `pnpm`/node for the plugin build (optional: ship `decky/dist` prebuilt so a
   plain install needs no node).
3. **App** — `bash flatpak/build.sh` (or install a prebuilt bundle from a GitHub release of this repo
   once we publish one), `systemctl --user enable --now z13-helper.service`.
4. **Host bits** — from `src/` and `tooling/`: `z13-profile`, `z13-button-watcher` + `z13_cdp.py`,
   `z13-max/balanced/cool`, `z13-restore-profile`, `boondock` → `~/.local/bin`; user units from
   `packaging/host/systemd/user/` → `~/.config/systemd/user/` (+ `daemon-reload`, enable);
   `/etc/systemd/system/z13-fan-resume.{sh,service}` + keep-list entry in
   `/etc/atomic-update.conf.d/z13-bucket1.conf` (so the next update doesn't drop it); **then the Decky
   plugin component**: `pnpm run build` (or the prebuilt `dist/`) → `~/homebrew/plugins/z13-helper`
   (`plugin.json`, `package.json`, `main.py`, `dist/`) with the clean loader restart, and a check that
   the backend logs "up". Order matters: Decky Loader first, plugin second, because the loader install
   restarts `plugin_loader` and would otherwise reload the old plugin.
5. **Verify** — the §12 checklist as code: one app instance, one button reader, daemon active, fans
   follow the curve, brightness per device returns 0, Decky backend "up", `z13-profile restore` works.
   Print a green/red report; non-zero exit on any red.
6. **Repair mode** — `install.sh --repair`: same as above but skips prompts and only re-applies what
   differs (`cmp` the installed copies against the repo, re-run `z13ctl setup --perms-only`).
   This is the "after an OS update" button. Wire it into the Updates panel too ("Repair install").

**Why not bundle:** z13ctl, Decky and the runtime move monthly; the installer records what it
installed (`~/.config/z13-helper/install.json`: versions + date) so the Updates panel and the
wiki can say what's on the machine.

**Done when:** on a fresh Desktop session after `steamos-update`, `./install.sh --repair` brings the
side button, fans, profiles and the QAM plugin back without any other step.

---

## 9b. Phase 12c — Post-update tasks in the Updates panel ✅ implemented; device acceptance pending (#27)

The desktop Updates tab, Decky QAM view, checks, repair plan, per-step confirmation, backups,
rollback records and redacted support-bundle preview are present on current `main` (issue #27 closed).
The design below records implemented behavior; remaining acceptance is a real SteamOS update check,
selected repair, failure rollback and support-bundle preview on the Z13. Do not treat a successful
unit test or simulated inventory as proof of a live repair.

**Why.** A SteamOS atomic update ("nuclear update") wipes `/usr`, Valve's grub file and the pacman
keyring, and rolls `/etc` changes back unless they're registered. Today the maintainer re-applies
things by hand from a wiki checklist. The owner wants the app to do it, **selectively**: tick what you need,
see what it will run, run it, read the log.

**The feature.**
- **"After SteamOS update" tab in Updates.** One row per task:
  - its status: ✅ fine · ⚠️ needs re-apply · 🔁 applied, reboot pending · ℹ️ info only · — not
    applicable here
  - what it fixes, in one line
  - a checkbox
  **Run selected** shows the plan (each command, what needs sudo, network or a reboot) and asks first.
  It then streams the log as **Repair install** does, backing up any file it replaces.
- **Order is automatic:** pacman keyring first (when any selected task uses pacman), then the rest,
  reboot-needing ones last. The app **never reboots by itself**; it ends with a "Reboot to finish"
  button.
- **Detecting an update:** `install.json` records SteamOS's `BUILD_ID`. When it changes, the tray and
  the Updates button say "SteamOS updated: N post-update tasks need attention", and the Decky panel
  shows the same line in Game Mode.
- **Without the GUI:** `./install.sh --post-update` (pick from a list) or `--post-update --only
  amdgpu,rocm`, for SSH or Game Mode. `--repair` keeps doing what it does today (Z13-Helper's own
  files) and then lists any post-update tasks still pending.
- **One task = one small recipe** with id, title, one-line "fixes", a read-only `check`, an
  idempotent `apply`, and flags (`sudo`, `network`, `reboot`, `pacman`, `default_on`). Built-in
  recipes live in `tooling/post-update/`. **Local recipes** in `~/.config/z13-helper/post-update.d/`
  appear in the same table, so personal items run from the app without going into the public repo.
- **Safety:** checks never write. Applies refuse on anything that isn't SteamOS on a GZ302. Grub only
  through `update-grub`, never `grub-mkconfig`. `steamos-readonly` is always re-enabled, even after
  a failure. Logs follow the logging opt-in.

**Candidates from the maintainer's post-update checklist**, scanned 2026-10-15. P1 = first:

| # | Task | Fixes | Check (read-only) | Apply | Needs | Tier |
|---|---|---|---|---|---|---|
| 1 | **amdgpu display + MES stability** ("the graphics glitch") | Game Mode display freezes (DMCUB `flip_done` timeouts), idle reboots, MES (GPU scheduler) firmware wedges on Strix Halo | `/proc/cmdline` and `/etc/default/grub-steamos` both carry `dcdebugmask=0x20e10 gpu_recovery=1 cwsr_enable=0 sg_display=0 abmlevel=0` | add the args to `grub-steamos` (Valve's file, so never registered), `update-grub` | sudo, reboot | **built-in, on; P1** |
| 2 | **pacman keyring** | "required key missing from keyring" on every pacman step after an update | `pacman-key --list-keys` count (0 after an update; ~180 when healthy) | `steamos-readonly disable` → `pacman-key --init` → `--populate` → `enable` | sudo | built-in; runs automatically before pacman tasks; P1 |
| 3 | **Carry-forward list health** | registered `/etc` files silently missing after a slot switch | every path in `/etc/atomic-update.conf.d/*.conf` exists (ours red, others info) | — (shows what's missing) | — | built-in, info; P1 |
| 4 | **Save SteamOS's own `/etc` snapshot** | SteamOS keeps per-update `/etc` backups in `/var/lib/steamos-atomupd/etc_backup/`, but per slot, so the next switch loses them | a new snapshot there with no copy under `~/.local/share/z13-helper/etc-backups/` | copy it to `/home` | sudo (read) | built-in, on; P2 |
| 5 | **MT7925 Bluetooth workarounds** (the Z13's Wi-Fi/BT card) | A2DP and HID drops, connect-time race after resume | udev no-autosuspend rule, `bt-mt7925-cycle.service`, `mt7925-bt-resume.service` + `.sh`, wireplumber SBC-only drop-in present and enabled | install from `packaging/host/`, register in the keep-list | sudo | built-in, on; P2 |
| 6 | **ROCm** (local LLMs on Strix Halo) | `/opt/rocm` survives, but the update strips `fmt` and `google-glog` and drops `/etc/ld.so.conf.d/00-rocm.conf`, so `rocminfo` won't run | `/opt/rocm` exists and `rocminfo` runs; `00-rocm.conf` and `profile.d/rocm.sh` present | `pacman -S --needed fmt google-glog` → write `00-rocm.conf` + `ldconfig` → `profile.d/rocm.sh` (PATH, `ROCM_PATH`) | sudo, network, pacman | **optional pack**, offered only when `/opt/rocm` exists; P2 |
| 7 | **"Has upstream fixed it?" probes** | knowing when a workaround can be retired | MES FW ≥ `0x7f` (then `cwsr_enable=0` can go) · mt7925 BT FW newer than `20250305` · kernel ≥ 6.19 (DCN 3.5 cursor fix: peel `dcdebugmask` bits) · kernel has Android binder again | — (links the retirement steps; never acts) | — | built-in, info; P2 |
| 8 | **Dev headers + `.pc` files** | `pkg-config`, gcc and Tauri builds on the host break | the stripped-`.pc` diff (`pacman -Qql` vs `/usr/lib/pkgconfig`) | extract only the missing `.pc` files from cached packages (`bsdtar`), not a full `--overwrite` reinstall (the rootfs is 5 GB) | sudo, pacman | optional pack, off; P3 |
| 9 | **Coredump size cap** | crash dumps filling the small rootfs | `/etc/systemd/coredump.conf.d/50-limit.conf` present | install it | sudo | optional pack, off; P3 |
| — | Already handled by `install.sh --repair` | z13ctl permissions, fan resume hook, lightbar reset + polkit rule, InputPlumber mask, Z13-Helper's keep-list lines | (existing checks) | (existing) | — | shown as rows so the tab is complete |

**Local recipes, not in the repo** (the maintainer's own setup; written as
`~/.config/z13-helper/post-update.d/*.toml` once the format exists): Waydroid restore and the
out-of-tree binder module build (per kernel), the rootless-podman LLM stack, Tailscale-up, the
network split-DNS dispatcher, sudoers drop-ins, the native-build container. When one moves into a
recipe, its wiki row changes to "Z13-Helper post-update task `<id>`", so the two lists don't drift.

**Order of work:**
1. The recipe format, the runner and `--post-update` in `install.sh`, with tasks 1–3.
2. The Updates-panel tab and the update detection.
3. Tasks 4–7.
4. Local recipes.
5. The optional packs (8, 9).

**Done when:** after a real SteamOS update, the tray says so; one tab shows what's broken; ticking
amdgpu + ROCm and pressing Run fixes both with a single reboot; the health report is green.

## 9c. Phase 12d — SteamOS 3.9 compatibility 🔧 (code fixes merged; device passes pending)

Valve's 3.9.5 post: https://store.steampowered.com/news/app/1675200/view/694275728822244963 (2026-10-23). Valve's page doesn't render to our fetcher, so the item list comes from GamingOnLinux's copy of the notes (2026-10-24), cross-checked with SteamDeckHQ (https://steamdeckhq.com/news/steamos-3-9-update-improves-idle-battery-life/, 2026-10-24) and MIXED (https://mixed-news.com/en/steamos-3-9-stable-steam-deck-idle-power-streaming-fixes/, 2026-10-24). Confidence: **RN** = in the release notes, **U** = user report, **PR** = press, **?** = unknown.

**Nothing in 3.9 replaces us:** the notes add no TDP, charge-limit or fan controls for ASUS or the Z13 (the only third-party items are Legion Go 2 fixes), so Steam's QAM still won't offer them (#29 stays after 1.0).

| Change | Conf. | What it means for us | Issue |
|---|---|---|---|
| **Kernel 6.18.50 → Linux 7.2** | RN | The initial 2026-10-26 note that no z13ctl release was tested on 7.2 is superseded: the 0.5.1 Desktop fan/power checks used z13ctl 1.4.2 on SteamOS 3.9.5 / kernel 7.2. The broader hardware and resume matrix remains open in #74. 7.2's x86 platform-driver merge has no Z13 change (Phoronix, 2026-06-26); the GZ302EAC went into asus-armoury in the 7.0 cycle. | #74 |
| asus-nb-wmi writes hung 60 s then EIO on a GZ302EA, kernels 7.0.3–7.0.10 | U | #79 added bounded writes and a clear timeout path; the underlying kernel/interface behavior and full device matrix are not inferred from the timeout fix. | #74; #79 closed |
| **"Worked around a firmware issue on some ASUS devices causing excessive fan speed upon waking from sleep"** (models not named) | RN | Touches the same resume moment as `z13-fan-resume` and z13ctl's own reconcile. If it writes `platform_profile` or pwm after us, the custom curve is lost. | #74, #80 (#66) |
| **`steamos-*` scripts and packages renamed `holo-*`**, aliases kept only "where appropriate"; `holo-readonly` exists (https://github.com/ValveSoftware/SteamOS/issues/2955) | RN | We call `steamos-update` (Updates panel, installer); the post-update runner will call `steamos-readonly`. Probe `holo-*` first, fall back. | #76 (#67) |
| **Steam client 2026-10-13 split the QAM class module**: every Decky plugin broke (`TypeError … reading 'Title'`) until **Decky Loader 3.3.0** (2026-10-18; https://github.com/SteamDeckHomebrew/decky-loader/issues/988, https://github.com/SteamDeckHomebrew/decky-loader/releases) | U (maintainer-closed) | Not a 3.9 change, but it lands at the same time. Our panel title uses `staticClasses.Title`. Decky 3.3.0 is the minimum; 3.3.0 also warns about unvetted plugins, which ours (sideloaded) may trigger. | #77, #78 |
| **Plasma 6.4.3 → 6.7.3** | RN | Tray (StatusNotifier), autostart and the `org.kde.ScreenBrightness` API could behave differently. | #75 |
| Wi-Fi default → **wpa_supplicant** (fast reconnect on resume); the preview said new installs only | RN | Resume path changes; our resume hook doesn't touch Wi-Fi, but the resume timing does. | #75 |
| Frame limiter now works with VRR on; external-display limits fixed | RN | Retest the 48–180 Hz profile with VRR on. | comment on #73 |
| Preliminary **hibernation** (no Game Mode UI) | RN | Our restore paths only run after suspend. | #81 (after 1.0) |
| A/B update, `/etc` overlay, Python version, Flatpak runtime, gamescope version | ? | Not mentioned anywhere. Our /etc units, polkit rule and keep-list must be re-checked by hand; `python3 --version` recorded. | #75 |
| steamos-manager | ? | Valve's gitlab couldn't be read. Watch item: the OpenGamingCollective fork's open PR to auto-switch the platform profile when a TDP is set (https://github.com/OpenGamingCollective/steamos-manager/pull/9) would fight our profile ownership if it ever ships. | comment on #29 |

Not issues (checked, nothing to do): the RDNA1/2 dedicated-GPU resume fix (Strix Halo is an iGPU); HDMI 2.1 FRL (press only, external displays); Android binder back in the kernel (only #71's probe list); the revoked eMudhra certificates; the Deck-only idle-power change.

**Status:** #76-#79 are closed with their code integrated. The remaining sequence is #74/#75 device passes with #61, plus the five-cycle #80 resume evidence and the separate #73 VRR matrix. Kernel-major and BUILD_ID triggers for the post-update tab are tracked by #70 and #71.

**Done when:** #74 and #75 pass on 3.9.5, #76–#79 remain merged, and #80's five-cycle device evidence is recorded. The implementation for #80 is merged; the issue stays open for that evidence.

## 10. Phase 14 — Feature backlog 💤 (from the 2026-10-09 research, priority order)
0. ✅ **Crash and error log** (2026-10-12, v0.4.1): `src/app_log.py` + Help → Error log; app log, crash reports and hard-crash recovery under `~/.local/state/z13-helper/`. The diagnostics panel below can grow out of the same window.
0b. ✅ **Fan trace** (2026-10-15): `fan_watchdog.FanTrace` logs both watchdogs (desktop app, Decky) to `fan-trace-{desktop,gamemode}.log` in the same folder — a sample every 30 s, every tick while a tach looks frozen, the last 2 min + next 1 min around any recovery; Help → Error log → Fan trace. Opened to settle whether today's frozen-tach recoveries at 8700/8800 RPM with Max Fans on are real or false positives.
0c. ✅ **Logging opt-in** (2026-10-15, for the public repo): a full `install.sh` asks "Do you want to enable error logging to help with development of the app?" once (`--yes` = yes; `--repair` keeps the answer) and records it as `"logging"` in `~/.config/z13-helper/install.json` (`src/install_record.py`; missing = on, so older installs keep logging). Help → Error log has a matching **Save logs to disk** checkbox that applies at once; the Decky plugin reads the choice when its watchdog starts. Off = no `app.log`, crash files, `fatal.log` or fan traces: crash reports stay in memory for the Error log window, faulthandler and warnings go to stderr/the journal.
1. **Diagnostics panel:** kernel version (and whether ≥ 7.0 → backlight/armoury fixed), `asus::kbd_backlight`
   present?, armoury `ppt_*` ENODEV?, lightbar responding?, asusd installed? (warn), z13ctl daemon state.
2. **Separate keyboard / rear-light lighting** (two hidraw devices; lightctl's packet `5D BD 01 kb bar lid rear FF`).
3. **Kernel-aware backlight:** on 7.0+ use `/sys/class/leds/asus::kbd_backlight` and follow `brightness_hw_changed` (Fn+F11).
4. **Battery health:** `energy_full / energy_full_design` (85 % today), `charge_mode` (read-only).
5. **Richer telemetry:** `amdgpu` `power1_input`, `k10temp` Tctl, NVMe temp, charger V/A (`ucsi_source_psy_*`), `BAT0/power_now`.
6. **Tablet mode on folio attach/detach:** udev on `0b05:1a30` → `SW_TABLET_MODE` via uinput (z13-tablet-switch); re-apply lighting; optional profile switch.
7. **Auto-rotation:** `iio-sensor-proxy` (installed, inactive) on `accel_3d`.
8. **Sleep hygiene:** wake-source report, folio wake toggle, webcam reset after resume.
9. **EPP / boost per profile** (`energy_performance_preference`; steamos-manager exposes `CpuBoost1`).
10. **GPU performance level** (`power_dpm_force_performance_level`; needs root helper).
11. **Panel overdrive + boot sound toggles** — z13ctl already has both commands; cheap.
12. **VRAM / GTT info page** for local LLMs (`ttm.pages_limit` advice only; no GRUB edits from the app).
13. **Fn+F5 cycles profiles** (6.18 keycode unverified).
14. **Undervolt via ryzenadj** without `ryzen_smu` — experimental, concurrent SMU writers hang; last.

---

## 10a. Phase 15 — Goal control 💤 after 1.0 (added 2026-11-01, the owner's pick)
**Why:** today the owner drives the firmware directly: four mode tiles, three wattage sliders and a hand-drawn RPM-per-°C chart, plus a Max Fans switch that several writers fight over (#86, #94, #96). Phase 15 lets the owner say what they want and has one controller deliver it.

**Goals:** **Quiet** (fan RPM ceiling), **Comfort** (keep the tablet comfortable to hold; the Z13 has no skin sensor, so #127 finds a proxy), **Hold FPS** (a frame-rate target in Game Mode, if #126 finds a dependable source), **Max** (today's Max Fans, now a goal), and **Custom** (today's fixed profiles, unchanged).

**Shape:** a pure decision function (#123) turns goal + readings + current setpoints into the next TDP and curve, with hysteresis, a maximum step and a minimum dwell. A runner (#125) is the only curve and TDP writer while a goal is active, in whichever mode owns hardware, through the #23 state owner (#120). The fan watchdog's curve-drop recovery becomes "re-assert the goal's setpoint", which retires the race class behind #86/#94/#96 once it lands (and overlaps #66). Profiles gain an optional `goal` (#124). The UI swaps the mode tiles for goal tiles that show whether the goal is met; the chart and sliders move to an Expert section (#128).

**Principles kept:** 1 (writes through `z13ctl` only), 4 (fixed order on a full profile apply), 5 (hwmon only, never Armoury `current_value`), 6 (Max wins: it is the top goal).

| Step | Issue | Blocked by |
|---|---|---|
| 15a decision function | #123 | — |
| 15b `goal` in the profile schema | #124 | — |
| 15c runner, single writer | #125 | #123, #124, #120, #79 |
| 15d frame-rate source (probe) | #126 | — |
| 15e Comfort sensor (probe) | #127 | — |
| 15f goal picker, Expert section | #128 | #125, #126 |

**Build snapshot (2026-11-07):** #123 has a pure decision-step implementation; it still needs trace/edge-case acceptance. #126's source probe ended with no dependable live FPS source, so Hold FPS remains unavailable. #124 is present from prior work. The goal runner (#125) and goal UI (#128) remain future builds; the Comfort probe tooling (#127) is built, with no device run yet (note below).

**15e Comfort sensor note (#127, 2026-11-04, desk work only; no run recorded, no constants chosen):** the candidates are Tctl (`k10temp`), `acpitz`, the NVMe composite, the battery (`BAT0/temp`) and the GPU edge, all read by `SensorReader.get_comfort_candidates()` (hwmon and power_supply only, never an Armoury file). `tooling/z13_comfort_probe.py sample --phase idle|game|load --external-c <°C>` appends a row pairing them with an IR-thermometer reading on the back panel; `fit FILE` ranks each candidate by RMSE of `external = offset + slope * reading`. Still to do on the Z13: one run each of idle, game and sustained load, then write the chosen sensor, offset, slope and any lag as constants for `goal_control.py` here. A lag model is not fitted yet; add it only if the residuals of the linear fit show a lag.

**15d frame-rate probe (#126) record:** each source tried, how it reads, and the pick. (1) gamescope's own stats: needs an output path supplied when gamescope starts; gamescope was not running during the probe, so no live read was made. (2) MangoHud: logging is opt-in and writes historical samples; there is no live read contract for the running process. (3) Steam client via Decky: no frame-rate reading exposed through an interface this plugin uses. Latency and overhead were not measured because no source produced a reading. **Pick: none.** `src/fps_source.read()` returns `None` without I/O, so Hold FPS is a no-go for now; 15f must hide it until a source is demonstrated.

**Done when:** Quiet and Max are verified on the Z13 across a Desktop↔Game Mode switch and resume; Comfort and Hold FPS are verified or recorded as a no-go with the reason; no other code path writes a curve while a goal is active.

## 10b. Phase 16 — Hardware as the interface 💤 after 1.0 (added 2026-11-01, the owner's pick)
**Why:** the Z13 is mostly a tablet, and the app is a dense mouse-sized form. Phase 16 makes the machine's own lights and button carry the everyday interactions, and gives the windows one touch-first look shared by the desktop and Game Mode.

**Pieces:**
- **Signal language** (#129, #130): the lightbar confirms a profile change, pulses while thermal throttling, glows when charging stops at the limit, and turns amber when a SteamOS update needs attention (Phase 12c). Priorities, a strict rate limit (the lightbar wedges under bursts, Phase 7), restore of the owner's lighting, and an off switch.
- **Side-button gestures** (#131): a triple press cycles profiles (hold is impossible; double press is Game Mode's Steam menu; one reader owns it per session) (goals, after Phase 15) with a lightbar confirmation, in both modes. InputPlumber stays masked (principle 2).
- **One touch-first interface** (#132 ✅, #133): [`docs/adr/0002-shared-touch-ui.md`](adr/0002-shared-touch-ui.md) picks **shared JSON design tokens (`src/design_tokens.json`) and a matching large-tile PyQt6 layout** over a desktop web view (which would add +277.3 MB to the 253.7 MB Flatpak, raise resident RAM from 65 MB to 516 MB across 4 processes in offscreen prototype measurements, and still require a custom `@decky/ui` shim). This is where the R04 design pass's shared design system lands.
- **Machine timeline** (#134, #135): `src/timeline.py` (#134) records every hardware or host change as one append-only entry under `~/.local/state/z13-helper/timeline.jsonl` (principle 7's journal line comes from the same call), with Phase 12c's rollback records; a view (#135) compares states and rolls back reversible entries. A SteamOS update (`BUILD_ID` change) shows as one entry listing what it changed.

| Step | Issue | Blocked by |
|---|---|---|
| 16a signal language | #129 | — |
| 16b signal runner + off switch | #130 | #129 |
| 16c side-button gestures | #131 | #130 |
| 16d ADR: shared touch UI ✅ | #132 | — |
| 16e touch-first layout | #133 | #132 |
| 16f timeline store (`src/timeline.py`; storage and host/Decky/post-update writers implemented; issue #134 closed) | #134 | #109 |
| 16g timeline view | #135 | #134 |

**Build snapshot (2026-11-07):** #130 now signals successful profile changes in Desktop and Game Mode, and update attention in Desktop. Thermal/charge/goal event sources and a one-hour no-wedge device check remain. #133 has shared tokens and initial desktop/Decky consumers, with rendered target-size and device evidence pending. #135 has desktop compare and QAM recent-change views plus allowlisted rollback for profile, Max Fans goal, TDP, charge limit and brightness entries; actions create their own timeline entries. Installer/post-update backup and OS rollback records remain view-only because no safe persisted executor exists. #131 remains unbuilt.

- **16e (#133) Where:** `src/design_tokens.json`, `src/theme.py`, `src/dashboard_ui.py`, `src/fans_window.py`, `decky/src/tokens.ts` (and token consumers in `decky/src/`), `MAP/README.md`, `MAP/decky.md` (no `PyQt6-WebEngine`, local web bridge, or `@decky/ui` DOM shim).
- **16e (#133) Done when:** `src/design_tokens.json` defines the shared palette, semantic profile/goal accents, radii, spacing and `>= 44 px` touch-target minimums consumed by both `src/theme.py` and `decky/src/` (checked in `pytest` without `PyQt6`); every interactive control in the desktop app is reachable by touch with targets of at least `44 px` while preserving every control from the current windows; the Decky plugin keeps `@decky/ui` `Focusable` QAM navigation styled from the same tokens; screenshots in tablet (keyboard off) and laptop use are in the PR; Flatpak dependencies remain unchanged. *Pending owner/coordinator action:* sync this Where and Done-when, including the shared-token and Decky acceptance criteria, to #133 on GitHub; it has not been posted. The ADR's prototype timings are offscreen only, with no committed harness, and are not application-level startup costs.

**Done when:** on the Z13, signals run an hour with no lightbar wedge; the gesture cycles profiles in both modes with no double-fire; the desktop window and the QAM share one look in tablet use; a SteamOS update appears in the timeline and a reversible entry rolls back.

## 11. Upstream watch (what the Updates panel checks, and what to look for)
Installed/pinned versions as of **2026-10-12**; SteamOS, kernel and Decky rows updated **2026-10-26** for SteamOS 3.9 (§9c). "Watch for" is what would change our plan.

| Component | Repo / source | Installed here | Latest | Watch for |
|---|---|---|---|---|
| **z13ctl** (backend) | `dahui/z13ctl` releases | **1.4.2** (daemon `--no-button`; 1.1.6 kept as rollback) | **1.4.2** (2026-10-09; still the latest 2026-10-26, untested on kernel 7.2) | a release tested on 7.2 / SteamOS 3.9; hard-lock reports after 1.4.x; asus-armoury path on Valve kernels; `subscribe` API changes (§8) |
| Decky Loader | `SteamDeckHomebrew/decky-loader` | 3.2.10 | **3.3.0** (2026-10-18; **minimum** since the 2026-10-13 Steam client, #77) | `@decky/api`/`@decky/ui` breaking changes; `_root` flag policy; the new unvetted-plugin warning |
| asusctl | `OpenGamingCollective/asusctl` | not installed (reference) | 6.5.0 | GZ302 Aura/LampArray support; boot-hang race with hid-asus (#718) — reason we don't run asusd |
| InputPlumber | `ShadowBlip/InputPlumber` | masked | 0.81.0 | a fix for the phantom Xbox Elite controller would let us unmask |
| G-Helper (UI reference) | `seerge/g-helper` | n/a | latest release | layout changes we might mirror |
| Linux kernel (Z13 fixes) | kernel.org `releases.json` | **6.18.50-valve2** | stable | SteamOS 3.9.5 ships **7.2**. **7.0**: hid-asus 64-byte handshake (fixes the `-75` backlight init), unified `asus::kbd_backlight`, GZ302 power-limit table; **7.3**: asus-armoury fan-curve gating |
| SteamOS | `holo-update check` (`steamos-update` before 3.9, #76) | 3.8.28 (20260922.1) | **3.9.5** stable (2026-10-23; kernel 7.2) | further `steamos-*` → `holo-*` renames; the ASUS fan-after-wake workaround; steamos-manager device profiles |
| steamos-manager (OGC fork) | `OpenGamingCollective/steamos-manager` PRs | — | — | PR #9 auto-switches the platform profile when a TDP is set; PR #8 TDP as a strict limit. Either would fight our profile ownership (#23) if it shipped in SteamOS |
| z13ctl-plus | `LostVector/z13ctl-plus` (commits) | — | — | EPP/boost ideas (§10.9) |
| z13-tablet-switch | `m4rw3r/z13-tablet-switch` (commits) | — | — | folio attach/detach handling (§10.6) |
| lightctl | `Xcli0126/lightctl` (commits) | — | — | rear-light packet changes (§10.2) |
| Handheld Daemon | `hhd-dev/hhd` releases | — | — | Z13 support on stock SteamOS kernels |

Manual check (what the app does, by hand):
```bash
for r in dahui/z13ctl OpenGamingCollective/asusctl SteamDeckHomebrew/decky-loader ShadowBlip/InputPlumber seerge/g-helper; do printf '%-36s ' "$r"; curl -s https://api.github.com/repos/$r/releases/latest | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("tag_name"),d.get("published_at","")[:10])'; done; z13ctl --version
```

---

## 12. Verification protocol (every phase)
0. Shortcut for most of the list: `./install.sh --repair` (or `python3 tooling/install_check.py verify`).
0a. Logic tests (no hardware needed; also run in CI): `python3 -m pytest` from the repo root (see `tests/README.md`).
1. `python3 -m py_compile src/*.py decky/main.py` and `pnpm run build` in `decky/`.
2. Rebuild + restart: `bash flatpak/build.sh && systemctl --user restart z13-helper.service`.
3. Exactly one instance: `pgrep -af '[/]app/share/z13-helper/main[.]py' | wc -l` → `1`.
4. Fan response: record both fan RPMs and temperatures before/after a profile or Max Fans change,
   allow the firmware ramp to settle, and correlate the trace with shared intent, the configured
   curve and backend saved TDP. Non-zero RPM and `pwm1_enable = 1` alone do not prove response;
   frozen tach requires corroboration. Repeat with the resident app to expose recovery races.
   Never poll Armoury live PPT values. See the 0.5.1 record for bounded checks already performed.
5. Brightness: `z13ctl --device keyboard brightness low` and `… lightbar …` both return 0.
6. Decky: inspect `journalctl -u plugin_loader.service` for backend startup/errors; verify one loader
   and walk through the QAM with a game running. A loaded backend does not establish interactive success.
7. Screenshot the new window(s) and compare with the G-Helper spec (§5).
8. Update the maintainer's notes and `README.md` when user-visible behaviour changed.
9. After a SteamOS update (first done for 3.9.5, #74 and #75): record `uname -r` and `/etc/os-release`; `ls /sys/class/firmware-attributes/asus-armoury/attributes`; a TDP write and a charge-limit write each return in under 5 s; a custom curve survives three suspend/resume cycles; tray, autostart and the side button (no double-fire) work; `holo-update`/`steamos-update` names recorded; Decky ≥ 3.3.0.

---

## Doc map
| File | Role |
|---|---|
| `CLAUDE.md` | cold-start orientation |
| `MAP/README.md`, `MAP/decky.md` | current module, process, state and installation connections |
| `docs/releases/0.5.1.md` | repair PR dependencies, evidence and outstanding device checks |
| **`docs/ROADMAP.md`** | **this plan — the only forward-looking doc** |
| `docs/plan/Phase_1…6.md` | historical record of how we got here (frozen; banner added) |
| `docs/BUTTON-AND-INPUTPLUMBER.md` | side-button root cause + fix |
| `docs/hardware-enablement.md` | the original enablement manual (z13ctl install rationale) |
| `README.md` | app write-up for users (refresh after Phase 9) |
