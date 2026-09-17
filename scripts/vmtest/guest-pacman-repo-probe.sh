#!/bin/bash
# Contract probe (not a pass/fail test): measures how pacman-key/gpg/pacman
# really behave on an already-installed Arch guest, for the
# `pacman.repositories` / `pacman.keys` design
# (docs/superpowers/specs/2026-09-16-pacman-repositories-design.md, "Hechos
# medidos" / "apply"). Later tasks parse the fixtures this produces instead of
# guessing at gpg/pacman-key output shapes.
#
# Deliberately does NOT use `set -x`: fixtures are cut straight out of this
# script's own stdout, between the PROBE-<n>-BEGIN/END markers below, and
# xtrace would interleave `+ <command>` echo lines into the captured output.
# Progress notes use `==` markers instead, which read distinctly from any
# command's real output (gpg/pacman-key never emit a line starting with `==`).
set -u

cd /root/repo || { echo "PROBE-DONE rc=90 (no /root/repo)"; sync; poweroff -f; exit 90; }

FPR=6C6568CE34894645A23ABC44B5BD6F8F9023E53B
GNUPGHOME=/etc/pacman.d/gnupg
KEYFILE=/var/tmp/amt911.gpg
ONECONF=/var/tmp/one.conf

begin() { echo "PROBE-$1-BEGIN"; }
end() {  # $1=n  $2=rc
    echo "PROBE-$1-RC=$2"
    echo "PROBE-$1-END"
}

# ---------------------------------------------------------------------------
echo "== measurement 1: cat /etc/pacman.conf =="
begin 1
cat /etc/pacman.conf
rc=$?
end 1 "$rc"

# ---------------------------------------------------------------------------
echo "== measurement 2: fetch amt911.gpg, gpg --show-keys --with-colons =="
begin 2
curl -fsSL https://amt911.github.io/arch-packages/amt911.gpg -o "$KEYFILE"
curl_rc=$?
echo "PROBE-2-CURL-RC=$curl_rc"
gpg --show-keys --with-colons "$KEYFILE"
rc=$?
end 2 "$rc"

# ---------------------------------------------------------------------------
echo "== measurement 3: list-keys, key absent (gpg direct vs pacman-key) =="
begin 3
echo "-- gpg --homedir $GNUPGHOME --with-colons --list-keys $FPR --"
gpg --homedir "$GNUPGHOME" --with-colons --list-keys "$FPR"
rc=$?
echo "-- pacman-key --list-keys $FPR --"
pacman-key --list-keys "$FPR"
pk_rc=$?
echo "PROBE-3-PACMANKEY-RC=$pk_rc"
end 3 "$rc"

# ---------------------------------------------------------------------------
echo "== measurement 4: pacman-key --add, then list-keys again =="
begin 4
pacman-key --add "$KEYFILE"
add_rc=$?
echo "PROBE-4-ADD-RC=$add_rc"
gpg --homedir "$GNUPGHOME" --with-colons --list-keys "$FPR"
rc=$?
end 4 "$rc"

# ---------------------------------------------------------------------------
echo "== measurement 5: lsign-key, then list-keys/list-sigs/list-secret-keys =="
begin 5
pacman-key --lsign-key "$FPR"
lsign_rc=$?
echo "PROBE-5-LSIGN-RC=$lsign_rc"
echo "-- list-keys --"
gpg --homedir "$GNUPGHOME" --with-colons --list-keys "$FPR"
lk_rc=$?
echo "PROBE-5-LISTKEYS-RC=$lk_rc"
echo "-- list-sigs --"
gpg --homedir "$GNUPGHOME" --with-colons --list-sigs "$FPR"
ls_rc=$?
echo "PROBE-5-LISTSIGS-RC=$ls_rc"
echo "-- list-secret-keys --"
gpg --homedir "$GNUPGHOME" --with-colons --list-secret-keys
rc=$?
end 5 "$rc"

# ---------------------------------------------------------------------------
echo "== measurement 6: /usr/share/pacman/keyrings, *-trusted head -3 =="
begin 6
ls /usr/share/pacman/keyrings/
for f in /usr/share/pacman/keyrings/*-trusted; do
    echo "-- $f --"
    head -3 "$f"
done
rc=$?
end 6 "$rc"

# ---------------------------------------------------------------------------
echo "== measurement 7: does arch-chroot mount a private /tmp? =="
begin 7
if ! command -v arch-chroot >/dev/null 2>&1; then
    echo "arch-chroot not found, installing arch-install-scripts"
    pacman -Sy --noconfirm --needed arch-install-scripts >/var/tmp/pacinstall.log 2>&1
    echo "PROBE-7-INSTALL-RC=$?"
fi
mkdir -p /mnt
mount --bind / /mnt
touch /tmp/probe-host
arch-chroot /mnt sh -c 'findmnt -n /tmp; ls /tmp/probe-host; ls /var/tmp'
rc=$?
umount /mnt
rm -f /tmp/probe-host
end 7 "$rc"

# ---------------------------------------------------------------------------
echo "== measurement 8: single-repo pacman -Sy vs core.db/extra.db mtimes =="
begin 8
echo "-- stat before --"
stat -c '%n %Y' /var/lib/pacman/sync/*.db
echo "-- building $ONECONF ([options] block + [amt911]) --"
awk '
/^\[options\]/ { flag=1 }
/^\[/ && !/^\[options\]/ { flag=0 }
flag { print }
' /etc/pacman.conf > "$ONECONF"
cat >> "$ONECONF" <<'EOF'
[amt911]
SigLevel = Required
Server = https://amt911.github.io/arch-packages/$arch
EOF
cat "$ONECONF"
pacman -Sy --config "$ONECONF"
sy_rc=$?
echo "PROBE-8-SY-RC=$sy_rc"
echo "-- stat after --"
stat -c '%n %Y' /var/lib/pacman/sync/*.db
echo "-- ls /var/lib/pacman/sync/ --"
ls -la /var/lib/pacman/sync/
echo "-- pacman --config $ONECONF -Sl amt911 | head -3 --"
pacman --config "$ONECONF" -Sl amt911 | head -3
rc=$?
end 8 "$rc"

# ---------------------------------------------------------------------------
echo "== measurement 9: pacman-key --delete, then list-keys again =="
begin 9
pacman-key --delete "$FPR"
del_rc=$?
echo "PROBE-9-DELETE-RC=$del_rc"
gpg --homedir "$GNUPGHOME" --with-colons --list-keys "$FPR"
rc=$?
end 9 "$rc"

# ---------------------------------------------------------------------------
# Fix round 1: FACT-PR-1 claimed the keyring command works identically
# "under both --target / and arch-chroot" but measurement 7 only ran
# findmnt/ls through arch-chroot, never gpg/pacman-key themselves. This
# repeats the add/lsign/list/delete cycle THROUGH arch-chroot (same
# bind-mount scaffold as measurement 7) and diffs the resulting colon
# output against the native (no-chroot) listing of the same keyring,
# since /mnt is a bind mount of / and both views resolve to the same
# real /etc/pacman.d/gnupg on disk.
echo "== measurement 10: same keyring ops through arch-chroot vs native =="
begin 10
mkdir -p /mnt
mount --bind / /mnt
mnt_rc=$?
echo "PROBE-10-MOUNT-RC=$mnt_rc"

echo "-- 10a: arch-chroot gpg --list-keys, key absent --"
arch-chroot /mnt gpg --homedir "$GNUPGHOME" --with-colons --list-keys "$FPR" 2>&1
rc10a=$?
echo "PROBE-10A-RC=$rc10a"

echo "-- 10b: arch-chroot pacman-key --add --"
arch-chroot /mnt pacman-key --add "$KEYFILE" 2>&1
rc10b=$?
echo "PROBE-10B-RC=$rc10b"

echo "-- 10c: arch-chroot pacman-key --lsign-key --"
arch-chroot /mnt pacman-key --lsign-key "$FPR" 2>&1
rc10c=$?
echo "PROBE-10C-RC=$rc10c"

echo "-- 10d: arch-chroot gpg --list-keys, key lsigned (through arch-chroot) --"
arch-chroot /mnt gpg --homedir "$GNUPGHOME" --with-colons --list-keys "$FPR" > /var/tmp/chroot-listkeys.txt 2>&1
rc10d_chroot=$?
cat /var/tmp/chroot-listkeys.txt
echo "PROBE-10D-CHROOT-RC=$rc10d_chroot"

echo "-- 10d: same key listed NATIVELY (no chroot), same keyring via the bind mount --"
gpg --homedir "$GNUPGHOME" --with-colons --list-keys "$FPR" > /var/tmp/native-listkeys.txt 2>&1
rc10d_native=$?
cat /var/tmp/native-listkeys.txt
echo "PROBE-10D-NATIVE-RC=$rc10d_native"

echo "-- 10d: diff native vs arch-chroot listing --"
if diff -u /var/tmp/native-listkeys.txt /var/tmp/chroot-listkeys.txt; then
    echo "PROBE-10D-DIFF=IDENTICAL"
else
    echo "PROBE-10D-DIFF=DIFFERS"
fi

echo "-- 10e: arch-chroot pacman-key --delete --"
arch-chroot /mnt pacman-key --delete "$FPR" 2>&1
rc10e=$?
echo "PROBE-10E-RC=$rc10e"

umount /mnt
rm -f /var/tmp/chroot-listkeys.txt /var/tmp/native-listkeys.txt
end 10 "$rc10d_chroot"

echo "PROBE-DONE rc=0"
sync
poweroff -f
