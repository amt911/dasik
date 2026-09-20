#!/bin/bash
# A codex plugin that is installed while the registry says nothing.
#
# WHAT THIS SCRIPT CAN AND CANNOT PROVE, because the first version of it got
# that wrong and the guest said so.
#
# The bug is the REMOTE catalog: codex keeps those installs account-side and
# writes nothing into ~/.codex/config.toml, so a reader that trusts the file
# alone never sees them — apply installs the plugin and the next plan proposes
# it again, for ever (reported from a real machine after twenty applies;
# measured on the tower as seven plugins `installed, enabled` against one
# registry entry). Signing in to that catalog is impossible in a guest, so THAT
# condition is not reproducible here and is measured on a real machine instead.
#
# Emptying the registry does NOT simulate it: for a git marketplace, codex's own
# idea of "installed" comes from that very file, so the CLI then answers
# `not installed` and planning the create again is correct. Measured in this
# guest, 2026-09-20. What the emptying DOES exercise is the opposite direction —
# the CLI says not installed, so dasik must ask for it — and the hidden-binary
# step exercises the fallback: with no codex, the registry is the only evidence.
#
# Ends with CXP-DONE, then powers off.
set -x
cd /root/repo || { echo "CXP-DONE rc=91"; poweroff -f; }

D="python -m dasik"
L="--no-log"
C=config/vm-uv-python.json
TOML=/home/test/.codex/config.toml
echo "CXP: BEGIN (target / = the live booted host)"

echo "CXP-A: the codex CLI (npm, not the AUR rust build)"
npm install -g @openai/codex >/dev/null 2>&1; echo "CXP-NPM-RC=$?"
su - test -c 'codex --version'; echo "CXP-CODEX-RC=$?"

echo "CXP-B: check, then plan — nothing is installed yet"
$D check "$C" $L; echo "CXP-CHECK-RC=$?"
$D plan "$C" --target / $L; echo "CXP-PLAN-RC=$?"

echo "CXP-C: apply, and see what each store says afterwards"
$D apply "$C" --target / --yes $L; echo "CXP-APPLY-RC=$?"
su - test -c 'codex plugin list' | grep -a "21st@21st-dev"
grep -a -A2 '^\[plugins' "$TOML" || echo "CXP-REGISTRY: empty"
$D plan "$C" --target / $L; echo "CXP-REPLAN-RC=$?"

echo "CXP-D: registry emptied — codex then says `not installed`, so plan must ask"
python - <<'PY'
import re
path = "/home/test/.codex/config.toml"
text = open(path).read()
# Drop every [plugins."..."] section, exactly what a remote-catalog install
# leaves behind: nothing.
text = re.sub(r'\[plugins\."[^"]+"\]\nenabled = \w+\n+', "", text)
open(path, "w").write(text)
print("CXP-REGISTRY-EMPTIED: ok")
PY
grep -ac '^\[plugins' "$TOML"
su - test -c 'codex plugin list' | grep -a "21st@21st-dev"
$D plan "$C" --target / $L; echo "CXP-GHOSTPLAN-RC=$?"

echo "CXP-E: the fallback branch — with codex unreachable the registry decides"
CODEX_BIN="$(command -v codex)"
mv "$CODEX_BIN" /root/codex.hidden
$D plan "$C" --target / $L; echo "CXP-NOCLI-RC=$?"
mv /root/codex.hidden "$CODEX_BIN"
su - test -c 'codex --version'; echo "CXP-CLI-BACK-RC=$?"

echo "CXP-F: generations and rollback"
$D generations --target / $L
$D rollback 1 --target / --yes $L; echo "CXP-ROLLBACK-RC=$?"
$D plan "$C" --target / $L; echo "CXP-POSTROLLBACK-RC=$?"

echo "CXP-G: sync — the entry has to come back, and the capture re-plan to nothing"
cp config/vm-uv-python.json /root/cfg.json
$D sync /root/cfg.json --target / $L; echo "CXP-SYNC-RC=$?"
python - <<'PY'
import json
cfg = json.load(open("/root/cfg.json"))
entries = (cfg.get("ai_skills") or {}).get("entries") or []
print("CXP-CAPTURED:", json.dumps([e.get("name") for e in entries]))
PY
$D check /root/cfg.json $L; echo "CXP-CAPCHECK-RC=$?"
$D plan /root/cfg.json --target / $L; echo "CXP-CAPPLAN-RC=$?"

echo "CXP-H: the block removed — what dasik owns has to be REMOVED"
python - <<'PY'
import json
cfg = json.load(open("/root/cfg.json"))
cfg["ai_skills"] = {"entries": []}
json.dump(cfg, open("/root/dropped.json", "w"), indent=2)
PY
$D plan /root/dropped.json --target / $L; echo "CXP-DROP-RC=$?"

echo "CXP-DONE rc=0"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
