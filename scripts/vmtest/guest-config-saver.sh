#!/bin/bash
# `config_saver`, checked INSIDE the booted guest against the LIVE host.
#
# The install already built the package from its Git PKGBUILD (it is in no repo
# and in no AUR), wrote the backup document and enabled the timer. What this
# adds is the half that needs a real machine and a real archive: produce one
# with config-saver itself, declare it as a `restore`, and prove it is unpacked
# once — not twice, and again when the archive changes.
#
# Ends with SAVER-DONE, then powers off.
set -x
cd /root/repo || { echo "SAVER-DONE rc=91"; poweroff -f; }

D="python -m dasik"
L="--no-log"                # the 9p repo is read-only; the run log defaults to $PWD
C=config/vm-config-saver.json
echo "SAVER: BEGIN (target / = the live booted host)"

echo "SAVER-A: the package built from the Git PKGBUILD"
pacman -Q config-saver && echo "SAVER-PKG: ok" || echo "SAVER-PKG: MISSING"
command -v config-saver

echo "SAVER-B: the backup document and the timer"
cat /etc/config-saver/configs/dotfiles.json
systemctl is-enabled config-saver@test.timer; echo "SAVER-TIMER-RC=$?"

echo "SAVER-C: check / plan (expect: No changes)"
$D check "$C" $L; echo "SAVER-CHECK-RC=$?"
$D plan "$C" --target / $L; echo "SAVER-PLAN-RC=$?"

echo "SAVER-D: apply, then plan again (expect both silent)"
$D apply "$C" --target / --yes $L; echo "SAVER-APPLY-RC=$?"
$D plan "$C" --target / $L; echo "SAVER-REPLAN-RC=$?"

echo "SAVER-E: produce a real archive with config-saver itself"
echo "# from the OLD machine" >> /home/test/.bashrc
su - test -c 'config-saver --compress --input /etc/config-saver/configs/dotfiles.json --output /home/test/dotfiles.tar.gz'
ls -l /home/test/dotfiles.tar.gz || echo "SAVER-ARCHIVE: MISSING"
cp /home/test/dotfiles.tar.gz /root/dotfiles.tar.gz

echo "SAVER-F: declare the restore — it must be planned, applied, then silent"
python - <<'PY'
import json
cfg = json.load(open("config/vm-config-saver.json"))
cfg["config_saver"]["restore"] = [{"user": "test", "archive": "/root/dotfiles.tar.gz"}]
json.dump(cfg, open("/tmp/with-restore.json", "w"), indent=2)
PY
$D plan /tmp/with-restore.json --target / $L; echo "SAVER-RESTORE-PLAN-RC=$?"
$D apply /tmp/with-restore.json --target / --yes $L; echo "SAVER-RESTORE-APPLY-RC=$?"
ls -l /home/test/.local/state/dasik/config-saver/ && echo "SAVER-MARKER: present"
stat -c 'SAVER-MARKER-OWNER: %U:%G' /home/test/.local/state/dasik/config-saver/* 2>/dev/null | head -1
$D plan /tmp/with-restore.json --target / $L; echo "SAVER-RESTORE-REPLAN-RC=$?"

echo "SAVER-G: a NEWER archive at the same path must be restored again"
echo "# newer" >> /home/test/.bashrc
su - test -c 'config-saver --compress --input /etc/config-saver/configs/dotfiles.json --output /home/test/dotfiles2.tar.gz'
cp /home/test/dotfiles2.tar.gz /root/dotfiles.tar.gz
$D plan /tmp/with-restore.json --target / $L; echo "SAVER-NEWER-PLAN-RC=$?"

echo "SAVER-H: an archive that is not there — apply must say so, not pretend"
python - <<'PY'
import json
cfg = json.load(open("config/vm-config-saver.json"))
cfg["config_saver"]["restore"] = [{"user": "test", "archive": "/root/does-not-exist.tar.gz"}]
json.dump(cfg, open("/tmp/missing-archive.json", "w"), indent=2)
PY
$D plan /tmp/missing-archive.json --target / $L; echo "SAVER-MISSING-PLAN-RC=$?"
$D apply /tmp/missing-archive.json --target / --yes $L; echo "SAVER-MISSING-APPLY-RC=$?"

echo "SAVER-I: generations and rollback (before sync, which widens ownership)"
$D generations --target / $L
$D rollback 1 --target / --yes $L; echo "SAVER-ROLLBACK-RC=$?"

echo "SAVER-J: sync — the documents and the timer must come back as the block"
cp "$C" /tmp/captured.json
$D sync /tmp/captured.json --target / $L; echo "SAVER-SYNC-RC=$?"
python -c 'import json;print("SAVER-CAPTURED:",json.dumps(json.load(open("/tmp/captured.json")).get("config_saver")))'
$D check /tmp/captured.json $L; echo "SAVER-CAPCHECK-RC=$?"
$D plan /tmp/captured.json --target / $L; echo "SAVER-CAPPLAN-RC=$?"

echo "SAVER-DONE rc=0"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
