# CPU Boost (EPP) is written by the desktop user through a tmpfiles grant

Issue #64 adds a CPU Boost choice (amd-pstate `energy_performance_preference`, EPP) to profiles. The app must write it, and `z13ctl` cannot: it has no EPP command, and every host command in this app goes through `src/host.py` as the desktop user. Nothing else in the install runs commands as root at profile-apply time, and adding a polkit rule or a root helper for one sysfs attribute would be a larger privilege surface than the attribute itself.

So `packaging/host/tmpfiles/z13-helper-epp.conf` makes the EPP attribute of each cpufreq policy group-writable (`0664 root users`). `install.sh` and `--repair` install it and apply it at once; `systemd-tmpfiles` reapplies it at boot. `profiles.epp_argv` then writes with a plain `sh` through `host.run`, no elevation.

## Scope of the widening

- Only `/sys/devices/system/cpu/cpufreq/policy*/energy_performance_preference`. No other sysfs file, no `scaling_governor`, no frequency limits.
- Group `users` only (the desktop account on SteamOS). Other accounts stay read-only.
- Effect: any user in `users` can pick one of four EPP hints, which the kernel validates. That can change power and performance, but cannot damage hardware or escalate privilege. Whether the firmware platform profile resets EPP on its own is expected but not yet checked on the device.
- Missing policies (no amd-pstate) are skipped by `systemd-tmpfiles`; the app reports "no node written" instead of success.
- Read and write both use the policy directories (`epp.policy_paths`, the shell glob in `profiles.EPP_SYSFS_GLOB`), never `cpu{n}`, because a policy number is not a CPU number.

## Considered options

- **A `z13ctl` EPP command:** upstream has none; waiting on it blocks #64.
- **A root helper or polkit rule:** more code and a bigger attack surface than a four-value sysfs attribute.
- **Writing `/sys` from the Flatpak directly:** the sandbox cannot write sysfs.
