#!/bin/bash
# Reconciler block-removal fix (Reconciler._any_managed_for), driven for real.
#
# The old probe (`cls.__new__(cls)`, no `__init__` run) raised AttributeError
# for most actions' managed_keys(), was swallowed by a broad `except`, and read
# as "owns nothing" — so `build_plan` skipped the action outright whenever its
# config block was absent. Dropping `timezone`/`locales`/`pacman` after they
# were applied should plan silently (their own managed_keys() already handles
# an undeclared config correctly) AND release the manifest's ownership of the
# domain — the part the old probe broke by never even visiting the action.
# Dropping `systemd` should plan the documented DISABLE for a unit the
# manifest still owns but nothing declares any more — a destructive change
# the old probe skipped outright.
#
# Also exercises the validation guard from fix/model-url-control-chars in the
# same guest: a newline embedded in package_sources/config_saver/mcp_servers
# url fields must be rejected by `dasik check`.
#
# CONVENTION: every BLOCKRM-<step>-RC=0 line is a PASS; rc() counts the
# failures so the final BLOCKRM-DONE rc=N is the verdict, not 40 lines to read
# by hand. Ends with BLOCKRM-DONE, then powers off.
set -x
export PYTHONPATH=/root/repo
cd /root/repo || { echo "BLOCKRM-DONE rc=91"; poweroff -f; }

if command -v dasik > /dev/null 2>&1; then D="dasik"; else D="python -m dasik"; fi
C=config/vm-block-removal.json
L="--no-log"

absent() { ! grep -q "$2" "$1"; }     # rc 0 when the pattern is NOT in the file
present() { grep -q "$2" "$1"; }      # rc 0 when it is

FAILS=0
rc() { local v=$?; [ "$v" -eq 0 ] || FAILS=$((FAILS + 1)); echo "$1-RC=$v"; }

echo "BLOCKRM: BEGIN (target / = the live booted host)"
echo "BLOCKRM-DRIVER: $D  CONFIG: $C"

echo "BLOCKRM-A: plan with the full config is silent for the four domains"
$D plan "$C" --target / $L > /tmp/plan-full.txt 2>&1; rc BLOCKRM-PLAN-FULL
cat /tmp/plan-full.txt
absent /tmp/plan-full.txt '\[timezone\]'; rc BLOCKRM-FULL-TZ-QUIET
absent /tmp/plan-full.txt '\[locales\]'; rc BLOCKRM-FULL-LOC-QUIET
absent /tmp/plan-full.txt '\[pacman\]'; rc BLOCKRM-FULL-PACMAN-QUIET
absent /tmp/plan-full.txt '\[systemd\]'; rc BLOCKRM-FULL-SYSTEMD-QUIET

echo "BLOCKRM-B: build the four block-absent configs, written to /tmp"
# Four PER-DOMAIN configs (one block dropped each) to prove `plan` visibility
# in isolation, PLUS one COMBINED config (all four dropped together) for the
# apply/manifest-release check below. Dropping only ONE already-inert block
# (timezone/locales/pacman alone) converges the WHOLE machine, so the
# aggregate Plan is entirely empty and `Reconciler.apply()` short-circuits on
# `plan.is_empty()` BEFORE ever building/persisting a new manifest — by
# design (no diff, no new generation), not a bug this fix is about. Dropping
# `systemd` too forces a real DISABLE into the same apply, so a manifest
# actually gets written, and that ONE manifest is where all four domains'
# released ownership is observable at once.
python - <<'PY'
import json
cfg = json.load(open("config/vm-block-removal.json"))

def drop(keys, out):
    c = dict(cfg)
    for key in keys:
        c.pop(key, None)
    json.dump(c, open(out, "w"), indent=2)

drop(["timezone"], "/tmp/no-timezone.json")
drop(["locales"], "/tmp/no-locales.json")
drop(["pacman"], "/tmp/no-pacman.json")
drop(["systemd"], "/tmp/no-systemd.json")
drop(["timezone", "locales", "pacman", "systemd"], "/tmp/no-blocks-combined.json")
PY
rc BLOCKRM-BUILD-CONFIGS

echo "BLOCKRM-C: timezone dropped alone -- plan silent"
$D plan /tmp/no-timezone.json --target / $L > /tmp/plan-no-tz.txt 2>&1; rc BLOCKRM-TZ-PLAN
cat /tmp/plan-no-tz.txt
absent /tmp/plan-no-tz.txt '\[timezone\]'; rc BLOCKRM-TZ-PLAN-QUIET

echo "BLOCKRM-D: locales dropped alone -- plan silent"
$D plan /tmp/no-locales.json --target / $L > /tmp/plan-no-loc.txt 2>&1; rc BLOCKRM-LOC-PLAN
absent /tmp/plan-no-loc.txt '\[locales\]'; rc BLOCKRM-LOC-PLAN-QUIET

echo "BLOCKRM-E: pacman dropped alone -- plan silent"
$D plan /tmp/no-pacman.json --target / $L > /tmp/plan-no-pm.txt 2>&1; rc BLOCKRM-PM-PLAN
absent /tmp/plan-no-pm.txt '\[pacman\]'; rc BLOCKRM-PM-PLAN-QUIET

echo "BLOCKRM-F: systemd dropped alone -- plan proposes the DISABLE"
$D plan /tmp/no-systemd.json --target / $L > /tmp/plan-no-sd.txt 2>&1; rc BLOCKRM-SD-PLAN
cat /tmp/plan-no-sd.txt
present /tmp/plan-no-sd.txt 'disable systemd-timesyncd.service'; rc BLOCKRM-SD-DISABLE-PLANNED

echo "BLOCKRM-G: all four dropped together -- apply, files unchanged, unit disabled, ownership released"
cp /etc/localtime /tmp/localtime-before
cp /etc/locale.gen /tmp/locale.gen-before
cp /etc/pacman.conf /tmp/pacman.conf-before
$D apply /tmp/no-blocks-combined.json --target / --yes $L > /tmp/apply-combined.txt 2>&1; rc BLOCKRM-COMBINED-APPLY
cat /tmp/apply-combined.txt
readlink -f /etc/localtime | grep -q 'Europe/Madrid'; rc BLOCKRM-TZ-UNCHANGED
diff /tmp/locale.gen-before /etc/locale.gen; rc BLOCKRM-LOC-UNCHANGED
diff /tmp/pacman.conf-before /etc/pacman.conf; rc BLOCKRM-PM-UNCHANGED
[ "$(systemctl is-enabled systemd-timesyncd.service 2>/dev/null)" != "enabled" ]; rc BLOCKRM-SD-DISABLED
python - <<'PY'
import json, sys
m = json.load(open("/var/lib/dasik/state.json"))
managed = m.get("managed", {})
released = {k: managed.get(k) for k in ("timezone", "locales", "pacman", "systemd")}
print("BLOCKRM-COMBINED-MANAGED:", json.dumps(released))
# timezone/locales/pacman have nothing else deriving into them in this config,
# so their whole managed list must go to empty. `systemd` is different: the
# `network`/`bootloader` expand toggles legitimately still own OTHER units
# (systemd-networkd.service, systemd-boot-update.service, ...) — only
# systemd-timesyncd.service, the one this config declared directly and then
# undeclared, must be gone from it.
ok = (not released["timezone"] and not released["locales"]
      and not released["pacman"]
      and "systemd-timesyncd.service" not in (released["systemd"] or []))
sys.exit(0 if ok else 1)
PY
rc BLOCKRM-COMBINED-RELEASED
$D plan /tmp/no-blocks-combined.json --target / $L > /tmp/plan-combined-2.txt 2>&1; rc BLOCKRM-COMBINED-REPLAN
cat /tmp/plan-combined-2.txt
absent /tmp/plan-combined-2.txt '\[timezone\]'; rc BLOCKRM-COMBINED-REPLAN-TZ-QUIET
absent /tmp/plan-combined-2.txt '\[locales\]'; rc BLOCKRM-COMBINED-REPLAN-LOC-QUIET
absent /tmp/plan-combined-2.txt '\[pacman\]'; rc BLOCKRM-COMBINED-REPLAN-PM-QUIET
absent /tmp/plan-combined-2.txt '\[systemd\]'; rc BLOCKRM-COMBINED-REPLAN-SD-QUIET

echo "BLOCKRM-H: rollback to the generation with all blocks -- unit re-enabled, plan silent"
$D generations --target / $L
$D rollback 1 --target / --yes $L; rc BLOCKRM-ROLLBACK
systemctl is-enabled systemd-timesyncd.service; rc BLOCKRM-SD-REENABLED
$D plan "$C" --target / $L > /tmp/plan-after-rollback.txt 2>&1; rc BLOCKRM-PLAN-AFTER-ROLLBACK
cat /tmp/plan-after-rollback.txt
absent /tmp/plan-after-rollback.txt '\[systemd\]'; rc BLOCKRM-ROLLBACK-QUIET

echo "BLOCKRM-I: validation guard (fix/model-url-control-chars) -- a newline in a url is rejected"
python - <<'PY'
import json
base = json.load(open("config/vm-block-removal.json"))

pkgsrc = dict(base)
pkgsrc["package_sources"] = {"config-saver": {
    "type": "pkgbuild-git",
    "url": "https://git.example.org/pkgbuilds/config-saver.git\nSigLevel = Never",
    "ref": "a520605367e13ec25db4c3c7e1c4bf46175ba8cd",
}}
pkgsrc["packages"] = base["packages"] + ["config-saver"]
json.dump(pkgsrc, open("/tmp/bad-package-sources.json", "w"), indent=2)

saver = dict(base)
saver["config_saver"] = {
    "source": {"url": "https://github.com/amt911/config-saver-aur.git\nSigLevel = Never",
               "ref": "a520605367e13ec25db4c3c7e1c4bf46175ba8cd"},
    "configs": {"dotfiles": {"directories": ["$HOME/.config"]}},
}
json.dump(saver, open("/tmp/bad-config-saver.json", "w"), indent=2)

mcp = dict(base)
mcp["mcp_servers"] = {"users": ["test"], "entries": [{
    "name": "evil", "url": "https://example.org/mcp\nHost: evil",
    "agents": ["claude-code"]}]}
json.dump(mcp, open("/tmp/bad-mcp-servers.json", "w"), indent=2)
PY
rc BLOCKRM-BUILD-BAD-CONFIGS

! $D check /tmp/bad-package-sources.json $L; rc BLOCKRM-BAD-PACKAGE-SOURCES-REJECTED
! $D check /tmp/bad-config-saver.json $L; rc BLOCKRM-BAD-CONFIG-SAVER-REJECTED
! $D check /tmp/bad-mcp-servers.json $L; rc BLOCKRM-BAD-MCP-SERVERS-REJECTED
$D check "$C" $L; rc BLOCKRM-FULL-CONFIG-STILL-VALID

echo "BLOCKRM-DONE rc=$FAILS"
sync
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
