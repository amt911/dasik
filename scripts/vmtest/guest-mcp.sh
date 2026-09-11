#!/bin/bash
# `mcp_servers` driven INSIDE the booted guest, against the LIVE host (--target /).
#
# What only a guest can prove: that `claude mcp add` and `codex mcp add` really
# accept the arguments dasik passes, really run non-interactively as the user,
# really write the registry dasik then reads back — and that the server behind
# the registration actually serves MCP.
#
# The install could not converge this domain (the agents' CLIs are npm packages,
# not pacman ones), so the domain arrives here UNOWNED and the whole cycle runs
# from zero: plan -> apply -> plan -> sync -> check -> plan -> generations ->
# rollback, the MODIFY path, and the block-removed path.
#
# CONVENTION: every MCP-<step>-RC=0 line is a PASS; rc() counts the failures so
# the final MCP-DONE rc=N is the verdict, not 60 lines to read by hand.
#
# Needs NETWORK (SLIRP is enough): npm and uvx both download.
# Ends with MCP-DONE, then powers off.
set -x
cd /root/repo || { echo "MCP-DONE rc=91"; poweroff -f; }

if command -v dasik > /dev/null 2>&1; then D="dasik"; else D="python -m dasik"; fi
C=config/vm-mcp.json
L="--no-log"                  # the 9p repo is read-only; the log defaults to $PWD
U=test
H=/home/$U

absent() { ! grep -q "$2" "$1"; }     # rc 0 when the pattern is NOT in the file
present() { grep -q "$2" "$1"; }      # rc 0 when it is

FAILS=0
rc() { local v=$?; [ "$v" -eq 0 ] || FAILS=$((FAILS + 1)); echo "$1-RC=$v"; }
echo "MCP: BEGIN (target / = the live booted host)"
echo "MCP-DRIVER: $D  CONFIG: $C"

echo "MCP-A: the guest has what the CLIs need"
command -v node npx uv; rc MCP-NODE
timeout 60 curl -sSf -o /dev/null https://registry.npmjs.org/; rc MCP-NET

echo "MCP-B: install the agent CLIs (npm, not the AUR: a rust build is minutes)"
# npm 11 refuses install scripts by default, and without the postinstall the
# `claude` on PATH is a stub that errors "native binary not installed".
npm install -g --allow-scripts=@anthropic-ai/claude-code,@openai/codex \
    @anthropic-ai/claude-code @openai/codex > /tmp/npm.log 2>&1
rc MCP-NPM; tail -3 /tmp/npm.log
command -v claude codex; rc MCP-CLI

echo "MCP-C: check, then plan — the domain names BOTH agents"
$D check "$C" $L; rc MCP-CHECK
$D plan "$C" --target / $L > /tmp/plan1.txt 2>&1; rc MCP-PLAN
grep '\[mcp_servers\]' /tmp/plan1.txt
present /tmp/plan1.txt 'test:claude-code:inkscape_mcp'; rc MCP-PLAN-CLAUDE
present /tmp/plan1.txt 'test:codex:inkscape_mcp'; rc MCP-PLAN-CODEX

echo "MCP-D: apply — the registration lands in each agent's own file"
$D apply "$C" --target / --yes $L; rc MCP-APPLY
cat $H/.claude.json | head -40
cat $H/.codex/config.toml
present $H/.claude.json 'inkscape_mcp'; rc MCP-CLAUDE-REGISTERED
present $H/.codex/config.toml 'mcp_servers.inkscape_mcp'; rc MCP-CODEX-REGISTERED
# USER scope, not the per-directory `local` one: `claude mcp add` defaults to
# local, and a registration there belongs to whatever directory su was in.
python - <<'PY'
import json, sys
data = json.load(open("/home/test/.claude.json"))
print(json.dumps(data.get("mcpServers"), indent=2))
sys.exit(0 if "inkscape_mcp" in (data.get("mcpServers") or {}) else 1)
PY
rc MCP-CLAUDE-USER-SCOPE
su - $U -c 'codex mcp list' 2>&1 | head -10
su - $U -c 'claude mcp list' 2>&1 | head -10

echo "MCP-E: plan again — silence (idempotency, with the real CLIs)"
$D plan "$C" --target / $L > /tmp/plan2.txt 2>&1; rc MCP-REPLAN
grep '\[mcp_servers\]' /tmp/plan2.txt
absent /tmp/plan2.txt '\[mcp_servers\]'; rc MCP-REPLAN-QUIET

echo "MCP-F: sync captures the block, check accepts it, plan is silent"
cp "$C" /tmp/captured.json
$D sync /tmp/captured.json --target / $L; rc MCP-SYNC
python - <<'PY'
import json, sys
block = json.load(open("/tmp/captured.json")).get("mcp_servers")
print(json.dumps(block, indent=2))
entries = (block or {}).get("entries", [])
ok = any(e["name"] == "inkscape_mcp"
         and e.get("command") == "uvx"
         and e.get("args") == ["inkscape_mcp"]
         and sorted(e.get("agents", [])) == ["claude-code", "codex"]
         for e in entries)
sys.exit(0 if ok else 1)
PY
rc MCP-SYNC-BLOCK
$D check /tmp/captured.json $L; rc MCP-CHECKSYNC
$D plan /tmp/captured.json --target / $L > /tmp/plan3.txt 2>&1; rc MCP-PLANSYNC
absent /tmp/plan3.txt '\[mcp_servers\]'; rc MCP-PLANSYNC-QUIET

echo "MCP-G: the manifest records the domain, and a rollback re-plans to nothing"
$D generations --target / $L | tail -20; rc MCP-GEN
present /var/lib/dasik/state.json 'mcp_servers'; rc MCP-MANIFEST
$D rollback --target / --yes $L > /tmp/rollback.txt 2>&1; rc MCP-ROLLBACK
tail -5 /tmp/rollback.txt
$D plan "$C" --target / $L > /tmp/plan4.txt 2>&1; rc MCP-PLAN-ROLLED
absent /tmp/plan4.txt '\[mcp_servers\]'; rc MCP-PLAN-ROLLED-QUIET

echo "MCP-H: a server nobody declared is left alone"
su - $U -c 'claude mcp add handmade -s user -- true' > /tmp/handmade.txt 2>&1
rc MCP-HANDMADE-ADD; cat /tmp/handmade.txt
$D plan "$C" --target / $L > /tmp/plan5.txt 2>&1
absent /tmp/plan5.txt 'handmade'; rc MCP-FOREIGN-UNTOUCHED

echo "MCP-I: a changed command is a MODIFY, and it converges"
python - <<'PY'
import json
cfg = json.load(open("config/vm-mcp.json"))
cfg["mcp_servers"]["entries"][0]["args"] = ["inkscape_mcp", "--help"]
json.dump(cfg, open("/tmp/modified.json", "w"), indent=2)
PY
$D plan /tmp/modified.json --target / $L > /tmp/plan6.txt 2>&1
grep '\[mcp_servers\]' /tmp/plan6.txt
present /tmp/plan6.txt 'modify'; rc MCP-MODIFY-PLAN
$D apply /tmp/modified.json --target / --yes $L; rc MCP-MODIFY-APPLY
present $H/.codex/config.toml '\-\-help'; rc MCP-MODIFY-WRITTEN
$D plan /tmp/modified.json --target / $L > /tmp/plan7.txt 2>&1
absent /tmp/plan7.txt '\[mcp_servers\]'; rc MCP-MODIFY-REPLAN-QUIET
# …and back, so the rest of the run drives the real command again.
$D apply "$C" --target / --yes $L; rc MCP-MODIFY-BACK

echo "MCP-J: the block removed — what dasik owns goes, what it does not stays"
python - <<'PY'
import json
cfg = json.load(open("config/vm-mcp.json"))
del cfg["mcp_servers"]
json.dump(cfg, open("/tmp/noblock.json", "w"), indent=2)
PY
$D plan /tmp/noblock.json --target / $L > /tmp/plan8.txt 2>&1
grep '\[mcp_servers\]' /tmp/plan8.txt
present /tmp/plan8.txt 'test:codex:inkscape_mcp'; rc MCP-DROPPED-PLAN
absent /tmp/plan8.txt 'handmade'; rc MCP-DROPPED-KEEPS-FOREIGN
$D apply /tmp/noblock.json --target / --yes $L; rc MCP-DROPPED-APPLY
cat $H/.codex/config.toml
absent $H/.codex/config.toml 'mcp_servers.inkscape_mcp'; rc MCP-DROPPED-GONE-CODEX
python - <<'PY'
import json, sys
servers = json.load(open("/home/test/.claude.json")).get("mcpServers") or {}
print(sorted(servers))
sys.exit(0 if "inkscape_mcp" not in servers and "handmade" in servers else 1)
PY
rc MCP-DROPPED-GONE-CLAUDE-FOREIGN-ALIVE
# put it back for the functional probe below
$D apply "$C" --target / --yes $L; rc MCP-REAPPLY

echo "MCP-K: the server itself — does the MCP actually draw anything?"
# Registration is not usefulness. This speaks JSON-RPC to `uvx inkscape_mcp`
# over stdio exactly as an agent would: initialize, tools/list, then one
# tools/call that has to leave an SVG on disk.
su - $U -c 'cd /tmp && uvx inkscape_mcp --help' > /tmp/uvx.txt 2>&1
tail -3 /tmp/uvx.txt
cp scripts/vmtest/mcp_probe.py /tmp/mcp_probe.py
chmod 0755 /tmp/mcp_probe.py
su - $U -c 'cd /tmp && python /tmp/mcp_probe.py' > /tmp/probe.txt 2>&1
rc MCP-PROBE
cat /tmp/probe.txt
test -s /tmp/chorra.svg; rc MCP-SVG-EXISTS
head -3 /tmp/chorra.svg
present /tmp/chorra.svg '<svg'; rc MCP-SVG-IS-SVG

echo "MCP: END with $FAILS failure(s)"
echo "MCP-DONE rc=$FAILS"
sync
poweroff -f
