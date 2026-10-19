#!/bin/bash
# `config_saver.restic_stores` on an EXISTING machine, INSIDE the booted guest.
#
# The image was installed with config-saver 3.4.0 (before the restic store),
# restic and age, and no store declared. Here the user already has .tar.gz
# backups; then the store is declared and every edge is walked against the live
# system. What only a machine shows: the real `config-saver --version` /
# `--restic-init` / `age-keygen` run through `su -`, the real files the store
# leaves, an upgrade of the package underneath a declared store, and that the
# old archives survive until the user migrates them by hand.
#
# Needs .vmcache/config-saver-3.5.0-1-any.pkg.tar.zst in the repo (the guest has
# no network): curl it from https://amt911.github.io/arch-packages/x86_64/.
#
# Ends with RSU-DONE rc=N, then powers off.
set -x
cd /root/repo || { echo "RSU-DONE rc=91"; poweroff -f; }
export PYTHONPATH=/root/repo
python -c 'import dasik' || { echo "RSU-DONE rc=93"; poweroff -f; }

D="python -m dasik"
L="--no-log"
U=test
H=$(getent passwd "$U" | cut -d: -f6)
S="$H/.config/config-saver"
PKG=/root/repo/.vmcache/config-saver-3.5.0-1-any.pkg.tar.zst
rc=0
ok()   { echo "RSU-$1 ok${2:+ ($2)}"; }
bad()  { echo "RSU-$1 BAD${2:+ ($2)}"; rc=1; }

echo "RSU-0: fresh state (the image persists between drives)"
[ -f "$PKG" ] || { echo "RSU-NO-PKG $PKG"; echo "RSU-DONE rc=94"; poweroff -f; }
rm -rf "$S/restic" "$S/restic-password.age" "$S/restic-store.json" "$S/configs" \
       "$S/configs.d" "$H/.config/age" /root/cfgs
[ -e /usr/bin/restic.away ] && mv /usr/bin/restic.away /usr/bin/restic
[ -e /usr/bin/config-saver.away ] && mv /usr/bin/config-saver.away /usr/bin/config-saver
mkdir -p /root/cfgs
python - <<'PY'
import json
c = json.load(open("config/vm-config-saver-restic-upgrade.json"))
c["config_saver"]["restic_stores"] = [{"user": "test"}]
json.dump(c, open("/root/cfgs/declared.json", "w"), indent=2)
PY
PLAIN=config/vm-config-saver-restic-upgrade.json
DECL=/root/cfgs/declared.json
pacman -Q config-saver restic age

echo "RSU-A: an existing user of config-saver 3.4.0, with encrypted archives"
pacman -Q config-saver | grep -q ' 3\.4\.0' && ok OLD-PKG || bad OLD-PKG "$(pacman -Q config-saver)"
su - "$U" -c 'mkdir -p ~/.config/age && age-keygen -o ~/.config/age/key.txt' 2>/dev/null
PUB=$(su - "$U" -c 'age-keygen -y ~/.config/age/key.txt' | tail -1)
install -d -o "$U" -g "$U" "$S" "$S/configs.d"
cat > "$S/configs.d/dotfiles.yaml" <<YAML
directories:
  - source: \$HOME
    files: [.bashrc]
encrypt:
  method: age
  recipients: [$PUB]
YAML
chown "$U:$U" "$S/configs.d/dotfiles.yaml"
su - "$U" -c 'config-saver --compress'; sleep 1; su - "$U" -c 'config-saver --compress'
archives=$(find "$S/configs" -name '*.tar.gz.age' | wc -l)
[ "$archives" = 2 ] && ok ARCHIVES 2 || bad ARCHIVES "$archives"

echo "RSU-B: declared — plan names the store"
$D plan $DECL --target / $L > /tmp/p1.txt 2>&1; cat /tmp/p1.txt
grep -q 'config_saver_restic.*test' /tmp/p1.txt && ok PLAN-PENDING || bad PLAN-PENDING

echo "RSU-C: config-saver 3.4.0 cannot make a store — a warning, apply succeeds"
$D apply $DECL --target / --yes $L > /tmp/a1.txt 2>&1; arc=$?; cat /tmp/a1.txt
[ "$arc" = 0 ] && ok OLD-APPLY-RC || bad OLD-APPLY-RC "$arc"
grep -q 'older than 3.5.0' /tmp/a1.txt && ok OLD-WARNED || bad OLD-WARNED
[ ! -e "$S/restic" ] && ok OLD-NO-STORE || bad OLD-NO-STORE

echo "RSU-D: the upgrade underneath (offline, the package the [amt911] repo serves)"
pacman -U --noconfirm "$PKG" > /tmp/up.txt 2>&1; urc=$?; tail -3 /tmp/up.txt
pacman -Q config-saver | grep -q ' 3\.5\.0' && ok UPGRADED || bad UPGRADED "rc=$urc"

echo "RSU-E: no restic on the target — a warning, nothing created"
mv /usr/bin/restic /usr/bin/restic.away
$D apply $DECL --target / --yes $L > /tmp/a2.txt 2>&1; arc=$?
mv /usr/bin/restic.away /usr/bin/restic
grep -q 'restic is not installed' /tmp/a2.txt && ok NORESTIC-WARNED || { cat /tmp/a2.txt; bad NORESTIC-WARNED; }
[ "$arc" = 0 ] && [ ! -e "$S/restic" ] && ok NORESTIC-NOTHING || bad NORESTIC-NOTHING "rc=$arc"

echo "RSU-F: no key — a warning, nothing created"
mv "$H/.config/age/key.txt" "$H/.config/age/key.away"
$D apply $DECL --target / --yes $L > /tmp/a3.txt 2>&1; arc=$?
mv "$H/.config/age/key.away" "$H/.config/age/key.txt"
grep -q 'is not on the target yet' /tmp/a3.txt && ok NOKEY-WARNED || { cat /tmp/a3.txt; bad NOKEY-WARNED; }
[ "$arc" = 0 ] && [ ! -e "$S/restic" ] && ok NOKEY-NOTHING || bad NOKEY-NOTHING "rc=$arc"

echo "RSU-F2: config-saver itself missing (a failed build) — a warning, not a crash"
mv /usr/bin/config-saver /usr/bin/config-saver.away
$D apply $DECL --target / --yes $L > /tmp/a3b.txt 2>&1; arc=$?
mv /usr/bin/config-saver.away /usr/bin/config-saver
grep -q 'config-saver is not installed' /tmp/a3b.txt && ok NOCS-WARNED || { cat /tmp/a3b.txt; bad NOCS-WARNED; }
[ "$arc" = 0 ] && [ ! -e "$S/restic" ] && ok NOCS-NOTHING || bad NOCS-NOTHING "rc=$arc"

echo "RSU-G: everything in place — the store is created, as the user"
$D apply $DECL --target / --yes $L > /tmp/a4.txt 2>&1; arc=$?; cat /tmp/a4.txt
[ "$arc" = 0 ] && ok APPLY-RC || bad APPLY-RC "$arc"
[ -d "$S/restic" ] && [ -f "$S/restic-password.age" ] && ok STORE || bad STORE
[ "$(stat -c %U "$S/restic")" = "$U" ] && ok STORE-OWNER || bad STORE-OWNER "$(stat -c %U "$S/restic")"
grep -q '"/home/test/.config/age/key.txt"' "$S/restic-store.json" && ok STORE-IDENTITY || bad STORE-IDENTITY
archives=$(find "$S/configs" -name '*.tar.gz.age' | wc -l)
[ "$archives" = 2 ] && ok OLD-ARCHIVES-KEPT || bad OLD-ARCHIVES-KEPT "$archives"

echo "RSU-H: plan → apply → plan is silent"
$D plan $DECL --target / $L > /tmp/p2.txt 2>&1; cat /tmp/p2.txt
grep -q 'config_saver_restic' /tmp/p2.txt && bad PLAN-SILENT || ok PLAN-SILENT

echo "RSU-I: the user migrates by hand; the timer's command stores a snapshot"
su - "$U" -c 'config-saver --migrate --delete-migrated' > /tmp/m.txt 2>&1; mrc=$?; cat /tmp/m.txt
[ "$mrc" = 0 ] && grep -q 'restic check: ok' /tmp/m.txt && ok MIGRATE || bad MIGRATE "rc=$mrc"
left=$(find "$S/configs" -name '*.tar.gz*' 2>/dev/null | wc -l)
[ "$left" = 0 ] && ok MIGRATED-NO-TARS || bad MIGRATED-NO-TARS "$left"
systemctl start "config-saver@$U.service"; src=$?
journalctl -u "config-saver@$U.service" --no-pager -n 20 | grep -q 'Stored snapshot' \
    && ok TIMER-SNAPSHOT || bad TIMER-SNAPSHOT "rc=$src"

echo "RSU-J: sync from a config that does not declare it brings it back"
cp $PLAIN /root/cfgs/sync.json
$D sync /root/cfgs/sync.json --target / $L > /tmp/s.txt 2>&1; src=$?; tail -5 /tmp/s.txt
python - <<'PY' && ok SYNC-CAPTURED || bad SYNC-CAPTURED
import json, sys
c = json.load(open("/root/cfgs/sync.json"))
sys.exit(0 if c["config_saver"].get("restic_stores") ==
         [{"user": "test", "identity": "~/.config/age/key.txt"}] else 1)
PY
$D check /root/cfgs/sync.json && ok SYNC-CHECK || bad SYNC-CHECK
$D plan /root/cfgs/sync.json --target / $L > /tmp/p3.txt 2>&1
grep -q 'config_saver_restic' /tmp/p3.txt && bad SYNC-PLAN-SILENT || ok SYNC-PLAN-SILENT

echo "RSU-K: the block dropped — no removal planned, the store stays"
$D plan $PLAIN --target / $L > /tmp/p4.txt 2>&1
grep -q 'config_saver_restic' /tmp/p4.txt && bad DROP-PLAN || ok DROP-PLAN
$D apply $PLAIN --target / --yes $L > /tmp/a5.txt 2>&1
[ -d "$S/restic" ] && [ -f "$S/restic-password.age" ] && ok DROP-STORE-KEPT || bad DROP-STORE-KEPT

echo "RSU-L: a half-made store is an error, never re-initialised over"
mv "$S/restic-password.age" /root/pw.away
$D apply $DECL --target / --yes $L > /tmp/a6.txt 2>&1; arc=$?
mv /root/pw.away "$S/restic-password.age"
[ "$arc" != 0 ] && grep -q 'incomplete' /tmp/a6.txt && ok HALF-ERROR || { cat /tmp/a6.txt; bad HALF-ERROR "rc=$arc"; }
su - "$U" -c 'config-saver --list' | grep -q restic && ok HALF-STORE-INTACT || bad HALF-STORE-INTACT

echo "RSU-M: generations"
$D generations --target / | tail -5

echo "RSU-DONE rc=$rc"
sync
poweroff -f
