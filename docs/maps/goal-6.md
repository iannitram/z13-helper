# Goal 6: Phase 12c — Post-update tasks in the Updates panel

## The goal

After a SteamOS "nuclear update" wipes `/usr`, Valve's grub file, the pacman keyring, and rolls `/etc`
changes back, the maintainer re-applies a checklist by hand. This feature gives the user a
**"After SteamOS update" tab in the Updates panel** that lists each post-update task with its status,
a checkbox, and a one-line explanation. The user ticks what they need, the app orders them
(pacman keyring first, reboot-needing last), shows the plan, asks for confirmation, then runs
everything with a streamed log — backing up files it replaces. It detects an update by comparing
`install.json`'s recorded `BUILD_ID` against the current one, surfaces a tray notification, and
offers a CLI (`install.sh --post-update`) for SSH/Game Mode. Recipes are TOML files in
`tooling/post-update/` (built-in) and `~/.config/z13-helper/post-update.d/` (local), each with a
read-only `check`, an idempotent `apply`, and flags (`sudo`, `network`, `reboot`, `pacman`,
`default_on`).

## What exists

- **`src/updates_dialog.py`** — the Updates panel (`UpdatesDialog`, `InstallDialog`, `UpdateCheckThread`).
  It shows installable rows from `update_watch.summary()`, runs `installers.py` via `InstallThread`,
  and streams the log. A new tab would slot into this dialog (the `QTabWidget` pattern already
  works for Fans + Power in `fans_window.py`).

- **`src/update_watch.py`** — upstream version watch. `check()` fetches latest versions (cached in
  `~/.config/z13-helper/updates.json` with ETags), `summary()` returns the display rows,
  `new_items()` returns keys that changed. `_steamos_version()` reads `/etc/os-release` for
  `VERSION_ID`. The same module is imported by `dashboard_ui.py` (line 36) for the tray notification
  flow.

- **`src/install_record.py`** — reads/writes `~/.config/z13-helper/install.json`. `load()` returns the
  record dict; `repo_path()` and `last_run_line()` extract display values. The `record()` function in
  `install.sh` (heredoc, line 501) writes `BUILD_ID` as part of the `"steamos"` string (e.g.
  `"3.8 (abc123)"`). The BUILD_ID is present but not parsed into its own field yet.

- **`src/installers.py`** — host-side installer for z13ctl, Decky, and SteamOS updates. `plan()` and
  `install()` actions print a log line per step. This is the pattern the post-update runner would
  follow: feed the script over `flatpak-spawn --host python3 -u -`.

- **`install.sh`** (root of repo) — one-shot installer. The `--repair` mode (line 44, `MODE=repair`)
  re-applies host files without prompts. The `verify()` step (line 488) calls
  `tooling/install_check.py verify`. The `record()` step (line 496) writes `BUILD_ID` into
  `install.json`. No `--post-update` flag exists yet.

- **`tooling/install_check.py`** — manifest + health check for host files. `verify` checks every
  manifest entry and runs `z13-profile restore`. SteamOS BUILD_ID is printed at line 192 but not
  used for detection.

- **`src/host.py`** — the stdlib-only host command runner (`host.run(argv, timeout=, as_deck=,
  input=)`). Returns `Result(rc, out, timed_out)`, never raises. Every post-update apply command
  must go through this (or `subprocess` in the runner, which wraps `host.run`).

- **`src/app_log.py`** — logging infrastructure. `clean()` strips ANSI codes (used by `InstallDialog`
  at line 57 of `updates_dialog.py`). The logging opt-in (`install_record.logging_enabled()`)
  controls whether files are written to disk.

- **`src/dashboard_ui.py`** — main window/tray. `_check_updates()` (line 637) runs
  `UpdateCheckThread`, `_updates_done()` (line 644) sets the tray badge, and
  `_open_updates()` (line 730) opens the dialog. The tray notification at line 660 is the pattern
  for "SteamOS updated: N tasks" alerts.

- **`decky/main.py`** — the Game Mode QAM plugin. It reads the same `install.json` for logging
  choice. This is where the Game Mode notification about pending post-update tasks would go.

- **`packaging/host/`** — reference copies of systemd units, udev rules, asusd, and xdg entries.
  The post-update tasks that install host files (amdgpu grub args, mt7925 udev rules, etc.) would
  reference files from here.

## What's missing

1. **BUILD_ID tracking as a separate field in `install.json`**. `install.sh`'s `record()` (line 501)
   stores it embedded in the `"steamos"` string (`"3.8 (abc123)"`). `update_watch._steamos_version()`
   reads `VERSION_ID` but not `BUILD_ID`. We need `install.json` to store `"steamos_build_id": "abc123"`
   as a separate key, and a new `update_watch._steamos_build_id()` probe that reads `/etc/os-release`
   for `BUILD_ID`.

2. **Post-update recipe format (TOML)**. No TOML parser is imported anywhere yet. The format needs:
   `id`, `title`, `fixes`, `check` (a shell command or script path), `apply` (a shell command or
   script path), `flags: [sudo, network, reboot, pacman, default_on, info_only]`. The `tomllib`
   module (stdlib, Python 3.11+) can parse TOML. This would live in `src/post_update/recipe.py` (new).

3. **Post-update task runner**. A module that loads recipes from the `tooling/post-update/` directory
   `~/.config/z13-helper/post-update.d/*.toml`, runs `check` for status, orders applies
   (pacman keyring → rest → reboot-needing), backs up files before replacing, and streams the log.
   This would be `src/post_update/runner.py` (new), modelled on `InstallThread` in
   `updates_dialog.py`.

4. **The "After SteamOS update" tab in `UpdatesDialog`**. `UpdatesDialog.__init__` (line 130) creates
   two tables (installable + tracking). A third table (post-update tasks) with checkboxes, status
   icons, and a "Run selected" button would slot in as a `QTabWidget`. The tab is hidden unless
   `BUILD_ID` changed since the last `install.sh` run.

5. **Update detection and notification**. When `BUILD_ID` changes, the tray must show
   "SteamOS updated: N post-update tasks need attention". This needs a `post_update.has_update()`
   function (new, `src/post_update/detect.py`) that compares the current `BUILD_ID` with the
   recorded one, plus a hook in `dashboard_ui._updates_done()` (line 644) to check it. The Decky
   panel also needs the same notification.

6. **`install.sh --post-update`**. The CLI flag doesn't exist. It would parse `--only` args, list
   available tasks with their status, let the user pick (TUI or `--only`), then call the runner.
   The `--repair` flow (line 560) should list pending post-update tasks after verification.

7. **Built-in recipes for tasks 1–3**. `tooling/post-update/amdgpu.toml`, `pacman_keyring.toml`,
   and `carry_forward.toml` (new) with their `check` and `apply` commands. Task 1 (amdgpu) needs
   to modify `/etc/default/grub-steamos` via `update-grub` (never `grub-mkconfig`). Task 2
   (pacman keyring) needs `steamos-readonly disable` → `pacman-key --init` → `--populate` → `enable`.
    Task 3 (carry-forward) is info-only: reads the carry-forward list (system path, may not exist).

8. **Safety guards**. Applies must refuse on non-SteamOS or non-GZ302. `steamos-readonly` must be
   re-enabled after every apply (even on failure). The runner in `src/post_update/runner.py` (new)
   must enforce this. A `post_update.safety.py` (new) could hold the guard functions.

## Small first steps

1. **Add `BUILD_ID` to `install.json` and detect changes** (1 day). Extend `install.sh`'s `record()`
   (line 501) to write `"steamos_build_id"` as a separate key. Add `update_watch._steamos_build_id()`
   that reads `/etc/os-release`. Add `post_update.has_update()` (new, `src/post_update/detect.py`)
   that compares current vs recorded. The existing `dashboard_ui._updates_done()` gets a one-line
   hook to show the badge.

2. **TOML recipe format + loader** (1 day). Create `src/post_update/recipe.py` (new) with
   `load_recipe(path)` using `tomllib`. Create `src/post_update/tasks.py` (new) with
   `load_built_in()` and `load_local()` that walk the `tooling/post-update/` directory
   `~/.config/z13-helper/post-update.d/*.toml`. Write one sample recipe:
   `tooling/post-update/carry_forward.toml` (info-only, tasks 3).

3. **Runner with ordering and safety** (2 days). `src/post_update/runner.py` (new): loads tasks,
   runs `check` for each, orders applies (pacman keyring first, reboot-needing last), backs up
   files, streams log via `pyqtSignal` (matching `InstallThread`'s pattern), and enforces
   `steamos-readonly` re-enable. Add the "After SteamOS update" tab to `UpdatesDialog` with
   checkboxes and status icons. Wire to the tray notification from step 1.

4. **`install.sh --post-update` and first built-in recipes** (2 days). Add the `--post-update` flag
   to install.sh. Add `tooling/post-update/amdgpu.toml` (task 1, sudo+reboot) and
   `tooling/post-update/pacman_keyring.toml` (task 2, sudo). The runner calls `update-grub` for
   amdgpu and `pacman-key --init` for keyring. `--repair` lists pending tasks after verify.
