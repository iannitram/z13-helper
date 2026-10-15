> **Historical (frozen 2026-10-09).** Superseded by [`docs/ROADMAP.md`](../ROADMAP.md); kept as a record of how the project got here.

# Phase 1: Research & Investigation

## Goals
- Investigate Fan Reading Issue in `z13ctl`.
- Identify Side Button Event via HID monitoring.
- Verify all `z13ctl` CLI syntax for backend implementation.

## Tasks
- [x] Explore `/sys/class/hwmon/` to identify temperature and fan sensors.
- [x] Compare `sysfs` raw values with `z13ctl status` output.
- [x] Use `evtest` or `python-evdev` on ASUS HID devices (`0b05:1a30`, `0b05:18c6`) to catch button events.
- [x] Document exact command-line arguments for all target hardware controls (profiles, battery, brightness, TDP).

## Findings
- `hwmon5` (name: `asus`) — `fan1_input`, `fan2_input` (main APU fans)
- `hwmon10` (name: `amdgpu`) — `temp1_input` "edge" (GPU die temp)
- `hwmon2` (name: `acpitz`) — `temp1_input` (APU/package thermal zone)
- Side button device: `/dev/input/event4` — "Asus WMI hotkeys" via `platform-asus-nb-wmi`
  - Requires `input` group membership + udev rule (see Phase 2 notes)
- `z13ctl` profile names: `balanced`, `performance`, `quiet`, `custom`
- `z13ctl` brightness levels: `off`, `low`, `medium`, `high`
- `z13ctl` battery range: 40–100
