#!/usr/bin/env python3
from __future__ import annotations
import argparse
import hashlib
import os
import pathlib
import sys
import tempfile
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


def digest(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--expected-sha256", required=True)
    p.add_argument("--expected-size", default="")
    p.add_argument("--retry-count", type=int, default=3)
    p.add_argument("--retry-delay", type=float, default=2.0)
    args = p.parse_args()
    if args.retry_count < 1:
        fail("retry-count must be at least 1", 2)
    parsed = urllib.parse.urlparse(args.url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        fail("public-url must be an http(s) URL", 2)
    if len(args.expected_sha256) != 64:
        fail("expected-sha256 must be a hex SHA-256 digest", 2)
    tmp_path = pathlib.Path(tempfile.mkstemp(prefix="pab-public-object-")[1])
    try:
        last = None
        for attempt in range(1, args.retry_count + 1):
            try:
                req = urllib.request.Request(args.url, headers={"User-Agent": "PAB_CI_Actions public verifier"})
                with urllib.request.urlopen(req, timeout=30) as resp, tmp_path.open("wb") as fh:  # nosec B310
                    if resp.status < 200 or resp.status >= 300:
                        fail(f"public object returned HTTP {resp.status}")
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
                last = None
                break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
                if attempt < args.retry_count:
                    time.sleep(args.retry_delay)
        if last is not None:
            fail("public object download failed after retries")
        got_sha = digest(tmp_path)
        got_size = tmp_path.stat().st_size
        if got_sha.lower() != args.expected_sha256.lower():
            fail("public object SHA-256 mismatch")
        if args.expected_size and got_size != int(args.expected_size):
            fail("public object size mismatch")
        out("sha256", got_sha)
        out("size", str(got_size))
        print(f"Verified public object bytes: size={got_size} sha256={got_sha}")
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
