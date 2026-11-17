#!/bin/bash
# Test setup for the vm-preserved-luks campaign, done on the first boot:
#
# 1. Enables the stand-in display manager (fake-dm.service, written by the
#    config's `files`), which creates the display-manager.service alias the
#    real greeter has — so the display-manager.service.d drop-in is tested the
#    way it applies to plasmalogin/sddm. (dasik's `systemd.enable_units` cannot
#    enable a unit that only `files` provides.)
# 2. Gives the ROOT a (fake) systemd-fido2 token WITHOUT a PIN requirement:
#    with one, systemd asks for the token PIN before it even looks for the key
#    (measured), and the serial driver cannot tell that prompt from the
#    passphrase one. What is under test is the WAIT for a missing key. As the real
# tower's root has two. The install declared unlock_fido2: 1 and could not
# enrol it (no key in a VM; luks_token_policy warn-and-continue), so the boot
# entry already carries fido2-device=auto for the root but the header had no
# token. With one, the NEXT boot's initramfs waits token-timeout for a key
# that never comes — the black-screen wait seen on torre-amd — which is what
# guest-preserved-luks.sh then shows on the console. Ends PREPARE-DONE rc=<n>.
set -x
rc=0
systemctl enable fake-dm.service || rc=1
[ "$(readlink /etc/systemd/system/display-manager.service)" = /etc/systemd/system/fake-dm.service ] || \
    { echo "PREPARE FAILED: no display-manager.service alias"; rc=1; }
dev=$(cryptsetup status cryptroot | awk '/device:/ {print $2}')
[ -n "$dev" ] || { echo "PREPARE FAILED: no cryptroot"; echo "PREPARE-DONE rc=1"; poweroff -f; }
head -c 32 /dev/urandom > /run/rootkey
printf %s keptpass | cryptsetup luksAddKey --key-file - --new-key-slot 1 "$dev" /run/rootkey || rc=1
cred=$(head -c 32 /dev/urandom | base64 -w0); salt=$(head -c 32 /dev/urandom | base64 -w0)
printf '{"type":"systemd-fido2","keyslots":["1"],"fido2-credential":"%s","fido2-salt":"%s","fido2-rp":"io.systemd.cryptsetup","fido2-clientPin-required":false,"fido2-up-required":true,"fido2-uv-required":false}' \
    "$cred" "$salt" > /run/roottoken.json
cryptsetup token import --json-file /run/roottoken.json "$dev" || rc=1
rm -f /run/rootkey /run/roottoken.json
n=$(cryptsetup luksDump --disable-external-tokens "$dev" | grep -c systemd-fido2)
echo "PREPARE-TOKENS=$n"; [ "$n" -eq 1 ] || rc=1
grep -q 'fido2-device=auto' /proc/cmdline || { echo "PREPARE FAILED: root cmdline lacks fido2-device=auto"; rc=1; }
echo "PREPARE-DONE rc=$rc"
sync; sleep 2; poweroff -f
