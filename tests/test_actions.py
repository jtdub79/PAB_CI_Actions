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


def test_r2_validation_endpoint_and_idempotency(tmp_path):
    f = tmp_path / "artifact.bin"
    f.write_bytes(b"artifact")
    state = tmp_path / "state.json"
    env = os.environ | {"PAB_CI_ACTIONS_R2_MOCK_STATE": str(state)}
    script = ACTIONS / "r2-upload-object" / "scripts" / "r2_upload_object.py"
    base = [
        sys.executable,
        str(script),
        "--account-id",
        "acct123",
        "--bucket",
        "bucket",
        "--object-key",
        "releases/app.bin",
        "--file",
        str(f),
        "--content-type",
        "application/octet-stream",
        "--cache-control",
        "public, max-age=31536000, immutable",
        "--access-key-id",
        "id",
        "--secret-access-key",
        "secret",
    ]
    first = run(base, env=env)
    assert first.returncode == 0, first.stderr
    second = run(base, env=env)
    assert second.returncode == 0, second.stderr
    data = json.loads(state.read_text())
    assert data["bucket"]["releases/app.bin"]["endpoint"] == "https://acct123.r2.cloudflarestorage.com"
    f.write_bytes(b"different")
    conflict = run(base, env=env)
    assert conflict.returncode != 0


def test_r2_rejects_directories_and_bad_keys(tmp_path):
    script = ACTIONS / "r2-upload-object" / "scripts" / "r2_upload_object.py"
    bad = run(
        [
            sys.executable,
            str(script),
            "--account-id",
            "a",
            "--bucket",
            "bucket",
            "--object-key",
            "../bad",
            "--file",
            str(tmp_path),
            "--content-type",
            "text/plain",
            "--cache-control",
            "no-store",
            "--access-key-id",
            "id",
            "--secret-access-key",
            "secret",
        ]
    )
    assert bad.returncode != 0


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
