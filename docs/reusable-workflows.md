# Focused reusable workflow evaluation for v4

No reusable workflow is added in implementation pass 1. Composite actions are ready for `v4`, but called workflows are resolved from the referenced repository and ref. Before an immutable `v4.0.0` pre-release tag exists, a consumer-safe workflow would either need to reference an unstable branch or self-reference a tag that has not been published.

Immediately after `v4.0.0` pre-release tagging and action self-test success, add focused reusable workflows only if they serve at least two consumers:

- `python-quality.yml` wrapping `.github/actions/job-quality@v4`.
- `python-security.yml` wrapping `.github/actions/job-security@v4`.
- A Python test workflow only if PAB-Shared and PABLicenseServer converge on a common test command contract.

Do not use `@main` as the long-term consumer contract.
