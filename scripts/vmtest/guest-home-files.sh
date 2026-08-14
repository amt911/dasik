#!/bin/bash
# `home_files` + the AppArmor notifier, checked INSIDE the booted guest.
#
# What only a real machine can answer: that the file landed in the home the
# machine says the user has, that it is OWNED BY THAT USER (a root-owned file in
# $HOME is the failure this domain exists to prevent) and that the mode stuck.
# Then the six verbs, as the two round trips: plan -> apply -> plan silent, and
# sync -> check -> plan silent. Ends with HOME-DONE, then powers off.
set -x
cd /root/repo || { echo "HOME-DONE rc=91"; poweroff -f; }

D="python -m dasik"
L="--no-log"                # the 9p repo is read-only; the run log defaults to $PWD
C=config/vm-apparmor.json
echo "HOME: BEGIN (target / = the live booted host)"

echo "HOME-A: the declared dotfile"
stat -c 'HOME-DOTFILE: %U:%G %a' /home/test/.config/dasik/hello.conf || echo "HOME-DOTFILE: MISSING"
cat /home/test/.config/dasik/hello.conf

echo "HOME-B: the directories dasik had to create belong to the user too"
stat -c 'HOME-DIR: %n %U:%G' /home/test/.config /home/test/.config/dasik

echo "HOME-C: the aa-notify autostart entry"
stat -c 'HOME-NOTIFY: %U:%G %a' /home/test/.config/autostart/apparmor-notify.desktop \
    || echo "HOME-NOTIFY: MISSING"
grep -q 'Exec=aa-notify' /home/test/.config/autostart/apparmor-notify.desktop \
    && echo "HOME-NOTIFY-EXEC: ok" || echo "HOME-NOTIFY-EXEC: WRONG"
command -v aa-notify && python -c 'import notify2, psutil; print("HOME-NOTIFY-DEPS: ok")'

echo "HOME-D: check"
$D check "$C" $L; echo "HOME-CHECK-RC=$?"

echo "HOME-E: plan (expect: No changes)"
$D plan "$C" --target / $L; echo "HOME-PLAN-RC=$?"

echo "HOME-F: apply, then plan again (expect both silent)"
$D apply "$C" --target / --yes $L; echo "HOME-APPLY-RC=$?"
$D plan "$C" --target / $L; echo "HOME-REPLAN-RC=$?"

echo "HOME-G: drift — somebody edits the file, and root takes it over"
echo "tampered" > /home/test/.config/dasik/hello.conf
chown root:root /home/test/.config/autostart/apparmor-notify.desktop
$D plan "$C" --target / $L; echo "HOME-DRIFT-RC=$?"
$D apply "$C" --target / --yes $L; echo "HOME-FIX-RC=$?"
stat -c 'HOME-REPAIRED: %U:%G' /home/test/.config/autostart/apparmor-notify.desktop
cat /home/test/.config/dasik/hello.conf
$D plan "$C" --target / $L; echo "HOME-AFTERFIX-RC=$?"

echo "HOME-H: generations and rollback (before sync, which widens ownership)"
$D generations --target / $L
$D rollback 1 --target / --yes $L; echo "HOME-ROLLBACK-RC=$?"
$D plan "$C" --target / $L; echo "HOME-POSTROLLBACK-RC=$?"

echo "HOME-I: sync — the dotfile and the notifier must come back as themselves"
cp "$C" /tmp/captured.json
$D sync /tmp/captured.json --target / $L; echo "HOME-SYNC-RC=$?"
python - <<'PY'
import json
c = json.load(open("/tmp/captured.json"))
print("HOME-CAPTURED-APPARMOR:", json.dumps(c.get("apparmor")))
print("HOME-CAPTURED-FILES:", json.dumps(c.get("home_files")))
PY
$D check /tmp/captured.json $L; echo "HOME-CAPCHECK-RC=$?"
$D plan /tmp/captured.json --target / $L; echo "HOME-CAPPLAN-RC=$?"

echo "HOME-J: the notifier turned OFF — the entry dasik owns must be deleted"
python - <<'PY'
import json
cfg = json.load(open("config/vm-apparmor.json"))
cfg["apparmor"]["desktop_notifications"] = False
cfg.pop("home_files", None)
json.dump(cfg, open("/tmp/no-notify.json", "w"), indent=2)
PY
$D plan /tmp/no-notify.json --target / $L; echo "HOME-OFF-RC=$?"

echo "HOME-DONE rc=0"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
