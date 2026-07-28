#!/usr/bin/env python3

from __future__ import annotations

import unittest

from validate_release import compare_versions, parse_version, validate_release


class VersionTests(unittest.TestCase):
    def test_semver_precedence(self) -> None:
        ordered = [
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0",
        ]
        for older, newer in zip(ordered, ordered[1:]):
            self.assertLess(compare_versions(parse_version(older), parse_version(newer)), 0)

    def test_rejects_invalid_versions(self) -> None:
        for version in ("v1.2.3", "1.2", "01.2.3", "1.2.3-01"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                validate_release(version, [])

    def test_requires_version_newer_than_all_tags(self) -> None:
        tags = [("v1.2.3", parse_version("1.2.3"))]
        validate_release("1.3.0-rc.1", tags)
        for version in ("1.2.3", "1.2.2", "1.2.3-rc.1"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                validate_release(version, tags)


if __name__ == "__main__":
    unittest.main()
