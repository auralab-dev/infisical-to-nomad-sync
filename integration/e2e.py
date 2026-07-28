#!/usr/bin/env python3
"""Provision and exercise the real Nomad + Infisical integration stack."""

from __future__ import annotations

import subprocess
import sys
import time

from .bootstrap import (
    bootstrap_infisical,
    bootstrap_nomad,
    put_stale_variable,
    submit_job,
)
from .config import (
    E2EError,
    EXPECTED_ITEMS,
    INFISICAL_URL,
    JOB_ID,
    NAMESPACE,
    NOMAD_URL,
    TASK,
    TIMEOUT,
    UNAUTHORIZED_JOB_ID,
    VAR_PATH,
    log,
)
from .http_client import nomad_api, nomad_task_logs, wait_for

# ---------------------------------------------------------------------------
# Job lifecycle helpers
# ---------------------------------------------------------------------------


def wait_for_job(job_id: str, token: str) -> dict:
    """Poll the Nomad job until it reaches a terminal state, then return
    the first allocation."""
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        allocations = nomad_api(
            "GET", f"/v1/job/{job_id}/allocations", token=token
        )
        if allocations:
            allocation = allocations[0]
            task_state = allocation.get("TaskStates", {}).get(TASK, {})
            state = task_state.get("State")
            if state in {"complete", "dead", "failed"}:
                return allocation
        time.sleep(2)
    raise E2EError(f"timed out waiting for Nomad job {job_id}")


def terminal_exit_code(task_state: dict) -> int | None:
    """Extract the exit code from the terminal Task event."""
    for event in reversed(task_state.get("Events", [])):
        if event.get("Type") == "Terminated" and event.get("ExitCode") is not None:
            return event["ExitCode"]
    return None


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


def assert_success(allocation: dict, token: str) -> None:
    """Verify the authorized job completed and wrote the expected secrets."""
    task_state = allocation["TaskStates"][TASK]
    exit_code = terminal_exit_code(task_state)
    if (
        task_state.get("State") not in {"complete", "dead"}
        or task_state.get("Failed")
        or exit_code != 0
    ):
        stderr = nomad_task_logs(allocation, token, "stderr")
        stdout = nomad_task_logs(allocation, token, "stdout")
        raise E2EError(
            f"Nomad job {allocation.get('JobID', JOB_ID)!r} failed unexpectedly "
            f"in task {TASK!r} with exit code "
            f"{exit_code if exit_code is not None else 'unknown'}.\n"
            f"task stderr:\n{stderr}\n"
            f"task stdout:\n{stdout}"
        )
    variable = nomad_api(
        "GET", f"/v1/var/{VAR_PATH}?namespace={NAMESPACE}", token=token
    )
    actual = variable.get("Items") if isinstance(variable, dict) else None
    if actual != EXPECTED_ITEMS:
        actual_keys = sorted(actual) if isinstance(actual, dict) else None
        raise E2EError(
            "Nomad variable contents did not match expected keys: "
            f"actual={actual_keys}, expected={sorted(EXPECTED_ITEMS)}"
        )
    log("Happy-path sync and replace semantics passed")


def assert_infisical_rejection(allocation: dict) -> None:
    """Verify the unauthorized job was rejected by Infisical with exit code 3."""
    task_state = allocation["TaskStates"][TASK]
    events = task_state.get("Events", [])
    exit_codes = [
        event.get("ExitCode")
        for event in events
        if event.get("ExitCode") is not None
    ]
    if task_state.get("State") not in {"dead", "failed"} or 3 not in exit_codes:
        raise E2EError(
            "unauthorized workload test produced an unexpected result; "
            "expected Infisical rejection with exit code 3, "
            f"got state={task_state.get('State')!r}, exit_codes={exit_codes}"
        )
    log("Infisical workload-claim rejection passed")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def main() -> int:
    """Run the full E2E test suite and return 0 on success, 1 on failure."""
    management_token = ""
    try:
        wait_for(f"{INFISICAL_URL}/api/status", "Infisical")
        wait_for(f"{NOMAD_URL}/v1/status/leader", "Nomad")
        _, _, identity_id = bootstrap_infisical()
        management_token = bootstrap_nomad()
        put_stale_variable(management_token)
        submit_job(JOB_ID, identity_id, management_token)
        assert_success(
            wait_for_job(JOB_ID, management_token), management_token
        )
        submit_job(UNAUTHORIZED_JOB_ID, identity_id, management_token)
        assert_infisical_rejection(
            wait_for_job(UNAUTHORIZED_JOB_ID, management_token)
        )
        log("All integration tests passed")
        return 0
    except (E2EError, OSError, subprocess.SubprocessError) as exc:
        print(f"[e2e] ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        # Keep jobs and allocations available in the Nomad UI for
        # inspection.  Tear down the environment with:
        #   docker compose --file integration/docker-compose.yml \
        #       down --volumes --remove-orphans
        del management_token


if __name__ == "__main__":
    sys.exit(main())
