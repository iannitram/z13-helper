# Tests

```
python3 -m pytest            # from the repo root; needs only the stdlib + pytest
python3 -m pytest -k watchdog -v
```

On the SteamOS host the system Python 3.14 has no `pytest` (`No module named pytest`), so run the same
commands from a virtualenv that has it, with a throwaway `HOME`: `AGENTS.md` "Running the tests" gives the
exact lines. The Decky suite is stdlib only and runs with the host `python3`.

The table below describes the main suites; it does not list every `tests/test_*.py` file (`ls tests` is the full list).

Runs anywhere (CI, a laptop, the Deck itself) without the Z13: no PyQt6, no
`z13ctl`, no sysfs, no network, no sudo. `conftest.py` puts `src/` and
`tooling/` on `sys.path` and installs an autouse guard that makes
`subprocess.run`/`Popen`, `urllib.request.urlopen` and HTTP opener calls raise, so a test that
forgets to fake something fails instead of touching the machine (`host.run`
lets that error through rather than turning it into an rc).

| File | Covers |
|---|---|
| `test_verify_gamescope_vrr.py` | Read-only #73 preflight: exact EDID identity, installed script drift, OS/Game Mode context and pending VRR/limiter matrix; no physical acceptance inferred. |
| `test_crash_wiring.py` | exception hooks, scrubbed uploads, consent, shared queue/rate limit, fatal recovery and build configuration; see `docs/crash-reporting.md` |
| `test_timeline_r5.py` | review fixes for #134: deferred recording off the hardware lock, read-only Decky Max Fans snapshot, watchdog start not waiting on the timeline, folder held by descriptor, no descriptor leak, no rollback for unknown values, logged failures |
| `test_timeline.py` | append-only `timeline.jsonl`, size-capped rotation, partly written last line handling, Principle 7 journal lines, and the five write paths |
| `test_profiles_apply.py` | `profiles.apply()`: write order, step independence, hooks, Max Fans state suspension, undervolt |
| `test_profiles_store.py` | store seeding/CRUD, `validate`, `apply_by_name`, the `z13-profile` CLI |
| `test_backend_memory.py` | Decky RSS threshold, read errors, independent monitor and plugin startup/unload; no real exit or hardware |
| `test_fan_trace.py` | Trace output, replay and history bounded by both time and entry count |
| `test_z13_helper_cli.py` | `z13-helper` argparse surface, `--json` on every command, and dispatch through a faked state owner (profile, TDP, fans, battery, brightness, lighting, crash and post-update) |
| `test_mcp_server.py` | `z13-helper mcp`: tool list/schemas, dispatch through the same CLI and faked state owner, argument validation, and a JSON-RPC round trip on fake stdio |
| `test_fan_watchdog.py` | recovery rules, throttle, stale queued recovery cancellation, in-flight Max Fans cancellation, and "profile rewrite before curve" |
| `test_power_range.py` | per-rail bounds from kernel metadata and fallback ranges |
| `test_compute_health.py` | memory pools, gfx identification, runtime discovery and the bounded smoke test (host faked) |
| `test_telemetry_history.py` | bounded telemetry ring buffer: age and count caps, gaps, no disk writes |
| `test_display_modes.py` | kscreen-doctor mode parsing, advertised-only refresh choices, read-back and rollback (host faked) |
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

Issue #23 state integration: `test_state_authority.py` covers named mapping/apply,
transaction isolation, stale fields, CLI reconciliation, capability failures and
watchdog targets. `test_z13ctl_client.py` uses a local fake Unix daemon for the
wire protocol and reconnect snapshots. `test_state_stream.py` checks record
delivery and idle-process shutdown. `test_profile_event_wiring.py` uses a small
signal stand-in to exercise the production desktop adapter without PyQt6;
`test_profile_control_events.py` exercises the shared callback dispatcher.
