#!/bin/bash
# "snapper y firewall deben eliminar su config si desaparece" -- driven
# end-to-end against the LIVE installed guest (target /), for all three
# disappearance forms (whole block gone, `enable: false`) across both
# domains: snapper (destructive: `delete-config` also drops its snapshots)
# and firewall (firewalld backend: the zone file goes).
#
# Round trips exercised: plan shows the destructive REMOVE -> apply --yes
# converges it -> re-plan is silent -> rollback restores the declaration ->
# plan is silent again. Also: apply WITHOUT --yes over a non-tty stdin must
# refuse (the destructive-change confirmation gate), and a final
# sync -> check -> plan must be silent on the converged machine.
#
# CONVENTION: every SFRM-<step>-RC=<rc> line is a pass (rc=0) or fail; rc()
# counts failures so SFRM-DONE rc=<fails> is the verdict, not lines to read by
# hand. Ends with SFRM-DONE, then powers off.
set -x
export PYTHONPATH=/root/repo
cd /root/repo || { echo "SFRM-DONE rc=91"; poweroff -f; }

D="python -m dasik"
C=config/vm-snapper-firewall-removal.json
L="--no-log"

FAILS=0
rc() { local v=$?; [ "$v" -eq 0 ] || FAILS=$((FAILS + 1)); echo "$1-RC=$v"; }
present() { grep -q "$2" "$1"; }
silent()  { grep -q "No changes" "$1"; }

echo "SFRM: BEGIN (target / = the live booted host)"

echo "SFRM-A: baseline plan is silent (fresh install already converged)"
$D plan "$C" --target / $L > /tmp/plan-baseline.txt 2>&1; rc SFRM-BASELINE-PLAN
cat /tmp/plan-baseline.txt
silent /tmp/plan-baseline.txt; rc SFRM-BASELINE-SILENT

# ===================== N4 (fix round 1): firewalld reload while ENABLED === #
#
# A MODIFY, not a REMOVE: every removal scenario in this script ALSO
# undeclares the `firewalld` package (the same toggle that installs it
# gates on `enable`), so PackagesAction uninstalls the binary in the SAME
# apply -- proving nothing reliable about "is firewall-cmd on disk right
# after" (racy: `systemctl is-active` can still read a stale "active" from
# a not-yet-exited process either way). A MODIFY keeps `enable: true`, so
# `firewalld` stays installed throughout, and it is the clean, non-racy way
# to prove the RUNNING daemon actually reflects a rewritten zone file
# without a manual restart.

echo "SFRM-N4-A: build a MODIFY-only config (samba dropped, block stays enabled)"
python - <<'PY'
import json
cfg = json.load(open("config/vm-snapper-firewall-removal.json"))
cfg["firewall"]["allowed_services"] = []
json.dump(cfg, open("/tmp/n4-modify.json", "w"), indent=2)
PY
rc SFRM-N4-BUILD-CONFIG

echo "SFRM-N4-B: baseline -- samba currently allowed live"
firewall-cmd --zone=public --list-services > /tmp/n4-before.txt 2>&1; rc SFRM-N4-BEFORE-LIST
cat /tmp/n4-before.txt
present /tmp/n4-before.txt 'samba'; rc SFRM-N4-SAMBA-ALLOWED-BEFORE

echo "SFRM-N4-C: apply the MODIFY -- firewalld package stays installed"
$D plan /tmp/n4-modify.json --target / $L > /tmp/n4-plan.txt 2>&1; rc SFRM-N4-PLAN
cat /tmp/n4-plan.txt
present /tmp/n4-plan.txt 'modify public'; rc SFRM-N4-MODIFY-PLANNED
$D apply /tmp/n4-modify.json --target / --yes $L > /tmp/n4-apply.txt 2>&1; rc SFRM-N4-APPLY
cat /tmp/n4-apply.txt
pacman -Qq firewalld; rc SFRM-N4-FIREWALLD-STILL-INSTALLED

echo "SFRM-N4-D: the running daemon reflects the rewritten zone LIVE (reloaded)"
firewall-cmd --zone=public --list-services > /tmp/n4-after.txt 2>&1; rc SFRM-N4-AFTER-LIST
cat /tmp/n4-after.txt
! present /tmp/n4-after.txt 'samba'; rc SFRM-N4-SAMBA-GONE-LIVE
present /tmp/n4-after.txt 'ssh'; rc SFRM-N4-DEFAULT-SSH
present /tmp/n4-after.txt 'dhcpv6-client'; rc SFRM-N4-DEFAULT-DHCPV6

echo "SFRM-N4-E: rollback -- samba restored, fully converged again"
$D rollback --target / --yes $L > /tmp/n4-rollback.txt 2>&1; rc SFRM-N4-ROLLBACK
cat /tmp/n4-rollback.txt
$D plan "$C" --target / $L > /tmp/n4-plan-after-rollback.txt 2>&1; rc SFRM-N4-PLAN-AFTER-ROLLBACK
cat /tmp/n4-plan-after-rollback.txt
silent /tmp/n4-plan-after-rollback.txt; rc SFRM-N4-SILENT

echo "SFRM-B: create two snapshots on root (something for delete-config to destroy)"
snapper -c root create --description sfrm-1; rc SFRM-SNAP1
snapper -c root create --description sfrm-2; rc SFRM-SNAP2
snapper -c root list

echo "SFRM-C: build the block-dropped / disabled configs"
python - <<'PY'
import json
cfg = json.load(open("config/vm-snapper-firewall-removal.json"))

def dump(c, path):
    json.dump(c, open(path, "w"), indent=2)

no_snapper = dict(cfg); no_snapper.pop("snapper", None)
dump(no_snapper, "/tmp/no-snapper.json")

snapper_disabled = dict(cfg); snapper_disabled["snapper"] = {"enable": False}
dump(snapper_disabled, "/tmp/snapper-disabled.json")

no_firewall = dict(cfg); no_firewall.pop("firewall", None)
dump(no_firewall, "/tmp/no-firewall.json")

firewall_disabled = dict(cfg); firewall_disabled["firewall"] = {"enable": False}
dump(firewall_disabled, "/tmp/firewall-disabled.json")
PY
rc SFRM-BUILD-CONFIGS

# ============================= SNAPPER ==================================== #

echo "SFRM-D: snapper block DROPPED -- plan shows the REMOVE of root"
$D plan /tmp/no-snapper.json --target / $L > /tmp/plan-no-snapper.txt 2>&1; rc SFRM-NOSNAP-PLAN
cat /tmp/plan-no-snapper.txt
present /tmp/plan-no-snapper.txt 'remove root'; rc SFRM-NOSNAP-REMOVE-PLANNED

echo "SFRM-D2: apply WITHOUT --yes over a non-tty refuses (destructive gate)"
$D apply /tmp/no-snapper.json --target / $L < /dev/null > /tmp/apply-no-yes.txt 2>&1
noyes_rc=$?
cat /tmp/apply-no-yes.txt
[ "$noyes_rc" -ne 0 ]; rc SFRM-NOYES-REFUSED
[ -e /etc/snapper/configs/root ]; rc SFRM-NOYES-CONFIG-STILL-THERE

echo "SFRM-E: apply --yes -- config+snapshots gone, .snapshots still mounted & usable"
$D apply /tmp/no-snapper.json --target / --yes $L > /tmp/apply-no-snapper.txt 2>&1; rc SFRM-NOSNAP-APPLY
cat /tmp/apply-no-snapper.txt
[ ! -e /etc/snapper/configs/root ]; rc SFRM-CONFIG-GONE
! grep -q '^root$\|"root"' /etc/conf.d/snapper; rc SFRM-CONFD-CLEARED
mountpoint -q /.snapshots; rc SFRM-SNAPSHOTS-STILL-MOUNTED
( touch /.snapshots/sfrm-probe && rm -f /.snapshots/sfrm-probe ); rc SFRM-SNAPSHOTS-USABLE
# N-8: the "config genuinely gone" check moved to the S1 section below --
# dropping the WHOLE `snapper` block here also undeclares the package
# (PackagesAction uninstalls `snapper`/`snap-pac` in the SAME apply), so a
# `--no-dbus list` failing here measures a MISSING BINARY
# (`/usr/bin/snapper: No such file or directory`), not a missing config.
! btrfs subvolume list / | grep -q '\.snapshots/[0-9]*/snapshot'; rc SFRM-NO-SNAPSHOT-SUBVOL-LEFT

echo "SFRM-F: re-plan is silent (converged)"
$D plan /tmp/no-snapper.json --target / $L > /tmp/plan-after-remove.txt 2>&1; rc SFRM-NOSNAP-REPLAN
cat /tmp/plan-after-remove.txt
silent /tmp/plan-after-remove.txt; rc SFRM-NOSNAP-REPLAN-SILENT

echo "SFRM-G: rollback -- config restored, plan silent again"
$D generations --target / $L
$D rollback --target / --yes $L > /tmp/rollback-snapper.txt 2>&1; rc SFRM-ROLLBACK
cat /tmp/rollback-snapper.txt
[ -e /etc/snapper/configs/root ]; rc SFRM-ROLLBACK-CONFIG-BACK
$D plan "$C" --target / $L > /tmp/plan-after-rollback.txt 2>&1; rc SFRM-PLAN-AFTER-ROLLBACK
cat /tmp/plan-after-rollback.txt
silent /tmp/plan-after-rollback.txt; rc SFRM-ROLLBACK-SILENT

echo "SFRM-H: enable:false form also removes"
$D plan /tmp/snapper-disabled.json --target / $L > /tmp/plan-snapdis-before.txt 2>&1; rc SFRM-SNAPDIS-PLAN
present /tmp/plan-snapdis-before.txt 'remove root'; rc SFRM-SNAPDIS-REMOVE-PLANNED
$D apply /tmp/snapper-disabled.json --target / --yes $L > /tmp/apply-snapper-disabled.txt 2>&1; rc SFRM-SNAPDIS-APPLY
[ ! -e /etc/snapper/configs/root ]; rc SFRM-SNAPDIS-CONFIG-GONE
$D plan /tmp/snapper-disabled.json --target / $L > /tmp/plan-snapdis-after.txt 2>&1; rc SFRM-SNAPDIS-REPLAN
silent /tmp/plan-snapdis-after.txt; rc SFRM-SNAPDIS-SILENT

echo "SFRM-I: rollback restores snapper before moving to firewall"
$D rollback --target / --yes $L > /tmp/rollback-snapper2.txt 2>&1; rc SFRM-ROLLBACK2
[ -e /etc/snapper/configs/root ]; rc SFRM-ROLLBACK2-CONFIG-BACK
$D plan "$C" --target / $L > /tmp/plan-mid.txt 2>&1; rc SFRM-PLAN-MID
silent /tmp/plan-mid.txt; rc SFRM-PLAN-MID-SILENT

# ============================= FIREWALL ==================================== #

echo "SFRM-J: firewall block DROPPED -- plan shows the REMOVE of public"
$D plan /tmp/no-firewall.json --target / $L > /tmp/plan-no-fw.txt 2>&1; rc SFRM-NOFW-PLAN
cat /tmp/plan-no-fw.txt
present /tmp/plan-no-fw.txt 'remove public'; rc SFRM-NOFW-REMOVE-PLANNED

echo "SFRM-K: apply --yes -- zone file gone, plan silent"
$D apply /tmp/no-firewall.json --target / --yes $L > /tmp/apply-no-fw.txt 2>&1; rc SFRM-NOFW-APPLY
cat /tmp/apply-no-fw.txt
[ ! -e /etc/firewalld/zones/public.xml ]; rc SFRM-ZONE-GONE
$D plan /tmp/no-firewall.json --target / $L > /tmp/plan-after-fw-remove.txt 2>&1; rc SFRM-NOFW-REPLAN
cat /tmp/plan-after-fw-remove.txt
silent /tmp/plan-after-fw-remove.txt; rc SFRM-NOFW-REPLAN-SILENT

echo "SFRM-L: rollback firewall -- zone restored, plan silent"
$D rollback --target / --yes $L > /tmp/rollback-fw.txt 2>&1; rc SFRM-FW-ROLLBACK
cat /tmp/rollback-fw.txt
[ -e /etc/firewalld/zones/public.xml ]; rc SFRM-FW-ROLLBACK-BACK
# SF-3: the FILE being restored is not the same thing as the RUNNING daemon
# enforcing it. Registry order is Firewall < Packages < Systemd, so this
# rollback writes the zone + attempts a reload BEFORE Packages reinstalls
# `firewalld` (the previous drop-block apply uninstalled it) and Systemd only
# `enable`s (never `--now`/starts) the unit -- measure the DAEMON directly,
# not just the plan/file, which cannot tell "restored on disk" apart from
# "the daemon nobody told".
firewall-cmd --state; rc SFRM-FW-ROLLBACK-DAEMON-RUNNING
firewall-cmd --zone=public --list-services > /tmp/fw-rollback-live.txt 2>&1; rc SFRM-FW-ROLLBACK-DAEMON-LIST
cat /tmp/fw-rollback-live.txt
present /tmp/fw-rollback-live.txt 'samba'; rc SFRM-FW-ROLLBACK-DAEMON-HAS-SAMBA
$D plan "$C" --target / $L > /tmp/plan-after-fw-rollback.txt 2>&1; rc SFRM-PLAN-AFTER-FW-ROLLBACK
silent /tmp/plan-after-fw-rollback.txt; rc SFRM-FW-ROLLBACK-SILENT

echo "SFRM-M: firewall enable:false form also removes"
$D plan /tmp/firewall-disabled.json --target / $L > /tmp/plan-fwdis-before.txt 2>&1; rc SFRM-FWDIS-PLAN
present /tmp/plan-fwdis-before.txt 'remove public'; rc SFRM-FWDIS-REMOVE-PLANNED
$D apply /tmp/firewall-disabled.json --target / --yes $L > /tmp/apply-fw-disabled.txt 2>&1; rc SFRM-FWDIS-APPLY
[ ! -e /etc/firewalld/zones/public.xml ]; rc SFRM-FWDIS-ZONE-GONE
$D plan /tmp/firewall-disabled.json --target / $L > /tmp/plan-fwdis-after.txt 2>&1; rc SFRM-FWDIS-REPLAN
silent /tmp/plan-fwdis-after.txt; rc SFRM-FWDIS-SILENT

echo "SFRM-N: final rollback -- full config back, machine fully converged"
$D rollback --target / --yes $L > /tmp/rollback-final.txt 2>&1; rc SFRM-FINAL-ROLLBACK
[ -e /etc/firewalld/zones/public.xml ]; rc SFRM-FINAL-ZONE-BACK
$D plan "$C" --target / $L > /tmp/plan-final.txt 2>&1; rc SFRM-FINAL-PLAN
cat /tmp/plan-final.txt
silent /tmp/plan-final.txt; rc SFRM-FINAL-SILENT

# ===================== S2 (fix round 1): sync must not dispossess ========= #
#
# `sync` computes `managed <- actual ∩ (claimable ∪ declared)`. Before S2,
# `actual()` returned `set()` whenever the block was disabled/absent, so a
# `sync` run BEFORE the next apply wiped ownership and the FOLLOWING plan/
# apply against the SAME still-disabled/absent config saw nothing to remove.
#
# `sync` is run against a SCRATCH copy (it rewrites its argument in place),
# but the FOLLOWING plan/apply deliberately target the ORIGINAL, UNTOUCHED
# disabled/absent config file (`snapper-disabled.json` / `no-firewall.json`),
# never the just-synced scratch copy: `sync` reports REALITY, and the
# config/zone still genuinely exists on the machine at this point in the
# script, so syncing the scratch copy re-populates the very block the test
# means to keep disabled/absent -- proving nothing about S2. What S2 tests
# is the REAL, machine-side manifest the `sync` call persists via
# StateStore (independent of which config path was handed to it): the next
# `plan`/`apply` reads that SAME persisted manifest through a DIFFERENT
# (untouched) config path, which is exactly the real-world shape of the bug
# (a `sync` run for any unrelated reason, before the next `apply`).

echo "SFRM-S2-A: snapper enable:false -- sync (scratch) must keep ownership"
cp /tmp/snapper-disabled.json /tmp/s2-snapper-scratch.json
$D sync /tmp/s2-snapper-scratch.json --target / $L > /tmp/s2-snapper-sync.txt 2>&1; rc SFRM-S2-SNAP-SYNC
cat /tmp/s2-snapper-sync.txt
$D plan /tmp/snapper-disabled.json --target / $L > /tmp/s2-snapper-plan.txt 2>&1; rc SFRM-S2-SNAP-PLAN
cat /tmp/s2-snapper-plan.txt
present /tmp/s2-snapper-plan.txt 'remove root'; rc SFRM-S2-SNAP-REMOVE-PLANNED
$D apply /tmp/snapper-disabled.json --target / --yes $L > /tmp/s2-snapper-apply.txt 2>&1; rc SFRM-S2-SNAP-APPLY
cat /tmp/s2-snapper-apply.txt
[ ! -e /etc/snapper/configs/root ]; rc SFRM-S2-SNAP-GONE

echo "SFRM-S2-B: rollback snapper before firewall's S2 pass"
$D rollback --target / --yes $L > /tmp/s2-snapper-rollback.txt 2>&1; rc SFRM-S2-SNAP-ROLLBACK
[ -e /etc/snapper/configs/root ]; rc SFRM-S2-SNAP-ROLLBACK-BACK

echo "SFRM-S2-C: firewall block ABSENT -- sync (scratch) must keep ownership"
cp /tmp/no-firewall.json /tmp/s2-firewall-scratch.json
$D sync /tmp/s2-firewall-scratch.json --target / $L > /tmp/s2-fw-sync.txt 2>&1; rc SFRM-S2-FW-SYNC
cat /tmp/s2-fw-sync.txt
$D plan /tmp/no-firewall.json --target / $L > /tmp/s2-fw-plan.txt 2>&1; rc SFRM-S2-FW-PLAN
cat /tmp/s2-fw-plan.txt
present /tmp/s2-fw-plan.txt 'remove public'; rc SFRM-S2-FW-REMOVE-PLANNED
$D apply /tmp/no-firewall.json --target / --yes $L > /tmp/s2-fw-apply.txt 2>&1; rc SFRM-S2-FW-APPLY
cat /tmp/s2-fw-apply.txt
[ ! -e /etc/firewalld/zones/public.xml ]; rc SFRM-S2-FW-GONE

echo "SFRM-S2-D: rollback firewall -- fully converged again (same state SFRM-O expects)"
$D rollback --target / --yes $L > /tmp/s2-fw-rollback.txt 2>&1; rc SFRM-S2-FW-ROLLBACK
[ -e /etc/firewalld/zones/public.xml ]; rc SFRM-S2-FW-ROLLBACK-BACK
$D plan "$C" --target / $L > /tmp/s2-plan-check.txt 2>&1; rc SFRM-S2-PLAN-CHECK
cat /tmp/s2-plan-check.txt
silent /tmp/s2-plan-check.txt; rc SFRM-S2-SILENT

# ============================= SYNC / CHECK / GENERATIONS ================== #

echo "SFRM-O: sync -> check -> plan silent on the converged machine"
cp "$C" /tmp/sync-target.json
$D sync /tmp/sync-target.json --target / $L > /tmp/sync-out.txt 2>&1; rc SFRM-SYNC
cat /tmp/sync-out.txt
$D check /tmp/sync-target.json $L; rc SFRM-SYNC-CHECK
$D plan /tmp/sync-target.json --target / $L > /tmp/plan-after-sync.txt 2>&1; rc SFRM-SYNC-PLAN
cat /tmp/plan-after-sync.txt
silent /tmp/plan-after-sync.txt; rc SFRM-SYNC-PLAN-SILENT

echo "SFRM-P: generations recorded the snapper/firewall domains"
$D generations --target / $L > /tmp/generations.txt 2>&1; rc SFRM-GENERATIONS
cat /tmp/generations.txt

# ===================== B1 (fix round 1, BLOCKER) =========================== #
#
# A REMOVE of a config whose SUBVOLUME= cannot be read must refuse before any
# destructive call -- never guess "/" and delete a DIFFERENT, still-declared
# config's snapshots. Machine state after this section is NOT restored to
# silence (nothing runs after S1 below except SFRM-DONE/poweroff).

echo "SFRM-B1-A: add a second snapper config (home on @home)"
python - <<'PY'
import json
cfg = json.load(open("config/vm-snapper-firewall-removal.json"))

two = dict(cfg)
two["snapper"] = {"enable": True, "configs": [
    {"name": "root", "subvolume": "/"},
    {"name": "home", "subvolume": "/home"},
]}
json.dump(two, open("/tmp/two-snapper-configs.json", "w"), indent=2)

drop_home = dict(cfg)
drop_home["snapper"] = {"enable": True, "configs": [{"name": "root", "subvolume": "/"}]}
json.dump(drop_home, open("/tmp/drop-home.json", "w"), indent=2)

drop_root_keep_home = dict(cfg)
drop_root_keep_home["snapper"] = {"enable": True, "configs": [{"name": "home", "subvolume": "/home"}]}
json.dump(drop_root_keep_home, open("/tmp/drop-root-keep-home.json", "w"), indent=2)
PY
rc SFRM-B1-BUILD-CONFIGS

$D apply /tmp/two-snapper-configs.json --target / --yes $L > /tmp/b1-add-home.txt 2>&1; rc SFRM-B1-ADD-HOME
cat /tmp/b1-add-home.txt
[ -e /etc/snapper/configs/home ]; rc SFRM-B1-HOME-CREATED

echo "SFRM-B1-B: fresh snapshots on root -- must survive the botched home removal"
snapper -c root create --description sfrm-b1-1; rc SFRM-B1-SNAP1
snapper -c root create --description sfrm-b1-2; rc SFRM-B1-SNAP2
btrfs subvolume list / > /tmp/b1-before-subvols.txt; rc SFRM-B1-SUBVOL-LIST-BEFORE
cat /tmp/b1-before-subvols.txt

echo "SFRM-B1-C: corrupt home's SUBVOLUME= line by hand"
sed -i '/^SUBVOLUME=/d' /etc/snapper/configs/home; rc SFRM-B1-CORRUPT
! grep -q '^SUBVOLUME=' /etc/snapper/configs/home; rc SFRM-B1-CORRUPT-VERIFIED

echo "SFRM-B1-D: drop home from the declaration -- apply --yes must REFUSE, not guess"
$D plan /tmp/drop-home.json --target / $L > /tmp/b1-plan.txt 2>&1; rc SFRM-B1-PLAN
cat /tmp/b1-plan.txt
present /tmp/b1-plan.txt 'remove home'; rc SFRM-B1-REMOVE-PLANNED
$D apply /tmp/drop-home.json --target / --yes $L > /tmp/b1-apply.txt 2>&1
b1_apply_rc=$?
cat /tmp/b1-apply.txt
[ "$b1_apply_rc" -ne 0 ]; rc SFRM-B1-APPLY-REFUSED
present /tmp/b1-apply.txt 'SUBVOLUME'; rc SFRM-B1-REFUSAL-MENTIONS-SUBVOLUME

echo "SFRM-B1-E: root's snapshots are UNTOUCHED by the botched home removal"
btrfs subvolume list / > /tmp/b1-after-subvols.txt; rc SFRM-B1-SUBVOL-LIST-AFTER
cat /tmp/b1-after-subvols.txt
diff /tmp/b1-before-subvols.txt /tmp/b1-after-subvols.txt; rc SFRM-B1-ROOT-SNAPSHOTS-UNTOUCHED
[ -e /etc/snapper/configs/root ]; rc SFRM-B1-ROOT-CONFIG-STILL-THERE
[ -e /etc/snapper/configs/home ]; rc SFRM-B1-HOME-CONFIG-STILL-THERE

# ===================== S1 (fix round 1): retry-safe after interruption ===== #
#
# A numbered `<N>/` whose `snapshot` subvolume is already gone (simulating a
# run killed between the btrfs delete and its bookkeeping cleanup) must not
# get the REMOVE stuck forever -- it converges, cleaning up the leftover.

echo "SFRM-S1-A: delete one snapshot subvolume by hand, leaving a stale bookkeeping dir"
N=$(ls /.snapshots | grep -E '^[0-9]+$' | sort -n | tail -1)
echo "SFRM-S1-TARGET-N=$N"
btrfs subvolume delete "/.snapshots/$N/snapshot"; rc SFRM-S1-MANUAL-DELETE
[ ! -e "/.snapshots/$N/snapshot" ]; rc SFRM-S1-SNAPSHOT-GONE
[ -e "/.snapshots/$N/info.xml" ]; rc SFRM-S1-LEFTOVER-STILL-THERE

echo "SFRM-S1-B: removing root now must converge despite the stale leftover"
$D plan /tmp/drop-root-keep-home.json --target / $L > /tmp/s1-plan.txt 2>&1; rc SFRM-S1-PLAN
cat /tmp/s1-plan.txt
present /tmp/s1-plan.txt 'remove root'; rc SFRM-S1-REMOVE-PLANNED
$D apply /tmp/drop-root-keep-home.json --target / --yes $L > /tmp/s1-apply.txt 2>&1; rc SFRM-S1-APPLY-CONVERGES
cat /tmp/s1-apply.txt
[ ! -e /etc/snapper/configs/root ]; rc SFRM-S1-ROOT-CONFIG-GONE
[ ! -e "/.snapshots/$N" ]; rc SFRM-S1-LEFTOVER-CLEANED
pacman -Qq snapper; rc SFRM-S1-SNAPPER-STILL-INSTALLED
# N-8 (moved from the SFRM-E section above): `home` is STILL declared here,
# so `snapper`/`snap-pac` stay installed throughout -- this genuinely
# measures a missing CONFIG, not a missing binary. `--no-dbus`, never plain
# `snapper list`: a plain (D-Bus) call can return STALE data for a config
# just deleted out-of-band -- MEASURED (FACT-SFRM-4) -- because the daemon,
# once activated, caches its own view. `--no-dbus` reads straight from disk,
# where root's config file is genuinely gone (host measurement, same FACT:
# `snapper --no-dbus -c <missing> list` -> "Config '<name>' not found." rc=1,
# snapper 0.13.1).
! snapper --no-dbus -c root list > /tmp/s1-snapper-list-after.txt 2>&1; rc SFRM-S1-SNAPPER-LIST-IMPOSSIBLE
cat /tmp/s1-snapper-list-after.txt

echo "SFRM-S1-C: re-plan is silent"
$D plan /tmp/drop-root-keep-home.json --target / $L > /tmp/s1-replan.txt 2>&1; rc SFRM-S1-REPLAN
cat /tmp/s1-replan.txt
silent /tmp/s1-replan.txt; rc SFRM-S1-REPLAN-SILENT

echo "SFRM-DONE rc=$FAILS"
sync
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
