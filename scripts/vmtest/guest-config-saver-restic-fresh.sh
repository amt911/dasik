#!/bin/bash
# `config_saver.restic_stores` on a FRESH install, INSIDE the booted guest.
#
# The install declared the store with config-saver 3.5.0, restic and age, but
# no age key: on a real machine the key arrives after the first boot. So the
# install must have WARNED (its config-saver version check ran inside
# arch-chroot) and succeeded — that line is in the HOST's install-driven
# output, `grep 'is not on the target yet'` there — and plan must still name
# the store, which is checked here. Then the key
# is put in place, apply creates the store on the live system, and the timer's
# own service writes a snapshot into it. A unit test cannot run the real
# arch-chroot, systemd unit or restic.
#
# Ends with RSF-DONE rc=N, then powers off.
set -x
cd /root/repo || { echo "RSF-DONE rc=91"; poweroff -f; }
export PYTHONPATH=/root/repo
python -c 'import dasik' || { echo "RSF-DONE rc=93"; poweroff -f; }

D="python -m dasik"
L="--no-log"
C=config/vm-config-saver-restic-fresh.json
U=test
H=$(getent passwd "$U" | cut -d: -f6)
S="$H/.config/config-saver"
rc=0
ok()  { echo "RSF-$1 ok${2:+ ($2)}"; }
bad() { echo "RSF-$1 BAD${2:+ ($2)}"; rc=1; }

echo "RSF-0: fresh state (the image persists between drives)"
rm -rf "$S/restic" "$S/restic-password.age" "$S/restic-store.json" "$S/configs" \
       "$S/configs.d" "$H/.config/age"
pacman -Q config-saver restic age
pacman -Q config-saver | grep -q ' 3\.5\.0' && ok PKG || bad PKG "$(pacman -Q config-saver)"

echo "RSF-A: installed without a key — no store, and plan still names it"
[ ! -e "$S/restic" ] && ok NO-STORE-YET || bad NO-STORE-YET
$D plan $C --target / $L > /tmp/p1.txt 2>&1; cat /tmp/p1.txt
grep -q 'config_saver_restic.*test' /tmp/p1.txt && ok PLAN-PENDING || bad PLAN-PENDING

echo "RSF-B: the key arrives; apply creates the store as the user"
su - "$U" -c 'mkdir -p ~/.config/age && age-keygen -o ~/.config/age/key.txt' 2>/dev/null
PUB=$(su - "$U" -c 'age-keygen -y ~/.config/age/key.txt' | tail -1)
$D apply $C --target / --yes $L > /tmp/a1.txt 2>&1; arc=$?; cat /tmp/a1.txt
[ "$arc" = 0 ] && ok APPLY-RC || bad APPLY-RC "$arc"
[ -d "$S/restic" ] && [ -f "$S/restic-password.age" ] && ok STORE || bad STORE
[ "$(stat -c %U "$S/restic")" = "$U" ] && ok STORE-OWNER || bad STORE-OWNER

echo "RSF-C: converged"
$D plan $C --target / $L > /tmp/p2.txt 2>&1; cat /tmp/p2.txt
grep -q 'config_saver_restic' /tmp/p2.txt && bad PLAN-SILENT || ok PLAN-SILENT

echo "RSF-D: the timer's service writes into the store"
install -d -o "$U" -g "$U" "$S/configs.d"
cat > "$S/configs.d/dotfiles.yaml" <<YAML
directories:
  - source: \$HOME
    files: [.bashrc]
encrypt:
  method: age
  recipients: [$PUB]
YAML
chown "$U:$U" "$S/configs.d/dotfiles.yaml"
systemctl start "config-saver@$U.service"; src=$?
journalctl -u "config-saver@$U.service" --no-pager -n 20
journalctl -u "config-saver@$U.service" --no-pager -n 20 | grep -q 'Stored snapshot' \
    && ok TIMER-SNAPSHOT || bad TIMER-SNAPSHOT "rc=$src"
left=$(find "$S/configs" -name '*.tar.gz*' 2>/dev/null | wc -l)
[ "$left" = 0 ] && ok NO-TARS || bad NO-TARS "$left"
su - "$U" -c 'config-saver --list' | grep -q restic && ok LIST || bad LIST

echo "RSF-DONE rc=$rc"
sync
poweroff -f
