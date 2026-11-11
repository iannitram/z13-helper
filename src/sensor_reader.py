import os
import logging

logger = logging.getLogger(__name__)

HWMON_ROOT = "/sys/class/hwmon"
PLATFORM_PROFILE = "/sys/firmware/acpi/platform_profile"


class SensorReader:
    """Reads hardware sensors directly from sysfs.

    Discovers hwmon devices by their kernel name rather than relying on fixed
    indices, which can shift between boots or kernel updates.

    Relevant nodes on this hardware:
      asus           (hwmon5) — fan1_input, fan2_input (main APU fans)
      amdgpu         (hwmon10) — temp1_input "edge" (GPU die temp)
      acpitz         (hwmon2)  — temp1_input (ACPI thermal zone, the fallback CPU reading)
      k10temp                  — temp*_input labeled Tdie / Tctl (preferred CPU reading)
    """

    def __init__(self):
        self._hwmon_cache: dict[str, str] = {}

    def _find_hwmon(self, device_name: str) -> str | None:
        """Returns the sysfs path for a hwmon device identified by its name file."""
        if device_name in self._hwmon_cache:
            cached = self._hwmon_cache[device_name]
            if os.path.exists(cached):
                return cached

        try:
            for entry in sorted(os.listdir(HWMON_ROOT)):
                name_file = os.path.join(HWMON_ROOT, entry, "name")
                try:
                    with open(name_file) as f:
                        if f.read().strip() == device_name:
                            path = os.path.join(HWMON_ROOT, entry)
                            self._hwmon_cache[device_name] = path
                            return path
                except OSError:
                    continue
        except OSError as e:
            logger.error(f"Cannot list {HWMON_ROOT}: {e}")

        logger.warning(f"hwmon device '{device_name}' not found")
        return None

    def _read_int(self, path: str) -> int | None:
        try:
            with open(path) as f:
                return int(f.read().strip())
        except (OSError, ValueError) as e:
            logger.debug(f"Cannot read {path}: {e}")
            return None

    # --- High-level sensor accessors ---

    def get_fan_speeds(self) -> tuple[int | None, int | None]:
        """Returns (fan1_rpm, fan2_rpm) from the asus hwmon node."""
        hwmon = self._find_hwmon("asus")
        if hwmon is None:
            return None, None
        fan1 = self._read_int(os.path.join(hwmon, "fan1_input"))
        fan2 = self._read_int(os.path.join(hwmon, "fan2_input"))
        return fan1, fan2

    def custom_fan_curve_enabled(self) -> bool | None:
        """True if both fans follow the custom curve (pwmN_enable == 1).

        False means the firmware's auto curve took over — asus-wmi silently
        drops custom curves whenever the platform profile is written. None if
        the asus_custom_fan_curve node is missing or unreadable.
        """
        hwmon = self._find_hwmon("asus_custom_fan_curve")
        if hwmon is None:
            return None
        states = [self._read_int(os.path.join(hwmon, f"pwm{n}_enable")) for n in (1, 2)]
        if None in states:
            return None
        return all(s == 1 for s in states)

    def fan_curve_modes(self) -> tuple[int | None, int | None] | None:
        """(pwm1_enable, pwm2_enable): 1 = manual custom curve, else firmware
        auto. Per fan, unlike custom_fan_curve_enabled(), which folds both into
        one value. None if the node is missing."""
        hwmon = self._find_hwmon("asus_custom_fan_curve")
        if hwmon is None:
            return None
        return tuple(
            self._read_int(os.path.join(hwmon, f"pwm{n}_enable")) for n in (1, 2)
        )

    def custom_fan_curve_points(self) -> list[list[int]] | None:
        """The 8 (temp_c, pwm) points of fan 1's custom curve, from sysfs.

        This is the kernel's copy of the curve, not necessarily what the
        firmware is running (see fan_watchdog.py). None if unreadable.
        """
        hwmon = self._find_hwmon("asus_custom_fan_curve")
        if hwmon is None:
            return None
        points = []
        for i in range(1, 9):
            t = self._read_int(os.path.join(hwmon, f"pwm1_auto_point{i}_temp"))
            p = self._read_int(os.path.join(hwmon, f"pwm1_auto_point{i}_pwm"))
            if t is None or p is None:
                return None
            points.append([t, p])
        return points

    def expected_fan_pwm(self, temp_c: float) -> int | None:
        """What the active custom curve asks for at temp_c (0-255).

        Uses the highest point at or below the temperature, i.e. a step
        curve. Below the first point the firmware idles the fans (seen
        2026-10-09: 0 RPM at 35 °C on a curve starting at 40 °C, back on at
        40 °C, with a few degrees of hysteresis on the way down), so 0 is
        expected there — which is why Max Fans starts its curve at 20 °C.
        None when no custom curve is readable.
        """
        points = self.custom_fan_curve_points()
        if not points:
            return None
        pwm = 0
        for t, p in points:
            if temp_c >= t:
                pwm = p
        return pwm

    def get_acpi_fan_rpm(self) -> int | None:
        """The ACPI fan device's own RPM (hwmon `acpi_fan`), or None.

        A second, independent tach path to the EC: it keeps moving when the
        asus node's reading is suspected frozen, so the fan trace logs it
        next to fan1/fan2 to tell a stale tach from a steady fan.
        """
        hwmon = self._find_hwmon("acpi_fan")
        return self._read_int(os.path.join(hwmon, "fan1_input")) if hwmon else None

    def get_platform_profile(self) -> str | None:
        """The firmware platform profile in force (quiet/balanced/performance/...)."""
        try:
            with open(PLATFORM_PROFILE) as f:
                return f.read().strip()
        except OSError:
            return None

    def get_battery(self) -> dict:
        """capacity %, status (Charging/Discharging/Full/...), power W — from BAT0."""
        base = "/sys/class/power_supply/BAT0"
        out = {"capacity": self._read_int(f"{base}/capacity"), "status": None, "power_w": None}
        try:
            with open(f"{base}/status") as f:
                out["status"] = f.read().strip()
        except OSError:
            pass
        pw = self._read_int(f"{base}/power_now")
        if pw is not None:
            out["power_w"] = pw / 1_000_000
        return out

    def get_apu_temp(self) -> float | None:
        """The ACPI thermal-zone temperature in °C (acpitz), not the CPU package.

        The fan watchdog's reference reading; the UI uses get_cpu_temp(), which
        names its source.
        """
        hwmon = self._find_hwmon("acpitz")
        if hwmon is None:
            return None
        raw = self._read_int(os.path.join(hwmon, "temp1_input"))
        return raw / 1000.0 if raw is not None else None

    def _read_str(self, path: str) -> str | None:
        try:
            with open(path) as f:
                return f.read().strip()
        except OSError:
            return None

    def _hwmons_named(self, device_name: str) -> list[str]:
        """Every hwmon directory whose `name` is device_name, sorted."""
        found = []
        try:
            for entry in sorted(os.listdir(HWMON_ROOT)):
                path = os.path.join(HWMON_ROOT, entry)
                if self._read_str(os.path.join(path, "name")) == device_name:
                    found.append(path)
        except OSError:
            pass
        return found

    def get_cpu_temp(self) -> tuple[float, str] | None:
        """(°C, source label) for the CPU, or None when nothing is readable.

        Prefers k10temp's labeled readings: Tdie (real die temperature) over
        Tctl (control value, may carry an offset). Falls back to the ACPI
        thermal zone, labeled "ACPI" so nobody mistakes it for the package.
        """
        hwmon = self._find_hwmon("k10temp")
        if hwmon is not None:
            labeled = {}
            for n in range(1, 10):
                label = self._read_str(os.path.join(hwmon, f"temp{n}_label"))
                if label in ("Tdie", "Tctl") and label not in labeled:
                    raw = self._read_int(os.path.join(hwmon, f"temp{n}_input"))
                    if raw is not None:
                        labeled[label] = raw / 1000.0
            for label in ("Tdie", "Tctl"):
                if label in labeled:
                    return labeled[label], f"k10temp {label}"
        raw = None
        hwmon = self._find_hwmon("acpitz")
        if hwmon is not None:
            raw = self._read_int(os.path.join(hwmon, "temp1_input"))
        return (raw / 1000.0, "ACPI") if raw is not None else None

    def _gpu_hwmon(self) -> str | None:
        """The amdgpu hwmon of the integrated GPU, chosen by device not index.

        Match the Z13's Strix Halo PCI ID (1002:1586). Its display-controller
        class has no boot_vga file, and USB4 eGPUs can have lower PCI addresses.
        If the integrated device cannot be identified, report it unavailable.
        """
        for path in self._hwmons_named("amdgpu"):
            device = os.path.join(path, "device")
            if (self._read_str(os.path.join(device, "vendor")) == "0x1002"
                    and self._read_str(os.path.join(device, "device")) == "0x1586"):
                return path
        return None

    def get_gpu_temp_labeled(self) -> tuple[float, str] | None:
        """(°C, "amdgpu <label>") of the integrated GPU, or None."""
        hwmon = self._gpu_hwmon()
        if hwmon is None:
            return None
        raw = self._read_int(os.path.join(hwmon, "temp1_input"))
        if raw is None:
            return None
        label = self._read_str(os.path.join(hwmon, "temp1_label")) or "edge"
        return raw / 1000.0, f"amdgpu {label}"

    def get_gpu_temp(self) -> float | None:
        """Returns the integrated GPU's temperature in °C from amdgpu."""
        got = self.get_gpu_temp_labeled()
        return got[0] if got else None

    def get_comfort_candidates(self) -> dict[str, float | None]:
        """°C readings that might track how warm the back of the tablet feels.

        Phase 15e probe: Tctl (k10temp), acpitz, the NVMe composite, the
        battery and the GPU edge. Read-only hwmon/power_supply files; no
        Armoury power-limit file is touched (principle 5). A missing sensor reads None.
        """
        def hwmon_temp(name):
            hwmon = self._find_hwmon(name)
            raw = self._read_int(os.path.join(hwmon, "temp1_input")) if hwmon else None
            return raw / 1000.0 if raw is not None else None

        # power_supply/BAT0/temp (tenths of °C) where the driver has it; else a
        # temp1_input on the BAT0 hwmon node. Some firmware offers neither, so None.
        battery = self._read_int("/sys/class/power_supply/BAT0/temp")
        if battery is not None:
            battery = battery / 10.0
        else:
            node = self._find_hwmon("BAT0")
            raw = self._read_int(os.path.join(node, "temp1_input")) if node else None
            battery = raw / 1000.0 if raw is not None else None
        return {
            "tctl": hwmon_temp("k10temp"),
            "acpitz": self.get_apu_temp(),
            "nvme": hwmon_temp("nvme"),
            "battery": battery,
            "gpu_edge": self.get_gpu_temp(),
        }


if __name__ == "__main__":
    reader = SensorReader()
    fan1, fan2 = reader.get_fan_speeds()
    cpu = reader.get_cpu_temp()
    gpu = reader.get_gpu_temp_labeled()
    print(f"Fan 1:    {fan1} RPM")
    print(f"Fan 2:    {fan2} RPM")
    print("CPU temp: " + (f"{cpu[0]} °C ({cpu[1]})" if cpu else "unavailable"))
    print("GPU temp: " + (f"{gpu[0]} °C ({gpu[1]})" if gpu else "unavailable"))
