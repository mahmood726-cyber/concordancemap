"""SHA-256 + HMAC provenance chain for ConcordanceMap.

HMAC key is read from env var CONCORDANCEMAP_HMAC_KEY only. Never from
the bundle itself (2026-04-14 portfolio crypto lesson — using bundle-
derived data as the HMAC key makes forgery trivial).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any


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


def _canonical_payload(components: dict[str, Any]) -> bytes:
    """Canonical JSON for hashing: recursively-sorted keys, compact, UTF-8.

    ensure_ascii=False emits non-ASCII as raw UTF-8 bytes (not \\uXXXX
    escapes). Any external verifier MUST use the same setting or it will
    compute a different MAC for the same logical input. Float values are
    serialized as-is via json.dumps; callers must not pre-normalize or
    round floats, since 0.1+0.2 and 0.3 have different IEEE-754 bit
    patterns and therefore different MACs.
    """
    return json.dumps(
        components, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def compute_chain(components: dict[str, Any]) -> str:
    """Return the HMAC-SHA256 hex digest of the canonical components payload.

    The HMAC key is read from CONCORDANCEMAP_HMAC_KEY env var (via
    get_hmac_key), never from components itself.
    """
    key = get_hmac_key()
    payload = _canonical_payload(components)
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def verify_chain(components: dict[str, Any], expected: str) -> bool:
    """Constant-time verify of an HMAC chain against recomputed value.

    Raises TruthCertError (not TypeError) if expected is not a str —
    callers should not have to handle two different exception types
    for what is fundamentally a configuration/usage error.
    """
    if not isinstance(expected, str):
        raise TruthCertError(
            f"verify_chain: expected must be a str hex digest, "
            f"got {type(expected).__name__}"
        )
    actual = compute_chain(components)
    return hmac.compare_digest(actual, expected)
