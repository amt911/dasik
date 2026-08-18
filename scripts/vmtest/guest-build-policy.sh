#!/bin/bash
# package_policy.build_failure=warn-and-continue regression, run INSIDE the
# booted guest via `qemu.sh drive <image> guest-build-policy.sh BUILD-POLICY-DONE`
# after `qemu.sh install-driven config/vm-aur-closure.json`.
#
# Same broken chain as guest-aur-closure.sh (lib32-gst-libav → lib32-ffmpeg →
# lib32-libdav1d), but with the machine-wide continue policy: the apply must
# NOT abort — it warns, drops the broken root, installs the canary repo
# package, prints the end-of-domain summary, records a NON-partial generation,
# and the next plan still lists the dropped package (visible divergence).
#
# Anti-rot: if upstream fixes lib32-libdav1d, echo BUILD-POLICY-SKIP-BROKEN and
# assert only the no-op half.
#
# QEMU-only. Never run on a real host.
set -u

cd /root || { echo "BUILD-POLICY-DONE rc=91"; poweroff -f; }
export PYTHONPATH=/root/repo

GOOD=/root/repo/config/vm-aur-closure.json
POLICY=/root/vm-build-policy.json
APPLY_LOG=/root/build-policy-apply.log
CHAIN='lib32-gst-libav → lib32-ffmpeg → lib32-libdav1d'
rc=0

fail() {
    echo "BAD: $*"
    rc=1
}

# Manual user-net (installed guest has no network config).
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
    echo "BUILD-POLICY-DONE rc=92 (no DNS in guest: $iface)"
    [ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
fi

broken_upstream=1
info=$(curl -sf 'https://aur.archlinux.org/rpc/v5/info?arg[]=lib32-libdav1d' || echo '')
provides=$(curl -sf 'https://aur.archlinux.org/rpc/v5/search/lib32-libdav1d?by=provides' || echo '')
case "$info" in *'"resultcount":0'*) ;; *) broken_upstream=0;; esac
case "$provides" in *'"resultcount":0'*) ;; *) broken_upstream=0;; esac
if [ "$broken_upstream" -eq 0 ]; then
    echo "BUILD-POLICY-SKIP-BROKEN (upstream fixed lib32-libdav1d)"
fi

if [ "$broken_upstream" -eq 1 ]; then
    python - "$GOOD" "$POLICY" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
data["packages"] = data["packages"] + ["lib32-gst-libav", "tree"]
data["package_policy"] = {"build_failure": "warn-and-continue"}
Path(sys.argv[2]).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
    [ "$?" -eq 0 ] || fail "could not derive the policy config"

    echo "BUILD-POLICY: apply with warn-and-continue must NOT abort"
    python -m dasik apply "$POLICY" --target / --yes --no-log >"$APPLY_LOG" 2>&1
    apply_rc=$?
    cat "$APPLY_LOG"
    [ "$apply_rc" -eq 0 ] || fail "apply exited $apply_rc under warn-and-continue"
    grep -F "$CHAIN" "$APPLY_LOG" >/dev/null || fail "apply output lacks the chain warning"
    grep -F "not installed this apply" "$APPLY_LOG" >/dev/null \
        || fail "apply output lacks the end-of-domain summary"
    pacman -Q tree >/dev/null 2>&1 || fail "canary repo package did not install (apply stopped?)"
    if pacman -Q lib32-gst-libav >/dev/null 2>&1; then
        fail "the broken package installed?!"
    fi
    echo "BUILD-POLICY-APPLY=$apply_rc"

    echo "BUILD-POLICY: the generation must be COMPLETE, not partial"
    python -m dasik generations --target / | tail -3
    python -m dasik generations --target / | grep -F "partial" >/dev/null \
        && fail "latest generation is partial under warn-and-continue"

    echo "BUILD-POLICY: the next plan must still show the dropped package"
    plan_output="$(python -m dasik plan "$POLICY" --target / --no-log 2>&1)"
    printf '%s\n' "$plan_output"
    printf '%s\n' "$plan_output" | grep -F "lib32-gst-libav" >/dev/null \
        || fail "plan no longer shows the never-installed package"
fi

echo "BUILD-POLICY-DONE rc=$rc"
[ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
