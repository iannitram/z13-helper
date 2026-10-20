#!/bin/sh
# Re-apply the active Z13-Helper profile after resume (Z13 GZ302EA).
# History: 2026-06-24 wrote a hard-coded protective curve; 2026-10-01 pinned Max Fans
# when ON; 2026-10-09 now restores the *active profile* (platform profile, TDP, curve,
# Max Fans pinned if ON) through the app's engine, falling back to the old curve.
# Lives in /etc/systemd/system (atomic-update keep list: /etc/atomic-update.conf.d/z13-bucket1.conf).
# DECK_HOME/DECK_USER are overridable so the test suite can point this script at a
# fake tree instead of the real deck account; the device always gets the defaults.
DECK_HOME="${Z13_FAN_RESUME_DECK_HOME:-/home/deck}"
DECK_USER="${Z13_FAN_RESUME_DECK_USER:-deck}"
Z13CTL="$DECK_HOME/.local/bin/z13ctl"
PROFILE="$DECK_HOME/.local/bin/z13-profile"
CURVE="40:127,50:191,60:229,70:255,75:255,80:255,85:255,90:255"
MAXF="$DECK_HOME/.config/z13-helper/max-fans.json"

# Every executable under the deck user's home runs *as* deck, even on the fallback
# path: root never executes a binary (or symlink) that deck's own account can replace.
as_deck() {
    runuser -u "$DECK_USER" -- env HOME="$DECK_HOME" XDG_RUNTIME_DIR=/run/user/1000 "$@"
}

# Give the EC a moment after wake; a write into a stalled EC can hard-lock (z13ctl 1.3.3 notes).
sleep 3

if [ -x "$PROFILE" ] && [ -f "$DECK_HOME/.config/z13-helper/profiles.json" ] \
   && as_deck "$PROFILE" restore >/dev/null 2>&1; then
    logger -t z13-fan-resume "re-applied active profile after resume (z13-profile restore)"
    exit 0
fi

# Fallback: the old behaviour. Still runs z13ctl as deck, never as root (#5).
if grep -q '"enabled": *true' "$MAXF" 2>/dev/null; then
    CURVE="20:100%,30:100%,40:100%,50:100%,60:100%,70:100%,80:100%,90:100%"
fi
if [ -x "$Z13CTL" ] && as_deck "$Z13CTL" fancurve --set "$CURVE" >/dev/null 2>&1; then
    logger -t z13-fan-resume "profile restore unavailable; re-applied curve after resume ($CURVE)"
else
    [ -x "$Z13CTL" ] && as_deck "$Z13CTL" fancurve --reset >/dev/null 2>&1
    for h in /sys/class/hwmon/hwmon*; do
        [ "$(cat "$h/name" 2>/dev/null)" = "asus_custom_fan_curve" ] || continue
        for e in "$h"/pwm*_enable; do echo 2 > "$e" 2>/dev/null || true; done
    done
    logger -t z13-fan-resume "WARN curve re-apply FAILED; forced firmware-auto fallback after resume"
fi
