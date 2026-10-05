#!/usr/bin/env bash
# Commit reference-store paths to main, surviving a racing push.
#
#   bash reference_commit.sh "<commit message>" <path> [<path> ...]
#
# Reference files are NOT the signals database, so there is nothing to merge:
# save the freshly written paths aside, reset to origin/main, put them back,
# commit only if they changed, push; five attempts, then fail LOUDLY (a quiet
# give-up would leave the pull on a runner that is about to be deleted).
set -euo pipefail
msg="$1"; shift
stash="${RUNNER_TEMP:-/tmp}/reference-commit-$$"
mkdir -p "$stash"
for p in "$@"; do
  [ -e "$p" ] || { echo "::error::$p was not written"; exit 1; }
  mkdir -p "$stash/$(dirname "$p")"
  cp -R "$p" "$stash/$(dirname "$p")/"
done
git config user.name  'talent-intel-bot'
git config user.email 'bot@users.noreply.github.com'
for attempt in 1 2 3 4 5; do
  git fetch -q origin main
  git reset -q --hard origin/main
  for p in "$@"; do
    mkdir -p "$(dirname "$p")"
    rm -rf "$p"
    cp -R "$stash/$p" "$p"
  done
  git add -- "$@"
  if git diff --staged --quiet; then
    echo "::notice::reference commit: nothing changed"
    exit 0
  fi
  git commit -q -m "$msg"
  if git push -q origin HEAD:main; then
    echo "::notice::reference commit pushed $(git rev-parse --short HEAD) ($msg)"
    exit 0
  fi
  sleep $((attempt * 7))
done
echo "::error::reference commit: push failed 5 times; the pull is lost with this runner"
exit 1
