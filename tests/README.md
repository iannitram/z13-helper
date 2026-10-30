# Tests

```
python3 -m pytest            # from the repo root; needs only the stdlib + pytest
python3 -m pytest -k watchdog -v
```

Runs anywhere (CI, a laptop, the Deck itself) without the Z13: no PyQt6, no
`z13ctl`, no sysfs, no network, no sudo. `conftest.py` puts `src/` and
`tooling/` on `sys.path` and installs an autouse guard that makes
`subprocess.run`/`Popen` and `urllib.request.urlopen` raise, so a test that
forgets to fake something fails instead of touching the machine (`host.run`
lets that error through rather than turning it into an rc).

| File | Covers |
|---|---|
| `test_profiles_apply.py` | `profiles.apply()`: write order, step independence, hooks, Max Fans state suspension, undervolt |
| `test_profiles_store.py` | store seeding/CRUD, `validate`, `apply_by_name`, the `z13-profile` CLI |
| `test_fan_watchdog.py` | recovery rules, throttle, stale queued recovery cancellation, in-flight Max Fans cancellation, and "profile rewrite before curve" |
| `test_power_range.py` | per-rail bounds from kernel metadata and fallback ranges |
| `test_sensor_reader.py` | hwmon discovery by name and the custom-curve readers, on a fake sysfs tree |
| `test_max_fans_state.py` | the shared `max-fans.json` (atomic merge, corrupt/missing file) |
| `test_install_check.py` | the manifest `install.sh` reads, `keep-list`, `flatpak_diff`, restore grading |
| `test_installers.py` | CLI dispatch, release checksum checks, `Z13_INSTALL_DRY_RUN=1` repair paths |
| `test_host.py` | `host.py`: mode detection, each mode's argv/env (incl. the `LD_LIBRARY_PATH` restore), `run()` never raising |
| `test_hardware_controller.py` | per-device brightness, wedged-lightbar reset + single retry, command timeouts |
| `test_lightbar_reset_script.py` | `z13-lightbar-reset.sh` against a fake `/sys/bus/usb/devices` tree (runs `sh`) |

Fixtures worth knowing: `profiles_env` (every profiles/max-fans path in
`tmp_path`), `fake_host` (replaces `host.run`: records each call's argv, timeout,
`as_deck` and input, and answers from `.fail(match, rc, out, times=)` rules; the
class lives in `fake_host.py` and the Decky suite uses it too), `clock` (a
settable `time.monotonic`), `make_hwmon`/`curve_files` for fake sysfs nodes, and
`real_subprocess` (explicit opt-out of the guard, only for running the repo's own
shell scripts against a tree in `tmp_path`).

The Decky backend has its own unittest suite: `python3 -m unittest discover -s decky/tests`
(CI runs both).

Not covered here (needs the device): real z13ctl/firmware behaviour, the Qt UI,
the evdev side-button reader, the Decky plugin, and `install.sh` itself. Unit tests
check watchdog state transitions and trace formatting; they do not prove physical
fan rotation or every Desktop/Game Mode, resume, and cold-boot transition. Those
device checks are tracked in `docs/ROADMAP.md` §12.
