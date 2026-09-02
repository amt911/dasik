#!/bin/bash
# Diagnostic probe (not a test): what does `npx skills add -a codex` actually do
# on a machine where codex is not installed? The install left the claude-code
# link in place and the codex one missing, and the plan asks for it forever.
set -x
cd /root/repo || { echo "PROBE-DONE rc=91"; poweroff -f; }
U=test
H=/home/$U

echo "PROBE-A: what is there now"
ls -la $H/.agents/skills/ $H/.claude/skills/ 2>&1
ls -la $H/.codex 2>&1; echo "PROBE-CODEX-HOME-RC=$?"
cat $H/.agents/.skill-lock.json 2>&1

echo "PROBE-B: the exact command dasik runs, by hand"
su - $U -c 'npx -y skills add "$1" --skill "$2" -g -a "$3" -y' -- sh pbakaus/impeccable impeccable codex 2>&1 | tail -30
echo "PROBE-ADD-RC=$?"
ls -la $H/.codex/skills/ 2>&1; echo "PROBE-AFTER-RC=$?"

echo "PROBE-C: same, with the agent home present"
su - $U -c 'mkdir -p ~/.codex'
su - $U -c 'npx -y skills add "$1" --skill "$2" -g -a "$3" -y' -- sh pbakaus/impeccable impeccable codex 2>&1 | tail -30
echo "PROBE-ADD2-RC=$?"
ls -la $H/.codex/skills/ 2>&1; echo "PROBE-AFTER2-RC=$?"

echo "PROBE-D: what `skills list` says"
su - $U -c 'npx -y skills list -g' 2>&1 | tail -20

echo "PROBE-DONE"
sync
poweroff -f
