# Decky imports root-owned copies of shared code

The Decky plugin's backend currently runs as `deck`: `plugin.json` says `"_root"`, while Decky Loader uses `"root"` to request a root backend. The shared `host.py` also supports a root backend by dropping host commands to the deck environment when requested. `install.sh` copies shared modules (`fan_watchdog.py`, `sensor_reader.py`, `host.py`, and `power_range.py`) from `src/` into the plugin's `py_modules/` as root-owned files, and `install_check` verifies they match the repo. Decky never imports these modules from a deck-writable location such as `~/.local/lib/z13-helper/` or the repo checkout. Root ownership keeps the installed code protected if the backend is ever configured to run as root. Where a module serves multiple frontends, it has separate deployment copies; the installer keeps the Decky copies in step with their source.

## Considered options

- **One deck-owned lib folder for everything:** fewer copies, but it gives deck a privilege escalation into root.
- **A symlink from the plugin to the repo:** the same problem, and it also breaks while the repo is mid-edit.
