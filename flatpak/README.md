# Z13-Helper — Flatpak packaging

This directory holds the Flatpak manifest and supporting files. The build
produces `io.github.iannitram.Z13Helper`, an installable Flatpak that wraps the
PyQt6 app.

## What's here

| File | Purpose |
|---|---|
| `io.github.iannitram.Z13Helper.yml` | flatpak-builder manifest |
| `io.github.iannitram.Z13Helper.desktop` | XDG desktop entry installed inside the sandbox |
| `io.github.iannitram.Z13Helper.metainfo.xml` | AppStream metadata |
| `launch.sh` | tiny shim that `exec`s `python3 main.py` inside the sandbox |
| `README.md` | this file |

## Prerequisites

```bash
# flatpak-builder, KDE 6.10 platform + SDK
flatpak install --user --noninteractive flathub \
    org.flatpak.Builder \
    org.kde.Platform//6.10 \
    org.kde.Sdk//6.10
```

## Build & install (one shot)

```bash
cd z13-helper   # the repo root
flatpak run org.flatpak.Builder \
    --user --install --force-clean \
    build-flatpak \
    flatpak/io.github.iannitram.Z13Helper.yml
```

The build downloads PyQt6 from PyPI (the manifest sets `--no-build-isolation`
so it uses the runtime's Python). First build is ~10–20 minutes — subsequent
builds reuse the cache.

If your environment blocks network during build, the manifest needs to be
extended with offline pip wheels (URLs + sha256). Skipped here for brevity.

## Run

```bash
flatpak run io.github.iannitram.Z13Helper            # tray-only, hidden
flatpak run io.github.iannitram.Z13Helper --show     # window visible
```

It also appears in the KDE app launcher under *System / Hardware Settings*.

## Why these `finish-args` permissions?

| Flag | Reason |
|---|---|
| `--share=ipc` `--socket=wayland` `--socket=fallback-x11` `--device=dri` | Standard Qt GUI |
| `--talk-name=org.freedesktop.Notifications` | Tray notifications |
| `--talk-name=org.kde.StatusNotifierWatcher` | Tray icon on Plasma |
| `--system-talk-name=org.shadowblip.InputPlumber` | Side-button D-Bus signals + setting `InterceptMode` / `SetTargetDevices` |
| `--filesystem=/sys:ro` | Direct hwmon reads (`SensorReader` walks `/sys/class/hwmon/`) |
| `--filesystem=xdg-config/z13-helper:create` | Persisted config at `~/.var/app/io.github.iannitram.Z13Helper/config/z13-helper/` (XDG-compliant inside the sandbox) |
| `--talk-name=org.freedesktop.Flatpak` `--filesystem=~/.local/bin:ro` | `flatpak-spawn --host z13ctl …` — the host binary needs to be reachable from inside the sandbox |

## How the sandbox reaches host hardware

`z13ctl` lives on the host because it touches asus-wmi sysfs, hidraw RGB
devices, ryzen_smu MSRs, ACPI power_supply, and asus-armoury firmware
attributes — none of which are accessible from a sandbox no matter what
permissions you grant.

The trick: `flatpak-spawn --host` runs a command on the host with the host's
permissions, returning stdout/stderr to the sandboxed caller. The
`HardwareController` detects it's running inside Flatpak (via
`/.flatpak-info`) and prepends `flatpak-spawn --host` to every `z13ctl`
invocation. From the host's perspective it's the same as running `z13ctl` in
a terminal.

System D-Bus access (for InputPlumber) goes through Flatpak's
`xdg-dbus-proxy`, filtered by the `--system-talk-name` rule.

## Caveats

- **Autostart** — Flatpak apps don't get an XDG autostart entry by default.
  Either manually drop `~/.config/autostart/io.github.iannitram.Z13Helper.desktop`
  pointing at `flatpak run io.github.iannitram.Z13Helper`, or keep using the
  source-based autostart (the two installations don't conflict).
- **App ID** is a placeholder — `io.github.iannitram.Z13Helper` would normally map
  to a real GitHub Pages site. Replace with whatever real reverse-DNS the
  project ends up at before any public distribution.
- **Not Flathub-ready.** This manifest is tuned for `flatpak-builder --user`
  on a known machine. Flathub submission would need offline-pinned wheels,
  no `--talk-name=org.freedesktop.Flatpak` (Flathub forbids host execution
  for security review reasons), and the entire architecture would have to
  change to bundle z13ctl or talk to a separate system service.
- **Two installs side by side.** The Flatpak version and the source-based
  install (`~/.local/bin/z13-helper`) both work. Different config paths
  (`~/.var/app/.../config/...` vs `~/.config/...`) so they don't fight.
