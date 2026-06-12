# PAB_CI_Actions v4 foundation

`PAB_CI_Actions` owns reusable CI/CD mechanics for the Precision Arrow Builder ecosystem. It does **not** own desktop release policy, version calculation, signing policy, installer creation, production environment selection, or server persistence policy.

## Action catalog

| Action | Purpose |
| --- | --- |
| `.github/actions/setup-uv` | Install Python 3.13 by default, install uv, optionally run locked `uv sync`, and clean up temporary private Git authentication. |
| `.github/actions/assert-repo-clean` | Fail when tracked files change unexpectedly; optionally fail on untracked generated files. |
| `.github/actions/job-quality` | Run Ruff lint, Ruff format, mypy, and optional import-linter/import-health commands. |
| `.github/actions/job-security` | Run Bandit and blocking pip-audit with explicit caller-supplied targets. |
| `.github/actions/r2-upload-object` | Upload or verify one immutable Cloudflare R2 object using the S3-compatible endpoint. |
| `.github/actions/verify-public-object` | Download a public object and verify bytes by SHA-256 and optional size. |
| `.github/actions/upsert-desktop-release-metadata` | PUT a prepared JSON metadata DTO to the existing admin endpoint with HTTP Basic auth. |

## `setup-uv` secret handling and cache behavior

The v4 setup action uses `actions/setup-python@v6.2.0` and `astral-sh/setup-uv@v8.2.0`. The latest stable releases were verified during implementation pass 1 on June 12, 2026.

Private GitHub dependency authentication is optional. If `private-auth-required: "true"` and `private-github-token` is empty, the action fails before dependency synchronization. When a token is supplied, the action writes a temporary file referenced through `GIT_CONFIG_GLOBAL` only for the `uv sync` step and removes that file in an `EXIT` trap. It does not call `git config --global`, does not write repository-tracked Git config, does not expose the token as an output, and does not print the token.

Caching uses `astral-sh/setup-uv` built-in cache support instead of a separate broad `actions/cache` step. Cache invalidation includes the operating system, Python version, caller cache suffix, sync arguments, and dependency files matched by `cache-dependency-glob`.

```yaml
- uses: jtdub79/PAB_CI_Actions/.github/actions/setup-uv@v4
  with:
    python-version: "3.13"
    sync-args: "--locked --no-sources --group linting"
    private-auth-required: "true"
    private-github-token: ${{ secrets.PAB_SHARED_CORE_GITHUB_TOKEN }}
```

## Quality action usage

`job-quality` installs dependencies once and then runs caller-configurable quality commands. Import-linter and repository import-health checks are opt-in commands because not every consumer has the same tooling or paths.

```yaml
- uses: jtdub79/PAB_CI_Actions/.github/actions/job-quality@v4
  with:
    python-version: "3.13"
    install-args: "--locked --no-sources --group linting"
    mypy-command: "uv run --frozen --no-sync mypy src tests"
    import-linter-command: "uv run --frozen --no-sync lint-imports"
```

## Security action usage

`job-security` does not assume a `src` directory. Callers must provide Bandit target paths. Bandit can be advisory or blocking; pip-audit is blocking by default.

```yaml
- uses: jtdub79/PAB_CI_Actions/.github/actions/job-security@v4
  with:
    python-version: "3.13"
    install-args: "--locked --no-sources --group security"
    bandit-targets: "src tests"
    bandit-blocking: "true"
```

## R2 upload usage

The R2 upload action targets `https://<account-id>.r2.cloudflarestorage.com` with S3 region `auto`. It maps the public `r2-access-key-id` and `r2-secret-access-key` inputs to `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` environment variables internally, never command-line arguments. It validates the file, computes SHA-256, reserves the custom metadata key `sha256` for trusted computed digest metadata, and uses conditional `If-None-Match: *` object creation so it never overwrites an immutable object. Existing objects with matching SHA-256 metadata and size are idempotent success; conflicting existing objects fail. It returns non-secret `object-key`, `sha256`, `size`, and `action` outputs. Self-tests use local fakes and require no real R2 credentials. Live uploads use a pinned `botocore` client instead of a custom SigV4 signer.

```yaml
- id: upload-installer
  uses: jtdub79/PAB_CI_Actions/.github/actions/r2-upload-object@v4
  with:
    cloudflare-account-id: ${{ vars.CLOUDFLARE_ACCOUNT_ID }}
    bucket: ${{ vars.R2_BUCKET }}
    object-key: releases/windows/example-installer.exe
    file: dist/example-installer.exe
    content-type: application/vnd.microsoft.portable-executable
    cache-control: public, max-age=31536000, immutable
    expected-sha256: ${{ steps.hash.outputs.sha256 }}
    custom-metadata: '{"release":"example"}'
    r2-access-key-id: ${{ secrets.R2_ACCESS_KEY_ID }}
    r2-secret-access-key: ${{ secrets.R2_SECRET_ACCESS_KEY }}
```

## Public object verification usage

The public verification action verifies bytes only. It does not decide whether a release is publishable.

```yaml
- uses: jtdub79/PAB_CI_Actions/.github/actions/verify-public-object@v4
  with:
    public-url: ${{ vars.R2_PUBLIC_BASE_URL }}/releases/windows/example-installer.exe
    expected-sha256: ${{ steps.hash.outputs.sha256 }}
    expected-size: ${{ steps.hash.outputs.size }}
    retry-count: "5"
    retry-delay: "10"
```

## Desktop release metadata upsert usage

The metadata action submits a prepared JSON file to `PUT /api/v1/admin/desktop/releases/{platform}/{channel}` using HTTP Basic authentication. Request files may be UTF-8 with or without a BOM. It does not construct Precision Arrow Builder-specific metadata and does not use bearer token authentication. The public `admin-username` and `admin-password` inputs remain unchanged, but the composite action maps them internally to `PAB_SERVER_ADMIN_USER` and `PAB_SERVER_ADMIN_PASSWORD` environment variables so credentials are not passed through process arguments. Self-tests use local mock servers and require no real server credentials.

```yaml
- uses: jtdub79/PAB_CI_Actions/.github/actions/upsert-desktop-release-metadata@v4
  with:
    server-base-url: ${{ vars.PAB_SERVER_BASE_URL }}
    platform: windows
    channel: stable
    request-json-file: dist/release-metadata.json
    admin-username: ${{ secrets.PAB_SERVER_ADMIN_USER }}
    admin-password: ${{ secrets.PAB_SERVER_ADMIN_PASSWORD }}
```

## v4 release policy

- Publish immutable semver tags such as `v4.0.0` and `v4.0.1`; never rewrite immutable semver tags.
- Move the floating `v4` tag only after self-tests pass and consumer validation completes.
- Required rollout order: PAB-Shared, PABLicenseServer, PrecisionArrowBuilder validation, then PrecisionArrowBuilder production publishing.
- Rollback by moving the floating `v4` tag back to the last validated immutable `v4.x.y` tag. Do not delete or rewrite semver tags.
- Older `v1`, `v2`, and `v3` majors remain available for existing consumers but are deprecated for new work after `v4` is published.
- Do not publish or move any `v4` tag until the self-test workflow succeeds on the immutable candidate tag.

## Migration notes from v2/v3

- Replace stale internal `@v2`/`@v3` assumptions with direct `@v4` action references after `v4` exists.
- Pass Python `3.13`; do not introduce Python 3.14.
- Use `sync-args`/`install-args` for dependency groups instead of repository-specific defaults.
- Use the R2 and metadata actions only from the repository that owns production release orchestration and production secrets.

## Local and CI validation

Run local validation from the repository root:

```bash
uv sync --python 3.13 --locked --group test
uv run --frozen pytest -q
ruby -e 'ARGV.each { |f| require "psych"; Psych.load_file(f); puts "ok #{f}" }' $(find .github -name '*.yml' -o -name '*.yaml')
```

The GitHub self-test workflow is `.github/workflows/action-self-tests.yml`. It requires no production R2 bucket, no production PAB server, and no production secrets.

## Reusable workflows

Focused reusable workflows are deferred until an immutable `v4.0.0` pre-release tag exists. See `docs/reusable-workflows.md` for the planned workflow shapes and the reference-semantics limitation.
