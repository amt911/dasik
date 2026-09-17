#!/bin/bash
# `codex mcp add --url` against a server that supports OAuth (Figma), driven on
# an image already converged by config/vm-agent-ui-tooling.json.
#
# The RED run is on record: the install of that config with the code before
# this fix stalled 300 s on
#     Detected OAuth support. Starting OAuth flow…
#     Error: timed out waiting for OAuth callback
# and reported `mcp_servers: test:codex:figma failed` although the registration
# was on disk. This script is the GREEN half: the same registration, removed and
# re-applied, must finish well inside the bound, succeed, say how to log in, be
# owned by the manifest and re-plan to silence.
#
# CONVENTION: every CODEXOAUTH-<step>-RC=0 line is a PASS; CODEXOAUTH-DONE rc=N.
set -x
cd /root/repo || { echo "CODEXOAUTH-DONE rc=91"; poweroff -f; }
export PYTHONPATH=/root/repo

D="python -m dasik"
C=config/vm-agent-ui-tooling.json
L="--no-log"
U=test
H=/home/$U

absent() { ! grep -q "$2" "$1"; }
present() { grep -q "$2" "$1"; }
FAILS=0
rc() { local v=$?; [ "$v" -eq 0 ] || FAILS=$((FAILS + 1)); echo "$1-RC=$v"; }

python -c 'import dasik'; rc CODEXOAUTH-IMPORT
timeout 60 curl -sSf -o /dev/null https://mcp.figma.com/; echo "figma reachable rc=$?"

echo "CODEXOAUTH-A: start from a machine without the codex registration"
su - $U -c 'codex mcp remove figma' 2>&1 | tail -2
absent $H/.codex/config.toml 'mcp_servers.figma'; rc CODEXOAUTH-REMOVED
$D plan "$C" --target / $L > /tmp/plan1.txt 2>&1
grep '\[mcp_servers\]' /tmp/plan1.txt
present /tmp/plan1.txt 'create test:codex:figma'; rc CODEXOAUTH-PLANNED

echo "CODEXOAUTH-B: apply — bounded, successful, and it says how to sign in"
start=$(date +%s)
$D apply "$C" --target / --yes $L > /tmp/apply.txt 2>&1; rc CODEXOAUTH-APPLY
elapsed=$(( $(date +%s) - start ))
echo "CODEXOAUTH-ELAPSED=${elapsed}s"
grep -E 'figma|OAuth' /tmp/apply.txt
[ "$elapsed" -lt 120 ]; rc CODEXOAUTH-BOUNDED
absent /tmp/apply.txt 'test:codex:figma failed'; rc CODEXOAUTH-NOT-REPORTED-FAILED
present /tmp/apply.txt 'codex mcp login figma'; rc CODEXOAUTH-LOGIN-HINT
present $H/.codex/config.toml 'mcp_servers.figma'; rc CODEXOAUTH-REGISTERED

echo "CODEXOAUTH-C: owned, and converged"
present /var/lib/dasik/state.json 'test:codex:figma'; rc CODEXOAUTH-OWNED
$D plan "$C" --target / $L > /tmp/plan2.txt 2>&1
absent /tmp/plan2.txt '\[mcp_servers\]'; rc CODEXOAUTH-REPLAN-QUIET

echo "CODEXOAUTH-DONE rc=$FAILS"
sync
poweroff -f
