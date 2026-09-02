#!/bin/bash
# Diagnostic probe (not a test): why does `codex plugin add` fail in a guest?
set -x
cd /root/repo || { echo "PROBE-DONE rc=91"; poweroff -f; }
U=test

echo "PROBE-A: does codex run at all"
su - $U -c 'codex --version' 2>&1 | tail -3; echo "PROBE-VERSION-RC=$?"

echo "PROBE-B: what marketplaces does it see"
su - $U -c 'codex plugin marketplace list' 2>&1 | tail -20; echo "PROBE-MKT-RC=$?"

echo "PROBE-C: what plugins are on offer"
su - $U -c 'codex plugin list' 2>&1 | tail -20; echo "PROBE-LIST-RC=$?"

echo "PROBE-D: the exact command dasik runs"
su - $U -c 'codex plugin add "$1"' -- sh superpowers@openai-curated 2>&1 | tail -20
echo "PROBE-ADD-RC=$?"

echo "PROBE-E: with the marketplace added explicitly"
su - $U -c 'codex plugin marketplace add "$1"' -- sh https://github.com/obra/superpowers 2>&1 | tail -20
echo "PROBE-MKTADD-RC=$?"
su - $U -c 'codex plugin add "$1"' -- sh superpowers@superpowers 2>&1 | tail -20
echo "PROBE-ADD2-RC=$?"
cat /home/$U/.codex/config.toml 2>&1 | head -20

echo "PROBE-DONE"
sync
poweroff -f
