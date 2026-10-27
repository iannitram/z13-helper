# Spec

**1.0 means:** on the Z13 (GZ302EA, SteamOS), every phase from 7 to 13 in `docs/ROADMAP.md` works and has been checked on the device, in both Desktop Mode and Game Mode. One component owns profile and hardware state, so the app, Decky and `z13ctl` always agree. After a SteamOS update the app shows what broke and repairs it reversibly.

**Approved by the owner:** 2026-10-26, the owner, in the Project Manager session's question tool: "Same + #23 and #27" (on the option "Phases 7-13 verified": spec = `docs/ROADMAP.md`'s principles + Phases 7-13 with every "on-device check pending" item verified on the Z13, plus the open bugs #16, #30, #58, #60; Phase 14 backlog and enhancements #22-#29, #31 after 1.0; plus #23 and #27 in 1.0).

A Must or a Won't changed after approval sends the line above back to "not yet".

## Musts

"On the Z13" in a check means the owner's device, so the check is marked `needs-owner-device`. Logic checks run in CI (`python3 -m pytest`, `python3 -m unittest discover -s decky/tests`).

| # | Must | Done looks like | Check |
|---|---|---|---|
| M1 | WHEN I pick a profile from the dropdown, from `z13-profile apply <name>` or from Decky, THE APP SHALL apply all of it (platform profile, TDP, curve, undervolt, lighting, battery, in principle 4's order), and a login or resume SHALL restore the active profile (Phase 8). | All three built-ins apply. The fans read non-zero whenever the curve says 50 % or more. After a Desktop↔Game Mode switch and after resume, the active profile is still in force. | `tests/` profile tests in CI; on the Z13, verification protocol steps 3-5 (`needs-owner-device`) |
| M2 | WHEN Max Fans is ON, NOTHING ELSE SHALL write a different curve; a profile with `max_fans` turns it on, and leaving that profile turns it off (principle 6). | Max Fans survives profile changes, Desktop↔Game Mode switches and resume. | `tests/` Max Fans tests; on the Z13, `fan1_input` stays at max across a mode switch (`needs-owner-device`) |
| M3 | THE DESKTOP APP SHALL have the G-Helper layout of Phase 9 complete: lighting mode combos for Rear Light and Laptop Keyboard, tile icons, and the CPU Boost (EPP) combo. | Every control from the old Dashboard and Advanced tabs exists in the new layout. A screenshot matches the §5 spec. | Phase 9 "Done when" + verification step 7 (`needs-owner-device`) |
| M4 | THE UPDATES PANEL SHALL say when z13ctl, G-Helper, SteamOS or our own release moved (Phase 10), and its **Repair install** button SHALL run `install.sh --repair` and stream the log (Phase 12b). | A newer upstream shows in red with a link; Repair finishes green on a healthy install. | `tests/` update-watch tests; on the Z13, Repair from the panel (`needs-owner-device`) |
| M5 | IN GAME MODE THE DECKY PLUGIN SHALL show the same profile list, Max Fans, TDP sliders, charge limit, per-device brightness and telemetry as the desktop app, and SHALL never show a state that did not apply (Phase 11, #16). | Applying a profile in the QAM changes the hardware exactly as the desktop does. A failed apply is shown, not hidden. | `decky/tests`; on the Z13, the Phase 11 "Done when" (`needs-owner-device`) |
| M6 | ONE COMPONENT SHALL own profile and hardware state: app profiles are mirrored into `z13ctl` named profiles, and every frontend refreshes on `z13ctl` events instead of polling (Phase 8 🚧, Phase 12 follow-ups, #23). | A change made in the app, Decky or the `z13ctl` CLI shows in all three within a second, with no polling loop. A stale slider cannot change another profile. A restart of the backend reconnects. | #23's acceptance list; on the Z13 (`needs-owner-device`) |
| M7 | AFTER A SteamOS UPDATE THE APP SHALL say so, list the post-update tasks that need attention, and run the selected ones with a plan, backups and a rollback record. It never reboots by itself (Phase 12c, #27). | The tray and Decky say "SteamOS updated: N tasks". Ticking amdgpu + ROCm and pressing Run fixes both with one reboot. Every applied repair has a before/after check and a rollback record. A redacted support bundle can be previewed. | Phase 12c "Done when" + #27's acceptance (`needs-owner-device` for the real update) |
| M8 | THE LIGHTBAR SHALL recover on its own from a wedge (Phase 7), and the Boondock script SHALL use the user `llama-swap` unit with no Docker or VM steps (Phase 7). | A lightbar timeout recovers within one retry. `boondock` runs with no Docker or VM steps. | `tests/` lightbar tests; on the Z13 (`needs-owner-device`) |
| M9 | IN GAME MODE THE QAM SHALL keep focus with a game running, and gamescope SHALL get a 48-180 Hz VRR profile if the panel accepts it (Phase 13, minus Steam's own controls, which are #29, after 1.0). | The QAM focus test passes with a game running. The VRR profile either works or is recorded as a no-go with the reason. | On the Z13 (`needs-owner-device`) |
| M10 | THE APP SHALL have no known open bugs: profiles without a fan curve apply (#60), the tests are stable across uid, root and temp names (#58), and the reported 43 GB resident memory spike is found and fixed (#30). | The issues are closed with their checks. In a 4-hour Game Mode session the backend's resident memory stays flat (the limit is set in #30 from a measured baseline). | CI for #58 and #60; a soak run for #30 (`needs-owner-device`) |

## Won'ts

- Phase 14 backlog (`docs/ROADMAP.md` §10).
- #22 explicit platform support and capability discovery; #24 charger-aware profiles and `autoswitch --ac/--battery`; #25 relabelled telemetry; #26 the unified-memory and compute-health panel; #28 display and tablet behaviour, including the Laptop Screen refresh-rate and overdrive row of Phase 9; #29 Steam's own TDP, charge-limit and profile controls in the QAM (the first Phase 13 bullet); #31 telemetry history. All are marked `after-1.0`.
- Any device other than the GZ302 on SteamOS, and any OS other than SteamOS.
- Any hardware write that does not go through `z13ctl` (principle 1).

## Problem

SteamOS has no control app for the Z13: power limits, fan curves, lighting and the side button need hand-run commands, and a SteamOS update quietly undoes the fixes.

## Solution

A G-Helper-style tray app in Desktop Mode and a Decky plugin in Game Mode, both on top of `z13ctl`. They share one state, and a repair flow puts things back after an update.

## User stories

1. As the owner, I want one dropdown to set the whole machine (power, fans, lights, battery) in either mode, so that I never run commands by hand.
2. As the owner, I want the app to tell me what a SteamOS update broke and fix it with one Run and one reboot, so that updates stop costing an evening.

## Implementation decisions

- The seven principles in `docs/ROADMAP.md` §2 are binding: z13ctl is the only hardware writer, InputPlumber stays masked, one state with two frontends, the fixed write order, no polling of `ppt_*`, Max Fans wins, and host changes are deliberate (backup, journal line, note).
- M6 replaces polling with `z13ctl` events. It probes the daemon's profile support first and keeps a compatibility path for an older daemon (#23).
- Post-update work is one recipe per task (id, title, read-only `check`, idempotent `apply`, flags), with built-ins in `tooling/post-update/` and local recipes in `~/.config/z13-helper/post-update.d/` (Phase 12c).

## Testing decisions

Logic is tested without hardware, with stdlib + pytest only and no PyQt6 import in tests (CI). The device checks follow `docs/ROADMAP.md` §12 (verification protocol) and are done by the owner on the Z13.

## Further notes

- Phase 13's first bullet (Steam's own controls) and issue #29 are the same work. The owner's answer listed #29 as after 1.0, so it stays out of M9. The same goes for the Laptop Screen row of Phase 9, which is #28.
- Platforms (R04): Linux only, SteamOS on one device. Mac and Windows do not apply: the app drives ASUS firmware through `z13ctl` and SteamOS's Game Mode.
- Target OS (2026-10-26): the current SteamOS stable, **3.9.5** (kernel 7.2), not 3.8.28. Every Must's on-device check runs on the shipped OS, so the SteamOS 3.9 compatibility work in docs/ROADMAP.md §9c (Phase 12d, #74-#80) is part of 1.0; #81 (hibernation) is not.
