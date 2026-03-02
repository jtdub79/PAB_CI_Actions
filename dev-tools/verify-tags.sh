#!/usr/bin/env bash
set -euo pipefail

major_tag="${1:-v1}"
semver_tag="${2:-}"

git fetch --tags origin

echo "==> Local tags"
git show -s --format="%D%n%H%n%s%n" "${major_tag}"
if [[ -n "${semver_tag}" ]]; then
  git show -s --format="%D%n%H%n%s%n" "${semver_tag}"
fi

echo "==> Remote tag refs"
git ls-remote --tags origin | grep -E "refs/tags/(${major_tag}|${semver_tag})$" || true