#!/bin/bash
# opencode as an AI agent (`mcp_servers` agent "opencode" + `ai_skills` method
# "opencode-plugin") driven INSIDE the booted guest, against the LIVE host
# (--target /).
#
# Why this is not a unit test: what only a guest can prove is the real
# `opencode plugin add|remove` (what it writes, to which file, what it does with a
# specifier and with a version change), the real `su - test -c ...` execution
# dasik uses to run it, and opencode itself LOADING the merged config
# (`opencode debug config`, `opencode mcp list`). The unit and journey tests mock
# every one of those.
#
# The install only provides the `opencode` package; the two blocks are added here
# (/tmp/oc-full.json), so the domains arrive UNOWNED and the whole cycle runs from
# zero, with a hand-written opencode.jsonc (a comment, a legacy `plugin` array)
# already in place that dasik must leave byte-for-byte alone.
#
# Plugin step and arch-chroot: the install-driven apply runs through arch-chroot,
# so OC-B first runs `opencode plugin add` under arch-chroot (a bind mount of the
# live root, as FACT-PR-1 did) into a scratch HOME and records whether it works.
# The result decides whether the plugin step is install-time or post-install only;
# see the verdict line OC-CHROOT-VERDICT and docs/FACTS.md. The dasik cycle itself
# is post-install either way: the install cannot converge it.
#
# CONVENTION: every OC-<step>-RC=0 line is a PASS; rc() counts failures so the
# final OC-DONE rc=N is the verdict. The measurements (OC-M1/M2/M3) print their
# data and are not counted.
#
# Needs NETWORK (SLIRP is enough): the plugins come from npm.
# Ends with OC-DONE, then powers off.
set -x
cd /root/repo || { echo "OC-DONE rc=91"; poweroff -f; }

if command -v dasik > /dev/null 2>&1; then D="dasik"; else D="python -m dasik"; fi
C=config/vm-opencode.json
F=/tmp/oc-full.json
L="--no-log"                  # the 9p repo is read-only; the log defaults to $PWD
U=test
H=/home/$U
CFG=$H/.config/opencode
J=$CFG/opencode.json
JC=$CFG/opencode.jsonc
SERVER=dasikprobe
P1=opencode-helicone-session@1.0.0
P2=opencode-helicone-session@1.0.1
SUPER='superpowers@git+https://github.com/obra/superpowers.git'
PORT=47123

absent() { ! grep -q "$2" "$1"; }     # rc 0 when the pattern is NOT in the file
present() { grep -q "$2" "$1"; }      # rc 0 when it is
asuser() { su - $U -c "$1"; }

FAILS=0
rc() { local v=$?; [ "$v" -eq 0 ] || FAILS=$((FAILS + 1)); echo "$1-RC=$v"; }
echo "OC: BEGIN (target / = the live booted host)"
echo "OC-DRIVER: $D  CONFIG: $C"

echo "OC-A: the guest has opencode and the network"
command -v opencode; rc OC-OPENCODE
asuser 'opencode --version'; rc OC-VERSION
timeout 60 curl -sSf -o /dev/null https://registry.npmjs.org/; rc OC-NET

echo "OC-B: MEASURE 1 - does 'opencode plugin add' work under arch-chroot (the install path)?"
mkdir -p /mnt && mount --bind / /mnt
install -d -o $U -g $U /var/tmp/chroot-home
arch-chroot /mnt su $U -s /bin/bash -c "export HOME=/var/tmp/chroot-home XDG_CONFIG_HOME=/var/tmp/chroot-home/.config XDG_DATA_HOME=/var/tmp/chroot-home/.local/share XDG_CACHE_HOME=/var/tmp/chroot-home/.cache XDG_STATE_HOME=/var/tmp/chroot-home/.local/state; opencode plugin add $P1" > /tmp/chroot-add.txt 2>&1
CHROOT_RC=$?
cat /tmp/chroot-add.txt
umount /mnt 2>/dev/null
echo "OC-M1: chroot plugin add rc=$CHROOT_RC"
if [ "$CHROOT_RC" -eq 0 ] && grep -q "$P1" /var/tmp/chroot-home/.config/opencode/opencode.json 2>/dev/null; then
    echo "OC-CHROOT-VERDICT: works under arch-chroot (no service needed); the plugin step may run at install time"
else
    echo "OC-CHROOT-VERDICT: FAILS under arch-chroot; the plugin step is post-install only"
fi
rm -rf /var/tmp/chroot-home

echo "OC-C: build the full config; hand-write the .jsonc the user would have"
python - <<'PY'
import json
cfg = json.load(open("config/vm-opencode.json"))
cfg["mcp_servers"] = {"users": ["test"], "entries": [
    {"name": "dasikprobe", "command": "sleep", "args": ["infinity"],
     "agents": ["opencode"]}]}
cfg["ai_skills"] = {"entries": [
    {"name": "opencode-helicone-session", "method": "opencode-plugin",
     "source": "opencode-helicone-session@1.0.0"}]}
json.dump(cfg, open("/tmp/oc-full.json", "w"), indent=2)
PY
install -d -o $U -g $U $H/.config $CFG
cat > $JC <<'EOF'
// hand-written by the user: dasik must never rewrite this file
{
  "provider": {}, // kept
  "plugin": ["superpowers@git+https://github.com/obra/superpowers.git"],
}
EOF
chown $U:$U $JC
SUM0=$(sha256sum $JC | cut -d' ' -f1)
echo "OC-JSONC-SUM0: $SUM0"
test ! -e $J; rc OC-NO-JSON-YET

echo "OC-D: check, plan (everything planned), apply, plan again (silent)"
$D check "$F" $L; rc OC-CHECK
$D plan "$F" --target / $L > /tmp/plan1.txt 2>&1; rc OC-PLAN
grep -E '\[(mcp_servers|ai_skills)\]' /tmp/plan1.txt
present /tmp/plan1.txt "test:opencode:$SERVER"; rc OC-PLAN-MCP
present /tmp/plan1.txt "test:opencode:plugin:$P1"; rc OC-PLAN-PLUGIN
$D apply "$F" --target / --yes $L > /tmp/apply1.txt 2>&1; rc OC-APPLY
cat /tmp/apply1.txt
echo "--- $J"; cat $J; echo "--- $JC"; cat $JC; ls -la $CFG
present $J "\"$SERVER\""; rc OC-MCP-WRITTEN
python - <<'PY'
import json, sys
d = json.load(open("/home/test/.config/opencode/opencode.json"))
s = d.get("mcp", {}).get("servers", {}).get("dasikprobe")
print(json.dumps(s))
sys.exit(0 if s and s.get("type") == "local"
         and s.get("command") == ["sleep", "infinity"] else 1)
PY
rc OC-MCP-SHAPE
python - <<'PY'
import json, sys
for f in ("opencode.json", "opencode.jsonc"):
    try:
        txt = open("/home/test/.config/opencode/" + f).read()
    except OSError:
        continue
    print("OC-M3: which file got the plugin?", f, "opencode-helicone-session@1.0.0" in txt)
txt = "".join(open("/home/test/.config/opencode/" + f).read()
              for f in ("opencode.json", "opencode.jsonc"))
sys.exit(0 if "opencode-helicone-session@1.0.0" in txt else 1)
PY
rc OC-PLUGIN-WRITTEN
stat -c '%U:%G %a' $J; test "$(stat -c %U $J)" = "$U"; rc OC-JSON-OWNER
$D plan "$F" --target / $L > /tmp/plan2.txt 2>&1; rc OC-REPLAN
absent /tmp/plan2.txt '\[mcp_servers\]'; rc OC-REPLAN-QUIET-MCP
absent /tmp/plan2.txt '\[ai_skills\]'; rc OC-REPLAN-QUIET-PLUGIN
echo "OC-M3: asked=$P1 landed=$(grep -h -o 'opencode-helicone-session@[0-9.]*' $J $JC | sort -u | tr '\n' ' ')"

echo "OC-E: opencode itself loads what dasik wrote"
asuser "opencode service set port $PORT" 2>&1 | tail -3
asuser 'opencode service get' 2>&1 | tail -3
asuser 'opencode debug config' > /tmp/debug-config.txt 2>&1; rc OC-DEBUG-CONFIG
head -80 /tmp/debug-config.txt
present /tmp/debug-config.txt "$SERVER"; rc OC-DEBUG-LISTS-SERVER
present /tmp/debug-config.txt "$P1"; rc OC-DEBUG-LISTS-PLUGIN
present /tmp/debug-config.txt 'opencode.jsonc'; rc OC-DEBUG-LOADS-JSONC
# the first `mcp list` after the service starts can answer "No MCP servers
# configured" before the config is loaded: retry, and say how many it took.
for i in 1 2 3 4 5 6; do
    timeout 120 su - $U -c 'opencode mcp list' > /tmp/mcp-list.txt 2>&1
    grep -q "$SERVER" /tmp/mcp-list.txt && break
    sleep 5
done
echo "OC-M4: mcp list showed the server on attempt $i"
cat /tmp/mcp-list.txt
present /tmp/mcp-list.txt "$SERVER"; rc OC-MCP-LIST-SHOWS-SERVER
asuser 'opencode service stop' 2>&1 | tail -2
asuser 'opencode service unset port' 2>&1 | tail -2

echo "OC-F: the hand-written .jsonc is untouched (checksum, comments)"
SUM1=$(sha256sum $JC | cut -d' ' -f1)
echo "OC-JSONC-SUM1: $SUM1"
test "$SUM0" = "$SUM1"; rc OC-JSONC-CHECKSUM
present $JC '// hand-written by the user'; rc OC-JSONC-COMMENT-KEPT
# opencode's own CLI may have added the plugin INTO the .jsonc (it does when no
# opencode.json exists yet); whatever it did, later steps are judged against it.
SUM0=$SUM1

echo "OC-G: sync captures both, check accepts it, plan is silent"
cp "$F" /tmp/captured.json
$D sync /tmp/captured.json --target / $L; rc OC-SYNC
python - <<'PY'
import json, sys
c = json.load(open("/tmp/captured.json"))
mcp = [e for e in (c.get("mcp_servers") or {}).get("entries", [])
       if e["name"] == "dasikprobe"]
plug = [e for e in (c.get("ai_skills") or {}).get("entries", [])
        if e.get("method") == "opencode-plugin"]
print(json.dumps(mcp), json.dumps(plug))
ok = mcp and mcp[0].get("agents") == ["opencode"] and mcp[0].get("command") == "sleep" \
    and any(e["source"] == "opencode-helicone-session@1.0.0" for e in plug)
sys.exit(0 if ok else 1)
PY
rc OC-SYNC-BLOCKS
python - <<'PY'
import json, sys
c = json.load(open("/tmp/captured.json"))
srcs = [e["source"] for e in (c.get("ai_skills") or {}).get("entries", [])
        if e.get("method") == "opencode-plugin"]
print("captured plugin sources:", srcs)
sys.exit(0 if "superpowers@git+https://github.com/obra/superpowers.git" in srcs else 1)
PY
rc OC-SYNC-CAPTURES-JSONC-PLUGIN
$D check /tmp/captured.json $L; rc OC-CHECKSYNC
$D plan /tmp/captured.json --target / $L > /tmp/plan3.txt 2>&1; rc OC-PLANSYNC
absent /tmp/plan3.txt '\[mcp_servers\]'; rc OC-PLANSYNC-QUIET-MCP
absent /tmp/plan3.txt '\[ai_skills\]'; rc OC-PLANSYNC-QUIET-PLUGIN

echo "OC-H: the manifest records the domains; generations, rollback, re-plan"
$D generations --target / $L | tail -20; rc OC-GEN
present /var/lib/dasik/state.json 'mcp_servers'; rc OC-MANIFEST-MCP
present /var/lib/dasik/state.json 'ai_skills'; rc OC-MANIFEST-PLUGIN
$D rollback --target / --yes $L > /tmp/rollback.txt 2>&1; rc OC-ROLLBACK
tail -8 /tmp/rollback.txt
# generation 1 is the state BEFORE the first apply: rolling back removes what
# dasik owns, so the full config plans both as CREATE again (and nothing else).
$D plan "$F" --target / $L > /tmp/plan4.txt 2>&1; rc OC-PLAN-ROLLED
grep -E '\[(mcp_servers|ai_skills)\]' /tmp/plan4.txt
present /tmp/plan4.txt "create test:opencode:$SERVER"; rc OC-ROLLED-MCP-REMOVED
present /tmp/plan4.txt "create test:opencode:plugin:$P1"; rc OC-ROLLED-PLUGIN-REMOVED
absent $J "\"$SERVER\""; rc OC-ROLLED-MCP-GONE-FROM-FILE
present $JC 'superpowers'; rc OC-ROLLED-USER-PLUGIN-ALIVE
present $JC '// hand-written by the user'; rc OC-ROLLED-JSONC-COMMENT-KEPT
$D apply "$F" --target / --yes $L > /tmp/apply-re.txt 2>&1; rc OC-REAPPLY
$D plan "$F" --target / $L > /tmp/plan4b.txt 2>&1
absent /tmp/plan4b.txt '\[mcp_servers\]'; rc OC-REAPPLY-QUIET-MCP
absent /tmp/plan4b.txt '\[ai_skills\]'; rc OC-REAPPLY-QUIET-PLUGIN

echo "OC-I: a plugin already living in the user's .jsonc counts as present"
python - <<'PY'
import json
c = json.load(open("/tmp/oc-full.json"))
c["ai_skills"]["entries"].append({"name": "superpowers",
    "method": "opencode-plugin",
    "source": "superpowers@git+https://github.com/obra/superpowers.git"})
json.dump(c, open("/tmp/oc-super.json", "w"), indent=2)
PY
$D plan /tmp/oc-super.json --target / $L > /tmp/plan-super.txt 2>&1; rc OC-SUPER-PLAN
absent /tmp/plan-super.txt 'plugin:superpowers'; rc OC-SUPER-RESIDENT-IN-JSONC-IS-SILENT

echo "OC-J: a changed arg is a MODIFY; a changed plugin version is CREATE(new)+DELETE(old)"
python - <<'PY'
import json
c = json.load(open("/tmp/oc-full.json"))
c["mcp_servers"]["entries"][0]["args"] = ["31536000"]
c["ai_skills"]["entries"][0]["source"] = "opencode-helicone-session@1.0.1"
json.dump(c, open("/tmp/oc-mod.json", "w"), indent=2)
PY
$D plan /tmp/oc-mod.json --target / $L > /tmp/plan6.txt 2>&1; rc OC-MOD-PLAN
grep -E '\[(mcp_servers|ai_skills)\]' /tmp/plan6.txt
grep -Ei '\[mcp_servers\].*(modify|update)' /tmp/plan6.txt; rc OC-MODIFY-PLAN
present /tmp/plan6.txt "plugin:$P2"; rc OC-VERSION-PLAN-NEW
present /tmp/plan6.txt "plugin:$P1"; rc OC-VERSION-PLAN-OLD
echo "OC-M2: plan order (CREATE new before DELETE old?):"
grep -n "plugin:opencode-helicone" /tmp/plan6.txt
$D apply /tmp/oc-mod.json --target / --yes $L > /tmp/apply2.txt 2>&1; rc OC-MOD-APPLY
cat /tmp/apply2.txt
cat $J
python - <<'PY'
import json, sys
d = json.load(open("/home/test/.config/opencode/opencode.json"))
print("OC-M2: plugins after the swap:", d.get("plugins"))
s = d["mcp"]["servers"]["dasikprobe"]["command"]
sys.exit(0 if s == ["sleep", "31536000"] else 1)
PY
rc OC-MODIFY-WRITTEN
python - <<'PY'
import json, sys
d = json.load(open("/home/test/.config/opencode/opencode.json"))
p = d.get("plugins") or []
sys.exit(0 if p == ["opencode-helicone-session@1.0.1"] else 1)
PY
rc OC-VERSION-SWAPPED
$D plan /tmp/oc-mod.json --target / $L > /tmp/plan7.txt 2>&1
absent /tmp/plan7.txt '\[mcp_servers\]'; rc OC-MOD-REPLAN-QUIET-MCP
absent /tmp/plan7.txt '\[ai_skills\]'; rc OC-MOD-REPLAN-QUIET-PLUGIN
present $JC '// hand-written by the user'; rc OC-MOD-JSONC-COMMENT-KEPT

echo "OC-K: the blocks removed - what dasik owns goes, the user's .jsonc stays"
python - <<'PY'
import json
c = json.load(open("/tmp/oc-mod.json"))
del c["mcp_servers"]; del c["ai_skills"]
json.dump(c, open("/tmp/oc-noblock.json", "w"), indent=2)
PY
$D plan /tmp/oc-noblock.json --target / $L > /tmp/plan8.txt 2>&1; rc OC-DROPPED-PLAN
grep -E '\[(mcp_servers|ai_skills)\]' /tmp/plan8.txt
present /tmp/plan8.txt "delete test:opencode:$SERVER"; rc OC-DROPPED-PLAN-MCP
present /tmp/plan8.txt "delete test:opencode:plugin:$P2"; rc OC-DROPPED-PLAN-PLUGIN
absent /tmp/plan8.txt 'superpowers'; rc OC-DROPPED-KEEPS-USER-PLUGIN
$D apply /tmp/oc-noblock.json --target / --yes $L > /tmp/apply3.txt 2>&1; rc OC-DROPPED-APPLY
cat /tmp/apply3.txt; cat $J
absent $J "\"$SERVER\""; rc OC-DROPPED-GONE-MCP
absent $J 'opencode-helicone-session'; rc OC-DROPPED-GONE-PLUGIN
present $JC '// hand-written by the user'; rc OC-DROPPED-JSONC-COMMENT-KEPT
present $JC 'superpowers'; rc OC-DROPPED-USER-PLUGIN-ALIVE
$D plan /tmp/oc-noblock.json --target / $L > /tmp/plan9.txt 2>&1
absent /tmp/plan9.txt '\[mcp_servers\]'; rc OC-DROPPED-REPLAN-QUIET-MCP
absent /tmp/plan9.txt '\[ai_skills\]'; rc OC-DROPPED-REPLAN-QUIET-PLUGIN

echo "OC: END with $FAILS failure(s)"
echo "OC-DONE rc=$FAILS"
sync
poweroff -f
