#!/bin/bash
# Three one-way streets, closed: sshd_config.d, smb.conf, and the non-public
# firewalld zones.
#
# Each was a domain `apply` wrote and `sync` could not read, so capturing the
# machine and re-applying the capture made it disappear. This drives the whole
# round trip against the LIVE host (target /), which is the only place the
# question can actually be answered.
#
# Ends with ETCZONES-DONE, then powers off.
set -x
cd /root/repo || { echo "ETCZONES-DONE rc=91"; poweroff -f; }

D="python -m dasik"
L="--no-log"
C=config/vm-sync-etc-zones.json
W=/root/etczones-work
mkdir -p "$W"
echo "ETCZONES: BEGIN"

# Here dasik runs from a bare source tree over 9p with no distribution
# installed, which is the case `dasik.__version__`'s fallback exists for: an
# unguarded importlib.metadata lookup would make `import dasik` itself raise,
# and every guest script would die on its first command.
echo "ETCZONES-0: importing dasik from an uninstalled source tree"
python -c "import dasik; print('ETCZONES-VERSION=' + dasik.__version__)"
echo "ETCZONES-IMPORT-RC=$?"

echo "ETCZONES-A: apply put the three things on the machine"
cat /etc/ssh/sshd_config.d/10-dasik.conf
cat /etc/samba/smb.conf
cat /etc/firewalld/zones/home.xml
cat /etc/firewalld/zones/public.xml

echo "ETCZONES-B: the plan right after the install must be SILENT"
$D plan "$C" --target / $L; echo "ETCZONES-PLAN-RC=$?"

echo "ETCZONES-C: apply then plan again — plan/apply/plan must end in silence"
$D apply "$C" --target / --yes $L; echo "ETCZONES-APPLY-RC=$?"
$D plan "$C" --target / $L; echo "ETCZONES-REPLAN-RC=$?"

# The real question: capture a machine from an EMPTY seed. A seed that already
# declares the files would prove nothing — it would just be echoing itself back.
echo "ETCZONES-D: sync from {} must DISCOVER all three"
echo '{}' > "$W/captured.json"
$D sync "$W/captured.json" --target / $L; echo "ETCZONES-SYNC-RC=$?"
python - <<'EOF'
import json
cfg = json.load(open('/root/etczones-work/captured.json'))
paths = {f['path']: f['content'] for f in cfg.get('files', [])}
print('ETCZONES-GOT-SSHD=%s' % ('/etc/ssh/sshd_config.d/10-dasik.conf' in paths))
print('ETCZONES-GOT-SMB=%s' % ('/etc/samba/smb.conf' in paths))
print('ETCZONES-SMB-HAS-SHARE=%s' % ('[shared]' in paths.get('/etc/samba/smb.conf', '')))
zones = (cfg.get('firewall') or {}).get('zones') or {}
print('ETCZONES-GOT-HOME-ZONE=%s' % ('home' in zones))
print('ETCZONES-HOME-SERVICES=%s' % (sorted(zones.get('home', {}).get('allowed_services', [])),))
print('ETCZONES-PUBLIC-NOT-IN-ZONES=%s' % ('public' not in zones))
EOF

echo "ETCZONES-E: the capture must validate and re-plan to nothing"
$D check "$W/captured.json"; echo "ETCZONES-CHECK-RC=$?"
$D plan "$W/captured.json" --target / $L; echo "ETCZONES-SYNCPLAN-RC=$?"

echo "ETCZONES-F: a hand edit on the machine is picked up by the next sync"
echo "X11Forwarding no" >> /etc/ssh/sshd_config.d/10-dasik.conf
printf '\n[extra]\n   path = /srv/extra\n' >> /etc/samba/smb.conf
$D sync "$W/captured.json" --target / $L; echo "ETCZONES-RESYNC-RC=$?"
python - <<'EOF'
import json
paths = {f['path']: f['content']
         for f in json.load(open('/root/etczones-work/captured.json')).get('files', [])}
print('ETCZONES-EDIT-SSHD=%s' % ('X11Forwarding no' in paths.get('/etc/ssh/sshd_config.d/10-dasik.conf', '')))
print('ETCZONES-EDIT-SMB=%s' % ('[extra]' in paths.get('/etc/samba/smb.conf', '')))
EOF

echo "ETCZONES-G: drop the home zone from the config — its file must GO"
python - <<'EOF'
import json
cfg = json.load(open('config/vm-sync-etc-zones.json'))
cfg['firewall'].pop('zones', None)
json.dump(cfg, open('/root/etczones-work/nozone.json', 'w'), indent=2)
EOF
$D plan "$W/nozone.json" --target / $L | grep -E "remove home"; echo "ETCZONES-DROP-PLANNED-RC=$?"
$D apply "$W/nozone.json" --target / --yes $L; echo "ETCZONES-DROP-APPLY-RC=$?"
test -e /etc/firewalld/zones/home.xml; echo "ETCZONES-HOME-FILE-STILL-THERE-RC=$?"
test -e /etc/firewalld/zones/public.xml; echo "ETCZONES-PUBLIC-SURVIVED-RC=$?"

echo "ETCZONES-DONE rc=0"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
