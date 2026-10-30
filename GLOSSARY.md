# Z13-Helper

Controls an ASUS ROG Flow Z13 (GZ302EA) on SteamOS from two frontends: the desktop app and the Decky plugin. Both drive the same hardware and share the same saved state.

## Frontends and places

**Desktop app**:
The PyQt tray app, run as a Flatpak in Desktop Mode.
_Avoid_: dashboard, GUI, controller app

**Decky plugin**:
The Game Mode Quick Access Menu plugin. Its backend currently runs as the `deck` user on the host; shared Python modules are installed as root-owned files.
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
Eight temperature→speed points requested from the firmware. The reported custom-curve-enabled bit describes the selected mode; it does not prove the fans physically follow the curve.
_Avoid_: fan profile

**Max Fans**:
A user toggle that requests a 100% curve from 20 °C. Turning it off restores the saved custom curve, or returns to firmware auto when the saved curve is explicitly empty. The shared flag is suspended during profile applies, and stale watchdog recoveries are canceled when Max Fans turns off.
_Avoid_: full fans, turbo fans

**Fan watchdog**:
The periodic check for signs that the requested fan mode or fan response has been lost, and the trigger for fan recovery. Mode bits and tach readings are evidence, not direct proof of physical fan motion.
_Avoid_: Max Fans enforcer, fan monitor

**Fan recovery**:
Rewriting the platform profile (same value) and then the fan curve. It's the only sequence that reliably gets the firmware following the curve again.
_Avoid_: re-pin, reassert

**Dropped curve**:
The firmware has switched the fans back to automatic, usually after a platform profile write.

**Stall**:
The curve asks for at least half speed, but both fans read 0 RPM for consecutive checks.

**Auto stall**:
No custom curve (firmware auto, Max Fans off), the APU at 65 °C or more, and both fans at 0 RPM for 20 s. Recovered by a platform profile rewrite, then the active profile's TDP and a curve reset; never by re-running profile hooks.

**Frozen tach**:
One fan's RPM reading stays exactly the same for 90 s while the curve asks for speed. This stale reading was observed on the device and is used as a likely-stall signal; it does not by itself prove the fan has stopped.
_Avoid_: stuck fan

## Lighting

**Lightbar recovery**:
Resetting a wedged rear lightbar, then restarting the z13ctl daemon so it finds the device again.
_Avoid_: lightbar fix
