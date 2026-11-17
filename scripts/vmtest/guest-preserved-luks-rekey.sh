#!/bin/bash
# Prepares the NEXT boot of the vm-preserved-luks machine for
# guest-preserved-luks-locked.sh: the kept disk's passphrase (keyslot 0) stops
# being the root's, so the passphrase the root unlock leaves in the kernel
# keyring no longer opens it. With the FIDO2 tokens unanswerable in a VM, that
# is exactly a boot WITHOUT the key in hand: nothing can open the disk
# silently. Ends with REKEY-DONE rc=<n>, then powers off.
set -x
rc=0
printf %s otherpass > /run/newkey
printf %s keptpass | cryptsetup luksChangeKey --key-slot 0 --key-file - /dev/vdb1 /run/newkey || rc=1
rm -f /run/newkey
printf %s keptpass | cryptsetup open --test-passphrase --key-file - /dev/vdb1 && \
    { echo "REKEY FAILED: the old passphrase still opens it"; rc=1; }
printf %s otherpass | cryptsetup open --test-passphrase --key-file - /dev/vdb1 || \
    { echo "REKEY FAILED: the new passphrase does not open it"; rc=1; }
echo "REKEY-DONE rc=$rc"
sync; sleep 2; poweroff -f
