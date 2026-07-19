#!/bin/bash
# AUR helper partial-retry regression, run INSIDE the booted (already-installed)
# guest via `qemu.sh drive <image> guest-aur-helper-retry.sh AUR-RETRY-DONE`.
#
# Reproduces the real failure sequence: a first apply installs yay and then the
# run dies; the next apply no longer sees yay as an INSTALL change. It must still
# reuse yay as the helper (never re-clone/rebuild it) and its `-S` flag must
# survive util-linux `su` option parsing (`-- sh` terminator). Ends with a single
# AUR-RETRY-DONE rc=<n> line the host driver greps.
#
# QEMU-only: it applies against the LIVE booted guest (target /). Never run on a
# real host.
set -u

cd /root || { echo "AUR-RETRY-DONE rc=91"; poweroff -f; }
export PYTHONPATH=/root/repo

FULL=/root/repo/config/vm-aur-helper-retry.json
BOOTSTRAP=/root/vm-aur-helper-bootstrap.json
BOOTSTRAP_LOG=/root/aur-bootstrap.log
RETRY_LOG=/root/aur-retry.log
rc=0

fail() {
    echo "BAD: $*"
    rc=1
}

# The installed guest has NO network configuration (dasik's network action only
# writes hostname/hosts), but the AUR path needs the pacman mirrors and
# aur.archlinux.org. Configure QEMU's user-net by hand: 10.0.2.15/24, gateway
# 10.0.2.2, DNS 10.0.2.3.
iface=""
for link in /sys/class/net/*; do
    case "${link##*/}" in lo) continue;; *) iface="${link##*/}"; break;; esac
done
ip link set "$iface" up
ip addr add 10.0.2.15/24 dev "$iface" 2>/dev/null
ip route add default via 10.0.2.2 2>/dev/null
rm -f /etc/resolv.conf
printf 'nameserver 10.0.2.3\n' > /etc/resolv.conf
if ! getent hosts aur.archlinux.org >/dev/null 2>&1; then
    echo "AUR-RETRY-DONE rc=92 (no DNS in guest: $iface)"
    [ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
fi

# The bootstrap config is the full one minus aur-downgrade: it leaves yay
# installed and downgrade pending, i.e. exactly the partial-apply state.
python - "$FULL" "$BOOTSTRAP" <<'PY'
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
data = json.loads(source.read_text(encoding="utf-8"))
data["packages"] = [
    package for package in data["packages"] if package != "aur-downgrade"
]
assert "aur-yay" in data["packages"]
assert "aur-downgrade" not in data["packages"]
destination.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
[ "$?" -eq 0 ] || fail "could not create bootstrap config"

echo "AUR-RETRY: bootstrap installs only yay"
python -m dasik apply "$BOOTSTRAP" --target / --yes --log "$BOOTSTRAP_LOG"
[ "$?" -eq 0 ] || fail "bootstrap apply failed"
pacman -Q yay >/dev/null 2>&1 || fail "yay missing after bootstrap"
if pacman -Q downgrade >/dev/null 2>&1; then
    fail "downgrade unexpectedly installed before retry"
fi

echo "AUR-RETRY: full apply must reuse preinstalled yay"
python -m dasik apply "$FULL" --target / --yes --log "$RETRY_LOG"
[ "$?" -eq 0 ] || fail "retry apply failed"
pacman -Q yay downgrade >/dev/null 2>&1 || fail "yay/downgrade missing after retry"

grep -F 'su - _aurbuilder -c exec "$@" -- sh yay -S --noconfirm --needed downgrade' \
    "$RETRY_LOG" >/dev/null \
    || fail "retry log lacks the exact su option barrier/helper argv"
if grep -F 'https://aur.archlinux.org/yay.git' "$RETRY_LOG" >/dev/null; then
    fail "retry rebuilt yay instead of reusing it"
fi
if grep -F "su: invalid option" "$RETRY_LOG" >/dev/null; then
    fail "su still consumed a helper flag"
fi
if id _aurbuilder >/dev/null 2>&1; then
    fail "temporary AUR builder still exists"
fi
if [ -e /etc/sudoers.d/_aurbuilder ]; then
    fail "temporary AUR sudoers fragment still exists"
fi

echo "AUR-RETRY: third apply must be a no-op"
third_output="$(python -m dasik apply "$FULL" --target / --yes --no-log 2>&1)"
third_rc=$?
printf '%s\n' "$third_output"
[ "$third_rc" -eq 0 ] || fail "third apply exited $third_rc"
printf '%s\n' "$third_output" | grep -F "No changes" >/dev/null \
    || fail "third apply was not a no-op"

echo "AUR-RETRY-DONE rc=$rc"
[ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
