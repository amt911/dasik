#!/bin/bash
# The UI/Android agent tooling of the author's three machines, driven INSIDE the
# booted guest against the LIVE host (--target /), after an install that already
# converged it in the chroot.
#
# What only a guest can prove, for THESE entries:
#   * every `npx skills add <repo> --skill <name>` names a skill that exists in
#     that repository today, and lands where both agents read it;
#   * `claude mcp add` / `codex mcp add` accept the exact commands and the http
#     URL, and write what `plan` reads back;
#   * `/etc/codex/config.toml` really raises project_doc_max_bytes for Codex —
#     measured with `codex debug prompt-input` on an AGENTS.md above 32 KiB, and
#     proven able to fail by taking the file away;
#   * android-cli from the AUR runs without downloading anything first;
#   * the install converged all of it with zero manual steps, and every verb
#     round-trips: plan -> apply -> plan, sync -> check -> plan, generations,
#     the blocks removed -> DELETE, rollback -> back and silent.
#
# The one thing that cannot converge here is measured, not assumed: the codex
# half of the 21st plugin, since the guest never runs `codex login`.
#
# CONVENTION: every AGUI-<step>-RC=0 line is a PASS; rc() counts the failures,
# so AGUI-DONE rc=N is the verdict.
#
# Needs NETWORK (SLIRP is enough). Ends with AGUI-DONE, then powers off.
set -x
cd /root/repo || { echo "AGUI-DONE rc=91"; poweroff -f; }
export PYTHONPATH=/root/repo

D="python -m dasik"
C=config/vm-agent-ui-tooling.json
L="--no-log"                  # the 9p repo is read-only; the log defaults to $PWD
U=test
H=/home/$U

# Written by hand from the guide, not derived from the config: an expectation
# computed from the input under test would agree with any mistake in it.
SKILLS="frontend-design ui-ux-pro-max web-design-guidelines
vercel-react-best-practices vercel-composition-patterns vercel-react-view-transitions
compose-component-design compose-state-and-effects compose-animations
compose-performance compose-focus-navigation compose-ui-testing-patterns
adaptive styles edge-to-edge android-cli android-profiler testing-setup"

absent() { ! grep -q "$2" "$1"; }     # rc 0 when the pattern is NOT in the file
present() { grep -q "$2" "$1"; }      # rc 0 when it is

FAILS=0
rc() { local v=$?; [ "$v" -eq 0 ] || FAILS=$((FAILS + 1)); echo "$1-RC=$v"; }

# The domain lines a converged plan may still carry: only the codex half of
# 21st, which needs `codex login`. Anything else in these four domains is a
# failure to converge.
leftovers() {
    grep -E '\[(ai_skills|mcp_servers|files|packages)\]' "$1" \
        | grep -vE 'test:codex:(plugin:21st@21st-dev|marketplace:21st-dev)'
}
quiet() { ! leftovers "$1"; }

echo "AGUI: BEGIN (target / = the live booted host)"
python -c 'import dasik' ; rc AGUI-IMPORT

echo "AGUI-A: the guest has what the installers need"
command -v node npx claude codex android; rc AGUI-TOOLS
timeout 60 curl -sSf -o /dev/null https://registry.npmjs.org/; rc AGUI-NET
claude --version; codex --version

echo "AGUI-B: what the install put there — zero manual steps"
missing=0
for n in $SKILLS; do
    test -f "$H/.agents/skills/$n/SKILL.md" || { echo "no canonical $n"; missing=1; }
    test -f "$H/.claude/skills/$n/SKILL.md" || { echo "no claude link $n"; missing=1; }
    test ! -e "$H/.codex/skills/$n" || { echo "codex dir for universal $n"; missing=1; }
done
[ "$missing" -eq 0 ]; rc AGUI-SKILLS-ON-DISK
test "$(echo $SKILLS | wc -w)" -eq 18; rc AGUI-SKILL-COUNT-IS-THE-GUIDE

cat > /tmp/expect_mcp.py <<'PY'
import json, sys, tomllib
home = sys.argv[1]
want = {
    "shadcn": {"command": "npx", "args": ["shadcn@latest", "mcp"]},
    "magicui": {"command": "npx", "args": ["-y", "@magicuidesign/mcp@latest"]},
    "chrome-devtools": {"command": "npx", "args": [
        "-y", "chrome-devtools-mcp@latest", "--executablePath",
        "/usr/bin/chromium", "--no-usage-statistics"]},
    "playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]},
    "figma": {"url": "https://mcp.figma.com/mcp"},
}
claude = json.load(open(f"{home}/.claude.json")).get("mcpServers") or {}
codex = tomllib.load(open(f"{home}/.codex/config.toml", "rb")).get("mcp_servers") or {}
bad = []
for name, spec in want.items():
    for agent, found in (("claude", claude.get(name)), ("codex", codex.get(name))):
        if not found or any(found.get(k) != v for k, v in spec.items()):
            bad.append((agent, name, found))
print("claude:", sorted(claude), "codex:", sorted(codex))
for b in bad:
    print("MISMATCH", b)
sys.exit(1 if bad else 0)
PY
python /tmp/expect_mcp.py $H; rc AGUI-MCP-REGISTERED

grep -q '^project_doc_max_bytes = 524288$' /etc/codex/config.toml; rc AGUI-CODEX-SYSTEM-FILE
present $H/.claude/plugins/installed_plugins.json '21st@21st-dev'; rc AGUI-21ST-CLAUDE
su - $U -c 'codex plugin list' > /tmp/codex-plugins.txt 2>&1; cat /tmp/codex-plugins.txt
if grep -q '21st' $H/.codex/config.toml; then echo "AGUI-21ST-CODEX-MEASURED=installed"
else echo "AGUI-21ST-CODEX-MEASURED=not-installed"; fi

su - $U -c 'android --version' > /tmp/android.txt 2>&1; rc AGUI-ANDROID-RUNS
cat /tmp/android.txt
absent /tmp/android.txt 'Downloading'; rc AGUI-ANDROID-NO-DOWNLOAD

echo "AGUI-C: check, then plan — converged by the install"
$D check "$C" $L; rc AGUI-CHECK
$D plan "$C" --target / $L > /tmp/plan1.txt 2>&1; rc AGUI-PLAN
cat /tmp/plan1.txt
leftovers /tmp/plan1.txt
quiet /tmp/plan1.txt; rc AGUI-PLAN-QUIET

echo "AGUI-D: apply, then plan again — both silent"
$D apply "$C" --target / --yes $L > /tmp/apply1.txt 2>&1; rc AGUI-APPLY
tail -20 /tmp/apply1.txt
$D plan "$C" --target / $L > /tmp/plan2.txt 2>&1; rc AGUI-REPLAN
quiet /tmp/plan2.txt; rc AGUI-REPLAN-QUIET

echo "AGUI-E: Codex reads /etc/codex/config.toml — and the check can fail"
# An AGENTS.md above the 32 KiB default with a unique last line. Codex drops the
# tail of an oversized file without a word, so the marker is the observable.
su - $U -c 'rm -rf ~/docprobe && mkdir ~/docprobe && cd ~/docprobe && git init -q &&
    { for i in $(seq 1 900); do echo "filler line $i: the quick brown fox jumps over the lazy dog"; done;
      echo "AGUI-TAIL-MARKER-7f3a"; } > AGENTS.md && wc -c AGENTS.md'
su - $U -c 'cd ~/docprobe && codex debug prompt-input hola' > /tmp/prompt-with.txt 2>&1
rc AGUI-PROMPT-INPUT-RUNS
head -c 600 /tmp/prompt-with.txt; echo
test "$(grep -o 'AGUI-TAIL-MARKER-7f3a' /tmp/prompt-with.txt | wc -l)" -ge 1; rc AGUI-DOC-TAIL-WITH-FILE
mv /etc/codex/config.toml /root/codex-config.toml.away
su - $U -c 'cd ~/docprobe && codex debug prompt-input hola' > /tmp/prompt-without.txt 2>&1
test "$(grep -o 'AGUI-TAIL-MARKER-7f3a' /tmp/prompt-without.txt | wc -l)" -eq 0; rc AGUI-DOC-TAIL-GONE-WITHOUT-FILE
# The file dasik owns went missing: plan must SAY so, then apply puts it back.
$D plan "$C" --target / $L > /tmp/plan-nofile.txt 2>&1
present /tmp/plan-nofile.txt '/etc/codex/config.toml'; rc AGUI-FILE-MISSING-PLANNED
rm -f /root/codex-config.toml.away
$D apply "$C" --target / --yes $L > /tmp/apply-file.txt 2>&1; rc AGUI-FILE-REAPPLY
grep -q '^project_doc_max_bytes = 524288$' /etc/codex/config.toml; rc AGUI-FILE-BACK

echo "AGUI-F: sync captures both blocks, check accepts it, plan is silent"
cp "$C" /tmp/captured.json
$D sync /tmp/captured.json --target / $L; rc AGUI-SYNC
cat > /tmp/expect_capture.py <<'PY'
import json, sys
cfg = json.load(open("/tmp/captured.json"))
skills = {e["name"]: sorted(e.get("agents", [])) for e in cfg["ai_skills"]["entries"]
          if e["method"] == "skills"}
want = sys.argv[1].split()
bad = [n for n in want if skills.get(n) != ["claude-code", "codex"]]
servers = sorted(e["name"] for e in cfg["mcp_servers"]["entries"])
print("captured skills:", len(skills), "servers:", servers, "bad:", bad)
ok = not bad and servers == sorted(["chrome-devtools", "figma", "magicui", "playwright", "shadcn"])
sys.exit(0 if ok else 1)
PY
python /tmp/expect_capture.py "$SKILLS"; rc AGUI-SYNC-BLOCKS
$D check /tmp/captured.json $L; rc AGUI-CHECKSYNC
$D plan /tmp/captured.json --target / $L > /tmp/plan3.txt 2>&1; rc AGUI-PLANSYNC
quiet /tmp/plan3.txt; rc AGUI-PLANSYNC-QUIET

echo "AGUI-G: the manifest records the domains"
$D generations --target / $L | tail -20; rc AGUI-GEN
for d in ai_skills mcp_servers /etc/codex/config.toml; do
    present /var/lib/dasik/state.json "$d"; rc "AGUI-MANIFEST-$(echo $d | tr '/.' '__')"
done

echo "AGUI-H: the blocks removed — what dasik owns goes"
python - <<'PY'
import json
cfg = json.load(open("config/vm-agent-ui-tooling.json"))
del cfg["ai_skills"], cfg["mcp_servers"]
cfg["files"] = [f for f in cfg["files"] if f["path"] != "/etc/codex/config.toml"]
json.dump(cfg, open("/tmp/noblock.json", "w"), indent=2)
PY
$D plan /tmp/noblock.json --target / $L > /tmp/plan4.txt 2>&1; rc AGUI-DROPPED-PLAN
grep -E '\[(ai_skills|mcp_servers|files)\]' /tmp/plan4.txt
present /tmp/plan4.txt 'delete test:claude-code:skill:frontend-design'; rc AGUI-DROPPED-PLANS-CLAUDE-SKILL
present /tmp/plan4.txt 'delete test:codex:skill:testing-setup'; rc AGUI-DROPPED-PLANS-CODEX-SKILL
present /tmp/plan4.txt 'test:codex:figma'; rc AGUI-DROPPED-PLANS-MCP
present /tmp/plan4.txt 'delete /etc/codex/config.toml'; rc AGUI-DROPPED-PLANS-FILE
$D apply /tmp/noblock.json --target / --yes $L > /tmp/apply2.txt 2>&1; rc AGUI-DROPPED-APPLY
tail -20 /tmp/apply2.txt
left=0
for n in $SKILLS; do
    test ! -e "$H/.agents/skills/$n" || { echo "canonical left: $n"; left=1; }
    test ! -e "$H/.claude/skills/$n" || { echo "claude link left: $n"; left=1; }
done
[ "$left" -eq 0 ]; rc AGUI-DROPPED-SKILLS-GONE
! python /tmp/expect_mcp.py $H > /tmp/mcp-after-drop.txt 2>&1; rc AGUI-DROPPED-MCP-NOT-REGISTERED
cat /tmp/mcp-after-drop.txt
test ! -e /etc/codex/config.toml; rc AGUI-DROPPED-FILE-GONE
$D plan /tmp/noblock.json --target / $L > /tmp/plan5.txt 2>&1
quiet /tmp/plan5.txt; rc AGUI-DROPPED-REPLAN-QUIET

echo "AGUI-I: rollback to the generation with the blocks — back, and silent"
$D rollback --target / --yes $L > /tmp/rollback.txt 2>&1; rc AGUI-ROLLBACK
tail -20 /tmp/rollback.txt
python /tmp/expect_mcp.py $H; rc AGUI-ROLLED-MCP
back=0
for n in $SKILLS; do
    test -f "$H/.claude/skills/$n/SKILL.md" || { echo "not back: $n"; back=1; }
done
[ "$back" -eq 0 ]; rc AGUI-ROLLED-SKILLS
grep -q '^project_doc_max_bytes = 524288$' /etc/codex/config.toml; rc AGUI-ROLLED-FILE
$D plan "$C" --target / $L > /tmp/plan6.txt 2>&1; rc AGUI-PLAN-ROLLED
quiet /tmp/plan6.txt; rc AGUI-PLAN-ROLLED-QUIET

echo "AGUI-J: informational — the servers answer (health check downloads them)"
su - $U -c 'timeout 300 claude mcp list' 2>&1 | tail -12

echo "AGUI-DONE rc=$FAILS"
sync
poweroff -f
