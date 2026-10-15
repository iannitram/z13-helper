# Z13-Helper

A PyQt6 system-tray hardware control GUI for the **ASUS ROG Flow Z13 (2025, Strix Halo / Ryzen AI Max+ 395)** running SteamOS. Reads sensors, switches power profiles, manages battery limits, configures keyboard brightness, and binds the Armoury Crate side button to a window-toggle action — without leaving SteamOS.

> **Install:** `./install.sh` (asks first) or, after a SteamOS update, `./install.sh --repair`.
> Plan and status: [`docs/ROADMAP.md`](docs/ROADMAP.md). Changes: [`CHANGELOG.md`](CHANGELOG.md).
> The side-button story: [`docs/BUTTON-AND-INPUTPLUMBER.md`](docs/BUTTON-AND-INPUTPLUMBER.md).
> This README is the original app write-up and predates the Game Mode work.
>
> Licensed under the [GPL-3.0](LICENSE), like G-Helper. Not affiliated with ASUS or Valve.

**Status (2026-10-12, v0.4.0):** Redesigned after **G-Helper**'s layout (see [`docs/ROADMAP.md`](docs/ROADMAP.md) §5): a single stacked main window (Performance Mode tiles Silent / Balanced / Turbo / Fans + Power, profile dropdown, Keyboard & Rear Light, Battery Charge Limit, Run on Startup, Version, Updates / Quit) plus a **Fans + Power** window with a draggable 8-point fan curve and SPL / sPPT / fPPT sliders. **Profiles** (`src/profiles.py`) replace the old Boondock button: a profile bundles platform profile, power limits, fan curve, undervolt, lighting brightness, battery limit and an enter/exit hook; Boondock is a built-in profile. The same store drives the Decky plugin's dropdown and the login restore (`z13-profile restore`). An **Updates** panel watches upstream (z13ctl, Decky, kernel, SteamOS, asusctl, …) and can **install** z13ctl (sha256-verified tarball, daemon with `--no-button`), Decky Loader and SteamOS updates after showing the plan. Backend is **z13ctl 1.4.2** with its daemon since 2026-10-09. One command installs or repairs everything: `./install.sh` / `./install.sh --repair` (also the **Repair install** button in the Updates panel). A wedged rear lightbar (USB `0b05:18c6`) is reset automatically through a root oneshot + polkit rule. The **Decky** plugin covers Game Mode (profiles, Max Fans, TDP, charge limit, brightness, telemetry). Changes per release: [`CHANGELOG.md`](CHANGELOG.md).

---

## Features

### Main window (G-Helper layout)
- **Performance Mode** — tiles **Silent / Balanced / Turbo** (the built-in profiles) and **Fans + Power** (opens the editor; highlighted when a custom profile is active). Title shows `Turbo+ 70W` (+ = custom curve, W = custom sustained limit). Right-aligned live `CPU · GPU · Fan` readout. **MAX FANS** pill (100 % both fans; shared with the Decky plugin).
- **Profile dropdown** — every profile incl. **Boondock** (quiet + 20/30/40 W + the boondock script's screen-dim/service hooks) and your own custom ones.
- **Keyboard & Rear Light** — Off / Low / Medium / High, target Both / Keyboard / Rear light (written per device, so a wedged lightbar can't block the keyboard).
- **Battery Charge Limit** — 40–100 % slider (applied on release) + **100 %** pill; live charge %.
- **Run on Startup** — enables the resident `z13-helper.service`. Power source / discharge watts on the right.
- **Version** — turns red when upstream updates exist; **Updates** opens the upstream-watch table (component · installed · latest · date · why it matters) with **Install / Update** for z13ctl, Decky Loader and SteamOS (asks first, logs every step, keeps a backup); **Quit**.

### Fans + Power window (Ctrl+F or the tile)
- **CPU page** — platform profile combo; **SPL / sPPT / fPPT** sliders 5–93 W (sPPT ≥ SPL, fPPT ≥ sPPT; above 75 W z13ctl runs the fans at full); **Apply Power Limits**.
- **Advanced page** — Curve Optimizer −40…0 (only shown when `ryzen_smu` is loaded; hidden on stock SteamOS).
- **BIOS Fan Curves** — profile combo with **＋ / Rename / Remove**; **Clamp to Grid**; one draggable 8-point chart (both Z13 fans share one curve): X 20–110 °C, Y 0–100 % with RPM labels (click the axis to toggle), BIOS safety floor `y ≥ (x−70)·1.2`, monotonic push, Shift-drag moves all, arrow keys nudge; **Factory Defaults**; **Apply Custom Fan Curve**.
- Everything edits the selected profile; edits to the active profile are pushed to the hardware immediately, in order (platform profile → TDP → curve → undervolt → lighting → battery).

### Always
- **System tray** — Show/Hide, one checkable entry per profile, Fans + Power, Quit; tooltip = live sensors.
- **Side button** — Desktop: shows/hides the window (host evdev reader, `KEY_PROG3`=202). Game Mode: `z13-button-watcher` → Quick Access Menu / Steam menu.
- **Fan watchdog** — re-pins Max Fans when a profile write drops the curve, and rewrites profile + curve when the fans are stalled at 0 RPM under a curve that says they should spin.

---

## Repository layout

```
z13-helper/
├── README.md                  ← this file
├── main.py                    ← entrypoint (sys.path setup, QApplication)
├── assets/
│   └── icon.svg               ← app icon (used everywhere)
├── src/
│   ├── dashboard_ui.py        ← main window (G-Helper layout), tray, side button
│   ├── fans_window.py         ← Fans + Power window (TDP / fan curve / undervolt)
│   ├── fan_curve_widget.py    ← draggable 8-point curve chart (QPainter)
│   ├── profiles.py            ← profile model + apply engine; also the host `z13-profile` CLI
│   ├── profile_control.py     ← store access + live pushes shared by both windows
│   ├── apply_worker.py        ← one ordered background thread for hardware writes
│   ├── fan_watchdog.py        ← keeps the requested curve actually running
│   ├── update_watch.py        ← upstream release/commit watch (GitHub, kernel.org, steamos-update)
│   ├── updates_dialog.py      ← the Updates table
│   ├── theme.py               ← G-Helper colours, ModeTile, Panel
│   ├── hardware_controller.py ← thin wrapper over z13ctl CLI
│   ├── sensor_reader.py       ← direct hwmon sysfs reads (dynamic discovery)
│   ├── config_manager.py      ← JSON config at ~/.config/z13-helper/config.json
│   └── evdev_listener.py      ← legacy raw-evdev listener (unused now; kept for fallback)
├── Plan/                      ← phase-by-phase project notes
├── tests/                     ← (placeholder for future tests)
├── venv/                      ← Python 3.13 venv with PyQt6 6.11
└── z13ctl-install/            ← bundled z13ctl installer artefacts
```

Plus:
- `~/.local/bin/z13-helper`             — bash launcher (uses the venv directly, no terminal window)
- `~/.local/share/applications/z13-helper.desktop` — KDE/GNOME app-menu entry
- `~/.config/autostart/z13-helper.desktop`         — XDG autostart (login-time, hidden)

---

## How it talks to the hardware

| Concern | Mechanism |
|---|---|
| Profile / battery / brightness | `z13ctl <subcommand>` via `subprocess` |
| TDP (PL1/PL2/PL3) | `z13ctl tdp --set N --pl2 N --pl3 N [--force]` |
| Fan curve (8 points) | `z13ctl fancurve --set "T1:P1%,T2:P2%,..."` |
| Undervolt (CO offset) | `z13ctl undervolt --set N` (requires `ryzen_smu`) |
| Sensor readouts | direct read from `/sys/class/hwmon/hwmon*/` (dynamic discovery by name: `asus`, `acpitz`, `amdgpu`) |
| Side-button event | system D-Bus signal from InputPlumber on `org.shadowblip.Input.DBusDevice.InputEvent` |

z13ctl handles all the gnarly stuff (asus-wmi sysfs, ryzen_smu MSRs, hidraw RGB, ACPI battery limit, asus-armoury firmware attributes) — this app is purely a GUI on top.

---

## The side-button odyssey (and what we learned)

This was the hard part. Documenting it because nothing on the internet covers it for the 2025 Z13.

**The problem:** the Armoury Crate side button on the Z13 is a WMI hotkey (`KEY_PROG3`, scancode 579) on `/dev/input/event4` (`Asus WMI hotkeys`). On SteamOS, **InputPlumber** runs as a system service and exclusively `EVIOCGRAB`s that device — so user-space evdev reads return `EBUSY`.

**What InputPlumber is supposed to do:** remap the button to `Gamepad:Button:QuickAccess` and emit it as a D-Bus signal (`InputEvent`) on a virtual `DBusDevice` target.

**What actually happens on the Z13:**

1. The Z13 InputPlumber profile (`/usr/share/inputplumber/devices/50-rog_flow_z13.yaml`) attaches **only** `xbox-elite`, `touchpad`, and `keyboard` targets — **no `dbus` target**. Without one, there is nowhere for `InputEvent` to be emitted. A stale `target/dbus0` D-Bus path appears in the bus tree but is not wired to `CompositeDevice0`.
2. Even if a `dbus` target exists, `InterceptMode` defaults to `0` (None). Events flow silently to the gamepad target. You must set `InterceptMode = 2` (All) on `CompositeDevice0` for `InputEvent` to actually fire.
3. The event name is `"ui_quick"` — *not* `"Gamepad:Button:QuickAccess"` as upstream docs suggest. The Z13 capability map (`flow_type1.yaml`) uses a custom name.

**The fix** (in `_start_side_button_listener` in `dashboard_ui.py`):

```python
# 1. Add 'dbus' to CompositeDevice0's target list
dbus-send --system --dest=org.shadowblip.InputPlumber \
    /org/shadowblip/InputPlumber/CompositeDevice0 \
    org.shadowblip.Input.CompositeDevice.SetTargetDevices \
    array:string:"xbox-elite","touchpad","keyboard","dbus"

# 2. Set InterceptMode=2 (uint32!)
dbus-send --system --dest=org.shadowblip.InputPlumber \
    /org/shadowblip/InputPlumber/CompositeDevice0 \
    org.freedesktop.DBus.Properties.Set \
    string:"org.shadowblip.Input.CompositeDevice" string:"InterceptMode" \
    variant:uint32:2

# 3. Subscribe via PyQt6 to InputEvent on /devices/target/dbusN
#    with explicit signature "sd" (string + double)
```

**PyQt6-specific pitfalls along the way:**
- `QDBusConnection.connect()` requires the signature-form overload (`"sd"`) so the two-argument `InputEvent(s,d)` signal unmarshals cleanly. The signal-only form (no signature) silently never fires.
- The slot must be a `@pyqtSlot(str, float)`-decorated method on a `QObject` — lambdas and plain functions are rejected with a `TypeError`.
- Setting the `InterceptMode` property via `QDBusInterface` fails with "incorrect type" because PyQt6 marshals Python `int` as D-Bus `i` (int32), but the property is `u` (uint32). We use `dbus-send` instead — the cleanest PyQt6-only path would be to construct a `QDBusArgument` manually, which is more code than just shelling out.

**Cleanup:** the app calls `app.aboutToQuit` to reset `InterceptMode` back to 0 on graceful exit so the gamepad target gets events again. SIGTERM bypasses this; in practice the user quits via File → Quit.

**Credit:** the CachyOS handheld project (`dahui/z13ctl` and `dahui/z13gui`) solves this differently — they bypass InputPlumber entirely and have their own daemon `EVIOCGRAB` the WMI device with a udev rule, then expose a Unix socket for GUI subscribers. That's a more robust path long-term but requires more infrastructure than we needed.

---

## Installing & running

### One-shot install / repair (2026-10-11)
```bash
./install.sh            # asks "Install dependencies?" (latest z13ctl, latest Decky Loader), then installs everything
./install.sh --repair   # after a SteamOS update: no prompts, puts back only what differs
./install.sh --dry-run  # show what would change, write nothing
```
Order: z13ctl → Decky Loader → Decky plugin (built with pnpm) → Flatpak (rebuilt only when the
installed tree differs from the repo) → `~/.local/bin` scripts + user units → `/etc` resume hook +
atomic-update keep-list → a green/red verification report (`tooling/install_check.py verify`, which
also runs `z13-profile restore`). Replaced files are backed up under
`~/.local/state/z13-helper/install-backups/`; the result is recorded in
`~/.config/z13-helper/install.json`. The manual steps below are what it automates.

### Prerequisites
- SteamOS 3.8 or any Arch-based distro
- `z13ctl` 1.4.2+ installed and set up: `sudo ~/.local/bin/z13ctl setup --group <your-group>`
- InputPlumber **masked** on this machine (phantom controller; see `docs/BUTTON-AND-INPUTPLUMBER.md`).
  The side button is read via evdev, not InputPlumber.
- Python 3.11+ with PyQt6 6.11+ in a venv at `./venv/`

### From source
```bash
cd z13-helper   # the repo root
python3 -m venv venv
venv/bin/pip install -r requirements.txt   # currently just PyQt6
venv/bin/python3 main.py --show
```

### Launch via the helper script
The bash launcher activates the venv silently — no terminal window:
```bash
~/.local/bin/z13-helper            # start hidden in tray
~/.local/bin/z13-helper --show     # open window immediately
```

### Autostart
The XDG autostart entry at `~/.config/autostart/z13-helper.desktop` is already in place. On next login, the app launches hidden in the tray. Press the side button or click the tray icon to bring it up.

### Gaming Mode (Gamescope)
Gamescope has no system tray, but the side button still works. To autostart in Gaming Mode: in Steam, *Add a Non-Steam Game* → browse to `/home/deck/.local/bin/z13-helper`. Launch it once and it'll stay in your library.

---

## Configuration

JSON file at `~/.config/z13-helper/config.json`. Stores:

```jsonc
{
  "profile": "balanced",          // last-applied profile
  "battery_limit": 80,            // last-applied limit
  "brightness": "medium",         // last-applied keyboard brightness
  "advanced_apply_on_launch": false,
  "tdp": { "pl1": 70, "pl2": 86, "pl3": 86, "force": false },
  "fancurve": [[45,5],[55,15],...],   // 8 (temp, %) pairs
  "undervolt": -20
}
```

Profile restore at login is the host's job: `z13-restore-profile.service` runs `z13-profile restore` (the active profile from `~/.config/z13-helper/profiles.json`, hooks skipped). Install the CLI with `install -m755 src/profiles.py ~/.local/bin/z13-profile`.

---

## Hardware reference (this machine)

| Sensor / control | Path / interface |
|---|---|
| Both fans | `hwmon` device named `asus`, `fan1_input` and `fan2_input` |
| APU temp | `hwmon` device named `acpitz` |
| GPU temp | `hwmon` device named `amdgpu` |
| Profile / battery / brightness / TDP / fan curve / undervolt | `z13ctl` (~/.local/bin/z13ctl) |
| Side button (raw) | `/dev/input/event4` "Asus WMI hotkeys", `KEY_PROG3` (579) — held by InputPlumber |
| Side button (D-Bus) | `org.shadowblip.InputPlumber` → `/org/shadowblip/InputPlumber/devices/target/dbusN` → `org.shadowblip.Input.DBusDevice.InputEvent("ui_quick", value)` |
| Asus HID | `0b05:1a30` (keyboard), `0b05:18c6` (N-KEY) |

`SensorReader` discovers hwmon devices by name rather than by index because index assignment is non-deterministic across reboots.

---

## Known issues / quirks

1. **APU temperature reads `N/A` from `z13ctl status`** but our `SensorReader` reads it correctly from `/sys/class/hwmon/<acpitz>/temp1_input`. The status bar / Dashboard tab shows the real value.
2. **`InterceptMode` cleanup is best-effort.** If the app is killed by SIGKILL, InterceptMode stays at 2 and InputPlumber routes everything through D-Bus until the next clean exit or reboot. Use File → Quit, not `kill -9`.
3. **Battery limit resets to 100 % on reboot.** This is a z13ctl / firmware behavior. With `advanced_apply_on_launch=false`, the app re-applies the last config-saved limit at the next launch. With it on, it also restores TDP / fan / UV.
4. **Undervolt requires `ryzen_smu` kernel module.** Not present on stock SteamOS. The Advanced tab disables those controls cleanly with an explanatory note.
5. **InputPlumber occasionally panics** in `composite_device/client.rs:463` (`Cannot block the current thread from within a runtime`) when a D-Bus property is set rapidly. Doesn't crash the service but spews into the journal. Filed upstream territory.

---

## Distribution

### Current
- Run from source via the venv launcher (above)
- Autostart via XDG `.desktop`
- App-menu entry via XDG `.desktop`

### In progress: Flatpak
See `flatpak/` for the manifest. Build with:
```bash
flatpak install --user flathub org.flatpak.Builder
bash flatpak/build.sh
flatpak run io.github.iannitram.Z13Helper --show
```
The Flatpak uses `flatpak-spawn --host z13ctl …` to call the host-side `z13ctl` binary so hardware access (udev, asus-wmi, hidraw, ACPI) works. System D-Bus access to InputPlumber is granted via `--system-talk-name=org.shadowblip.InputPlumber`. See `flatpak/README.md` for the full story.

### Future options considered
- **AppImage** — single-file, no install. Best for users who don't want to install Flatpak runtimes. Would bundle Python + PyQt6 (~80 MB).
- **AUR package** — native install on Arch derivatives (CachyOS, EndeavourOS). Excludes Bazzite/Fedora users.

---

## Project history

| Phase | Status | Notes |
|---|---|---|
| 1 — Research | ✅ done | hwmon discovery, evdev probing, z13ctl flag mapping |
| 2 — Backend | ✅ done | HardwareController, SensorReader, ConfigManager (evdev listener superseded by D-Bus path) |
| 3 — GUI | ✅ done | Dashboard, tray, menu, autostart, custom icon, Gamescope-aware tray, Advanced panel |
| 4 — Side-button | ✅ done | InputPlumber `SetTargetDevices` + `InterceptMode=2` + `ui_quick` event subscription |
| 5 — Distribution | 🟡 in progress | Flatpak manifest |
| 6 — Polish | ⏳ planned | Icon refinement, fan curve graph view, save profiles, telemetry history |

Historical (Phases 1–6, kept as written). Current status and plan: [`docs/ROADMAP.md`](docs/ROADMAP.md); per-release changes: [`CHANGELOG.md`](CHANGELOG.md); per-phase history: `docs/plan/Phase_*.md`.

---

## References

- z13ctl: <https://github.com/dahui/z13ctl> (community CLI, primary backend)
- InputPlumber: <https://github.com/ShadowBlip/InputPlumber>
- InputPlumber Z13 profile: `/usr/share/inputplumber/devices/50-rog_flow_z13.yaml`
- InputPlumber capability map: `/usr/share/inputplumber/capability_maps/flow_type1.yaml`
- D-Bus signal definition: `src/dbus/interface/target/dbus.rs` in InputPlumber upstream
- PyQt6 D-Bus quirks: PYSIDE-2547 (slot connection), upstream Qt docs for `QDBusConnection.connect` overloads
- CachyOS handheld stack (alternative button-handling pattern): `dahui/z13gui` AUR package
