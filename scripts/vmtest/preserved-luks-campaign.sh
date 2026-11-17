#!/bin/bash
# The whole vm-preserved-luks campaign, from an empty work dir:
#
#   install (pre-preserved-luks.sh builds the kept disk on vdb, closed)
#   -> prepare     enable the stand-in greeter; give the root a fake FIDO2
#                  token (so the next boot waits for a key)
#   -> main        guest-preserved-luks.sh, kept disk attached
#   -> absent      kept disk detached
#   -> rekey       kept disk's passphrase stops being the root's
#   -> locked      nothing cached opens it: skipped with no prompt pending
#
# plus what only the HOST can see: the serial console of the main boot must
# show the unlock messages themselves (not the journal lines the guest prints)
# — the black screen on torre-amd was exactly their absence.
#
# Usage: preserved-luks-campaign.sh <workdir> [http-port]
# Needs DASIK_VM_ISO. Exit 0 only if every step and the console check pass.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
W=${1:?workdir}; port=${2:-8760}
mkdir -p "$W"; truncate -s 2G "$W/vdb.raw"
: "${DASIK_VM_ISO:?set DASIK_VM_ISO}"
fail=0
DASIK_VM_HTTP_PORT=$port DASIK_VM_WORKDIR=$W DASIK_VM_RAM=${DASIK_VM_RAM:-4096} \
DASIK_VM_CPUS=${DASIK_VM_CPUS:-4} DASIK_VM_DISK=12G DASIK_VM_KEYDEV=$W/vdb.raw \
DASIK_VM_PRE=scripts/vmtest/pre-preserved-luks.sh DASIK_VM_INSTALL_TIMEOUT=2400 \
    timeout --kill-after=60s 2700 scripts/vmtest/qemu.sh install-driven \
    config/vm-preserved-luks.json > "$W/install.out" 2>&1
rc=$?; echo "install rc=$rc"; [ $rc -eq 0 ] || exit 1
step() {  # <with-disk 1|0> <guest script> <marker>
    local kd=(); [ "$1" = 1 ] && kd=(DASIK_VM_KEYDEV="$W/vdb.raw")
    env DASIK_VM_WORKDIR="$W" DASIK_VM_RAM=${DASIK_VM_RAM:-4096} DASIK_VM_CPUS=${DASIK_VM_CPUS:-4} \
        "${kd[@]}" DASIK_VM_LUKS_PASSWORD=keptpass \
        timeout --kill-after=60s 900 scripts/vmtest/qemu.sh drive "$W/vda.qcow2" "$2" "$3" \
        > "$W/$2.out" 2>&1
    local r=$?; cp "$W/drive.log" "$W/$2.log" 2>/dev/null
    echo "$2 rc=$r"; [ $r -eq 0 ] || fail=1
}
step 1 guest-preserved-luks-prepare.sh PREPARE-DONE
step 1 guest-preserved-luks.sh KEPT-DONE
# The console itself, not the journal lines the guest script echoes (those
# carry the hostname, "dasik-kept"; the console's own lines do not).
console() { sed 's/\x1b\[[0-9;]*[A-Za-z]//g' "$W/guest-preserved-luks.sh.log" \
            | grep -a "$1" | grep -av 'dasik-kept' | head -1; }
for vol in cryptroot games; do
    # "... volume <label> (<mapper>)[ on <mountpoint>], please plug it in."
    line=$(console "Security token not present for unlocking volume .*($vol)[^,]*, please plug it in")
    if [ -n "$line" ]; then echo "CONSOLE-$vol=yes: $line"; else echo "CONSOLE-$vol=NO"; fail=1; fi
done
step 0 guest-preserved-luks-absent.sh ABSENT-DONE
step 1 guest-preserved-luks-rekey.sh REKEY-DONE
step 1 guest-preserved-luks-locked.sh LOCKED-DONE
grep -a -h "FAILED\|-DONE rc\|-DM-SAW\|LOCKED-PENDING\|STATE=" "$W"/guest-*.log 2>/dev/null \
    | grep -av "^+" | sed 's/\x1b\[[0-9;]*[A-Za-z]//g' | sort | uniq -c
echo "CAMPAIGN rc=$fail"; exit $fail
