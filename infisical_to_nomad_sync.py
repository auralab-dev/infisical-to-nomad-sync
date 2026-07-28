#!/usr/bin/env python3
"""Copy filtered Infisical secrets into a Nomad Variable."""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.parse import quote

import requests


LOGGER = logging.getLogger("infisical-to-nomad-sync")
NOMAD_PATH_RE = re.compile(r"^[a-zA-Z0-9-_~/]{1,128}$")
NOMAD_ITEMS_LIMIT_BYTES = 64 * 1024
SYNC_MODES = {"full", "leave-orphans"}


class ConfigError(ValueError):
    """Configuration is missing or invalid."""


class InfisicalError(RuntimeError):
    """Infisical authentication or secret retrieval failed."""


class SecretDataError(RuntimeError):
    """Fetched secrets cannot safely be written to Nomad."""


class NomadError(RuntimeError):
    """Nomad Variable upload failed."""


def parse_bool(name: str, raw: str) -> bool:
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be one of true/false, yes/no, on/off, or 1/0")


def clean_url(name: str, raw: str) -> str:
    value = raw.strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        raise ConfigError(f"{name} must start with http:// or https://")
    return value


def validate_nomad_path(path: str, name: str = "NOMAD_VAR_PATH") -> str:
    if not NOMAD_PATH_RE.fullmatch(path):
        raise ConfigError(
            f"{name} must be 1-128 characters, have no leading slash, and contain "
            "only letters, digits, '-', '_', '~', or '/'"
        )
    return path


@dataclass(frozen=True)
class Config:
    nomad_token: str
    nomad_addr: str
    nomad_namespace: str
    nomad_var_path: str
    infisical_url: str
    infisical_identity_id: str
    infisical_project_id: str | None
    infisical_project_slug: str | None
    infisical_environment: str
    infisical_secret_path: str
    infisical_organization_slug: str | None
    tag_filters: tuple[str, ...]
    metadata_filter: str | None
    recursive: bool
    include_imports: bool
    include_personal_overrides: bool
    expand_references: bool
    sync_mode: str
    timeout_seconds: float
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        source = os.environ if env is None else env
        required = (
            "NOMAD_TOKEN",
            "NOMAD_ADDR",
            "NOMAD_VAR_PATH",
            "INFISICAL_IDENTITY_ID",
            "INFISICAL_ENVIRONMENT",
            "INFISICAL_SECRET_PATH",
        )
        missing = [name for name in required if not source.get(name, "").strip()]
        if missing:
            raise ConfigError("missing required environment variables: " + ", ".join(missing))

        project_id = source.get("INFISICAL_PROJECT_ID", "").strip() or None
        project_slug = source.get("INFISICAL_PROJECT_SLUG", "").strip() or None
        if not project_id and not project_slug:
            raise ConfigError(
                "one of INFISICAL_PROJECT_SLUG or INFISICAL_PROJECT_ID is required"
            )

        path = validate_nomad_path(source["NOMAD_VAR_PATH"].strip())

        try:
            timeout = float(source.get("HTTP_TIMEOUT_SECONDS", "15"))
        except ValueError as exc:
            raise ConfigError("HTTP_TIMEOUT_SECONDS must be a number") from exc
        if timeout <= 0:
            raise ConfigError("HTTP_TIMEOUT_SECONDS must be greater than zero")

        log_level = source.get("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in logging.getLevelNamesMapping():
            raise ConfigError(f"invalid LOG_LEVEL: {log_level}")

        sync_mode = source.get("SYNC_MODE", "leave-orphans").strip().lower()
        if sync_mode not in SYNC_MODES:
            raise ConfigError("SYNC_MODE must be one of: full, leave-orphans")

        tags = tuple(
            tag.strip()
            for tag in source.get("INFISICAL_TAG_FILTERS", "").split(",")
            if tag.strip()
        )

        return cls(
            nomad_token=source["NOMAD_TOKEN"].strip(),
            nomad_addr=clean_url("NOMAD_ADDR", source["NOMAD_ADDR"]),
            nomad_namespace=source.get("NOMAD_NAMESPACE", "default").strip() or "default",
            nomad_var_path=path,
            infisical_url=clean_url(
                "INFISICAL_URL", source.get("INFISICAL_URL", "https://app.infisical.com")
            ),
            infisical_identity_id=source["INFISICAL_IDENTITY_ID"].strip(),
            infisical_project_id=project_id,
            infisical_project_slug=project_slug,
            infisical_environment=source["INFISICAL_ENVIRONMENT"].strip(),
            infisical_secret_path=source["INFISICAL_SECRET_PATH"].strip(),
            infisical_organization_slug=(
                source.get("INFISICAL_ORGANIZATION_SLUG", "").strip() or None
            ),
            tag_filters=tags,
            metadata_filter=source.get("INFISICAL_METADATA_FILTER", "").strip() or None,
            recursive=parse_bool(
                "INFISICAL_RECURSIVE", source.get("INFISICAL_RECURSIVE", "false")
            ),
            include_imports=parse_bool(
                "INFISICAL_INCLUDE_IMPORTS",
                source.get("INFISICAL_INCLUDE_IMPORTS", "false"),
            ),
            include_personal_overrides=parse_bool(
                "INFISICAL_INCLUDE_PERSONAL_OVERRIDES",
                source.get("INFISICAL_INCLUDE_PERSONAL_OVERRIDES", "false"),
            ),
            expand_references=parse_bool(
                "INFISICAL_EXPAND_REFERENCES",
                source.get("INFISICAL_EXPAND_REFERENCES", "true"),
            ),
            sync_mode=sync_mode,
            timeout_seconds=timeout,
            log_level=log_level,
        )


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


def safe_api_message(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return "no structured error message"
    if not isinstance(body, dict):
        return "no structured error message"
    message = body.get("message") or body.get("error")
    if isinstance(message, (str, int, float, bool)):
        return str(message)[:300]
    return "no structured error message"


def infisical_login(
    session: requests.Session, config: Config
) -> str:
    payload: dict[str, str] = {
        "identityId": config.infisical_identity_id,
        "jwt": config.nomad_token,
    }
    if config.infisical_organization_slug:
        payload["organizationSlug"] = config.infisical_organization_slug

    try:
        response = session.post(
            f"{config.infisical_url}/api/v1/auth/jwt-auth/login",
            json=payload,
            timeout=config.timeout_seconds,
        )
    except requests.RequestException as exc:
        raise InfisicalError(f"JWT login request failed: {exc}") from exc
    if not response.ok:
        raise InfisicalError(
            f"JWT login failed with HTTP {response.status_code}: {safe_api_message(response)}"
        )
    try:
        access_token = response.json()["accessToken"]
    except (ValueError, KeyError, TypeError) as exc:
        raise InfisicalError("JWT login response did not contain accessToken") from exc
    if not isinstance(access_token, str) or not access_token:
        raise InfisicalError("JWT login returned an invalid accessToken")
    return access_token


def resolve_project_id(
    session: requests.Session, config: Config, access_token: str
) -> str:
    """Return the configured project ID, resolving a preferred slug when present."""
    if not config.infisical_project_slug:
        if not config.infisical_project_id:
            raise ConfigError(
                "one of INFISICAL_PROJECT_SLUG or INFISICAL_PROJECT_ID is required"
            )
        return config.infisical_project_id

    try:
        response = session.get(
            f"{config.infisical_url}/api/v1/projects",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=config.timeout_seconds,
        )
    except requests.RequestException as exc:
        raise InfisicalError(f"project list request failed: {exc}") from exc
    if not response.ok:
        raise InfisicalError(
            f"project list failed with HTTP {response.status_code}: {safe_api_message(response)}"
        )
    try:
        projects = response.json()["projects"]
    except (ValueError, KeyError, TypeError) as exc:
        raise InfisicalError("project list response did not contain a projects list") from exc
    if not isinstance(projects, list):
        raise InfisicalError("project list response did not contain a projects list")

    matches = [
        project
        for project in projects
        if isinstance(project, dict)
        and project.get("slug") == config.infisical_project_slug
    ]
    if not matches:
        raise InfisicalError(
            f"no accessible Infisical project has slug {config.infisical_project_slug!r}"
        )
    if len(matches) > 1:
        raise InfisicalError(
            f"multiple accessible Infisical projects have slug {config.infisical_project_slug!r}"
        )
    project_id = matches[0].get("id")
    if not isinstance(project_id, str) or not project_id:
        raise InfisicalError(
            f"Infisical project {config.infisical_project_slug!r} has an invalid id"
        )
    return project_id


def fetch_secrets(
    session: requests.Session, config: Config, access_token: str, project_id: str
) -> dict[str, Any]:
    params: dict[str, str] = {
        "projectId": project_id,
        "environment": config.infisical_environment,
        "secretPath": config.infisical_secret_path,
        "viewSecretValue": "true",
        "expandSecretReferences": str(config.expand_references).lower(),
        "recursive": str(config.recursive).lower(),
        "includeImports": str(config.include_imports).lower(),
        "includePersonalOverrides": str(config.include_personal_overrides).lower(),
    }
    if config.tag_filters:
        params["tagSlugs"] = ",".join(config.tag_filters)
    if config.metadata_filter:
        params["metadataFilter"] = config.metadata_filter

    try:
        response = session.get(
            f"{config.infisical_url}/api/v4/secrets",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
            timeout=config.timeout_seconds,
        )
    except requests.RequestException as exc:
        raise InfisicalError(f"secret list request failed: {exc}") from exc
    if not response.ok:
        raise InfisicalError(
            f"secret list failed with HTTP {response.status_code}: {safe_api_message(response)}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise InfisicalError("secret list response was not valid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("secrets"), list):
        raise InfisicalError("secret list response did not contain a secrets list")
    return payload


def _add_secret(
    items: dict[str, str], sources: dict[str, str], secret: Any, source: str
) -> None:
    if not isinstance(secret, dict):
        raise SecretDataError("Infisical returned a malformed secret entry")
    key = secret.get("secretKey")
    value = secret.get("secretValue")
    if not isinstance(key, str) or not key:
        raise SecretDataError("Infisical returned a secret with an invalid key")
    if not isinstance(value, str):
        raise SecretDataError(f"secret {key!r} has no visible string value")
    if secret.get("secretValueHidden") is True:
        raise SecretDataError(f"secret {key!r} is hidden and cannot be synchronized")
    if key in items:
        raise SecretDataError(
            f"duplicate secret key {key!r} from {sources[key]} and {source}"
        )
    items[key] = value
    sources[key] = source


def secrets_to_items(payload: Mapping[str, Any]) -> dict[str, str]:
    """Flatten imported and local secrets; local secrets override imported values."""
    imported_items: dict[str, str] = {}
    imported_sources: dict[str, str] = {}
    imports = payload.get("imports", [])
    if imports is None:
        imports = []
    if not isinstance(imports, list):
        raise SecretDataError("Infisical returned a malformed imports list")
    for imported in imports:
        if not isinstance(imported, dict) or not isinstance(imported.get("secrets"), list):
            raise SecretDataError("Infisical returned a malformed import entry")
        label = f"import {imported.get('environment', '?')}:{imported.get('secretPath', '?')}"
        for secret in imported["secrets"]:
            _add_secret(imported_items, imported_sources, secret, label)

    local_items: dict[str, str] = {}
    local_sources: dict[str, str] = {}
    secrets = payload.get("secrets")
    if not isinstance(secrets, list):
        raise SecretDataError("Infisical returned a malformed secrets list")
    for secret in secrets:
        path = secret.get("secretPath", "?") if isinstance(secret, dict) else "?"
        _add_secret(local_items, local_sources, secret, f"local path {path}")

    imported_items.update(local_items)
    return imported_items


def nomad_items_size(items: Mapping[str, str]) -> int:
    return sum(len(key.encode("utf-8")) + len(value.encode("utf-8")) for key, value in items.items())


def validate_nomad_items(items: Mapping[str, str]) -> int:
    size = nomad_items_size(items)
    if size > NOMAD_ITEMS_LIMIT_BYTES:
        raise SecretDataError(
            f"Nomad Items size is {size} bytes; limit is {NOMAD_ITEMS_LIMIT_BYTES} bytes"
        )
    return size


def get_nomad_variable_items(
    session: requests.Session, config: Config
) -> dict[str, str]:
    encoded_path = quote(config.nomad_var_path, safe="/-_~")
    try:
        response = session.get(
            f"{config.nomad_addr}/v1/var/{encoded_path}",
            headers={"Authorization": f"Bearer {config.nomad_token}"},
            params={"namespace": config.nomad_namespace},
            timeout=config.timeout_seconds,
        )
    except requests.RequestException as exc:
        raise NomadError(f"Variable read request failed: {exc}") from exc
    if response.status_code == 404:
        return {}
    if not response.ok:
        raise NomadError(
            f"Variable read failed with HTTP {response.status_code}: {safe_api_message(response)}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise NomadError("Variable read response was not valid JSON") from exc
    items = payload.get("Items") if isinstance(payload, dict) else None
    if not isinstance(items, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in items.items()
    ):
        raise NomadError("Variable read response did not contain valid string Items")
    return dict(items)


def build_target_items(
    desired: Mapping[str, str], current: Mapping[str, str], sync_mode: str
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Build the Nomad Items map and return sorted Nomad-only keys."""
    orphans = tuple(sorted(current.keys() - desired.keys()))
    if sync_mode == "full":
        return dict(desired), orphans
    if sync_mode == "leave-orphans":
        target = dict(current)
        target.update(desired)
        return target, orphans
    raise ConfigError("SYNC_MODE must be one of: full, leave-orphans")


def put_nomad_variable(
    session: requests.Session, config: Config, items: Mapping[str, str]
) -> None:
    encoded_path = quote(config.nomad_var_path, safe="/-_~")
    payload = {
        "Namespace": config.nomad_namespace,
        "Path": config.nomad_var_path,
        "Items": dict(items),
    }
    try:
        response = session.put(
            f"{config.nomad_addr}/v1/var/{encoded_path}",
            # Workload identities are JWTs and must be presented as a Bearer
            # credential. X-Nomad-Token is for Nomad ACL SecretIDs.
            headers={"Authorization": f"Bearer {config.nomad_token}"},
            params={"namespace": config.nomad_namespace},
            json=payload,
            timeout=config.timeout_seconds,
        )
    except requests.RequestException as exc:
        raise NomadError(f"Variable write request failed: {exc}") from exc
    if not response.ok:
        raise NomadError(
            f"Variable write failed with HTTP {response.status_code}: {safe_api_message(response)}"
        )


def run(config: Config, session: requests.Session | None = None) -> None:
    http = session or requests.Session()
    LOGGER.info("Authenticating to Infisical with Nomad workload identity")
    access_token = infisical_login(http, config)
    project_id = resolve_project_id(http, config, access_token)
    LOGGER.info(
        "Fetching Infisical secrets: project=%s project_id=%s environment=%s path=%s",
        config.infisical_project_slug or config.infisical_project_id,
        project_id,
        config.infisical_environment,
        config.infisical_secret_path,
    )
    response = fetch_secrets(http, config, access_token, project_id)
    desired_items = secrets_to_items(response)
    current_items = get_nomad_variable_items(http, config)
    items, orphans = build_target_items(
        desired_items, current_items, config.sync_mode
    )
    if orphans:
        action = "remove" if config.sync_mode == "full" else "preserve"
        LOGGER.info(
            "Found %d orphaned Nomad secrets: %s; sync_mode=%s action=%s",
            len(orphans),
            ", ".join(orphans),
            config.sync_mode,
            action,
        )
    else:
        LOGGER.info("Found 0 orphaned Nomad secrets; sync_mode=%s", config.sync_mode)
    size = validate_nomad_items(items)
    LOGGER.info(
        "Writing %d secrets (%d bytes) to Nomad namespace=%s path=%s",
        len(items),
        size,
        config.nomad_namespace,
        config.nomad_var_path,
    )
    put_nomad_variable(http, config, items)
    LOGGER.info("Synchronization completed successfully")


def main(argv: Sequence[str] | None = None) -> int:
    del argv  # Environment-only interface by design.
    try:
        config = Config.from_env()
        configure_logging(config.log_level)
        run(config)
        return 0
    except ConfigError as exc:
        configure_logging("INFO")
        LOGGER.error("Configuration error: %s", exc)
        return 2
    except InfisicalError as exc:
        LOGGER.error("Infisical error: %s", exc)
        return 3
    except SecretDataError as exc:
        LOGGER.error("Secret data error: %s", exc)
        return 4
    except NomadError as exc:
        LOGGER.error("Nomad error: %s", exc)
        return 5
    except Exception:
        LOGGER.exception("Unexpected synchronization failure")
        return 1


if __name__ == "__main__":
    sys.exit(main())
