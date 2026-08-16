#!/bin/bash
# Does a config that says NOTHING about /etc/hosts still get the block the Arch
# wiki recommends? nss-myhostname covers most software, but some reads the file
# directly — and without an entry it resolves the machine's own name over the
# network. The flag defaulted to off, so a config that said nothing got that.
set -x
rc=0
cat /etc/hosts
grep -q '^127\.0\.0\.1 localhost$'      /etc/hosts || { echo "HOSTS NO-V4-LOCALHOST"; rc=1; }
grep -q '^::1 localhost$'               /etc/hosts || { echo "HOSTS NO-V6-LOCALHOST"; rc=1; }
grep -q '^127\.0\.1\.1 dasik-hosts$'    /etc/hosts || { echo "HOSTS NO-HOSTNAME-LINE"; rc=1; }
# And the name really resolves locally rather than going out to a resolver.
getent hosts dasik-hosts || { echo "HOSTS NAME-DOES-NOT-RESOLVE"; rc=1; }
echo "HOSTS-DONE rc=$rc"
sync
poweroff -f
