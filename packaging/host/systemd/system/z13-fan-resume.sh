#!/bin/sh
# Re-apply the active Z13-Helper profile after resume (Z13 GZ302EA).
# History: 2026-06-24 wrote a hard-coded protective curve; 2026-10-01 pinned Max Fans
# when ON; 2026-10-09 now restores the *active profile* (platform profile, TDP, curve,
# Max Fans pinned if ON) through the app's engine, falling back to the old curve.
# Lives in /etc/systemd/system (atomic-update keep list: /etc/atomic-update.conf.d/z13-bucket1.conf).
# DECK_HOME/DECK_USER are overridable so the test suite can point this script at a
# fake tree instead of the real deck account; the device always gets the defaults.
wake_ts=$(date +%s)
DECK_HOME="${Z13_FAN_RESUME_DECK_HOME:-/home/deck}"
DECK_USER="${Z13_FAN_RESUME_DECK_USER:-deck}"
Z13CTL="$DECK_HOME/.local/bin/z13ctl"
PROFILE="$DECK_HOME/.local/bin/z13-profile"
# Unknown profile / Max Fans state falls back to the protective full-speed curve.
CURVE="20:100%,30:100%,40:100%,50:100%,60:100%,70:100%,80:100%,90:100%"

# Every executable under the deck user's home runs *as* deck, even on the fallback
# path: root never executes a binary (or symlink) that deck's own account can replace.
as_deck() {
    runuser -u "$DECK_USER" -- env HOME="$DECK_HOME" XDG_RUNTIME_DIR=/run/user/1000 "$@"
}

# A write into a stalled EC can hard-lock (z13ctl 1.3.3 notes). Wait for the
# measured daemon resume window, then poll only until wake + 9 s.
sleep 4

restore_rc=127
if [ -x "$PROFILE" ] && [ -f "$DECK_HOME/.config/z13-helper/profiles.json" ]; then
    deadline=$((wake_ts + 9))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        # z13-profile uses exit 10 only with post-wake journal and state evidence.
        timeout 3 runuser -u "$DECK_USER" -- env HOME="$DECK_HOME" XDG_RUNTIME_DIR=/run/user/1000 \
            "$PROFILE" restore --if-needed --check-only --since-epoch "$wake_ts" >/dev/null 2>&1
        check_rc=$?
        if [ "$check_rc" -eq 10 ]; then
            logger -t z13-fan-resume "z13ctl daemon restored the profile; hook skipped (exit 10)"
            exit 0
        fi
        if [ "$check_rc" -eq 2 ]; then
            # Older z13-profile CLIs reject the new option with argparse status 2.
            break
        fi
        if [ "$check_rc" -eq 11 ] || [ "$check_rc" -eq 124 ]; then
            break
        fi
        if [ "$check_rc" -ne 0 ]; then
            break
        fi
        sleep 1
    done

    # The state never proved current (or this is an older CLI): restore now.
    as_deck "$PROFILE" restore >/dev/null 2>&1
    restore_rc=$?
    if [ "$restore_rc" -eq 0 ]; then
        logger -t z13-fan-resume "hook re-applied the active profile"
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

# Fallback: the old behaviour, only when restore did not run at all. Unknown
# Max Fans state gets the full-speed curve. Still run z13ctl as deck, never root.
if [ -x "$Z13CTL" ] && as_deck "$Z13CTL" fancurve --set "$CURVE" >/dev/null 2>&1; then
    logger -t z13-fan-resume "profile restore unavailable; re-applied curve after resume ($CURVE)"
else
    # Unknown profile/Max Fans state is not permission to enable firmware auto:
    # that would discard a custom curve or the required full-speed pin.
    logger -t z13-fan-resume "WARN protective curve re-apply FAILED; left curve state untouched"
fi
