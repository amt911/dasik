#!/bin/bash
# `dasik save` INSIDE the booted guest, where root and sudo are real.
#
# The unit tests drive it as the current user against a scratch repository.
# What only a machine can prove is the privilege half: sync runs as root, the
# commit must belong to the user who ran sudo, and the files the capture wrote
# must come back owned by them — `sudo dasik sync` leaves them root:root today.
#
# Ends with SAVE-DONE, then powers off.
set -x
cd /root/repo || { echo "SAVE-DONE rc=91"; poweroff -f; }
export PYTHONPATH=/root/repo
python -c 'import dasik' || { echo "SAVE-IMPORT BROKEN"; echo "SAVE-DONE rc=93"; poweroff -f; }

rc=0
U="test"                                 # the unprivileged user this config creates
HOME_DIR=$(getent passwd "$U" | cut -d: -f6)

echo "SAVE-A: a config repository owned by $U, with a bare remote"
install -d -o "$U" -g "$U" "$HOME_DIR/cfg" "$HOME_DIR/remote.git"
su - "$U" -c "git init -q -b main $HOME_DIR/cfg"
su - "$U" -c "git init -q --bare $HOME_DIR/remote.git"
su - "$U" -c "git -C $HOME_DIR/cfg config user.email t@example.com"
su - "$U" -c "git -C $HOME_DIR/cfg config user.name Test"
su - "$U" -c "git -C $HOME_DIR/cfg remote add origin $HOME_DIR/remote.git"
cp -r config/vm-etc-tree/. "$HOME_DIR/cfg/"
printf 'secrets/*\n*.log\n' > "$HOME_DIR/cfg/.gitignore"
install -d -o "$U" -g "$U" "$HOME_DIR/cfg/secrets"
printf 'a-secret\n' > "$HOME_DIR/cfg/secrets/token"
chown -R "$U:$U" "$HOME_DIR/cfg"
su - "$U" -c "git -C $HOME_DIR/cfg add -A && git -C $HOME_DIR/cfg commit -qm seed"

echo "SAVE-B: save as root via sudo, on behalf of $U"
cd "$HOME_DIR/cfg" || { echo "SAVE-DONE rc=92"; poweroff -f; }
# SUDO_USER is what a real `sudo dasik save` sets; the guest driver is already
# root, so it is set explicitly here rather than pretending to log in twice.
SUDO_USER="$U" python -m dasik save main.json --target / --no-log > /tmp/save.txt 2>&1
save_rc=$?
cat /tmp/save.txt
[ "$save_rc" = "0" ] || { echo "SAVE-RC BAD=$save_rc"; rc=1; }

echo "SAVE-C: the commit exists and belongs to $U"
su - "$U" -c "git -C $HOME_DIR/cfg --no-pager log --pretty='%an <%ae> %s' -2"
author=$(su - "$U" -c "git -C $HOME_DIR/cfg log -1 --pretty=%ae")
subject=$(su - "$U" -c "git -C $HOME_DIR/cfg log -1 --pretty=%s")
[ "$author" = "t@example.com" ] && echo "SAVE-AUTHOR ok" || { echo "SAVE-AUTHOR BAD=$author"; rc=1; }
case "$subject" in
    *": sync "*) echo "SAVE-SUBJECT ok ($subject)" ;;
    seed)        echo "SAVE-SUBJECT NO-COMMIT"; rc=1 ;;
    *)           echo "SAVE-SUBJECT unexpected ($subject)"; rc=1 ;;
esac

echo "SAVE-D: the files the capture wrote came back to $U"
owner=$(stat -c '%U' "$HOME_DIR/cfg/main.json")
[ "$owner" = "$U" ] && echo "SAVE-OWNER ok" || { echo "SAVE-OWNER BAD=$owner"; rc=1; }

echo "SAVE-E: the gitignored file was never staged"
if su - "$U" -c "git -C $HOME_DIR/cfg ls-files --error-unmatch secrets/token" \
        > /dev/null 2>&1; then
    echo "SAVE-IGNORED STAGED"; rc=1
else
    echo "SAVE-IGNORED ok (untracked)"
fi

echo "SAVE-F: it pushed, and the work tree is clean afterwards"
su - "$U" -c "git -C $HOME_DIR/remote.git --no-pager log --oneline main" | head -3
su - "$U" -c "git -C $HOME_DIR/remote.git log -1 --pretty=%s main" | grep -q ": sync " \
    && echo "SAVE-PUSH ok" || { echo "SAVE-PUSH MISSING"; rc=1; }
status=$(su - "$U" -c "git -C $HOME_DIR/cfg status --porcelain")
[ -z "$status" ] && echo "SAVE-CLEAN ok" || { echo "SAVE-CLEAN DIRTY: $status"; rc=1; }

echo "SAVE-G: a second save on a converged machine commits nothing"
before=$(su - "$U" -c "git -C $HOME_DIR/cfg rev-parse HEAD")
SUDO_USER="$U" python -m dasik save main.json --target / --no-log > /tmp/save2.txt 2>&1
cat /tmp/save2.txt
after=$(su - "$U" -c "git -C $HOME_DIR/cfg rev-parse HEAD")
[ "$before" = "$after" ] && echo "SAVE-IDEMPOTENT ok" || { echo "SAVE-IDEMPOTENT EXTRA-COMMIT"; rc=1; }

echo "SAVE-DONE rc=$rc"
sync
poweroff -f
