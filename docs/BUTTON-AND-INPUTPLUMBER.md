# The Side Button, InputPlumber, and Why the Button Stopped Opening the App

This doc is the single place that explains the **side-button-doesn't-open-the-app** problem,
why it happens, and the fix. It synthesizes the maintainer's notes on this machine (kept outside
the repo).

---

## TL;DR

- The small button on the side of the Z13 is the **Armoury Crate button**. In the kernel it is
  a WMI hotkey — **`KEY_PROG3`, evdev keycode 202 (0xca)**, on the `Asus WMI hotkeys` input device
  (`/dev/input/event5` on the current boot; index varies). **The wiki and the original code said
  "579" — that number is wrong (it is not `KEY_PROG3`), and a listener waiting for 579 never fires.
  That mis-recorded constant was the actual root cause of the button doing nothing.** Verified live
  2026-07-14: pressing the button emits `EV_KEY code=202 value=1/0` on `event5`.
- The app was written to receive that press through **InputPlumber's D-Bus** (`SetTargetDevices`
  + `InterceptMode=2` + an `InputEvent` named `ui_quick`).
- **InputPlumber is permanently masked on this machine** (`systemctl mask inputplumber.service`,
  symlinked to `/dev/null`). It was masked on **2026-05-06** to kill a phantom "SteamOS Handheld
  Controller" (`deck-uhid`) that hijacked real game-controller input (e.g. God of War Ragnarök).
  **The mask must stay** — it is load-bearing for game controllers.
- That explained the original failure: the app depended on a pathway that was unavailable on this
  machine. The current implementation reads the button with evdev directly; D-Bus remains only as
  a fallback for systems where InputPlumber owns the button.
- **The fix:** read the button **directly via evdev** on the `Asus WMI hotkeys` device. The
  volume keys and the Armoury Crate button still work through that driver with InputPlumber
  masked; they never needed it.

---

## Why it can't just "use InputPlumber" (the trap)

InputPlumber on the Z13 shipped a profile (`50-rog_flow_z13.yaml`) that declares an `xbox-elite`
gamepad target for hardware that **has no gamepad**. Once its `CompositeDevice0` exists,
`steamos-manager` attaches a `deck-uhid` "Valve Steam Deck Controller" to it within ~1 second.
Steam then shows a phantom "SteamOS Handheld Controller", and games without a controller-priority
UI bind to the phantom and ignore the real controller. Every softer mitigation (YAML overrides,
stripping the `-steamdeck` flag) failed or caused regressions (broke Sony PSPC init). The only
working fix was `disable --now` + `mask`. See the mirrored bug page for the full 5-approach matrix.

**Consequence for this app:** any design that depends on InputPlumber running is dead on arrival
here. The side button must be handled without InputPlumber.

## Why the naive evdev read fails *inside the Flatpak*

The app ships as a Flatpak (`io.github.iannitram.Z13Helper`). Two facts make an in-sandbox evdev read
of `/dev/input/event5` fail:

1. The device is `crw-rw---- root users` (via `/etc/udev/rules.d/99-z13ctl.rules`, which grants the
   `users` group read access to `ATTRS{name}=="Asus WMI hotkeys"`).
2. **The Flatpak sandbox drops supplementary groups.** Inside the sandbox the process is
   `uid=1000(deck) gid=1000(deck) groups=1000(deck),65534(nfsnobody)` — no `users`, no `input`.
   So even with `--device=input` in the manifest, `open("/dev/input/event5")` returns `EACCES`.

On the **host**, the `deck` user *is* in `users`, so the read works. Nothing is `EVIOCGRAB`-bing the
device now that InputPlumber is masked, so a plain read receives the events.

## The fix that shipped in this repo

Read the button **on the host** via `flatpak-spawn --host` (the same tunnel the app already uses
for `z13ctl` and `boondock`), and stream press events back into the Qt app:

- `src/evdev_listener.py` — canonical device finder (`find_side_button_device()` → `event5`, code
  202) plus a machine-readable `--emit` mode (`run_emit_loop()`) that prints `PRESS` per side-button
  press. Runnable standalone for diagnostics: `python3 src/evdev_listener.py` prints every keycode
  and marks the side button.
- `src/dashboard_ui.py` — `_start_side_button_listener()` now:
  - **In Flatpak:** spawns `flatpak-spawn --host --watch-bus python3 -u - --emit`, feeding the reader
    its own source on stdin (so no companion file is installed on the host), and pumps `PRESS`
    lines. Each `PRESS` fires the thread-safe `sideButtonPressed` Qt signal → `_toggle_window()`.
  - **Outside Flatpak:** reads the device in-process via `EvdevListener`.
  - **Fallback only:** if no evdev device is found, it falls back to the legacy InputPlumber D-Bus
    path (`_start_inputplumber_dbus_listener()`), kept for machines where InputPlumber still owns the
    button. On this machine that path is never taken.

Because the read happens on the host, **no manifest `--device=input` grant and no udev change are
needed.** The only manifest permission required is `--talk-name=org.freedesktop.Flatpak` (already
present).

## Two other things that must be true for the button to open the app

1. **For the in-app listener, the app must be resident.** The side button toggles an already-running
   (hidden/tray) instance. The `z13-helper.service` user unit keeps it resident; when the app is not
   running, the separate `z13-button-watcher` handles the tap and starts/shows the desktop app. The
   fixed unit is in
   [`packaging/host/systemd/user/z13-helper.service`](../packaging/host/systemd/user/z13-helper.service)
   and installed live.
2. **The installed Flatpak must contain the evdev implementation.** A source checkout can be newer
   than an already-installed bundle. Rebuild and reinstall (`flatpak/build.sh`) if the installed
   build predates the host-side evdev reader, then restart the app.

## Follow-up: wiki KB corrected (2026-09-23)

The maintainer's notes were rewritten on 2026-09-23 and now describe evdev-direct as the real
side-button path, with InputPlumber D-Bus as a never-taken fallback.
