#!/usr/bin/env bash
set -euo pipefail

major_tag="${1:-}"
target_tag="${2:-}"

if [[ -z "${major_tag}" || -z "${target_tag}" ]]; then
  echo "Usage: $0 <major-tag> <target-semver-tag>  (example: v1 v1.3.0)" >&2
  exit 2
fi

git fetch --tags origin

# Ensure target tag exists
if ! git rev-parse "refs/tags/${target_tag}" >/dev/null 2>&1; then
  echo "Target tag not found: ${target_tag}" >&2
  exit 1
fi

target_sha="$(git rev-list -n 1 "${target_tag}")"
echo "Target ${target_tag} -> ${target_sha}"

# Move local tag
git tag -f "${major_tag}" "${target_tag}"

# Force-push only the major tag
git push origin -f "${major_tag}"

echo "Moved ${major_tag} to ${target_tag}."