#!/bin/bash
# AUR transitive-closure gate regression (2026-08-18 incident), run INSIDE the
# booted guest via `qemu.sh drive <image> guest-aur-closure.sh AUR-CLOSURE-DONE`.
#
# Reproduces the real failure: a declared AUR package (lib32-gst-libav) whose
# dep chain ends in a name nothing satisfies (lib32-ffmpeg -> lib32-libdav1d).
# The apply must abort BEFORE any package-domain mutation — no build user, no
# sudoers fragment, not even the repo transaction — and `check --resolve-aur`
# must catch the same chain without a target. The good config then installs,
# re-applies to silence, and shows up in generations.
#
# Anti-rot: upstream will eventually fix the lib32-ffmpeg PKGBUILD. The script
# asks the live RPC first; once the dep resolves, it echoes AUR-CLOSURE-SKIP-BROKEN
# and runs only the good half. To find a replacement broken pair, look for any
# AUR package whose info Depends lists a name with no rpc/v5/info entry and an
# empty rpc/v5/search/<name>?by=provides result.
#
# QEMU-only: it applies against the LIVE booted guest (target /). Never run on a
# real host.
set -u

cd /root || { echo "AUR-CLOSURE-DONE rc=91"; poweroff -f; }
export PYTHONPATH=/root/repo

GOOD=/root/repo/config/vm-aur-closure.json
BAD=/root/vm-aur-closure-bad.json
CHECK_LOG=/root/aur-closure-check.log
APPLY_BAD_LOG=/root/aur-closure-bad.log
APPLY_GOOD_LOG=/root/aur-closure-good.log
CHAIN='lib32-gst-libav → lib32-ffmpeg → lib32-libdav1d'
rc=0

fail() {
    echo "BAD: $*"
    rc=1
}

# The installed guest has NO network configuration; the resolver needs the
# mirrors and aur.archlinux.org. Configure QEMU's user-net by hand: 10.0.2.15/24,
# gateway 10.0.2.2, DNS 10.0.2.3.
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
    echo "AUR-CLOSURE-DONE rc=92 (no DNS in guest: $iface)"
    [ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
fi

# Is the incident dep still broken upstream? (info: no entry; provides: none)
broken_upstream=1
info=$(curl -sf 'https://aur.archlinux.org/rpc/v5/info?arg[]=lib32-libdav1d' || echo '')
provides=$(curl -sf 'https://aur.archlinux.org/rpc/v5/search/lib32-libdav1d?by=provides' || echo '')
case "$info" in *'"resultcount":0'*) ;; *) broken_upstream=0;; esac
case "$provides" in *'"resultcount":0'*) ;; *) broken_upstream=0;; esac
if [ "$broken_upstream" -eq 0 ]; then
    echo "AUR-CLOSURE-SKIP-BROKEN (upstream fixed lib32-libdav1d; bad-config half skipped)"
fi

if [ "$broken_upstream" -eq 1 ]; then
    # BAD config = GOOD + the broken AUR root + a canary repo package that must
    # never get installed (proves the gate fired before the repo transaction).
    python - "$GOOD" "$BAD" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
data["packages"] = data["packages"] + ["lib32-gst-libav", "tree"]
data["hostname"] = "dasik-aur-closure"
Path(sys.argv[2]).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
    [ "$?" -eq 0 ] || fail "could not derive the bad config"

    echo "AUR-CLOSURE: check --resolve-aur must name the chain"
    python -m dasik check "$BAD" --resolve-aur >"$CHECK_LOG" 2>&1
    check_rc=$?
    cat "$CHECK_LOG"
    [ "$check_rc" -eq 1 ] || fail "check --resolve-aur exited $check_rc, wanted 1"
    grep -F "$CHAIN" "$CHECK_LOG" >/dev/null || fail "check output lacks the chain"
    echo "AUR-CLOSURE-CHECK=$check_rc"

    echo "AUR-CLOSURE: apply of the bad config must abort pre-mutation"
    # Capture the CONSOLE (--no-log): the gate's chains and the resolution
    # split line go to stdout/stderr; the --log file only records commands.
    python -m dasik apply "$BAD" --target / --yes --no-log >"$APPLY_BAD_LOG" 2>&1
    bad_rc=$?
    cat "$APPLY_BAD_LOG"
    [ "$bad_rc" -ne 0 ] || fail "bad apply exited 0"
    grep -F "$CHAIN" "$APPLY_BAD_LOG" >/dev/null || fail "bad apply output lacks the chain"
    grep -F "resolved sources:" "$APPLY_BAD_LOG" >/dev/null \
        || fail "bad apply output lacks the resolution split line"
    if id _aurbuilder >/dev/null 2>&1; then
        fail "build user exists: the gate fired too late"
    fi
    if [ -e /etc/sudoers.d/_aurbuilder ]; then
        fail "sudoers fragment exists: the gate fired too late"
    fi
    if pacman -Q lib32-gst-libav >/dev/null 2>&1; then
        fail "lib32-gst-libav installed despite the broken chain"
    fi
    if pacman -Q tree >/dev/null 2>&1; then
        fail "canary repo package installed: the repo transaction ran"
    fi
    echo "AUR-CLOSURE-BAD-APPLY=$bad_rc"
fi

echo "AUR-CLOSURE: good config must converge through the same gate"
# install-driven already applied this config, so the usual outcome here is a
# clean no-op; a delta (if any) must install fine. Either way: rc 0 and the
# AUR packages present. The install phase's console is where the fresh-install
# gate pass + split line live — the host driver asserts on that log.
python -m dasik apply "$GOOD" --target / --yes --no-log >"$APPLY_GOOD_LOG" 2>&1
good_rc=$?
cat "$APPLY_GOOD_LOG"
[ "$good_rc" -eq 0 ] || fail "good apply exited $good_rc"
pacman -Q yay downgrade >/dev/null 2>&1 || fail "yay/downgrade missing after good apply"
if id _aurbuilder >/dev/null 2>&1; then
    fail "temporary AUR builder still exists"
fi
echo "AUR-CLOSURE-GOOD-APPLY=$good_rc"

echo "AUR-CLOSURE: second apply must be a no-op"
second_output="$(python -m dasik apply "$GOOD" --target / --yes --no-log 2>&1)"
second_rc=$?
printf '%s\n' "$second_output"
[ "$second_rc" -eq 0 ] || fail "second apply exited $second_rc"
printf '%s\n' "$second_output" | grep -F "No changes" >/dev/null \
    || fail "second apply was not a no-op"

echo "AUR-CLOSURE: generations must list the applied generation"
python -m dasik generations --target / | grep -E '^Generation [0-9]+' >/dev/null \
    || fail "generations lists nothing"

echo "AUR-CLOSURE-DONE rc=$rc"
[ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
