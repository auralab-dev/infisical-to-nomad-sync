"""Shared constants and helpers for the integration test suite."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "integration" / "docker-compose.yml"
NOMAD_URL = os.environ.get("E2E_NOMAD_URL", "http://127.0.0.1:14646")
INFISICAL_URL = os.environ.get("E2E_INFISICAL_URL", "http://127.0.0.1:18080")
TIMEOUT = int(os.environ.get("E2E_TIMEOUT_SECONDS", "240"))
REQUEST_TIMEOUT = float(os.environ.get("E2E_REQUEST_TIMEOUT_SECONDS", "10"))
COMMAND_TIMEOUT = float(os.environ.get("E2E_COMMAND_TIMEOUT_SECONDS", "10"))

JOB_ID = "infisical-sync-e2e"
UNAUTHORIZED_JOB_ID = "infisical-sync-e2e-unauthorized"
GROUP = "sync"
TASK = "sync"
NAMESPACE = "default"
VAR_PATH = "e2e/secrets/application"
ENVIRONMENT = "dev"
SECRET_PATH = "/apps"
PROJECT_SLUG = "nomad-sync-e2e"

SECRETS = {
    "API_KEY": ("integration-api-key", "main"),
    "DATABASE_URL": ("postgres://integration.example/db", "main"),
    "IGNORED_BRANCH_SECRET": ("must-not-arrive", "dev"),
}
EXPECTED_ITEMS = {key: value for key, (value, branch) in SECRETS.items() if branch == "main"}


class E2EError(RuntimeError):
    pass


def log(message: str) -> None:
    print(f"[e2e] {message}", flush=True)
