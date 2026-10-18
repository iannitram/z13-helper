# Goal 1 — Boondock Script Cleanup

## Goal
Remove Docker/VM dependencies from the `boondock` script and the system-level `llama-swap`
systemd unit. Point `BG_SERVICES` at user-level units (`systemctl --user`) so the script
works on SteamOS without requiring Docker, libvirt, or root-owned system services.

## What Exists

### `tooling/boondock` (109 lines, installed to `~/.local/bin/boondock`)
- **`BG_SERVICES=(llama-swap)`** (line 25) — references a system-level systemd service that
  does not exist in this repo and is not installed by `install.sh`.
- **`BG_CONTAINERS=(pm-fork-runner-z13)`** (line 26) — references a Docker container managed
  by `docker`/`docker ps`/`docker stop`/`docker start`.
- **`--pause-vm` flag** (line 36) — optional VM pause via `virsh suspend`/`virsh resume`.
- **Snapshot state** at `~/.local/state/boondock.state` — stores brightness, profile, TDP,
  active services (`svc=`), active containers (`ctr=`), and paused VMs (`vm=`).
- **`do_on()`** (lines 35–69): snapshots state, sets hardware limits, stops BG services,
  stops BG containers, optionally pauses VM.
- **`do_off()`** (lines 71–93): restores snapshot (or defaults), restarts services/containers/VMs.
- **`do_status()`** (lines 95–102): reports brightness, profile, TDP, VM state, service states.
- Uses `sudo systemctl stop/start` for services (lines 59, 88), `docker stop/start` for
  containers (lines 60, 89), `sudo virsh suspend/resume` for VMs (lines 64, 82, 90).
- Comment on line 12: "The dev-VM ('the Shuttle') is left running unless you pass
  `--pause-vm`" — interactive workloads are NOT touched by default.

### `src/profiles.py` (references boondock hooks)
- Line 84: `BOONDOCK_BIN = os.path.expanduser("~/.local/bin/boondock")`
- Line 128: "Hardware side of boondock is explicit here; the script does the rest"
- Line 640: "re-run `boondock on` (its snapshot is from the previous session)"
- Line 479: "Leave the previous profile (e.g. `boondock off` restores the screen)"

### `tooling/install_check.py`
- Line 67: `("tooling/boondock", f"{BIN}/boondock", "755", False)` — boondock is installed
  and verified as part of the host-file manifest.

### `docs/ROADMAP.md`
- Line 100–101: Lists "Boondock script cleanup" as a pending goal.

## What's Missing

1. **No `llama-swap` systemd unit in this repo.** The service name `llama-swap` appears only
   in `BG_SERVICES` (a variable in boondock). There is no unit file, no Flatpak manifest entry,
   and no installation step for it. It was likely an external dependency (a local LLM stack)
   that the maintainer ran manually or installed from elsewhere.

2. **No Docker or VM infrastructure in the repo.** The script depends on `docker`, `docker ps`,
   `docker stop`, `docker start`, `virsh`, and `sudo virsh` — none of which are part of the
   z13-helper project. These are the maintainer's personal dev tools (local LLM containers,
   a VM called "dev-vm" / "the Shuttle").

3. **No user-level replacement for `llama-swap`.** The roadmap says to "point `BG_SERVICES` at
   the user unit" but no such user unit exists yet. The maintainer's wiki documents the intended
   target, but it has not been implemented in this repo.

4. **`BG_CONTAINERS` is a dead list.** Since Docker is not part of the project and the container
   `pm-fork-runner-z13` is not defined anywhere, this list serves no purpose after Docker removal.

5. **`--pause-vm` is dead code.** Without virsh installed and no VM defined in the project,
   the `--pause-vm` flag and all VM-related code paths (lines 36, 48, 63–67, 82, 90, 99)
   are non-functional.

## Small First Steps

### Step 1: Remove Docker/VM code paths from `tooling/boondock`
- Delete lines 26 (`BG_CONTAINERS`), 47 (container snapshot), 60 (container stop), 63–67
  (VM pause logic in `do_on`), 81 (container restart), 82 (VM resume in `do_off`), 89
  (container restart in defaults), 90 (VM resume in defaults), 99 (VM status line).
- Remove `--pause-vm` from the usage string (line 108) and the flag check (line 36).
- Remove `local pause_vm` variable and all `pause_vm` conditional blocks.

### Step 2: Replace `BG_SERVICES=(llama-swap)` with user-level equivalents
- Change line 25 to `BG_SERVICES=()` (empty list) until the maintainer specifies which user
  units should be included. The roadmap says the wiki documents the target — once known, add
  the user unit names here.
- Update lines 46, 59, 80, 88, 100 to use `systemctl --user` instead of bare `systemctl`
  (or `sudo systemctl`). Under SteamOS, user units don't need `sudo`.

### Step 3: Update snapshot format
- Remove `ctr=` and `vm=` lines from the snapshot (lines 47–48, 81–82, 90).
- Keep `svc=` lines but change their restart logic to use `systemctl --user`.

### Step 4: Verify `install_check.py` still passes
- The manifest entry at line 67 references `tooling/boondock` — after edits, run
  `python3 tooling/install_check.py verify` to confirm the installed file matches.

### Step 5: Update `src/profiles.py` references (if needed)
- Line 128 says "Hardware side of boondock is explicit here; the script does the rest" —
  this is still accurate after cleanup since boondock handles non-hardware effects (services).
- No changes needed unless the boondock hook interface changes.

## Files Changed
- `tooling/boondock` — edited (remove Docker/VM, switch to user units)

## Files Not Changed
- `tooling/install_check.py` — manifest entry stays; boondock is still installed.
- `src/profiles.py` — boondock hook references remain valid.
- `docs/ROADMAP.md` — mark goal as complete after verification.

## Verification
- `bash tooling/boondock on` — should succeed without Docker/virsh errors.
- `bash tooling/boondock off` — should restore from snapshot without container/VM errors.
- `bash tooling/boondock status` — should report brightness, profile, TDP, and service states.
- `python3 tooling/install_check.py verify` — should pass with no reds for boondock.
