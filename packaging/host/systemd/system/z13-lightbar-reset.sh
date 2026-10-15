#!/bin/sh
# Reset the Z13's rear lightbar USB device when its HID endpoint has wedged
# (ROADMAP §3). Symptom: every z13ctl write to it blocks ~5 s and fails with
# "write /dev/hidraw5: connection timed out". Fix: de-authorize and re-authorize
# that one USB device, which makes the kernel re-enumerate it.
#
# The lightbar is USB 0b05:18c6 ("N-KEY Device"). It is found by its IDs, never
# by port (it was 3-5 when this was written; ports move). The keyboard is a
# different USB device and is never touched here.
#
# Started on demand by z13-lightbar-reset.service (root, Type=oneshot, not
# enabled). deck may start it without a password through
# /etc/polkit-1/rules.d/50-z13-lightbar-reset.rules; the app, z13-profile and
# the Decky backend start it at most once per 2 minutes, after a lightbar write
# timed out.
#
# Exit: 0 reset done · 1 device not found · 2 more than one match (refused) ·
# 3 the authorized write failed.
#
# Z13_LIGHTBAR_SYSFS overrides the sysfs root for the tests only (the unit runs
# with a clean environment, so it is always /sys there).

VID=0b05
PID=18c6
SYSFS="${Z13_LIGHTBAR_SYSFS:-/sys}"
TAG=z13-lightbar-reset

log() {
    logger -t "$TAG" "$*" 2>/dev/null || true
    echo "$TAG: $*" >&2
}

found=""
n=0
for d in "$SYSFS"/bus/usb/devices/*; do
    [ -r "$d/idVendor" ] && [ -r "$d/idProduct" ] || continue
    [ "$(cat "$d/idVendor")" = "$VID" ] || continue
    [ "$(cat "$d/idProduct")" = "$PID" ] || continue
    found="$d"
    n=$((n + 1))
done

if [ "$n" -eq 0 ]; then
    log "lightbar USB device $VID:$PID not found under $SYSFS/bus/usb/devices"
    exit 1
fi
if [ "$n" -gt 1 ]; then
    log "found $n devices with ID $VID:$PID; refusing to guess which is the lightbar"
    exit 2
fi

port=$(basename "$found")
if ! printf 0 > "$found/authorized"; then
    log "could not de-authorize $port ($VID:$PID)"
    exit 3
fi
sleep 1
if ! printf 1 > "$found/authorized"; then
    log "de-authorized $port but could NOT re-authorize it; run: echo 1 > $found/authorized"
    exit 3
fi
log "reset lightbar $port ($VID:$PID) via authorized 0/1"
exit 0
