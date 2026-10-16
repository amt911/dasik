#!/bin/bash
# `dasik upgrade` driven INSIDE the booted guest, against the LIVE host (--target /),
# through every verb: check/plan/apply/sync/generations/rollback around the upgrade.
#
# Why this is a guest and not a unit test: the verb's whole value is what the REAL
# CLIs do when asked to update — `uv tool install --upgrade` over a receipt pinned
# by an older install, `npx skills update` against a bogus lock hash, `claude plugin
# update`, `codex plugin add` over an existing plugin — plus real versions and real
# network. A monkeypatched Command.execute can only say we ran the argv we wrote.
#
# Guest was installed from config/vm-ai-upgrade.json (uv_tools graphifyy + ruff +
# black==24.1.0, the impeccable skill, the graphify tool skill). This script adds
# the claude/codex CLIs (npm), a claude plugin, a codex plugin and an mcp server.
#
# CONVENTION: every AIUP-<step>-RC=0 line is a PASS; rc() counts the failures so
# the final AIUP-DONE rc=N is the verdict. Every version comparison prints both
# values first. Needs NETWORK. Ends with AIUP-DONE, then powers off.
set -x
cd /root/repo || { echo "AIUP-DONE rc=91"; poweroff -f; }

if command -v dasik > /dev/null 2>&1; then D="dasik"; else D="python -m dasik"; fi
export PYTHONPATH=/root/repo
BASE=config/vm-ai-upgrade.json
FULL=/tmp/full.json
L="--no-log"                  # the 9p repo is read-only; the log defaults to $PWD
U=test
H=/home/$U
GPATH='PATH="$HOME/.local/bin:$PATH"'

absent() { ! grep -q "$2" "$1"; }
present() { grep -q "$2" "$1"; }
FAILS=0
rc() { local v=$?; [ "$v" -eq 0 ] || FAILS=$((FAILS + 1)); echo "$1-RC=$v"; }
# tool version as uv reports it, e.g. `graphifyy 0.4.1` -> 0.4.1
uvver() { su - $U -c "uv tool list" 2>/dev/null | awk -v t="$1" '$1==t{sub(/^v/,"",$2); print $2}'; }
# rc 0 when $1 < $2 by version order
older() { [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$1" ]; }
dgens() { $D generations --target / $L 2>&1 | grep -c '^Generation'; }

echo "AIUP: BEGIN"
echo "AIUP-DRIVER: $D"
timeout 60 curl -sSf -o /dev/null https://registry.npmjs.org/; rc AIUP-NET
npm install -g --allow-scripts=@anthropic-ai/claude-code,@openai/codex \
    @anthropic-ai/claude-code @openai/codex > /tmp/npm.log 2>&1
rc AIUP-NPM; tail -3 /tmp/npm.log
su - $U -c 'claude --version; codex --version'; rc AIUP-CLIS

python - <<'PY'
import json
cfg = json.load(open("config/vm-ai-upgrade.json"))
cfg["ai_skills"]["entries"] += [
    {"name": "caveman", "method": "claude-plugin",
     "marketplace": {"name": "caveman", "source": "JuliusBrussee/caveman"}},
    {"name": "superpowers", "method": "codex-plugin",
     "marketplace": {"name": "superpowers-dev",
                     "source": "https://github.com/obra/superpowers"}},
]
cfg["mcp_servers"] = {"users": ["test"], "entries": [
    {"name": "mcp-time", "command": "uvx", "args": ["mcp-server-time"],
     "agents": ["claude-code", "codex"]}]}
json.dump(cfg, open("/tmp/full.json", "w"), indent=2)
nb = {k: v for k, v in cfg.items() if k not in ("ai_skills", "uv_tools", "mcp_servers")}
json.dump(nb, open("/tmp/noblocks.json", "w"), indent=2)
PY

echo "AIUP-1: install sequence — check, plan, apply, plan silent"
$D check $FULL $L; rc AIUP-CHECK
$D plan $FULL --target / $L > /tmp/plan0.txt 2>&1; rc AIUP-PLAN
grep -E '^\[|ai_skills|mcp' /tmp/plan0.txt | head
$D apply $FULL --target / --yes $L; rc AIUP-APPLY
$D plan $FULL --target / $L > /tmp/plan1.txt 2>&1
cat /tmp/plan1.txt | tail -8
absent /tmp/plan1.txt '\[ai_skills\]'; rc AIUP-REPLAN-SKILLS-SILENT
absent /tmp/plan1.txt '\[uv_tools\]'; rc AIUP-REPLAN-UV-SILENT
absent /tmp/plan1.txt '\[mcp_servers\]'; rc AIUP-REPLAN-MCP-SILENT
GENS_BEFORE=$(dgens); echo "AIUP-GENS-BEFORE=$GENS_BEFORE"

echo "AIUP-2: make items stale on purpose"
# graphifyy: an older release, via a manual install (pins the uv receipt — this is
# exactly the receipt `--upgrade` must get past).
CANDS=$(python - <<'PY'
import json, urllib.request
d = json.load(urllib.request.urlopen("https://pypi.org/pypi/graphifyy/json"))
rel = sorted(((min(f["upload_time"] for f in fs), v) for v, fs in d["releases"].items()
              if fs and not any(c.isalpha() for c in v)))
print(" ".join(v for _, v in rel[:-3][::-1][:6]))
PY
)
echo "AIUP-OLD-CANDIDATES: $CANDS"
NEW_G=$(uvver graphifyy); echo "AIUP-GRAPHIFY-LATEST=$NEW_G"
OLD_G=""
for v in $CANDS; do
    if su - $U -c "uv tool install graphifyy==$v --force" > /tmp/oldg.log 2>&1; then OLD_G=$v; break; fi
done
[ -n "$OLD_G" ]; rc AIUP-OLD-GRAPHIFY
OLD_G=$(uvver graphifyy); echo "AIUP-GRAPHIFY-OLD=$OLD_G (latest was $NEW_G)"
older "$OLD_G" "$NEW_G"; rc AIUP-OLD-IS-OLDER
su - $U -c "$GPATH; graphify install --platform claude && graphify install --platform codex" > /tmp/oldskill.log 2>&1
rc AIUP-OLD-GRAPHIFY-SKILL
OLD_GS=$(md5sum $H/.claude/skills/graphify/SKILL.md | cut -d' ' -f1); echo "AIUP-GRAPHIFY-SKILL-OLD-MD5=$OLD_GS"
# ruff: unpinned in the declaration, pinned by a manual install.
su - $U -c "uv tool install ruff==0.5.0 --force" > /tmp/oldr.log 2>&1; rc AIUP-OLD-RUFF
OLD_R=$(uvver ruff); echo "AIUP-RUFF-OLD=$OLD_R"
PIN_B=$(uvver black); echo "AIUP-BLACK-PIN-BEFORE=$PIN_B"
# the skill: bogus lock hash + old content
LOCK=$H/.agents/.skill-lock.json
python - <<PY
import json
p = "$LOCK"
d = json.load(open(p)); d["skills"]["impeccable"]["skillFolderHash"] = "bogus0000"
json.dump(d, open(p, "w"), indent=2)
PY
printf -- '---\nname: impeccable\n---\nOLD CONTENT\n' > $H/.agents/skills/impeccable/SKILL.md
chown -R $U:$U $H/.agents
OLD_SM=$(md5sum $H/.agents/skills/impeccable/SKILL.md | cut -d' ' -f1)
# the claude plugin: a bogus recorded commit, and (if the clone has history) an old checkout
CLONE=$H/.claude/plugins/marketplaces/caveman
git -C $CLONE log --oneline 2>/dev/null | wc -l
su - $U -c "git -C ~/.claude/plugins/marketplaces/caveman checkout -q HEAD~2" ; echo "AIUP-CLONE-CHECKOUT-rc=$?"
PLUG_BEFORE=$(python - <<PY
import json
d = json.load(open("$H/.claude/plugins/installed_plugins.json"))
r = d["plugins"]["caveman@caveman"][0]
print(r.get("version"), r.get("gitCommitSha"))
PY
)
echo "AIUP-CAVEMAN-RECORD-BEFORE=$PLUG_BEFORE"
su - $U -c 'codex plugin list' 2>&1 | head -10 > /tmp/codex-before.txt; cat /tmp/codex-before.txt
GENS_PRE=$(dgens)

echo "AIUP-3: dasik upgrade"
$D upgrade $FULL --target / $L > /tmp/upgrade1.txt 2>&1; echo "AIUP-UPGRADE-rc=$?"
cat /tmp/upgrade1.txt
present /tmp/upgrade1.txt 'updated'; rc AIUP-SUMMARY-UPDATED
present /tmp/upgrade1.txt 'mcp_servers: nothing to upgrade'; rc AIUP-MCP-NOTE
absent /tmp/upgrade1.txt 'FAILED'; rc AIUP-NO-FAILED
NOW_G=$(uvver graphifyy); echo "AIUP-GRAPHIFY now=$NOW_G old=$OLD_G"
older "$OLD_G" "$NOW_G"; rc AIUP-UV-MOVED
NOW_R=$(uvver ruff); echo "AIUP-RUFF now=$NOW_R old=$OLD_R"
older "$OLD_R" "$NOW_R"; rc AIUP-RUFF-MOVED
NOW_B=$(uvver black); echo "AIUP-BLACK now=$NOW_B before=$PIN_B"
[ "$NOW_B" = "$PIN_B" ] && [ "$NOW_B" = "24.1.0" ]; rc AIUP-PIN-HELD
present /tmp/upgrade1.txt 'black.*skipped'; rc AIUP-PIN-SKIPPED
NEWHASH=$(python -c "import json;print(json.load(open('$LOCK'))['skills']['impeccable']['skillFolderHash'])")
NEW_SM=$(md5sum $H/.agents/skills/impeccable/SKILL.md | cut -d' ' -f1)
echo "AIUP-SKILL hash now=$NEWHASH (was bogus0000) md5 now=$NEW_SM old=$OLD_SM"
[ "$NEWHASH" != "bogus0000" ] && [ "$NEW_SM" != "$OLD_SM" ]; rc AIUP-SKILL-MOVED
su - $U -c "$GPATH; graphify install --platform claude" > /tmp/gi.log 2>&1
cp $H/.claude/skills/graphify/SKILL.md /tmp/gs-after-upgrade.md
su - $U -c "$GPATH; graphify install --platform claude" > /tmp/gi2.log 2>&1
NEW_GS=$(md5sum $H/.claude/skills/graphify/SKILL.md | cut -d' ' -f1)
echo "AIUP-GRAPHIFY-SKILL md5 old=$OLD_GS new=$NEW_GS"
cmp /tmp/gs-after-upgrade.md $H/.claude/skills/graphify/SKILL.md; rc AIUP-GRAPHIFY-SKILL
PLUG_AFTER=$(python - <<PY
import json
d = json.load(open("$H/.claude/plugins/installed_plugins.json"))
r = d["plugins"]["caveman@caveman"][0]
print(r.get("version"), r.get("gitCommitSha"))
PY
)
echo "AIUP-CAVEMAN-RECORD-AFTER=$PLUG_AFTER (before: $PLUG_BEFORE)"
su - $U -c 'codex plugin list' 2>&1 | head -10 > /tmp/codex-after.txt; cat /tmp/codex-after.txt
diff /tmp/codex-before.txt /tmp/codex-after.txt; echo "AIUP-CODEX-LISTDIFF-rc=$? (measurement only)"
grep -E 'caveman|superpowers|impeccable|graphify|ruff|black' /tmp/upgrade1.txt

echo "AIUP-4: round trips"
$D plan $FULL --target / $L > /tmp/plan2.txt 2>&1
absent /tmp/plan2.txt '\[ai_skills\]'; rc AIUP-PLAN-SILENT-SKILLS
absent /tmp/plan2.txt '\[uv_tools\]'; rc AIUP-PLAN-SILENT-UV
cp $FULL /tmp/s.json
$D sync /tmp/s.json --target / $L; rc AIUP-SYNC
$D check /tmp/s.json $L; rc AIUP-CHECK-SYNCED
$D plan /tmp/s.json --target / $L > /tmp/plan3.txt 2>&1
absent /tmp/plan3.txt '\[ai_skills\]'; rc AIUP-PLAN-SYNCED-SKILLS
absent /tmp/plan3.txt '\[uv_tools\]'; rc AIUP-PLAN-SYNCED-UV
GENS_AFTER=$(dgens); echo "AIUP-GENS before-upgrade=$GENS_PRE after=$GENS_AFTER"
[ "$GENS_PRE" = "$GENS_AFTER" ]; rc AIUP-GENERATIONS-UNCHANGED
$D rollback --target / --yes $L; rc AIUP-ROLLBACK
$D apply $FULL --target / --yes $L; rc AIUP-REAPPLY
$D plan $FULL --target / $L > /tmp/plan4.txt 2>&1
absent /tmp/plan4.txt '\[ai_skills\]'; rc AIUP-REPLAN-SILENT-2
absent /tmp/plan4.txt '\[uv_tools\]'; rc AIUP-REPLAN-SILENT-3

echo "AIUP-5: blocks removed -> Nothing to upgrade."
$D upgrade /tmp/noblocks.json --target / $L > /tmp/upgrade2.txt 2>&1; rc AIUP-NOBLOCK-RC
cat /tmp/upgrade2.txt
present /tmp/upgrade2.txt 'Nothing to upgrade.'; rc AIUP-NOTHING

echo "AIUP-6: offline"
IFACE=$(ip -o link | awk -F': ' '$2!="lo"{print $2; exit}')
echo "AIUP-IFACE=$IFACE"
ip link set "$IFACE" down
timeout 15 curl -sSf -o /dev/null https://pypi.org/ ; [ $? -ne 0 ]; rc AIUP-OFFLINE-REALLY
$D upgrade $FULL --target / $L > /tmp/upgrade3.txt 2>&1; UP3=$?
cat /tmp/upgrade3.txt
echo "AIUP-OFFLINE-UPGRADE-rc=$UP3"
[ "$UP3" -eq 1 ]; rc AIUP-OFFLINE-RC1
present /tmp/upgrade3.txt 'graphifyy: FAILED'; rc AIUP-OFFLINE-UV-FAILED
present /tmp/upgrade3.txt 'ruff: FAILED'; rc AIUP-OFFLINE-RUFF-FAILED
# MEASURED: `npx skills update` swallows the network error and exits 0, so the
# verb can only report the skill as unchanged ("already latest"), never FAILED.
# What must hold is that offline it never claims to have moved anything.
absent /tmp/upgrade3.txt 'impeccable: updated'; rc AIUP-OFFLINE-SKILL-NOT-UPDATED
grep 'impeccable' /tmp/upgrade3.txt
ip link set "$IFACE" up
systemctl restart systemd-networkd
curl -sSf --retry 30 --retry-connrefused --retry-delay 1 -o /dev/null https://pypi.org/; rc AIUP-ONLINE-AGAIN
$D plan $FULL --target / $L > /tmp/plan5.txt 2>&1
absent /tmp/plan5.txt '\[ai_skills\]'; rc AIUP-POSTOFFLINE-PLAN-SKILLS
absent /tmp/plan5.txt '\[uv_tools\]'; rc AIUP-POSTOFFLINE-PLAN-UV
test -d $H/.local/share/uv/tools/graphifyy; rc AIUP-TOOL-SURVIVED

echo "AIUP-DONE rc=$FAILS"
sync
poweroff -f
