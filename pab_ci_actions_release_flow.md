# PAB_CI_Actions v4 release flow

This document describes the current release process for the shared action repository.

## Current major line

- Current planned major: `v4`.
- Immutable tags: `v4.0.0`, `v4.0.1`, and later `v4.x.y` tags must never be rewritten.
- Floating major tag: `v4` may move only after validation.
- No `v4` tag is published by implementation pass 1.

## Required validation before tagging

1. Merge the `workflows -> main` PR after review.
2. Run the self-test workflow on `main`.
3. Create an immutable pre-release candidate tag such as `v4.0.0` only after self-tests pass.
4. Validate consumers in order:
   1. PAB-Shared.
   2. PABLicenseServer.
   3. PrecisionArrowBuilder validation.
   4. PrecisionArrowBuilder production publishing.
5. Move floating `v4` only after the candidate is validated.

## Rollback

Rollback uses the floating major only: move `v4` back to the last validated immutable `v4.x.y` tag. Never force-push an immutable semver tag.

## Scripts

The `dev-tools` scripts remain low-level tag helpers. Use them only after the validation gates above are satisfied:

```bash
./dev-tools/tag-semver.sh v4.0.0
./dev-tools/move-major-tag.sh v4 v4.0.0
./dev-tools/verify-tags.sh v4 v4.0.0
```

## Older majors

`v1`, `v2`, and `v3` remain available for existing consumers. After `v4` is published, new work should target `v4` and older majors are deprecated except for emergency compatibility fixes.
