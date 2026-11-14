#!/usr/bin/env bash
# RobotDx device reset access: the ONE root step (docs/device-reset.md "Permissions"). Run it once, with sudo:
#
#   sudo bash /home/bam/io-diagnostics/tools/install_device_reset_access.sh
#
# After it, every USB rung of a reset ladder runs as the rack user (group plugdev) with no root at runtime:
#   1. installs tools/udev/70-robotdx-device-reset.rules (generated from io_diagnostics/recovery/profiles/, read it first):
#      for the rig's device classes only (fdcanusb, RealSense, OBSBOT), plugdev may open the device's usbfs node, flip its
#      sysfs `authorized` switch, and flip the `disable` switch of the hub port it is plugged into (and that port's USB 3 peer)
#   2. reads each hub's power switching from its hub descriptor with the stock `lsusb -v` (only root may open a hub) into
#      /var/lib/robotdx/hub-power-switching.txt, so the ladder knows, as any user, where a port power cycle really cuts VBUS
#   3. prints `python3 -m io_diagnostics.recovery check` as you: which rungs now run here, and why each other one cannot
#
# It installs no package, runs no repo code as root, and touches no device (udevadm trigger re-applies permissions only).
# Undo: sudo rm /etc/udev/rules.d/70-robotdx-device-reset.rules /var/lib/robotdx/hub-power-switching.txt && sudo udevadm control --reload
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname "$here")"
rules_src="$here/udev/70-robotdx-device-reset.rules"
rules_dst=/etc/udev/rules.d/70-robotdx-device-reset.rules
facts_dir=/var/lib/robotdx
facts="$facts_dir/hub-power-switching.txt"

if [ "$(id -u)" -ne 0 ]; then
  echo "run it with sudo: sudo bash $0" >&2
  exit 2
fi
user="${SUDO_USER:-}"
if [ -n "$user" ] && ! id -nG "$user" | tr ' ' '\n' | grep -qx plugdev; then
  echo "note: $user is not in plugdev; add it once: sudo usermod -aG plugdev $user (then log in again)" >&2
fi

# 1. the rules
udevadm verify "$rules_src" >/dev/null 2>&1 || { echo "udevadm verify rejects $rules_src: not installed" >&2; exit 1; }
install -m 0644 "$rules_src" "$rules_dst"
udevadm control --reload
udevadm trigger --action=change --subsystem-match=usb
udevadm settle --timeout=10 || true
echo "installed $rules_dst"

# 2. each hub's power switching (wHubCharacteristics), by its sysfs name and vendor:product
mkdir -p "$facts_dir"
tmp="$(mktemp)"
{
  echo "# hub  vid:pid  individual|ganged|none|unknown   (read $(date -Is) by $0 from each hub's descriptor, lsusb -v as root)"
  for d in /sys/bus/usb/devices/*; do
    name="$(basename "$d")"
    case "$name" in *:*) continue ;; esac
    [ "$(cat "$d/bDeviceClass" 2>/dev/null)" = "09" ] || continue
    bus="$(cat "$d/busnum")"; dev="$(cat "$d/devnum")"
    ids="$(cat "$d/idVendor"):$(cat "$d/idProduct")"
    line="$(lsusb -v -s "$bus:$dev" 2>/dev/null | grep -m1 -oE '(Per-port|Ganged|No) power switching' || true)"
    case "$line" in
      Per-port*) mode=individual ;;
      Ganged*) mode=ganged ;;
      No*) mode=none ;;
      *) mode=unknown ;;
    esac
    echo "$name $ids $mode"
  done
} > "$tmp"
install -m 0644 "$tmp" "$facts"
rm -f "$tmp"
echo "wrote $facts:"
grep -v '^#' "$facts" | sed 's/^/  /'

# 3. what runs now, as the user (never as root)
if [ -n "$user" ]; then
  echo
  sudo -u "$user" env -C "$repo" python3 -m io_diagnostics.recovery check || true
fi
