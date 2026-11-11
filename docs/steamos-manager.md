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
image: a SteamOS update replaces it. No user-writable override directory was found: `/etc/steamos-manager`
holds only `remotes.d`, and `~/.config/steamos-manager/state.toml` is session state, not device config.
The user service exposes `RemoteInterface1.RemoteInterfaces`, which is empty on this Z13 (no remote
loaded). That is the remote mechanism `remotes.d` feeds. Whether a remote can serve `TdpLimit1`,
`BatteryChargeLimit1` or `PerformanceProfile1` was not tested and is the one route this measurement
leaves open.

## Consequence for #29

- Steam's Performance tab cannot show TDP, charge limit or profile on the Z13 with the installed
  device configs. Whether a `remotes.d` remote could supply them is untested. Our repo could change that by shipping a device TOML into the immutable image
  (re-applied after every update) or by replacing Valve's service. Valve could also ship a
  `GZ302EA` entry.
- Decky stays the front end for these controls (as in Phase 13 of the roadmap).
- Open decisions for the owner before any adapter is built:
  1. Replace `steamos-manager` with a shim that serves `TdpLimit1`, `BatteryChargeLimit1` and
     `PerformanceProfile1` by calling `z13ctl` (risks breaking updates, Wi-Fi, session switching),
     or only file a device-config request with Valve?
  2. Is shipping a device TOML into the immutable image acceptable if it must be re-applied after
     every update (it would join the post-update repair list, #27)?
