#!/bin/bash
# Migration scenario (controller ruling on top of Task 9's brief): the
# author's three real machines get `config-saver` (and dasik, and two fonts)
# BUILT FROM GIT today via `package_sources`, and are about to switch to the
# `[amt911]` pacman repo instead. This machine was installed from
# config/vm-pacman-repo-migrate.json — config-saver built from a pinned
# pkgbuild-git source, NO `pacman.repositories`/`pacman.keys` declared at
# all. This script drives the actual switch: `plan`/`apply`
# config/vm-pacman-repo.json (the repo config, with NO package_sources) and
# asserts the ONLY thing that changes is the new key+repo — config-saver is
# already installed under that name and must not be reinstalled or removed,
# it simply gains a new provider.
#
# CONVENTION: every PACMIG-<step>-RC=0 line is a PASS (two lines are
# INVERTED on purpose and say so inline); rc() counts the failures so the
# final PACMIG-DONE rc=N is the verdict.
#
# Needs NETWORK: applying the switch does a real `pacman -Sy` against
# https://amt911.github.io/arch-packages/$arch and downloads the real
# amt911.gpg to verify against the declared fingerprint.
#
# The 9p share (/root/repo) is READ-ONLY: the destination config is driven
# from a /tmp copy.
set -x
cd /root/repo || { echo "PACMIG-DONE rc=91"; sync; poweroff -f; exit 91; }
export PYTHONPATH=/root/repo

D="python -m dasik"
L="--no-log"                  # the 9p repo is read-only; the log defaults to $PWD
TARGET=/tmp/target.json
FPR=6C6568CE34894645A23ABC44B5BD6F8F9023E53B

absent() { ! grep -q "$2" "$1"; }     # rc 0 when the pattern is NOT in the file
present() { grep -q "$2" "$1"; }      # rc 0 when it is

FAILS=0
rc() { local v=$?; [ "$v" -eq 0 ] || FAILS=$((FAILS + 1)); echo "$1-RC=$v"; }

echo "PACMIG: BEGIN (target / = the live booted host, installed via package_sources)"
cp config/vm-pacman-repo.json "$TARGET"

echo "PACMIG-A: what the git-pkgbuild install produced (before the switch)"
pacman -Qi config-saver > /tmp/qi-before.txt 2>&1; rc PACMIG-PKG-INSTALLED-BEFORE
cat /tmp/qi-before.txt
pacman -Sl amt911 > /tmp/sl-before.txt 2>&1
sl_before_rc=$?
echo "PACMIG-REPO-ABSENT-BEFORE-RC=$sl_before_rc"   # INVERTED: PASS means non-zero (no such repo yet)
[ "$sl_before_rc" -eq 0 ] && FAILS=$((FAILS + 1))
cat /tmp/sl-before.txt

echo "PACMIG-B: check the destination (repo) config"
$D check "$TARGET" $L; rc PACMIG-CHECK

echo "PACMIG-C: plan the switch -- only the key+repo, [packages] untouched"
$D plan "$TARGET" --target / $L > /tmp/planA.txt 2>&1; rc PACMIG-PLAN
echo "PACMIG: full plan (pre-switch) follows"
cat /tmp/planA.txt
present /tmp/planA.txt "create key:$FPR"; rc PACMIG-PLAN-CREATE-KEY
present /tmp/planA.txt 'create repo:amt911'; rc PACMIG-PLAN-CREATE-REPO
absent /tmp/planA.txt '\[packages\].*config-saver'; rc PACMIG-PLAN-NO-PACKAGE-CHANGE

echo "PACMIG-D: apply the switch"
$D apply "$TARGET" --target / --yes $L > /tmp/applyA.txt 2>&1; rc PACMIG-APPLY
cat /tmp/applyA.txt

echo "PACMIG-E: plan again -- silent, both for the new domain and for packages"
$D plan "$TARGET" --target / $L > /tmp/planB.txt 2>&1; rc PACMIG-REPLAN
echo "PACMIG: full replan (post-switch) follows"
cat /tmp/planB.txt
absent /tmp/planB.txt '\[pacman_repositories\]'; rc PACMIG-REPLAN-QUIET-REPOS
absent /tmp/planB.txt '\[packages\]'; rc PACMIG-REPLAN-QUIET-PACKAGES

echo "PACMIG-F: config-saver survived the switch, now served by the new repo"
pacman -Qi config-saver > /tmp/qi-after.txt 2>&1; rc PACMIG-PKG-INSTALLED-AFTER
cat /tmp/qi-after.txt
pacman -Sl amt911 > /tmp/sl-after.txt 2>&1; rc PACMIG-REPO-LISTED-AFTER
present /tmp/sl-after.txt 'config-saver'; rc PACMIG-REPO-HAS-PACKAGE
present /tmp/sl-after.txt 'installed'; rc PACMIG-PKG-MARKED-INSTALLED-AFTER

echo "PACMIG-G: generations records the new generation"
$D generations --target / $L > /tmp/gens.txt 2>&1; rc PACMIG-GENERATIONS
cat /tmp/gens.txt
present /var/lib/dasik/state.json 'pacman_repositories'; rc PACMIG-MANIFEST

echo "PACMIG: END with $FAILS failure(s)"
echo "PACMIG-DONE rc=$FAILS"
sync
poweroff -f
