#!/bin/bash
# Everything-at-once verification, run INSIDE the booted (already-installed) guest.
#
# The point is the INTERACTION: an encrypted btrfs root with subvolumes, a
# random-key swap, zram, snapper, AppArmor, PAM, firewalld and docker all
# reconciling in the same run. Each of those has its own suite; none of them
# proves they coexist. Emits MEGAMIX-* markers; ends with MEGAMIX-DONE.
set -x
cd /root/repo 2>/dev/null || true

echo "MEGAMIX: BEGIN (booted guest)"

# --- the encrypted root came up, on the right subvolume --------------------- #
echo "MEGAMIX-ROOT-SRC: $(findmnt -no SOURCE / 2>/dev/null)"
echo "MEGAMIX-ROOT-OPTS: $(findmnt -no OPTIONS / 2>/dev/null | tr ',' '\n' | grep -E 'subvol|compress' | tr '\n' ' ')"
echo "MEGAMIX-SUBVOLS: $(btrfs subvolume list / 2>/dev/null | awk '{print $NF}' | tr '\n' ' ')"
echo "MEGAMIX-HOME-MOUNTED: $(findmnt -no TARGET /home 2>/dev/null || echo MISSING)"
echo "MEGAMIX-LUKS: $(lsblk -no NAME,FSTYPE /dev/vda3 2>/dev/null | tr '\n' '|')"
echo "MEGAMIX-CMDLINE-LUKS: $(tr ' ' '\n' < /proc/cmdline | grep -E '^rd.luks|^root=' | tr '\n' ' ')"

# --- swap: the random-key one AND zram, both live -------------------------- #
echo "MEGAMIX-SWAPS: $(swapon --show=NAME,TYPE,PRIO --noheadings 2>/dev/null | tr '\n' '|')"
echo "MEGAMIX-CRYPTTAB: $(grep -vE '^\s*(#|$)' /etc/crypttab 2>/dev/null | tr '\n' '|')"
echo "MEGAMIX-ZRAM-CONF: $(grep -vE '^\s*(#|$)' /etc/systemd/zram-generator.conf 2>/dev/null | tr '\n' '|')"
echo "MEGAMIX-ZRAM-DEV: $([ -b /dev/zram0 ] && echo present || echo MISSING)"

# --- snapper --------------------------------------------------------------- #
echo "MEGAMIX-SNAPPER-CONFIGS: $(snapper list-configs 2>&1 | tail -n +3 | awk '{print $1}' | tr '\n' ' ')"
echo "MEGAMIX-SNAPPER-TIMERS: $(systemctl is-enabled snapper-timeline.timer snapper-cleanup.timer 2>&1 | tr '\n' ' ')"

# --- apparmor + audit ------------------------------------------------------ #
echo "MEGAMIX-AA-ENABLED: $(aa-enabled 2>&1 | head -1)"
echo "MEGAMIX-AA-PROFILES: $(aa-status --profiled 2>/dev/null || echo unknown)"
echo "MEGAMIX-AUDIT-LOGDIR: $(stat -c '%A %U %G' /var/log/audit 2>&1)"
echo "MEGAMIX-AUDITD-LOGGROUP: $(grep -i '^log_group' /etc/audit/auditd.conf 2>/dev/null)"

# --- pam ------------------------------------------------------------------- #
echo "MEGAMIX-FAILLOCK: $(grep -vE '^\s*(#|$)' /etc/security/faillock.conf 2>/dev/null | tr '\n' ' ')"
echo "MEGAMIX-PWQUALITY: $(grep -c pam_pwquality /etc/pam.d/passwd 2>/dev/null)"

# --- firewalld ------------------------------------------------------------- #
echo "MEGAMIX-FIREWALLD-UNIT: $(systemctl is-enabled firewalld.service 2>&1)"
echo "MEGAMIX-FIREWALLD-ZONE: $(firewall-offline-cmd --zone=public --list-services 2>&1 | tail -1)"
echo "MEGAMIX-FIREWALLD-RICH: $(firewall-offline-cmd --zone=public --list-rich-rules 2>&1 | tr '\n' '|')"

# --- docker ---------------------------------------------------------------- #
echo "MEGAMIX-DOCKER-UNIT: $(systemctl is-enabled docker.socket 2>&1)"
echo "MEGAMIX-DOCKER-GROUP: $(id -nG test 2>&1)"

# --- network --------------------------------------------------------------- #
echo "MEGAMIX-NM: $(systemctl is-enabled NetworkManager.service 2>&1)"

# --- the verbs, against the LIVE host -------------------------------------- #
D="python -m dasik --no-log"

echo "MEGAMIX-VERB-CHECK: $($D check config/vm-megamix-encrypted.json >/dev/null 2>&1; echo rc=$?)"

echo "MEGAMIX-PLAN-BEGIN"
$D plan config/vm-megamix-encrypted.json --target /
echo "MEGAMIX-PLAN-RC=$?"

echo "MEGAMIX-GENERATIONS-BEGIN"
$D generations --target /
echo "MEGAMIX-GENERATIONS-RC=$?"

cp config/vm-megamix-encrypted.json /root/captured.json
echo "MEGAMIX-SYNC-BEGIN"
$D sync /root/captured.json --target /
echo "MEGAMIX-SYNC-RC=$?"
echo "MEGAMIX-CAPTURED-CHECK: $($D check /root/captured.json >/dev/null 2>&1; echo rc=$?)"
for k in disks zram snapper apparmor pam firewall containers network; do
  echo "MEGAMIX-CAPTURED-$k: $(python -c "import json,sys;print(json.dumps(json.load(open('/root/captured.json')).get('$k'))[:320])" 2>&1)"
done

echo "MEGAMIX-PLAN-AFTER-SYNC-BEGIN"
$D plan /root/captured.json --target /
echo "MEGAMIX-PLAN-AFTER-SYNC-RC=$?"

echo "MEGAMIX-DONE rc=0"
sync
sleep 3
poweroff -f
