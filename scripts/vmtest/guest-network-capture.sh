#!/bin/bash
# A capture of a machine with no `network` block must validate (issue #196).
#
# `config/vm-minimal.json` declares a hostname and no network manager — a valid
# config, and the shape that produced `network: {"type": ""}`, which `dasik
# check` then refused. Ends with NET-DONE, then powers off.
set -x
cd /root/repo || { echo "NET-DONE rc=91"; poweroff -f; }

D="python -m dasik"
L="--no-log"
C=config/vm-minimal.json
echo "NET: BEGIN"

echo "NET-A: what this machine actually runs"
systemctl is-enabled NetworkManager.service
systemctl is-enabled systemd-networkd.service

echo "NET-B: sync"
cp "$C" /tmp/captured.json
$D sync /tmp/captured.json --target / $L; echo "NET-SYNC-RC=$?"
python -c 'import json;print("NET-CAPTURED:",json.dumps(json.load(open("/tmp/captured.json")).get("network")))'

echo "NET-C: the capture must validate and re-plan to nothing"
$D check /tmp/captured.json $L; echo "NET-CAPCHECK-RC=$?"
$D plan /tmp/captured.json --target / $L; echo "NET-CAPPLAN-RC=$?"

echo "NET-D: and a machine that DOES run one reports it"
systemctl enable systemd-networkd.service
cp "$C" /tmp/captured2.json
$D sync /tmp/captured2.json --target / $L; echo "NET-SYNC2-RC=$?"
python -c 'import json;print("NET-CAPTURED2:",json.dumps(json.load(open("/tmp/captured2.json")).get("network")))'
$D check /tmp/captured2.json $L; echo "NET-CAPCHECK2-RC=$?"

echo "NET-DONE rc=0"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
