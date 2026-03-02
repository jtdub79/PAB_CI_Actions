#!/usr/bin/env bash
set -euo pipefail

git fetch origin
git checkout main
git pull --ff-only origin main

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree not clean. Commit or stash changes first." >&2
  git status --porcelain
  exit 1
fi

echo "OK: main is up-to-date and clean."