# Focused reusable workflow evaluation for v4

No reusable workflow is included in the initial v4 foundation pass.

The v4 composite actions must first be:

1. merged into `main`
2. validated by hosted Ubuntu and Windows self-tests
3. published under an immutable release-candidate tag such as `v4.0.0-rc.1`

Consumer validation must not reference:

* `main`
* the mutable `v4` tag
* an unpublished tag

## Why reusable workflows are deferred

Reusable workflows are resolved from the repository and ref named by the caller.

Before an immutable release-candidate tag exists, a consuming repository would have to reference an unstable branch or an unpublished tag. Neither is an acceptable long-term or release-validation contract.

The initial v4 foundation therefore publishes and validates the composite actions first.

## Candidate workflow design

After `v4.0.0-rc.1` exists and the action repository self-tests pass, evaluate focused reusable workflows only when the workflow serves at least two consumers.

Potential candidates are:

* `.github/workflows/python-quality.yml`
* `.github/workflows/python-security.yml`
* `.github/workflows/python-tests.yml`, only if at least two consumers share a genuinely common test contract

Do not create one large reusable workflow controlled by many boolean inputs.

## Composite-action references

Reusable workflows must use a deterministic action implementation.

During release-candidate validation, any cross-repository action reference must use the exact immutable candidate tag, for example:

```yaml
uses: jtdub79/PAB_CI_Actions/.github/actions/job-quality@v4.0.0-rc.1
```

Do not reference:

```yaml
uses: jtdub79/PAB_CI_Actions/.github/actions/job-quality@main
```

Do not reference the floating major until final promotion:

```yaml
uses: jtdub79/PAB_CI_Actions/.github/actions/job-quality@v4
```

Before implementing the reusable workflows, verify how same-repository composite-action references behave inside a called workflow. Avoid relative action references that would resolve against the consuming repository rather than `PAB_CI_Actions`.

Prefer an explicit immutable self-reference when necessary.

## `python-quality.yml`

Add this workflow only if at least two repositories can share the same job-level quality policy.

It may wrap the v4 quality composite and expose explicit inputs such as:

* Python version
* dependency sync arguments
* Ruff command
* Ruff format command
* mypy command
* optional import-linter command
* optional import-health command
* private dependency authentication requirement

It must not include:

* unit tests
* security scanning
* package publishing
* release behavior
* repository-specific architecture policy

Repository-specific commands should remain caller-provided inputs or repo-owned scripts.

## `python-security.yml`

Add this workflow only if at least two repositories can share the same security job policy.

It may wrap the v4 security composite and expose explicit inputs such as:

* Python version
* dependency sync arguments
* Bandit target paths
* Bandit advisory or blocking mode
* pip-audit command or options

It should be suitable for:

* dependency-file changes
* scheduled scans
* main-readiness validation
* manual security runs

It does not need to run on every feature PR.

Secrets required for private dependencies must be explicitly passed by the caller using GitHub reusable-workflow secret handling.

## `python-tests.yml`

Do not add a generic Python test workflow merely for consistency.

Add it only if at least two consumers converge on:

* the same dependency setup contract
* the same test-command interface
* compatible coverage behavior
* compatible artifact behavior
* compatible runner requirements

`PAB-Shared` and `PABLicenseServer` currently have different responsibilities, so a shared test workflow must demonstrate real reuse rather than hiding repository policy behind many inputs.

Desktop Qt, headless display, Windows, installer, and release tests do not belong in a generic Python test workflow.

## Validation sequence

The reusable-workflow sequence is:

1. Publish `v4.0.0-rc.1` containing the validated composite actions.
2. Pilot composite-action adoption in `PAB-Shared`.
3. Confirm at least two repositories share a useful job-level contract.
4. Add focused reusable workflows on the `workflows` branch.
5. Run action-repository self-tests.
6. Publish a new immutable candidate such as `v4.0.0-rc.2`.
7. Validate consuming repositories against that exact candidate.
8. Include the reusable workflows in final `v4.0.0` only after consumer validation succeeds.

Never rewrite `v4.0.0-rc.1` to add reusable workflows.

## Final release behavior

After final consumer validation:

1. create immutable `v4.0.0`
2. verify it points to the validated commit
3. move floating `v4` to `v4.0.0`
4. update general consumer documentation to recommend `@v4`

Consumers requiring maximum reproducibility may remain pinned to:

```yaml
@v4.0.0
```

## Decision rule

A reusable workflow should be added only when it:

* serves at least two repositories
* materially reduces duplication
* preserves clear repository ownership
* has a stable input and secret contract
* can be tested independently
* does not depend on an unstable branch
* does not require many policy booleans

Otherwise, keep the shared logic in a composite action and let the consuming repository own the job orchestration.
