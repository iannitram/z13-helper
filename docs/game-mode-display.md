# Game Mode display behaviour (#28)

Game Mode owns the panel through gamescope, not through the desktop. The desktop refresh choice
(`src/display_modes.py`, kscreen-doctor) does nothing there, and nothing in this repo writes the
display in Game Mode except the file below.

## What we install

`packaging/host/gamescope/displays/asus.rogflowz13.lcd.lua` is copied by `install.sh` /
`install.sh --repair` (manifest row in `tooling/install_check.py`) to
`~/.config/gamescope/scripts/00-gamescope/displays/asus.rogflowz13.lcd.lua`.

- It registers the panel (vendor `TMA`, product `0x0803`, model `TL134ADXP03`) as a known display.
  Any other panel does not match (the match returns -1), so the script is inert elsewhere.
- It offers gamescope's dynamic refresh from 48 to 180 Hz by lengthening only the vertical front
  porch at a fixed 869.04 MHz pixel clock, and refuses rates outside that range.
- 48-180 Hz is what the panel's EDID advertises; it is a test candidate until checked on the
  device (Steam's refresh slider in Game Mode, frame-time graph at 48 and 180 Hz).

## Back out

1. Delete the file above (or move it elsewhere).
2. Restart Game Mode (Steam: Power > Switch to Desktop, then back; or `systemctl --user restart gamescope-session`).

gamescope then falls back to its built-in handling of the panel, as before the script existed.
Note that `install.sh --repair` copies the file back; to keep it off, do not run repair, or
remove the manifest row locally.

## Desktop Mode

The Screen Refresh row in the main window lists only the rates `kscreen-doctor -o` shows for the
panel at its current resolution. A change is read back; if the panel is not in the requested mode
the previous mode is restored and the row says so (prefixed with ⚠) until the next change. "Back" returns to the mode before the last
change. The choice is not persisted: Plasma's own display settings still decide after login.
