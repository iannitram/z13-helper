#!/usr/bin/env bash
# Z13-Helper — one-shot installer / repair (ROADMAP §9a).
#
#   ./install.sh            full install: asks "install dependencies?" (latest z13ctl,
#                           latest Decky Loader), then everything below
#   ./install.sh --repair   after a SteamOS update: no prompts, puts back only what
#                           differs, keeps the installed z13ctl version
#   ./install.sh --dry-run  show what would change, write nothing (combine with either)
#   ./install.sh --yes      full install without prompts
#   ./install.sh --rebuild  rebuild the Flatpak even if it matches the repo
#
# Order (each step independent; a failure is reported, the rest still runs):
#   1. preflight     SteamOS/kernel, `users` group, sudo, network
#   2. z13ctl        src/installers.py (sha256-verified release, `sudo z13ctl setup`,
#                    daemon units with --no-button)
#   3. Decky Loader  src/installers.py (official installer, stale-:1337 cleanup)
#   4. Decky plugin  pnpm build -> ~/homebrew/plugins/z13-helper, clean loader restart
#                    (after the loader, which would otherwise reload the old plugin)
#   5. Flatpak       flatpak/build.sh when the installed tree differs from the repo
#   6. host files    ~/.local/bin scripts + user units (tooling/install_check.py manifest)
#   7. /etc          resume hook, lightbar reset unit + polkit rule, atomic-update
#                    keep-list entries (listed and asked first)
#   8. verify        tooling/install_check.py verify (runs `z13-profile restore`)
#
# Every replaced file is copied to ~/.local/state/z13-helper/install-backups/<stamp>/
# first; /etc changes also get a `logger -t z13-install` journal line. The run log is
# ~/.local/state/z13-helper/install-<stamp>.log, and the result is recorded in
# ~/.config/z13-helper/install.json (with this checkout's path, for the app's
# "Repair install" button). Exit status: 0 = everything verified green.
#
# A dry run writes nothing outside ~/.local/state/z13-helper (its log).

set -uo pipefail

REPO=$(cd "$(dirname "$0")" && pwd)
MODE=install DRY=0 YES=0 REBUILD=0
for a in "$@"; do
    case "$a" in
        --repair) MODE=repair ;;
        --dry-run) DRY=1 ;;
        --yes|-y) YES=1 ;;
        --rebuild) REBUILD=1 ;;
        -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $a (try --help)" >&2; exit 2 ;;
    esac
done
if [[ $EUID -eq 0 ]]; then
    echo "Run as deck, not root (it uses sudo where it must)." >&2
    exit 2
fi

STAMP=$(date +%Y%m%d-%H%M%S)
STATE="$HOME/.local/state/z13-helper"
BACKUP="$STATE/install-backups/$STAMP"
LOG="$STATE/install-$STAMP.log"
# Rename (2026-10-15, Z13 Controller -> Z13-Helper): the state folder holds the run
# logs, so it moves before the log opens; everything else moves in rename_migration().
# move_dir OLD NEW: move OLD's contents into NEW (OLD's files win: they're the real
# ones; NEW may only hold a dry run's log), then leave a symlink at OLD.
move_dir() {
    [[ -d $1 && ! -L $1 ]] || return 1
    mkdir -p "$2" || return 1
    cp -a "$1"/. "$2"/ && rm -rf "$1" && ln -s "$(basename "$2")" "$1"
}
if ((!DRY)); then
    move_dir "$HOME/.local/state/z13-controller" "$STATE" && MOVED_STATE=1
fi
mkdir -p "$STATE"
((DRY)) && LOG="$STATE/install-$STAMP-dryrun.log"
# tee -p: keep logging if our stdout goes away mid-run (the app's "Repair install"
# button streams this, and the app restarts itself near the end of a repair).
exec > >(tee -p -a "$LOG") 2>&1
# The verify step imports src/ modules; don't leave __pycache__ in the checkout.
export PYTHONDONTWRITEBYTECODE=1

PY="python3"
CHECK="$REPO/tooling/install_check.py"
INSTALLERS="$REPO/src/installers.py"
PLUGIN_DIR="$HOME/homebrew/plugins/z13-helper"
PLUGIN_FILES=(plugin.json package.json main.py dist/index.js dist/index.js.map
              py_modules/fan_watchdog.py py_modules/sensor_reader.py)
# py_modules/X comes from src/X (shared with the desktop app, installed root-owned:
# docs/adr/0001-decky-imports-root-owned-copies.md); everything else from decky/.
plugin_src() { [[ $1 == py_modules/* ]] && echo "$REPO/src/${1#py_modules/}" || echo "$REPO/decky/$1"; }
export Z13_INSTALL_DRY_RUN=$DRY

FAILED=() CHANGED=()
APP_REBUILT=0 PLUGIN_CHANGED=0 NET=1 DESKTOP=0

say()  { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '  \033[33mwarn:\033[0m %s\n' "$*"; }
fail() { printf '  \033[31mFAIL:\033[0m %s\n' "$*"; FAILED+=("$*"); }

# act CMD...   run it (echoing), or just print it in a dry run
act() {
    if ((DRY)); then printf '  (dry run) %s\n' "$*"; return 0; fi
    printf '  $ %s\n' "$*"
    "$@"
}

# have_tty: a controlling terminal we can actually open. (`-r /dev/tty` is true
# even without one: the node is 0666.)
have_tty() { { : </dev/tty; } 2>/dev/null; }

# ask "question"  -> 0 for yes. Repair and --yes answer yes; no tty and no --yes -> abort.
ask() {
    if [[ $MODE == repair ]] || ((YES || DRY)); then return 0; fi
    if ! have_tty; then
        echo "No terminal to ask on; re-run with --yes or --repair." >&2
        exit 2
    fi
    local ans
    read -r -p "  $1 [Y/n] " ans </dev/tty
    [[ -z $ans || $ans == [Yy]* ]]
}

# backup DEST [sudo]   copy an existing file into the backup tree (readable by deck)
backup() {
    if [[ ${2:-} == sudo ]]; then
        # deck may not even be able to stat it (polkit's rules.d is root:polkitd 0750).
        sudo test -e "$1" || return 0
        mkdir -p "$BACKUP$(dirname "$1")"
        sudo cat "$1" >"$BACKUP$1"
    else
        [[ -e $1 ]] || return 0
        mkdir -p "$BACKUP$(dirname "$1")"
        cp -a "$1" "$BACKUP$1"
    fi
}

# same_root SRC DEST   0 if the root-owned DEST matches SRC. Falls back to
# `sudo -n cmp` when deck can't read DEST; without sudo it counts as different.
same_root() {
    if cmp -s "$1" "$2" 2>/dev/null; then return 0; fi
    [[ -r $2 ]] && return 1
    sudo -n true 2>/dev/null && sudo -n cmp -s "$1" "$2" 2>/dev/null
}

# ----------------------------------------------------------------------
# 0. rename migration: Z13 Controller -> Z13-Helper (2026-10-15)
# ----------------------------------------------------------------------
OLD_APP_ID="io.github.z13.Controller"
APP_ID="io.github.iannitram.Z13Helper"
OLD_UNIT="z13-controller.service"
OLD_PLUGIN_DIR="$HOME/homebrew/plugins/z13-decky"

# Stops the old app and unit, then moves the config folder (profiles, Max Fans, install
# record) to the new name, leaving a symlink at the old path. The old Flatpak goes once
# the new one is installed (flatpak_app); the old Decky plugin goes in decky_plugin.
rename_migration() {
    local old_cfg="$HOME/.config/z13-controller" new_cfg="$HOME/.config/z13-helper" todo=0
    ((${MOVED_STATE:-0})) && CHANGED+=("moved ~/.local/state/z13-controller -> ~/.local/state/z13-helper")
    [[ -e $HOME/.config/systemd/user/$OLD_UNIT ]] && todo=1
    [[ -d $old_cfg && ! -L $old_cfg ]] && todo=1
    ((todo)) || return 0
    say "Rename: Z13 Controller -> Z13-Helper"
    if [[ -e $HOME/.config/systemd/user/$OLD_UNIT ]]; then
        act systemctl --user disable --now "$OLD_UNIT"
        act flatpak kill "$OLD_APP_ID" 2>/dev/null
        ((DRY)) || backup "$HOME/.config/systemd/user/$OLD_UNIT"
        act rm -f "$HOME/.config/systemd/user/$OLD_UNIT"
        act systemctl --user daemon-reload
        CHANGED+=("removed $OLD_UNIT")
    fi
    if [[ -d $old_cfg && ! -L $old_cfg ]]; then
        if ((DRY)); then
            info "(dry run) move ~/.config/z13-controller -> ~/.config/z13-helper, symlink the old path"
        elif move_dir "$old_cfg" "$new_cfg"; then
            CHANGED+=("moved ~/.config/z13-controller -> ~/.config/z13-helper")
        else
            fail "move ~/.config/z13-controller -> ~/.config/z13-helper"
        fi
    fi
}

# ----------------------------------------------------------------------
# 1. preflight
# ----------------------------------------------------------------------
preflight() {
    say "Preflight ($MODE$( ((DRY)) && echo ', dry run'))"
    # shellcheck disable=SC1091
    . /etc/os-release 2>/dev/null
    info "SteamOS ${VERSION_ID:-?} (build ${BUILD_ID:-?}), kernel $(uname -r)"
    info "repo $REPO @ $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo '?')$(git -C "$REPO" diff --quiet 2>/dev/null || echo ' (uncommitted changes)')"
    if systemctl --user is-active -q plasma-workspace.target; then
        DESKTOP=1; info "session: Desktop (Plasma)"
    else
        info "session: Game Mode / no Plasma (the app restart is skipped; it starts with Plasma)"
    fi
    id -nG | tr ' ' '\n' | grep -qx users \
        || warn "deck is not in group 'users': z13ctl's udev grants won't apply (sudo usermod -aG users deck, then log out/in)"
    for t in python3 flatpak curl systemctl; do
        command -v "$t" >/dev/null || fail "missing tool: $t"
    done
    if curl -fsS -m 10 -o /dev/null https://api.github.com; then
        info "network: ok"
    else
        NET=0; warn "no network: dependency fetches, the plugin build and a Flatpak rebuild will be skipped"
    fi
    if ((DRY)); then
        info "sudo: not needed for a dry run"
    elif sudo -n true 2>/dev/null; then
        info "sudo: ok (passwordless)"
    elif have_tty && sudo -v </dev/tty; then
        info "sudo: ok"
    else
        fail "sudo is not available; z13ctl setup, the Decky steps and /etc are skipped"
        return 1
    fi
}

# ----------------------------------------------------------------------
# 2 + 3. dependencies: z13ctl, then Decky Loader
# ----------------------------------------------------------------------
dependencies() {
    say "Dependencies: z13ctl, Decky Loader"
    local upgrade=0
    if [[ $MODE == install ]]; then
        if ask "Install dependencies? (latest z13ctl and latest Decky Loader, fetched now)"; then
            upgrade=1
        else
            info "Not upgrading; checking the installed ones still work instead."
        fi
    fi
    if ((upgrade && NET)); then
        $PY "$INSTALLERS" install z13ctl || fail "z13ctl install"
        # An "already up to date" install skips setup; make sure perms and units are right.
        $PY "$INSTALLERS" repair z13ctl || fail "z13ctl setup/daemon"
        $PY "$INSTALLERS" install decky || fail "Decky Loader install"
    else
        ((upgrade)) && warn "no network: repairing the installed versions instead of upgrading"
        $PY "$INSTALLERS" repair z13ctl || fail "z13ctl repair"
        $PY "$INSTALLERS" repair decky || fail "Decky Loader repair"
    fi
}

# ----------------------------------------------------------------------
# 4. Decky plugin component
# ----------------------------------------------------------------------
find_pnpm() {
    command -v pnpm 2>/dev/null && return 0
    # nvm isn't on a non-interactive PATH; take the newest node that has pnpm.
    ls -d "$HOME"/.nvm/versions/node/*/bin/pnpm 2>/dev/null | sort -V | tail -1
}

restart_loader() {
    # stop -> kill -> start: a plain restart can leave the old loader holding :1337.
    if ((DRY)); then
        info "(dry run) sudo systemctl stop/kill/start plugin_loader.service, wait for 'z13-helper up'"
        return 0
    fi
    act sudo systemctl stop plugin_loader.service
    sleep 2
    sudo systemctl kill plugin_loader.service 2>/dev/null
    sleep 1
    local pid
    pid=$(sudo ss -Hltnp 'sport = :1337' | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
    if [[ -n $pid ]]; then
        warn "a stale loader (pid $pid) still holds :1337 outside the unit; killing it"
        sudo kill "$pid"; sleep 2
    fi
    sudo systemctl reset-failed plugin_loader.service 2>/dev/null
    local marker="$STATE/.loader-restart" i newest
    touch "$marker"
    act sudo systemctl start plugin_loader.service
    for i in $(seq 1 30); do
        sleep 1
        newest=$(ls -t "$HOME"/homebrew/logs/z13-helper/*.log 2>/dev/null | head -1)
        if [[ -n $newest && $newest -nt $marker ]] && grep -q 'z13-helper up' "$newest"; then
            info "z13-helper backend up (${i}s)"
            return 0
        fi
    done
    fail "z13-helper backend did not log 'up' within 30 s after the loader restart"
}

decky_plugin() {
    say "Decky plugin (z13-helper)"
    local pnpm
    pnpm=$(find_pnpm)
    if ((DRY)); then
        info "(dry run) would build with ${pnpm:-<no pnpm: use the existing decky/dist>}"
    elif [[ -n $pnpm ]] && { ((NET)) || [[ -d $REPO/decky/node_modules ]]; }; then
        export PATH="$(dirname "$pnpm"):$PATH"
        (
            cd "$REPO/decky" || exit 1
            [[ -d node_modules ]] || "$pnpm" install --frozen-lockfile
            "$pnpm" run build
        ) >/dev/null 2>"$STATE/decky-build.err" || {
            cat "$STATE/decky-build.err"
            # Don't replace a working installed plugin with a stale or partial dist/.
            fail "decky build (pnpm run build); installed plugin left as is"
            return 1
        }
        info "built decky/dist with $pnpm"
    elif [[ -f $REPO/decky/dist/index.js ]]; then
        warn "no pnpm (or no network for its first install); installing the existing decky/dist"
    else
        fail "no pnpm and no decky/dist: can't build the plugin (install node+pnpm via nvm)"
        return 1
    fi

    if [[ -d $OLD_PLUGIN_DIR ]]; then
        info "removing the old plugin $OLD_PLUGIN_DIR (renamed to z13-helper)"
        if ((!DRY)); then
            mkdir -p "$BACKUP" && sudo tar -C "$(dirname "$OLD_PLUGIN_DIR")" -cf "$BACKUP/z13-decky-plugin.tar" z13-decky
        fi
        act sudo rm -rf "$OLD_PLUGIN_DIR" && CHANGED+=("removed old plugin z13-decky")
        PLUGIN_CHANGED=1
    fi
    local f diff=()
    for f in "${PLUGIN_FILES[@]}"; do
        cmp -s "$(plugin_src "$f")" "$PLUGIN_DIR/$f" || diff+=("$f")
    done
    if ((${#diff[@]} == 0)); then
        info "installed plugin matches the build"
        ((PLUGIN_CHANGED)) && restart_loader
        return 0
    fi
    info "differs: ${diff[*]}"
    if ((!DRY)); then
        for f in "${PLUGIN_FILES[@]}"; do backup "$PLUGIN_DIR/$f" sudo; done
    fi
    for f in "${PLUGIN_FILES[@]}"; do
        act sudo install -D -m 644 -o root -g root "$(plugin_src "$f")" "$PLUGIN_DIR/$f" || fail "install plugin $f"
    done
    CHANGED+=("decky plugin (${diff[*]})")
    PLUGIN_CHANGED=1
    restart_loader
}

# ----------------------------------------------------------------------
# 5. Flatpak app
# ----------------------------------------------------------------------
flatpak_app() {
    say "Desktop app (Flatpak)"
    local out rc
    out=$($PY "$CHECK" flatpak-diff); rc=$?
    if ((rc == 0 && !REBUILD)); then
        info "installed Flatpak matches the repo"
    else
        case $rc in
            0) info "rebuild requested" ;;
            2) info "not installed" ;;
            *) info "differs from the repo: $(echo "$out" | head -3 | tr '\n' ' ')" ;;
        esac
        if ((!NET)); then
            fail "Flatpak build needs network (PyQt6 from PyPI); skipped"
        else
            act bash "$REPO/flatpak/build.sh" && { APP_REBUILT=1; CHANGED+=("flatpak rebuilt"); } || fail "flatpak build"
        fi
    fi
    # Renamed app: drop the old Flatpak once the new one is in place.
    if flatpak info "$OLD_APP_ID" >/dev/null 2>&1 && flatpak info "$APP_ID" >/dev/null 2>&1; then
        act flatpak kill "$OLD_APP_ID" 2>/dev/null
        act flatpak uninstall --noninteractive "$OLD_APP_ID" && CHANGED+=("uninstalled old Flatpak $OLD_APP_ID")
    fi
}

# ----------------------------------------------------------------------
# 6. host scripts + user units   7. /etc
# ----------------------------------------------------------------------
host_files() {
    say "Host scripts and user units"
    local rel dest mode root n=0 units_changed=0 watcher_changed=0 app_unit_changed=0
    while IFS=$'\t' read -r rel dest mode root; do
        ((root)) && continue
        if cmp -s "$REPO/$rel" "$dest"; then continue; fi
        info "${dest/#$HOME/\~}: $([[ -e $dest ]] && echo differs || echo missing)"
        ((DRY)) || backup "$dest"
        act install -D -m "$mode" "$REPO/$rel" "$dest" || { fail "install $dest"; continue; }
        CHANGED+=("${dest/#$HOME/\~}"); n=$((n + 1))
        [[ $dest == *.service ]] && units_changed=1
        [[ $dest == *z13-button-watcher* || $dest == *z13_cdp.py ]] && watcher_changed=1
        [[ $dest == *z13-helper.service ]] && app_unit_changed=1
    done < <($PY "$CHECK" manifest)
    ((n)) || info "all match the repo"
    ((units_changed)) && act systemctl --user daemon-reload

    local u
    for u in z13-battery-default.service z13-button-watcher.service z13-helper.service z13-restore-profile.service; do
        [[ $(systemctl --user is-enabled "$u" 2>/dev/null) == enabled ]] && continue
        act systemctl --user enable "$u" && CHANGED+=("enabled $u")
    done
    if ((watcher_changed)) || ! systemctl --user is-active -q z13-button-watcher.service; then
        act systemctl --user restart z13-button-watcher.service || fail "start z13-button-watcher"
    fi
    if ((DESKTOP)) && { ((APP_REBUILT || app_unit_changed)) || ! pgrep -f '[/]app/share/z13-helper/main[.]py' >/dev/null; }; then
        act systemctl --user restart z13-helper.service || fail "restart z13-helper.service"
        ((DRY)) || sleep 12   # ExecStartPre sleeps 10 s before the app comes up
    fi
}

etc_files() {
    say "/etc: resume hook, lightbar reset + polkit rule, atomic-update keep-list"
    local rel dest mode root todo=() keep_missing=() p units_changed=0
    while IFS=$'\t' read -r rel dest mode root; do
        ((root)) || continue
        same_root "$REPO/$rel" "$dest" || todo+=("$rel"$'\t'"$dest"$'\t'"$mode")
    done < <($PY "$CHECK" manifest)
    local keep=/etc/atomic-update.conf.d/z13-bucket1.conf
    while read -r p; do
        grep -qxF "$p" "$keep" 2>/dev/null || keep_missing+=("$p")
    done < <($PY "$CHECK" keep-list)
    local enabled=1
    [[ $(systemctl is-enabled z13-fan-resume.service 2>/dev/null) == enabled ]] || enabled=0

    if ((${#todo[@]} == 0 && ${#keep_missing[@]} == 0 && enabled)); then
        info "resume hook installed + enabled; keep-list complete"
        return 0
    fi
    local t
    for t in "${todo[@]}"; do IFS=$'\t' read -r rel dest mode <<<"$t"; info "will install $dest"; done
    for p in "${keep_missing[@]}"; do info "will add to keep-list: $p"; done
    ((enabled)) || info "will enable z13-fan-resume.service"
    ask "Apply these /etc changes?" || { warn "/etc left as is"; return 0; }

    for t in "${todo[@]}"; do
        IFS=$'\t' read -r rel dest mode <<<"$t"
        ((DRY)) || backup "$dest" sudo
        # -D: creates the parent (e.g. /etc/polkit-1/rules.d) if it's missing.
        act sudo install -D -m "$mode" -o root -g root "$REPO/$rel" "$dest" || { fail "install $dest"; continue; }
        ((DRY)) || logger -t z13-install "installed $dest from z13-helper $rel (backup: $BACKUP)"
        CHANGED+=("$dest")
        [[ $dest == *.service ]] && units_changed=1
    done
    # polkitd picks up rules.d changes by itself; systemd needs a reload for units.
    ((units_changed)) && act sudo systemctl daemon-reload
    if ((!enabled)); then
        act sudo systemctl enable z13-fan-resume.service && CHANGED+=("enabled z13-fan-resume.service")
    fi
    if ((${#keep_missing[@]})); then
        ((DRY)) || backup "$keep" sudo
        if ((DRY)); then
            info "(dry run) would append ${#keep_missing[@]} line(s) to $keep"
        else
            if sudo install -d -m 755 "$(dirname "$keep")" \
                && { echo; echo "# Z13-Helper (added by install.sh $STAMP)"; printf '%s\n' "${keep_missing[@]}"; } \
                    | sudo tee -a "$keep" >/dev/null; then
                logger -t z13-install "added ${keep_missing[*]} to $keep"
                CHANGED+=("$keep (+${#keep_missing[@]})")
            else
                fail "append to $keep"
            fi
        fi
    fi
}

# ----------------------------------------------------------------------
# 8. verify + record
# ----------------------------------------------------------------------
verify() {
    say "Verify"
    if ((DRY)); then
        $PY "$CHECK" verify --no-restore
    else
        $PY "$CHECK" verify
    fi
}

record() {
    ((DRY)) && return 0
    $PY - "$MODE" "$1" "$REPO" "$LOG" <<'EOF' || warn "could not write ~/.config/z13-helper/install.json"
import json, os, re, subprocess, sys, time
mode, verify_rc, repo, log = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
def out(*a):
    try:
        return subprocess.run(a, capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
osr = dict(l.strip().split("=", 1) for l in open("/etc/os-release") if "=" in l)
m = re.search(r"\d+\.\d+\.\d+", out(os.path.expanduser("~/.local/bin/z13ctl"), "--version"))
try:
    decky = open(os.path.expanduser("~/homebrew/services/.loader.version")).read().strip()
except OSError:
    decky = None
path = os.path.expanduser("~/.config/z13-helper/install.json")
try:
    with open(path) as f:
        rec = json.load(f)
    if not isinstance(rec, dict):
        rec = {}
except (OSError, ValueError):
    rec = {}
rec.update({                      # keys this run doesn't know about are kept
    "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "mode": mode,
    "verify": "green" if verify_rc == 0 else "red",
    "repo_commit": out("git", "-C", repo, "rev-parse", "--short", "HEAD"),
    "z13ctl": m.group(0) if m else None,
    "decky_loader": decky,
    "flatpak_commit": (re.search(r"Commit:\s*(\S+)", out("flatpak", "info", "io.github.iannitram.Z13Helper")) or [None, None])[1],
    "steamos": f"{osr.get('VERSION_ID', '?').strip(chr(34))} ({osr.get('BUILD_ID', '?').strip(chr(34))})",
    "kernel": os.uname().release,
    "repo_path": repo,            # the app's "Repair install" button runs <repo_path>/install.sh
    "log": log,
})
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path + ".tmp", "w") as f:
    json.dump(rec, f, indent=2)
os.replace(path + ".tmp", path)
EOF
}

# ----------------------------------------------------------------------
main() {
    rename_migration
    if preflight; then
        dependencies
        decky_plugin
    else
        warn "skipping the sudo steps (z13ctl setup, Decky)"
    fi
    flatpak_app
    host_files
    if ((DRY)) || sudo -n true 2>/dev/null; then
        etc_files
    else
        warn "no sudo: /etc not checked"
    fi
    verify; local vrc=$?
    record "$vrc"

    say "Summary"
    if ((${#CHANGED[@]})); then
        info "$( ((DRY)) && echo 'would change' || echo 'changed'):"; printf '    - %s\n' "${CHANGED[@]}"
        [[ -d $BACKUP ]] && info "backups: $BACKUP"
    else
        info "nothing needed changing"
    fi
    if ((${#FAILED[@]})); then
        info "failed steps:"; printf '    - %s\n' "${FAILED[@]}"
    fi
    info "log: $LOG"
    if ((vrc == 0 && ${#FAILED[@]} == 0)); then
        printf '\n\033[32mAll green.\033[0m\n'
        return 0
    fi
    printf '\n\033[31mNot all green — see above.\033[0m\n'
    return 1
}

main
