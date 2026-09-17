#!/bin/bash
# ufw backend of the firewall-removal semantics, driven against the LIVE
# installed guest (target /). config/vm-ufw-removal.json (a copy of
# config/vm-ufw.json + python/pydantic/colorama + a baked-in ttyS0 autologin
# drop-in, both needed by this drive-based harness) declares 5 rules; this
# exercises both removal shapes:
#   1. one rule dropped from `rules` while the block stays enabled (the
#      pre-existing round-F behaviour, reaffirmed live here) -- REMOVE just
#      that rule, others untouched, `ufw` package stays installed.
#   2. the WHOLE `firewall` block removed -- REMOVE every remaining managed
#      rule. This is entangled with package ownership: the `firewall` expand
#      toggle stops declaring the `ufw` package the moment the block goes
#      undeclared, and PackagesAction (which now runs AFTER FirewallAction,
#      branch feat/snapper-firewall-removal) uninstalls it in the SAME apply.
#      FirewallAction is registered BEFORE PackagesAction precisely so its
#      `ufw --force
#      delete` runs while the binary is still there -- so `ufw status`
#      cannot be asked AFTER the full apply (the binary is gone by design,
#      nothing declares it any more), and the real observable is the KERNEL
#      packet-filter state: the port must be gone from BOTH iptables-save and
#      nft's ruleset (checked defensively -- a tool that is no longer
#      installed prints nothing, which reads as "0 matches", the correct
#      answer either way).
#
# CONVENTION: every UFWRM-<step>-RC=<rc> line is a pass (rc=0) or fail; rc()
# counts failures so UFWRM-DONE rc=<fails> is the verdict. Ends with
# UFWRM-DONE, then powers off.
set -x
export PYTHONPATH=/root/repo
cd /root/repo || { echo "UFWRM-DONE rc=91"; poweroff -f; }

D="python -m dasik"
C=config/vm-ufw-removal.json
L="--no-log"

FAILS=0
rc() { local v=$?; [ "$v" -eq 0 ] || FAILS=$((FAILS + 1)); echo "$1-RC=$v"; }
present() { grep -q "$2" "$1"; }
silent()  { grep -q "No changes" "$1"; }
# Kernel packet-filter count for a port fragment, from whichever backend is
# present (0 either way if the tool itself is gone -- see header).
pf_count() { { iptables-save 2>/dev/null; nft list ruleset 2>/dev/null; } | grep -c "$1"; }

echo "UFWRM: BEGIN (target / = the live booted host)"

echo "UFWRM-A: baseline -- ufw reports the declared rules, plan silent"
ufw status | tee /tmp/ufw-status-baseline.txt
present /tmp/ufw-status-baseline.txt '22000'; rc UFWRM-BASELINE-22000-LIVE
$D plan "$C" --target / $L > /tmp/plan-baseline.txt 2>&1; rc UFWRM-BASELINE-PLAN
cat /tmp/plan-baseline.txt
silent /tmp/plan-baseline.txt; rc UFWRM-BASELINE-SILENT
[ "$(pf_count 22000)" -gt 0 ]; rc UFWRM-BASELINE-22000-IN-KERNEL

echo "UFWRM-B: one rule dropped (block stays enabled) -- REMOVE just that rule"
python - <<'PY'
import json
cfg = json.load(open("config/vm-ufw-removal.json"))
cfg["firewall"]["rules"] = [r for r in cfg["firewall"]["rules"] if "22000" not in r]
json.dump(cfg, open("/tmp/ufw-minus-one.json", "w"), indent=2)
PY
rc UFWRM-BUILD-MINUS-ONE
$D plan /tmp/ufw-minus-one.json --target / $L > /tmp/plan-minus-one.txt 2>&1; rc UFWRM-MINUS-ONE-PLAN
cat /tmp/plan-minus-one.txt
present /tmp/plan-minus-one.txt 'remove allow 22000/tcp'; rc UFWRM-MINUS-ONE-REMOVE-PLANNED
$D apply /tmp/ufw-minus-one.json --target / --yes $L > /tmp/apply-minus-one.txt 2>&1; rc UFWRM-MINUS-ONE-APPLY
cat /tmp/apply-minus-one.txt
ufw status | tee /tmp/ufw-status-minus-one.txt
! present /tmp/ufw-status-minus-one.txt '22000'; rc UFWRM-22000-GONE-FROM-UFW
present /tmp/ufw-status-minus-one.txt '21027'; rc UFWRM-21027-STILL-THERE
pacman -Qq ufw; rc UFWRM-UFW-STILL-INSTALLED
$D plan /tmp/ufw-minus-one.json --target / $L > /tmp/plan-minus-one-2.txt 2>&1; rc UFWRM-MINUS-ONE-REPLAN
silent /tmp/plan-minus-one-2.txt; rc UFWRM-MINUS-ONE-REPLAN-SILENT

echo "UFWRM-C: rollback -- rule restored, plan silent"
$D rollback --target / --yes $L > /tmp/rollback-minus-one.txt 2>&1; rc UFWRM-ROLLBACK-MINUS-ONE
ufw status | grep -q '22000'; rc UFWRM-22000-BACK
$D plan "$C" --target / $L > /tmp/plan-after-rollback-1.txt 2>&1; rc UFWRM-PLAN-AFTER-ROLLBACK-1
silent /tmp/plan-after-rollback-1.txt; rc UFWRM-ROLLBACK-1-SILENT

echo "UFWRM-D: whole firewall block DROPPED -- REMOVE every remaining rule"
python - <<'PY'
import json
cfg = json.load(open("config/vm-ufw-removal.json"))
cfg.pop("firewall", None)
json.dump(cfg, open("/tmp/no-firewall.json", "w"), indent=2)
firewall_disabled = json.load(open("config/vm-ufw-removal.json"))
firewall_disabled["firewall"]["enable"] = False
json.dump(firewall_disabled, open("/tmp/firewall-disabled.json", "w"), indent=2)
PY
rc UFWRM-BUILD-BLOCK-DROPPED
$D plan /tmp/no-firewall.json --target / $L > /tmp/plan-no-fw.txt 2>&1; rc UFWRM-NOFW-PLAN
cat /tmp/plan-no-fw.txt
present /tmp/plan-no-fw.txt 'remove allow 22000/tcp'; rc UFWRM-NOFW-22000-REMOVE-PLANNED
present /tmp/plan-no-fw.txt 'remove limit 22/tcp'; rc UFWRM-NOFW-LIMIT22-REMOVE-PLANNED

echo "UFWRM-E: apply --yes -- rules gone from the kernel, ufw package gone (undeclared)"
$D apply /tmp/no-firewall.json --target / --yes $L > /tmp/apply-no-fw.txt 2>&1; rc UFWRM-NOFW-APPLY
cat /tmp/apply-no-fw.txt
[ "$(pf_count 22000)" -eq 0 ]; rc UFWRM-22000-GONE-FROM-KERNEL
[ "$(pf_count 21027)" -eq 0 ]; rc UFWRM-21027-GONE-FROM-KERNEL
! pacman -Qq ufw > /tmp/pacman-qq-ufw.txt 2>&1; rc UFWRM-UFW-PACKAGE-GONE
cat /tmp/pacman-qq-ufw.txt
$D plan /tmp/no-firewall.json --target / $L > /tmp/plan-after-fw-remove.txt 2>&1; rc UFWRM-NOFW-REPLAN
cat /tmp/plan-after-fw-remove.txt
silent /tmp/plan-after-fw-remove.txt; rc UFWRM-NOFW-REPLAN-SILENT

echo "UFWRM-F: rollback -- ufw + rules restored, plan silent"
$D rollback --target / --yes $L > /tmp/rollback-fw.txt 2>&1; rc UFWRM-FW-ROLLBACK
cat /tmp/rollback-fw.txt
pacman -Qq ufw; rc UFWRM-UFW-REINSTALLED
ufw status | grep -q '22000'; rc UFWRM-22000-RESTORED
$D plan "$C" --target / $L > /tmp/plan-after-fw-rollback.txt 2>&1; rc UFWRM-PLAN-AFTER-FW-ROLLBACK
silent /tmp/plan-after-fw-rollback.txt; rc UFWRM-FW-ROLLBACK-SILENT

echo "UFWRM-G: enable:false form also removes"
$D plan /tmp/firewall-disabled.json --target / $L > /tmp/plan-fwdis.txt 2>&1; rc UFWRM-FWDIS-PLAN
present /tmp/plan-fwdis.txt 'remove allow 22000/tcp'; rc UFWRM-FWDIS-REMOVE-PLANNED
$D apply /tmp/firewall-disabled.json --target / --yes $L > /tmp/apply-fwdis.txt 2>&1; rc UFWRM-FWDIS-APPLY
[ "$(pf_count 22000)" -eq 0 ]; rc UFWRM-FWDIS-22000-GONE
$D plan /tmp/firewall-disabled.json --target / $L > /tmp/plan-fwdis-2.txt 2>&1; rc UFWRM-FWDIS-REPLAN
silent /tmp/plan-fwdis-2.txt; rc UFWRM-FWDIS-SILENT

echo "UFWRM-H: final rollback -- fully converged"
$D rollback --target / --yes $L > /tmp/rollback-final.txt 2>&1; rc UFWRM-FINAL-ROLLBACK
$D plan "$C" --target / $L > /tmp/plan-final.txt 2>&1; rc UFWRM-FINAL-PLAN
silent /tmp/plan-final.txt; rc UFWRM-FINAL-SILENT

# ===================== SF-4 (fix round 2): no recorded backend at all ====== #
#
# `_resolved_backend(())`'s shape heuristic cannot classify an EMPTY tuple
# (`actual()` never sees `managed`), so a manifest that predates the
# recorded-backend field entirely (dasik <= 0.18.0), or one from a converged
# apply that never persisted a decision, used to make `actual()` probe ONLY
# firewalld and dispossess a live ufw rule on `sync`. Reproduced by stripping
# `action_state` from the live manifest BY HAND (`/var/lib/dasik/state.json`,
# what `plan`/`sync` actually read via StateStore -- not a
# `generations/<N>/state.json` snapshot, which neither reads) before syncing.

echo "UFWRM-I: SF-4 -- strip the recorded backend from the live manifest"
cp /var/lib/dasik/state.json /tmp/ufwrm-state-backup.json
python - <<'PY'
import json
path = "/var/lib/dasik/state.json"
state = json.load(open(path))
state.pop("action_state", None)
json.dump(state, open(path, "w"), indent=2)
PY
rc UFWRM-SF4-STRIP-ACTION-STATE
python -c "import json,sys; sys.exit(0 if json.load(open('/var/lib/dasik/state.json')).get('action_state', {}) == {} else 1)"; rc UFWRM-SF4-ACTION-STATE-GONE

echo "UFWRM-J: sync (scratch, block ABSENT) must still keep ufw ownership"
cp /tmp/no-firewall.json /tmp/ufwrm-sf4-scratch.json
$D sync /tmp/ufwrm-sf4-scratch.json --target / $L > /tmp/ufwrm-sf4-sync.txt 2>&1; rc UFWRM-SF4-SYNC
cat /tmp/ufwrm-sf4-sync.txt
$D plan /tmp/no-firewall.json --target / $L > /tmp/ufwrm-sf4-plan.txt 2>&1; rc UFWRM-SF4-PLAN
cat /tmp/ufwrm-sf4-plan.txt
present /tmp/ufwrm-sf4-plan.txt 'remove allow 22000/tcp'; rc UFWRM-SF4-REMOVE-PLANNED

echo "UFWRM-K: restore the manifest -- no real apply/rollback needed, nothing on the machine itself changed (only the manifest was hand-edited and re-synced)"
cp /tmp/ufwrm-state-backup.json /var/lib/dasik/state.json
ufw status | grep -q '22000'; rc UFWRM-SF4-22000-STILL-THERE
$D plan "$C" --target / $L > /tmp/ufwrm-sf4-plan-final.txt 2>&1; rc UFWRM-SF4-PLAN-FINAL
silent /tmp/ufwrm-sf4-plan-final.txt; rc UFWRM-SF4-FINAL-SILENT

echo "UFWRM-DONE rc=$FAILS"
sync
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
