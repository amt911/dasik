#!/bin/bash
# `dasik save --home` against config-saver 3.5.0's restic store, INSIDE the guest.
#
# Why a guest and not a unit test: the unit tests mock `config-saver`. What only
# a machine shows is the real chain — sudo'd dasik, `su - test` running the real
# config-saver export (store password decrypted with the user's age key), a
# private export directory handed from root to the user, `gh` running as the
# user, the asset being a real encrypted archive, and nothing left behind.
# `gh` itself is a recording stub: the guest has no network and no GitHub login.
#
# Ends with RSAVE-DONE rc=N, then powers off.
set -x
cd /root/repo || { echo "RSAVE-DONE rc=91"; poweroff -f; }
export PYTHONPATH=/root/repo
python -c 'import dasik' || { echo "RSAVE-DONE rc=93"; poweroff -f; }

rc=0
U=test
H=$(getent passwd "$U" | cut -d: -f6)

echo "RSAVE-0: fresh state (the image persists between drives)"
rm -rf "$H/.config/config-saver" "$H/cfg" "$H/remote.git" "$H/key.txt" /tmp/published /tmp/gh.log
rm -rf "$H"/.cache/dasik-home-export-*
pacman -Q config-saver restic age || { echo "RSAVE-PKGS MISSING"; echo "RSAVE-DONE rc=94"; poweroff -f; }
su - "$U" -c 'config-saver --version'

echo "RSAVE-A: an encrypted document, one archive, then the restic store"
su - "$U" -c 'age-keygen -o ~/key.txt' 2>/dev/null
PUB=$(su - "$U" -c 'age-keygen -y ~/key.txt')
install -d -o "$U" -g "$U" "$H/.config/config-saver" "$H/.config/config-saver/configs.d"
cat > "$H/.config/config-saver/configs.d/dotfiles.yaml" <<YAML
directories:
  - source: \$HOME
    files: [.bashrc]
encrypt:
  method: age
  recipients: [$PUB]
YAML
chown "$U:$U" "$H/.config/config-saver/configs.d/dotfiles.yaml"
su - "$U" -c 'config-saver --compress'; echo "RSAVE-COMPRESS1 rc=$?"
su - "$U" -c "config-saver --restic-init --encrypt-to $PUB --identity ~/key.txt"; r=$?
echo "RSAVE-INIT rc=$r"; [ "$r" = 0 ] || rc=1
su - "$U" -c 'config-saver --migrate --delete-migrated'; r=$?
echo "RSAVE-MIGRATE rc=$r"; [ "$r" = 0 ] || rc=1
su - "$U" -c 'config-saver --compress'; echo "RSAVE-COMPRESS2 rc=$?"
left=$(find "$H/.config/config-saver/configs" -name '*.tar.gz*' 2>/dev/null | wc -l)
[ "$left" = 0 ] && echo "RSAVE-NO-TARS ok" || { echo "RSAVE-NO-TARS BAD=$left"; rc=1; }
su - "$U" -c 'config-saver --list'

echo "RSAVE-B: a recording gh (who ran it, with what) that keeps the assets"
install -d -m 1777 /tmp/published
cat > /usr/local/bin/gh <<'GH'
#!/bin/bash
echo "$(id -un) $*" >> /tmp/gh.log
[ "$1 $2" = "release view" ] && exit 1
for a in "$@"; do [ -f "$a" ] && cp "$a" /tmp/published/; done
exit 0
GH
chmod 755 /usr/local/bin/gh
touch /tmp/gh.log && chmod 666 /tmp/gh.log

echo "RSAVE-C: a config repository owned by $U (as guest-save.sh)"
install -d -o "$U" -g "$U" "$H/cfg" "$H/remote.git"
su - "$U" -c "git init -q -b main $H/cfg && git init -q --bare $H/remote.git"
su - "$U" -c "git -C $H/cfg config user.email t@example.com && git -C $H/cfg config user.name Test"
su - "$U" -c "git -C $H/cfg remote add origin $H/remote.git"
cp -r config/vm-etc-tree/. "$H/cfg/"
chown -R "$U:$U" "$H/cfg"
su - "$U" -c "git -C $H/cfg add -A && git -C $H/cfg commit -qm seed"

echo "RSAVE-D: sudo dasik save --home publishes the store's exports"
cd "$H/cfg" || { echo "RSAVE-DONE rc=92"; poweroff -f; }
SUDO_USER="$U" python -m dasik save main.json --target / --no-log --home me/home-archives > /tmp/rsave.txt 2>&1
save_rc=$?
cat /tmp/rsave.txt; cat /tmp/gh.log
[ "$save_rc" = 0 ] || { echo "RSAVE-RC BAD=$save_rc"; rc=1; }
grep -q "Published 1 archive" /tmp/rsave.txt && echo "RSAVE-PUBLISHED ok" || { echo "RSAVE-PUBLISHED MISSING"; rc=1; }
grep -q "^$U release create dasik-save-restic " /tmp/gh.log && echo "RSAVE-GH-AS-USER ok" || { echo "RSAVE-GH-AS-USER BAD"; rc=1; }
asset=$(ls /tmp/published/dotfiles-*.tar.gz.age 2>/dev/null | head -1)
if [ -n "$asset" ] && su - "$U" -c "age -d -i ~/key.txt $asset" | tar -tz | grep -q 'bashrc'; then
    echo "RSAVE-ASSET ok ($(basename "$asset"))"
else
    echo "RSAVE-ASSET BAD (${asset:-none})"; ls -la /tmp/published; rc=1
fi
leftover=$(ls -d "$H"/.cache/dasik-home-export-* 2>/dev/null | wc -l)
[ "$leftover" = 0 ] && echo "RSAVE-CLEANUP ok" || { echo "RSAVE-CLEANUP BAD=$leftover"; rc=1; }
stat -c 'RSAVE-CACHE-OWNER %U' "$H/.cache"

echo "RSAVE-E: without the age key the export fails — reported, save still succeeds"
mv "$H/key.txt" "$H/key.txt.away"
: > /tmp/gh.log
SUDO_USER="$U" python -m dasik save main.json --target / --no-log --home me/home-archives > /tmp/rsave2.txt 2>&1
save_rc=$?
cat /tmp/rsave2.txt
[ "$save_rc" = 0 ] && echo "RSAVE-NOKEY-RC ok" || { echo "RSAVE-NOKEY-RC BAD=$save_rc"; rc=1; }
grep -q "Not published:" /tmp/rsave2.txt && echo "RSAVE-NOKEY-REPORTED ok" || { echo "RSAVE-NOKEY-REPORTED MISSING"; rc=1; }
grep -q "release" /tmp/gh.log && { echo "RSAVE-NOKEY-PUBLISHED-ANYWAY"; rc=1; } || echo "RSAVE-NOKEY-NOTHING-UPLOADED ok"
mv "$H/key.txt.away" "$H/key.txt"
leftover=$(ls -d "$H"/.cache/dasik-home-export-* 2>/dev/null | wc -l)
[ "$leftover" = 0 ] && echo "RSAVE-NOKEY-CLEANUP ok" || { echo "RSAVE-NOKEY-CLEANUP BAD=$leftover"; rc=1; }

echo "RSAVE-DONE rc=$rc"
sync
poweroff -f
