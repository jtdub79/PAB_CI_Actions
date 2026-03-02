"""
Created on 02-19-2026

@author: James Westfall
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


def die(msg: str) -> None:
    raise SystemExit(f"Version gate failed: {msg}")


def sh(*args: str) -> str:
    p = subprocess.run(
        list(args),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return p.stdout.strip()


@dataclass(frozen=True)
class Semver:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, s: str) -> Semver:
        m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", s)
        if not m:
            die(f"Expected semver X.Y.Z, got: {s}")
        return cls(*(int(x) for x in m.groups()))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def as_tuple(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)


def read_pyproject_version(pyproject: Path) -> Semver:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    try:
        v = data["project"]["version"]
    except KeyError as e:
        die(f"Missing [project].version in pyproject.toml: {e}")
    return Semver.parse(v)


def read_nuitka_product_version(app_py: Path) -> Semver:
    text = app_py.read_text(encoding="utf-8")
    m = re.search(
        r"(?m)^#\s*nuitka-project:\s*--product-version=(\d+\.\d+\.\d+)\s*$",
        text,
    )
    if not m:
        die("Missing Nuitka '# nuitka-project: --product-version=X.Y.Z' line")
    return Semver.parse(m.group(1))


def read_inno_define_version(iss: Path) -> Semver:
    text = iss.read_text(encoding="utf-8")
    m = re.search(r'(?m)^#define\s+MyAppVer\s+"(\d+\.\d+\.\d+)"\s*$', text)
    if not m:
        die('Missing Inno `#define MyAppVer  "X.Y.Z"` line')
    return Semver.parse(m.group(1))


def check_versions_match() -> Semver:
    pyproject = Path("pyproject.toml")
    app_py = Path("src/precision_arrow_builder.py")
    iss = Path("installer.iss")

    for p in (pyproject, app_py, iss):
        if not p.exists():
            die(f"Missing file: {p}")

    v_pyproject = read_pyproject_version(pyproject)
    v_app = read_nuitka_product_version(app_py)
    v_iss = read_inno_define_version(iss)

    if str(v_pyproject) != str(v_app) or str(v_pyproject) != str(v_iss):
        die(
            "Version mismatch: "
            f"pyproject={v_pyproject}, app_py={v_app}, installer={v_iss}"
        )

    return v_pyproject


def _is_exactly_one_step_bump(prev: Semver, curr: Semver) -> bool:
    # Allowed:
    # - patch bump:  X.Y.(Z+1)
    # - minor bump:  X.(Y+1).0
    # - major bump:  (X+1).0.0
    if (curr.major, curr.minor, curr.patch) == (prev.major, prev.minor, prev.patch + 1):
        return True
    if (curr.major, curr.minor, curr.patch) == (prev.major, prev.minor + 1, 0):
        return True
    if (curr.major, curr.minor, curr.patch) == (prev.major + 1, 0, 0):
        return True
    return False


def enforce_bump_for_prs_to_main(current: Semver) -> None:
    base_ref = os.environ.get("GITHUB_BASE_REF", "")
    if base_ref != "main":
        return

    sh("git", "fetch", "--no-tags", "origin", "main:refs/remotes/origin/main")

    main_pyproject_text = sh("git", "show", "origin/main:pyproject.toml")
    main_data = tomllib.loads(main_pyproject_text)
    main_v = Semver.parse(main_data["project"]["version"])

    if not _is_exactly_one_step_bump(main_v, current):
        die(
            "PR to main must bump version by exactly one step. "
            f"main={main_v}, this PR={current}. "
            "Allowed: major+1.0.0 OR same major minor+1.0 OR same major/minor patch+1."
        )


def enforce_tag_matches_repo(tag: str, repo_version: Semver) -> None:
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        die(f"Invalid tag format: {tag}")
    tag_v = Semver.parse(tag[1:])
    if str(tag_v) != str(repo_version):
        die(f"Tag {tag} does not match repo version {repo_version}")


def cmd_pr() -> None:
    v = check_versions_match()
    enforce_bump_for_prs_to_main(v)
    print(f"OK(pr): version={v}")


def cmd_tag() -> None:
    v = check_versions_match()
    ref = os.environ.get("GITHUB_REF", "")
    m = re.fullmatch(r"refs/tags/(v\d+\.\d+\.\d+)", ref)
    if not m:
        die(f"Unexpected tag ref: {ref}")
    enforce_tag_matches_repo(m.group(1), v)
    print(f"OK(tag): version={v}")


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"pr", "tag"}:
        raise SystemExit("Usage: python dev-tools/version_gate.py {pr|tag}")
    if sys.argv[1] == "pr":
        cmd_pr()
    else:
        cmd_tag()


if __name__ == "__main__":
    main()
