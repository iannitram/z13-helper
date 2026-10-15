# Decky imports root-owned copies of shared code

The Decky plugin's backend may run as root (it doesn't today: `plugin.json` says `"_root"`, and Decky Loader only honours `"root"`, so it runs as deck; the code works either way). It shares modules with the desktop app (first `fan_watchdog.py` and `sensor_reader.py`). `install.sh` copies them from `src/` into the plugin's `py_modules/`, owned by root, and `install_check` verifies they match the repo. Decky never imports from a deck-writable location such as `~/.local/lib/z13-helper/` or the repo checkout, because if it ever runs as root, anything running as deck could change code that root executes. So code shared with Decky exists on disk twice (the root-owned copy, plus the Flatpak's or the host CLI's). The installer keeps both in step, so don't merge them into a single deck-owned copy.

## Considered options

- **One deck-owned lib folder for everything:** fewer copies, but it gives deck a privilege escalation into root.
- **A symlink from the plugin to the repo:** the same problem, and it also breaks while the repo is mid-edit.
