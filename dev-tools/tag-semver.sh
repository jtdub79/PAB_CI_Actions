#!/usr/bin/env bash
set -euo pipefail

tag="${1:-}"
if [[ -z "${tag}" ]]; then
  echo "Usage: $0 <tag>  (example: v1.3.0)" >&2
  exit 2
fi

# Safety: refuse if tag already exists locally or remotely
if git rev-parse "${tag}" >/dev/null 2>&1; then
  echo "Tag already exists locally: ${tag}" >&2
  exit 1
fi

git fetch --tags origin
if git rev-parse "refs/tags/${tag}" >/dev/null 2>&1; then
  echo "Tag already exists on origin: ${tag}" >&2
  exit 1
fi

git tag "${tag}"
git push origin "${tag}"

echo "Published immutable tag: ${tag}"