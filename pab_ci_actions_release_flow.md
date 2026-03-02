# PAB_CI_Actions release flow

This document describes a safe, repeatable release process for your shared GitHub Actions repo
(`PAB_CI_Actions`) and includes bash scripts you can run for each step.

## Assumptions

- Default branch: `main`
- Actions repo remote: `origin`
- Current major line: `v1`
- You will publish:
  - Immutable semver tags: `v1.2.3` (never moved)
  - Floating major tag: `v1` (moves forward to latest `v1.x.x`)
- You develop on `main`, and consuming repos pin to `@v1` (not `@main`).

---

## One-time setup

### 0. Confirm repo state

Run:

```bash
git remote -v
git status
git branch --show-current
```

You should be on `main`, clean working tree.

### 1. Recommended repository protections (manual)

In GitHub UI for `PAB_CI_Actions`:

- Protect `main`:
  - Require PRs
  - Require status checks (your actions self-test workflow)
- Repo **Settings → Actions → General → Access**:
  - Allow usage from other repos that need it (org-wide if applicable)

---

## Day-to-day development flow (main branch)

### Step A: Create a feature branch

```bash
./tools/new-branch.sh feat/my-change
```

**Script: `tools/new-branch.sh`**
```bash
#!/usr/bin/env bash
set -euo pipefail

branch="${1:-}"
if [[ -z "${branch}" ]]; then
  echo "Usage: $0 <branch-name>" >&2
  exit 2
fi

git fetch origin
git checkout main
git pull --ff-only origin main
git checkout -b "${branch}"
git status
```

### Step B: Validate locally (optional but nice)

Run your local checks (adjust to your repo):

```bash
./tools/validate-local.sh
```

**Script: `tools/validate-local.sh`**
```bash
#!/usr/bin/env bash
set -euo pipefail

# Example: basic YAML sanity + show action metadata
# Customize as needed.

echo "==> Checking for action.yml files"
find . -name "action.yml" -o -name "action.yaml" | sed 's|^| - |'

echo "==> Optional: run yamllint if installed"
if command -v yamllint >/dev/null 2>&1; then
  yamllint .
else
  echo "yamllint not installed; skipping."
fi

echo "==> Done"
```

### Step C: Push branch and open PR

```bash
git push -u origin HEAD
```

Open PR in GitHub. Wait for status checks to pass, then merge.

---

## Release flow (publish tags safely)

### Step 1: Update `main` and verify clean working tree

```bash
./tools/release/prepare-main.sh
```

**Script: `tools/release/prepare-main.sh`**
```bash
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
```

### Step 2: Decide the version number

Choose one:

- Patch: `v1.2.4` (bugfix only)
- Minor: `v1.3.0` (backward compatible enhancements)
- Major: `v2.0.0` (breaking)

This doc assumes you are releasing a `v1.x.x`.

### Step 3: Create an immutable semver tag

```bash
./tools/release/tag-semver.sh v1.3.0
```

**Script: `tools/release/tag-semver.sh`**
```bash
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
```

### Step 4: Move the floating major tag (`v1`) to the new semver tag

After `v1.3.0` is published and validated:

```bash
./tools/release/move-major-tag.sh v1 v1.3.0
```

**Script: `tools/release/move-major-tag.sh`**
```bash
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
```

### Step 5: Verify what `v1` points to

```bash
./tools/release/verify-tags.sh v1 v1.3.0
```

**Script: `tools/release/verify-tags.sh`**
```bash
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
```

---

## Consuming repos update flow

### Step 1: Pin to `@v1` (recommended)

In consuming repos, reference:

- Composite action:

```yaml
- uses: jtdub79/PAB_CI_Actions/.github/actions/job-lint@v1
```

- Reusable workflow:

```yaml
jobs:
  ci:
    uses: jtdub79/PAB_CI_Actions/.github/workflows/python-ci.yml@v1
```

### Step 2: “Try main” workflow (optional, recommended)

Add a workflow_dispatch input that lets you test `main` before moving `v1`.

Example snippet for a consuming repo:

```yaml
on:
  workflow_dispatch:
    inputs:
      ci_actions_ref:
        description: "Ref for PAB_CI_Actions"
        required: true
        default: "v1"

jobs:
  lint:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: jtdub79/PAB_CI_Actions/.github/actions/job-lint@${{ inputs.ci_actions_ref }}
        with:
          python-version: "3.13"
```

---

## Operational rules

1. Consuming repos should use `@v1` (stable), not `@main`.
2. Only move `v1` after:
   - Actions repo self-tests are green
   - (Optionally) you’ve run the consuming repos’ “Try main” workflow dispatch
3. Never rewrite semver tags (`v1.3.0` stays forever).
4. Only rewrite floating tags (`v1`, later `v2`).

---

## Suggested directory layout for scripts

```
tools/
  new-branch.sh
  validate-local.sh
  release/
    prepare-main.sh
    tag-semver.sh
    move-major-tag.sh
    verify-tags.sh
```

Make scripts executable once:

```bash
chmod +x tools/*.sh tools/release/*.sh
```
