#!/bin/sh
# Re-pin MT7925 BT (USB 13d3:3608) autosuspend OFF after resume. Re-created post-3.8.11 update 2026-06-24.
for d in /sys/bus/usb/devices/*; do
    [ -f "$d/idVendor" ] || continue
    if [ "$(cat "$d/idVendor")" = "13d3" ] && [ "$(cat "$d/idProduct")" = "3608" ]; then
        echo on > "$d/power/control" 2>/dev/null
        logger -t mt7925-bt-resume "re-pinned $(basename "$d") power/control=on after resume"
    fi
done
