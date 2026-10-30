# z13-helper — Z13-Helper Decky plugin (Game Mode)

A [Decky Loader](https://decky.xyz) plugin that exposes the Z13 hardware controls in the Steam
**Quick Access Menu**, so they're usable in Game Mode. It's a thin frontend over the same
`z13ctl` / `boondock` CLIs the desktop PyQt app uses — one backend contract, two frontends.

Started as **Spike 2** from [`../docs/plan/Phase_5.md`](../docs/plan/Phase_5.md) (Max Fans +
Profile dropdown); ROADMAP §7 "Phase 11 — Decky parity" added the rest of the panel:

| Section | Controls | Writes |
|---|---|---|
| **Z13-Helper** | Temps `APU 52°C · GPU 48°C`, Fans `2100 · 2250 RPM` (2 s poll, only while the QAM is open) · Max Fans · Profile | Max Fans → `max-fans.json` + `fancurve`; Profile → `z13-profile apply` |
| **Power limits · &lt;profile&gt;** | Apply power limits toggle · SPL / sPPT / fPPT use per-rail kernel metadata bounds (sPPT ≥ SPL, fPPT ≥ sPPT) | active profile's `tdp` in `profiles.json`, then `z13ctl tdp --set` (off → `z13-profile apply <active> --no-hooks`) |
| **Battery** | Charge limit 40…100 % (step 5) | active profile's `battery_limit`, then `z13ctl batterylimit --set` |
| **Keyboard brightness** | Off / Low / Med / High | `z13ctl --device keyboard …` then `--device lightbar …`; a failed device is shown, not fatal |

Sliders commit **on release** (pointer/touch up, or ~0.7 s after the last D-pad step), not per
drag tick. Every write goes through z13ctl / z13-profile run **as deck**; `profiles.json` is
written atomically and stays deck-owned. The panel never reads live `ppt_*` values. It uses each
rail's `min_value`/`max_value` metadata (probed on the host from Flatpak); if Armoury metadata is
unavailable, the legacy backend range is 5–93 W. With no TDP override, the panel says **Not set**
and disables the rails. Its internal slider positions are placeholders, not applied or saved power
limits.
Still to come: the 8-point fan curve editor and Boondock-specific controls.

Code: `src/index.tsx` (layout), `src/backend.ts` (callables + shared clamp), `src/hooks.ts`
(commit-on-release, poll-while-visible), `src/components/*`. Backend helpers are unit-tested
without Decky: `python3 -m unittest discover -s decky/tests` (from the repo root).

## Stack (modern Decky)
`@decky/api` + `@decky/ui` + `@decky/rollup`, built with pnpm → `dist/index.js`. Backend `main.py`
uses `import decky` and calls `~/.local/bin/z13ctl` and `~/.local/bin/boondock` on the host through
the shared host seam `src/host.py` (installed root-owned into `py_modules/`, like the fan watchdog).
It currently runs **as deck**: `plugin.json` has `"_root"`, but Decky Loader only runs a backend as root for
`"root"` (checked 2026-10-15). The code works either way: `host.run(..., as_deck=True)` adds the
`runuser -u deck` hop only under root, and every child gets the deck env (deck's runtime dir and
session bus, and the host's `LD_LIBRARY_PATH` instead of the loader's bundled one).

## Build
```bash
cd decky
pnpm install
pnpm run build        # -> dist/index.js
```

## Install / reload
```bash
./install.sh --repair
```
The installer stages and verifies the full plugin tree, stops Decky's watcher before swapping the
root-owned plugin directory, then starts the loader once. An unchanged repair leaves it running.
Do not copy files directly into the live plugin directory; Decky can reload while a bundle is
only partly installed. Then open **QAM → Decky (plug icon) → Z13-Helper**. For a frontend-only
iteration, use Decky → Settings → Developer mode → per-plugin **Reload**.

## Logs
- Backend: `journalctl -u plugin_loader.service`
- Frontend: CEF inspector at `http://localhost:8080` (SharedJSContext)

## Notes
- **Max Fans** mirrors the desktop app: ON → 8×100% curve; OFF → restore the custom curve
  snapshotted on engage, or firmware auto when the snapshot is explicitly empty. The ON/OFF flag and snapshot live in
  `~/.config/z13-helper/max-fans.json`, shared with the desktop app (`src/max_fans_state.py`),
  so a toggle in either mode is seen by the other.
- **Fan watchdog:** a 2 s task ticks the shared `src/fan_watchdog.py` (installed into
  `py_modules/` with `src/sensor_reader.py`): it re-pins Max Fans after a dropped curve and recovers
  stalled or frozen-tach fans. It acts only in Game Mode (the desktop app owns Desktop Mode) and
  uses the shared apply state to suspend Max Fans while a profile is applied. Queued dropped-curve
  work and an in-flight Max Fans recovery recheck the flag before writing 100% PWM. The trace records
  watchdog decisions, not physical fan rotation. Desktop (`fan-trace.log`) and Game Mode
  (`fan-trace-gamemode.log`) traces are separate; trace output alone does not verify every resume or
  cold-boot transition.
- HID controls (brightness) route through the deck-user z13ctl daemon socket: the panel's z13ctl
  calls run as deck (`host.run(..., as_deck=True)`; under root that is
  `runuser -u deck -- env HOME=/home/deck XDG_RUNTIME_DIR=/run/user/1000 …`).
- Easiest install: `./install.sh --repair` from the repo root (build + copy + clean loader restart).
