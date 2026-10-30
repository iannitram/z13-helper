> **Historical manual, not current setup instructions.** This Part 2 records an early manual z13ctl setup and predates the integrated Z13-Helper app, the current profile engine, and z13ctl 1.4.2. Its “current” claims and step-by-step commands describe the environment at the time it was written. For current install and repair steps, use the repository [`README.md`](../README.md), [`docs/ROADMAP.md`](ROADMAP.md), and the app's live hardware-range detection. Do not treat old range assumptions or install commands below as current.

Below is **Manual Part 2**. This continues from Part 1 and focuses on **Z13 hardware enablement, ASUS/Linux control tooling, and safe profile switching**. This section follows your pasted requirement that hardware/software support claims must be verified before manual instructions are written.

* * *

# SteamOS Z13 Strix Halo Manual

## Part 2 — Z13 Hardware Enablement and Safe Operating Profile Switching

## Part 2 scope

This part will:

1.  Verify whether the Z13 hardware control interfaces are visible.
    
2.  Install `z13ctl` conservatively as a user-local binary.
    
3.  Preview `z13ctl`’s privileged setup before applying it.
    
4.  Enable safe access to profile, battery limit, RGB, TDP, and fan status controls.
    
5.  Create simple profile-switching scripts.
    
6.  Stay away from undervolting, EC flashing, firmware hacks, and forced high-TDP custom profiles.
    

This part will **not** yet:

1.  Install ROCm.
    
2.  Install LLM runtimes.
    
3.  Create custom fan curves.
    
4.  Apply custom TDP values.
    
5.  Apply undervolting.
    
6.  Modify BIOS settings.
    
7.  Disable SteamOS read-only mode.
    
8.  Install the larger GUI/toolkit layers.
    

That restraint is deliberate. First we make the hardware-control layer work. Then we stress-test. Then we tune.

* * *

# 1. Research / verification pass for Part 2

## 1.1 Device-specific tooling

At the time this manual was drafted, the latest release checked was `z13ctl` v1.1.6. That version reference and its setup guidance are historical; the current app install uses z13ctl 1.4.2. Current slider bounds are detected from each kernel rail's metadata on the host (with a legacy range fallback), not assumed from the manual's examples. `z13ctl` remains a community tool, not ASUS official software.

## 1.2 Generic ASUS Linux tooling

`asusctl` is still relevant background knowledge. ASUS Linux’s manual documents support for LED control, battery charge limits, BIOS-type controls, and performance profile switching on many ASUS laptops. However, the 2025 Flow Z13 is unusual enough that I would not start with generic `asusctl` when `z13ctl` exists for this exact model. (Asus Linux)

## 1.3 Z13 hardware context

ASUS’s own product page confirms this Z13 uses the Ryzen AI Max+ 395 with Radeon 8060S graphics, 16 Zen 5 CPU cores, 40 GPU compute units, unified quad-channel memory, and a 70Wh battery. Those details matter because the machine is a unified-memory, high-power APU tablet, not a normal laptop with a discrete GPU. (@ROG)

## 1.4 Current recommendation

For Part 2:

*   **Primary tool:** `z13ctl`
    
*   **Install method:** user-local binary in `$HOME/.local/bin`
    
*   **Privilege approach:** dry-run first, then minimal `sudo z13ctl setup`
    
*   **Profile approach:** stock firmware profiles first
    
*   **Custom TDP/fan curves:** defer until after verification
    
*   **Undervolting:** defer entirely for now
    

* * *

# 2. Important safety rule for this part

Do **not** run random AUR helpers or install Arch packages directly into SteamOS yet. Specifically, do **not** do this right now:

```bash
yay -S z13ctl-bin
```

```bash
sudo pacman -Syu
```

```bash
sudo pacman -U z13ctl-*.pkg.tar.zst
```

```bash
sudo holo-readonly disable   # steamos-readonly on SteamOS before 3.9
```

Those may become relevant later, but they are not the safest first move on SteamOS.

* * *

# 3. Confirm Part 1 audit exists

## What this does

Checks that your Part 1 audit folder exists before continuing.

## Why it is needed

If anything goes wrong, we want a baseline snapshot.

## Risk level

Low.

## Command

```bash
ls -lah "$HOME/z13-audit"
```

## Expected output

You should see files like:

```text
01-system.txt
02-hardware.txt
03-amdgpu.txt
04-vulkan-vaapi.txt
05-rocm-existing.txt
06-app-containers.txt
07-power-thermal-asus.txt
```

## Verification command

```bash
test -d "$HOME/z13-audit" && echo "Part 1 audit folder exists."
```

## Rollback command

No rollback needed. This is read-only.

## If it fails

Stop and run Part 1 first.

* * *

# 4. Read-only hardware-control discovery

Before installing anything, inspect the interfaces Linux already exposes.

## 4.1 Check ASUS HID devices

## What this does

Looks for ASUS USB/HID devices that `z13ctl` may control.

## Why it is needed

`z13ctl` targets the Z13’s ASUS HID devices. If they are missing, profile/RGB control may not work.

## Risk level

Low.

## Command

```bash
{
  echo "=== USB DEVICES MATCHING ASUS ==="
  lsusb | grep -i asus || true

  echo
  echo "=== HIDRAW DEVICES ==="
  ls -lah /dev/hidraw* 2>/dev/null || true

  echo
  echo "=== HIDRAW UDEV DETAILS, ASUS ONLY IF PRESENT ==="
  for dev in /dev/hidraw*; do
    echo "--- $dev ---"
    udevadm info --query=all --name="$dev" 2>/dev/null | grep -i -E 'asus|0b05|18c6|1a30|hidraw|product|vendor' || true
    echo
  done
} | tee "$HOME/z13-audit/08-asus-hid-discovery.txt"
```

## Expected output

Good signs:

*   `lsusb` shows ASUS devices.
    
*   `/dev/hidraw*` exists.
    
*   One or more devices mention ASUS or vendor ID `0b05`.
    

## Verification command

```bash
cat "$HOME/z13-audit/08-asus-hid-discovery.txt"
```

## Rollback command

```bash
rm -f "$HOME/z13-audit/08-asus-hid-discovery.txt"
```

## Common failure modes

If there are no `/dev/hidraw*` devices, do not install `z13ctl` yet. Reboot once and rerun the command.

* * *

## 4.2 Check platform profile support

## What this does

Checks whether Linux exposes firmware power profiles.

## Why it is needed

`z13ctl profile` uses the ASUS/platform profile sysfs interface. The expected choices are usually things like `quiet`, `balanced`, and `performance`.

## Risk level

Low.

## Command

```bash
{
  echo "=== PLATFORM PROFILE CURRENT ==="
  cat /sys/firmware/acpi/platform_profile 2>/dev/null || true

  echo
  echo "=== PLATFORM PROFILE CHOICES ==="
  cat /sys/firmware/acpi/platform_profile_choices 2>/dev/null || true

  echo
  echo "=== POWER PROFILES DAEMON ==="
  command -v powerprofilesctl || true
  powerprofilesctl list 2>&1 || true
} | tee "$HOME/z13-audit/09-platform-profile.txt"
```

## Expected output

Good signs:

```text
balanced
```

And choices like:

```text
quiet balanced performance
```

The exact formatting may differ.

## Verification command

```bash
cat "$HOME/z13-audit/09-platform-profile.txt"
```

## Rollback command

```bash
rm -f "$HOME/z13-audit/09-platform-profile.txt"
```

## If it fails

If `/sys/firmware/acpi/platform_profile` does not exist, profile switching may require a newer kernel, a different ASUS kernel module state, or a Z13-specific tool. Do not write directly to sysfs yet.

* * *

## 4.3 Check battery charge-limit interface

## What this does

Looks for Linux battery charge threshold controls.

## Why it is needed

Charge limits are one of the safest and most useful hardware-longevity features.

## Risk level

Low.

## Command

```bash
{
  echo "=== POWER SUPPLY DIRECTORIES ==="
  ls -lah /sys/class/power_supply

  echo
  echo "=== BATTERY CHARGE CONTROL FILES ==="
  find /sys/class/power_supply -maxdepth 3 -type f \
    \( -name 'charge_control_end_threshold' -o -name 'charge_control_start_threshold' \) \
    -print -exec sh -c 'echo "--- $1 ---"; cat "$1"' sh {} \; 2>/dev/null || true
} | tee "$HOME/z13-audit/10-battery-thresholds.txt"
```

## Expected output

Best case:

```text
/sys/class/power_supply/BAT0/charge_control_end_threshold
80
```

The battery may be named `BAT0`, `BAT1`, or something else.

## Verification command

```bash
cat "$HOME/z13-audit/10-battery-thresholds.txt"
```

## Rollback command

```bash
rm -f "$HOME/z13-audit/10-battery-thresholds.txt"
```

* * *

# 5. Install `z13ctl` conservatively

This is the first real install step. We are **not** using `curl | bash`. We are **not** using an AUR helper. We are **not** installing to `/usr/local/bin`. We are downloading the latest release metadata, inspecting it, downloading the Linux AMD64 archive, inspecting the archive, then installing only the binary into:

```text
$HOME/.local/bin/z13ctl
```

That keeps the binary in your home directory.

* * *

## 5.1 Check required basic tools

## What this does

Checks for `curl`, `tar`, `sha256sum`, and `python3`.

## Why it is needed

The install process uses those tools to download, inspect, checksum, and parse GitHub release metadata.

## Risk level

Low.

## Command

```bash
{
  echo "=== REQUIRED TOOL CHECK ==="

  for tool in curl tar sha256sum python3 file install; do
    printf "%-12s" "$tool"
    command -v "$tool" || echo "MISSING"
  done
} | tee "$HOME/z13-audit/11-z13ctl-tool-check.txt"
```

## Expected output

Each tool should show a path, such as:

```text
curl        /usr/bin/curl
tar         /usr/bin/tar
sha256sum   /usr/bin/sha256sum
python3     /usr/bin/python3
```

## Verification command

```bash
cat "$HOME/z13-audit/11-z13ctl-tool-check.txt"
```

## Rollback command

```bash
rm -f "$HOME/z13-audit/11-z13ctl-tool-check.txt"
```

## If it fails

If `python3` or `curl` is missing, stop here. Do not install system packages yet.

* * *

## 5.2 Download and inspect latest release metadata

## What this does

Downloads GitHub’s latest-release metadata for `z13ctl` into your Downloads folder.

## Why it is needed

This lets the system find the current Linux AMD64 release archive without guessing the filename.

## Risk level

Low.

## Command

```bash
mkdir -p "$HOME/Downloads/z13ctl-install"
cd "$HOME/Downloads/z13ctl-install"

curl -fL \
  "https://api.github.com/repos/dahui/z13ctl/releases/latest" \
  -o latest-release.json

echo "Downloaded release metadata:"
ls -lh latest-release.json

echo
echo "First 80 lines for inspection:"
sed -n '1,80p' latest-release.json
```

## Expected output

You should see JSON text containing fields like:

```text
"tag_name"
"name"
"assets"
"browser_download_url"
```

## Verification command

```bash
python3 -m json.tool "$HOME/Downloads/z13ctl-install/latest-release.json" >/dev/null && echo "Release JSON is valid."
```

## Rollback command

```bash
rm -rf "$HOME/Downloads/z13ctl-install"
```

## Common failure modes

If GitHub rate-limits or blocks the request, wait and retry later from a browser. Do not substitute a random mirror.

* * *

## 5.3 Select the Linux AMD64 release asset

## What this does

Parses the release JSON and prints the Linux AMD64 tarball URL.

## Why it is needed

We want the official release asset from the project’s GitHub release.

## Risk level

Low.

## Command

```bash
cd "$HOME/Downloads/z13ctl-install"

python3 - <<'PY'
import json
from pathlib import Path

data = json.loads(Path("latest-release.json").read_text())

assets = data.get("assets", [])
matches = []

for asset in assets:
    name = asset.get("name", "")
    url = asset.get("browser_download_url", "")
    lowered = name.lower()
    if "linux" in lowered and "amd64" in lowered and lowered.endswith((".tar.gz", ".tgz")):
        matches.append((name, url))

print("Release tag:", data.get("tag_name"))
print("Release name:", data.get("name"))
print()

if not matches:
    print("ERROR: No linux amd64 tar.gz asset was found.")
    print("Available assets:")
    for asset in assets:
        print("-", asset.get("name"))
    raise SystemExit(1)

print("Selected asset:")
print(matches[0][0])
print(matches[0][1])
PY
```

## Expected output

You should see one selected Linux AMD64 archive.

## Verification command

```bash
cd "$HOME/Downloads/z13ctl-install"

python3 - <<'PY'
import json
from pathlib import Path

data = json.loads(Path("latest-release.json").read_text())
assets = data.get("assets", [])
matches = [
    asset for asset in assets
    if "linux" in asset.get("name", "").lower()
    and "amd64" in asset.get("name", "").lower()
    and asset.get("name", "").lower().endswith((".tar.gz", ".tgz"))
]

print(len(matches), "matching linux amd64 archive(s) found.")
PY
```

## Rollback command

```bash
rm -rf "$HOME/Downloads/z13ctl-install"
```

* * *

## 5.4 Download the selected archive

## What this does

Downloads the selected `z13ctl` Linux AMD64 archive.

## Why it is needed

This gives us the binary without installing a package into SteamOS root.

## Risk level

Low to Medium. Low because it only downloads to your home folder. Medium because you are downloading executable software.

## Command

```bash
cd "$HOME/Downloads/z13ctl-install"

Z13CTL_URL="$(
python3 - <<'PY'
import json
from pathlib import Path

data = json.loads(Path("latest-release.json").read_text())
assets = data.get("assets", [])

for asset in assets:
    name = asset.get("name", "")
    lowered = name.lower()
    if "linux" in lowered and "amd64" in lowered and lowered.endswith((".tar.gz", ".tgz")):
        print(asset.get("browser_download_url"))
        raise SystemExit(0)

raise SystemExit("No linux amd64 tar.gz asset found.")
PY
)"

echo "Downloading:"
echo "$Z13CTL_URL"

curl -fL "$Z13CTL_URL" -o z13ctl-latest-linux-amd64.tar.gz

echo
echo "Downloaded archive:"
ls -lh z13ctl-latest-linux-amd64.tar.gz

echo
echo "Local SHA256 hash:"
sha256sum z13ctl-latest-linux-amd64.tar.gz | tee z13ctl-latest-linux-amd64.tar.gz.sha256.local
```

## Expected output

You should see:

```text
z13ctl-latest-linux-amd64.tar.gz
z13ctl-latest-linux-amd64.tar.gz.sha256.local
```

## Verification command

```bash
cd "$HOME/Downloads/z13ctl-install"
sha256sum -c z13ctl-latest-linux-amd64.tar.gz.sha256.local
```

Expected:

```text
z13ctl-latest-linux-amd64.tar.gz: OK
```

## Important limitation

This local SHA256 check confirms the file did not change after you downloaded it. It does **not** prove upstream authenticity unless the project also publishes independently verifiable checksums or signatures. I did not verify a signed release workflow for this tool.

## Rollback command

```bash
rm -rf "$HOME/Downloads/z13ctl-install"
```

* * *

## 5.5 Inspect the archive before extracting

## What this does

Lists the archive contents without extracting them.

## Why it is needed

This avoids blindly unpacking unknown paths.

## Risk level

Low.

## Command

```bash
cd "$HOME/Downloads/z13ctl-install"

echo "Archive contents:"
tar -tzf z13ctl-latest-linux-amd64.tar.gz
```

## Expected output

You should see files including something named:

```text
z13ctl
```

You may also see documentation and systemd unit files.

## Verification command

```bash
cd "$HOME/Downloads/z13ctl-install"

tar -tzf z13ctl-latest-linux-amd64.tar.gz | grep -E '(^|/)z13ctl$' && echo "z13ctl binary appears to be present."
```

## Rollback command

```bash
rm -rf "$HOME/Downloads/z13ctl-install"
```

* * *

## 5.6 Extract and inspect the binary

## What this does

Extracts the release archive into a temporary folder and inspects the binary.

## Why it is needed

We verify that the extracted file is an executable Linux binary before installing it.

## Risk level

Low.

## Command

```bash
cd "$HOME/Downloads/z13ctl-install"

rm -rf extracted
mkdir -p extracted

tar -xzf z13ctl-latest-linux-amd64.tar.gz -C extracted

echo "Extracted files:"
find extracted -maxdepth 3 -type f -print

echo
echo "Candidate z13ctl binary:"
Z13CTL_EXTRACTED_BIN="$(find "$HOME/Downloads/z13ctl-install/extracted" -type f -name z13ctl -perm -111 | head -n 1)"
echo "$Z13CTL_EXTRACTED_BIN"

echo
echo "File type:"
file "$Z13CTL_EXTRACTED_BIN"
```

## Expected output

The file type should indicate a Linux executable, likely ELF 64-bit.

## Verification command

```bash
test -x "$(find "$HOME/Downloads/z13ctl-install/extracted" -type f -name z13ctl -perm -111 | head -n 1)" && echo "Extracted z13ctl binary is executable."
```

## Rollback command

```bash
rm -rf "$HOME/Downloads/z13ctl-install"
```

* * *

## 5.7 Install `z13ctl` into your user-local bin folder

## What this does

Copies the `z13ctl` binary to:

```text
$HOME/.local/bin/z13ctl
```

## Why it is needed

This makes the tool available without modifying `/usr/local/bin`.

## Risk level

Low.

## Command

```bash
mkdir -p "$HOME/.local/bin"

Z13CTL_EXTRACTED_BIN="$(find "$HOME/Downloads/z13ctl-install/extracted" -type f -name z13ctl -perm -111 | head -n 1)"

install -Dm755 "$Z13CTL_EXTRACTED_BIN" "$HOME/.local/bin/z13ctl"

echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"

export PATH="$HOME/.local/bin:$PATH"

"$HOME/.local/bin/z13ctl" --help
```

## Expected output

You should see `z13ctl` help text.

## Verification command

```bash
"$HOME/.local/bin/z13ctl" --help >/dev/null && echo "z13ctl runs from user-local bin."
```

## Rollback command

```bash
rm -f "$HOME/.local/bin/z13ctl"
```

## Common failure modes

If you see:

```text
Permission denied
```

then the binary may not be executable. Re-run:

```bash
chmod +x "$HOME/.local/bin/z13ctl"
"$HOME/.local/bin/z13ctl" --help
```

* * *

# 6. First `z13ctl` read-only checks

## 6.1 List supported devices

## What this does

Asks `z13ctl` to list matching Z13 devices.

## Why it is needed

This confirms whether `z13ctl` sees the ASUS HID devices.

## Risk level

Low.

## Command

```bash
export PATH="$HOME/.local/bin:$PATH"

z13ctl list | tee "$HOME/z13-audit/12-z13ctl-list-before-setup.txt"
```

## Expected output

You want to see matching devices and Aura support. `z13ctl` documentation says `z13ctl list` should print discovered `hidraw` devices and confirm Aura support when the device is detected. (Jeff Hagadorn)

## Verification command

```bash
cat "$HOME/z13-audit/12-z13ctl-list-before-setup.txt"
```

## Rollback command

```bash
rm -f "$HOME/z13-audit/12-z13ctl-list-before-setup.txt"
```

## Common failure modes

If it prints nothing:

```bash
lsmod | grep hid
ls /dev/hidraw*
```

If `/dev/hidraw*` exists but `z13ctl list` sees nothing, the device may not expose the expected USB IDs, or permissions may not be set yet.

* * *

## 6.2 Read status

## What this does

Runs `z13ctl status`, which is documented as a read-only summary of APU temperature, fan speed/mode, profile, TDP limits, undervolt availability, and battery state. (Jeff Hagadorn)

## Why it is needed

This tells us what the tool can read before privileged setup.

## Risk level

Low.

## Command

```bash
export PATH="$HOME/.local/bin:$PATH"

z13ctl status | tee "$HOME/z13-audit/13-z13ctl-status-before-setup.txt"
```

## Expected output

Best case, you see:

```text
APU:
Fans:
Profile:
TDP:
Battery:
```

Some values may be missing before setup. That is acceptable.

## Verification command

```bash
cat "$HOME/z13-audit/13-z13ctl-status-before-setup.txt"
```

## Rollback command

```bash
rm -f "$HOME/z13-audit/13-z13ctl-status-before-setup.txt"
```

* * *

# 7. Preview privileged setup

This is the most important safety step in Part 2. `z13ctl setup` writes udev rules and a small systemd service to allow non-root access to hardware-control sysfs paths. The project documentation says setup writes `/etc/udev/rules.d/99-z13ctl.rules`, reloads udev, applies permissions, and writes/enables `/etc/systemd/system/z13ctl-perms.service` for battery and optional `ryzen_smu` sysfs permissions. (Jeff Hagadorn) We will preview first.

* * *

## 7.1 Choose a group for hardware permissions

## What this does

Uses your current primary Linux group instead of assuming a `users` group exists.

## Why it is needed

The `z13ctl` docs default to `users`, but SteamOS account/group layouts can vary. Using your current primary group avoids changing your group membership unless needed.

## Risk level

Low.

## Command

```bash
Z13_GROUP="$(id -gn)"

echo "Current user: $USER"
echo "Primary group selected for z13ctl permissions: $Z13_GROUP"

getent group "$Z13_GROUP"
```

## Expected output

You should see your current user and group.

## Verification command

```bash
id
```

## Rollback command

No rollback needed. This only sets a shell variable.

* * *

## 7.2 Dry-run setup

## What this does

Runs setup in preview mode.

## Why it is needed

`--dry-run` shows what would be written without making changes. The `z13ctl` command documentation explicitly supports `--dry-run` for setup and other commands. (Jeff Hagadorn)

## Risk level

Low.

## Command

```bash
export PATH="$HOME/.local/bin:$PATH"

Z13_GROUP="$(id -gn)"

z13ctl --dry-run setup --group "$Z13_GROUP" | tee "$HOME/z13-audit/14-z13ctl-setup-dry-run.txt"
```

## Expected output

You should see a preview of:

*   udev rules
    
*   permission changes
    
*   systemd permission service behavior
    
*   target group
    

## Verification command

```bash
cat "$HOME/z13-audit/14-z13ctl-setup-dry-run.txt"
```

## Rollback command

```bash
rm -f "$HOME/z13-audit/14-z13ctl-setup-dry-run.txt"
```

## Stop here if

Stop if the dry-run mentions anything that looks unrelated to:

*   ASUS HID devices
    
*   platform profile
    
*   battery charge threshold
    
*   fan curve sysfs
    
*   TDP/PPT sysfs
    
*   optional `ryzen_smu`
    

Do **not** proceed if you see unexpected destructive behavior. This tool should not be partitioning, formatting, touching bootloaders, or changing firmware blobs.

* * *

# 8. Apply `z13ctl setup`

This is the first privileged hardware-control change.

## What this does

Applies `z13ctl`’s udev and systemd permission setup.

## Why it is needed

Without this, many profile, battery, RGB, TDP, and fan-control commands may require root or fail with permission errors.

## Risk level

Medium. Reasons:

*   Writes under `/etc/udev/rules.d`.
    
*   Writes a systemd service under `/etc/systemd/system`.
    
*   Reloads udev.
    
*   Changes permissions on hardware-control sysfs paths.
    

This is still much safer than patching firmware, flashing ECs, or disabling SteamOS read-only to install random packages.

## Command

```bash
export PATH="$HOME/.local/bin:$PATH"

Z13_GROUP="$(id -gn)"

sudo "$HOME/.local/bin/z13ctl" setup --group "$Z13_GROUP"

echo
echo "Setup command completed."

echo
echo "Reloading shell group context is usually not necessary when using your primary group, but we will verify access now."
```

## Expected output

You should see setup complete without errors.

## Verification command

```bash
export PATH="$HOME/.local/bin:$PATH"

echo "=== z13ctl list ==="
z13ctl list

echo
echo "=== z13ctl status ==="
z13ctl status

echo
echo "=== Installed udev rule ==="
ls -lah /etc/udev/rules.d/99-z13ctl.rules 2>/dev/null || true

echo
echo "=== Installed permission service ==="
systemctl status z13ctl-perms.service --no-pager 2>/dev/null || true
```

## Rollback command

Use this only if setup breaks something or you decide to remove `z13ctl`’s privileged integration:

```bash
sudo systemctl disable --now z13ctl-perms.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/z13ctl-perms.service
sudo rm -f /etc/udev/rules.d/99-z13ctl.rules
sudo systemctl daemon-reload
sudo udevadm control --reload-rules

rm -f "$HOME/.local/bin/z13ctl"

echo "z13ctl privileged setup and user-local binary removed."
```

## Common failure modes

### Failure: read-only filesystem

If you see a read-only filesystem error, do **not** immediately run:

```bash
sudo holo-readonly disable   # steamos-readonly on SteamOS before 3.9
```

Instead, stop here. This means SteamOS is preventing the `/etc` write path we expected.

### Failure: permission denied after setup

Run:

```bash
id
ls -lah /dev/hidraw* 2>/dev/null || true
z13ctl list
```

Then log out of Desktop Mode and log back in. If you used your primary group, this may not be necessary, but a session refresh can still help.

* * *

# 9. Check for InputPlumber conflict

`z13ctl` documents that gaming distributions with InputPlumber may have a conflict where the Armoury Crate button device is grabbed exclusively, preventing the `z13ctl` daemon button watcher from opening it. We are not enabling the daemon yet, but it is worth checking. (Jeff Hagadorn)

## What this does

Checks whether InputPlumber is running.

## Why it is needed

If InputPlumber is present, it may affect button handling later.

## Risk level

Low.

## Command

```bash
{
  echo "=== INPUTPLUMBER PROCESS CHECK ==="
  pgrep -a inputplumber || true

  echo
  echo "=== SYSTEM SERVICE CHECK ==="
  systemctl status inputplumber --no-pager 2>/dev/null || true

  echo
  echo "=== USER SERVICE CHECK ==="
  systemctl --user status inputplumber --no-pager 2>/dev/null || true
} | tee "$HOME/z13-audit/15-inputplumber-check.txt"
```

## Expected output

Most likely either:

```text
Unit inputplumber.service could not be found
```

or a running service.

## Verification command

```bash
cat "$HOME/z13-audit/15-inputplumber-check.txt"
```

## Rollback command

```bash
rm -f "$HOME/z13-audit/15-inputplumber-check.txt"
```

## What to do if InputPlumber is running

Do nothing for now. We are not enabling the Armoury Crate button daemon in this part.

* * *

# 10. Confirm safe control commands

Now that setup is applied, test only the low-risk controls.

* * *

## 10.1 Check current profile

## What this does

Reads the active performance profile.

## Why it is needed

This confirms profile control is available.

## Risk level

Low.

## Command

```bash
export PATH="$HOME/.local/bin:$PATH"

z13ctl profile --get | tee "$HOME/z13-audit/16-profile-current.txt"
```

## Expected output

One of:

```text
quiet
balanced
performance
custom
```

`z13ctl` documents valid profiles as `quiet`, `balanced`, `performance`, and `custom`; stock profile changes reset custom fan curves and TDP values back to firmware defaults. (Jeff Hagadorn)

## Verification command

```bash
cat "$HOME/z13-audit/16-profile-current.txt"
```

## Rollback command

```bash
rm -f "$HOME/z13-audit/16-profile-current.txt"
```

* * *

## 10.2 Check battery charge limit

## What this does

Reads the active battery charge limit.

## Why it is needed

This verifies the battery longevity control path.

## Risk level

Low.

## Command

```bash
export PATH="$HOME/.local/bin:$PATH"

z13ctl batterylimit --get | tee "$HOME/z13-audit/17-battery-limit-current.txt"
```

## Expected output

A number between `40` and `100`, if the interface works. `z13ctl` documents battery charge limits from 40–100, with 100 removing the charge cap. (Jeff Hagadorn)

## Verification command

```bash
cat "$HOME/z13-audit/17-battery-limit-current.txt"
```

## Rollback command

```bash
rm -f "$HOME/z13-audit/17-battery-limit-current.txt"
```

* * *

## 10.3 Check fan/TDP status, read-only

## What this does

Reads fan curve and TDP/PPT information without changing it.

## Why it is needed

We need to confirm visibility before changing anything.

## Risk level

Low.

## Command

```bash
export PATH="$HOME/.local/bin:$PATH"

{
  echo "=== STATUS ==="
  z13ctl status

  echo
  echo "=== FAN CURVE READBACK ==="
  z13ctl fancurve --get

  echo
  echo "=== TDP READBACK ==="
  z13ctl tdp --get
} | tee "$HOME/z13-audit/18-fan-tdp-readonly.txt"
```

## Expected output

You should see some combination of:

*   APU temperature
    
*   fan mode
    
*   fan RPM
    
*   current profile
    
*   TDP/PPT limits
    

`z13ctl` documents TDP control through ASUS `asus-nb-wmi` PPT attributes and warns that readback values may be cached and not always reflect EC ground truth immediately after boot or profile change. (Jeff Hagadorn)

## Verification command

```bash
cat "$HOME/z13-audit/18-fan-tdp-readonly.txt"
```

## Rollback command

```bash
rm -f "$HOME/z13-audit/18-fan-tdp-readonly.txt"
```

* * *

# 11. First low-risk hardware settings

We will now apply only settings that are easy to reverse:

1.  Stock performance profile.
    
2.  Battery charge limit.
    
3.  Keyboard/lightbar brightness.
    

No custom TDP yet.  
No custom fan curves yet.  
No undervolting yet.

* * *

# 12. Battery charge limit policy

Recommended charge limits:
| Profile | Charge limit | Why |
| --- | --- | --- |
| Maximum Throughput | 80% | Mostly plugged in; preserves battery while allowing heavy work. |
| Balanced Daily Driver | 80% | Best default for your mixed use. |
| Longevity / Cool & Stable | 70% | More conservative for long plugged-in sessions. |
| Travel override | 100% | Use only before you actually need full battery. |

For your vanlife use case, I would not make 60% the default. It is great for a docked laptop but annoying for a portable system. **80% is the sane default.**

* * *

## 12.1 Set Balanced battery limit

## What this does

Sets the battery charge cap to 80%.

## Why it is needed

Reduces time spent sitting at 100% charge while plugged in.

## Risk level

Low.

## Command

```bash
export PATH="$HOME/.local/bin:$PATH"

z13ctl batterylimit --set 80
z13ctl batterylimit --get
```

## Expected output

```text
80
```

## Verification command

```bash
z13ctl status
```

## Rollback command

To remove the charge cap:

```bash
z13ctl batterylimit --set 100
z13ctl batterylimit --get
```

* * *

# 13. Stock profile switching

For now, use the firmware’s stock profiles. `z13ctl` documents:

*   `quiet`
    
*   `balanced`
    
*   `performance`
    
*   `custom`
    

We are intentionally avoiding `custom` until a later part.

* * *

## 13.1 Balanced Daily Driver Profile

## What this does

Sets the Z13 to firmware-managed balanced mode, sets battery limit to 80%, and sets lighting to medium.

## Why it is needed

This is the default profile for daily SteamOS use, gaming, coding, and moderate LLM work.

## Risk level

Low.

## Command

```bash
export PATH="$HOME/.local/bin:$PATH"

z13ctl profile --set balanced
z13ctl batterylimit --set 80
z13ctl brightness medium || true

mkdir -p "$HOME/.config/z13-profiles"
printf "balanced-daily-driver\n" > "$HOME/.config/z13-profiles/current-profile"

echo "Current recorded profile:"
cat "$HOME/.config/z13-profiles/current-profile"

echo
echo "z13ctl status:"
z13ctl status
```

## Expected output

You should see:

```text
balanced-daily-driver
```

And `z13ctl status` should show profile/battery information.

## Verification command

```bash
z13ctl profile --get
z13ctl batterylimit --get
cat "$HOME/.config/z13-profiles/current-profile"
```

## Rollback command

This profile is the rollback target:

```bash
export PATH="$HOME/.local/bin:$PATH"

z13ctl profile --set balanced
z13ctl batterylimit --set 80
z13ctl fancurve --reset 2>/dev/null || true
z13ctl tdp --reset 2>/dev/null || true
z13ctl brightness medium || true

mkdir -p "$HOME/.config/z13-profiles"
printf "balanced-daily-driver\n" > "$HOME/.config/z13-profiles/current-profile"

z13ctl status
```

* * *

## 13.2 Maximum Throughput Profile

## What this does

Sets the firmware profile to performance mode, keeps battery limit at 80%, and sets lighting high.

## Why it is needed

This is the safest first “heavy work” mode without custom TDP or fan curves.

## Risk level

Low to Medium. Low because it uses stock firmware performance mode. Medium because it may increase heat, fan noise, and power draw.

## Command

```bash
export PATH="$HOME/.local/bin:$PATH"

z13ctl profile --set performance
z13ctl batterylimit --set 80
z13ctl brightness high || true

mkdir -p "$HOME/.config/z13-profiles"
printf "maximum-throughput\n" > "$HOME/.config/z13-profiles/current-profile"

echo "Current recorded profile:"
cat "$HOME/.config/z13-profiles/current-profile"

echo
echo "z13ctl status:"
z13ctl status
```

## Expected output

You should see:

```text
maximum-throughput
```

`z13ctl profile --get` should return:

```text
performance
```

## Verification command

```bash
z13ctl profile --get
z13ctl batterylimit --get
z13ctl status
```

## Rollback command

Return to Balanced:

```bash
export PATH="$HOME/.local/bin:$PATH"

z13ctl profile --set balanced
z13ctl batterylimit --set 80
z13ctl brightness medium || true

mkdir -p "$HOME/.config/z13-profiles"
printf "balanced-daily-driver\n" > "$HOME/.config/z13-profiles/current-profile"

z13ctl status
```

* * *

## 13.3 Longevity / Cool & Stable Profile

## What this does

Sets the firmware profile to quiet mode, sets battery limit to 70%, and lowers lighting.

## Why it is needed

This is for heat, quiet operation, battery preservation, travel, and long low-intensity sessions.

## Risk level

Low.

## Command

```bash
export PATH="$HOME/.local/bin:$PATH"

z13ctl profile --set quiet
z13ctl batterylimit --set 70
z13ctl brightness low || true

mkdir -p "$HOME/.config/z13-profiles"
printf "longevity-cool-stable\n" > "$HOME/.config/z13-profiles/current-profile"

echo "Current recorded profile:"
cat "$HOME/.config/z13-profiles/current-profile"

echo
echo "z13ctl status:"
z13ctl status
```

## Expected output

You should see:

```text
longevity-cool-stable
```

`z13ctl profile --get` should return:

```text
quiet
```

## Verification command

```bash
z13ctl profile --get
z13ctl batterylimit --get
z13ctl status
```

## Rollback command

Return to Balanced:

```bash
export PATH="$HOME/.local/bin:$PATH"

z13ctl profile --set balanced
z13ctl batterylimit --set 80
z13ctl brightness medium || true

mkdir -p "$HOME/.config/z13-profiles"
printf "balanced-daily-driver\n" > "$HOME/.config/z13-profiles/current-profile"

z13ctl status
```

* * *

# 14. Create profile-switching scripts

Now we make the three profiles easy to call. These scripts still use only stock firmware profiles.

* * *

## 14.1 Create Balanced script

## What this does

Creates:

```text
$HOME/.local/bin/z13-balanced
```

## Why it is needed

Gives you one command to return to the recommended default.

## Risk level

Low.

## Command

```bash
mkdir -p "$HOME/.local/bin"

cat > "$HOME/.local/bin/z13-balanced" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"

z13ctl profile --set balanced
z13ctl batterylimit --set 80
z13ctl brightness medium >/dev/null 2>&1 || true

mkdir -p "$HOME/.config/z13-profiles"
printf "balanced-daily-driver\n" > "$HOME/.config/z13-profiles/current-profile"

echo "Z13 profile set to: Balanced Daily Driver"
echo
z13ctl status
EOF

chmod +x "$HOME/.local/bin/z13-balanced"

"$HOME/.local/bin/z13-balanced"
```

## Expected output

You should see:

```text
Z13 profile set to: Balanced Daily Driver
```

Then `z13ctl status`.

## Verification command

```bash
"$HOME/.local/bin/z13-balanced"
cat "$HOME/.config/z13-profiles/current-profile"
```

## Rollback command

```bash
rm -f "$HOME/.local/bin/z13-balanced"
```

* * *

## 14.2 Create Maximum Throughput script

## What this does

Creates:

```text
$HOME/.local/bin/z13-max
```

## Why it is needed

Gives you one command for plugged-in heavy workloads.

## Risk level

Low to Medium.

## Command

```bash
mkdir -p "$HOME/.local/bin"

cat > "$HOME/.local/bin/z13-max" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"

z13ctl profile --set performance
z13ctl batterylimit --set 80
z13ctl brightness high >/dev/null 2>&1 || true

mkdir -p "$HOME/.config/z13-profiles"
printf "maximum-throughput\n" > "$HOME/.config/z13-profiles/current-profile"

echo "Z13 profile set to: Maximum Throughput"
echo "Use this plugged in. Expect more heat and fan noise."
echo
z13ctl status
EOF

chmod +x "$HOME/.local/bin/z13-max"

"$HOME/.local/bin/z13-max"
```

## Expected output

You should see:

```text
Z13 profile set to: Maximum Throughput
```

## Verification command

```bash
"$HOME/.local/bin/z13-max"
cat "$HOME/.config/z13-profiles/current-profile"
```

## Rollback command

```bash
rm -f "$HOME/.local/bin/z13-max"
```

* * *

## 14.3 Create Longevity script

## What this does

Creates:

```text
$HOME/.local/bin/z13-cool
```

## Why it is needed

Gives you one command for quiet, cool, conservative operation.

## Risk level

Low.

## Command

```bash
mkdir -p "$HOME/.local/bin"

cat > "$HOME/.local/bin/z13-cool" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"

z13ctl profile --set quiet
z13ctl batterylimit --set 70
z13ctl brightness low >/dev/null 2>&1 || true

mkdir -p "$HOME/.config/z13-profiles"
printf "longevity-cool-stable\n" > "$HOME/.config/z13-profiles/current-profile"

echo "Z13 profile set to: Longevity / Cool & Stable"
echo
z13ctl status
EOF

chmod +x "$HOME/.local/bin/z13-cool"

"$HOME/.local/bin/z13-cool"
```

## Expected output

You should see:

```text
Z13 profile set to: Longevity / Cool & Stable
```

## Verification command

```bash
"$HOME/.local/bin/z13-cool"
cat "$HOME/.config/z13-profiles/current-profile"
```

## Rollback command

```bash
rm -f "$HOME/.local/bin/z13-cool"
```

* * *

# 15. Create a profile status command

## What this does

Creates:

```text
$HOME/.local/bin/z13-whereami
```

## Why it is needed

Gives you one command to see your recorded profile and actual hardware state.

## Risk level

Low.

## Command

```bash
mkdir -p "$HOME/.local/bin"

cat > "$HOME/.local/bin/z13-whereami" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"

echo "Recorded Companion Profile:"
if [ -f "$HOME/.config/z13-profiles/current-profile" ]; then
  cat "$HOME/.config/z13-profiles/current-profile"
else
  echo "No recorded profile file found."
fi

echo
echo "z13ctl Hardware Status:"
z13ctl status
EOF

chmod +x "$HOME/.local/bin/z13-whereami"

"$HOME/.local/bin/z13-whereami"
```

## Expected output

You should see:

*   recorded profile
    
*   hardware status
    

## Verification command

```bash
z13-whereami
```

## Rollback command

```bash
rm -f "$HOME/.local/bin/z13-whereami"
```

* * *

# 16. Profile validation procedure

Run these in order.

## 16.1 Validate Balanced

```bash
z13-balanced

echo
echo "Profile:"
z13ctl profile --get

echo
echo "Battery limit:"
z13ctl batterylimit --get

echo
echo "Recorded profile:"
cat "$HOME/.config/z13-profiles/current-profile"
```

Success:

*   `profile --get` returns `balanced`.
    
*   battery limit returns `80`.
    
*   recorded profile returns `balanced-daily-driver`.
    

* * *

## 16.2 Validate Maximum Throughput

```bash
z13-max

echo
echo "Profile:"
z13ctl profile --get

echo
echo "Battery limit:"
z13ctl batterylimit --get

echo
echo "Recorded profile:"
cat "$HOME/.config/z13-profiles/current-profile"
```

Success:

*   `profile --get` returns `performance`.
    
*   battery limit returns `80`.
    
*   recorded profile returns `maximum-throughput`.
    

* * *

## 16.3 Validate Longevity

```bash
z13-cool

echo
echo "Profile:"
z13ctl profile --get

echo
echo "Battery limit:"
z13ctl batterylimit --get

echo
echo "Recorded profile:"
cat "$HOME/.config/z13-profiles/current-profile"
```

Success:

*   `profile --get` returns `quiet`.
    
*   battery limit returns `70`.
    
*   recorded profile returns `longevity-cool-stable`.
    

* * *

## 16.4 Return to Balanced

```bash
z13-balanced
```

This should be your final state at the end of Part 2.

* * *

# 17. Reboot persistence check

This is important.

## What this does

Checks whether profile and battery settings survive a reboot.

## Why it is needed

Some hardware settings persist. Some reset. We need to know which behavior your install has.

## Risk level

Low.

## Before reboot

Set Balanced:

```bash
z13-balanced
z13-whereami | tee "$HOME/z13-audit/19-before-reboot-profile.txt"
```

Now reboot normally from the SteamOS UI, or run:

```bash
systemctl reboot
```

## After reboot

Open Desktop Mode and Konsole again, then run:

```bash
export PATH="$HOME/.local/bin:$PATH"

z13-whereami | tee "$HOME/z13-audit/20-after-reboot-profile.txt"

echo
echo "Difference between before and after, if any:"
diff -u "$HOME/z13-audit/19-before-reboot-profile.txt" "$HOME/z13-audit/20-after-reboot-profile.txt" || true
```

## Expected output

Good result:

*   recorded profile still says `balanced-daily-driver`
    
*   hardware profile still says `balanced`
    
*   battery limit still says `80`
    

Acceptable result:

*   recorded profile survives
    
*   hardware profile resets
    

If hardware profile resets, the scripts still work. Later we can add a user service to reapply your preferred profile at login.

## Rollback command

No rollback needed.

* * *

# 18. Do not enable these yet

`z13ctl` supports these, but we are not using them yet:

```bash
z13ctl tdp --set 85 --force
```

```bash
z13ctl fancurve --set "48:1%,53:9%,57:12%,60:17%,63:22%,65:27%,70:35%,76:40%"
```

```bash
z13ctl undervolt --set -20
```

Why:

*   Custom TDP changes power behavior.
    
*   Fan curves change thermal response.
    
*   Undervolting requires `ryzen_smu`, and the `z13ctl` docs specifically note Strix Halo needs the `amkillam` fork rather than the original `ryzen_smu` project. (Jeff Hagadorn)
    
*   SteamOS kernel/module handling is not the same as a normal Arch install.
    
*   We have not yet run thermal/load validation.
    

* * *

# 19. Me5 microcontroller status

I still could not verify a safe, authoritative Me5 microcontroller mitigation. For now:

*   Do not flash EC firmware.
    
*   Do not apply random ACPI patches.
    
*   Do not install kernel modules from unverified forum posts.
    
*   Do not use Windows-only firmware tools through Wine.
    
*   Do not assume a Reddit workaround is safe.
    

Current status:

```text
I could not verify this.
Do not do this unless you are willing to risk firmware or hardware instability.
The safest alternative is to use stock firmware profiles and verified Linux sysfs controls only.
```

* * *

# 20. Optional: travel charge override

Use this only when you actually need a full battery.

## What this does

Sets battery charge limit to 100%.

## Why it is needed

Useful before travel or long unplugged use.

## Risk level

Low.

## Command

```bash
z13ctl batterylimit --set 100
z13ctl batterylimit --get
```

## Expected output

```text
100
```

## Rollback command

Return to normal:

```bash
z13ctl batterylimit --set 80
z13ctl batterylimit --get
```

* * *

# 21. Part 2 final state

At the end of this part, run:

```bash
z13-balanced
z13-whereami
```

Expected final state:

```text
balanced-daily-driver
```

Hardware profile:

```text
balanced
```

Battery limit:

```text
80
```

* * *

# 22. Part 2 validation checklist

## All profiles

*   `z13ctl --help` works.
    
*   `z13ctl list` detects the Z13 hardware.
    
*   `z13ctl status` works.
    
*   `z13ctl profile --get` works.
    
*   `z13ctl batterylimit --get` works.
    
*   `z13-balanced` works.
    
*   `z13-max` works.
    
*   `z13-cool` works.
    
*   `z13-whereami` works.
    
*   `z13-balanced` returns the system to Balanced Daily Driver.
    
*   No custom TDP has been applied.
    
*   No custom fan curve has been applied.
    
*   No undervolt has been applied.
    
*   SteamOS read-only mode has not been disabled.
    

## Maximum Throughput Profile

Verify:

```bash
z13-max
z13ctl profile --get
z13ctl batterylimit --get
z13ctl status
```

Success:

*   profile is `performance`
    
*   battery limit is `80`
    
*   system is plugged in for heavy work
    

Return to Balanced:

```bash
z13-balanced
```

## Balanced Daily Driver Profile

Verify:

```bash
z13-balanced
z13ctl profile --get
z13ctl batterylimit --get
z13ctl status
```

Success:

*   profile is `balanced`
    
*   battery limit is `80`
    

## Longevity / Cool & Stable Profile

Verify:

```bash
z13-cool
z13ctl profile --get
z13ctl batterylimit --get
z13ctl status
```

Success:

*   profile is `quiet`
    
*   battery limit is `70`
    

Return to Balanced:

```bash
z13-balanced
```

* * *

# 23. Part 2 rollback: remove all changes from this part

This removes:

*   `z13ctl` user-local binary
    
*   profile scripts
    
*   profile record
    
*   `z13ctl` udev rule
    
*   `z13ctl` permission service
    
*   install staging folder
    

## Risk level

Medium.

## Command

```bash
sudo systemctl disable --now z13ctl-perms.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/z13ctl-perms.service
sudo rm -f /etc/udev/rules.d/99-z13ctl.rules
sudo systemctl daemon-reload
sudo udevadm control --reload-rules

rm -f "$HOME/.local/bin/z13ctl"
rm -f "$HOME/.local/bin/z13-balanced"
rm -f "$HOME/.local/bin/z13-max"
rm -f "$HOME/.local/bin/z13-cool"
rm -f "$HOME/.local/bin/z13-whereami"

rm -rf "$HOME/.config/z13-profiles"
rm -rf "$HOME/Downloads/z13ctl-install"

echo "Part 2 changes removed."
```

## Verification command

```bash
command -v z13ctl || echo "z13ctl no longer found in PATH."
ls -lah /etc/udev/rules.d/99-z13ctl.rules 2>/dev/null || echo "z13ctl udev rule removed."
systemctl status z13ctl-perms.service --no-pager 2>/dev/null || echo "z13ctl permission service removed or not loaded."
```

* * *

# 24. Safe stopping point

Stop here for Part 2. The machine should now have:

*   a conservative Z13 hardware-control tool installed in your home directory
    
*   verified hardware status readback
    
*   stock profile switching
    
*   battery charge limits
    
*   no dangerous firmware/EC/power-table changes
    
*   no custom TDP
    
*   no undervolting
    
*   no ROCm install yet
    

**Next manual section:** Part 3 — BIOS/firmware policy, immutable-root strategy, storage layout, and preparation for the local LLM stack.  
  
Back to the index  
[[Z13 Steam OS and LLM Manual]]  
  
Next chapter  
[[Part 3 — Firmware Policy, Security Posture, Immutable-Root Strategy, and Storage Layout]]
