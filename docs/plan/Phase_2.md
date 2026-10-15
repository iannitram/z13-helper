> **Historical (frozen 2026-10-09).** Superseded by [`docs/ROADMAP.md`](../ROADMAP.md); kept as a record of how the project got here.

# Phase 2: Backend Development (Python)

## Goals
- Create a robust hardware controller module and background listener.

## Tasks
- [x] Develop `HardwareController` Python class to wrap `z13ctl` calls.
- [x] Implement direct `sysfs` reading for accurate sensor telemetry (RPM, Temp).
- [x] Build an `EvdevListener` daemon to detect the side button hardware event.
- [x] Create a configuration manager for persisting user settings (e.g., battery limit).

## Notes
- `HardwareController` auto-resolves `~/.local/bin/z13ctl`; exposes get/set for profile, battery limit, brightness, and full status.
- `SensorReader` discovers hwmon nodes by name (not hardcoded index) — robust across reboots.
- `EvdevListener` uses raw `struct` reads from `/dev/input/` — no `evdev` package needed (kernel headers unavailable on SteamOS immutable root).
- **Pending permission setup** (one-time, requires sudo):
  ```bash
  sudo sh -c 'echo "SUBSYSTEM==\"input\", ENV{ID_PATH}==\"platform-asus-nb-wmi\", GROUP=\"input\", MODE=\"0660\"" > /etc/udev/rules.d/99-z13-controller.rules'
  sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=input
  sudo usermod -aG input deck   # log out and back in after this
  ```
