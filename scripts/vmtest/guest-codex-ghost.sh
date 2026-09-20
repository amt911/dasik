#!/bin/bash
# A codex marketplace whose cached root is gone, healed by `dasik apply`.
#
# The real machine, 2026-09-20: `~/.codex/.tmp/marketplaces/<name>` is a CACHE.
# Lose it — a restored $HOME, a cleaned tmp — and `codex plugin marketplace
# list` shows nothing while config.toml still carries `[marketplaces.<name>]`.
# dasik then plans a create, and codex refuses it with "already added from a
# different source", for ever, until somebody runs `marketplace remove` by hand.
#
# Mocks cannot prove this: it is codex's own two stores disagreeing. Ends with
# GHOST-DONE, then powers off.
set -x
cd /root/repo || { echo "GHOST-DONE rc=91"; poweroff -f; }

D="python -m dasik"
L="--no-log"
C=config/vm-uv-python.json
echo "GHOST: BEGIN"

echo "GHOST-A: the codex CLI (npm, not the AUR rust build)"
npm install -g @openai/codex >/dev/null 2>&1; echo "GHOST-NPM-RC=$?"
su - test -c 'codex --version'; echo "GHOST-CODEX-RC=$?"

echo "GHOST-B: converge once, so the marketplace and its cache exist"
$D apply "$C" --target / --yes $L; echo "GHOST-APPLY-RC=$?"
su - test -c 'codex plugin marketplace list'
$D plan "$C" --target / $L; echo "GHOST-REPLAN-RC=$?"

echo "GHOST-C: make the ghost — the cache dies, the registration survives"
rm -rf /home/test/.codex/.tmp/marketplaces
grep -a -A2 '^\[marketplaces' /home/test/.codex/config.toml || echo "GHOST-REG: MISSING"
su - test -c 'codex plugin marketplace list'

echo "GHOST-D: this is what used to need hands — apply must heal it"
$D plan "$C" --target / $L; echo "GHOST-GHOSTPLAN-RC=$?"
$D apply "$C" --target / --yes $L; echo "GHOST-HEAL-RC=$?"
su - test -c 'codex plugin marketplace list'
ls -d /home/test/.codex/.tmp/marketplaces/21st-dev && echo "GHOST-CACHE: back"
$D plan "$C" --target / $L; echo "GHOST-AFTERHEAL-RC=$?"

echo "GHOST-E: and a failure now says WHY (stderr is merged into stdout)"
rm -rf /home/test/.codex/.tmp/marketplaces
sed -i 's#^source = .*#source = "https://github.com/21st-dev/magic-mcp.git"#' \
    /home/test/.codex/config.toml
$D apply "$C" --target / --yes $L 2>&1 | tail -5; echo "GHOST-SECOND-HEAL-RC=$?"

echo "GHOST-DONE rc=0"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
