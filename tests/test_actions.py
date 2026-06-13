from __future__ import annotations
import contextlib
import hashlib
import http.server
import json
import os
import pathlib
import shutil
import subprocess
import sys
import threading

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
ACTIONS = ROOT / ".github" / "actions"


GITHUB_COMMAND_FILE_VARS = {"GITHUB_OUTPUT", "GITHUB_ENV", "GITHUB_PATH", "GITHUB_STEP_SUMMARY"}


def subprocess_env(**overrides):
    env = dict(os.environ)
    for name in GITHUB_COMMAND_FILE_VARS:
        env.pop(name, None)
    env.update(overrides)
    return env


def run(args, **kwargs):
    kwargs.setdefault("env", subprocess_env())
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, **kwargs)


def github_output_records(path):
    records = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        assert separator, f"invalid GitHub output record: {line!r}"
        records[name] = value
    return records


def test_action_metadata_parse_and_required_inputs():
    required = {
        "setup-uv": {"python-version", "sync-args", "private-auth-required"},
        "assert-repo-clean": {"check-untracked"},
        "job-quality": {"ruff-check-command", "mypy-command", "import-linter-command", "import-health-command"},
        "job-security": {"bandit-targets", "bandit-blocking"},
        "r2-upload-object": {
            "cloudflare-account-id",
            "bucket",
            "object-key",
            "file",
            "content-type",
            "cache-control",
            "r2-access-key-id",
            "r2-secret-access-key",
        },
        "verify-public-object": {"public-url", "expected-sha256", "expected-size", "retry-count", "retry-delay"},
    }
    for action_yml in ACTIONS.glob("*/action.yml"):
        data = yaml.safe_load(action_yml.read_text())
        assert data["runs"]["using"] == "composite"
        assert data.get("description")
        assert required[action_yml.parent.name].issubset(set(data.get("inputs", {})))


def test_setup_uv_missing_required_private_token_is_clear():
    text = (ACTIONS / "setup-uv" / "action.yml").read_text()
    assert "private-auth-required is true but private-github-token was not supplied" in text
    assert "git config --global" not in text
    assert "GIT_CONFIG_GLOBAL" in text


def test_assert_repo_clean_reports_tracked_changes_without_mutation(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "tracked.txt").write_text("a\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "tracked.txt").write_text("b\n")
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"], cwd=repo, text=True, capture_output=True
    )
    assert "tracked.txt" in status.stdout


def test_quality_action_has_optional_import_commands_not_repo_specific_defaults():
    data = yaml.safe_load((ACTIONS / "job-quality" / "action.yml").read_text())
    assert data["inputs"]["import-linter-command"]["default"] == ""
    assert data["inputs"]["import-health-command"]["default"] == ""
    assert "dev-tools/check_import_health.py" not in json.dumps(data)


def test_security_action_requires_bandit_targets_and_pip_audit_blocks():
    data = yaml.safe_load((ACTIONS / "job-security" / "action.yml").read_text())
    assert data["inputs"]["bandit-targets"]["required"] is True
    assert data["inputs"]["pip-audit-command"]["default"].endswith("pip-audit")
    assert "bandit -r src" not in json.dumps(data)


def job_security_step_script(step_name):
    data = yaml.safe_load((ACTIONS / "job-security" / "action.yml").read_text())
    for step in data["runs"]["steps"]:
        if step.get("name") == step_name:
            return step["run"]
    raise AssertionError(f"job-security step not found: {step_name}")


def bash_candidates():
    candidates = []
    path_bash = shutil.which("bash")
    if path_bash:
        candidates.append(pathlib.Path(path_bash))
    if os.name == "nt":
        candidates.extend(
            [
                pathlib.Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe",
                pathlib.Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "usr" / "bin" / "bash.exe",
                pathlib.Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
                / "Git"
                / "bin"
                / "bash.exe",
            ]
        )
    return candidates


def working_bash():
    attempted = []
    for bash in bash_candidates():
        bash_text = str(bash)
        if bash_text in attempted:
            continue
        attempted.append(bash_text)
        if not bash.exists():
            continue
        probe = subprocess.run([bash_text, "-c", "true"], text=True, capture_output=True)
        if probe.returncode == 0:
            return bash_text
    pytest.skip(f"bash is required to execute composite action shell snippets; attempted: {attempted}")


def prepend_path(env, path):
    existing = env.get("PATH") or env.get("Path") or ""
    env["PATH"] = f"{path}{os.pathsep}{existing}" if existing else str(path)
    return env


def read_captured_args(path):
    raw = path.read_bytes()
    if not raw:
        return []
    parts = raw.split(b"\0")
    if parts[-1] == b"":
        parts.pop()
    return [part.decode("utf-8") for part in parts]


def fake_uv_dir(tmp_path, *, exit_status=0):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "uv"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        ': "${UV_CAPTURE:=uv-args.bin}"\n'
        ': > "${UV_CAPTURE}"\n'
        'for arg in "$@"; do\n'
        '  printf \'%s\\0\' "${arg}" >> "${UV_CAPTURE}"\n'
        "done\n"
        'exit "${UV_EXIT_STATUS:-0}"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    capture = tmp_path / "uv-args.bin"
    env = prepend_path(
        subprocess_env(
            UV_CAPTURE="uv-args.bin",
            UV_EXIT_STATUS=str(exit_status),
        ),
        bindir,
    )
    return bindir, capture, env


def run_job_security_step(tmp_path, step_name, **env_overrides):
    script = job_security_step_script(step_name)
    env = subprocess_env(**env_overrides)
    return subprocess.run([working_bash(), "-c", script], cwd=tmp_path, text=True, capture_output=True, env=env)


def run_bandit_scan(tmp_path, *, targets="src", bandit_args="", blocking="true", exit_status=0):
    _bindir, capture, env = fake_uv_dir(tmp_path, exit_status=exit_status)
    env.update(BANDIT_TARGETS=targets, BANDIT_ARGS=bandit_args, BANDIT_BLOCKING=blocking)
    result = subprocess.run(
        [working_bash(), "-c", job_security_step_script("Bandit security scan")],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        env=env,
    )
    captured_args = read_captured_args(capture) if capture.exists() else None
    return result, captured_args


def test_security_action_fake_uv_does_not_require_python3_command(tmp_path):
    bindir, capture, env = fake_uv_dir(tmp_path)
    script_text = (bindir / "uv").read_text(encoding="utf-8")
    assert "#!/usr/bin/env python3" not in script_text
    assert "python3" not in script_text

    result = subprocess.run(
        [working_bash(), "-c", "uv run --frozen --no-sync bandit -r src -q -lll"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert read_captured_args(capture) == ["run", "--frozen", "--no-sync", "bandit", "-r", "src", "-q", "-lll"]


def test_security_action_bandit_args_default_empty_and_command_is_safe():
    data = yaml.safe_load((ACTIONS / "job-security" / "action.yml").read_text())
    assert data["inputs"]["bandit-args"] == {
        "description": "Additional arguments passed to Bandit after the recursive targets.",
        "required": False,
        "default": "",
    }
    script = job_security_step_script("Bandit security scan")
    assert "eval" not in script
    assert "bash -c" not in script
    assert "bandit_args=()" in script
    assert "${bandit_args[@]}" in script
    assert "uv run --frozen --no-sync bandit" in script


def test_security_action_existing_bandit_behavior_is_preserved_when_args_omitted(tmp_path):
    (tmp_path / "src").mkdir()
    result, captured_args = run_bandit_scan(tmp_path, targets="src", bandit_args="", blocking="true")
    assert result.returncode == 0, result.stderr
    assert captured_args == ["run", "--frozen", "--no-sync", "bandit", "-r", "src", "-q"]


def test_security_action_passes_additional_bandit_args_and_lll_policy(tmp_path):
    (tmp_path / "src").mkdir()
    result, captured_args = run_bandit_scan(tmp_path, targets="src", bandit_args="-lll --skip B101", blocking="true")
    assert result.returncode == 0, result.stderr
    assert captured_args == ["run", "--frozen", "--no-sync", "bandit", "-r", "src", "-q", "-lll", "--skip", "B101"]


def test_security_action_bandit_blocking_mode_propagates_nonzero_exit(tmp_path):
    (tmp_path / "src").mkdir()
    result, _captured_args = run_bandit_scan(tmp_path, blocking="true", exit_status=7)
    assert result.returncode == 7
    assert "::warning::" not in result.stdout


def test_security_action_bandit_nonblocking_mode_warns_and_succeeds(tmp_path):
    (tmp_path / "src").mkdir()
    result, _captured_args = run_bandit_scan(tmp_path, blocking="false", exit_status=7)
    assert result.returncode == 0
    assert "::warning::Bandit reported findings but bandit-blocking is false." in result.stdout


def test_security_action_bandit_args_are_not_executed_through_shell(tmp_path):
    (tmp_path / "src").mkdir()
    marker = tmp_path / "pwned"
    result, captured_args = run_bandit_scan(tmp_path, bandit_args=f"-lll; touch {marker.name}", blocking="true")
    assert result.returncode == 0, result.stderr
    assert not marker.exists()
    assert captured_args == [
        "run",
        "--frozen",
        "--no-sync",
        "bandit",
        "-r",
        "src",
        "-q",
        "-lll;",
        "touch",
        marker.name,
    ]


def test_security_action_bandit_supports_multiple_targets_and_validation(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    valid = run_job_security_step(
        tmp_path, "Validate Bandit targets", BANDIT_TARGETS="src tests", GITHUB_RUN_ID="local"
    )
    assert valid.returncode == 0, valid.stderr

    result, captured_args = run_bandit_scan(tmp_path, targets="src tests", bandit_args="-lll", blocking="true")
    assert result.returncode == 0, result.stderr
    assert captured_args == ["run", "--frozen", "--no-sync", "bandit", "-r", "src", "tests", "-q", "-lll"]

    invalid = run_job_security_step(tmp_path, "Validate Bandit targets", BANDIT_TARGETS="src missing")
    assert invalid.returncode == 2
    assert "::error::Bandit target does not exist: missing" in invalid.stdout


def load_r2_module():
    import importlib.util

    script = ACTIONS / "r2-upload-object" / "scripts" / "r2_upload_object.py"
    spec = importlib.util.spec_from_file_location("r2_upload_object", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def r2_script_args(file_path, object_key="releases/app.bin", custom_metadata="", content_disposition=""):
    script = ACTIONS / "r2-upload-object" / "scripts" / "r2_upload_object.py"
    return [
        sys.executable,
        str(script),
        "--account-id",
        "acct123",
        "--bucket",
        "bucket",
        "--object-key",
        object_key,
        "--file",
        str(file_path),
        "--content-type",
        "application/octet-stream",
        "--cache-control",
        "public, max-age=31536000, immutable",
        "--content-disposition",
        content_disposition,
        "--custom-metadata",
        custom_metadata,
    ]


def r2_env(state, **overrides):
    env = subprocess_env(
        PAB_CI_ACTIONS_R2_MOCK_STATE=str(state),
        AWS_ACCESS_KEY_ID="credential-one-for-tests",
        AWS_SECRET_ACCESS_KEY="credential-two-for-tests",
    )
    env.update(overrides)
    return env


def test_r2_output_supports_stdout_and_github_output_file(tmp_path, monkeypatch, capsys):
    module = load_r2_module()

    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    module.output("action", "uploaded")
    captured = capsys.readouterr()
    assert captured.out == "action=uploaded\n"

    output_file = tmp_path / "github-output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    module.output("action", "uploaded")
    captured = capsys.readouterr()
    assert "action=uploaded" not in captured.out
    assert github_output_records(output_file) == {"action": "uploaded"}


def test_r2_credentials_are_environment_only():
    action_text = (ACTIONS / "r2-upload-object" / "action.yml").read_text()
    script_text = (ACTIONS / "r2-upload-object" / "scripts" / "r2_upload_object.py").read_text()
    assert "AWS_ACCESS_KEY_ID" in action_text
    assert "AWS_SECRET_ACCESS_KEY" in action_text
    removed_access_arg = "--access-key" + "-id"
    removed_secret_arg = "--secret-access" + "-key"
    assert removed_access_arg not in action_text
    assert removed_secret_arg not in action_text
    assert removed_access_arg not in script_text
    assert removed_secret_arg not in script_text


def test_r2_missing_credentials_fail_safely(tmp_path):
    f = tmp_path / "artifact.bin"
    f.write_bytes(b"artifact")
    state = tmp_path / "state.json"
    access_missing = run(r2_script_args(f), env=r2_env(state, AWS_ACCESS_KEY_ID=""))
    secret_missing = run(r2_script_args(f), env=r2_env(state, AWS_SECRET_ACCESS_KEY=""))
    assert access_missing.returncode != 0
    assert secret_missing.returncode != 0
    combined = access_missing.stdout + access_missing.stderr + secret_missing.stdout + secret_missing.stderr
    assert "credential-one-for-tests" not in combined
    assert "credential-two-for-tests" not in combined
    assert "AWS_ACCESS_KEY_ID is required" in combined
    assert "AWS_SECRET_ACCESS_KEY is required" in combined


def test_r2_absent_object_uploads_and_existing_same_bytes_returns_exists(tmp_path):
    f = tmp_path / "artifact.bin"
    payload = b"artifact"
    f.write_bytes(payload)
    expected_digest = hashlib.sha256(payload).hexdigest()
    state = tmp_path / "state.json"
    first_output = tmp_path / "first-github-output.txt"
    first = run(
        r2_script_args(f, content_disposition='attachment; filename="app.bin"', custom_metadata='{"release":"v1"}'),
        env=r2_env(state, GITHUB_OUTPUT=str(first_output)),
    )
    assert first.returncode == 0, first.stderr
    assert "R2 uploaded:" in first.stdout
    assert github_output_records(first_output) == {
        "object-key": "releases/app.bin",
        "sha256": expected_digest,
        "size": str(len(payload)),
        "action": "uploaded",
    }

    second_output = tmp_path / "second-github-output.txt"
    second = run(
        r2_script_args(f, content_disposition='attachment; filename="app.bin"', custom_metadata='{"release":"v1"}'),
        env=r2_env(state, GITHUB_OUTPUT=str(second_output)),
    )
    assert second.returncode == 0, second.stderr
    assert "R2 exists:" in second.stdout
    assert github_output_records(second_output) == {
        "object-key": "releases/app.bin",
        "sha256": expected_digest,
        "size": str(len(payload)),
        "action": "exists",
    }

    data = json.loads(state.read_text())
    stored = data["bucket"]["releases/app.bin"]
    assert stored["endpoint"] == "https://acct123.r2.cloudflarestorage.com"
    assert stored["content_type"] == "application/octet-stream"
    assert stored["cache_control"] == "public, max-age=31536000, immutable"
    assert stored["content_disposition"] == 'attachment; filename="app.bin"'
    assert stored["metadata"]["release"] == "v1"
    assert stored["metadata"]["sha256"] == hashlib.sha256(b"artifact").hexdigest()


def test_r2_existing_different_bytes_fails(tmp_path):
    f = tmp_path / "artifact.bin"
    f.write_bytes(b"artifact")
    state = tmp_path / "state.json"
    assert run(r2_script_args(f), env=r2_env(state)).returncode == 0
    f.write_bytes(b"different")
    conflict = run(r2_script_args(f), env=r2_env(state))
    assert conflict.returncode != 0
    assert "immutable object already exists" in conflict.stderr


def test_r2_rejects_directories_bad_keys_expected_sha_and_bad_metadata(tmp_path):
    f = tmp_path / "artifact.bin"
    f.write_bytes(b"artifact")
    state = tmp_path / "state.json"
    bad_key = run(r2_script_args(f, object_key="../bad"), env=r2_env(state))
    directory = run(r2_script_args(tmp_path), env=r2_env(state))
    expected_sha = run(r2_script_args(f) + ["--expected-sha256", "0" * 64], env=r2_env(state))
    bad_metadata = run(r2_script_args(f, custom_metadata="[]"), env=r2_env(state))
    invalid_bucket_args = r2_script_args(f)
    invalid_bucket_args[invalid_bucket_args.index("--bucket") + 1] = "bad.bucket"
    invalid_bucket = run(invalid_bucket_args, env=r2_env(state))
    assert bad_key.returncode != 0
    assert directory.returncode != 0
    assert expected_sha.returncode != 0
    assert bad_metadata.returncode != 0
    assert invalid_bucket.returncode != 0


@pytest.mark.parametrize("reserved_key", ["sha256", "SHA256", "Sha256", "sHa256"])
def test_r2_rejects_reserved_sha256_custom_metadata(tmp_path, reserved_key):
    f = tmp_path / "artifact.bin"
    f.write_bytes(b"artifact")
    state = tmp_path / "state.json"
    result = run(r2_script_args(f, custom_metadata=json.dumps({reserved_key: "caller"})), env=r2_env(state))
    assert result.returncode != 0
    assert "reserved" in result.stderr


class RaceClient:
    def __init__(self, module, status, head_after):
        self.module = module
        self.status = status
        self.head_after = head_after
        self.head_calls = 0
        self.put_kwargs = None

    def head_object(self, **kwargs):
        self.head_calls += 1
        if self.head_calls == 1:
            raise self.module.FakeClientError(404, "NoSuchKey")
        return self.head_after

    def put_object(self, **kwargs):
        self.put_kwargs = kwargs
        raise self.module.FakeClientError(self.status, "PreconditionFailed")


def r2_args(**overrides):
    values = {
        "bucket": "bucket",
        "object_key": "folder/app file+v1.bin",
        "content_type": "application/octet-stream",
        "cache_control": "public, max-age=31536000, immutable",
        "content_disposition": "attachment",
        "custom_metadata": '{"release":"v1"}',
    }
    values.update(overrides)
    return type("Args", (), values)()


def test_r2_concurrent_identical_conditional_upload_returns_exists(tmp_path):
    module = load_r2_module()
    f = tmp_path / "artifact.bin"
    payload = b"artifact"
    f.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    client = RaceClient(module, 412, {"Metadata": {"sha256": digest}, "ContentLength": len(payload)})
    action = module.upload_or_verify(client, r2_args(), f, digest, len(payload))
    assert action == "exists"
    assert client.put_kwargs["IfNoneMatch"] == "*"
    assert client.put_kwargs["Metadata"]["sha256"] == digest
    assert client.put_kwargs["ContentType"] == "application/octet-stream"
    assert client.put_kwargs["CacheControl"] == "public, max-age=31536000, immutable"
    assert client.put_kwargs["ContentDisposition"] == "attachment"
    assert client.put_kwargs["Metadata"]["release"] == "v1"
    assert client.put_kwargs["Key"] == "folder/app file+v1.bin"
    assert hasattr(client.put_kwargs["Body"], "read")
    assert client.put_kwargs["Body"].closed


def test_r2_concurrent_conflicting_conditional_upload_fails(tmp_path):
    module = load_r2_module()
    f = tmp_path / "artifact.bin"
    payload = b"artifact"
    f.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    client = RaceClient(module, 409, {"Metadata": {"sha256": "0" * 64}, "ContentLength": len(payload)})
    with pytest.raises(SystemExit):
        module.upload_or_verify(client, r2_args(), f, digest, len(payload))


def test_r2_unexpected_conditional_put_failure_fails(tmp_path):
    module = load_r2_module()
    f = tmp_path / "artifact.bin"
    payload = b"artifact"
    f.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    client = RaceClient(module, 500, {"Metadata": {"sha256": digest}, "ContentLength": len(payload)})
    with pytest.raises(SystemExit):
        module.upload_or_verify(client, r2_args(), f, digest, len(payload))
    assert client.head_calls == 1


def test_r2_does_not_call_production_without_mock_credentials_or_client(tmp_path):
    f = tmp_path / "artifact.bin"
    f.write_bytes(b"artifact")
    state = tmp_path / "state.json"
    result = run(r2_script_args(f), env=r2_env(state))
    assert result.returncode == 0, result.stderr
    assert state.exists()


class StaticHandler(http.server.BaseHTTPRequestHandler):
    body = b"public-bytes"

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


@contextlib.contextmanager
def server(handler):
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/file"
    finally:
        httpd.shutdown()
        thread.join()


def test_public_verify_temp_path_can_be_removed_on_windows():
    import importlib.util

    script = ACTIONS / "verify-public-object" / "scripts" / "verify_public_object.py"
    spec = importlib.util.spec_from_file_location("verify_public_object", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tmp_path = module.temporary_download_path()
    try:
        tmp_path.write_bytes(b"closed descriptor")
    finally:
        tmp_path.unlink()
    assert not tmp_path.exists()


def test_public_verify_success_and_mismatch():
    script = ACTIONS / "verify-public-object" / "scripts" / "verify_public_object.py"
    sha = hashlib.sha256(StaticHandler.body).hexdigest()
    with server(StaticHandler) as url:
        ok = run(
            [
                sys.executable,
                str(script),
                "--url",
                url,
                "--expected-sha256",
                sha,
                "--expected-size",
                str(len(StaticHandler.body)),
            ]
        )
        assert ok.returncode == 0, ok.stderr
        bad = run([sys.executable, str(script), "--url", url, "--expected-sha256", "0" * 64])
        assert bad.returncode != 0
        assert url not in ok.stdout + ok.stderr + bad.stdout + bad.stderr
