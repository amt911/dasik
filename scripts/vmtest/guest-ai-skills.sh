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

# Drive the PACKAGED dasik when the machine has one — that is what the author's
# machines install (package_sources -> dasik-aur), and a domain that works from
# the source tree but not from the package would be a domain nobody can use.
# A machine that HAS the package was installed from the config that declares it,
# so the two travel together: driving the other one would plan the difference
# between them and call it a failure.
if command -v dasik > /dev/null 2>&1; then
    D="dasik"; C=config/vm-ai-skills-pkg.json
else
    D="python -m dasik"; C=config/vm-ai-skills.json
fi
export C                      # the python blocks below derive from it
L="--no-log"                 # the 9p repo is read-only; the log defaults to $PWD
U=test
H=/home/$U

absent() { ! grep -q "$2" "$1"; }     # rc 0 when the pattern is NOT in the file
present() { grep -q "$2" "$1"; }      # rc 0 when it is

# Every check goes through rc(), which prints the line AND counts the failures,
# so the harness's own verdict (AISKILLS-DONE rc=N) means something. Reading 55
# lines by hand to decide whether a run passed is how a red run gets called
# green.
FAILS=0
rc() { local v=$?; [ "$v" -eq 0 ] || FAILS=$((FAILS + 1)); echo "$1-RC=$v"; }
echo "AISKILLS: BEGIN (target / = the live booted host)"
echo "AISKILLS-DRIVER: $D  CONFIG: $C"
command -v dasik && dasik --version

echo "AISKILLS-A: the guest has what the installers need"
command -v node npx; rc AISKILLS-NODE
timeout 60 curl -sSf -o /dev/null https://registry.npmjs.org/; rc AISKILLS-NET

echo "AISKILLS-B: what the install already put there — ZERO manual steps"
# uv_tools installed the program during the install, and ai_skills then ran its
# `graphify install --platform`. Nobody typed anything.
test -d $H/.local/share/uv/tools/graphifyy; rc AISKILLS-UVTOOL
test -f $H/.claude/skills/graphify/SKILL.md; rc AISKILLS-GRAPHIFY-CLAUDE
test -f $H/.codex/skills/graphify/SKILL.md; rc AISKILLS-GRAPHIFY-CODEX
su - $U -c 'uv tool list' 2>&1 | head -5
# codex/cursor/opencode read ~/.agents/skills themselves (the `skills` CLI calls
# them universal agents and writes no directory of their own); claude-code gets
# a link. Asserting a ~/.codex/skills/<n> here would assert a bug.
test -f $H/.agents/skills/impeccable/SKILL.md; rc AISKILLS-CANONICAL
test -e $H/.claude/skills/impeccable; rc AISKILLS-CLAUDE-LINK
test ! -e $H/.codex/skills/impeccable; rc AISKILLS-NO-CODEX-DIR
head -20 $H/.agents/.skill-lock.json

echo "AISKILLS-C: check / plan (expect: no ai_skills change)"
$D check "$C" $L; rc AISKILLS-CHECK
$D plan "$C" --target / $L > /tmp/plan1.txt 2>&1; rc AISKILLS-PLAN
cat /tmp/plan1.txt
absent /tmp/plan1.txt '\[ai_skills\]'; rc AISKILLS-PLAN-QUIET

echo "AISKILLS-D: apply, then plan again — both silent"
$D apply "$C" --target / --yes $L; rc AISKILLS-APPLY
$D plan "$C" --target / $L > /tmp/plan2.txt 2>&1; rc AISKILLS-REPLAN
absent /tmp/plan2.txt '\[ai_skills\]'; rc AISKILLS-REPLAN-QUIET

echo "AISKILLS-E: sync captures the block, check accepts it, plan is silent"
# `sync` rewrites the config it is given, and the 9p repo is read-only.
cp "$C" /tmp/captured.json
$D sync /tmp/captured.json --target / $L; rc AISKILLS-SYNC
python - <<'PY'
import json, sys
block = json.load(open("/tmp/captured.json")).get("ai_skills")
print(json.dumps(block, indent=2))
names = sorted(e["name"] for e in (block or {}).get("entries", []))
sys.exit(0 if "impeccable" in names else 1)
PY
rc AISKILLS-SYNC-BLOCK
$D check /tmp/captured.json $L; rc AISKILLS-CHECKSYNC
$D plan /tmp/captured.json --target / $L > /tmp/plan3.txt 2>&1; rc AISKILLS-PLANSYNC
absent /tmp/plan3.txt '\[ai_skills\]'; rc AISKILLS-PLANSYNC-QUIET

echo "AISKILLS-F: the manifest records the domain"
$D generations --target / $L | tail -20; rc AISKILLS-GEN
grep -o 'ai_skills' /var/lib/dasik/state.json | head -1
present /var/lib/dasik/state.json 'ai_skills'; rc AISKILLS-MANIFEST

echo "AISKILLS-G: a skill nobody declared is left alone"
su - $U -c 'mkdir -p ~/.claude/skills/handmade && printf -- "---\nname: handmade\n---\n" > ~/.claude/skills/handmade/SKILL.md'
$D plan "$C" --target / $L > /tmp/plan4.txt 2>&1
absent /tmp/plan4.txt 'handmade'; rc AISKILLS-FOREIGN
test -d $H/.claude/skills/handmade; rc AISKILLS-FOREIGN-ALIVE

echo "AISKILLS-H: the plugin methods, with the real CLIs"
# npm 11 refuses install scripts by default, and without the postinstall the
# `claude` on PATH is a stub that errors "native binary not installed" — which
# would test dasik's failure path instead of its plugin path.
npm install -g --allow-scripts=@anthropic-ai/claude-code,@openai/codex \
    @anthropic-ai/claude-code @openai/codex > /tmp/npm.log 2>&1
rc AISKILLS-NPM; tail -3 /tmp/npm.log
command -v claude codex
su - $U -c 'claude --version' > /tmp/claude-version.txt 2>&1; rc AISKILLS-CLI
cat /tmp/claude-version.txt
python - <<'PY'
import json
import os
cfg = json.load(open(os.environ["C"]))
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
present /tmp/plan5.txt 'plugin:caveman@caveman'; rc AISKILLS-PLUGIN-PLAN
$D apply /tmp/with-plugins.json --target / --yes $L; rc AISKILLS-PLUGIN-APPLY
su - $U -c 'claude plugin list' 2>&1 | head -20
head -30 $H/.claude/plugins/installed_plugins.json
head -30 $H/.codex/config.toml
su - $U -c 'codex plugin list' 2>&1 | head -10
$D plan /tmp/with-plugins.json --target / $L > /tmp/plan6.txt 2>&1
grep '\[ai_skills\]' /tmp/plan6.txt      # names itself when it is not silent
absent /tmp/plan6.txt '\[ai_skills\]'; rc AISKILLS-PLUGIN-REPLAN-QUIET

echo "AISKILLS-H2: the tool method converged during the install"
command -v graphify || ls -l $H/.local/bin/graphify; rc AISKILLS-GRAPHIFY
su - $U -c 'PATH="$HOME/.local/bin:$PATH"; graphify' 2>&1 | head -2
cp /tmp/with-plugins.json /tmp/with-tool.json
$D plan /tmp/with-tool.json --target / $L > /tmp/plan10.txt 2>&1
grep '\[ai_skills\]\|\[uv_tools\]' /tmp/plan10.txt
absent /tmp/plan10.txt 'skill:graphify'; rc AISKILLS-TOOL-PLAN
$D apply /tmp/with-tool.json --target / --yes $L; rc AISKILLS-TOOL-APPLY
test -f $H/.claude/skills/graphify/SKILL.md; rc AISKILLS-TOOL-CLAUDE
test -f $H/.codex/skills/graphify/SKILL.md; rc AISKILLS-TOOL-CODEX
$D plan /tmp/with-tool.json --target / $L > /tmp/plan11.txt 2>&1
grep '\[ai_skills\]\|\[uv_tools\]' /tmp/plan11.txt
absent /tmp/plan11.txt '\[ai_skills\]'; rc AISKILLS-TOOL-REPLAN-QUIET

echo "AISKILLS-H2b: uv_tools plans, removes and re-installs like any domain"
python - <<'PY'
import json
cfg = json.load(open("/tmp/with-tool.json"))
cfg["uv_tools"] = {"tools": []}
json.dump(cfg, open("/tmp/no-uvtool.json", "w"), indent=2)
PY
$D plan /tmp/no-uvtool.json --target / $L > /tmp/plan13.txt 2>&1
grep '\[uv_tools\]' /tmp/plan13.txt
present /tmp/plan13.txt 'remove test:graphifyy'; rc AISKILLS-UV-REMOVE-PLAN
$D apply /tmp/no-uvtool.json --target / --yes $L; rc AISKILLS-UV-REMOVE-APPLY
test ! -d $H/.local/share/uv/tools/graphifyy; rc AISKILLS-UV-REMOVED
$D apply /tmp/with-tool.json --target / --yes $L; rc AISKILLS-UV-REINSTALL
test -d $H/.local/share/uv/tools/graphifyy; rc AISKILLS-UV-BACK
$D plan /tmp/with-tool.json --target / $L > /tmp/plan14.txt 2>&1
absent /tmp/plan14.txt '\[uv_tools\]'; rc AISKILLS-UV-REPLAN-QUIET

echo "AISKILLS-H3: sync captures the tool entry, and its own plan is silent"
cp /tmp/with-tool.json /tmp/captured-tool.json
$D sync /tmp/captured-tool.json --target / $L; rc AISKILLS-TOOL-SYNC
python - <<'PY'
import json
block = json.load(open("/tmp/captured-tool.json"))["ai_skills"]
print(json.dumps(block, indent=2))
tool = [e for e in block["entries"] if e["method"] == "tool"]
uv = json.load(open("/tmp/captured-tool.json")).get("uv_tools") or {}
print(json.dumps(uv))
ok = bool(tool) and tool[0]["command"] == "graphify" \
    and "graphifyy" in (uv.get("tools") or [])
import sys; sys.exit(0 if ok else 1)
PY
rc AISKILLS-CAPTURED
$D check /tmp/captured-tool.json $L; rc AISKILLS-TOOL-CHECKSYNC
$D plan /tmp/captured-tool.json --target / $L > /tmp/plan12.txt 2>&1
grep '\[ai_skills\]' /tmp/plan12.txt
absent /tmp/plan12.txt '\[ai_skills\]'; rc AISKILLS-TOOL-PLANSYNC-QUIET
absent /tmp/plan12.txt '\[uv_tools\]'; rc AISKILLS-UV-PLANSYNC-QUIET
echo "AISKILLS-H4: a registry that outlived the files it describes"
# The bug: `~/.claude/plugins/{installed_plugins,known_marketplaces}.json` are
# small and describe an intent, so backups keep them; `cache/` and
# `marketplaces/` are re-downloadable, so backups skip them. Restore that $HOME
# on a fresh machine and the registry claims a plugin nobody downloaded — and
# dasik used to believe the registry, plan nothing, and leave the machine
# without the skills it declares.
#
# TWO things this section learned the hard way:
#   * the state a restore leaves is BOTH directories gone. With the marketplace
#     clone still there, any `claude` command re-materialises the plugin cache
#     from it (asserted in H5), so deleting only the cache tests nothing.
#   * nothing may run `claude` between the deletion and the plan, for the same
#     reason: the machine would heal itself and the silence would be correct.
CACHE=$H/.claude/plugins/cache/caveman/caveman
CLONE=$H/.claude/plugins/marketplaces/caveman
test -d "$CACHE"; rc AISKILLS-GHOST-BEFORE
rm -rf "$CACHE" "$CLONE"
present $H/.claude/plugins/installed_plugins.json 'caveman@caveman'; rc AISKILLS-GHOST-REGISTRY
$D plan /tmp/with-tool.json --target / $L > /tmp/plan-ghost1.txt 2>&1
grep '\[ai_skills\]' /tmp/plan-ghost1.txt
present /tmp/plan-ghost1.txt 'plugin:caveman@caveman'; rc AISKILLS-GHOST-PLAN
# The marketplace is deliberately NOT planned: `claude plugin marketplace add`
# on a name the registry already knows answers "already on disk" and clones
# nothing, so planning it could never converge. Re-installing the plugin is
# what brings the clone back.
absent /tmp/plan-ghost1.txt 'marketplace:caveman'; rc AISKILLS-GHOST-NO-MARKET-PLAN
$D apply /tmp/with-tool.json --target / --yes $L; rc AISKILLS-GHOST-APPLY
test -d "$CACHE"; rc AISKILLS-GHOST-REPAIRED
test -d "$CLONE"; rc AISKILLS-GHOST-MARKET-BACK
su - $U -c 'claude plugin list' 2>&1 | head -12
$D plan /tmp/with-tool.json --target / $L > /tmp/plan-ghost2.txt 2>&1
absent /tmp/plan-ghost2.txt '\[ai_skills\]'; rc AISKILLS-GHOST-REPLAN-QUIET

echo "AISKILLS-H5: with the clone in place, the cache comes back on its own"
# Measured, and the reason H4 deletes both: Claude Code re-materialises a
# missing plugin cache from the marketplace clone the next time it runs. dasik
# is right to stay silent about a machine that repairs itself.
rm -rf "$CACHE"
su - $U -c 'claude plugin list' > /tmp/ghost-list.txt 2>&1
head -12 /tmp/ghost-list.txt
test -d "$CACHE"; rc AISKILLS-GHOST-SELFHEAL
$D plan /tmp/with-tool.json --target / $L > /tmp/plan-ghost3.txt 2>&1
absent /tmp/plan-ghost3.txt '\[ai_skills\]'; rc AISKILLS-GHOST-SELFHEAL-QUIET

echo "AISKILLS-H6: sync reports the machine, not the registry"
rm -rf "$CACHE" "$CLONE"
cp /tmp/with-tool.json /tmp/ghost-captured.json
$D sync /tmp/ghost-captured.json --target / $L; rc AISKILLS-GHOST-SYNC
python - <<'PY'
import json, sys
block = json.load(open("/tmp/ghost-captured.json")).get("ai_skills") or {}
plugins = [e["name"] for e in block.get("entries", [])
           if e.get("method") == "claude-plugin"]
print("captured claude plugins:", plugins)
# The files are gone, so the machine does not carry it — capturing it anyway
# would hand back a config describing an installation that is not there.
sys.exit(0 if "caveman" not in plugins else 1)
PY
rc AISKILLS-GHOST-SYNC-REALITY
$D check /tmp/ghost-captured.json $L; rc AISKILLS-GHOST-CHECKSYNC
# Put the machine back the way the sections below expect to find it.
$D apply /tmp/with-tool.json --target / --yes $L; rc AISKILLS-GHOST-RESTORE
test -d "$CACHE"; rc AISKILLS-GHOST-RESTORED

GEN_GOOD=$(basename "$(readlink -f /var/lib/dasik/generations/current)")
echo "AISKILLS-GEN-GOOD=$GEN_GOOD"

echo "AISKILLS-I: an installer that fails must not abort the apply"
python - <<'PY'
import json
# Build on the config the machine has converged to — KEEPING the working
# entries. Starting from the base config here would drop the plugins and
# graphify, and step J would then have nothing left to remove.
cfg = json.load(open("/tmp/with-tool.json"))
cfg["ai_skills"]["entries"].append(
    {"name": "nope", "method": "skills",
     "source": "dasik-test/definitely-not-a-repo", "agents": ["codex"]})
json.dump(cfg, open("/tmp/broken.json", "w"), indent=2)
PY
$D apply /tmp/broken.json --target / --yes $L; rc AISKILLS-WARN-APPLY
$D plan /tmp/broken.json --target / $L > /tmp/plan7.txt 2>&1
present /tmp/plan7.txt 'skill:nope'; rc AISKILLS-WARN-REPLAN

echo "AISKILLS-J: dropping the block removes what dasik owned"
python - <<'PY'
import json
cfg = json.load(open("/tmp/with-tool.json"))
cfg.pop("ai_skills")
json.dump(cfg, open("/tmp/no-block.json", "w"), indent=2)
PY
$D plan /tmp/no-block.json --target / $L > /tmp/plan8.txt 2>&1
grep '\[ai_skills\]' /tmp/plan8.txt
present /tmp/plan8.txt 'delete .*skill:impeccable'; rc AISKILLS-REMOVE-PLAN
$D apply /tmp/no-block.json --target / --yes $L; rc AISKILLS-REMOVE-APPLY
test ! -e $H/.agents/skills/impeccable; rc AISKILLS-REMOVED
test ! -e $H/.codex/skills/graphify; rc AISKILLS-TOOL-REMOVED
test -d $H/.claude/skills/handmade; rc AISKILLS-FOREIGN-STILL

echo "AISKILLS-K: rollback restores the generation and re-plans to nothing"
$D generations --target / $L | tail -10
$D rollback "$GEN_GOOD" --target / --yes $L; rc AISKILLS-ROLLBACK
# The generation dasik rolled back to IS the desired state now; planning its
# own config against the machine it just restored must propose nothing.
cp "$(readlink -f /var/lib/dasik/generations/current)/config.json" /tmp/restored.json
$D plan /tmp/restored.json --target / $L > /tmp/plan9.txt 2>&1
grep '\[ai_skills\]' /tmp/plan9.txt
absent /tmp/plan9.txt '\[ai_skills\]'; rc AISKILLS-ROLLBACK-QUIET

echo "AISKILLS-L: a codex marketplace out of scope is EXPLAINED, not just planned"
# The bug this section exists for: on a machine where codex has never been
# signed in, `codex plugin add superpowers@openai-curated` fails with
#   Error: plugin `superpowers` was not found in marketplace `openai-curated`
# because a curated marketplace is fetched by codex itself and does not exist
# until then. dasik was right to keep proposing the entry, but the reason only
# appeared as a red line ~24000 lines into the run log. `plan` must say it.
#
# Nothing here is applied: the entry CANNOT converge on this guest, and that is
# precisely the state under test. Codex is installed here rather than in the
# config so the rest of this script drives exactly the machine it did before.
pacman -S --noconfirm --needed openai-codex > /tmp/codexinst.txt 2>&1
tail -2 /tmp/codexinst.txt
command -v codex; rc AISKILLS-CODEX-INSTALLED

# The contract, on the real binary. TWO shapes, and the first run of this check
# got it wrong by assuming only the second:
#   * this user already has a marketplace of their own (`superpowers-dev`,
#     registered by section D above) but NOT the curated one — so the table is
#     printed and `openai-curated` is simply absent from it. That is the sharper
#     case: the warning has to be about THIS marketplace missing, not about the
#     user having none at all.
#   * root has never used codex, and gets the sentence instead.
# Both exit 0, which is why the OUTPUT is what dasik reads.
su - $U -c 'codex plugin marketplace list' > /tmp/markets.txt 2>&1
cat /tmp/markets.txt
present /tmp/markets.txt 'MARKETPLACE'; rc AISKILLS-MARKET-TABLE
absent /tmp/markets.txt 'openai-curated'; rc AISKILLS-NO-CURATED
codex plugin marketplace list > /tmp/markets-root.txt 2>&1
cat /tmp/markets-root.txt
present /tmp/markets-root.txt 'No plugin marketplaces in scope'; rc AISKILLS-NONE-SENTENCE

python - <<'PY'
import json, os
cfg = json.load(open(os.environ["C"]))
cfg["ai_skills"]["entries"].append(
    {"name": "superpowers", "method": "codex-plugin",
     "marketplace": {"name": "openai-curated"}})
json.dump(cfg, open("/tmp/codex-plugin.json", "w"), indent=2)
PY
$D plan /tmp/codex-plugin.json --target / $L > /tmp/plan10.txt 2>&1
grep -iE '\[ai_skills\]|marketplace' /tmp/plan10.txt | head -10
# Both halves matter: the change is still proposed (dasik never pretends an
# entry it cannot install is done), AND the plan explains why it will fail.
present /tmp/plan10.txt 'superpowers@openai-curated'; rc AISKILLS-CODEX-PLANNED
present /tmp/plan10.txt "no marketplace 'openai-curated' in scope"; rc AISKILLS-CODEX-WARNED
present /tmp/plan10.txt 'codex login'; rc AISKILLS-CODEX-REMEDY

echo "AISKILLS-DONE rc=$FAILS"
sync
poweroff -f
