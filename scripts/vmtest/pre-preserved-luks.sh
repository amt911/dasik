#!/bin/bash
# Pre-install step for config/vm-preserved-luks.json (run by guest-install-auto.sh
# through dasik_pre=, inside the live ISO, BEFORE dasik). Builds on /dev/vdb the
# disk a reinstall must KEEP — torre-amd's games disk in miniature — and leaves
# it CLOSED, which is the state such a disk is in on a live ISO:
#
#   * GPT, one partition; LUKS2 with the config's fixed luks_uuid and the
#     passphrase 'keptpass' in keyslot 0;
#   * btrfs labelled Games with subvolumes @ and @snapshots, and a marker file
#     in @ that the booted machine must see through the mount;
#   * two systemd-fido2 tokens on keyslots 1 and 2. They are FAKE (no key can
#     answer them in a VM), which is enough: dasik counts tokens in the header,
#     and the boot falls back to the passphrase after token-timeout.
#
# Not a unit test because it needs a real LUKS header, a real btrfs and the
# udev by-uuid link the fix reads the closed header through.
set -euxo pipefail
DEV=/dev/vdb
PART=/dev/vdb1
UUID=5eed5eed-0000-4000-8000-00000000c0de
PASS=keptpass

sgdisk --zap-all "$DEV"
sgdisk -n1:0:0 -t1:8300 -c1:Games "$DEV"
partprobe "$DEV"; udevadm settle
printf %s "$PASS" | cryptsetup luksFormat --type luks2 --batch-mode --uuid "$UUID" --key-file - "$PART"
printf %s "$PASS" | cryptsetup open --key-file - "$PART" kept-prep
mkfs.btrfs -q -L Games /dev/mapper/kept-prep
mkdir -p /run/kept && mount /dev/mapper/kept-prep /run/kept
btrfs subvolume create /run/kept/@
btrfs subvolume create /run/kept/@snapshots
mkdir -p /run/kept/@/.snapshots
echo kept-across-reinstall > /run/kept/@/MARKER
umount /run/kept
cryptsetup close kept-prep

for slot in 1 2; do
    head -c 32 /dev/urandom > /run/kept-key$slot
    printf %s "$PASS" | cryptsetup luksAddKey --key-file - --new-key-slot "$slot" "$PART" /run/kept-key$slot
    cred=$(head -c 32 /dev/urandom | base64 -w0)
    salt=$(head -c 32 /dev/urandom | base64 -w0)
    printf '{"type":"systemd-fido2","keyslots":["%s"],"fido2-credential":"%s","fido2-salt":"%s","fido2-rp":"io.systemd.cryptsetup","fido2-clientPin-required":false,"fido2-up-required":true,"fido2-uv-required":false}' \
        "$slot" "$cred" "$salt" > /run/kept-token$slot.json
    cryptsetup token import --json-file /run/kept-token$slot.json "$PART"
done
udevadm trigger --settle "$PART" || udevadm settle
cryptsetup luksDump --disable-external-tokens "$PART" | sed -n '/^Tokens:/,/^Digests:/p'
test "$(cryptsetup luksDump --disable-external-tokens "$PART" | grep -c 'systemd-fido2')" -eq 2
test -e "/dev/disk/by-uuid/$UUID"
