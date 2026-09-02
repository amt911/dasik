#!/bin/bash
# Diagnostic probe for `ai_skills` — the MEASUREMENTS behind three fixes.
#
# Not a pass/fail test: it prints what the real installers do, which is the only
# way to know. Each of these was a bug dasik shipped and the unit suite could
# not see, because the suite mocks Command.execute and therefore encodes the
# guess instead of testing it.
#
#   1. `npx skills add -a codex` writes ONLY ~/.agents/skills/<n> — codex is a
#      "universal agent" and gets no directory of its own. Reading
#      ~/.codex/skills meant the domain never converged.
#   2. `codex plugin marketplace add` writes [marketplaces.<name>], not
#      [plugin_marketplaces.<name>].
#   3. It records the URL it cloned, WITH the .git the config did not write, so
#      comparing sources as strings made an eternal MODIFY.
#
# Run it against a guest that already has node and network.
set -x
cd /root/repo || { echo "PROBE-DONE rc=91"; poweroff -f; }
U=test
H=/home/$U

echo "PROBE-A: where does a skill land for a universal agent"
su - $U -c 'npx -y skills add "$1" --skill "$2" -g -a "$3" -y' \
    -- sh pbakaus/impeccable impeccable codex 2>&1 | tail -20
ls -la $H/.agents/skills/ 2>&1
ls -la $H/.codex/skills/ 2>&1; echo "PROBE-CODEX-DIR-RC=$?   # 2 = no directory of its own"
su - $U -c 'npx -y skills list -g' 2>&1 | tail -10

echo "PROBE-B: what codex knows about marketplaces on a fresh machine"
su - $U -c 'codex plugin marketplace list' 2>&1 | tail -10
su - $U -c 'codex plugin add "$1"' -- sh superpowers@openai-curated 2>&1 | tail -5

echo "PROBE-C: what `codex plugin marketplace add` writes, verbatim"
su - $U -c 'codex plugin marketplace add "$1"' \
    -- sh https://github.com/obra/superpowers 2>&1 | tail -5
su - $U -c 'codex plugin add "$1"' -- sh superpowers@superpowers-dev 2>&1 | tail -5
cat $H/.codex/config.toml 2>&1

echo "PROBE-DONE"
sync
poweroff -f
