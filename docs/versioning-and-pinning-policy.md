# PAB_CI_Actions versioning and consumer pinning policy

This document is the canonical reference for how `PAB_CI_Actions` releases are
tagged and how consuming repositories reference shared actions.

---

## Current release state

| Ref | Commit | Notes |
|-----|--------|-------|
| `v4.0.1` | `9d851058` | Current immutable release — never move |
| `v4` | `9d851058` | Floating major — points to same commit as `v4.0.1` |
| `v4.0.0` | `e716a386` | Previous immutable release — never move |

Verify at any time:

```bash
git show v4.0.1 --no-patch --format="%H %s"
git show v4     --no-patch --format="%H %s"
# Both must print the same commit hash.
```

---

## Immutable release tags

- Full semantic release tags such as `v4.0.0` and `v4.0.1` are permanent.
- An immutable release tag must never be moved after publication.
- Semantic releases identify reviewed, tested snapshots of the shared actions.
- Breaking changes require a new major version (`v5.0.0`, etc.).
- Release-candidate tags such as `v4.0.0-rc.1` are also immutable once published.

---

## Floating major tag

- `v4` is the supported floating major tag for compatible v4 releases.
- `v4` may advance forward to a newer reviewed v4 release.
- `v4` must always point to the same commit as an immutable semantic release tag.
- `v4` must not point directly to an arbitrary development commit or branch head.
- Do not create unnecessary intermediate floating tags such as `v4.0` unless a
  future documented need arises.

---

## Consumer pinning policy

### Ordinary CI workflows

Ordinary CI consumers — quality checks, linting, security scans, type checks,
test setup — may use the floating major tag:

```yaml
uses: jtdub79/PAB_CI_Actions/.github/actions/setup-uv@v4
uses: jtdub79/PAB_CI_Actions/.github/actions/job-quality@v4
uses: jtdub79/PAB_CI_Actions/.github/actions/job-security@v4
uses: jtdub79/PAB_CI_Actions/.github/actions/assert-repo-clean@v4
```

This automatically adopts backward-compatible v4 updates when `v4` is advanced.

### Release-critical, signing, deployment, and production-publication consumers

Consumers involved in production signing, R2 publication, server metadata
upserts, GitHub Release publication, or other irreversible release actions should
use an exact reviewed commit SHA when reproducibility and deliberate adoption are
required:

```yaml
# Pinned to the reviewed commit for v4.0.1.
uses: jtdub79/PAB_CI_Actions/.github/actions/r2-upload-object@9d851058b851778a54efd1b5dd3cc60b274f5df0
```

An exact semantic release tag may be used where the consumer deliberately wants a
fixed version and accepts that the tag is immutable:

```yaml
uses: jtdub79/PAB_CI_Actions/.github/actions/r2-upload-object@v4.0.1
```

### Repository-local actions

Repository-local actions use:

```yaml
uses: ./.github/actions/<action-name>
```

They are versioned by the consuming repository commit and do not need a separate
shared-action tag. Single-consumer release adapters should prefer this model.

### Prohibited references

Consumers must not use any of the following in production workflows:

- `@main`
- `@dev`
- `@latest`
- Release-candidate refs such as `@v4.0.0-rc.1`
- Any other floating branch or non-reviewed ref

---

## Advancing `v4` to a new release

The procedure for publishing a new v4 patch or minor release and advancing the
floating `v4` tag:

1. Merge and validate the intended `PAB_CI_Actions` change on `main`.
2. Run the action self-test workflow on that exact commit and confirm it passes.
3. Create a new immutable semantic tag:
   ```bash
   ./dev-tools/tag-semver.sh v4.0.2
   git push origin v4.0.2
   ```
4. Verify the immutable tag before advancing the floating tag:
   ```bash
   git show v4.0.2 --no-patch --format="%H %s"
   ```
5. Move `v4` to that exact same commit:
   ```bash
   git tag -f v4 v4.0.2
   git push --force-with-lease origin v4
   ```
6. Verify both refs resolve to the expected commit:
   ```bash
   git show v4    --no-patch --format="%H %s"
   git show v4.0.2 --no-patch --format="%H %s"
   # Hashes must match.
   ```
7. Review affected consumer CI after the floating tag advances.
8. Never move the immutable semantic tag after it is published.

---

## Rollback policy

### Immutable tags are never moved

A bad shared-action release is corrected by publishing a new patch release —
never by rewriting or moving the defective immutable tag.

### Normal path: new patch release

1. Fix the defect on a branch, merge to `main`, and validate.
2. Publish a new immutable patch tag (e.g., `v4.0.3`).
3. Push the new immutable tag.
4. Advance `v4` to the corrected release.
5. Consumers using `@v4` pick up the correction on their next run.

### Emergency path: move `v4` backward

If a regression requires an immediate rollback before a corrected patch is ready,
`v4` may be moved backward to the most recently validated immutable release. This
must be a deliberate, documented decision:

```bash
# Emergency rollback example only — do not run without confirming the target.
git tag -f v4 v4.0.1
git push --force-with-lease origin v4
```

Document the rollback reason and the targeted corrected release before executing.

### Production consumers pinned to a SHA

Consumers explicitly pinned to a commit SHA are unaffected by floating-tag
changes. They must be deliberately updated when the operator is ready to adopt
a corrected release.

---

## Change and review expectations

Changes affecting shared action behavior should include:

- Focused self-tests for the changed action, runnable without production secrets.
- Compatibility review for existing v4 consumers.
- Selection of an appropriate semantic version increment:
  - patch for fixes and non-breaking additions;
  - minor for new inputs or opt-in behavior changes;
  - major for breaking interface changes.
- Self-test passage before advancing `v4`.
- A concise changelog note or release description when behavior changes materially.

---

## Third-party action pinning (for consuming repositories)

Third-party actions involved in production signing, cloud authentication, or
artifact publication should use immutable commit SHAs with a nearby comment
identifying the verified upstream release:

```yaml
# azure/login v3.0.0
uses: azure/login@<sha>
```

See each consuming repository's `RELEASE_WORKFLOW.md` for its specific
third-party action pins and rehearsal requirements.
