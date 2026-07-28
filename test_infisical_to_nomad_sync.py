from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from infisical_to_nomad_sync import (
    Config,
    ConfigError,
    InfisicalError,
    NomadError,
    SecretDataError,
    build_target_items,
    fetch_secrets,
    get_nomad_variable_items,
    resolve_project_id,
    run,
)


BASE_ENV = {
    "NOMAD_TOKEN": "nomad-jwt",
    "NOMAD_ADDR": "http://nomad:4646",
    "NOMAD_VAR_PATH": "apps/secrets/example",
    "INFISICAL_IDENTITY_ID": "identity-id",
    "INFISICAL_ENVIRONMENT": "dev",
    "INFISICAL_SECRET_PATH": "/",
}


class ProjectIdentifierTests(unittest.TestCase):
    def test_requires_project_slug_or_id(self) -> None:
        with self.assertRaisesRegex(ConfigError, "INFISICAL_PROJECT_SLUG"):
            Config.from_env(BASE_ENV)

    def test_id_only_does_not_make_resolution_request(self) -> None:
        config = Config.from_env({**BASE_ENV, "INFISICAL_PROJECT_ID": "project-id"})
        session = Mock()

        self.assertEqual(resolve_project_id(session, config, "token"), "project-id")
        session.get.assert_not_called()

    def test_slug_is_resolved_and_preferred_over_id(self) -> None:
        config = Config.from_env(
            {
                **BASE_ENV,
                "INFISICAL_PROJECT_ID": "fallback-id",
                "INFISICAL_PROJECT_SLUG": "preferred-project",
            }
        )
        response = Mock(ok=True)
        response.json.return_value = {
            "projects": [
                {"id": "resolved-id", "slug": "preferred-project"},
                {"id": "other-id", "slug": "other-project"},
            ]
        }
        session = Mock()
        session.get.return_value = response

        self.assertEqual(resolve_project_id(session, config, "token"), "resolved-id")
        session.get.assert_called_once_with(
            "https://app.infisical.com/api/v1/projects",
            headers={"Authorization": "Bearer token"},
            timeout=15.0,
        )

    def test_unknown_slug_is_rejected(self) -> None:
        config = Config.from_env(
            {**BASE_ENV, "INFISICAL_PROJECT_SLUG": "missing-project"}
        )
        response = Mock(ok=True)
        response.json.return_value = {"projects": []}
        session = Mock()
        session.get.return_value = response

        with self.assertRaisesRegex(InfisicalError, "no accessible Infisical project"):
            resolve_project_id(session, config, "token")

    def test_fetch_secrets_uses_effective_project_id(self) -> None:
        config = Config.from_env(
            {**BASE_ENV, "INFISICAL_PROJECT_SLUG": "preferred-project"}
        )
        response = Mock(ok=True)
        response.json.return_value = {"secrets": []}
        session = Mock()
        session.get.return_value = response

        fetch_secrets(session, config, "token", "resolved-id")

        params = session.get.call_args.kwargs["params"]
        self.assertEqual(params["projectId"], "resolved-id")

    def test_malformed_project_list_is_rejected(self) -> None:
        config = Config.from_env(
            {**BASE_ENV, "INFISICAL_PROJECT_SLUG": "preferred-project"}
        )
        response = Mock(ok=True)
        response.json.return_value = {"projects": {}}
        session = Mock()
        session.get.return_value = response

        with self.assertRaisesRegex(InfisicalError, "projects list"):
            resolve_project_id(session, config, "token")


class SyncModeConfigTests(unittest.TestCase):
    def test_defaults_to_leave_orphans(self) -> None:
        config = Config.from_env({**BASE_ENV, "INFISICAL_PROJECT_ID": "project-id"})

        self.assertEqual(config.sync_mode, "leave-orphans")

    def test_accepts_supported_modes_case_insensitively(self) -> None:
        for raw, expected in (("FULL", "full"), ("leave-orphans", "leave-orphans")):
            with self.subTest(raw=raw):
                config = Config.from_env(
                    {
                        **BASE_ENV,
                        "INFISICAL_PROJECT_ID": "project-id",
                        "SYNC_MODE": raw,
                    }
                )
                self.assertEqual(config.sync_mode, expected)

    def test_rejects_unknown_mode(self) -> None:
        with self.assertRaisesRegex(ConfigError, "SYNC_MODE"):
            Config.from_env(
                {
                    **BASE_ENV,
                    "INFISICAL_PROJECT_ID": "project-id",
                    "SYNC_MODE": "merge",
                }
            )


class TargetItemsTests(unittest.TestCase):
    def test_full_removes_orphans_and_updates_shared_keys(self) -> None:
        target, orphans = build_target_items(
            {"SHARED": "new"}, {"SHARED": "old", "ORPHAN": "keep-or-remove"}, "full"
        )

        self.assertEqual(target, {"SHARED": "new"})
        self.assertEqual(orphans, ("ORPHAN",))

    def test_leave_orphans_preserves_orphans_and_updates_shared_keys(self) -> None:
        target, orphans = build_target_items(
            {"SHARED": "new"},
            {"SHARED": "old", "Z_ORPHAN": "z", "A_ORPHAN": "a"},
            "leave-orphans",
        )

        self.assertEqual(
            target, {"SHARED": "new", "Z_ORPHAN": "z", "A_ORPHAN": "a"}
        )
        self.assertEqual(orphans, ("A_ORPHAN", "Z_ORPHAN"))

    def test_empty_desired_obeys_each_mode(self) -> None:
        current = {"ORPHAN": "value"}

        self.assertEqual(build_target_items({}, current, "full")[0], {})
        self.assertEqual(build_target_items({}, current, "leave-orphans")[0], current)


class NomadReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config.from_env(
            {**BASE_ENV, "INFISICAL_PROJECT_ID": "project-id"}
        )

    def test_missing_variable_is_empty(self) -> None:
        response = Mock(ok=False, status_code=404)
        session = Mock()
        session.get.return_value = response

        self.assertEqual(get_nomad_variable_items(session, self.config), {})

    def test_reads_string_items(self) -> None:
        response = Mock(ok=True, status_code=200)
        response.json.return_value = {"Items": {"KEY": "value"}}
        session = Mock()
        session.get.return_value = response

        self.assertEqual(
            get_nomad_variable_items(session, self.config), {"KEY": "value"}
        )

    def test_rejects_malformed_items(self) -> None:
        response = Mock(ok=True, status_code=200)
        response.json.return_value = {"Items": {"KEY": 123}}
        session = Mock()
        session.get.return_value = response

        with self.assertRaisesRegex(NomadError, "valid string Items"):
            get_nomad_variable_items(session, self.config)


class RunSyncModeTests(unittest.TestCase):
    def test_leave_orphans_logs_names_and_validates_merged_map(self) -> None:
        config = Config.from_env(
            {
                **BASE_ENV,
                "INFISICAL_PROJECT_ID": "project-id",
                "SYNC_MODE": "leave-orphans",
            }
        )
        session = Mock()

        with (
            patch("infisical_to_nomad_sync.infisical_login", return_value="token"),
            patch(
                "infisical_to_nomad_sync.fetch_secrets",
                return_value={"secrets": [{"secretKey": "SHARED", "secretValue": "new"}]},
            ),
            patch(
                "infisical_to_nomad_sync.get_nomad_variable_items",
                return_value={"SHARED": "old", "ORPHAN": "value"},
            ),
            patch(
                "infisical_to_nomad_sync.validate_nomad_items", return_value=24
            ) as validate,
            patch("infisical_to_nomad_sync.put_nomad_variable") as put,
            self.assertLogs("infisical-to-nomad-sync", level="INFO") as logs,
        ):
            run(config, session)

        expected = {"SHARED": "new", "ORPHAN": "value"}
        validate.assert_called_once_with(expected)
        put.assert_called_once_with(session, config, expected)
        self.assertTrue(
            any(
                "Found 1 orphaned Nomad secrets: ORPHAN; "
                "sync_mode=leave-orphans action=preserve" in entry
                for entry in logs.output
            )
        )

    def test_final_merged_size_failure_prevents_write(self) -> None:
        config = Config.from_env(
            {
                **BASE_ENV,
                "INFISICAL_PROJECT_ID": "project-id",
                "SYNC_MODE": "leave-orphans",
            }
        )
        session = Mock()

        with (
            patch("infisical_to_nomad_sync.infisical_login", return_value="token"),
            patch("infisical_to_nomad_sync.fetch_secrets", return_value={"secrets": []}),
            patch(
                "infisical_to_nomad_sync.get_nomad_variable_items",
                return_value={"ORPHAN": "x" * (64 * 1024)},
            ),
            patch("infisical_to_nomad_sync.put_nomad_variable") as put,
        ):
            with self.assertRaises(SecretDataError):
                run(config, session)

        put.assert_not_called()


if __name__ == "__main__":
    unittest.main()
