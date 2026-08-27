#!/bin/bash
# Torre AMD reinstall assertions, run INSIDE the booted (already-installed)
# guest via:
#
#   qemu.sh drive <image> guest-torre.sh TORRE-DONE
#
# config/vm-torre-reinstall.json rehearses torre-amd.json from the
# personal config repo: the desktop moved onto the ThinkPad's disk layout.
#
# The point of this run is HIBERNATION, which this machine has never had. Its
# old install carried a 2GiB swap with a RANDOM key and no resume=, so the
# resume path is entirely new ground here — a persistent-key LUKS swap, the
# derived rd.luks.name for it, and dracut's resume module. Beside that:
#
#   * amd_pstate=active on the boot entry (the desktop's scaling policy);
#   * amd-ucode as the microcode initrd, and nothing that is not on the ESP;
#   * nvidia-open-dkms installed but NOT in the initramfs — early KMS has no
#     access to NVreg_TemporaryFilePath, which is exactly what would make the
#     new hibernation lose the VRAM;
#   * the ten-subvolume layout and both LUKS volumes.
#
# Ends with a sync -> check -> plan round trip and a TORRE-DONE rc=<n> line.
# QEMU-only: reads the LIVE booted guest.
set -u

rc=0
fail() { echo "BAD: $*"; rc=1; }
ok()   { echo "ok: $*"; }

echo "TORRE-A: btrfs subvolumes"
subvols="@:/ @srv:/srv @snapshots:/.snapshots @home:/home
@var_lib_containers:/var/lib/containers @var_lib_libvirt:/var/lib/libvirt
@var_cache:/var/cache @var_log:/var/log @var_tmp:/var/tmp @var_spool:/var/spool"
for pair in $subvols; do
    name="${pair%%:*}"; mnt="${pair##*:}"
    opts="$(findmnt -no OPTIONS --target "$mnt" 2>/dev/null)"
    src="$(findmnt -no SOURCE  --target "$mnt" 2>/dev/null)"
    real="$(findmnt -no TARGET --target "$mnt" 2>/dev/null)"
    if [ "$real" != "$mnt" ]; then
        fail "$mnt is not a mountpoint (findmnt reports '$real') — $name never mounted"
        continue
    fi
    case "$opts" in
        *"subvol=/$name"*|*"subvol=$name"*) ;;
        *) fail "$mnt mounted from '$src' with '$opts' — expected subvol=$name";;
    esac
    case "$opts" in
        *compress-force=zstd:3*) ;;
        *) fail "$mnt lost compress-force=zstd:3 (opts: $opts)";;
    esac
done
[ "$rc" -eq 0 ] && ok "ten subvolumes mounted, all compressed"

echo "TORRE-B: both LUKS volumes open, swap attached in the initramfs"
for name in cryptroot cryptswap; do
    cryptsetup status "$name" >/dev/null 2>&1 \
        && ok "$name open" || fail "$name is NOT open"
done
grep -q 'x-initrd.attach' /etc/crypttab \
    && ok "crypttab carries x-initrd.attach" \
    || fail "no x-initrd.attach in /etc/crypttab — resume runs too late to matter"

echo "TORRE-C: the kernel knows where to resume from"
resume="$(cat /sys/power/resume 2>/dev/null)"
case "$resume" in
    ""|"0:0") fail "/sys/power/resume is '$resume' — hibernation writes an image nothing will read";;
    *)        ok "/sys/power/resume = $resume";;
esac
img="$(ls -1 /boot/initramfs-*.img 2>/dev/null | head -1)"
if [ -z "$img" ]; then
    fail "no initramfs found under /boot"
elif lsinitrd "$img" 2>/dev/null | grep -q 'resume'; then
    ok "resume present in $(basename "$img")"
else
    fail "$(basename "$img") carries no resume module"
fi

echo "TORRE-D: zram outranks the disk swap"
swapon --show=NAME,TYPE,PRIO --noheadings
zprio="$(swapon --show=NAME,PRIO --noheadings | awk '/zram/ {print $2}')"
dprio="$(swapon --show=NAME,PRIO --noheadings | awk '/mapper|dm-/ {print $2}')"
if [ -z "$zprio" ]; then
    fail "no zram device is swapped on"
elif [ -z "$dprio" ]; then
    fail "the LUKS swap partition is not swapped on — nothing to hibernate into"
elif [ "$zprio" -gt "$dprio" ]; then
    ok "zram prio $zprio > disk swap prio $dprio"
else
    fail "zram prio $zprio does NOT outrank disk swap $dprio"
fi

echo "TORRE-E: the boot entry — AMD scaling, the hand-set params, resume, derived LUKS tokens"
entry="$(grep -rl 'resume=' /boot/loader/entries/ 2>/dev/null | head -1)"
if [ -z "$entry" ]; then
    fail "no boot entry carries resume="
else
    cat "$entry"
    line="$(grep -h '^options' "$entry")"
    case "$line" in *"resume=/dev/mapper/cryptswap"*) ok "resume= present";;
        *) fail "resume= missing or wrong: $line";; esac
    case "$line" in *"amd_pstate=active"*) ok "amd_pstate=active present";;
        *) fail "amd_pstate=active missing — the cpu block is invisible: $line";; esac
    case "$line" in *"intel_pstate"*) fail "intel_pstate leaked onto an AMD config: $line";; esac
    case "$line" in *"usbcore.quirks=5566:0008:i"*) ok "the KVM-switch quirk survived";;
        *) fail "usbcore.quirks missing — the hand-set parameter was dropped";; esac
    case "$line" in *"zswap.enabled=0"*) ok "zswap.enabled=0 survived";;
        *) fail "zswap.enabled=0 missing";; esac
    case "$line" in *"sysrq_always_enabled=1"*) ok "sysrq present";;
        *) fail "sysrq_always_enabled=1 missing: $line";; esac
    n="$(printf '%s' "$line" | grep -o 'rd.luks.name=' | wc -l)"
    [ "$n" -eq 2 ] && ok "two rd.luks.name tokens derived" \
                   || fail "expected 2 derived rd.luks.name, found $n"
    case "$line" in *token-timeout=10s*) ok "token-timeout=10s carried through";;
        *) fail "token-timeout=10s missing from the entry";; esac

    echo "TORRE-F: the microcode initrd — AMD's, and nothing that is not on the ESP"
    grep -h '^initrd' "$entry"
    grep -qh '^initrd.*amd-ucode.img' "$entry" \
        && ok "amd-ucode.img listed" || fail "amd-ucode.img NOT listed"
    # The host is a Ryzen and so is the target, so unlike the GE63 rehearsal there
    # should be exactly ONE ucode image here. What matters (dasik #159, "Error
    # preparing initrd: Not found") is that sd-boot is never pointed at an image
    # the ESP does not carry — asserted just below.
    if grep -qh '^initrd.*intel-ucode.img' "$entry"; then
        fail "intel-ucode.img listed on an AMD machine"
    else
        ok "no intel-ucode.img"
    fi
    for ucode in $(grep -h '^initrd' "$entry" | awk '{print $2}' | grep ucode); do
        [ -f "/boot/${ucode#/}" ] && ok "$ucode exists on the ESP" \
                                 || fail "$ucode is listed but NOT on the ESP"
    done
fi

echo "TORRE-G: snapper took the root config"
if [ -f /etc/snapper/configs/root ]; then
    ok "/etc/snapper/configs/root exists"
    grep -q 'SUBVOLUME="/"' /etc/snapper/configs/root && ok "it points at /" \
        || fail "the snapper config does not point at /"
else
    fail "no snapper config for root"
fi

echo "TORRE-I: the cpu block's daemon"
systemctl is-enabled power-profiles-daemon.service >/dev/null 2>&1 \
    && ok "power-profiles-daemon.service enabled" \
    || fail "power-profiles-daemon.service is not enabled — the cpu block declares it"

echo "TORRE-J: the DKMS driver stack"
for p in nvidia-open-dkms nvidia-utils dkms linux-headers amd-ucode; do
    pacman -Q "$p" >/dev/null 2>&1 && ok "$p installed" || fail "$p is NOT installed"
done

echo "TORRE-K: no early KMS — nvidia must stay OUT of the initramfs"
if [ -z "$img" ]; then
    fail "no initramfs to inspect"
else
    # KERNEL MODULES only. nvidia-utils ships /usr/lib/modprobe.d/nvidia-utils.conf
    # (it blacklists nouveau) and dracut copies every modprobe.d file in, so a bare
    # `grep nvidia` matches a config file and calls it early KMS.
    kmods="$(lsinitrd "$img" 2>/dev/null | grep -Ei '(^|[ /])nvidia[^ /]*\.ko' || true)"
    other="$(lsinitrd "$img" 2>/dev/null | grep -i 'nvidia' || true)"
    if [ -n "$kmods" ]; then
        fail "$(basename "$img") carries nvidia KERNEL MODULES — early KMS breaks hibernation"
        echo "$kmods"
    else
        ok "no nvidia kernel module in the initramfs"
        [ -n "$other" ] && { echo "  (non-module nvidia files, harmless:)"; echo "$other" | sed 's/^/  /'; }
    fi
fi

echo "TORRE-L: nothing dasik-written in the NVIDIA config paths"
for f in /etc/modprobe.d/nvidia.conf /etc/modprobe.d/blacklist-nvidia.conf \
         /etc/X11/xorg.conf /etc/X11/xorg.conf.d/10-nvidia.conf; do
    if [ -e "$f" ] && grep -qs 'Managed by dasik' "$f"; then
        fail "$f is written by dasik — envycontrol owns it, this WILL show as drift"
    fi
done
ok "no dasik-managed file in envycontrol's territory"

echo "TORRE-M: sync -> check -> plan round trip against the live host"
cd /root/repo || { echo "TORRE-DONE rc=91"; poweroff -f; }
cp config/vm-torre-reinstall.json /tmp/torre-sync.json
python -m dasik plan config/vm-torre-reinstall.json --target / --no-log > /tmp/plan1.txt 2>&1
if grep -q 'No changes' /tmp/plan1.txt; then
    ok "plan after apply is silent (converged)"
else
    fail "plan is NOT silent after apply"; tail -30 /tmp/plan1.txt
fi
python -m dasik sync /tmp/torre-sync.json --target / --no-log > /tmp/sync.txt 2>&1 \
    && ok "sync captured the machine" || { fail "sync failed"; tail -20 /tmp/sync.txt; }
python -m dasik check /tmp/torre-sync.json > /tmp/check.txt 2>&1 \
    && ok "the captured config validates" || { fail "check REJECTED the capture"; cat /tmp/check.txt; }
python -m dasik plan /tmp/torre-sync.json --target / --no-log > /tmp/plan2.txt 2>&1
if grep -q 'No changes' /tmp/plan2.txt; then
    ok "sync -> plan is silent"
else
    fail "the captured config re-plans changes"; tail -40 /tmp/plan2.txt
fi
echo "--- what the capture kept of this machine ---"
python - <<'PYEOF'
import json
c = json.load(open('/tmp/torre-sync.json'))
for k in ('drivers', 'cpu', 'enable_microcode', 'sysrq', 'hardware_acceleration'):
    print(f"  {k}: {json.dumps(c.get(k))}")
PYEOF

echo "TORRE-DONE rc=$rc"
sync
[ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
