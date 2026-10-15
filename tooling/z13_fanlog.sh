#!/bin/bash
# Z13 fan/thermal logger — samples every 2s, survives Desktop<->Game Mode switch.
# Usage: z13_fanlog.sh <out.csv> <max_samples>
OUT="${1:-/home/deck/.z13-fanlog/fanlog.csv}"
MAX="${2:-900}"   # 900 * 2s = 30 min
mkdir -p "$(dirname "$OUT")" 2>/dev/null

find_hwmon() {
  for h in /sys/class/hwmon/hwmon*; do
    [ "$(cat "$h/name" 2>/dev/null)" = "$1" ] && { echo "$h"; return; }
  done
}
H7=$(find_hwmon asus_custom_fan_curve)
ASUS=$(find_hwmon asus)
GPU=$(find_hwmon amdgpu)
ACPITZ=$(find_hwmon acpitz)
TTP=/sys/devices/platform/asus-nb-wmi/throttle_thermal_policy
PP=/sys/firmware/acpi/platform_profile
BUSY=/sys/class/drm/card0/device/gpu_busy_percent

r() { cat "$1" 2>/dev/null; }
rk() { local v; v=$(cat "$1" 2>/dev/null); echo $(( ${v:-0} / 1000 )); }
rm2() { local v; v=$(cat "$1" 2>/dev/null); echo $(( ${v:-0} / 1000000 )); }

echo "epoch,iso,mode,ttp,pprofile,pwm1en,pwm2en,cpu_fan,gpu_fan,gpu_temp,acpitz1,acpitz2,ppt_w,gpu_busy" > "$OUT"
i=0
while [ "$i" -lt "$MAX" ]; do
  if pgrep -x gamescope >/dev/null 2>&1; then mode=game; else mode=desktop; fi
  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$(date +%s)" "$(date -Is)" "$mode" \
    "$(r "$TTP")" "$(r "$PP")" "$(r "$H7/pwm1_enable")" "$(r "$H7/pwm2_enable")" \
    "$(r "$ASUS/fan1_input")" "$(r "$ASUS/fan2_input")" \
    "$(rk "$GPU/temp1_input")" "$(rk "$ACPITZ/temp1_input")" "$(rk "$ACPITZ/temp2_input")" \
    "$(rm2 "$GPU/power1_input")" "$(r "$BUSY")" >> "$OUT"
  i=$((i+1))
  sleep 2
done
