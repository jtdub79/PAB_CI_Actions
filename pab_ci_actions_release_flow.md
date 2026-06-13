# PAB_CI_Actions v4 release flow

This document defines the release process for the `PAB_CI_Actions` v4 line.


## Current v4 status

The initial v4 release has completed the release-candidate validation path.

The immutable final tag is:

```text
v4.0.0
```

It points to the same validated commit as `v4.0.0-rc.2`. Current consumer migrations should use the immutable final tag:

```yaml
uses: jtdub79/PAB_CI_Actions/.github/actions/setup-uv@v4.0.0
```

Do not update active consumers from `v4.0.0-rc.2` to the floating `v4` tag as part of the final-tag migration. Promote `v4` separately only after the final tag and all intended consumers are verified.

The release-candidate references below are retained as the historical and future process for validating new major or materially changed action releases.

## Release goals

The v4 release process must:

* validate the action repository before any consumer adopts v4
* validate consumers against an immutable release-candidate tag
* never require consumers to reference `main` or the mutable `v4` tag during validation
* never rewrite immutable semantic-version tags
* move the floating `v4` tag only after all required consumers pass
* preserve a clear rollback point

## Tag model

### Release-candidate tags

Use immutable prerelease tags while validating the v4 contract:

* `v4.0.0-rc.1`
* `v4.0.0-rc.2`
* later `v4.0.0-rc.N` tags as needed

Release-candidate tags are immutable. Never move or rewrite them.

Create a new release-candidate tag whenever code changes after a candidate was published.

### Final semantic-version tags

Use immutable final release tags for validated releases:

* `v4.0.0`
* `v4.0.1`
* later `v4.x.y` tags

Final semantic-version tags are immutable. Never move or rewrite them.

### Floating major tag

`v4` is a mutable convenience tag for validated consumers.

Move `v4` only after:

1. the final immutable release tag exists
2. repository self-tests pass for that exact commit
3. required consumer validation passes
4. the release commit is approved for general adoption

Never use `v4` as the initial validation target.

## Implementation and validation sequence

### 1. Complete the v4 foundation

Implement the v4 action contract on the `workflows` branch.

Before opening or updating the pull request, run the complete local validation suite.

Do not create any v4 tag during initial implementation.

### 2. Merge the action repository pull request

Open and review:

`workflows -> main`

Merge only after:

* local validation passes
* GitHub-hosted self-tests pass
* required review comments are resolved
* the v4 contract and documentation are internally consistent

### 3. Validate `main`

After merge, run the action self-test workflow on `main`.

Required hosted validation should include:

* Ubuntu self-tests
* Windows smoke tests for cross-platform actions
* action metadata validation
* workflow validation
* quality checks
* security checks
* mocked R2 and metadata-submission behavior

Do not tag a failing or partially validated commit.

### 4. Create the first immutable release candidate

After `main` passes, create:

```bash
./dev-tools/tag-semver.sh v4.0.0-rc.1
```

Push the immutable tag.

Do not create or move the floating `v4` tag yet.

### 5. Validate consumers against the immutable candidate

During release-candidate validation, consumers must reference the exact candidate tag, for example:

```yaml
uses: jtdub79/PAB_CI_Actions/.github/actions/setup-uv@v4.0.0-rc.1
```

After final promotion, active consumers should migrate from the validated candidate to the exact immutable final release:

```yaml
uses: jtdub79/PAB_CI_Actions/.github/actions/setup-uv@v4.0.0
```

Validate consumers in this order:

1. `PAB-Shared`
2. `PABLicenseServer`
3. `PrecisionArrowBuilder` validation workflows
4. `PrecisionArrowBuilder` production-release mechanics

Each consumer must validate against the same immutable candidate unless a defect requires a new candidate.

### 6. Create a new candidate when fixes are required

If candidate validation finds a defect:

1. fix the defect on `workflows`
2. merge the fix into `main`
3. rerun the complete self-test suite
4. create the next immutable candidate

Example:

```bash
./dev-tools/tag-semver.sh v4.0.0-rc.2
```

Do not rewrite `v4.0.0-rc.1`.

Update consumer branches to the new exact candidate tag and repeat validation.

### 7. Perform non-production integration tests

Before final v4 publication, validate cloud-facing actions against non-production resources.

At minimum:

* upload a harmless object to a dedicated R2 test bucket or prefix
* verify public bytes and SHA-256
* verify same-byte idempotent upload behavior
* verify conflicting bytes are rejected
* submit release metadata to a local or staging server
* verify Basic authentication behavior
* verify invalid metadata and server failures are handled safely

Do not use production R2 objects or production release metadata for these tests.

### 8. Create the final immutable release

After the latest release candidate passes all required validation, create the final release tag:

```bash
./dev-tools/tag-semver.sh v4.0.0
```

The final tag must point to the exact validated commit.

Verify the tag before promoting the floating major.

### 9. Move the floating major tag

After `v4.0.0` is verified and approved:

```bash
./dev-tools/move-major-tag.sh v4 v4.0.0
./dev-tools/verify-tags.sh v4 v4.0.0
```

Only after this step should general consumer documentation recommend:

```yaml
uses: jtdub79/PAB_CI_Actions/.github/actions/setup-uv@v4
```

Consumers that require maximum reproducibility may remain pinned to an immutable semantic-version tag.

## Validation gates before `v4` promotion

All of the following must pass before moving `v4`:

* action repository Ubuntu self-tests
* action repository Windows smoke tests
* action metadata and workflow validation
* quality and security checks
* mocked R2 upload tests
* mocked public-object verification tests
* mocked metadata-submission tests
* non-production R2 integration test
* non-production metadata-submission integration test
* `PAB-Shared` consumer validation
* `PABLicenseServer` consumer validation
* `PrecisionArrowBuilder` validation-workflow adoption
* `PrecisionArrowBuilder` release-mechanic validation
* documentation review
* confirmation that no consumer still depends on an unintended v2 or v3 internal reference

## Rollback

### Floating-major rollback

If a defect is discovered after moving `v4`, move `v4` back to the most recent validated immutable v4 release.

Example:

```bash
./dev-tools/move-major-tag.sh v4 v4.0.0
./dev-tools/verify-tags.sh v4 v4.0.0
```

Never rewrite or delete the defective immutable release tag merely to hide it.

### Consumer rollback

Consumers pinned to an immutable release should revert to the previous known-good immutable tag.

Consumers using `v4` will follow the floating-major rollback after their next checkout.

### Follow-up release

Fix the defect and publish a new immutable patch release:

```bash
./dev-tools/tag-semver.sh v4.0.1
./dev-tools/move-major-tag.sh v4 v4.0.1
./dev-tools/verify-tags.sh v4 v4.0.1
```

Do not reuse `v4.0.0`.

## Helper scripts

The `dev-tools` scripts are low-level tag helpers. They do not replace the validation gates in this document.

Use them only after the applicable gates are satisfied.

Examples:

```bash
# Create an immutable release candidate.
./dev-tools/tag-semver.sh v4.0.0-rc.1

# Create the final immutable release.
./dev-tools/tag-semver.sh v4.0.0

# Promote the floating major after final validation.
./dev-tools/move-major-tag.sh v4 v4.0.0

# Verify the floating major points to the intended immutable release.
./dev-tools/verify-tags.sh v4 v4.0.0
```

Before using these scripts, confirm they accept prerelease semantic versions such as `v4.0.0-rc.1`. If they do not, update and test the scripts before creating the first release candidate.

## Older major lines

`v1`, `v2`, and `v3` remain available for existing consumers.

After v4 is published:

* new development should target v4
* older majors are deprecated
* older immutable tags remain available
* older floating-major tags must not be repointed to v4
* fixes to older majors should be limited to justified compatibility or security work

## Prohibited release actions

Do not:

* use `v4.0.0` as a prerelease candidate
* validate consumers against `main`
* move `v4` before consumer validation completes
* rewrite an immutable release-candidate tag
* rewrite an immutable final semantic-version tag
* publish a new candidate without rerunning repository self-tests
* skip non-production integration testing for cloud-facing actions
* promote a release with unresolved security or credential-handling defects
  ::: 
