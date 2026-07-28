"""Docker Compose and Nomad CLI wrappers for integration tests."""

from __future__ import annotations

import subprocess

from .config import COMMAND_TIMEOUT, COMPOSE_FILE, E2EError, ROOT


def compose(
    *args: str, input_data: str | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run ``docker compose``, appending ``--file`` automatically."""
    command = [
        "docker",
        "compose",
        "--file",
        str(COMPOSE_FILE),
        *args,
    ]
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            input=input_data,
            text=True,
            capture_output=True,
            check=False,
            timeout=COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        safe_args = [
            "<redacted>" if arg.startswith("NOMAD_TOKEN=") else arg
            for arg in args
        ]
        raise E2EError(
            f"docker compose command timed out after {COMMAND_TIMEOUT:g}s: "
            f"{' '.join(safe_args)}"
        ) from exc
    if check and result.returncode != 0:
        safe_args = [
            "-token=<redacted>"
            if arg.startswith("-token=")
            else "NOMAD_TOKEN=<redacted>"
            if arg.startswith("NOMAD_TOKEN=")
            else arg
            for arg in args
        ]
        raise E2EError(
            f"docker compose command failed ({' '.join(safe_args)}): "
            f"{result.stderr[-1000:]}"
        )
    return result


def nomad_cli(
    *args: str, token: str | None = None, input_data: str | None = None
) -> str:
    """Run a Nomad CLI command inside the `nomad` container."""
    command = ["exec", "-T"]
    if token:
        command.extend(["-e", f"NOMAD_TOKEN={token}"])
    command.extend(["nomad", "nomad", *args])
    result = compose(*command, input_data=input_data)
    return result.stdout
