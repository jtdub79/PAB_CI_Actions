#!/usr/bin/env python3
"""Upload one immutable object to Cloudflare R2 with SHA-256 idempotency."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
from typing import Any, Protocol

SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~!$&'()*+,;=:@/ -]*$")
RESERVED_METADATA_KEYS = {"sha256"}
CONCURRENT_EXISTS_STATUSES = {409, 412}


class S3Client(Protocol):
    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def put_object(self, **kwargs: Any) -> dict[str, Any]: ...


class FakeClientError(Exception):
    def __init__(self, status: int, code: str) -> None:
        self.response = {"ResponseMetadata": {"HTTPStatusCode": status}, "Error": {"Code": code}}
        super().__init__(f"S3 request failed with HTTP {status}")


def fail(message: str, code: int = 1) -> None:
    print(f"::error::{message}", file=sys.stderr)
    raise SystemExit(code)


def output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_key(key: str) -> None:
    if not key or key.startswith("/") or ".." in key.split("/") or key.endswith("/"):
        fail("object-key must be a non-directory relative object key without '..' segments")
    if not SAFE_KEY_RE.match(key):
        fail("object-key contains unsupported characters")


def validate_credentials() -> tuple[str, str]:
    access_key_id = os.environ.get("AWS_ACCESS_KEY_ID", "")
    secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    if not access_key_id:
        fail("AWS_ACCESS_KEY_ID is required for R2 uploads", 2)
    if not secret_access_key:
        fail("AWS_SECRET_ACCESS_KEY is required for R2 uploads", 2)
    return access_key_id, secret_access_key


def parse_metadata(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"custom-metadata must be valid JSON object: {exc}")
    if not isinstance(data, dict):
        fail("custom-metadata must be a JSON object")
    metadata: dict[str, str] = {}
    for key, value in data.items():
        key_text = str(key)
        normalized_key = key_text.lower()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key_text):
            fail(f"custom metadata key is invalid: {key_text}")
        if normalized_key in RESERVED_METADATA_KEYS:
            fail("custom metadata key 'sha256' is reserved for the computed object digest")
        metadata[normalized_key] = str(value)
    return metadata


def error_status(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if isinstance(status, int):
            return status
    return None


def error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code", "")
        return str(code)
    return ""


def is_not_found(exc: BaseException) -> bool:
    return error_status(exc) == 404 or error_code(exc) in {"404", "NoSuchKey", "NotFound"}


def is_concurrent_exists(exc: BaseException) -> bool:
    return error_status(exc) in CONCURRENT_EXISTS_STATUSES


def head_object(client: S3Client, bucket: str, key: str) -> dict[str, Any] | None:
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if is_not_found(exc):
            return None
        raise


def remote_matches(head: dict[str, Any], digest: str, size: int) -> bool:
    metadata = {str(key).lower(): str(value) for key, value in dict(head.get("Metadata") or {}).items()}
    content_length = int(head.get("ContentLength", -1))
    return metadata.get("sha256", "").lower() == digest.lower() and content_length == size


def immutable_conflict() -> None:
    fail("immutable object already exists with different bytes or SHA-256")


def build_client(account_id: str, access_key_id: str, secret_access_key: str) -> S3Client:
    try:
        import botocore.session
        from botocore.config import Config
    except ImportError:
        fail("botocore is required for live R2 uploads; install botocore before running this helper", 2)

    endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
    session = botocore.session.get_session()
    return session.create_client(
        "s3",
        endpoint_url=endpoint,
        region_name="auto",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


class StateS3Client:
    def __init__(self, state_file: pathlib.Path, endpoint: str) -> None:
        self.state_file = state_file
        self.endpoint = endpoint

    def _read(self) -> dict[str, Any]:
        return json.loads(self.state_file.read_text(encoding="utf-8")) if self.state_file.exists() else {}

    def _write(self, state: dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        state = self._read()
        bucket = state.get(kwargs["Bucket"], {})
        existing = bucket.get(kwargs["Key"])
        if not existing:
            raise FakeClientError(404, "NoSuchKey")
        return {"Metadata": dict(existing.get("metadata") or {}), "ContentLength": int(existing.get("size", -1))}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        state = self._read()
        bucket = state.setdefault(kwargs["Bucket"], {})
        key = kwargs["Key"]
        if kwargs.get("IfNoneMatch") == "*" and key in bucket:
            status = int(os.environ.get("PAB_CI_ACTIONS_R2_MOCK_CONDITIONAL_STATUS", "412"))
            raise FakeClientError(status, "PreconditionFailed" if status == 412 else "ConditionalRequestConflict")
        body = kwargs["Body"]
        payload = body.read() if hasattr(body, "read") else bytes(body)
        bucket[key] = {
            "sha256": dict(kwargs.get("Metadata") or {}).get("sha256", ""),
            "size": len(payload),
            "content_type": kwargs.get("ContentType", ""),
            "cache_control": kwargs.get("CacheControl", ""),
            "content_disposition": kwargs.get("ContentDisposition", ""),
            "metadata": dict(kwargs.get("Metadata") or {}),
            "endpoint": self.endpoint,
        }
        self._write(state)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


def make_metadata(custom_metadata: dict[str, str], digest: str) -> dict[str, str]:
    metadata = dict(custom_metadata)
    metadata["sha256"] = digest
    return metadata


def put_new_object(
    client: S3Client,
    args: argparse.Namespace,
    file_path: pathlib.Path,
    metadata: dict[str, str],
) -> None:
    put_kwargs: dict[str, Any] = {
        "Bucket": args.bucket,
        "Key": args.object_key,
        "Body": None,
        "ContentType": args.content_type,
        "CacheControl": args.cache_control,
        "Metadata": metadata,
        "IfNoneMatch": "*",
    }
    if args.content_disposition:
        put_kwargs["ContentDisposition"] = args.content_disposition
    with file_path.open("rb") as body:
        put_kwargs["Body"] = body
        client.put_object(**put_kwargs)


def upload_or_verify(
    client: S3Client, args: argparse.Namespace, file_path: pathlib.Path, digest: str, size: int
) -> str:
    metadata = make_metadata(parse_metadata(args.custom_metadata), digest)
    existing = head_object(client, args.bucket, args.object_key)
    if existing is not None:
        if remote_matches(existing, digest, size):
            return "exists"
        immutable_conflict()

    try:
        put_new_object(client, args, file_path, metadata)
        return "uploaded"
    except Exception as exc:
        if not is_concurrent_exists(exc):
            status = error_status(exc)
            if status is None:
                fail("R2 conditional upload failed")
            fail(f"R2 conditional upload failed with HTTP {status}")
        concurrent = head_object(client, args.bucket, args.object_key)
        if concurrent is not None and remote_matches(concurrent, digest, size):
            return "exists"
        immutable_conflict()
    raise AssertionError("unreachable")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--object-key", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--content-type", required=True)
    parser.add_argument("--cache-control", required=True)
    parser.add_argument("--content-disposition", default="")
    parser.add_argument("--expected-sha256", default="")
    parser.add_argument("--custom-metadata", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    access_key_id, secret_access_key = validate_credentials()
    validate_key(args.object_key)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", args.bucket):
        fail("bucket contains unsupported characters")
    file_path = pathlib.Path(args.file)
    if not file_path.is_file():
        fail("file must exist and must not be a directory")
    digest = sha256_file(file_path)
    if args.expected_sha256 and args.expected_sha256.lower() != digest:
        fail("local file SHA-256 does not match expected-sha256")
    size = file_path.stat().st_size
    endpoint = f"https://{args.account_id}.r2.cloudflarestorage.com"
    state_path = os.environ.get("PAB_CI_ACTIONS_R2_MOCK_STATE")
    client = (
        StateS3Client(pathlib.Path(state_path), endpoint)
        if state_path
        else build_client(args.account_id, access_key_id, secret_access_key)
    )
    action = upload_or_verify(client, args, file_path, digest, size)

    output("object-key", args.object_key)
    output("sha256", digest)
    output("size", str(size))
    output("action", action)
    print(f"R2 {action}: bucket={args.bucket} key={args.object_key} size={size}")


if __name__ == "__main__":
    main()
