# Z13-Helper

A [G-Helper](https://github.com/seerge/g-helper)-style control app for the **ASUS ROG Flow Z13
(2025, GZ302EA, Strix Halo / Ryzen AI Max+ 395)** on **SteamOS**. It covers both of SteamOS's
sessions:

- **Desktop Mode:** a tray app, shipped as a Flatpak.
- **Game Mode:** a [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin in the
  Quick Access Menu.

It also makes the Armoury Crate side button useful in both. Behind both front-ends,
[**z13ctl**](https://github.com/dahui/z13ctl) does the hardware access; Z13-Helper is the interface,
the glue and the self-repair around it.

> **Status:** a personal project, built and tested on one Z13 (GZ302EA, SteamOS 3.8.28, kernel
> 6.18, z13ctl 1.4.2). It writes power limits and fan curves, so read what it does before you run
> it. Not affiliated with ASUS, Valve or any project credited below. Licensed under the
> [GPL-3.0](LICENSE).
>
> Plan and status: [`docs/ROADMAP.md`](docs/ROADMAP.md) · Changes: [`CHANGELOG.md`](CHANGELOG.md)
> · The side-button story: [`docs/BUTTON-AND-INPUTPLUMBER.md`](docs/BUTTON-AND-INPUTPLUMBER.md)

---

## What it does

### Desktop app (Desktop Mode)

The window is laid out after G-Helper's: one main window plus a **Fans + Power** window.

- **Performance Mode.** Tiles for **Silent / Balanced / Turbo**, plus **Fans + Power**.
  - The title shows the active mode, e.g. `Turbo+ 70W`: `+` means a custom fan curve, and the
    wattage is a custom sustained limit.
  - It also shows live CPU and GPU temperatures and both fans' RPM.
- **Profiles** dropdown with every profile, including your own (see [Profiles](#profiles)).
- **Max Fans** pill: both fans at 100 %. Turning it off puts back the curve you had.
- **Keyboard & Rear Light** brightness (Off / Low / Medium / High), for both or for either one.
  Each device is written separately, so a stuck lightbar can't block the keyboard.
- **Screen Brightness** slider:
  - Uses KDE PowerDevil's brightness API, so Plasma stays in step.
  - Falls back to the backlight in sysfs.
  - Has a minimum, so it can't black out the panel.
- **Battery Charge Limit**, 40–100 %, with a one-tap **100 %**, plus live charge and power draw.
- **Run on Startup**: runs the app as a resident user service, hidden in the tray until you need it.
- **Fans + Power window:**
  - **Power limits:** SPL / sPPT / fPPT sliders, 5–93 W, kept in order (sPPT ≥ SPL, fPPT ≥ sPPT).
  - **Fan curve:** a draggable 8-point curve editor with Clamp to Grid, Factory Defaults and RPM
    labels. Both Z13 fans share one curve.
  - **Curve Optimizer:** undervolt, shown only when the `ryzen_smu` module is present. Stock SteamOS
    doesn't have it.
- **Tray menu:** show/hide, switch profile, Fans + Power, Quit. The tooltip shows live sensors.
- **Help → Error log** (Ctrl+L):
  - the app's log
  - saved crash reports (uncaught exceptions and hard crashes are caught, and the app keeps
    running)
  - the **fan trace** from both watchdogs

### Decky plugin (Game Mode)

In the Quick Access Menu:
- Live telemetry, the **Max Fans** toggle and the **Profile** dropdown.
- **Power limit** sliders and the **battery charge limit**. Changes are saved into the active
  profile, so they come back after a reboot.
- **Keyboard brightness.**

Its backend also runs the **fan watchdog** in Game Mode. In Desktop Mode it restarts the desktop
app if the app has gone, but never after you quit it on purpose.

### The side button

The small Armoury Crate button next to the power button:
- **Desktop Mode:** shows and hides the window. If the app isn't running, a press starts it.
- **Game Mode:**
  - **single tap** opens the **Quick Access Menu**, and a second tap closes it
  - **double tap** opens the **Steam menu** (app switching, library, power)

  Menus open through a JavaScript call into the Steam client, with no input emulation, so no
  phantom controller appears.

The button is read straight from the kernel (`Asus WMI hotkeys`, `KEY_PROG3` = 202) on the host,
because the Flatpak sandbox can't open input devices.
[`docs/BUTTON-AND-INPUTPLUMBER.md`](docs/BUTTON-AND-INPUTPLUMBER.md) explains why it doesn't go
through InputPlumber.

### Fans you can trust

The Z13's firmware has some surprises. Every curve change stops the fans for 5–10 s. A
platform-profile write silently drops a custom curve. Sometimes the firmware ignores a curve even
though it reads back as active, or a stopped fan keeps reporting its last RPM. So a **fan
watchdog** runs in both front-ends. It is one shared module, tested once.

It reacts to three things:
- **Dropped curve while Max Fans is on:** puts Max Fans back.
- **Stall:** the curve asks for at least half speed and both fans read 0 RPM.
- **Frozen tach:** a spinning fan's reading stays exactly the same for 90 s. Real readings jitter
  every tick, so a stuck one means that fan has probably stopped.

Each time, it recovers the only way that reliably works: rewrite the platform profile, then the
curve. It never acts while a profile change is in progress. The **fan trace** logs its readings
and decisions, including the two minutes before and the minute after every recovery, so false
alarms can be told from real stalls.

### Profiles

A profile bundles:
- the platform profile
- power limits
- the fan curve, with an optional **Max Fans**
- an undervolt
- lighting brightness
- the battery limit
- an optional enter/exit command

Silent, Balanced and Turbo come built in, and Turbo has Max Fans on. **Boondock** is a built-in
off-grid profile: quiet, 20/30/40 W, a dimmed screen, and background services stopped. Your own
profiles are made in the Fans + Power window.

One profile store drives the desktop app, the Decky plugin and the host command
**`z13-profile`** (`list`, `active`, `show`, `apply`, `restore`). `z13-profile` is the same Python file as the
app's profile engine.

### Settings that stick

The firmware forgets things: the battery limit resets to 100 % on boot, and curves drop after
sleep. Z13-Helper puts them back:
- **At login:** a user service runs `z13-profile restore`, which re-applies the active profile and
  keeps Max Fans if it's on.
- **On resume:** a system resume hook re-applies the active profile, so the curve and Max Fans come
  back after sleep.
- **Also at login:** a separate service puts the 80 % battery limit back.
- **Across SteamOS updates:** the system files it installs are registered in SteamOS's
  carry-forward list, so an atomic update keeps them.

### A rear lightbar that un-wedges itself

The rear lightbar's USB controller sometimes stops answering, with writes timing out. Z13-Helper
spots that and recovers on its own:
1. A tightly scoped root helper resets that one USB device, allowed by a polkit rule (no
   password).
2. The z13ctl daemon restarts, so it finds the device's new nodes.
3. The write is retried.

It never retries more than once every two minutes.

### Updates and installers

The **Updates** panel watches upstream projects and shows installed versus latest, with a note on
why each matters:
- z13ctl, Decky Loader and SteamOS, which it can also **install or update**: it shows the plan,
  keeps a backup and logs every step. z13ctl comes from a sha256-checked release; SteamOS goes
  through Valve's own `steamos-update`.
- The mainline kernel, as reference only: SteamOS ships its own kernel.
- Projects it only tracks, such as asusctl, InputPlumber, G-Helper, Handheld Daemon and community
  Z13 tools (see [Credits](#credits)).

### One-shot install, repair and health check

```bash
./install.sh            # full install; asks first (fetches the latest z13ctl and Decky Loader)
./install.sh --repair   # after a SteamOS update: no prompts, re-applies only what differs
./install.sh --dry-run  # shows what would change, writes nothing
python3 tooling/install_check.py verify   # just the green/red health report
```

The installer runs these steps in order:
1. z13ctl
2. Decky Loader
3. the Decky plugin
4. the Flatpak, rebuilt only when it differs from the repo
5. host scripts and user services
6. the `/etc` resume hook, the lightbar reset helper, and the carry-forward list
7. a **verification report**

Every replaced file is backed up first. The app's **Repair install** button runs the same
`--repair`.

---

## Requirements

- An **ASUS ROG Flow Z13 (2025, GZ302EA)**. Other models aren't supported; z13ctl is model-specific.
- **SteamOS 3.8** (tested on 3.8.28). Other Arch-based distros may work but are untested.
- The deck user in the `users` group, which z13ctl's device permissions use.
- **sudo**, for z13ctl setup, Decky and the `/etc` files.
- Network access for the first install, which pulls PyQt6 from PyPI into the Flatpak and the Decky
  plugin's build dependencies.
- **Node.js + pnpm** to build the Decky plugin (for example through `nvm`).

## Install

```bash
git clone https://github.com/iannitram/z13-helper.git
cd z13-helper
./install.sh
```

After a SteamOS update, run `./install.sh --repair` (or Updates → **Repair install** in the app).

**Upgrading from "Z13 Controller"** (the name before 0.5.0): `./install.sh --repair` migrates your
install. It moves your profiles and settings to the new folders and removes the old Flatpak,
service and plugin.

---

## How it works

| Concern | Mechanism |
|---|---|
| All hardware writes (profile, power limits, fan curve, battery limit, lighting, undervolt) | the [`z13ctl`](https://github.com/dahui/z13ctl) CLI and its user daemon; the Flatpak reaches the host with `flatpak-spawn --host` |
| Sensors | hwmon in sysfs, found by device name: `asus` (fans), `acpitz` (APU), `amdgpu` (GPU), `acpi_fan` |
| Firmware profile | `/sys/firmware/acpi/platform_profile` |
| Side button | evdev on `Asus WMI hotkeys` (`KEY_PROG3` = 202), read on the host |
| Game Mode menus | the Steam client's CEF debug socket (`MenuStore.OpenSideMenu`) |
| Screen brightness | KDE PowerDevil `org.kde.ScreenBrightness` over D-Bus, else `/sys/class/backlight` |
| One app instance | file lock + Unix socket in the Flatpak's runtime folder |
| Shared state | `~/.config/z13-helper/` (`profiles.json`, `max-fans.json`, `config.json`) |
| Logs | `~/.local/state/z13-helper/` (`app.log`, `crashes/`, `fan-trace-*.log`, install logs) |

```
main.py, src/            the desktop app (PyQt6) and the shared modules
                         (profiles engine, fan watchdog, sensors, installers, …)
decky/                   the Decky plugin (TypeScript UI + Python backend)
tooling/                 host scripts: install_check, z13-button-watcher, boondock, z13-* profiles
packaging/host/          systemd units, the resume hook, the lightbar reset helper, the polkit rule
flatpak/                 Flatpak manifest, desktop file, metainfo
tests/, decky/tests/     pytest + unittest suites (no hardware needed)
docs/                    ROADMAP, the side-button write-up, enablement notes, frozen phase history
```

Run the tests with `python3 -m pytest` and `python3 -m unittest discover -s decky/tests`.

---

## Credits

Z13-Helper is a thin layer over other people's work. Thank you to all of them.

**What it runs on**

- **[z13ctl](https://github.com/dahui/z13ctl)** by **dahui** (Apache-2.0): the CLI and daemon that
  do every hardware write: profiles, TDP, fan curves, battery limit, RGB, undervolt. It also
  ships the udev rules and permission setup that let the deck user control the hardware without
  root. Without it there is no Z13-Helper. A release tarball (with its license) is kept in
  `tooling/z13ctl-install/` as a known-good rollback. dahui's
  **[z13gui](https://github.com/dahui/z13gui)** is the reference GUI for z13ctl.
- **[G-Helper](https://github.com/seerge/g-helper)** by **seerge** (GPL-3.0): the inspiration for
  the name, the window layout, the mode tiles and the fan-curve editor. No code is shared;
  G-Helper is a Windows C# app.
- **The Linux kernel's ASUS drivers** (`asus-wmi`, `asus-nb-wmi`, `asus-armoury`) and the
  **[asus-linux](https://asus-linux.org)** community behind them and
  **[asusctl](https://gitlab.com/asus-linux/asusctl)**. They are why platform profiles, fan curves
  and charge limits exist in sysfs at all.
- **[SteamOS](https://store.steampowered.com/steamos)** by Valve. The Game Mode menus are opened
  through the Steam client's own UI code.

**Desktop app**
- **[PyQt6](https://www.riverbankcomputing.com/software/pyqt/)** by Riverbank Computing (GPL-3.0)
  and **[Qt 6](https://www.qt.io/)**: the whole desktop UI.
- **[Flatpak](https://flatpak.org/)**,
  **[flatpak-builder](https://github.com/flatpak/flatpak-builder)** and the
  **[KDE Flatpak runtime](https://invent.kde.org/packaging/flatpak-kde-runtime)**
  (`org.kde.Platform` 6.10): packaging and the runtime.
- **[KDE PowerDevil](https://invent.kde.org/plasma/powerdevil)**: the `org.kde.ScreenBrightness`
  API behind the screen brightness slider.

**Decky plugin**
- **[Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader)** (GPL-2.0) by the
  SteamDeckHomebrew team. `install.sh` installs it with their official
  **[decky-installer](https://github.com/SteamDeckHomebrew/decky-installer)**.
- **[@decky/ui](https://github.com/SteamDeckHomebrew/decky-frontend-lib)** (LGPL-2.1),
  **[@decky/api](https://github.com/SteamDeckHomebrew/loader-api)** (LGPL-2.1) and
  **[@decky/rollup](https://www.npmjs.com/package/@decky/rollup)** (BSD-3-Clause).
- **[React](https://react.dev/)**,
  **[react-icons](https://github.com/react-icons/react-icons)** (the fan icon is Font Awesome's,
  through react-icons), **[Rollup](https://rollupjs.org/)**,
  **[TypeScript](https://www.typescriptlang.org/)** and **[pnpm](https://pnpm.io/)**.

**Reference and inspiration** (consulted, watched in the Updates panel, not bundled)
- **[InputPlumber](https://github.com/ShadowBlip/InputPlumber)** by ShadowBlip (GPL-3.0). Its Z13
  device profile and schema are the basis of the disabling override in `packaging/host/asusd/`
  (reference only, not installed). Its documentation shaped the side-button investigation.
- **[Handheld Daemon](https://github.com/hhd-dev/hhd)**,
  **[z13ctl-plus](https://github.com/LostVector/z13ctl-plus)** (LostVector),
  **[z13-tablet-switch](https://github.com/m4rw3r/z13-tablet-switch)** (m4rw3r) and
  **[lightctl](https://github.com/Xcli0126/lightctl)** (Xcli0126; its lighting packet layout is
  noted for a future per-zone lighting feature).

**Tooling**
- **[pytest](https://docs.pytest.org/)** for the test suite.
- Written by Ian Martin with a lot of help from
  **[Claude Code](https://claude.com/claude-code)** (Anthropic).

If your project is used here and isn't credited, or is credited wrongly, please open an issue.
