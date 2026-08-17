#!/usr/bin/env bash
# UNATTENDED in-guest installer, fetched and run by archiso via the `script=`
# kernel parameter (see qemu.sh install). Runs as root inside the Arch ISO guest
# and performs a real `dasik apply` onto the guest's /dev/vda.
#
# Everything it writes is prefixed "DASIK-VM:" so the host can follow progress on
# the serial console, and it ends with a single "DASIK-VM-DONE rc=<n>" line the
# host waits for. Safe: it only ever runs inside the throwaway QEMU guest.
exec > /dev/ttyS0 2>&1
set -x

# Config path comes from the kernel cmdline (dasik_config=…), set by the host's
# qemu.sh install; fall back to the minimal config.
CONFIG="$(sed -n 's/.*dasik_config=\([^ ]*\).*/\1/p' /proc/cmdline)"
CONFIG="${CONFIG:-config/vm-minimal.json}"

# dasik_verbose=1 echoes the live command stream to the console. Without it the
# output of pacstrap/makepkg/… only reaches dasik's run log, which lives in the
# ISO's tmpfs and dies with the guest — so a build that fails inside the chroot
# leaves the host holding an exit code and no reason for it. Opt-in: it makes
# the serial log an order of magnitude longer.
VERBOSE=""
grep -q 'dasik_verbose=1' /proc/cmdline && VERBOSE="-v"

echo "DASIK-VM: BEGIN unattended install ($CONFIG)"

# 1. Wait for the QEMU user-net gateway (host) to be reachable.
for _ in $(seq 1 30); do
    ping -c1 -W1 10.0.2.2 >/dev/null 2>&1 && break
    sleep 2
done

# 2. Mount the read-only repo share (9p) exported by the host.
mkdir -p /mnt-src
if ! mount -t 9p -o trans=virtio,ro dasik /mnt-src; then
    echo "DASIK-VM: FATAL 9p mount failed"
    echo "DASIK-VM-DONE rc=90"; sleep 3; poweroff -f
fi

# 3. Install dasik into a venv (pydantic/colorama pulled over the guest network).
cp -r /mnt-src /root/dasik
cd /root/dasik || { echo "DASIK-VM-DONE rc=91"; poweroff -f; }
python -m venv /root/venv
if ! /root/venv/bin/pip install -e . ; then
    echo "DASIK-VM: FATAL pip install failed"
    echo "DASIK-VM-DONE rc=92"; sleep 3; poweroff -f
fi

# 4. The real test: converge the guest disk to the config.
echo "DASIK-VM: plan"
/root/venv/bin/dasik plan "$CONFIG" --target /mnt
echo "DASIK-VM: apply (destructive, guest /dev/vda only)"
/root/venv/bin/dasik apply "$CONFIG" --target /mnt --yes $VERBOSE
first_rc=$?
echo "DASIK-VM: dasik apply exit=$first_rc"

# 5. Evidence for the host to grep.
echo "DASIK-VM: /mnt ->"; ls -A /mnt 2>&1 | tr '\n' ' '; echo
echo "DASIK-VM: /mnt/boot ->"; ls -A /mnt/boot 2>&1 | tr '\n' ' '; echo
echo "DASIK-VM: kernel present ->"; ls /mnt/boot/vmlinuz-* 2>/dev/null && echo yes || echo no
echo "DASIK-VM: pacman db ->"; ls -d /mnt/var/lib/pacman 2>/dev/null && echo yes || echo no

# 6. Idempotency check in a REAL Arch environment: a second apply must be a no-op.
#    A failing second apply is a real regression, so it must reach the marker too
#    (it used to be reported and then discarded).
second_rc=0
if [ "$first_rc" -eq 0 ]; then
    echo "DASIK-VM: second apply (expect no-op)"
    /root/venv/bin/dasik apply "$CONFIG" --target /mnt --yes $VERBOSE
    second_rc=$?
    echo "DASIK-VM: second apply exit=$second_rc"
else
    echo "DASIK-VM: second apply skipped because first apply failed"
fi

final_rc=$first_rc
if [ "$final_rc" -eq 0 ] && [ "$second_rc" -ne 0 ]; then
    final_rc=$second_rc
fi

echo "DASIK-VM-DONE rc=$final_rc"
sync
sleep 3
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
