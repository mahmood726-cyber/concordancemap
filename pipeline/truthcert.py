"""SHA-256 + HMAC provenance chain for ConcordanceMap.

HMAC key is read from env var CONCORDANCEMAP_HMAC_KEY only. Never from
the bundle itself (2026-04-14 portfolio crypto lesson — using bundle-
derived data as the HMAC key makes forgery trivial).
"""
from __future__ import annotations

import os


class TruthCertError(Exception):
    """TruthCert configuration or verification failure."""


_HMAC_ENV_VAR = "CONCORDANCEMAP_HMAC_KEY"
_MIN_KEY_BYTES = 16


def get_hmac_key() -> bytes:
    """Return HMAC key bytes from env. Fails closed if missing or empty.

    Never silently defaults — missing key must halt the pipeline.
    Rejects leading/trailing whitespace (silent MAC mismatch otherwise)
    and keys shorter than 16 bytes (128-bit minimum).
    """
    value = os.environ.get(_HMAC_ENV_VAR)
    if value is None:
        raise TruthCertError(
            f"{_HMAC_ENV_VAR} environment variable is not set. "
            f"TruthCert cannot proceed without an explicit HMAC key."
        )
    if not value.strip():
        raise TruthCertError(
            f"{_HMAC_ENV_VAR} is empty or whitespace-only; set a real key."
        )
    if value != value.strip():
        raise TruthCertError(
            f"{_HMAC_ENV_VAR} has leading or trailing whitespace; "
            f"strip it or quote the value correctly."
        )
    encoded = value.encode("utf-8")
    if len(encoded) < _MIN_KEY_BYTES:
        raise TruthCertError(
            f"{_HMAC_ENV_VAR} must be at least {_MIN_KEY_BYTES} bytes "
            f"(got {len(encoded)}); use a randomly generated key."
        )
    return encoded
