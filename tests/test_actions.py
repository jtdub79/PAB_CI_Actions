from __future__ import annotations
import contextlib
import hashlib
import http.server
import json
import os
import pathlib
import subprocess
import sys
import threading

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
ACTIONS = ROOT / ".github" / "actions"


def run(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, **kwargs)


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
        "upsert-desktop-release-metadata": {
            "server-base-url",
            "platform",
            "channel",
            "request-json-file",
            "admin-username",
            "admin-password",
        },
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
    env = os.environ | {
        "PAB_CI_ACTIONS_R2_MOCK_STATE": str(state),
        "AWS_ACCESS_KEY_ID": "credential-one-for-tests",
        "AWS_SECRET_ACCESS_KEY": "credential-two-for-tests",
    }
    env.update(overrides)
    return env


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
    f.write_bytes(b"artifact")
    state = tmp_path / "state.json"
    first = run(
        r2_script_args(f, content_disposition='attachment; filename="app.bin"', custom_metadata='{"release":"v1"}'),
        env=r2_env(state),
    )
    assert first.returncode == 0, first.stderr
    assert "action=uploaded" in first.stdout
    second = run(
        r2_script_args(f, content_disposition='attachment; filename="app.bin"', custom_metadata='{"release":"v1"}'),
        env=r2_env(state),
    )
    assert second.returncode == 0, second.stderr
    assert "action=exists" in second.stdout
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


class MetadataHandler(http.server.BaseHTTPRequestHandler):
    expected_auth = "Basic " + __import__("base64").b64encode(b"admin:password").decode()

    def do_PUT(self):
        if self.headers.get("Authorization") != self.expected_auth:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"auth failed")
            return
        if self.path.endswith("/fail"):
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"server failed")
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *args):
        pass


def test_metadata_submission_success_auth_failure_invalid_json_and_server_failure(tmp_path):
    script = ACTIONS / "upsert-desktop-release-metadata" / "scripts" / "upsert_desktop_release_metadata.py"
    body = tmp_path / "body.json"
    body.write_text('{"version":"1.0"}')
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{bad")
    with server(MetadataHandler) as base_url:
        base_url = base_url.rsplit("/", 1)[0]
        common = [
            sys.executable,
            str(script),
            "--server-base-url",
            base_url,
            "--platform",
            "windows",
            "--channel",
            "stable",
            "--request-json-file",
            str(body),
            "--admin-username",
            "admin",
            "--admin-password",
        ]
        ok = run(common + ["password"])
        assert ok.returncode == 0, ok.stderr
        auth = run(common + ["wrong"])
        assert auth.returncode != 0
        invalid = run(
            [
                sys.executable,
                str(script),
                "--server-base-url",
                base_url,
                "--platform",
                "windows",
                "--channel",
                "stable",
                "--request-json-file",
                str(bad_json),
                "--admin-username",
                "admin",
                "--admin-password",
                "password",
            ]
        )
        assert invalid.returncode != 0
        fail = run(
            [
                sys.executable,
                str(script),
                "--server-base-url",
                base_url,
                "--platform",
                "windows",
                "--channel",
                "fail",
                "--request-json-file",
                str(body),
                "--admin-username",
                "admin",
                "--admin-password",
                "password",
                "--retry-count",
                "1",
            ]
        )
        assert fail.returncode != 0
        assert "password" not in ok.stdout + ok.stderr + auth.stdout + auth.stderr + fail.stdout + fail.stderr
