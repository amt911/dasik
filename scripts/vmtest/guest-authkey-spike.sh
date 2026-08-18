#!/bin/bash
# Oracle for the conffile AuthKey spelling + file: semantics (issue #318), run
# INSIDE a booted day-2 guest via
#   qemu.sh drive <image> guest-authkey-spike.sh AUTHKEY-DONE
#
# tsspike's trick: `tailscaled --config` exits instantly on a parse/schema
# error and HANGS (daemon runs) on success, so a short timeout is a clean
# verdict. Three probes:
#   1. "AuthKey": "file:<path>" with the file PRESENT  -> must be ACCEPTED
#   2. same, file ABSENT                               -> learn the behavior
#   3. "authKey" (lowercase)                           -> expected REJECTED
#
# QEMU-only. Never run on a real host.
set -u

rc=0

iface=""
for link in /sys/class/net/*; do
    case "${link##*/}" in lo) continue;; *) iface="${link##*/}"; break;; esac
done
ip link set "$iface" up
ip addr add 10.0.2.15/24 dev "$iface" 2>/dev/null
ip route add default via 10.0.2.2 2>/dev/null
printf 'nameserver 10.0.2.3\n' > /etc/resolv.conf
getent hosts archlinux.org >/dev/null 2>&1 || {
    echo "AUTHKEY-DONE rc=90 (no DNS)"
    [ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
}
pacman -Sy --noconfirm --needed tailscale >/tmp/pac.log 2>&1 || {
    echo "AUTHKEY-DONE rc=91 (pacman failed)"; tail -3 /tmp/pac.log
    [ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
}
echo "AUTHKEY-VERSION $(tailscale version | head -1)"

D=/tmp/ts; mkdir -p $D

probe() {   # $1 label  $2 conffile-json
    printf '%s\n' "$2" > $D/c.json
    out=$(timeout 3 tailscaled --config $D/c.json --statedir $D/state \
          --socket $D/s.sock --tun userspace-networking 2>&1)
    st=$?
    if [ $st -eq 124 ]; then
        echo "AUTHKEY-PROBE $1 ACCEPTED"
    else
        echo "AUTHKEY-PROBE $1 REJECTED rc=$st :: $(printf '%s' "$out" | tail -1)"
    fi
}

mkdir -p /etc/tailscale
printf 'tskey-auth-invalid-dummy\n' > /etc/tailscale/authkey
chmod 600 /etc/tailscale/authkey

probe present  '{"version": "alpha0", "AuthKey": "file:/etc/tailscale/authkey", "AcceptRoutes": true}'
rm -f /etc/tailscale/authkey
probe absent   '{"version": "alpha0", "AuthKey": "file:/etc/tailscale/authkey", "AcceptRoutes": true}'
probe lowercase '{"version": "alpha0", "authKey": "file:/etc/tailscale/authkey"}'

echo "AUTHKEY-DONE rc=$rc"
[ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
