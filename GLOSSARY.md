# Z13-Helper

Controls an ASUS ROG Flow Z13 (GZ302EA) on SteamOS from two frontends: the desktop app and the Decky plugin. Both drive the same hardware and share the same saved state.

## Frontends and places

**Desktop app**:
The PyQt tray app, run as a Flatpak in Desktop Mode.
_Avoid_: dashboard, GUI, controller app

**Decky plugin**:
The Game Mode Quick Access Menu plugin. Its backend runs as root on the host.
_Avoid_: QAM plugin, decky backend

**Frontend**:
Either the desktop app or the Decky plugin. The shared fan, sensor and profile behaviour sits beneath both of them.

**Host**:
The SteamOS system outside the Flatpak sandbox, where z13ctl, systemd and the hardware are reached.
_Avoid_: system, outside

## Profiles

**Profile**:
A named set of settings saved by the app (platform profile, TDP, fan curve, Max Fans, brightness, battery limit). One profile is active at a time, shared by both frontends.
_Avoid_: preset, mode

**Platform profile**:
The firmware's own power mode (quiet, balanced, performance). Writing it drops any custom fan curve.
_Avoid_: power profile, firmware profile

## Fans

**Fan curve**:
Eight temperature→speed points the firmware follows. Below the first point the fans idle.
_Avoid_: fan profile

**Max Fans**:
A user toggle that pins the fan curve at 100% from 20 °C. Turning it off restores the curve that was running before.
_Avoid_: full fans, turbo fans

**Fan watchdog**:
The periodic check that the fan curve the user asked for is actually running, and the trigger for fan recovery when it isn't.
_Avoid_: Max Fans enforcer, fan monitor

**Fan recovery**:
Rewriting the platform profile (same value) and then the fan curve. It's the only sequence that reliably gets the firmware following the curve again.
_Avoid_: re-pin, reassert

**Dropped curve**:
The firmware has switched the fans back to automatic, usually after a platform profile write.

**Stall**:
The curve asks for at least half speed, but both fans read 0 RPM for consecutive checks.

**Frozen tach**:
One fan's RPM reading stays exactly the same for 90 s while the curve asks for speed. It means the fan has likely stopped and its reported speed is out of date.
_Avoid_: stuck fan

## Lighting

**Lightbar recovery**:
Resetting a wedged rear lightbar, then restarting the z13ctl daemon so it finds the device again.
_Avoid_: lightbar fix
