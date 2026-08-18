#!/bin/bash
# Does libvirt's `default` network actually autostart after an install?
#
# libvirt ships /etc/libvirt/qemu/networks/default.xml and an EMPTY autostart/
# directory, so the network is defined, inactive, and stays inactive across
# reboots. Every guest then fails with "network 'default' is not active". The
# symlink that fixes it had always been made by hand, which is why no reinstall
# and no `sync` ever carried it.
#
# The observable is `virsh net-info default` → "Autostart: yes", NOT whether the
# network is up: autostart is the thing the domain owns, and a nested guest with
# no KVM may well fail to bring the bridge up for unrelated reasons. The symlink
# is asserted separately so a libvirtd that refuses to start cannot hide a
# missing link. Ends with LIBVIRTNET-DONE, then powers off.
set -x
rc=0
cd /root/repo || { echo "LIBVIRTNET-DONE rc=91"; poweroff -f; }

D="python -m dasik"
L="--no-log"
C=config/vm-libvirt-net.json
LINK=/etc/libvirt/qemu/networks/autostart/default.xml
DEF=/etc/libvirt/qemu/networks/default.xml
echo "LIBVIRTNET: BEGIN"

echo "LIBVIRTNET-A: the install already made the symlink"
ls -la /etc/libvirt/qemu/networks/ /etc/libvirt/qemu/networks/autostart/
[ -L "$LINK" ] || { echo "LIBVIRTNET NO-SYMLINK"; rc=1; }
# Absolute, the way virsh writes it — it has to resolve on the BOOTED machine.
[ "$(readlink "$LINK")" = "$DEF" ] || { echo "LIBVIRTNET WRONG-TARGET=$(readlink "$LINK")"; rc=1; }
# The definition is still there: the domain links the network, it never owns it.
[ -f "$DEF" ] || { echo "LIBVIRTNET DEFINITION-GONE"; rc=1; }

echo "LIBVIRTNET-B: and libvirt agrees it will autostart"
systemctl start libvirtd.service; echo "LIBVIRTNET-LIBVIRTD-RC=$?"
virsh net-info default; echo "LIBVIRTNET-NETINFO-RC=$?"
if virsh net-info default 2>/dev/null | grep -qi '^Autostart: *yes'; then
    echo "LIBVIRTNET-AUTOSTART=yes"
else
    echo "LIBVIRTNET AUTOSTART-NOT-CONFIRMED"; rc=1
fi

echo "LIBVIRTNET-C: a converged machine plans nothing"
$D plan "$C" --target / $L 2>&1 | tee /tmp/plan-converged.txt; echo "LIBVIRTNET-PLAN-RC=$?"
grep -q 'libvirt_networks' /tmp/plan-converged.txt && { echo "LIBVIRTNET REPLANS-FOREVER"; rc=1; }

echo "LIBVIRTNET-D: sync captures the flag as its own block"
cp "$C" /tmp/captured.json
$D sync /tmp/captured.json --target / $L; echo "LIBVIRTNET-SYNC-RC=$?"
python -c 'import json;print("LIBVIRTNET-CAPTURED:",json.dumps(json.load(open("/tmp/captured.json")).get("kvm")))'
python - <<'PY' || rc=1
import json
kvm = json.load(open("/tmp/captured.json")).get("kvm") or {}
assert kvm.get("default_network") is True, f"not captured: {kvm}"
assert "install" not in kvm or kvm["install"] is False, f"sync spoke for install: {kvm}"
PY

echo "LIBVIRTNET-E: the capture validates and re-plans to nothing"
$D check /tmp/captured.json $L; echo "LIBVIRTNET-CAPCHECK-RC=$?"
$D plan /tmp/captured.json --target / $L 2>&1 | tee /tmp/plan-captured.txt; echo "LIBVIRTNET-CAPPLAN-RC=$?"
grep -q 'libvirt_networks' /tmp/plan-captured.txt && { echo "LIBVIRTNET CAPTURE-REPLANS"; rc=1; }

echo "LIBVIRTNET-F: dropping the block REMOVES the autostart dasik owns"
python - <<'PY'
import json
cfg = json.load(open("/root/repo/config/vm-libvirt-net.json"))
cfg.pop("kvm", None)
json.dump(cfg, open("/tmp/nokvm.json", "w"), indent=2)
PY
$D plan /tmp/nokvm.json --target / $L 2>&1 | tee /tmp/plan-drop.txt; echo "LIBVIRTNET-DROPPLAN-RC=$?"
grep -q 'remove default' /tmp/plan-drop.txt || { echo "LIBVIRTNET DROP-NOT-PLANNED"; rc=1; }
$D apply /tmp/nokvm.json --target / --yes $L; echo "LIBVIRTNET-DROPAPPLY-RC=$?"
[ -L "$LINK" ] && { echo "LIBVIRTNET LINK-SURVIVED-REMOVAL"; rc=1; }
# REMOVE un-autostarts. It must never `net-undefine`.
[ -f "$DEF" ] || { echo "LIBVIRTNET REMOVE-DESTROYED-THE-NETWORK"; rc=1; }
$D plan /tmp/nokvm.json --target / $L 2>&1 | tee /tmp/plan-drop2.txt
grep -q 'libvirt_networks' /tmp/plan-drop2.txt && { echo "LIBVIRTNET DROP-REPLANS"; rc=1; }

echo "LIBVIRTNET-G: and declaring it again puts it back"
$D plan "$C" --target / $L 2>&1 | tee /tmp/plan-back.txt
grep -q 'install default' /tmp/plan-back.txt || { echo "LIBVIRTNET READD-NOT-PLANNED"; rc=1; }
$D apply "$C" --target / --yes $L; echo "LIBVIRTNET-READDAPPLY-RC=$?"
[ -L "$LINK" ] || { echo "LIBVIRTNET READD-NO-SYMLINK"; rc=1; }
$D plan "$C" --target / $L 2>&1 | tee /tmp/plan-back2.txt
grep -q 'libvirt_networks' /tmp/plan-back2.txt && { echo "LIBVIRTNET READD-REPLANS"; rc=1; }

echo "LIBVIRTNET-DONE rc=$rc"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
