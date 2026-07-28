"""HTTP request helpers shared across the integration test suite."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import E2EError, NOMAD_URL, REQUEST_TIMEOUT, TIMEOUT, TASK


def http_request(
    method: str,
    url: str,
    *,
    body: Any | None = None,
    token: str | None = None,
    token_type: str | None = None,
    label: str = "",
    timeout: float = REQUEST_TIMEOUT,
) -> Any:
    """Send an HTTP request and return the parsed JSON body.

    ``token_type`` controls the ``Authorization`` header prefix (e.g.
    ``"Bearer"`` or ``"X-Nomad-Token"``).  ``label`` is prepended to
    every error message so callers can identify the source.
    """
    headers: dict[str, str] = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token and token_type:
        headers["Authorization"] = f"{token_type} {token}"

    data = json.dumps(body).encode() if body is not None else None
    request = Request(url, data=data, headers=headers, method=method)

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        details = exc.read().decode(errors="replace")[:300]
        raise E2EError(
            f"{label}{method} {url} returned HTTP {exc.code}: {details}"
        ) from exc
    except URLError as exc:
        raise E2EError(f"{label}{method} {url} failed: {exc.reason}") from exc
    except OSError as exc:
        # The service can accept TCP and reset the connection while its
        # migrations / server bootstrap are still completing.  Let
        # wait_for() retry this transient startup condition.
        raise E2EError(
            f"{label}{method} {url} failed during startup: {exc}"
        ) from exc
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise E2EError(f"{label}{method} {url} returned invalid JSON") from exc


def api_json(
    method: str,
    url: str,
    *,
    body: Any | None = None,
    token: str | None = None,
    timeout: float = REQUEST_TIMEOUT,
) -> Any:
    """Convenience wrapper that presents the token as a Bearer credential."""
    return http_request(
        method, url, body=body, token=token, token_type="Bearer", timeout=timeout
    )


def nomad_api(
    method: str, path: str, *, token: str, body: Any | None = None
) -> Any:
    """Convenience wrapper that targets the Nomad API with an ACL token."""
    return http_request(
        method,
        f"{NOMAD_URL}{path}",
        token=token,
        token_type="X-Nomad-Token",
        body=body,
        label="Nomad ",
    )


def wait_for(url: str, label: str) -> None:
    deadline = time.monotonic() + TIMEOUT
    last_error = "not ready"
    while time.monotonic() < deadline:
        try:
            http_request("GET", url, label=f"{label} ")
            print(f"[e2e] {label} is ready", flush=True)
            return
        except E2EError as exc:
            last_error = str(exc)
            time.sleep(2)
    raise E2EError(f"timed out waiting for {label}: {last_error}")


def nomad_task_logs(
    allocation: dict[str, Any], token: str, log_type: str
) -> str:
    query = urlencode(
        {
            "task": TASK,
            "type": log_type,
            "follow": "false",
            "plain": "true",
        }
    )
    request = Request(
        f"{NOMAD_URL}/v1/client/fs/logs/{allocation['ID']}?{query}",
        headers={"X-Nomad-Token": token},
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return response.read().decode(errors="replace")[-4000:]
    except (HTTPError, URLError, OSError) as exc:
        return f"unable to read {log_type}: {exc}"
