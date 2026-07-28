"""Provision Infisical and Nomad with the fixtures needed for E2E tests."""

from __future__ import annotations

import json
import sys

from .compose import nomad_cli
from .config import (
    E2EError,
    ENVIRONMENT,
    GROUP,
    INFISICAL_URL,
    JOB_ID,
    NAMESPACE,
    NOMAD_URL,
    PROJECT_SLUG,
    ORPHAN_KEY,
    ORPHAN_VALUE,
    ROOT,
    SECRET_PATH,
    SECRETS,
    TASK,
    UNAUTHORIZED_JOB_ID,
    VAR_PATH,
    log,
)
from .crypto import nomad_signing_key_pem
from .http_client import api_json, nomad_api


def bootstrap_infisical() -> tuple[str, str, str]:
    """Create org, project, JWT identity, folder, and test secrets.

    Returns ``(admin_token, project_id, identity_id)``.
    """
    log("Bootstrapping Infisical")

    nomad_public_key = nomad_signing_key_pem()

    result = api_json(
        "POST",
        f"{INFISICAL_URL}/api/v1/admin/bootstrap",
        body={
            "email": "e2e-admin@example.test",
            "password": "e2e-admin-password-123!",
            "organization": "Nomad Sync E2E",
        },
    )
    try:
        token = result["identity"]["credentials"]["token"]
        org_id = result["organization"]["id"]
    except (KeyError, TypeError) as exc:
        raise E2EError(
            "Infisical bootstrap response is missing identity or organization"
        ) from exc

    project_result = api_json(
        "POST",
        f"{INFISICAL_URL}/api/v1/projects",
        token=token,
        body={
            "projectName": "Nomad Sync E2E",
            "projectDescription": "Ephemeral integration-test project",
            "slug": PROJECT_SLUG,
            "template": "default",
            "type": "secret-manager",
        },
    )
    try:
        project_id = project_result["project"]["id"]
    except (KeyError, TypeError) as exc:
        raise E2EError(
            "Infisical project response is missing project.id"
        ) from exc

    identity_result = api_json(
        "POST",
        f"{INFISICAL_URL}/api/v1/projects/{project_id}/identities",
        token=token,
        body={"name": "Nomad Sync E2E", "roles": [{"role": "viewer"}]},
    )
    try:
        identity_id = identity_result["identity"]["id"]
    except (KeyError, TypeError) as exc:
        raise E2EError(
            "Infisical identity response is missing identity.id"
        ) from exc

    api_json(
        "POST",
        f"{INFISICAL_URL}/api/v2/folders",
        token=token,
        body={
            "projectId": project_id,
            "environment": ENVIRONMENT,
            "name": "apps",
            "path": "/",
        },
    )

    api_json(
        "POST",
        f"{INFISICAL_URL}/api/v1/auth/jwt-auth/identities/{identity_id}",
        token=token,
        body={
            "configurationType": "static",
            "publicKeys": [nomad_public_key],
            "boundAudiences": "nomadproject.io",
            "boundClaims": {
                "nomad_namespace": NAMESPACE,
                "nomad_job_id": JOB_ID,
                "nomad_task": TASK,
            },
            "accessTokenTTL": 300,
            "accessTokenMaxTTL": 300,
        },
    )

    for name, (value, branch) in SECRETS.items():
        api_json(
            "POST",
            f"{INFISICAL_URL}/api/v4/secrets/{name}",
            token=token,
            body={
                "projectId": project_id,
                "environment": ENVIRONMENT,
                "secretPath": SECRET_PATH,
                "secretValue": value,
                "secretMetadata": [{"key": "branch", "value": branch}],
                "type": "shared",
            },
        )

    log("Infisical project, JWT identity, and test secrets are ready")
    return token, project_id, identity_id


_NOMAD_WORKLOAD_POLICY = """\
namespace "{namespace}" {{
  variables {{
    path "{path}" {{
      # Nomad requires read access on the target path when a write
      # returns the resulting variable object.
      capabilities = ["write", "read"]
    }}
  }}
}}
"""


def bootstrap_nomad() -> str:
    """Bootstrap ACLs and create workload-associated policies.

    Returns the Nomad management token.
    """
    log("Bootstrapping Nomad ACLs")
    result = nomad_cli("acl", "bootstrap", "-json")
    try:
        management_token = json.loads(result)["SecretID"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise E2EError(
            "Nomad ACL bootstrap did not return SecretID JSON"
        ) from exc

    policy = _NOMAD_WORKLOAD_POLICY.format(namespace=NAMESPACE, path=VAR_PATH)
    for job_id in (JOB_ID, UNAUTHORIZED_JOB_ID):
        policy_name = f"infisical-sync-e2e-writer-{job_id}"
        nomad_cli(
            "acl",
            "policy",
            "apply",
            f"-namespace={NAMESPACE}",
            f"-job={job_id}",
            f"-group={GROUP}",
            f"-task={TASK}",
            policy_name,
            "-",
            token=management_token,
            input_data=policy,
        )
        attached = nomad_api(
            "GET",
            f"/v1/acl/policy/{policy_name}",
            token=management_token,
        )
        job_acl = attached.get("JobACL", {}) if isinstance(attached, dict) else {}
        expected_acl = {
            "Namespace": NAMESPACE,
            "JobID": job_id,
            "Group": GROUP,
            "Task": TASK,
        }
        if not isinstance(attached, dict) or any(
            job_acl.get(key) != value for key, value in expected_acl.items()
        ):
            raise E2EError(
                f"Nomad workload policy {policy_name!r} was not attached to "
                f"job={job_id!r}, group={GROUP!r}, task={TASK!r}"
            )

    print(
        f"[e2e] DEBUG Nomad management token: {management_token}",
        file=sys.stderr,
        flush=True,
    )

    log("Nomad workload-associated policies are ready")
    return management_token


def put_stale_variable(token: str, include_existing_secret: bool = False) -> None:
    """Write an orphan and optionally a stale managed value before a sync."""
    items = {ORPHAN_KEY: ORPHAN_VALUE}
    if include_existing_secret:
        items["API_KEY"] = "stale-infisical-value"
    nomad_api(
        "PUT",
        f"/v1/var/{VAR_PATH}",
        token=token,
        body={
            "Namespace": NAMESPACE,
            "Path": VAR_PATH,
            "Items": items,
        },
    )


def render_job(
    job_id: str, identity_id: str, sync_mode: str = "leave-orphans"
) -> str:
    """Load the Nomad job template and substitute runtime values."""
    template = (ROOT / "integration" / "e2e-job.nomad.hcl").read_text()
    return (
        template.replace('job "infisical-sync-e2e"', f'job "{job_id}"')
        .replace("__INFISICAL_IDENTITY_ID__", identity_id)
        .replace("__INFISICAL_PROJECT_SLUG__", PROJECT_SLUG)
        .replace("__SYNC_MODE__", sync_mode)
    )


def submit_job(
    job_id: str,
    identity_id: str,
    token: str,
    sync_mode: str = "leave-orphans",
) -> None:
    """Parse and register a Nomad batch job through the HTTP API."""
    log(f"Submitting Nomad job {job_id}")
    parsed = nomad_api(
        "POST",
        f"/v1/jobs/parse?namespace={NAMESPACE}",
        token=token,
        body={
            "JobHCL": render_job(job_id, identity_id, sync_mode),
            "Canonicalize": True,
        },
    )
    job = parsed.get("Job", parsed) if isinstance(parsed, dict) else None
    if not isinstance(job, dict):
        raise E2EError("Nomad job parser did not return a job object")
    nomad_api(
        "POST",
        f"/v1/jobs?namespace={NAMESPACE}",
        token=token,
        body={"Job": job},
    )
