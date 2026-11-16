# steamos-manager on the Z13: measured interfaces (#29)

Measured 2026-11-04 on the Z13 (board `GZ302EA`, SteamOS with `steamos-manager 26.4.1-2`, kernel
7.2.x valve). Method: `busctl introspect` on both buses, `steamosctl` getters (read-only), the
installed device configs under `/usr/share/steamos-manager/`, and the D-Bus policy file. Valve's
source repository was intentionally not read, a deliberate deviation from issue #29's "read
Valve's contract" step: everything below is from the installed package, so treat behaviour not
listed here as unknown.

## Two services, same bus name

| Bus | Process | Interfaces on `/com/steampowered/SteamOSManager1` |
|---|---|---|
| system | root `steamos-manager` | `RootManager`: setters for CPU boost, governor, fan speed, GPU level and power profile, manual GPU clock, max charge level, performance profile, TDP limit, Wi-Fi, session, BIOS/dock update |
| session (user `deck`) | `steamos-manager` | `CpuBoost1`, `CpuScaling1`, `CpuScheduler1`, `FanControl1`, `Manager2` (device model, session switching, Wi-Fi), `SessionManagement1`, `Storage1`, `UdevEvents1`, `UpdateBios1`, `UpdateDock1`, `WifiBackend1`, `WifiPowerManagement1`, `HdmiCec1/2`, `FactoryReset1`, `RemoteInterface1` |

The user service is what Steam's UI and `steamosctl` talk to; it forwards privileged work to the
root service. The system-bus policy (`/usr/share/dbus-1/system.d/com.steampowered.SteamOSManager1.conf`)
lets only `root` own the name, so a third-party adapter cannot take the system-bus name; it could
only own a different name or replace the package's service.

## What the Z13 does not get

`Manager2.DeviceModel` reports `("unknown", "unknown")`. The manager picks per-device config by DMI
(`/usr/share/steamos-manager/devices/*.toml`, matched on `sys_vendor` and `board_name`); `GZ302EA`
appears in none of the 26 files, so the optional interfaces are never created:

| Wanted control | Interface Steam would use | On this Z13 |
|---|---|---|
| TDP | `TdpLimit1` | `steamosctl get-tdp-limit`: UnknownInterface |
| Charge limit | `BatteryChargeLimit1` | `get-max-charge-level`: UnknownInterface |
| Performance profile | `PerformanceProfile1` | `get-available-performance-profiles`: UnknownInterface |
| CPU boost | `CpuBoost1` | works (`enabled`) |
| Fan control state | `FanControl1` | works (`BIOS`) |

The ROG Ally entry (`rog-ally-series.toml`) shows the shape of the missing config: TDP through the
`asus-armoury` firmware attributes, performance profile through the `asus-wmi` platform profile,
charge limit through `acpi_sb`. The Z13 exposes the same kernel nodes this app already writes (via
`z13ctl`), so a device entry would be small, but it would live in `/usr/share`, the immutable
image: a SteamOS update replaces it. No user-writable override *directory* was found: `/etc/steamos-manager`
holds only `remotes.d`, and `~/.config/steamos-manager/state.toml` is session state, not device config.
There is, however, an override *flag*: the daemon takes `--device-config <path>` ("Force the device
config. This bypasses DMI matching, so use at your own risk"; shown by the installed
`/usr/lib/steamos-manager --help`). The packaged units run `ExecStart=/usr/lib/steamos-manager -r`
(system) and `/usr/lib/steamos-manager` (user), so a systemd drop-in on the writable `/etc` overlay could
pass a Z13 TOML kept outside the image to both (route a2 below).
The user service exposes `RemoteInterface1.RemoteInterfaces`, which is empty on this Z13 (no remote
loaded). That is the remote mechanism `remotes.d` feeds. Whether a remote can serve `TdpLimit1`,
`BatteryChargeLimit1` or `PerformanceProfile1` was not tested and is the one route this measurement
leaves open.

## Feasibility (read 2026-11-11, steamos-manager 26.4.1)

Method: read-only. `busctl --user introspect`, `steamosctl`, the installed binary's strings, and
Valve's public repository (README, `data/interfaces/*.xml`, `src/hardware.rs`, `src/manager/user.rs`),
read on the web. Nothing was installed or written. This supersedes the "source was not read" note at
the top for the schema and remote questions; the rest of the page is still the 2026-11-04 measurement.

**Device config (routes a and a2).** `DeviceConfig::load` reads `*.toml` only from `/usr/share/steamos-manager/devices`
(a compile-time constant; no `/etc` or user directory) and returns the first file whose `[[device]]`
`dmi.sys_vendor` plus `board_name` or `product_name` matches. Schema, from `rog-ally-series.toml` and the
struct: `[[device]]` (`dmi.*`, `device`, `variant`, `friendly_name`), `[performance_profile]`
(`platform_profile_name`, `suggested_default`), `[tdp_limit]` (`method = "firmware_attribute"` plus
`[tdp_limit.firmware_attribute]` `attribute`, `performance_profile`), `[battery_charge_limit]`
(`method = "acpi_sb"`, `suggested_minimum_limit`), `[gpu_performance]`, `[gpu_power_profile]`, `[fan_speed]`.
So a `GZ302EA` file is a small TOML. Placed in `/usr/share` it sits in the immutable image; kept elsewhere
and passed with `--device-config` it does not, but the daemon reads it once at start either way. Unknown: whether the Z13's `asus-armoury` attributes satisfy the `firmware_attribute` method as
the Ally's do (not probed on the device here).

**Remote (the route the 2026-11-04 page left open).** Valve's README documents it: a daemon puts a TOML in
`/etc/steamos-manager/remotes.d` (or `/usr/share/.../remotes.d`) with a section per interface plus
`bus_name` and `object_path`; the user daemon relays that interface's D-Bus traffic. `BatteryChargeLimit1`,
`PerformanceProfile1` and `TdpLimit1` are all on the remotable list (the user daemon has a
`#[remote]` owner for each). Limits, from the README except where marked: a remote fills holes only and cannot override a
local implementation (the Z13 has none, so that is fine); the remote must be on the **system** bus;
new remote files are not picked up at runtime (the README implies a daemon restart; `Manager2.ReloadConfig` exists in the interface and sends `DaemonCommand::ReadConfig` in the source, but whether it rescans `remotes.d` is unverified);
the interface is the public one, so `TdpLimit` (u, with `TdpLimitMin`/`TdpLimitMax`), `MaxChargeLevel`
(i, -1 resets), `SuggestedMinimumLimit` (i, read, on `BatteryChargeLimit1`) and `AvailablePerformanceProfiles` / `PerformanceProfile` /
`SuggestedDefaultPerformanceProfile` (as, s, s) are what the adapter must serve. Today
`RemoteInterface1.RemoteInterfaces` is empty and `/etc/steamos-manager/remotes.d` is empty, as measured.
Not tested live: that needs a running adapter on the system bus, which means installing files, which
this job did not do. `/etc` is a writable overlay here (it survives image updates in the usual SteamOS
layout; not verified across an update), and no `/etc/dbus-1/system.d` exists yet, so the adapter would
ship its own bus policy and a root systemd unit.

`TdpLimit1` is not a plain relay: Valve's `register_tdp_limit1` hands the remote to its `TdpManagerService`
(`SetProxy`) and registers nothing if that service failed to start. The schema also allows
`method = "remote_interface"` under `[tdp_limit]` in a device TOML, a hybrid of a2 and b2 (device file
selects the remote-backed TDP manager) that this page did not evaluate.

## Options (the owner's choice)

| | Route | Feasible | Cost and risk |
|---|---|---|---|
| a | GZ302EA device TOML in `/usr/share/steamos-manager/devices`, re-applied by post-update repair (#27) | Yes on paper; schema known | Edits the immutable image (`steamos-readonly` off), wiped by every update until repair runs; Valve's daemon then writes the sysfs nodes itself and can fight the single state owner (#23); daemon restart needed |
| a2 | Same TOML kept outside the image, passed by `--device-config` in systemd drop-ins (`/etc/systemd/system/steamos-manager.service.d/` and the user unit equivalent) | Yes: flag is in the installed `--help`; not run | No image edit and survives updates if `/etc` does. But Valve's daemon then writes the sysfs nodes itself and fights the single state owner (#23); the flag bypasses DMI matching (Valve says use at own risk); the drop-in must repeat Valve's `ExecStart` and track it when Valve changes the unit; root and user units both need it; restart needed |
| b | Shim replacing Valve's service | Yes, but heaviest | Re-implements Wi-Fi, session switching, updates; one bug breaks them. Not recommended |
| b2 | Remote adapter: small system-bus service plus `/etc/steamos-manager/remotes.d/z13.toml`, serving the three interfaces by calling `z13ctl` and the Z13-Helper state owner | Yes per Valve's documented contract; not yet run | No image edit; Valve's service untouched; adapter is the one state owner, so no fight with #23. Needs a root unit, bus policy and a restart; interface is stable by Valve's compatibility rule |
| c | Ask Valve for a GZ302EA entry | Cheap, slow | Not in our control; fixes nothing until a SteamOS release |
| d | Not feasible | No | Contradicted by the remote route |

## Recommendation

Do not close #29 and do not choose (d). Prefer **b2**, the remote adapter, as a small prototype after
1.0: it is the only route that needs no image edit and no replacement of Valve's service, and it keeps
Z13-Helper as the single state owner. First, behind an explicit owner go-ahead, prove it on the device
with a throwaway remote serving only `TdpLimit1` (read the QAM, set a value, confirm it reaches
`z13ctl`, remove it). Keep (a2) as the fallback if the relay misbehaves: it avoids the image edit that (a) needs, at the cost of
the #23 conflict and a drop-in that tracks Valve's unit (use (a) only if (a2) proves unworkable), and file (c) in parallel since
it costs nothing. The adapter is not built until the owner chooses.

## Consequence for #29

- Steam's Performance tab cannot show TDP, charge limit or profile on the Z13 with the installed
  device configs; a remote could supply them (documented, untested here).
- Decky stays the front end for these controls (as in Phase 13 of the roadmap) until an adapter exists.
- Owner decisions before any adapter is built:
  1. Allow the remote prototype (installs a root service, bus policy and an `/etc` file), or only ask Valve?
  2. If the remote fails, is a `--device-config` drop-in (a2, no image edit) acceptable, accepting that
     Valve's daemon then writes the sysfs nodes and the #23 state owner must yield for those controls?
     Or a device TOML in the immutable image (a), re-applied after every update through #27?
