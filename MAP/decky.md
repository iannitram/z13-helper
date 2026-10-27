status: unreviewed (verifier-passed, qwen3-coder-30b-a3b-instruct, 2026-10-20)

# Script half: decky @ 0fad07409b5bb3ee54b47fd2a16c711ba7224052

## Files
- `decky/main.py` — 1120 lines, blob `dc6de26c9c35116382dc8e5f429f721f6e16d009`
- `decky/src/backend.ts` — 94 lines, blob `567adced2a5733ceede29904ea8f2766ed726853`
- `decky/src/components/BatteryLimit.tsx` — 58 lines, blob `a4f748696a2c4e84bf186cc01eb57b1c8836794a`
- `decky/src/components/KeyboardBrightness.tsx` — 75 lines, blob `0e214fda4ace9c241f407da33ea2015ecb0e31dd`
- `decky/src/components/PowerLimits.tsx` — 115 lines, blob `424b12d68e0d7d93b45a0bffd774e41c0a1ec124`
- `decky/src/components/Telemetry.tsx` — 34 lines, blob `c615b7fe4c4cad057b26cdfcb20796c8a301ae59`
- `decky/src/hooks.ts` — 112 lines, blob `c1a8ec562e9e225faebf849e972b808449c2ba98`
- `decky/src/index.tsx` — 132 lines, blob `cf1089c033124326a333cde2316e59b8de0145d4`
- `decky/src/profileSwitch.ts` — 32 lines, blob `d4c4b3d791d115f15c9c085a205fd1ca7c6bca5d`
- `decky/tests/test_backend.py` — 904 lines, blob `2014558a86371406c136e47288076ea7a82d31a0`
- `decky/tests/profileSwitch.test.ts` — 68 lines, blob `e2b3481e82fdb1054fb900645fce08655eff43f5`

## Public items: `decky/main.py`
- `decky/main.py::262` class `StoreLockError` — `class StoreLockError(OSError)`
- `decky/main.py::436` def `tdp_ranges` — `def tdp_ranges()`
- `decky/main.py::442` def `clamp_tdp` — `def clamp_tdp(pl1, pl2, pl3, ranges=None)`
- `decky/main.py::452` def `one` — `def one(v, k)`
- `decky/main.py::461` def `clamp_battery` — `def clamp_battery(value)`
- `decky/main.py::467` def `tdp_argv` — `def tdp_argv(tdp)`
- `decky/main.py::497` def `active_profile` — `def active_profile(store)`
- `decky/main.py::508` def `update_active_profile` — `def update_active_profile(store, **fields)`
- `decky/main.py::524` def `write_store_atomic` — `def write_store_atomic(store, path=None)`
- `decky/main.py::582` def `telemetry` — `def telemetry()`
- `decky/main.py::592` def `lightbar_failure_kind` — `def lightbar_failure_kind(out)`
- `decky/main.py::602` def `lightbar_timed_out` — `def lightbar_timed_out(out)`
- `decky/main.py::624` def `restart_lightbar_daemon` — `def restart_lightbar_daemon(*, sleep=time.sleep)`
- `decky/main.py::635` def `recover_lightbar` — `def recover_lightbar(*, sleep=time.sleep, now=time.time, stamp=None)`
- `decky/main.py::661` def `recover_lightbar_stale` — `def recover_lightbar_stale(*, sleep=time.sleep, now=time.time, stamp=None)`
- `decky/main.py::673` def `set_brightness_per_device` — `def set_brightness_per_device(level, recover=None, recover_stale=None)`
- `decky/main.py::733` def `power_state` — `def power_state()`
- `decky/main.py::762` def `apply_tdp` — `def apply_tdp(enabled, pl1, pl2, pl3, profile=None)`
- `decky/main.py::790` def `apply_battery_limit` — `def apply_battery_limit(value, profile=None)`
- `decky/main.py::806` def `max_fans_off_argv` — `def max_fans_off_argv(state, store)`
- `decky/main.py::822` def `set_max_fans_blocking` — `def set_max_fans_blocking(enabled)`
- `decky/main.py::838` class `DeckyFans` — `class DeckyFans`
- `decky/main.py::850` def `rewrite_profile` — `def rewrite_profile(self)`
- `decky/main.py::858` def `restore_tdp` — `def restore_tdp(self)`
- `decky/main.py::867` def `restore_undervolt` — `def restore_undervolt(self)`
- `decky/main.py::880` def `set_fans_max` — `def set_fans_max(self)`
- `decky/main.py::883` def `set_fancurve_pwm` — `def set_fancurve_pwm(self, points)`
- `decky/main.py::886` def `apply_tdp` — `def apply_tdp(self, tdp)`
- `decky/main.py::890` def `reset_fancurve` — `def reset_fancurve(self)`
- `decky/main.py::915` class `DesktopAppCheck` — `class DesktopAppCheck`
- `decky/main.py::931` def `check` — `def check(self)`
- `decky/main.py::955` def `logging_enabled` — `def logging_enabled()`
- `decky/main.py::964` def `make_fan_trace` — `def make_fan_trace(path=None, owner=DECK_IDS)`
- `decky/main.py::980` def `make_fan_watchdog` — `def make_fan_watchdog(sensors=None, fans=None, may_act=None, clock=time.monotonic, trace=None)`
- `decky/main.py::988` def `fan_watchdog_tick` — `async def fan_watchdog_tick(watchdog)`
- `decky/main.py::1016` class `Plugin` — `class Plugin`
- `decky/main.py::1018` def `get_max_fans` — `async def get_max_fans(self) -> bool`
- `decky/main.py::1021` def `set_max_fans` — `async def set_max_fans(self, enabled: bool) -> bool`
- `decky/main.py::1048` def `get_profiles` — `async def get_profiles(self) -> dict`
- `decky/main.py::1061` def `set_app_profile` — `async def set_app_profile(self, name: str) -> bool`
- `decky/main.py::1071` def `get_profile` — `async def get_profile(self) -> str`
- `decky/main.py::1074` def `set_profile` — `async def set_profile(self, profile: str) -> bool`
- `decky/main.py::1081` def `get_power_state` — `async def get_power_state(self) -> dict`
- `decky/main.py::1085` def `set_tdp` — `async def set_tdp(self, enabled: bool, pl1: int, pl2: int, pl3: int,`
- `decky/main.py::1090` def `set_battery_limit` — `async def set_battery_limit(self, value: int, profile: str = "") -> dict`
- `decky/main.py::1094` def `set_brightness` — `async def set_brightness(self, level: str) -> dict`
- `decky/main.py::1101` def `get_telemetry` — `async def get_telemetry(self) -> dict`

## Public items: `decky/src/backend.ts`
- `decky/src/backend.ts::6` const `getMaxFans` — `export const getMaxFans = callable<[], boolean>("get_max_fans");`
- `decky/src/backend.ts::7` const `setMaxFans` — `export const setMaxFans = callable<[enabled: boolean], boolean>("set_max_fans");`
- `decky/src/backend.ts::8` const `getProfile` — `export const getProfile = callable<[], string>("get_profile");`
- `decky/src/backend.ts::9` const `setProfile` — `export const setProfile = callable<[profile: string], boolean>("set_profile");`
- `decky/src/backend.ts::12` const `getProfiles` — `export const getProfiles = callable<[], { names: string[]; active: string }>("get_profiles");`
- `decky/src/backend.ts::13` const `setAppProfile` — `export const setAppProfile = callable<[name: string], boolean>("set_app_profile");`
- `decky/src/backend.ts::16` type `BrightnessLevel` — `export type BrightnessLevel = "off" | "low" | "medium" | "high";`
- `decky/src/backend.ts::18` interface `PowerState` — `export interface PowerState`
- `decky/src/backend.ts::33` interface `ApplyResult` — `export interface ApplyResult`
- `decky/src/backend.ts::40` interface `TdpResult` — `export interface TdpResult extends ApplyResult`
- `decky/src/backend.ts::44` interface `BrightnessResult` — `export interface BrightnessResult extends ApplyResult`
- `decky/src/backend.ts::48` interface `Telemetry` — `export interface Telemetry`
- `decky/src/backend.ts::55` const `getPowerState` — `export const getPowerState = callable<[], PowerState>("get_power_state");`
- `decky/src/backend.ts::56` const `setTdp` — `export const setTdp = callable<`
- `decky/src/backend.ts::60` const `setBatteryLimit` — `export const setBatteryLimit = callable<`
- `decky/src/backend.ts::66` const `setBrightness` — `export const setBrightness = callable<[level: BrightnessLevel], BrightnessResult>("set_brightness");`
- `decky/src/backend.ts::67` const `getTelemetry` — `export const getTelemetry = callable<[], Telemetry>("get_telemetry");`
- `decky/src/backend.ts::71` function `clampTdp` — `export function clampTdp(`
- `decky/src/backend.ts::88` function `safely` — `export async function safely<T extends ApplyResult>(p: Promise<T>): Promise<T | ApplyResult>`

## Public items: `decky/src/components/BatteryLimit.tsx`
- `decky/src/components/BatteryLimit.tsx::7` function `BatteryLimit` — `export function BatteryLimit({ state }: { state: PowerState | null })`

## Public items: `decky/src/components/KeyboardBrightness.tsx`
- `decky/src/components/KeyboardBrightness.tsx::16` function `KeyboardBrightness` — `export function KeyboardBrightness({ state }: { state: PowerState | null })`

## Public items: `decky/src/components/PowerLimits.tsx`
- `decky/src/components/PowerLimits.tsx::19` function `PowerLimits` — `export function PowerLimits({ state }: { state: PowerState | null })`

## Public items: `decky/src/components/Telemetry.tsx`
- `decky/src/components/Telemetry.tsx::12` function `TelemetryRow` — `export function TelemetryRow()`

## Public items: `decky/src/hooks.ts`
- `decky/src/hooks.ts::7` function `cancelPendingCommits` — `export function cancelPendingCommits()`
- `decky/src/hooks.ts::21` function `useCommitOnRelease` — `export function useCommitOnRelease<T>(commit: (v: T) => void, settleMs = 700)`
- `decky/src/hooks.ts::102` function `usePollWhileVisible` — `export function usePollWhileVisible(fn: () => void, ms: number)`

## Public items: `decky/src/profileSwitch.ts`
- `decky/src/profileSwitch.ts::3` interface `SwitchDeps` — `export interface SwitchDeps`
- `decky/src/profileSwitch.ts::9` interface `SwitchResult` — `export interface SwitchResult`
- `decky/src/profileSwitch.ts::22` function `switchProfile` — `export async function switchProfile(deps: SwitchDeps, name: string): Promise<SwitchResult>`

## Public items: `decky/tests/test_backend.py`
- `decky/tests/test_backend.py::77` def `fake_host` — `def fake_host(test, fail_if=(), out=FAIL_OUT)`
- `decky/tests/test_backend.py::89` class `TdpRangesTest` — `class TdpRangesTest(unittest.TestCase)`
- `decky/tests/test_backend.py::90` def `test_clamp_per_rail_with_armoury_ranges` — `def test_clamp_per_rail_with_armoury_ranges(self)`
- `decky/tests/test_backend.py::94` def `test_tdp_ranges_runs_no_subprocess` — `def test_tdp_ranges_runs_no_subprocess(self)`
- `decky/tests/test_backend.py::100` class `ClampTdpTest` — `class ClampTdpTest(unittest.TestCase)`
- `decky/tests/test_backend.py::101` def `test_in_range_unchanged` — `def test_in_range_unchanged(self)`
- `decky/tests/test_backend.py::105` def `test_bounds` — `def test_bounds(self)`
- `decky/tests/test_backend.py::109` def `test_spl_pushes_sppt_and_fppt_up` — `def test_spl_pushes_sppt_and_fppt_up(self)`
- `decky/tests/test_backend.py::113` def `test_fppt_never_below_sppt` — `def test_fppt_never_below_sppt(self)`
- `decky/tests/test_backend.py::117` def `test_force_only_above_75` — `def test_force_only_above_75(self)`
- `decky/tests/test_backend.py::121` def `test_floats_and_strings_round` — `def test_floats_and_strings_round(self)`
- `decky/tests/test_backend.py::126` def `test_tdp_argv` — `def test_tdp_argv(self)`
- `decky/tests/test_backend.py::132` class `ClampBatteryTest` — `class ClampBatteryTest(unittest.TestCase)`
- `decky/tests/test_backend.py::133` def `test_steps_and_bounds` — `def test_steps_and_bounds(self)`
- `decky/tests/test_backend.py::141` class `StoreTest` — `class StoreTest(unittest.TestCase)`
- `decky/tests/test_backend.py::142` def `setUp` — `def setUp(self)`
- `decky/tests/test_backend.py::148` def `tearDown` — `def tearDown(self)`
- `decky/tests/test_backend.py::159` def `test_update_active_only` — `def test_update_active_only(self)`
- `decky/tests/test_backend.py::168` def `test_update_rejects_unknown_fields` — `def test_update_rejects_unknown_fields(self)`
- `decky/tests/test_backend.py::172` def `test_update_without_active_raises` — `def test_update_without_active_raises(self)`
- `decky/tests/test_backend.py::176` def `test_atomic_write_roundtrip` — `def test_atomic_write_roundtrip(self)`
- `decky/tests/test_backend.py::183` def `test_schema_keys_preserved` — `def test_schema_keys_preserved(self)`
- `decky/tests/test_backend.py::194` def `test_apply_tdp_on_writes_store_then_z13ctl` — `def test_apply_tdp_on_writes_store_then_z13ctl(self)`
- `decky/tests/test_backend.py::206` def `test_apply_tdp_off_clears_and_reapplies_profile` — `def test_apply_tdp_off_clears_and_reapplies_profile(self)`
- `decky/tests/test_backend.py::219` def `test_apply_tdp_hw_failure_still_saved` — `def test_apply_tdp_hw_failure_still_saved(self)`
- `decky/tests/test_backend.py::226` def `test_apply_tdp_no_store_touches_nothing` — `def test_apply_tdp_no_store_touches_nothing(self)`
- `decky/tests/test_backend.py::237` def `test_written_profile_passes_engine_validation` — `def test_written_profile_passes_engine_validation(self)`
- `decky/tests/test_backend.py::253` def `test_apply_tdp_for_a_stale_profile_is_rejected` — `def test_apply_tdp_for_a_stale_profile_is_rejected(self)`
- `decky/tests/test_backend.py::266` def `test_apply_battery_limit_for_a_stale_profile_is_rejected` — `def test_apply_battery_limit_for_a_stale_profile_is_rejected(self)`
- `decky/tests/test_backend.py::276` def `test_targeted_request_for_the_active_profile_is_applied` — `def test_targeted_request_for_the_active_profile_is_applied(self)`
- `decky/tests/test_backend.py::283` def `test_apply_battery_limit` — `def test_apply_battery_limit(self)`
- `decky/tests/test_backend.py::294` class `FileLockSafetyTest` — `class FileLockSafetyTest(unittest.TestCase)`
- `decky/tests/test_backend.py::297` def `setUp` — `def setUp(self)`
- `decky/tests/test_backend.py::308` def `test_symlinked_lock_is_refused` — `def test_symlinked_lock_is_refused(self)`
- `decky/tests/test_backend.py::315` def `test_hardlinked_lock_is_refused` — `def test_hardlinked_lock_is_refused(self)`
- `decky/tests/test_backend.py::322` def `test_held_lock_times_out` — `def test_held_lock_times_out(self)`
- `decky/tests/test_backend.py::343` def `test_lock_file_owned_by_current_user_is_accepted` — `def test_lock_file_owned_by_current_user_is_accepted(self)`
- `decky/tests/test_backend.py::352` def `test_normal_use_creates_plain_lock_file` — `def test_normal_use_creates_plain_lock_file(self)`
- `decky/tests/test_backend.py::358` class `BrightnessTest` — `class BrightnessTest(unittest.TestCase)`
- `decky/tests/test_backend.py::359` def `test_one_call_per_device` — `def test_one_call_per_device(self)`
- `decky/tests/test_backend.py::366` def `test_wedged_lightbar_does_not_fail_keyboard` — `def test_wedged_lightbar_does_not_fail_keyboard(self)`
- `decky/tests/test_backend.py::377` def `test_all_failed` — `def test_all_failed(self)`
- `decky/tests/test_backend.py::384` def `test_bad_level_runs_nothing` — `def test_bad_level_runs_nothing(self)`
- `decky/tests/test_backend.py::390` class `LightbarRecoveryTest` — `class LightbarRecoveryTest(unittest.TestCase)`
- `decky/tests/test_backend.py::393` def `setUp` — `def setUp(self)`
- `decky/tests/test_backend.py::399` def `recover` — `def recover(self, now=1000.0)`
- `decky/tests/test_backend.py::402` def `recover_stale` — `def recover_stale(self, now=1000.0)`
- `decky/tests/test_backend.py::405` def `test_timeout_gets_one_reset_daemon_restart_and_one_retry_keyboard_first` — `def test_timeout_gets_one_reset_daemon_restart_and_one_retry_keyboard_first(self)`
- `decky/tests/test_backend.py::426` def `test_stale_gets_one_daemon_restart_and_one_retry_no_usb_reset` — `def test_stale_gets_one_daemon_restart_and_one_retry_no_usb_reset(self)`
- `decky/tests/test_backend.py::443` def `test_unrecognised_failure_no_recovery` — `def test_unrecognised_failure_no_recovery(self)`
- `decky/tests/test_backend.py::449` def `test_still_wedged_after_reset_retries_only_once` — `def test_still_wedged_after_reset_retries_only_once(self)`
- `decky/tests/test_backend.py::458` def `test_still_stale_after_daemon_restart_retries_only_once` — `def test_still_stale_after_daemon_restart_retries_only_once(self)`
- `decky/tests/test_backend.py::467` def `test_rate_limit` — `def test_rate_limit(self)`
- `decky/tests/test_backend.py::478` def `test_stale_rate_limit` — `def test_stale_rate_limit(self)`
- `decky/tests/test_backend.py::489` def `test_reset_unit_failure_is_reported` — `def test_reset_unit_failure_is_reported(self)`
- `decky/tests/test_backend.py::496` def `test_daemon_restart_failure_is_reported_but_reset_still_counts_as_ok` — `def test_daemon_restart_failure_is_reported_but_reset_still_counts_as_ok(self)`
- `decky/tests/test_backend.py::502` def `test_stale_daemon_restart_failure_means_not_ok` — `def test_stale_daemon_restart_failure_means_not_ok(self)`
- `decky/tests/test_backend.py::508` def `test_matches_engine_constants` — `def test_matches_engine_constants(self)`
- `decky/tests/test_backend.py::524` class `MaxFansOffTest` — `class MaxFansOffTest(unittest.TestCase)`
- `decky/tests/test_backend.py::528` def `test_saved_snapshot_wins` — `def test_saved_snapshot_wins(self)`
- … 53 more public items (not listed)

## Include / import edges (outgoing, this unit's own files)
- `decky/main.py`: `asyncio`, `contextlib`, `decky`, `errno`, `fan_watchdog`, `fcntl`, `host`, `json`, `os`, `power_range`, `re`, `sensor_reader`, `socket`, `stat`, `sys`, `threading`, `time`
- `decky/src/backend.ts`: `@decky/api`
- `decky/src/components/BatteryLimit.tsx`: `../backend`, `../hooks`, `@decky/ui`, `react`
- `decky/src/components/KeyboardBrightness.tsx`: `../backend`, `@decky/ui`, `react`
- `decky/src/components/PowerLimits.tsx`: `../backend`, `../hooks`, `@decky/ui`, `react`
- `decky/src/components/Telemetry.tsx`: `../backend`, `../hooks`, `@decky/ui`, `react`
- `decky/src/hooks.ts`: `@decky/api`, `react`
- `decky/src/index.tsx`: `./backend`, `./profileSwitch`, `./components/BatteryLimit`, `./components/KeyboardBrightness`, `./components/PowerLimits`, `./components/Telemetry`, `./hooks`, `@decky/api`, `react`, `react-icons/fa`
- `decky/src/profileSwitch.ts`: (none)
- `decky/tests/profileSwitch.test.ts`: `../src/profileSwitch.ts`, `node:assert/strict`, `node:test`
- `decky/tests/test_backend.py`: `asyncio`, `fake_host`, `fan_watchdog`, `host`, `importlib.util`, `json`, `logging`, `os`, `power_range`, `sensor_reader`, `socket`, `sys`, `tempfile`, `threading`, `types`, `unittest`

## Top callers (rg -w, repo-wide, this unit's own files excluded)
### `Plugin`
    ./docs/plan/Phase_5.md:3:# Phase 5: Game Mode Support via Decky Plugin
    ./docs/plan/Phase_5.md:73:- **Plugin source lives in the repo at `decky/`**, installs as `~/homebrew/plugins/z13-decky`.
    ./docs/ROADMAP.md:300:- Plugin: pnpm found on PATH or under `~/.nvm` (falls back to the existing `decky/dist`); installed only
### `StoreLockError`
    ./src/max_fans_state.py:34:class StoreLockError(OSError):
    ./src/max_fans_state.py:55:                raise StoreLockError(f"{lock_path} is a symlink; refusing") from e
    ./src/max_fans_state.py:60:            raise StoreLockError(f"{lock_path} is not a plain single-link file; refusing")
### `Telemetry`
    ./src/dashboard_ui.py:741:    # Telemetry tick
### `apply_tdp`
    ./src/profiles.py:673:def apply_tdp(profile, *, z13ctl=Z13CTL):
    ./src/profiles.py:732:    results.extend(apply_tdp(profile, z13ctl=z13ctl))
    ./src/hardware_controller.py:81:        for _, ok, msg in profiles.apply_tdp(p, z13ctl=self.z13ctl_path):
### `check`
    ./CLAUDE.md:106:  z13ctl version). `--dry-run` writes nothing. Health check alone: `python3 tooling/install_check.py verify`
    ./docs/maps/goal-1.md:69:- Remove `--pause-vm` from the usage string (line 108) and the flag check (line 36).
    ./docs/ROADMAP.md:20:| Decky plugin `z13-helper` | ✅ profile dropdown, Max Fans, TDP sliders, charge limit, per-device brightness, telemetry (§7; on-device check pending) |
### `custom_fan_curve_enabled`
    ./tests/test_fan_watchdog.py:30:    def custom_fan_curve_enabled(self):
    ./tooling/install_check.py:418:    enabled = s.custom_fan_curve_enabled()
    ./tests/test_sensor_reader.py:57:    assert s.custom_fan_curve_enabled() is None
### `custom_fan_curve_points`
    ./tests/test_fan_watchdog.py:33:    def custom_fan_curve_points(self):
    ./tests/test_fan_watchdog.py:341:    orig_points = ns.sensors.custom_fan_curve_points
    ./tests/test_fan_watchdog.py:342:    ns.sensors.custom_fan_curve_points = lambda: (order.append("read"), orig_points())[1]
### `expected_fan_pwm`
    ./src/fan_watchdog.py:240:        expected = t["want"] = self.sensors.expected_fan_pwm(temp - STALL_HYSTERESIS_C)
    ./src/sensor_reader.py:101:    def expected_fan_pwm(self, temp_c: float) -> int | None:
    ./tests/test_fan_watchdog.py:24:    """Real expected_fan_pwm() (inherited) over fake readings."""
### `fake_host`
    ./CLAUDE.md:38:  and Decky's root-owned `py_modules/host.py` (ADR 0001). Tests: the `fake_host` fixture
    ./CLAUDE.md:39:  (`tests/fake_host.py`, shared with `decky/tests`) records argv and scripts results.
    ./tests/test_profiles_max_fans.py:26:def fan_calls(fake_host):
### `get_apu_temp`
    ./tests/test_fan_watchdog.py:36:    def get_apu_temp(self):
    ./src/dashboard_ui.py:746:            apu = self.sensors.get_apu_temp()
    ./tests/test_sensor_reader.py:34:    assert s.get_apu_temp() == 45.5
### `get_fan_speeds`
    ./src/dashboard_ui.py:748:            fan1, fan2 = self.sensors.get_fan_speeds()
    ./src/fan_watchdog.py:207:            t["rpm"] = self.sensors.get_fan_speeds()
    ./src/fan_watchdog.py:231:        fan1, fan2 = t["rpm"] = self.sensors.get_fan_speeds()
### `get_profile`
    ./tooling/install_check.py:416:    prof = profiles.get_profile(store.get("active"), store) or {}
    ./src/profiles.py:411:def get_profile(name, store=None):
    ./src/profiles.py:463:        p = get_profile(name, store)
### `get_profiles`
    ./tests/test_decky_profile_logging.py:3:`get_profiles()` runs `z13-profile list --json`, whose output holds every
    ./tests/test_decky_profile_logging.py:56:    result = asyncio.run(main.Plugin().get_profiles())
    ./tests/test_decky_profile_logging.py:69:    result = asyncio.run(main.Plugin().get_profiles())
### `lightbar_failure_kind`
    ./docs/ROADMAP.md:118:  daemon restart alone, via `recover_lightbar_stale()` — `lightbar_failure_kind()` tells the two apart.
    ./tests/test_profiles_apply.py:331:    assert profiles.lightbar_failure_kind(out) == expected
    ./tests/test_host.py:136:    assert "timed out" in r.out     # profiles.lightbar_failure_kind reads this as "wedged"
### `lightbar_timed_out`
    ./src/profiles.py:574:def lightbar_timed_out(out):
    ./tests/test_profiles_apply.py:322:    assert profiles.lightbar_timed_out(out) is expected
### `logging_enabled`
    ./main.py:49:    app_log.setup(to_disk=install_record.logging_enabled())
    ./tests/test_decky_profile_logging.py:37:    monkeypatch.setattr(mod, "logging_enabled", lambda: False)   # the user said no logs
    ./tests/test_install_record.py:75:    assert ir.logging_enabled(str(p)) is True                     # no record: on
### `recover`
    ./CHANGELOG.md:98:- **Fan watchdog** rules (app and Decky): re-pin a dropped Max Fans curve, recover
    ./docs/ROADMAP.md:41:  profile-rewrite recovery. Both watchdog branches (curve dropped / fans stalled) now recover with
    ./src/profiles.py:558:    """Classify a failed lightbar write, or None if it isn't one we can recover.
### `recover_lightbar`
    ./src/profiles.py:610:def recover_lightbar(*, sleep=time.sleep, now=time.time):
    ./src/profiles.py:645:    Rate-limited like recover_lightbar(), but via its own stamp so the two
    ./src/profiles.py:775:                    rec_ok, rec_msg = recover_lightbar()
### `recover_lightbar_stale`
    ./CLAUDE.md:143:`recover_lightbar_stale()` restarts it alone, no reset, when the daemon's cached node goes stale
    ./src/profiles.py:642:def recover_lightbar_stale(*, sleep=time.sleep, now=time.time):
    ./src/profiles.py:777:                    rec_ok, rec_msg = recover_lightbar_stale()
### `recover_stale`
    ./tests/test_hardware_controller.py:42:def recover_stale(monkeypatch):
    ./tests/test_hardware_controller.py:94:def test_stale_lightbar_one_daemon_restart_one_retry(recover, recover_stale):
    ./tests/test_hardware_controller.py:99:    assert len(recover_stale.seen) == 1
### `restart_lightbar_daemon`
    ./src/profiles.py:596:def restart_lightbar_daemon(*, sleep=time.sleep):
    ./src/profiles.py:636:    daemon_ok, daemon_msg = restart_lightbar_daemon(sleep=sleep)
    ./src/profiles.py:655:    return restart_lightbar_daemon(sleep=sleep)
### `restore_tdp`
    ./src/hardware_controller.py:74:    def restore_tdp(self):
    ./src/hardware_controller.py:87:        """As restore_tdp, for the undervolt (the firmware profile write resets it)."""
    ./src/fan_watchdog.py:375:            restore("restore_tdp")
### `restore_undervolt`
    ./src/fan_watchdog.py:380:            restore("restore_undervolt")
    ./src/hardware_controller.py:86:    def restore_undervolt(self):
### `rewrite_profile`
    ./src/hardware_controller.py:58:    def rewrite_profile(self):
    ./tests/test_fan_watchdog.py:53:    def rewrite_profile(self):
    ./tests/test_fan_watchdog.py:54:        self._call("rewrite_profile")
### `set_battery_limit`
    ./src/hardware_controller.py:117:    def set_battery_limit(self, limit_percent: int):
    ./src/profile_control.py:203:            return c.set_battery_limit(bl) if bl is not None else "unchanged"
    ./src/dashboard_ui.py:619:        self.control._submit(f"Battery limit {value}%", lambda: (self.controller.set_battery_limit(value),
### `set_brightness`
    ./docs/ROADMAP.md:119:  Same logic in `HardwareController.set_brightness` and the Decky backend (as deck, with
    ./tests/test_hardware_controller.py:60:    assert c.set_brightness("low", devices=("lightbar", "keyboard")) == []
    ./tests/test_hardware_controller.py:69:    assert c.set_brightness("high") == []
### `set_fancurve_pwm`
    ./tests/test_fan_watchdog.py:59:    def set_fancurve_pwm(self, points):
    ./tests/test_fan_watchdog.py:60:        self._call("set_fancurve_pwm", points)
    ./tests/test_fan_watchdog.py:113:        if n in ("set_fans_max", "set_fancurve_pwm"):
### `set_fans_max`
    ./tests/test_fan_watchdog.py:56:    def set_fans_max(self):
    ./tests/test_fan_watchdog.py:57:        self._call("set_fans_max")
    ./tests/test_fan_watchdog.py:113:        if n in ("set_fans_max", "set_fancurve_pwm"):
### `set_profile`
    ./docs/ROADMAP.md:147:- ✅ **Decky:** dropdown lists `profiles.json`; `set_profile` runs `runuser -u deck -- z13-profile apply <name>`.
    ./src/hardware_controller.py:54:    def set_profile(self, profile_name: str):
### `set_tdp`
    ./src/profile_control.py:178:            return c.set_tdp(pl1=tdp["pl1"], pl2=tdp["pl2"], pl3=tdp["pl3"], force=bool(tdp.get("force")))
    ./src/profile_control.py:190:                c.set_tdp(pl1=tdp["pl1"], pl2=tdp["pl2"], pl3=tdp["pl3"], force=bool(tdp.get("force")))
    ./src/hardware_controller.py:204:    def set_tdp(self, pl1=None, pl2=None, pl3=None, force: bool = False):
### `telemetry`
    ./README.md:54:  telemetry. The backend loads, but the panel hasn't been walked through end to end. Its profile
    ./README.md:99:6. **Backlog** (§10): a diagnostics panel, battery health, richer telemetry, tablet mode on folio
    ./README.md:147:- Live telemetry, the **Max Fans** toggle and the **Profile** dropdown.
### `test_logging_choice_on_off_missing`
    ./tests/test_install_record.py:73:def test_logging_choice_on_off_missing(tmp_path):

## Tests
- `decky/tests/test_backend.py::90` `test_clamp_per_rail_with_armoury_ranges`
- `decky/tests/test_backend.py::94` `test_tdp_ranges_runs_no_subprocess`
- `decky/tests/test_backend.py::101` `test_in_range_unchanged`
- `decky/tests/test_backend.py::105` `test_bounds`
- `decky/tests/test_backend.py::109` `test_spl_pushes_sppt_and_fppt_up`
- `decky/tests/test_backend.py::113` `test_fppt_never_below_sppt`
- `decky/tests/test_backend.py::117` `test_force_only_above_75`
- `decky/tests/test_backend.py::121` `test_floats_and_strings_round`
- `decky/tests/test_backend.py::126` `test_tdp_argv`
- `decky/tests/test_backend.py::133` `test_steps_and_bounds`
- `decky/tests/test_backend.py::159` `test_update_active_only`
- `decky/tests/test_backend.py::168` `test_update_rejects_unknown_fields`
- `decky/tests/test_backend.py::172` `test_update_without_active_raises`
- `decky/tests/test_backend.py::176` `test_atomic_write_roundtrip`
- `decky/tests/test_backend.py::183` `test_schema_keys_preserved`
- `decky/tests/test_backend.py::194` `test_apply_tdp_on_writes_store_then_z13ctl`
- `decky/tests/test_backend.py::206` `test_apply_tdp_off_clears_and_reapplies_profile`
- `decky/tests/test_backend.py::219` `test_apply_tdp_hw_failure_still_saved`
- `decky/tests/test_backend.py::226` `test_apply_tdp_no_store_touches_nothing`
- `decky/tests/test_backend.py::237` `test_written_profile_passes_engine_validation`
- `decky/tests/test_backend.py::253` `test_apply_tdp_for_a_stale_profile_is_rejected`
- `decky/tests/test_backend.py::266` `test_apply_battery_limit_for_a_stale_profile_is_rejected`
- `decky/tests/test_backend.py::276` `test_targeted_request_for_the_active_profile_is_applied`
- `decky/tests/test_backend.py::283` `test_apply_battery_limit`
- `decky/tests/test_backend.py::308` `test_symlinked_lock_is_refused`
- `decky/tests/test_backend.py::315` `test_hardlinked_lock_is_refused`
- `decky/tests/test_backend.py::322` `test_held_lock_times_out`
- `decky/tests/test_backend.py::343` `test_lock_file_owned_by_current_user_is_accepted`
- `decky/tests/test_backend.py::352` `test_normal_use_creates_plain_lock_file`
- `decky/tests/test_backend.py::359` `test_one_call_per_device`
- `decky/tests/test_backend.py::366` `test_wedged_lightbar_does_not_fail_keyboard`
- `decky/tests/test_backend.py::377` `test_all_failed`
- `decky/tests/test_backend.py::384` `test_bad_level_runs_nothing`
- `decky/tests/test_backend.py::405` `test_timeout_gets_one_reset_daemon_restart_and_one_retry_keyboard_first`
- `decky/tests/test_backend.py::426` `test_stale_gets_one_daemon_restart_and_one_retry_no_usb_reset`
- `decky/tests/test_backend.py::443` `test_unrecognised_failure_no_recovery`
- `decky/tests/test_backend.py::449` `test_still_wedged_after_reset_retries_only_once`
- `decky/tests/test_backend.py::458` `test_still_stale_after_daemon_restart_retries_only_once`
- `decky/tests/test_backend.py::467` `test_rate_limit`
- `decky/tests/test_backend.py::478` `test_stale_rate_limit`
- … 38 more tests (not listed)

## Platform guards
- `decky/main.py`: preprocessor_if=0, platform_checks=0
- `decky/src/backend.ts`: preprocessor_if=0, platform_checks=0
- `decky/src/components/BatteryLimit.tsx`: preprocessor_if=0, platform_checks=0
- `decky/src/components/KeyboardBrightness.tsx`: preprocessor_if=0, platform_checks=0
- `decky/src/components/PowerLimits.tsx`: preprocessor_if=0, platform_checks=0
- `decky/src/components/Telemetry.tsx`: preprocessor_if=0, platform_checks=0
- `decky/src/hooks.ts`: preprocessor_if=0, platform_checks=0
- `decky/src/index.tsx`: preprocessor_if=0, platform_checks=0
- `decky/src/profileSwitch.ts`: preprocessor_if=0, platform_checks=0
- `decky/tests/test_backend.py`: preprocessor_if=0, platform_checks=0
- `decky/tests/profileSwitch.test.ts`: preprocessor_if=0, platform_checks=0
## Purpose
- `decky/main.py` — main backend logic for Decky plugin including TDP control, battery limits, fan management, and lightbar recovery
- `decky/src/backend.ts` — TypeScript backend API definitions for Decky plugin communication
- `decky/src/components/BatteryLimit.tsx` — React component for battery limit UI control
- `decky/src/components/KeyboardBrightness.tsx` — React component for keyboard brightness control
- `decky/src/components/PowerLimits.tsx` — React component for power limit controls
- `decky/src/components/Telemetry.tsx` — React component for telemetry display
- `decky/src/hooks.ts` — React hooks for Decky plugin UI behavior
- `decky/src/index.tsx` — main Decky plugin entry point and component exports
- `decky/src/profileSwitch.ts` — pure profile-switch flow: apply, then re-read the active profile and Max Fans (no Decky imports, runs under plain Node)
- `decky/tests/test_backend.py` — unit tests for backend logic and API behavior
- `decky/tests/profileSwitch.test.ts` — Node built-in test runner tests for `switchProfile`

## Data flow
- Input: TDP values, battery limit, brightness level, profile selection from UI
- Transformation: Clamp TDP values to valid ranges, validate battery limits, process brightness levels, manage profile state
- Output: Apply settings to hardware, return success/failure status, provide telemetry data
- Error paths: Handle lightbar recovery failures, stale profile rejection, hardware communication errors, file lock conflicts

## Gotchas
- `decky/main.py::436` `tdp_ranges` returns empty list when subprocess fails, not raising exception
- `decky/main.py::442` `clamp_tdp` uses `ranges=None` default but `tdp_ranges()` returns `[]` not `None`
- `decky/main.py::624` `restart_lightbar_daemon` uses `time.sleep` with default parameter, not injected
- `decky/main.py::635` `recover_lightbar` uses `time.sleep` and `time.time` with defaults, not injected
- `decky/main.py::661` `recover_lightbar_stale` uses `time.sleep` and `time.time` with defaults, not injected
- `decky/main.py::806` `max_fans_off_argv` returns `[]` when `store` is None, not raising exception

## To change X, edit Y
- to change TDP range validation, edit `decky/main.py::tdp_ranges`
- to change battery limit clamping, edit `decky/main.py::clamp_battery`
- to change lightbar recovery behavior, edit `decky/main.py::recover_lightbar`
- to change stale lightbar recovery, edit `decky/main.py::recover_lightbar_stale`
- to change brightness setting logic, edit `decky/main.py::set_brightness_per_device`
- to change profile application, edit `decky/main.py::apply_tdp`
- to change battery limit application, edit `decky/main.py::apply_battery_limit`
- to change max fans behavior, edit `decky/main.py::set_max_fans_blocking`
- to change fan curve PWM setting, edit `decky/main.py::set_fancurve_pwm`
- to change fan watchdog behavior, edit `decky/main.py::fan_watchdog_tick`
- to change profile retrieval, edit `decky/main.py::get_profiles`
- to change profile setting, edit `decky/main.py::set_profile`
- to change power state retrieval, edit `decky/main.py::get_power_state`
- to change telemetry data, edit `decky/main.py::telemetry`
