#!/bin/bash
# Block C on a booted guest, and then the direction nobody tests: the blocks
# REMOVED from the config.
#
# The reconciler hands an action its EMPTY config when a previous generation
# owned the domain, and "empty" is not "the empty value" — that distinction has
# produced destructive MODIFYs in this repo before. So this runs the stack, then
# re-applies a config with `apparmor`, `pam` and `firewall` stripped out and
# checks that what dasik owned is undone and nothing else is touched.
# Emits BLOCKC-* markers; ends with BLOCKC-DONE.
set -x
cd /root/repo 2>/dev/null || true
D="python -m dasik --no-log"

echo "BLOCKC: BEGIN"

# --- the stack is live ------------------------------------------------------ #
echo "BLOCKC-SWAP: $(swapon --show=NAME --noheadings 2>/dev/null | tr '\n' ' ')"
echo "BLOCKC-CRYPTTAB: $(grep -vE '^\s*(#|$)' /etc/crypttab 2>/dev/null | tr '\n' '|')"
echo "BLOCKC-AA: $(aa-enabled 2>&1 | head -1)"
echo "BLOCKC-AUDIT-LOGDIR: $(stat -c '%A %U %G' /var/log/audit 2>&1)"
echo "BLOCKC-FAILLOCK: $(grep -vE '^\s*(#|$)' /etc/security/faillock.conf 2>/dev/null | tr '\n' ' ')"
echo "BLOCKC-PWQ: $(grep -c pam_pwquality /etc/pam.d/passwd 2>/dev/null)"
echo "BLOCKC-UFW-STATUS: $(ufw status 2>&1 | tr '\n' '|')"

# --- re-apply the SAME config: the #211 regression ------------------------- #
# `ufw` rules that a re-apply removes instead of leaving alone is exactly what
# that PR fixed; a rule count that drops here is the symptom.
echo "BLOCKC-REAPPLY-BEGIN"
$D apply config/vm-blockc-ufw.json --target / --yes
echo "BLOCKC-REAPPLY-RC=$?"
echo "BLOCKC-UFW-AFTER-REAPPLY: $(ufw status 2>&1 | grep -cE '^(22|22000|21027|445|139)')"

echo "BLOCKC-PLAN-AFTER-REAPPLY-BEGIN"
$D plan config/vm-blockc-ufw.json --target /
echo "BLOCKC-PLAN-AFTER-REAPPLY-RC=$?"

# --- now DROP the blocks --------------------------------------------------- #
python - <<'PY'
import json
cfg = json.load(open('/root/repo/config/vm-blockc-ufw.json'))
for key in ("apparmor", "pam", "firewall"):
    cfg.pop(key, None)
json.dump(cfg, open('/root/stripped.json', 'w'), indent=2)
PY
echo "BLOCKC-STRIPPED-CHECK: $($D check /root/stripped.json >/dev/null 2>&1; echo rc=$?)"

echo "BLOCKC-PLAN-STRIPPED-BEGIN"
$D plan /root/stripped.json --target /
echo "BLOCKC-PLAN-STRIPPED-RC=$?"

echo "BLOCKC-APPLY-STRIPPED-BEGIN"
$D apply /root/stripped.json --target / --yes
echo "BLOCKC-APPLY-STRIPPED-RC=$?"

# What must be undone, and what must NOT be touched.
echo "BLOCKC-AFTER-STRIP-FAILLOCK: $(grep -vE '^\s*(#|$)' /etc/security/faillock.conf 2>/dev/null | tr '\n' ' ')"
echo "BLOCKC-AFTER-STRIP-LIMITS: $([ -f /etc/security/limits.d/10-dasik.conf ] && echo present || echo removed)"
echo "BLOCKC-AFTER-STRIP-PWQ: $(grep -c pam_pwquality /etc/pam.d/passwd 2>/dev/null)"
echo "BLOCKC-AFTER-STRIP-LSM: $(tr ' ' '\n' < /proc/cmdline | grep -c '^lsm=')"
echo "BLOCKC-AFTER-STRIP-ENTRY-LSM: $(grep -ho 'lsm=[^ ]*' /boot/loader/entries/*.conf 2>/dev/null | head -1)"
echo "BLOCKC-AFTER-STRIP-AUDITD: $(grep -i '^log_group' /etc/audit/auditd.conf 2>/dev/null || echo removed)"
echo "BLOCKC-AFTER-STRIP-SWAP: $(swapon --show=NAME --noheadings 2>/dev/null | tr '\n' ' ')"
echo "BLOCKC-AFTER-STRIP-ROOT: $(findmnt -no SOURCE / 2>/dev/null)"

echo "BLOCKC-PLAN-STRIPPED-AGAIN-BEGIN"
$D plan /root/stripped.json --target /
echo "BLOCKC-PLAN-STRIPPED-AGAIN-RC=$?"

echo "BLOCKC-DONE rc=0"
sync
sleep 3
poweroff -f
