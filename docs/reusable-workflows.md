# Focused reusable workflow evaluation for v4

No reusable workflow was included in the initial v4 foundation release.

The validated v4 composite-action foundation is published at:

```text
v4.0.1  (immutable)
v4      (floating — points to the same commit as v4.0.1)
```

Ordinary CI consumers should use the floating major:

```yaml
uses: jtdub79/PAB_CI_Actions/.github/actions/<action-name>@v4
```

Release-critical consumers that require deliberate adoption may pin the immutable
tag or an exact commit SHA. See `docs/versioning-and-pinning-policy.md` for the
full consumer pinning policy.

Consumer references must not use:

* `@main`
* `@dev`
* `@latest`
* release-candidate refs such as `@v4.0.0-rc.1`

## Why reusable workflows are deferred

Reusable workflows are resolved from the repository and ref named by the caller.

The initial v4 foundation therefore published and validated the composite actions before evaluating shared job orchestration.

Reusable workflows remain deferred until the cross-repository workflow analysis demonstrates that at least two consumers share a stable job-level contract.

## Candidate workflow design

Now that `v4.0.0` exists and the action repository self-tests and consumer rehearsals have passed, evaluate focused reusable workflows only when the workflow serves at least two consumers.

Potential candidates are:

* `.github/workflows/python-quality.yml`
* `.github/workflows/python-security.yml`
* `.github/workflows/python-tests.yml`, only if at least two consumers share a genuinely common test contract

Do not create one large reusable workflow controlled by many boolean inputs.

## Composite-action references

Reusable workflows must use a deterministic action implementation.

Cross-repository action references within reusable workflows should use the
floating major for ordinary CI use cases:

```yaml
uses: jtdub79/PAB_CI_Actions/.github/actions/job-quality@v4
```

Release-critical references should use an immutable tag or SHA:

```yaml
uses: jtdub79/PAB_CI_Actions/.github/actions/job-quality@v4.0.1
```

Do not reference:

```yaml
uses: jtdub79/PAB_CI_Actions/.github/actions/job-quality@main
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

The reusable-workflow evaluation sequence is now:

1. Inventory workflow duplication across all four PAB repositories.
2. Confirm at least two repositories share a useful, stable job-level contract.
3. Decide whether each candidate belongs in a composite action, reusable workflow, common Make target contract, or repository-owned workflow.
4. Implement only the approved focused reusable workflows on a feature branch in `PAB_CI_Actions`.
5. Run action-repository self-tests on Ubuntu and Windows.
6. Publish a new immutable semantic-version release containing the reusable workflows.
7. Validate each consuming repository against that exact immutable release.
8. Promote the floating `v4` tag only after the new immutable release and required consumers pass.

Never rewrite `v4.0.0` or any release-candidate tag to add reusable workflows.

## Current release behavior

The validated composite-action foundation is available at:

```text
v4.0.1  (immutable)
v4      (floating — points to the same commit as v4.0.1)
```

Ordinary CI consumers should use `@v4`. Consumers requiring maximum
reproducibility may pin to the immutable tag `@v4.0.1` or an exact commit SHA.

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
