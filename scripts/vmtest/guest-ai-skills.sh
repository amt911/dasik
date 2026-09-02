#!/bin/bash
# `ai_skills` driven INSIDE the booted guest, against the LIVE host (--target /).
#
# This is the day-2 case the domain exists for: a machine that is already
# installed grows the block and converges. The install itself already applied
# one `skills` entry, so the first thing checked here is that a re-plan is
# silent — the idempotency the unit suite can only assert with mocks.
#
# What only a guest can prove: that `npx skills add`, `claude plugin install`
# and `codex plugin add` really accept the arguments dasik passes, really run
# non-interactively as the user, and really leave behind the state that
# `plan`/`sync` read back.
#
# CONVENTION: every AISKILLS-<step>-RC=0 line is a PASS. The helpers below make
# "the plan said nothing about this domain" an rc of 0 like everything else,
# instead of grep's inverted one.
#
# Needs NETWORK (SLIRP is enough) — every installer downloads.
# Ends with AISKILLS-DONE, then powers off.
set -x
cd /root/repo || { echo "AISKILLS-DONE rc=91"; poweroff -f; }

D="python -m dasik"
L="--no-log"                 # the 9p repo is read-only; the log defaults to $PWD
C=config/vm-ai-skills.json
U=test
H=/home/$U

absent() { ! grep -q "$2" "$1"; }     # rc 0 when the pattern is NOT in the file
present() { grep -q "$2" "$1"; }      # rc 0 when it is
echo "AISKILLS: BEGIN (target / = the live booted host)"

echo "AISKILLS-A: the guest has what the installers need"
command -v node npx; echo "AISKILLS-NODE-RC=$?"
timeout 60 curl -sSf -o /dev/null https://registry.npmjs.org/; echo "AISKILLS-NET-RC=$?"

echo "AISKILLS-B: what the install already put there"
# codex/cursor/opencode read ~/.agents/skills themselves (the `skills` CLI calls
# them universal agents and writes no directory of their own); claude-code gets
# a link. Asserting a ~/.codex/skills/<n> here would assert a bug.
test -f $H/.agents/skills/impeccable/SKILL.md; echo "AISKILLS-CANONICAL-RC=$?"
test -e $H/.claude/skills/impeccable; echo "AISKILLS-CLAUDE-LINK-RC=$?"
test ! -e $H/.codex/skills/impeccable; echo "AISKILLS-NO-CODEX-DIR-RC=$?"
head -20 $H/.agents/.skill-lock.json

echo "AISKILLS-C: check / plan (expect: no ai_skills change)"
$D check "$C" $L; echo "AISKILLS-CHECK-RC=$?"
$D plan "$C" --target / $L > /tmp/plan1.txt 2>&1; echo "AISKILLS-PLAN-RC=$?"
cat /tmp/plan1.txt
absent /tmp/plan1.txt '\[ai_skills\]'; echo "AISKILLS-PLAN-QUIET-RC=$?"

echo "AISKILLS-D: apply, then plan again — both silent"
$D apply "$C" --target / --yes $L; echo "AISKILLS-APPLY-RC=$?"
$D plan "$C" --target / $L > /tmp/plan2.txt 2>&1; echo "AISKILLS-REPLAN-RC=$?"
absent /tmp/plan2.txt '\[ai_skills\]'; echo "AISKILLS-REPLAN-QUIET-RC=$?"

echo "AISKILLS-E: sync captures the block, check accepts it, plan is silent"
# `sync` rewrites the config it is given, and the 9p repo is read-only.
cp "$C" /tmp/captured.json
$D sync /tmp/captured.json --target / $L; echo "AISKILLS-SYNC-RC=$?"
python - <<'PY'
import json, sys
block = json.load(open("/tmp/captured.json")).get("ai_skills")
print(json.dumps(block, indent=2))
names = sorted(e["name"] for e in (block or {}).get("entries", []))
print("AISKILLS-SYNC-BLOCK-RC=%d" % (0 if "impeccable" in names else 1))
PY
$D check /tmp/captured.json $L; echo "AISKILLS-CHECKSYNC-RC=$?"
$D plan /tmp/captured.json --target / $L > /tmp/plan3.txt 2>&1; echo "AISKILLS-PLANSYNC-RC=$?"
absent /tmp/plan3.txt '\[ai_skills\]'; echo "AISKILLS-PLANSYNC-QUIET-RC=$?"

echo "AISKILLS-F: the manifest records the domain"
$D generations --target / $L | tail -20; echo "AISKILLS-GEN-RC=$?"
grep -o 'ai_skills' /var/lib/dasik/state.json | head -1
present /var/lib/dasik/state.json 'ai_skills'; echo "AISKILLS-MANIFEST-RC=$?"

echo "AISKILLS-G: a skill nobody declared is left alone"
su - $U -c 'mkdir -p ~/.claude/skills/handmade && printf -- "---\nname: handmade\n---\n" > ~/.claude/skills/handmade/SKILL.md'
$D plan "$C" --target / $L > /tmp/plan4.txt 2>&1
absent /tmp/plan4.txt 'handmade'; echo "AISKILLS-FOREIGN-RC=$?"
test -d $H/.claude/skills/handmade; echo "AISKILLS-FOREIGN-ALIVE-RC=$?"

echo "AISKILLS-H: the plugin methods, with the real CLIs"
# npm 11 refuses install scripts by default, and without the postinstall the
# `claude` on PATH is a stub that errors "native binary not installed" — which
# would test dasik's failure path instead of its plugin path.
npm install -g --allow-scripts=@anthropic-ai/claude-code,@openai/codex \
    @anthropic-ai/claude-code @openai/codex > /tmp/npm.log 2>&1
echo "AISKILLS-NPM-RC=$?"; tail -3 /tmp/npm.log
command -v claude codex
su - $U -c 'claude --version' > /tmp/claude-version.txt 2>&1; echo "AISKILLS-CLI-RC=$?"
cat /tmp/claude-version.txt
python - <<'PY'
import json
cfg = json.load(open("config/vm-ai-skills.json"))
cfg["ai_skills"]["entries"] += [
    {"name": "caveman", "method": "claude-plugin",
     "marketplace": {"name": "caveman", "source": "JuliusBrussee/caveman"}},
    # Not `openai-curated`: that marketplace only exists once codex has
    # populated its own cache, which a freshly installed one has not. A git
    # marketplace is deterministic — and the NAME is the one the marketplace
    # manifest declares (superpowers-dev), not the repository's.
    {"name": "superpowers", "method": "codex-plugin",
     "marketplace": {"name": "superpowers-dev",
                     "source": "https://github.com/obra/superpowers"}},
]
json.dump(cfg, open("/tmp/with-plugins.json", "w"), indent=2)
PY
$D plan /tmp/with-plugins.json --target / $L > /tmp/plan5.txt 2>&1
grep '\[ai_skills\]' /tmp/plan5.txt
present /tmp/plan5.txt 'plugin:caveman@caveman'; echo "AISKILLS-PLUGIN-PLAN-RC=$?"
$D apply /tmp/with-plugins.json --target / --yes $L; echo "AISKILLS-PLUGIN-APPLY-RC=$?"
su - $U -c 'claude plugin list' 2>&1 | head -20
head -30 $H/.claude/plugins/installed_plugins.json
head -30 $H/.codex/config.toml
su - $U -c 'codex plugin list' 2>&1 | head -10
$D plan /tmp/with-plugins.json --target / $L > /tmp/plan6.txt 2>&1
grep '\[ai_skills\]' /tmp/plan6.txt      # names itself when it is not silent
absent /tmp/plan6.txt '\[ai_skills\]'; echo "AISKILLS-PLUGIN-REPLAN-QUIET-RC=$?"
# The last generation that CAN converge: step I deliberately declares a skill
# that does not exist, so rolling back to anything after this point would
# (correctly) keep asking for it forever.
GEN_GOOD=$(basename "$(readlink -f /var/lib/dasik/generations/current)")
echo "AISKILLS-GEN-GOOD=$GEN_GOOD"

echo "AISKILLS-I: an installer that fails must not abort the apply"
python - <<'PY'
import json
cfg = json.load(open("config/vm-ai-skills.json"))
# KEEP the working entry: dropping it here would remove impeccable and leave
# step J with nothing to remove.
cfg["ai_skills"]["entries"].append(
    {"name": "nope", "method": "skills",
     "source": "dasik-test/definitely-not-a-repo", "agents": ["codex"]})
json.dump(cfg, open("/tmp/broken.json", "w"), indent=2)
PY
$D apply /tmp/broken.json --target / --yes $L; echo "AISKILLS-WARN-APPLY-RC=$?"
$D plan /tmp/broken.json --target / $L > /tmp/plan7.txt 2>&1
present /tmp/plan7.txt 'skill:nope'; echo "AISKILLS-WARN-REPLAN-RC=$?"

echo "AISKILLS-J: dropping the block removes what dasik owned"
python - <<'PY'
import json
cfg = json.load(open("config/vm-ai-skills.json"))
cfg.pop("ai_skills")
json.dump(cfg, open("/tmp/no-block.json", "w"), indent=2)
PY
$D plan /tmp/no-block.json --target / $L > /tmp/plan8.txt 2>&1
grep '\[ai_skills\]' /tmp/plan8.txt
present /tmp/plan8.txt 'delete .*skill:impeccable'; echo "AISKILLS-REMOVE-PLAN-RC=$?"
$D apply /tmp/no-block.json --target / --yes $L; echo "AISKILLS-REMOVE-APPLY-RC=$?"
test ! -e $H/.agents/skills/impeccable; echo "AISKILLS-REMOVED-RC=$?"
test -d $H/.claude/skills/handmade; echo "AISKILLS-FOREIGN-STILL-RC=$?"

echo "AISKILLS-K: rollback restores the generation and re-plans to nothing"
$D generations --target / $L | tail -10
$D rollback "$GEN_GOOD" --target / --yes $L; echo "AISKILLS-ROLLBACK-RC=$?"
# The generation dasik rolled back to IS the desired state now; planning its
# own config against the machine it just restored must propose nothing.
cp "$(readlink -f /var/lib/dasik/generations/current)/config.json" /tmp/restored.json
$D plan /tmp/restored.json --target / $L > /tmp/plan9.txt 2>&1
grep '\[ai_skills\]' /tmp/plan9.txt
absent /tmp/plan9.txt '\[ai_skills\]'; echo "AISKILLS-ROLLBACK-QUIET-RC=$?"

echo "AISKILLS-DONE"
sync
poweroff -f
