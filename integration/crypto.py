"""RSA JWK-to-PEM conversion for Infisical static JWT configuration."""

from __future__ import annotations

import base64
from typing import Any

from .config import NOMAD_URL, REQUEST_TIMEOUT, E2EError
from .http_client import http_request


def _der_length(length: int) -> bytes:
    if length < 128:
        return bytes([length])
    encoded = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(encoded)]) + encoded


def _der_integer(value: bytes) -> bytes:
    value = value.lstrip(b"\x00") or b"\x00"
    if value[0] & 0x80:
        value = b"\x00" + value
    return b"\x02" + _der_length(len(value)) + value


def rsa_jwk_to_pem(key: dict[str, Any]) -> str:
    """Convert a JWK RSA public key to PEM (PKCS#1 / RFC 8017 Appendix C)."""
    if key.get("kty") != "RSA" or not key.get("n") or not key.get("e"):
        raise E2EError("Nomad JWKS did not contain an RSA signing key")

    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    rsa_public_key = _der_integer(_decode(key["n"])) + _der_integer(_decode(key["e"]))
    der = (
        b"\x30"  # SEQUENCE
        + _der_length(len(rsa_public_key))
        + rsa_public_key
    )
    encoded = base64.b64encode(der).decode()
    lines = [encoded[i : i + 64] for i in range(0, len(encoded), 64)]
    return (
        "-----BEGIN RSA PUBLIC KEY-----\n"
        + "\n".join(lines)
        + "\n-----END RSA PUBLIC KEY-----\n"
    )


def nomad_signing_key_pem() -> str:
    """Fetch Nomad's JWKS and return the RSA signing key as a PEM string."""
    result = http_request(
        "GET",
        f"{NOMAD_URL}/.well-known/jwks.json",
        timeout=REQUEST_TIMEOUT,
    )
    try:
        keys = result["keys"]
        signing_key = next(key for key in keys if key.get("use") == "sig")
    except (KeyError, TypeError, StopIteration) as exc:
        raise E2EError("Nomad JWKS did not contain a signing key") from exc
    return rsa_jwk_to_pem(signing_key)
