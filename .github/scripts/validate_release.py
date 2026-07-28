#!/usr/bin/env python3
"""Validate a requested release version against existing Git tags."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass


SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...]


def parse_version(raw: str) -> Version:
    match = SEMVER_RE.fullmatch(raw)
    if not match:
        raise ValueError(f"invalid semantic version: {raw}")
    prerelease = match.group("prerelease")
    return Version(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch")),
        prerelease=tuple(prerelease.split(".")) if prerelease else (),
    )


def compare_versions(left: Version, right: Version) -> int:
    left_core = (left.major, left.minor, left.patch)
    right_core = (right.major, right.minor, right.patch)
    if left_core != right_core:
        return 1 if left_core > right_core else -1

    if not left.prerelease and not right.prerelease:
        return 0
    if not left.prerelease:
        return 1
    if not right.prerelease:
        return -1

    for left_part, right_part in zip(left.prerelease, right.prerelease):
        if left_part == right_part:
            continue
        left_numeric = left_part.isdigit()
        right_numeric = right_part.isdigit()
        if left_numeric and right_numeric:
            return 1 if int(left_part) > int(right_part) else -1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return 1 if left_part > right_part else -1

    if len(left.prerelease) == len(right.prerelease):
        return 0
    return 1 if len(left.prerelease) > len(right.prerelease) else -1


def existing_versions() -> list[tuple[str, Version]]:
    result = subprocess.run(
        ["git", "tag", "--list", "v*"],
        check=True,
        capture_output=True,
        text=True,
    )
    versions: list[tuple[str, Version]] = []
    for tag in result.stdout.splitlines():
        try:
            versions.append((tag, parse_version(tag.removeprefix("v"))))
        except ValueError:
            continue
    return versions


def validate_release(raw: str, tags: list[tuple[str, Version]]) -> None:
    if raw.startswith("v"):
        raise ValueError("provide the version without the 'v' prefix")
    requested = parse_version(raw)
    requested_tag = f"v{raw}"
    if any(tag == requested_tag for tag, _ in tags):
        raise ValueError(f"tag already exists: {requested_tag}")
    for tag, version in tags:
        if compare_versions(requested, version) <= 0:
            raise ValueError(f"{raw} must be newer than existing release {tag}")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_release.py <version>", file=sys.stderr)
        return 2
    try:
        tags = existing_versions()
        validate_release(sys.argv[1], tags)
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    print(f"Release version is valid: v{sys.argv[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
