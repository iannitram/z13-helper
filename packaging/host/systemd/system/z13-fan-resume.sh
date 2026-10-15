#!/bin/sh
# Re-apply the active Z13-Helper profile after resume (Z13 GZ302EA).
# History: 2026-06-24 wrote a hard-coded protective curve; 2026-10-01 pinned Max Fans
# when ON; 2026-10-09 now restores the *active profile* (platform profile, TDP, curve,
# Max Fans pinned if ON) through the app's engine, falling back to the old curve.
# Lives in /etc/systemd/system (atomic-update keep list: /etc/atomic-update.conf.d/z13-bucket1.conf).
Z13CTL=/home/deck/.local/bin/z13ctl
PROFILE=/home/deck/.local/bin/z13-profile
CURVE="40:127,50:191,60:229,70:255,75:255,80:255,85:255,90:255"
MAXF=/home/deck/.config/z13-helper/max-fans.json

# Give the EC a moment after wake; a write into a stalled EC can hard-lock (z13ctl 1.3.3 notes).
sleep 3

if [ -x "$PROFILE" ] && [ -f /home/deck/.config/z13-helper/profiles.json ] \
   && runuser -u deck -- env HOME=/home/deck XDG_RUNTIME_DIR=/run/user/1000 "$PROFILE" restore >/dev/null 2>&1; then
    logger -t z13-fan-resume "re-applied active profile after resume (z13-profile restore)"
    exit 0
fi

# Fallback: the old behaviour.
if grep -q '"enabled": *true' "$MAXF" 2>/dev/null; then
    CURVE="20:100%,30:100%,40:100%,50:100%,60:100%,70:100%,80:100%,90:100%"
fi
if [ -x "$Z13CTL" ] && "$Z13CTL" fancurve --set "$CURVE" >/dev/null 2>&1; then
    logger -t z13-fan-resume "profile restore unavailable; re-applied curve after resume ($CURVE)"
else
    "$Z13CTL" fancurve --reset >/dev/null 2>&1 || true
    for h in /sys/class/hwmon/hwmon*; do
        [ "$(cat "$h/name" 2>/dev/null)" = "asus_custom_fan_curve" ] || continue
        for e in "$h"/pwm*_enable; do echo 2 > "$e" 2>/dev/null || true; done
    done
    logger -t z13-fan-resume "WARN curve re-apply FAILED; forced firmware-auto fallback after resume"
fi
