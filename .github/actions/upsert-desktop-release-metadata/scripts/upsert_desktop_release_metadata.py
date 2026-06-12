#!/usr/bin/env python3
from __future__ import annotations
import argparse
import base64
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def fail(msg: str, code: int = 1) -> None:
    print(f"::error::{msg}", file=sys.stderr)
    raise SystemExit(code)


def out(name: str, value: str) -> None:
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def safe_segment(value: str, name: str) -> str:
    if not value or "/" in value or ".." in value:
        fail(f"{name} must be a simple path segment", 2)
    return urllib.parse.quote(value, safe="")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--server-base-url", required=True)
    p.add_argument("--platform", required=True)
    p.add_argument("--channel", required=True)
    p.add_argument("--request-json-file", required=True)
    p.add_argument("--admin-username", required=True)
    p.add_argument("--admin-password", required=True)
    p.add_argument("--retry-count", type=int, default=3)
    p.add_argument("--retry-delay", type=float, default=2.0)
    args = p.parse_args()
    req_path = pathlib.Path(args.request_json_file)
    if not req_path.is_file():
        fail("request-json-file must exist", 2)
    try:
        payload_obj = json.loads(req_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"request-json-file is not valid JSON: {exc}", 2)
    payload = json.dumps(payload_obj, separators=(",", ":")).encode("utf-8")
    base = args.server_base_url.rstrip("/")
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        fail("server-base-url must be an http(s) URL", 2)
    path = f"/api/v1/admin/desktop/releases/{safe_segment(args.platform, 'platform')}/{safe_segment(args.channel, 'channel')}"
    url = base + path
    token = base64.b64encode(f"{args.admin_username}:{args.admin_password}".encode()).decode()
    headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json", "Accept": "application/json"}
    response_file = (
        pathlib.Path(os.environ.get("RUNNER_TEMP", ".")) / f"pab-release-metadata-response-{os.getpid()}.txt"
    )
    last_status = 0
    for attempt in range(1, max(args.retry_count, 1) + 1):
        req = urllib.request.Request(url, data=payload, method="PUT", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
                resp.read(1024 * 1024)
                body = b"<response body intentionally not retained by shared action>"
                last_status = resp.status
        except urllib.error.HTTPError as exc:
            exc.read(1024 * 1024)
            body = b"<response body intentionally not retained by shared action>"
            last_status = exc.code
        if 200 <= last_status < 300:
            break
        if last_status in {408, 429, 500, 502, 503, 504} and attempt < args.retry_count:
            time.sleep(args.retry_delay)
            continue
        break
    response_file.write_bytes(body)
    out("http-status", str(last_status))
    out("platform", args.platform)
    out("channel", args.channel)
    out("response-file", str(response_file))
    if not (200 <= last_status < 300):
        fail(f"metadata upsert failed with HTTP {last_status}; sanitized diagnostic retained at {response_file}")
    print(
        f"Metadata upsert succeeded: status={last_status} platform={args.platform} channel={args.channel} response_file={response_file}"
    )


if __name__ == "__main__":
    main()
