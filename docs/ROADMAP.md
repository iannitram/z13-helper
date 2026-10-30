# Z13-Helper — Roadmap

**Rewritten 2026-10-09.** This is the single plan for the project. It replaces the per-phase files
in `docs/plan/` (kept as history, see [Doc map](#doc-map)). Read `CLAUDE.md` first for orientation;
the maintainer's notes (outside the repo) stay the reference for *how the machine works*; this file is
*what we are building next and why*.

Status marks: ✅ shipped · 🔧 in progress · ⏳ planned · 🚧 gated (needs a decision or a precondition) · 💤 backlog

**1.0 scope (2026-10-26, [SPEC.md](../SPEC.md)):** Phases 7-13 below, every "on-device check pending" item verified on the Z13, the open bugs, plus issues #23 (one state owner, events) and #27 (reversible post-update repair). Since 2026-10-26 the checks run on SteamOS 3.9.5, so Phase 12d (§9c, #74-#80) is in 1.0 too. Out of 1.0: Phase 14, the Laptop Screen row of Phase 9 (#28) and Steam's own QAM controls in Phase 13 (#29). Code map: [`MAP/README.md`](../MAP/README.md).

---

## 1. Where we are (audit, 2026-10-09)

**2026-10-30 update:** Repair release **0.5.1** has Desktop fan/power checks on SteamOS
**3.9.5 / kernel 7.2** with z13ctl 1.4.2. See the [release verification record](releases/0.5.1.md)
and [current code/connection map](../MAP/README.md). PRs #89, #92, #93, #95 and #97 remain
open (#97 stacks on #93). The remaining historical audit and upstream-watch tables below retain
their dated observations; they are not current inventory. Interactive Game Mode, cold boot,
suspend/resume, sustained load and soak still need verification; 1.0 remains unfinished.

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
| Boondock | Earlier hardware/profile-hook checks: quiet + 20/30/40 W + screen 35 %. Optional service/container/VM paths remain in the script; cleanup and current device verification are pending. |

### Open bugs and test debt (updated 2026-10-30)
- **#60** no-curve `NameError`: fixed via PR #83. Current repair PR CI is green; the old main-CI blocker no longer applies.
- **#16** Decky Profile dropdown showing a state that never applied: ✅ fixed by PR #49 (`decky/src/profileSwitch.ts`), merged 2026-10-26; the on-device check is part of #61.
- **#58** flaky and environment-dependent tests: partly resolved, including temp-name/store-owner fixtures. Current suites pass; this does not close every environment check in the issue.
- **#86** fan/power regression: individual fixes are under review; root firmware causality and broader device verification remain open. See the release record for observed behaviour and rejected workarounds.
- **#30** the reported 43 GB memory spike: open (investigation).
- Also merged 2026-10-26: PR #3, `tooling/z13_gamemode_report.py` (reads a Game Mode session back from local logs).

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

## 3. Phase 7 — Stability ✅ (Boondock cleanup still ⏳)
Shipped today: per-device brightness, fan-stall watchdog, dashboard scroll (above).

Remaining:
- ✅ **Resume hook vs. profiles** (2026-10-09). `/etc/systemd/system/z13-fan-resume.sh` now waits 3 s for
  the EC, then runs `z13-profile restore` as deck (active profile: platform profile, TDP, curve, Max Fans
  pinned if ON) and falls back to the old curve logic. Backup: `z13-fan-resume.sh.bak-20261009`.
  Repo copy: `packaging/host/systemd/system/z13-fan-resume.sh`.
- ⏳ **Boondock script cleanup.** Drop the Docker/VM steps and the system `llama-swap` unit; point
  `BG_SERVICES` at the user unit (`systemctl --user`). The wiki already documents this.
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

## 4. Phase 8 — Profiles engine ✅ (2026-10-09; z13ctl mirror still 🚧)
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
- 🚧 **After the z13ctl upgrade (§8):** mirror each profile into a z13ctl named profile
  (`profile --create`, `tdp/fancurve/undervolt --set --profile <name>`) so the daemon re-applies it on
  resume and `autoswitch --ac/--battery` can pick between two of ours.

- ✅ **Per-profile Max Fans** (2026-10-11): profile field `max_fans`; Turbo ships with it ON.
  Switching *to* such a profile turns Max Fans ON, switching *away* turns it OFF (and resets to
  firmware auto if the new profile has no curve); `restore` (login/resume) leaves the user's toggle
  alone. Checkbox "Max Fans" in the Fans + Power window. Older stores get the shipped value on load.

**Done when:** every built-in applies from the dropdown and from `z13-profile apply`; a login restores
the active profile; Max Fans survives a profile change; Decky shows and applies the same list.

---

## 5. Phase 9 — G-Helper layout port ✅ (2026-10-09; Screen panel, lighting modes and tile icons still ⏳)
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
  balanced / performance); *CPU Boost* combo ⏳ (amd-pstate EPP — `power/balance_power/balance_performance/performance`);
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

## 8. Phase 12 — Adopt z13ctl 1.4.2 ✅ installed 2026-10-09 (the owner's go); follow-ups 🔧
**Why:** named profiles + autoswitch + resume re-apply + `subscribe` events + the curve-drop and TDP-undone
fixes + the EC hard-lock fixes. It would let us delete `z13-fan-resume.sh`, most of the Max Fans
watchdog, and the polling.
**Why not yet:** 1.4.0, 1.4.1 and 1.4.2 all shipped **today**; 1.4.1 fixes a hard-lock hazard 1.4.0
introduced. Let it settle a week. The daemon (`systemctl --user z13ctl`) is **not running here** today;
1.3+ expects it.
**Go/no-go checklist:** (1) no new hard-lock issues on the tracker for 7 days; (2) vendored 1.1.6 tarball
in `tooling/z13ctl-install/` verified as rollback; (3) `sudo z13ctl setup` re-run (new permission grants);
(4) daemon enabled `--no-button` (our watcher owns the button); (5) `tdp --get` confirms the old
asus-nb-wmi path on 6.18 (armoury `ppt_*` are ENODEV here, so the 28 W floor doesn't apply); (6) one
suspend/resume cycle on a custom curve with the fans checked after; (7) profiles mirrored (§4).
**Rollback:** `cp ~/.local/bin/z13ctl.bak-1.1.6 ~/.local/bin/z13ctl && systemctl --user disable --now z13ctl.socket z13ctl.service`
(the vendored 1.1.6 tarball in `tooling/z13ctl-install/` is the second copy).
**Follow-ups:** mirror app profiles into z13ctl named profiles + autoswitch (§4); retire the Max Fans
watchdog's curve-drop branch once the daemon is proven to re-apply curves itself; use `subscribe`
events (not in 1.4.2's CLI — check the socket API) instead of polling.

---

## 9. Phase 13 — Game Mode polish ⏳
- **Steam's own TDP / charge-limit / profile controls.** steamos-manager reports `DeviceModel "unknown"`
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
  tested in Game Mode.
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

## 9b. Phase 12c — Post-update tasks in the Updates panel ⏳ (added 2026-10-15, widened same day)

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

## 9c. Phase 12d — SteamOS 3.9 compatibility ⏳ (3.9.5 stable 2026-10-23)

Valve's 3.9.5 post: https://store.steampowered.com/news/app/1675200/view/694275728822244963 (2026-10-23). Valve's page doesn't render to our fetcher, so the item list comes from GamingOnLinux's copy of the notes (2026-10-24), cross-checked with SteamDeckHQ (https://steamdeckhq.com/news/steamos-3-9-update-improves-idle-battery-life/, 2026-10-24) and MIXED (https://mixed-news.com/en/steamos-3-9-stable-steam-deck-idle-power-streaming-fixes/, 2026-10-24). Confidence: **RN** = in the release notes, **U** = user report, **PR** = press, **?** = unknown.

**Nothing in 3.9 replaces us:** the notes add no TDP, charge-limit or fan controls for ASUS or the Z13 (the only third-party items are Legion Go 2 fixes), so Steam's QAM still won't offer them (#29 stays after 1.0).

| Change | Conf. | What it means for us | Issue |
|---|---|---|---|
| **Kernel 6.18.50 → Linux 7.2** | RN | Every sysfs path z13ctl writes may have moved or changed. z13ctl 1.4.0 already writes power limits through asus-armoury and 1.4.1 falls back when armoury isn't writable, but **no z13ctl release is tested on 7.2** (still 1.4.2, https://github.com/dahui/z13ctl/releases). 7.2's x86 platform-driver merge has no Z13 change (Phoronix, 2026-06-26, https://www.phoronix.com/news/Linux-7.2-x86-Platform-Drivers); the GZ302EAC went into asus-armoury in the 7.0 cycle. | #74 |
| asus-nb-wmi writes hung 60 s then EIO on a GZ302EA, kernels 7.0.3–7.0.10 | U | Unknown on 7.2. Our power writes wait up to 120 s, so a hang would freeze a profile apply for two minutes. | #74, #79 |
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

**Order of work:** #77 and #76 (small, no device) → #79 → the device passes #74 and #75 (with #61) → #78 → #80 if #74 shows a clash. Kernel-major and BUILD_ID triggers for the post-update tab: comments on #70 and #71.

**Done when:** #74 and #75 are green on 3.9.5, #76–#79 are merged, and #80 is merged or closed with measurements.

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
