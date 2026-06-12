#!/usr/bin/env python3
"""Upload one immutable object to Cloudflare R2 with SHA-256 idempotency."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~!$&'()*+,;=:@/ -]*$")


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
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(key)):
            fail(f"custom metadata key is invalid: {key}")
        metadata[str(key).lower()] = str(value)
    return metadata


def mock_mode(args: argparse.Namespace, digest: str, size: int, metadata: dict[str, str]) -> None:
    state_path = os.environ.get("PAB_CI_ACTIONS_R2_MOCK_STATE")
    if not state_path:
        return
    state_file = pathlib.Path(state_path)
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    bucket = state.setdefault(args.bucket, {})
    existing = bucket.get(args.object_key)
    if existing:
        if existing.get("sha256") == digest and int(existing.get("size", -1)) == size:
            action = "exists"
        else:
            fail("immutable object already exists with different bytes or SHA-256")
    else:
        bucket[args.object_key] = {
            "sha256": digest,
            "size": size,
            "content_type": args.content_type,
            "cache_control": args.cache_control,
            "metadata": {**metadata, "sha256": digest},
            "endpoint": f"https://{args.account_id}.r2.cloudflarestorage.com",
        }
        action = "uploaded"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    output("object-key", args.object_key)
    output("sha256", digest)
    output("size", str(size))
    output("action", action)
    print(f"R2 mock {action}: bucket={args.bucket} key={args.object_key} size={size}")
    raise SystemExit(0)


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


def sign_key(secret: str, date: str, region: str, service: str) -> bytes:
    k_date = hmac.new(("AWS4" + secret).encode(), date.encode(), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode(), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode(), hashlib.sha256).digest()
    return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()


def request(
    method: str, args: argparse.Namespace, payload: bytes = b"", extra_headers: dict[str, str] | None = None
) -> Response:
    endpoint = f"https://{args.account_id}.r2.cloudflarestorage.com"
    encoded_key = "/".join(urllib.parse.quote(part, safe="") for part in args.object_key.split("/"))
    url = f"{endpoint}/{args.bucket}/{encoded_key}"
    now = dt.datetime.now(dt.UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload).hexdigest()
    host = urllib.parse.urlparse(endpoint).netloc
    headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    if extra_headers:
        headers.update({k.lower(): v for k, v in extra_headers.items() if v != ""})
    signed_names = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{' '.join(str(headers[k]).strip().split())}\n" for k in sorted(headers))
    canonical_uri = f"/{urllib.parse.quote(args.bucket, safe='')}/{encoded_key}"
    canonical_request = "\n".join([method, canonical_uri, "", canonical_headers, signed_names, payload_hash])
    scope = f"{date_stamp}/auto/s3/aws4_request"
    string_to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()]
    )
    signature = hmac.new(
        sign_key(args.secret_access_key, date_stamp, "auto", "s3"), string_to_sign.encode(), hashlib.sha256
    ).hexdigest()
    headers["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={args.access_key_id}/{scope}, SignedHeaders={signed_names}, Signature={signature}"
    )
    req = urllib.request.Request(url, data=payload if method != "HEAD" else None, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
            return Response(resp.status, dict(resp.headers), resp.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, dict(exc.headers), exc.read(4096))


def main() -> None:
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
    parser.add_argument("--access-key-id", required=True)
    parser.add_argument("--secret-access-key", required=True)
    args = parser.parse_args()

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
    metadata = parse_metadata(args.custom_metadata)
    mock_mode(args, digest, size, metadata)

    head = request("HEAD", args)
    if head.status == 200:
        remote_sha = head.headers.get("x-amz-meta-sha256", "").lower()
        remote_size = int(head.headers.get("Content-Length", "-1"))
        if remote_sha == digest and remote_size == size:
            action = "exists"
        else:
            fail("immutable object already exists with different bytes or SHA-256")
    elif head.status == 404:
        headers = {
            "content-type": args.content_type,
            "cache-control": args.cache_control,
            "x-amz-meta-sha256": digest,
        }
        if args.content_disposition:
            headers["content-disposition"] = args.content_disposition
        for key, value in metadata.items():
            headers[f"x-amz-meta-{key}"] = value
        put = request("PUT", args, file_path.read_bytes(), headers)
        if put.status not in (200, 201, 204):
            fail(f"R2 upload failed with HTTP {put.status}")
        action = "uploaded"
    else:
        fail(f"R2 existence check failed with HTTP {head.status}")

    output("object-key", args.object_key)
    output("sha256", digest)
    output("size", str(size))
    output("action", action)
    print(f"R2 {action}: bucket={args.bucket} key={args.object_key} size={size}")


if __name__ == "__main__":
    main()
