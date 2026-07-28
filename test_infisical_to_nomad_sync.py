from __future__ import annotations

import unittest
from unittest.mock import Mock

from infisical_to_nomad_sync import (
    Config,
    ConfigError,
    InfisicalError,
    fetch_secrets,
    resolve_project_id,
)


BASE_ENV = {
    "NOMAD_TOKEN": "nomad-jwt",
    "NOMAD_ADDR": "http://nomad:4646",
    "NOMAD_VAR_PATH": "apps/secrets/example",
    "NOMAD_VAR_PREFIX": "apps/secrets",
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


if __name__ == "__main__":
    unittest.main()
