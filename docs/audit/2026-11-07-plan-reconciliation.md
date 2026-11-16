# Plan and code-map reconciliation, 2026-11-07

Scope: current canonical checkout, `main` at `84f0b1e` (2026-11-06). This is a targeted recheck of
the plan, map and the high-risk findings in [`2026-11-04-code-audit.md`](2026-11-04-code-audit.md),
not a fresh line-by-line audit of every module. No tests or device actions were run for this
documentation pass.

## Current project state

- The 0.5.1 release record describes its October 30 functional snapshot. It must not be read as the
  current code inventory or as 1.0 acceptance.
- There are no open PRs. Issues #23 and #27 are closed and their state-owner/event-stream and
  post-update repair implementations are present on `main`. The GitHub PRs #153 and #154 themselves
  are closed without merge metadata; later integration commits contain the implementations.
- Current main also includes #64 EPP controls, #73 VRR script/preflight, #80 conditional resume
  ownership, #100/#122 staged Decky replacement and frontend checks, and #179 named-profile fixes.
  These code changes do not close the corresponding owner-device checks.
- Open 1.0 evidence/issues include #30, #61, #66, #73, #74, #75, #80 and #86. #81 hibernation and
  Steam's native controls (#29) remain outside 1.0 per the approved spec.

## Recheck of the Nov 4 audit's headline findings

| Earlier finding | Current disposition |
|---|---|
| Resume hook could overwrite a partially restored profile with a generic curve | **Fixed in current main (#80).** The hook only uses its protective fallback when restore did not run; a restore that ran and returned a step failure is logged and does not get a second curve write. It first defers only when z13ctl 1.4.2+ and the file-only ownership check match. Five-cycle device evidence remains open. |
| Boondock read live Armoury PPT values and claimed unsupported writes succeeded | **Read hazard fixed in source.** The script now snapshots profile values and checks `z13ctl` command status. Its standalone fallback remains 20/30/40 W, which may not fit the current Armoury range; verify the built-in profile values and behavior on the device before calling the Boondock path complete. |
| Desktop battery edits were not persisted to the active profile | **Fixed in current main (#23).** Desktop edits save through `ProfileControl`/`state_authority`; legacy `config.json` state is migrated and removed. |
| Profile apply could drop Max Fans intent on errors | **Fixed in current main.** `profiles.apply()` restores the initial intent on ordinary failures and records a suspended state for uncertain timeouts; targeted tests were added in subsequent work. Hardware response remains a device check. |
| Decky could send an unchecked platform profile and bypass profile application | **Fixed in current main (#23).** `Plugin.set_profile()` maps accepted bases to app profiles and uses the profile engine. Production profile and field writes share the state authority. |
| Resume fallback wrote fan PWM sysfs directly | **Fixed in current main (#80).** The resume fallback uses z13ctl; no PWM write remains in that script. The separate lightbar USB reauthorization is still a deliberate recovery action, not a z13ctl hardware command. |
| No cross-process apply lock / duplicated state writers | **Main implementation is present (#23).** `state_authority.transaction()` uses the shared strict file lock across full applies and writes. Decky's production writers now route through it; installed `py_modules` are deployment copies of shared modules. Verify daemon and multi-frontend behavior on-device. |
| Hardware wrapper did not validate TDP ranges or partial rail writes | **Partly fixed.** Partial rail calls are rejected and generic 5–93 W/order checks exist. Those generic limits are not the same as live per-rail Armoury bounds, so profile/CLI apply paths still need a single range-aware validation policy; stored profiles can outlive a kernel/interface change. |
| Post-update modules had no production caller | **Fixed in current main (#27).** Desktop and Decky expose the post-update view and repair operations, and the production driver records rollback results. A real update/repair/failure-rollback cycle remains unverified. |

## Remaining code-review risks from the older audit

- The `HardwareController` is safer than the Nov 4 snapshot, but range validation still needs to be
  consistent between the profile engine, CLI, Desktop and Decky against detected per-rail bounds.
- `dashboard_ui.py` retains the InputPlumber D-Bus fallback. InputPlumber must remain masked; if it
  becomes active, this fallback can request interception of all controller devices. Decide whether
  this legacy path should be removed or make the fallback fail closed.
- The older audit did not line-audit several large areas (installer, update dialog, Decky TypeScript,
  post-update runner). The current map describes their wiring, not a fresh security or correctness
  certification. Continue focused review from open issues and device evidence.
- Automated checks do not establish Game Mode interaction, fan response, resume/cold-boot behavior,
  actual VRR timing acceptance, installer frontend success on the live Steam UI, or memory behavior
  over 24 hours. Track these separately in the roadmap and issues.

## Documentation corrections applied

- `docs/ROADMAP.md` now distinguishes implemented code from on-device acceptance, updates the phase
  status for #23/#27/#64/#66/#73/#80, and removes obsolete z13ctl 1.1.6 adoption steps.
- `SPEC.md` and `PIPELINE.md` now reflect the integrated work and remaining 1.0 gates.
- `MAP/README.md` and `MAP/decky.md` now identify the current main commit and describe the shared
  production state owner without claiming Decky maintains separate production writers.

