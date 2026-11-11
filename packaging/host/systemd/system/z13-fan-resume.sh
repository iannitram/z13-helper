#!/bin/sh
# Re-apply the active Z13-Helper profile after resume (Z13 GZ302EA).
# History: 2026-06-24 wrote a hard-coded protective curve; 2026-10-01 pinned Max Fans
# when ON; 2026-10-09 restored the active profile through the app engine. z13ctl 1.4.2+
# now owns resume restore when its saved named profile matches; the hook defers then.
# Lives in /etc/systemd/system (atomic-update keep list: /etc/atomic-update.conf.d/z13-bucket1.conf).
# DECK_HOME/DECK_USER are overridable so the test suite can point this script at a
# fake tree instead of the real deck account; the device always gets the defaults.
DECK_HOME="${Z13_FAN_RESUME_DECK_HOME:-/home/deck}"
DECK_USER="${Z13_FAN_RESUME_DECK_USER:-deck}"
Z13CTL="$DECK_HOME/.local/bin/z13ctl"
PROFILE="$DECK_HOME/.local/bin/z13-profile"
# First anchor at 20 C: below the first anchor the EC may hold its previous duty (#86).
CURVE="20:128,40:128,50:191,60:230,70:255,75:255,80:255,90:255"
MAXF="$DECK_HOME/.config/z13-helper/max-fans.json"

# Every executable under the deck user's home runs *as* deck, even on the fallback
# path: root never executes a binary (or symlink) that deck's own account can replace.
as_deck() {
    runuser -u "$DECK_USER" -- env HOME="$DECK_HOME" XDG_RUNTIME_DIR=/run/user/1000 "$@"
}

# z13ctl 1.4.2+ waits for the EC and reconciles its active named profile after wake.
# Defer only when both its version and the file-only profile check prove ownership.
if [ -x "$Z13CTL" ] && [ -x "$PROFILE" ]; then
    version_output=$(as_deck timeout 3 "$Z13CTL" --version 2>/dev/null)
    version_rc=$?
    if [ "$version_rc" -eq 0 ]; then
        # Accept only the documented three-token form with a plain numeric version.
        set -f
        set -- $version_output
        set +f
        if [ "$#" -eq 3 ] && [ "$1" = "z13ctl" ] && [ "$2" = "version" ]; then
            version=$3
            case "$version" in
                *[!0-9.]*|.*|*..*|*.) version="" ;;
            esac
            case "$version" in
                *.*.*) ;;
                *) version="" ;;
            esac
            if [ -n "$version" ]; then
                old_ifs=$IFS
                IFS=.
                set -- $version
                IFS=$old_ifs
                if [ "$#" -eq 3 ]; then
                    major=$1 minor=$2 patch=$3
                    if { [ "$major" -gt 1 ] ||
                         { [ "$major" -eq 1 ] && [ "$minor" -gt 4 ]; } ||
                         { [ "$major" -eq 1 ] && [ "$minor" -eq 4 ] && [ "$patch" -ge 2 ]; }; }; then
                        as_deck timeout 3 "$PROFILE" resume-owner >/dev/null 2>&1
                        owner_rc=$?
                        if [ "$owner_rc" -eq 10 ]; then
                            logger -t z13-fan-resume "z13ctl 1.4.2+ owns the resume restore; hook deferred"
                            exit 0
                        fi
                    fi
                fi
            fi
        fi
    fi
fi

# Keep the full restore and protective fallback for every unproven case.
sleep 3

restore_rc=127
if [ -x "$PROFILE" ] && [ -f "$DECK_HOME/.config/z13-helper/profiles.json" ]; then
    as_deck "$PROFILE" restore >/dev/null 2>&1
    restore_rc=$?
    if [ "$restore_rc" -eq 0 ]; then
        logger -t z13-fan-resume "re-applied active profile after resume (z13-profile restore)"
        exit 0
    fi
    if [ "$restore_rc" -ne 127 ]; then
        # restore ran but one step failed (e.g. a wedged lightbar). Do not
        # overwrite the profile curve it did apply; report the live fan state.
        fan_state="unavailable"
        if [ -x "$Z13CTL" ]; then
            fan_state=$(as_deck "$Z13CTL" fancurve --get 2>/dev/null | sed -n '1p')
            [ -n "$fan_state" ] || fan_state="unavailable"
        fi
        logger -t z13-fan-resume "profile restore ran with a failed step (rc=$restore_rc); fan state: $fan_state"
        exit 0
    fi
fi

# Fallback: the old behaviour, only when restore did not run at all.
# Still runs z13ctl as deck, never as root (#5).
if grep -q '"enabled": *true' "$MAXF" 2>/dev/null; then
    CURVE="20:100%,30:100%,40:100%,50:100%,60:100%,70:100%,80:100%,90:100%"
fi
if [ -x "$Z13CTL" ] && as_deck "$Z13CTL" fancurve --set "$CURVE" >/dev/null 2>&1; then
    logger -t z13-fan-resume "profile restore unavailable; re-applied curve after resume ($CURVE)"
else
    [ -x "$Z13CTL" ] && as_deck "$Z13CTL" fancurve --reset >/dev/null 2>&1
    logger -t z13-fan-resume "WARN curve re-apply FAILED; firmware-auto reset after resume"
fi
