#!/usr/bin/env bash
# Publish docs/wiki/ to the GitHub wiki repository.
#
# The wiki source is versioned in THIS repo (docs/wiki/) so a documentation
# change goes through review like any other change; the GitHub wiki is a
# rendering of it. Two mechanical differences the script handles:
#
#   * the wiki's landing page must be called Home.md, ours is README.md (which
#     is what GitHub renders when you browse docs/wiki/ in the repo);
#   * wiki links resolve WITHOUT the .md suffix ([CLI](CLI)), while in-repo
#     links need it ([CLI](CLI.md)). The source keeps the .md form so both
#     views work; the suffix is stripped on publish.
#
# Usage:
#   scripts/publish-wiki.sh [--dry-run] [--remote URL] [--message MSG]
#
# The wiki repository must already exist. GitHub creates it the first time a
# page is saved from the web UI (github.com/<owner>/<repo>/wiki), so if the
# clone fails with "Repository not found", create one page there first.
set -euo pipefail

cd "$(dirname "$0")/.."

SRC="docs/wiki"
REMOTE="${DASIK_WIKI_REMOTE:-git@github.com:amt911/dasik.wiki.git}"
MESSAGE="docs: sync wiki from $(git rev-parse --short HEAD)"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)  DRY_RUN=1; shift ;;
    --remote)   REMOTE="$2"; shift 2 ;;
    --message)  MESSAGE="$2"; shift 2 ;;
    -h|--help)  sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -d "$SRC" ]] || { echo "error: $SRC not found (run from the repo)" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
STAGE="$WORK/stage"
mkdir -p "$STAGE"

# --- transform ------------------------------------------------------------ #
# README.md -> Home.md; every other page keeps its name. Inter-page links lose
# the .md suffix, and a link to README.md becomes a link to Home.
for path in "$SRC"/*.md; do
  base="$(basename "$path")"
  out="$base"
  [[ "$base" == "README.md" ]] && out="Home.md"
  sed -E \
    -e 's/\]\(README\.md(#[^)]*)?\)/](Home\1)/g' \
    -e 's/\]\(([A-Za-z0-9_-]+)\.md(#[^)]*)?\)/](\1\2)/g' \
    "$path" > "$STAGE/$out"
done

echo "Pages to publish:"
for f in "$STAGE"/*.md; do
  printf '  %-24s %5s lines\n' "$(basename "$f")" "$(wc -l < "$f")"
done

if [[ $DRY_RUN -eq 1 ]]; then
  echo
  echo "--dry-run: nothing pushed. Rendered pages are in $STAGE (removed on exit)."
  echo "Remote would be: $REMOTE"
  exit 0
fi

# --- publish -------------------------------------------------------------- #
CLONE="$WORK/wiki"
if ! git clone --quiet "$REMOTE" "$CLONE"; then
  cat >&2 <<EOF
error: could not clone $REMOTE

If it says "Repository not found", the wiki has never been initialised. Open
  https://github.com/amt911/dasik/wiki
and save any page once; GitHub creates the repository at that moment.
EOF
  exit 1
fi

# Remove pages that no longer exist in the source, then copy the new set.
find "$CLONE" -maxdepth 1 -name '*.md' -delete
cp "$STAGE"/*.md "$CLONE/"

cd "$CLONE"
git add -A
if git diff --cached --quiet; then
  echo "Wiki already up to date — nothing to push."
  exit 0
fi
git commit --quiet -m "$MESSAGE"
git push --quiet origin HEAD
echo "Published $(ls "$STAGE"/*.md | wc -l) pages to $REMOTE"
