#!/bin/bash
# MSI GE63 Raider 8SG assertions, run INSIDE the booted (already-installed)
# guest via:
#
#   qemu.sh drive <image> guest-ge63.sh GE63-DONE
#
# config/vm-ge63.json is the VM rehearsal of msi-ge63.json in the personal
# config repo. The boot-chain half (subvolumes, both LUKS volumes, resume) is
# the same ground guest-p14s.sh covers, because the two laptops declare the same
# disk; what is NEW here is everything that makes this machine Intel with a
# hybrid NVIDIA GPU:
#
#   * intel_pstate=active on the boot entry, and sysrq beside it;
#   * ONE microcode initrd — intel-ucode.img and NOT amd-ucode.img. Listing a
#     ucode image that is not on the ESP is how systemd-boot got to answer
#     "Error preparing initrd: Not found" (dasik issue #159);
#   * the snd_hda_intel quirk file, which is the difference between working
#     speakers and near-silent ones on this chassis;
#   * a /etc/pam.d/sudo with pam_u2f and NO pam_fprintd — this laptop has no
#     reader, and the ThinkPad's file would silently ask for a finger nothing
#     can read;
#   * the hybrid driver set installing TOGETHER (intel mesa/vulkan + nvidia-open
#     + the lib32 halves + the VA-API/VDPAU bridges) without pacman conflicts;
#   * NO nvidia module inside the initramfs. Early KMS has no access to
#     NVreg_TemporaryFilePath, so it silently breaks hibernation — which is the
#     one thing this laptop must not lose;
#   * NO dasik-written /etc/modprobe.d/nvidia.conf: that path belongs to
#     envycontrol, and declaring it is what would turn every mode switch into
#     drift in `dasik plan`.
#
# Ends with a sync -> check -> plan round trip against the LIVE host, and a
# single GE63-DONE rc=<n> line the host driver greps.
#
# What this CANNOT prove, and is asserted nowhere below: intel_pstate BINDING
# (the host is a Ryzen, so the guest CPU is AMD and the driver cannot load), and
# anything the NVIDIA driver does at runtime (there is no GPU behind it).
# QEMU-only: reads the LIVE booted guest.
set -u

rc=0
fail() { echo "BAD: $*"; rc=1; }
ok()   { echo "ok: $*"; }

echo "GE63-A: btrfs subvolumes"
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

echo "GE63-B: both LUKS volumes open, swap attached in the initramfs"
for name in cryptroot cryptswap; do
    cryptsetup status "$name" >/dev/null 2>&1 \
        && ok "$name open" || fail "$name is NOT open"
done
grep -q 'x-initrd.attach' /etc/crypttab \
    && ok "crypttab carries x-initrd.attach" \
    || fail "no x-initrd.attach in /etc/crypttab — resume runs too late to matter"

echo "GE63-C: the kernel knows where to resume from"
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

echo "GE63-D: zram outranks the disk swap"
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

echo "GE63-E: the boot entry — Intel scaling, sysrq, resume, derived LUKS tokens"
entry="$(grep -rl 'resume=' /boot/loader/entries/ 2>/dev/null | head -1)"
if [ -z "$entry" ]; then
    fail "no boot entry carries resume="
else
    cat "$entry"
    line="$(grep -h '^options' "$entry")"
    case "$line" in *"resume=/dev/mapper/cryptswap"*) ok "resume= present";;
        *) fail "resume= missing or wrong: $line";; esac
    case "$line" in *"intel_pstate=active"*) ok "intel_pstate=active present";;
        *) fail "intel_pstate=active missing — the cpu block is invisible: $line";; esac
    case "$line" in *"amd_pstate"*) fail "amd_pstate leaked onto an Intel config: $line";; esac
    case "$line" in *"sysrq_always_enabled=1"*) ok "sysrq present";;
        *) fail "sysrq_always_enabled=1 missing: $line";; esac
    n="$(printf '%s' "$line" | grep -o 'rd.luks.name=' | wc -l)"
    [ "$n" -eq 2 ] && ok "two rd.luks.name tokens derived" \
                   || fail "expected 2 derived rd.luks.name, found $n"
    case "$line" in *token-timeout=10s*) ok "token-timeout=10s carried through";;
        *) fail "token-timeout=10s missing from the entry";; esac

    echo "GE63-F: the microcode initrd — Intel's, and nothing that is not on the ESP"
    grep -h '^initrd' "$entry"
    grep -qh '^initrd.*intel-ucode.img' "$entry" \
        && ok "intel-ucode.img listed" || fail "intel-ucode.img NOT listed"
    # amd-ucode appears HERE and not on the real laptop: enable_microcode installs
    # the ucode of the CPU it sees, and this guest runs -cpu host on a Ryzen. What
    # matters (dasik #159, "Error preparing initrd: Not found") is that sd-boot is
    # never pointed at an image the ESP does not carry — asserted just below.
    if grep -qh '^initrd.*amd-ucode.img' "$entry"; then
        ok "amd-ucode.img also listed — expected: the guest CPU is AMD (host passthrough)"
    fi
    for ucode in $(grep -h '^initrd' "$entry" | awk '{print $2}' | grep ucode); do
        [ -f "/boot/${ucode#/}" ] && ok "$ucode exists on the ESP" \
                                 || fail "$ucode is listed but NOT on the ESP"
    done
fi

echo "GE63-G: the MSI audio quirk"
f=/etc/modprobe.d/msi-audio.conf
if [ -f "$f" ]; then
    grep -q '^options snd_hda_intel model=lenovo-y530' "$f" \
        && ok "snd_hda_intel model=lenovo-y530" \
        || { fail "$f does not set the quirk"; cat "$f"; }
else
    fail "$f is missing — speakers stay thin and headphones muted"
fi

echo "GE63-H: sudo takes the YubiKey, and never asks for a finger"
f=/etc/pam.d/sudo
if [ -f "$f" ]; then
    grep -qE '^[[:space:]]*auth.*pam_u2f\.so' "$f" \
        && ok "pam_u2f present" || fail "pam_u2f missing from $f"
    # ^auth only: the file's own comment explains why there is no fingerprint
    # line, and a plain `grep pam_fprintd` matches that comment.
    if grep -qE '^[[:space:]]*auth.*pam_fprintd' "$f"; then
        fail "pam_fprintd is an active auth line in $f — this laptop has no reader"
    else
        ok "no active pam_fprintd line"
    fi
    pacman -Q fprintd >/dev/null 2>&1 && fail "fprintd is installed on a machine with no reader" \
                                      || ok "fprintd not installed"
else
    fail "$f is missing"
fi

echo "GE63-I: thermald is enabled"
systemctl is-enabled thermald.service >/dev/null 2>&1 \
    && ok "thermald.service enabled" \
    || fail "thermald.service is not enabled (a 45W Coffee Lake H needs it)"
systemctl is-enabled power-profiles-daemon.service >/dev/null 2>&1 \
    && ok "power-profiles-daemon.service enabled" \
    || fail "power-profiles-daemon.service is not enabled — the cpu block declares it"

echo "GE63-J: the hybrid driver set installed together"
for p in mesa vulkan-intel intel-media-driver nvidia-open nvidia-utils nvidia-settings \
         lib32-mesa lib32-vulkan-intel lib32-nvidia-utils libva-nvidia-driver nvtop \
         intel-gpu-tools libvdpau-va-gl libva-utils vdpauinfo; do
    pacman -Q "$p" >/dev/null 2>&1 && ok "$p installed" || fail "$p is NOT installed"
done

echo "GE63-K: no early KMS — nvidia must stay OUT of the initramfs"
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

echo "GE63-L: envycontrol's files are NOT dasik's"
for f in /etc/modprobe.d/nvidia.conf /etc/modprobe.d/blacklist-nvidia.conf \
         /etc/X11/xorg.conf /etc/X11/xorg.conf.d/10-nvidia.conf; do
    if [ -e "$f" ] && grep -qs 'Managed by dasik' "$f"; then
        fail "$f is written by dasik — envycontrol owns it, this WILL show as drift"
    fi
done
ok "no dasik-managed file in envycontrol's territory"

echo "GE63-M: sync -> check -> plan round trip against the live host"
cd /root/repo || { echo "GE63-DONE rc=91"; poweroff -f; }
cp config/vm-ge63.json /tmp/ge63-sync.json
python -m dasik plan config/vm-ge63.json --target / --no-log > /tmp/plan1.txt 2>&1
if grep -q 'No changes' /tmp/plan1.txt; then
    ok "plan after apply is silent (converged)"
else
    fail "plan is NOT silent after apply"; tail -30 /tmp/plan1.txt
fi
python -m dasik sync /tmp/ge63-sync.json --target / --no-log > /tmp/sync.txt 2>&1 \
    && ok "sync captured the machine" || { fail "sync failed"; tail -20 /tmp/sync.txt; }
python -m dasik check /tmp/ge63-sync.json > /tmp/check.txt 2>&1 \
    && ok "the captured config validates" || { fail "check REJECTED the capture"; cat /tmp/check.txt; }
python -m dasik plan /tmp/ge63-sync.json --target / --no-log > /tmp/plan2.txt 2>&1
if grep -q 'No changes' /tmp/plan2.txt; then
    ok "sync -> plan is silent"
else
    fail "the captured config re-plans changes"; tail -40 /tmp/plan2.txt
fi
echo "--- what the capture kept of this machine ---"
python - <<'PYEOF'
import json
c = json.load(open('/tmp/ge63-sync.json'))
for k in ('drivers', 'cpu', 'enable_microcode', 'sysrq', 'hardware_acceleration'):
    print(f"  {k}: {json.dumps(c.get(k))}")
PYEOF

echo "GE63-DONE rc=$rc"
sync
[ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
