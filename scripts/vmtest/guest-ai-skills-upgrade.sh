#!/bin/bash
# The day-2 upgrade rehearsal: a machine that was installed with an OLDER dasik
# grows the `ai_skills` and `uv_tools` blocks.
#
# This is the case the author's already-installed machine is in, and it has a
# wrinkle worth pinning: the dasik that READS the config during the first apply
# is the old one, which does not know either block. It says so — `unknown key
# 'ai_skills': dasik ignores it` — installs the new package, and converges
# nothing. Only the SECOND apply, run by the dasik the first one installed,
# converges the domains.
#
# Guest was installed from config/vm-day2-upgrade.json (old pin, no blocks);
# it now applies config/vm-ai-skills-pkg.json (new pin, both blocks).
#
# CONVENTION: every UPGRADE-<step>-RC=0 line is a PASS.
# Ends with UPGRADE-DONE rc=<failures>, then powers off.
set -x
cd /root/repo || { echo "UPGRADE-DONE rc=91"; poweroff -f; }

NEW=config/vm-ai-skills-pkg.json
L="--no-log"
U=test
H=/home/$U

FAILS=0
rc() { local v=$?; [ "$v" -eq 0 ] || FAILS=$((FAILS + 1)); echo "$1-RC=$v"; }
absent() { ! grep -q "$2" "$1"; }
present() { grep -q "$2" "$1"; }

echo "UPGRADE-A: the machine as it is today — an older dasik, no blocks"
dasik --version | tee /tmp/before.txt
present /tmp/before.txt "0.13"; rc UPGRADE-OLD-VERSION
test ! -d $H/.local/share/uv/tools/graphifyy; rc UPGRADE-NO-TOOL-YET
test ! -e $H/.agents/skills/impeccable; rc UPGRADE-NO-SKILL-YET

echo "UPGRADE-B: check the NEW config with the OLD dasik — it must say so, not crash"
dasik check "$NEW" $L > /tmp/oldcheck.txt 2>&1; rc UPGRADE-OLD-CHECK
cat /tmp/oldcheck.txt
present /tmp/oldcheck.txt "unknown key 'ai_skills'"; rc UPGRADE-OLD-WARNS-SKILLS
present /tmp/oldcheck.txt "unknown key 'uv_tools'"; rc UPGRADE-OLD-WARNS-UVTOOLS

echo "UPGRADE-C: first apply — replaces dasik itself, converges no new domain"
dasik apply "$NEW" --target / --yes $L > /tmp/apply1.txt 2>&1; rc UPGRADE-APPLY1
tail -25 /tmp/apply1.txt
dasik --version | tee /tmp/after1.txt
present /tmp/after1.txt "0.14.0"; rc UPGRADE-NEW-VERSION

echo "UPGRADE-D: the new dasik now SEES the blocks — no unknown-key warning"
dasik check "$NEW" $L > /tmp/newcheck.txt 2>&1; rc UPGRADE-NEW-CHECK
absent /tmp/newcheck.txt "unknown key"; rc UPGRADE-NO-UNKNOWN-KEYS
dasik plan "$NEW" --target / $L > /tmp/plan1.txt 2>&1; rc UPGRADE-PLAN
grep -E '\[ai_skills\]|\[uv_tools\]' /tmp/plan1.txt
present /tmp/plan1.txt '\[uv_tools\]'; rc UPGRADE-PLAN-SEES-UVTOOLS
present /tmp/plan1.txt '\[ai_skills\]'; rc UPGRADE-PLAN-SEES-SKILLS

echo "UPGRADE-E: second apply — this is the one that converges them"
dasik apply "$NEW" --target / --yes $L > /tmp/apply2.txt 2>&1; rc UPGRADE-APPLY2
tail -20 /tmp/apply2.txt
test -d $H/.local/share/uv/tools/graphifyy; rc UPGRADE-TOOL-INSTALLED
test -f $H/.claude/skills/graphify/SKILL.md; rc UPGRADE-GRAPHIFY-CLAUDE
test -f $H/.codex/skills/graphify/SKILL.md; rc UPGRADE-GRAPHIFY-CODEX
test -f $H/.agents/skills/impeccable/SKILL.md; rc UPGRADE-IMPECCABLE
test -e $H/.claude/skills/impeccable; rc UPGRADE-IMPECCABLE-CLAUDE

echo "UPGRADE-F: and a third apply changes nothing"
dasik plan "$NEW" --target / $L > /tmp/plan2.txt 2>&1; rc UPGRADE-REPLAN
grep -E '\[ai_skills\]|\[uv_tools\]' /tmp/plan2.txt
absent /tmp/plan2.txt '\[ai_skills\]'; rc UPGRADE-REPLAN-QUIET-SKILLS
absent /tmp/plan2.txt '\[uv_tools\]'; rc UPGRADE-REPLAN-QUIET-UVTOOLS

echo "UPGRADE-G: sync on the upgraded machine round-trips"
cp "$NEW" /tmp/captured.json
dasik sync /tmp/captured.json --target / $L; rc UPGRADE-SYNC
python - <<'PY'
import json, sys
cfg = json.load(open("/tmp/captured.json"))
print(json.dumps({k: cfg.get(k) for k in ("ai_skills", "uv_tools")}, indent=2))
ok = "graphifyy" in ((cfg.get("uv_tools") or {}).get("tools") or []) \
    and any(e["name"] == "graphify" for e in (cfg.get("ai_skills") or {}).get("entries", []))
sys.exit(0 if ok else 1)
PY
rc UPGRADE-SYNC-BLOCKS
dasik check /tmp/captured.json $L; rc UPGRADE-SYNC-CHECK
dasik plan /tmp/captured.json --target / $L > /tmp/plan3.txt 2>&1
absent /tmp/plan3.txt '\[ai_skills\]'; rc UPGRADE-SYNC-PLAN-QUIET

echo "UPGRADE-DONE rc=$FAILS"
sync
poweroff -f
