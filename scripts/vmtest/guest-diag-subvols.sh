#!/bin/bash
# Why does `sync` -> `plan` propose a rootflags the install never wrote?
#
# Dumps the exact findmnt rows dasik reads, and the subvolume block a sync
# produces from two different seeds — the config itself (what the megamix run
# did) and an empty one. Ends with DIAG-DONE.
set -x
cd /root/repo 2>/dev/null || true
D="python -m dasik --no-log"

echo "DIAG-FINDMNT-BEGIN"
findmnt -rn -t btrfs -o TARGET,SOURCE,OPTIONS
echo "DIAG-FINDMNT-END"

echo "DIAG-ROOT-OPTS: $(findmnt -no OPTIONS / 2>/dev/null)"

cp config/vm-megamix-encrypted.json /root/from-seed.json
$D sync /root/from-seed.json --target / >/dev/null 2>&1
echo "DIAG-SEED-SUBVOLS: $(python -c "
import json
p=[x for x in json.load(open('/root/from-seed.json'))['disks']['disks'][0]['partitions'] if x.get('btrfs_subvolumes')][0]
print('part=', p.get('mount_options'), 'subvols=', [(s['name'], s.get('mount_options')) for s in p['btrfs_subvolumes']])" 2>&1)"

echo '{}' > /root/from-empty.json
$D sync /root/from-empty.json --target / >/dev/null 2>&1
echo "DIAG-EMPTY-SUBVOLS: $(python -c "
import json
d=json.load(open('/root/from-empty.json'))
p=[x for x in d.get('disks',{}).get('disks',[{}])[0].get('partitions',[]) if x.get('btrfs_subvolumes')]
print('part=', p[0].get('mount_options'), 'subvols=', [(s['name'], s.get('mount_options')) for s in p[0]['btrfs_subvolumes']]) if p else print('NO BTRFS CAPTURED')" 2>&1)"

echo "DIAG-PLAN-FROM-SEED-BEGIN"
$D plan /root/from-seed.json --target /
echo "DIAG-PLAN-FROM-SEED-END"

echo "DIAG-DONE rc=0"
sync
sleep 2
poweroff -f
