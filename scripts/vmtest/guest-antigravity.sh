#!/bin/bash
# Antigravity as a third agent, driven INSIDE the booted guest against the LIVE
# host (--target /), after an install that already converged it in the chroot.
#
# What only a guest can prove:
#   * `agy mcp add` accepts the argv dasik builds (flags before the name, `--`
#     before a command whose args start with `-`) and writes exactly what the
#     reader reads back from ~/.gemini/config/mcp_config.json;
#   * `npx skills add -a antigravity antigravity-cli` lands only in the shared
#     ~/.agents/skills, never under ~/.gemini/antigravity*/skills;
#   * `agy plugin install` from a throwaway clone installs superpowers and 21st
#     with zero manual steps, and the clone does not survive;
#   * a changed server, a server switched off by hand and a plugin whose files
#     were lost are all SEEN by plan and put back by apply;
#   * every verb round-trips: plan -> apply -> plan, sync -> check -> plan,
#     generations, the blocks removed -> DELETE, rollback -> back and silent.
#
# CONVENTION: every AGY-<step>-RC=0 line is a PASS; rc() counts the failures,
# so AGY-DONE rc=N is the verdict.
#
# Needs NETWORK (SLIRP is enough). Ends with AGY-DONE, then powers off.
set -x
cd /root/repo || { echo "AGY-DONE rc=91"; poweroff -f; }
export PYTHONPATH=/root/repo

D="python -m dasik"
C=config/vm-antigravity.json
L="--no-log"                  # the 9p repo is read-only; the log defaults to $PWD
U=test
H=/home/$U
MCP=$H/.gemini/config/mcp_config.json
PLUGINS=$H/.gemini/config/plugins

absent() { ! grep -q "$2" "$1"; }
present() { grep -q "$2" "$1"; }

FAILS=0
rc() { local v=$?; [ "$v" -eq 0 ] || FAILS=$((FAILS + 1)); echo "$1-RC=$v"; }

# ai_skills/mcp_servers lines left in a plan. The codex marketplace plugins are
# not part of this config, so nothing is exempt: any line is a failure.
leftovers() { grep -E '\[(ai_skills|mcp_servers)\]' "$1"; }
quiet() { ! leftovers "$1"; }

# Written by hand, not derived from the config under test.
cat > /tmp/expect_agy.py <<'PY'
import json, sys
path = sys.argv[1]
want = {
    "shadcn": {"command": "npx", "args": ["shadcn@latest", "mcp"]},
    "chrome-devtools": {"command": "npx", "args": [
        "-y", "chrome-devtools-mcp@latest", "--executablePath",
        "/usr/bin/chromium", "--no-usage-statistics"]},
    "figma": {"serverUrl": "https://mcp.figma.com/mcp"},
}
try:
    servers = json.load(open(path)).get("mcpServers") or {}
except (OSError, ValueError):
    servers = {}
bad = [(n, servers.get(n)) for n, spec in want.items()
       if not servers.get(n) or servers[n].get("disabled") is True
       or any(servers[n].get(k) != v for k, v in spec.items())]
print("antigravity servers:", sorted(servers), "bad:", bad)
sys.exit(1 if bad else 0)
PY

echo "AGY: BEGIN (target / = the live booted host)"
python -c 'import dasik'; rc AGY-IMPORT
command -v agy npx git claude codex; rc AGY-TOOLS
agy --help > /dev/null 2>&1; rc AGY-RUNS

echo "AGY-A: what the install put there — zero manual steps"
python /tmp/expect_agy.py $MCP; rc AGY-MCP-REGISTERED
cat $MCP
su - $U -c 'agy mcp list'
for n in frontend-design android-cli; do
    test -f "$H/.agents/skills/$n/SKILL.md"; rc "AGY-SKILL-CANONICAL-$n"
done
test ! -e $H/.gemini/antigravity/skills -a ! -e $H/.gemini/antigravity-cli/skills
rc AGY-NO-GEMINI-SKILL-DIRS
su - $U -c 'agy plugin list' > /tmp/plugins.json 2>&1; cat /tmp/plugins.json
for p in superpowers 21st; do
    present /tmp/plugins.json "\"name\": \"$p\""; rc "AGY-PLUGIN-LISTED-$p"
    test -d "$PLUGINS/$p"; rc "AGY-PLUGIN-PAYLOAD-$p"
done
# the throwaway clone must be gone (mktemp -d under /tmp, clone at <dir>/plugin)
! ls -d /tmp/tmp.*/plugin 2>/dev/null; rc AGY-NO-CLONE-LEFT
absent $MCP 'magic'; rc AGY-21ST-KEY-NEVER-IN-MCP-CONFIG

echo "AGY-B: check, plan, apply, plan — converged and silent"
$D check "$C" $L; rc AGY-CHECK
$D plan "$C" --target / $L > /tmp/plan1.txt 2>&1; rc AGY-PLAN
cat /tmp/plan1.txt
quiet /tmp/plan1.txt; rc AGY-PLAN-QUIET
$D apply "$C" --target / --yes $L > /tmp/apply1.txt 2>&1; rc AGY-APPLY
$D plan "$C" --target / $L > /tmp/plan2.txt 2>&1
quiet /tmp/plan2.txt; rc AGY-REPLAN-QUIET

echo "AGY-C: drift is seen and repaired"
# 1. a server registered with different arguments -> MODIFY, one re-add
su - $U -c 'agy mcp add shadcn npx -- shadcn@1.0.0 mcp'; rc AGY-C1-DRIFT-MADE
$D plan "$C" --target / $L > /tmp/plan-mod.txt 2>&1
present /tmp/plan-mod.txt 'test:antigravity:shadcn'; rc AGY-C1-MODIFY-PLANNED
# 2. a server switched off by hand -> planned again
su - $U -c 'agy mcp disable figma'; rc AGY-C2-DISABLED
$D plan "$C" --target / $L > /tmp/plan-dis.txt 2>&1
present /tmp/plan-dis.txt 'create test:antigravity:figma'; rc AGY-C2-CREATE-PLANNED
# 3. a plugin whose copied files are gone (a restored $HOME) -> planned again
rm -rf "$PLUGINS/21st"
$D plan "$C" --target / $L > /tmp/plan-ghost.txt 2>&1
present /tmp/plan-ghost.txt 'create test:antigravity:plugin:21st'; rc AGY-C3-GHOST-PLANNED
cat /tmp/plan-mod.txt /tmp/plan-dis.txt /tmp/plan-ghost.txt | grep -E '\[(ai_skills|mcp_servers)\]'
$D apply "$C" --target / --yes $L > /tmp/apply-drift.txt 2>&1; rc AGY-C-APPLY
tail -20 /tmp/apply-drift.txt
python /tmp/expect_agy.py $MCP; rc AGY-C-SERVERS-REPAIRED
test -d "$PLUGINS/21st"; rc AGY-C3-PAYLOAD-BACK
$D plan "$C" --target / $L > /tmp/plan-after-drift.txt 2>&1
quiet /tmp/plan-after-drift.txt; rc AGY-C-REPLAN-QUIET

echo "AGY-D: sync captures the agent, check accepts it, plan is silent"
cp "$C" /tmp/captured.json
$D sync /tmp/captured.json --target / $L; rc AGY-SYNC
cat > /tmp/expect_capture.py <<'PY'
import json, sys
cfg = json.load(open("/tmp/captured.json"))
entries = cfg["ai_skills"]["entries"]
skills = {e["name"]: sorted(e.get("agents", [])) for e in entries if e["method"] == "skills"}
plugins = sorted((e["name"], e["source"]) for e in entries if e["method"] == "antigravity-plugin")
servers = {e["name"]: sorted(e["agents"]) for e in cfg["mcp_servers"]["entries"]}
four = sorted(["claude-code", "codex", "antigravity", "antigravity-cli"])
three = sorted(["claude-code", "codex", "antigravity"])
print("skills", skills, "plugins", plugins, "servers", servers)
ok = (skills == {"frontend-design": four, "android-cli": four}
      and plugins == [("21st", "21st-dev/magic-mcp"), ("superpowers", "obra/superpowers")]
      and servers == {"shadcn": three, "chrome-devtools": three, "figma": three})
sys.exit(0 if ok else 1)
PY
python /tmp/expect_capture.py; rc AGY-SYNC-BLOCKS
$D check /tmp/captured.json $L; rc AGY-CHECKSYNC
$D plan /tmp/captured.json --target / $L > /tmp/plan3.txt 2>&1; rc AGY-PLANSYNC
quiet /tmp/plan3.txt; rc AGY-PLANSYNC-QUIET

echo "AGY-E: the manifest records the antigravity items"
$D generations --target / $L | tail -10; rc AGY-GEN
present /var/lib/dasik/state.json 'test:antigravity:plugin:superpowers'; rc AGY-MANIFEST-PLUGIN
present /var/lib/dasik/state.json 'test:antigravity:figma'; rc AGY-MANIFEST-MCP
present /var/lib/dasik/state.json 'test:antigravity-cli:skill:frontend-design'; rc AGY-MANIFEST-SKILL

echo "AGY-F: the blocks removed — what dasik owns goes"
python - <<'PY'
import json
cfg = json.load(open("config/vm-antigravity.json"))
del cfg["ai_skills"], cfg["mcp_servers"]
json.dump(cfg, open("/tmp/noblock.json", "w"), indent=2)
PY
$D plan /tmp/noblock.json --target / $L > /tmp/plan4.txt 2>&1; rc AGY-DROPPED-PLAN
grep -E '\[(ai_skills|mcp_servers)\]' /tmp/plan4.txt
present /tmp/plan4.txt 'delete test:antigravity:plugin:superpowers'; rc AGY-DROPPED-PLANS-PLUGIN
present /tmp/plan4.txt 'delete test:antigravity:chrome-devtools'; rc AGY-DROPPED-PLANS-MCP
present /tmp/plan4.txt 'delete test:antigravity:skill:android-cli'; rc AGY-DROPPED-PLANS-SKILL
$D apply /tmp/noblock.json --target / --yes $L > /tmp/apply2.txt 2>&1; rc AGY-DROPPED-APPLY
tail -20 /tmp/apply2.txt
! python /tmp/expect_agy.py $MCP > /dev/null 2>&1; rc AGY-DROPPED-MCP-GONE
python - "$MCP" <<'PY'; rc AGY-DROPPED-NO-SERVER-LEFT
import json, sys
try:
    servers = json.load(open(sys.argv[1])).get("mcpServers") or {}
except (OSError, ValueError):
    servers = {}
print("left:", sorted(servers))
sys.exit(1 if servers else 0)
PY
test ! -e "$PLUGINS/superpowers" -a ! -e "$PLUGINS/21st"; rc AGY-DROPPED-PLUGINS-GONE
test ! -e "$H/.agents/skills/android-cli"; rc AGY-DROPPED-SKILL-GONE
$D plan /tmp/noblock.json --target / $L > /tmp/plan5.txt 2>&1
quiet /tmp/plan5.txt; rc AGY-DROPPED-REPLAN-QUIET

echo "AGY-G: rollback to the generation with the blocks — back, and silent"
$D rollback --target / --yes $L > /tmp/rollback.txt 2>&1; rc AGY-ROLLBACK
tail -20 /tmp/rollback.txt
python /tmp/expect_agy.py $MCP; rc AGY-ROLLED-MCP
test -d "$PLUGINS/superpowers" -a -d "$PLUGINS/21st"; rc AGY-ROLLED-PLUGINS
test -f "$H/.agents/skills/android-cli/SKILL.md"; rc AGY-ROLLED-SKILL
$D plan "$C" --target / $L > /tmp/plan6.txt 2>&1; rc AGY-PLAN-ROLLED
quiet /tmp/plan6.txt; rc AGY-PLAN-ROLLED-QUIET

echo "AGY-DONE rc=$FAILS"
sync
poweroff -f
