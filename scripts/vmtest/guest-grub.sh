#!/bin/bash
# The other branch of the boot code, on a booted guest: grub + mkinitcpio +
# LUKS, plus podman, home_files and zram — and then generations/rollback.
#
# Every other VM in this round used dracut + sd-boot, so this is the half of
# the boot chain none of them exercised. Emits GRUB-* markers; ends GRUB-DONE.
set -x
cd /root/repo 2>/dev/null || true
D="python -m dasik --no-log"

echo "GRUB: BEGIN"
echo "GRUB-ROOT: $(findmnt -no SOURCE / 2>/dev/null)"
echo "GRUB-CMDLINE: $(tr ' ' '\n' < /proc/cmdline | grep -E '^cryptdevice|^root=|^rd.luks' | tr '\n' ' ')"
echo "GRUB-BOOTLOADER: $([ -d /boot/grub ] && echo grub-present || echo MISSING)"
echo "GRUB-CFG-ENTRIES: $(grep -c '^menuentry' /boot/grub/grub.cfg 2>/dev/null)"
echo "GRUB-MKINITCPIO-HOOKS: $(grep -E '^HOOKS' /etc/mkinitcpio.conf 2>/dev/null)"
echo "GRUB-INITRAMFS: $(ls /boot/initramfs-linux*.img 2>/dev/null | tr '\n' ' ')"
echo "GRUB-CRYPTTAB: $(grep -vcE '^\s*(#|$)' /etc/crypttab 2>/dev/null)"
echo "GRUB-ZRAM: $(swapon --show=NAME,PRIO --noheadings 2>/dev/null | tr '\n' '|')"
echo "GRUB-PODMAN: $(pacman -Qq podman podman-docker 2>&1 | tr '\n' ' ')"
echo "GRUB-HOMEFILE: $(stat -c '%A %U' /home/test/.config/dasik/hello.conf 2>&1)"
echo "GRUB-HOMEFILE-BODY: $(tr '\n' '|' < /home/test/.config/dasik/hello.conf 2>&1)"

echo "GRUB-PLAN-BEGIN"
$D plan config/vm-grub-mkinitcpio.json --target /
echo "GRUB-PLAN-RC=$?"

echo "GRUB-GENERATIONS-BEGIN"
$D generations --target /
echo "GRUB-GENERATIONS-RC=$?"

cp config/vm-grub-mkinitcpio.json /root/captured.json
echo "GRUB-SYNC-BEGIN"
$D sync /root/captured.json --target /
echo "GRUB-SYNC-RC=$?"
echo "GRUB-CAPTURED-CHECK: $($D check /root/captured.json >/dev/null 2>&1; echo rc=$?)"
echo "GRUB-CAPTURED-BOOTLOADER: $(python -c "import json;d=json.load(open('/root/captured.json'));print(d.get('bootloader'), d.get('initramfs'))" 2>&1)"
echo "GRUB-CAPTURED-CONTAINERS: $(python -c "import json;print(json.load(open('/root/captured.json')).get('containers'))" 2>&1)"

echo "GRUB-PLAN-AFTER-SYNC-BEGIN"
$D plan /root/captured.json --target /
echo "GRUB-PLAN-AFTER-SYNC-RC=$?"

# A second generation to roll back FROM: add one harmless file.
python - <<'PY'
import json
cfg = json.load(open('/root/repo/config/vm-grub-mkinitcpio.json'))
cfg.setdefault("files", []).append(
    {"path": "/etc/dasik-rollback-marker.conf", "content": "# gen 2\n"})
json.dump(cfg, open('/root/gen2.json', 'w'), indent=2)
PY
echo "GRUB-APPLY-GEN2-BEGIN"
$D apply /root/gen2.json --target / --yes
echo "GRUB-APPLY-GEN2-RC=$?"
echo "GRUB-MARKER-AFTER-GEN2: $([ -f /etc/dasik-rollback-marker.conf ] && echo present || echo MISSING)"

echo "GRUB-ROLLBACK-BEGIN"
$D rollback --target / --yes
echo "GRUB-ROLLBACK-RC=$?"
echo "GRUB-MARKER-AFTER-ROLLBACK: $([ -f /etc/dasik-rollback-marker.conf ] && echo present || echo removed)"
echo "GRUB-ROOT-AFTER-ROLLBACK: $(findmnt -no SOURCE / 2>/dev/null)"
echo "GRUB-CMDLINE-ENTRY-AFTER-ROLLBACK: $(grep -ho 'cryptdevice=[^ ]*\|root=[^ ]*' /boot/grub/grub.cfg 2>/dev/null | head -2 | tr '\n' ' ')"

echo "GRUB-PLAN-AFTER-ROLLBACK-BEGIN"
$D plan config/vm-grub-mkinitcpio.json --target /
echo "GRUB-PLAN-AFTER-ROLLBACK-RC=$?"

echo "GRUB-DONE rc=0"
sync
sleep 3
poweroff -f
