#!/usr/bin/env bash
# Install the NVIDIA driver for the two Tesla P40s.
#
#   sudo bash deploy/20-gpu-driver.sh
#
# PIN: 580 is the LAST NVIDIA branch that supports Pascal. The 595 packages
# apt offers on this host will install and then not drive a P40. Do not
# "upgrade" past 580; there is nothing to gain and the cards stop working.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo bash $0" >&2; exit 1; fi
log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

log "Confirming the cards are visible on the PCI bus"
if ! lspci | grep -qi 'NVIDIA.*GP102GL'; then
  echo "No Tesla P40 found by lspci. Check the VMware passthrough config." >&2
  exit 1
fi
lspci | grep -i nvidia

log "Blacklisting nouveau"
cat > /etc/modprobe.d/blacklist-nouveau.conf <<'CONF'
blacklist nouveau
options nouveau modeset=0
CONF
update-initramfs -u

log "Installing nvidia-driver-580-server"
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  nvidia-driver-580-server nvidia-utils-580-server

# Hold the packages so an unattended upgrade cannot move them to 595.
apt-mark hold nvidia-driver-580-server nvidia-utils-580-server || true

log "Enabling persistence mode at boot"
systemctl enable --now nvidia-persistenced 2>/dev/null || true

cat <<'BANNER'

------------------------------------------------------------
 REBOOT NOW, then run:  bash deploy/21-verify-gpu.sh
------------------------------------------------------------
 IF nvidia-smi FAILS AFTER THE REBOOT, the cause is almost
 certainly the VM, not the driver. A 24 GB card needs large
 MMIO. Power the VM off and add to its .vmx:

   pciPassthru.use64bitMMIO = "TRUE"
   pciPassthru.64bitMMIOSizeGB = "128"

 lspci listing the cards (as it already does) does NOT mean
 this is set — the driver is what needs the address space.
------------------------------------------------------------
BANNER
