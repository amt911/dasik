#!/bin/bash
# ThinkPad P14s boot-chain assertions, run INSIDE the booted (already-installed)
# guest via:
#
#   DASIK_VM_LUKS_PASSWORD=hibpass \
#   qemu.sh drive <image> guest-p14s.sh P14S-DONE
#
# What only a booted guest can prove about config/vm-p14s-hibernate.json (and
# therefore about the real laptop it mirrors):
#
#   * the ten-subvolume layout actually MOUNTS, at the mountpoints declared,
#     carrying the compression the partition asked for;
#   * the swap LUKS volume is opened by the INITRAMFS (x-initrd.attach) rather
#     than by fstab, which is the difference between resuming and cold-booting;
#   * /sys/power/resume is a real device instead of 0:0 — the exact reading that
#     exposed the 2026-08-08 dracut bug, where hibernating "worked" every time
#     and silently lost the session;
#   * zram sits ABOVE the disk swap in priority, so the partition stays free for
#     the hibernation image;
#   * the boot entry carries resume= and BOTH rd.luks.name tokens, neither of
#     which is declared in the config — they are derived.
#
# Ends with a single P14S-DONE rc=<n> line the host driver greps.
# QEMU-only: reads the LIVE booted guest. Harmless, but pointless on a host.
set -u

rc=0
fail() { echo "BAD: $*"; rc=1; }
ok()   { echo "ok: $*"; }

echo "P14S-A: btrfs subvolumes"
# name -> mountpoint, exactly as config/vm-p14s-hibernate.json declares them.
subvols="@:/ @srv:/srv @snapshots:/.snapshots @home:/home
@var_lib_containers:/var/lib/containers @var_lib_libvirt:/var/lib/libvirt
@var_cache:/var/cache @var_log:/var/log @var_tmp:/var/tmp @var_spool:/var/spool"
for pair in $subvols; do
    name="${pair%%:*}"; mnt="${pair##*:}"
    opts="$(findmnt -no OPTIONS --target "$mnt" 2>/dev/null)"
    src="$(findmnt -no SOURCE  --target "$mnt" 2>/dev/null)"
    # --target on a path that is NOT itself a mountpoint silently reports the
    # parent, which would pass this test for a subvolume that never mounted.
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

echo "P14S-B: both LUKS volumes open"
for name in cryptroot cryptswap; do
    cryptsetup status "$name" >/dev/null 2>&1 \
        && ok "$name open" || fail "$name is NOT open"
done

echo "P14S-C: the swap is attached in the INITRAMFS, not by fstab"
grep -q 'x-initrd.attach' /etc/crypttab \
    && ok "crypttab carries x-initrd.attach" \
    || fail "no x-initrd.attach in /etc/crypttab — resume runs too late to matter"
cat /etc/crypttab

echo "P14S-D: the kernel knows where to resume from"
resume="$(cat /sys/power/resume 2>/dev/null)"
case "$resume" in
    ""|"0:0") fail "/sys/power/resume is '$resume' — hibernation writes an image nothing will read";;
    *)        ok "/sys/power/resume = $resume";;
esac

echo "P14S-E: the resume module is IN the initramfs"
img="$(ls -1 /boot/initramfs-*.img 2>/dev/null | head -1)"
if [ -z "$img" ]; then
    fail "no initramfs found under /boot"
elif lsinitrd "$img" 2>/dev/null | grep -q 'resume'; then
    ok "resume present in $(basename "$img")"
else
    fail "$(basename "$img") carries no resume module (dracut's 74resume check() fails in a chroot)"
fi

echo "P14S-F: zram outranks the disk swap"
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
    fail "zram prio $zprio does NOT outrank disk swap $dprio — the partition fills up first"
fi

echo "P14S-G: the boot entry derived what the config never declared"
entry="$(grep -rl 'resume=' /boot/loader/entries/ 2>/dev/null | head -1)"
if [ -z "$entry" ]; then
    fail "no boot entry carries resume="
else
    cat "$entry"
    line="$(grep -h '^options' "$entry")"
    case "$line" in *"resume=/dev/mapper/cryptswap"*) ok "resume= present";;
        *) fail "resume= missing or wrong: $line";; esac
    # NEITHER of these is in the config; KernelCmdlineAction derives one per
    # encrypted partition. The swap one is what makes resume reachable at all.
    n="$(printf '%s' "$line" | grep -o 'rd.luks.name=' | wc -l)"
    [ "$n" -eq 2 ] && ok "two rd.luks.name tokens derived" \
                   || fail "expected 2 derived rd.luks.name, found $n"
    case "$line" in *token-timeout=10s*) ok "token-timeout=10s carried through";;
        *) fail "token-timeout=10s missing from the entry";; esac
fi

echo "P14S-H: the logind drop-in landed"
f=/etc/systemd/logind.conf.d/10-dasik.conf
if [ -f "$f" ]; then
    grep -q '^HandleLidSwitch=suspend-then-hibernate' "$f" \
        && ok "lid switch set to suspend-then-hibernate" \
        || fail "$f exists but does not set HandleLidSwitch"
else
    fail "$f is missing"
fi

echo "P14S-I: the ESP is the 2GiB one, mounted at /boot"
findmnt -no SOURCE,FSTYPE,SIZE,AVAIL /boot || fail "/boot is not mounted"
esp_size="$(findmnt -bno SIZE /boot 2>/dev/null)"
# 2GiB minus FAT overhead; anything near 512MiB means the old size survived.
if [ -n "$esp_size" ] && [ "$esp_size" -gt 1900000000 ]; then
    ok "ESP is $((esp_size / 1024 / 1024)) MiB"
else
    fail "ESP is only $((${esp_size:-0} / 1024 / 1024)) MiB — expected ~2048"
fi

echo "P14S-DONE rc=$rc"
sync
[ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
