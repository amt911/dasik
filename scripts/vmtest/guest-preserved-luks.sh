#!/bin/bash
# A reinstall that KEPT an encrypted data disk (config/vm-preserved-luks.json).
#
# The install ran with that disk closed (pre-preserved-luks.sh built it and
# closed it). Asserted here, on the booted machine, is what no unit test can
# show: the initramfs unlocked the kept disk, the INSTALLED fstab mounted both
# of its subvolumes with the old data intact, its two (fake) FIDO2 tokens are
# untouched, and a day-2 plan wants nothing for it — neither a keyslot nor a
# subvolume.
#
# Ends with KEPT-DONE rc=<n>, then powers off.
set -x
cd /root/repo || { echo "KEPT-DONE rc=91"; poweroff -f; }
rc=0
D="python -m dasik"
L="--no-log"                 # the 9p repo is read-only
export PYTHONPATH=/root/repo
UUID=5eed5eed-0000-4000-8000-00000000c0de
G=/home/tester/Games

# The kept disk is unlocked by the REAL system (not the initramfs), so the
# autologin shell can come up while that unlock still runs (token-timeout,
# then the key derivation). Wait on the state, not on a clock.
state=$(timeout 180 systemctl is-system-running --wait 2>/dev/null)
echo "KEPT-BOOT-STATE=$state"

echo "KEPT-A: the kept disk is open under the name the config gives it"
cryptsetup status games | head -5
[ "$(cryptsetup status games | awk '/device:/ {print $2}')" = /dev/vdb1 ] || \
    { echo "KEPT-A FAILED: games is not open on /dev/vdb1"; rc=1; }

echo "KEPT-B: both subvolumes are mounted where declared, from the mapping"
findmnt -rn -o TARGET,SOURCE,OPTIONS "$G" "$G/.snapshots"
findmnt -rn -o SOURCE "$G" | grep -qx '/dev/mapper/games\[/@\]' || \
    { echo "KEPT-B FAILED: $G is not games:@"; rc=1; }
findmnt -rn -o SOURCE "$G/.snapshots" | grep -qx '/dev/mapper/games\[/@snapshots\]' || \
    { echo "KEPT-B FAILED: $G/.snapshots is not games:@snapshots"; rc=1; }

echo "KEPT-C: the data written before the reinstall is there"
[ "$(cat "$G/MARKER" 2>/dev/null)" = kept-across-reinstall ] || \
    { echo "KEPT-C FAILED: the marker is missing"; rc=1; }

echo "KEPT-D: the INSTALLED fstab names the mapping, once per subvolume"
grep -n games /etc/fstab
[ "$(grep -c "^/dev/mapper/games $G " /etc/fstab)" -eq 1 ] || \
    { echo "KEPT-D FAILED: no single fstab line for $G"; rc=1; }
[ "$(grep -c "^/dev/mapper/games $G/.snapshots " /etc/fstab)" -eq 1 ] || \
    { echo "KEPT-D FAILED: no single fstab line for $G/.snapshots"; rc=1; }

echo "KEPT-D2: the kept disk is optional (nofail) in crypttab and fstab"
grep -n games /etc/crypttab
grep -q '^games UUID=5eed5eed-0000-4000-8000-00000000c0de none luks,fido2-device=auto,token-timeout=10s,nofail,timeout=1s$' /etc/crypttab || \
    { echo "KEPT-D2 FAILED: crypttab line lacks the unlock options (token, nofail, timeout)"; rc=1; }
[ "$(grep -c "^/dev/mapper/games .*,nofail 0 0$" /etc/fstab)" -eq 2 ] || \
    { echo "KEPT-D2 FAILED: fstab lines lack nofail"; rc=1; }

echo "KEPT-D3: the FIDO2 library systemd-cryptsetup dlopens is there, initramfs included"
pacman -Q libfido2 || { echo "KEPT-D3 FAILED: libfido2 not installed"; rc=1; }
lsinitrd /boot/initramfs-linux.img 2>/dev/null | grep -q 'libfido2\.so' || \
    { echo "KEPT-D3 FAILED: libfido2 is not in the initramfs"; rc=1; }
journalctl -b --no-pager | grep -q "libfido2.so.1' is not available" && \
    { echo "KEPT-D3 FAILED: systemd-cryptsetup could not load libfido2"; rc=1; }
systemctl status --no-pager systemd-cryptsetup@games.service | head -5

echo "KEPT-D4: the kept disk is off the kernel cmdline, the root is not"
cat /proc/cmdline
grep -q "$UUID" /proc/cmdline && { echo "KEPT-D4 FAILED: the kept disk is on the kernel cmdline"; rc=1; }
grep -q 'rd.luks.name=[^ ]*=cryptroot' /proc/cmdline || \
    { echo "KEPT-D4 FAILED: the root lost its rd.luks.name"; rc=1; }

echo "KEPT-D5: only the real system unlocks the kept disk, and it succeeds"
journalctl -b -o short-monotonic --no-pager -u systemd-cryptsetup@games.service
# The initramfs starts an unlock only for what the kernel cmdline (D4) or its
# OWN crypttab names; neither may name the kept disk. The journal of the unit
# is printed for the record, not counted (its wording is not ours).
lsinitrd -f etc/crypttab /boot/initramfs-linux.img
lsinitrd -f etc/crypttab /boot/initramfs-linux.img | grep -q '^games ' && \
    { echo "KEPT-D5 FAILED: the initramfs crypttab names the kept disk"; rc=1; }
res=$(systemctl show -p Result --value systemd-cryptsetup@games.service)
[ "$res" = success ] || { echo "KEPT-D5 FAILED: games unlock result $res"; rc=1; }

echo "KEPT-E: the header still carries its two tokens"
tokens=$(cryptsetup luksDump --disable-external-tokens /dev/vdb1 | grep -c 'systemd-fido2')
echo "KEPT-TOKENS=$tokens"
[ "$tokens" -eq 2 ] || { echo "KEPT-E FAILED: expected 2 fido2 tokens"; rc=1; }

echo "KEPT-F: day-2 plan wants nothing for the kept disk"
rm -rf /root/cfg && mkdir -p /root/cfg && cp config/vm-preserved-luks.json /root/cfg/main.json
python - <<'PY'
import json, pathlib
p = pathlib.Path("/root/cfg/main.json")
cfg = json.loads(p.read_text())
disk = cfg["disks"]["disks"][0]
disk["wipe_disk"] = False
for part in disk["partitions"]:
    part["format"] = False
p.write_text(json.dumps(cfg, indent=2))
PY
$D plan /root/cfg/main.json --target / $L > /tmp/plan.txt 2>&1
echo "KEPT-PLAN-RC=$?"
cat /tmp/plan.txt
grep -a 'games:\|Games:' /tmp/plan.txt && \
    { echo "KEPT-F FAILED: the plan touches the kept disk"; rc=1; }

echo "KEPT-G: sync -> check -> plan round trip stays silent for the kept disk"
cp /root/cfg/main.json /tmp/captured.json
$D sync /tmp/captured.json --target / $L > /tmp/sync.txt 2>&1
echo "KEPT-SYNC-RC=$?"; tail -5 /tmp/sync.txt
python - <<'PY'
import json, sys
cfg = json.load(open("/tmp/captured.json"))
parts = [p for d in cfg["disks"]["disks"] for p in d["partitions"]
         if p.get("luks_name") == "games"]
print("KEPT-CAPTURED=", json.dumps(parts))
ok = (len(parts) == 1 and parts[0].get("format") is False
      and parts[0].get("unlock_fido2") == 2
      and parts[0].get("luks_options") == ["token-timeout=10s", "nofail", "timeout=1s"]
      and sorted((s["name"], s["mountpoint"]) for s in parts[0].get("btrfs_subvolumes", []))
      == [("@", "/home/tester/Games"), ("@snapshots", "/home/tester/Games/.snapshots")])
sys.exit(0 if ok else 1)
PY
[ $? -eq 0 ] || { echo "KEPT-G FAILED: sync did not capture the kept disk as declared"; rc=1; }
$D check /tmp/captured.json > /tmp/check.txt 2>&1
grep -qa 'OK' /tmp/check.txt || { cat /tmp/check.txt; echo "KEPT-G FAILED: check rejects the capture"; rc=1; }
$D plan /tmp/captured.json --target / $L > /tmp/plan2.txt 2>&1
grep -a 'games:\|Games:' /tmp/plan2.txt && \
    { echo "KEPT-G FAILED: the re-plan of the capture touches the kept disk"; rc=1; }

echo "KEPT-DONE rc=$rc"
sync
sleep 2
poweroff -f
