"""Tests for pipeline.truthcert — SHA-256 provenance chain."""
from __future__ import annotations

import pytest

from pipeline.truthcert import (
    TruthCertError,
    compute_chain,
    get_hmac_key,
    verify_chain,
)


def test_get_hmac_key_reads_env_var(monkeypatch):
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "my-secret-key-1234")
    key = get_hmac_key()
    assert key == b"my-secret-key-1234"


def test_get_hmac_key_missing_raises(monkeypatch):
    monkeypatch.delenv("CONCORDANCEMAP_HMAC_KEY", raising=False)
    with pytest.raises(TruthCertError, match="CONCORDANCEMAP_HMAC_KEY"):
        get_hmac_key()


def test_get_hmac_key_empty_raises(monkeypatch):
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "")
    with pytest.raises(TruthCertError, match="empty"):
        get_hmac_key()


def test_get_hmac_key_whitespace_only_raises(monkeypatch):
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "   \t\n ")
    with pytest.raises(TruthCertError, match="empty"):
        get_hmac_key()


def test_get_hmac_key_leading_whitespace_raises(monkeypatch):
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", " my-secret-key-1234")
    with pytest.raises(TruthCertError, match="whitespace"):
        get_hmac_key()


def test_get_hmac_key_trailing_whitespace_raises(monkeypatch):
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "my-secret-key-1234 ")
    with pytest.raises(TruthCertError, match="whitespace"):
        get_hmac_key()


def test_get_hmac_key_too_short_raises(monkeypatch):
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "shortkey")
    with pytest.raises(TruthCertError, match="at least 16 bytes"):
        get_hmac_key()


def test_get_hmac_key_exactly_min_length_accepted(monkeypatch):
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "x" * 16)
    assert get_hmac_key() == b"x" * 16


def test_compute_chain_is_deterministic(monkeypatch):
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "k" * 16)
    components = {"pmids": ["1", "2", "3"], "delta": 0.42}
    a = compute_chain(components)
    b = compute_chain(components)
    assert a == b
    assert isinstance(a, str)
    assert len(a) == 64  # SHA-256 hex


def test_compute_chain_differs_on_input_change(monkeypatch):
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "k" * 16)
    a = compute_chain({"x": 1})
    b = compute_chain({"x": 2})
    assert a != b


def test_compute_chain_differs_on_key_change(monkeypatch):
    components = {"x": 1}
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "key-a" + "z" * 11)
    a = compute_chain(components)
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "key-b" + "z" * 11)
    b = compute_chain(components)
    assert a != b


def test_compute_chain_is_order_invariant(monkeypatch):
    """Dict key order must not affect the hash (JSON sorted)."""
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "k" * 16)
    a = compute_chain({"x": 1, "y": 2})
    b = compute_chain({"y": 2, "x": 1})
    assert a == b


def test_verify_chain_passes_on_untampered(monkeypatch):
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "k" * 16)
    components = {"pmids": ["1", "2"], "delta": 0.1}
    chain = compute_chain(components)
    assert verify_chain(components, chain) is True


def test_verify_chain_fails_on_tampered(monkeypatch):
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "k" * 16)
    components = {"pmids": ["1", "2"], "delta": 0.1}
    chain = compute_chain(components)
    tampered = {"pmids": ["1", "2"], "delta": 0.9}
    assert verify_chain(tampered, chain) is False


def test_verify_chain_uses_constant_time_compare(monkeypatch):
    """Must call hmac.compare_digest, never ==."""
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "k" * 16)
    calls: list[tuple] = []
    import hmac as _hmac

    real_compare = _hmac.compare_digest
    monkeypatch.setattr(
        "pipeline.truthcert.hmac.compare_digest",
        lambda a, b: (calls.append((a, b)), real_compare(a, b))[1],
    )
    components = {"x": 1}
    chain = compute_chain(components)
    verify_chain(components, chain)
    assert len(calls) >= 1


def test_verify_chain_fails_on_wrong_length_digest(monkeypatch):
    """Truncated/extended digests must not crash and must return False."""
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "k" * 16)
    components = {"x": 1}
    chain = compute_chain(components)
    assert verify_chain(components, chain[:-1]) is False
    assert verify_chain(components, chain + "0") is False


def test_compute_chain_is_order_invariant_nested(monkeypatch):
    """Nested dict key order must not affect the hash (sort_keys is recursive)."""
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "k" * 16)
    a = compute_chain({"meta": {"z": 1, "a": 2}})
    b = compute_chain({"meta": {"a": 2, "z": 1}})
    assert a == b


def test_compute_chain_empty_components(monkeypatch):
    """Empty dict produces a stable 64-char SHA-256 hex digest."""
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "k" * 16)
    result = compute_chain({})
    assert isinstance(result, str)
    assert len(result) == 64


def test_verify_chain_rejects_non_str_expected(monkeypatch):
    """Bytes (or any non-str) for expected raises TruthCertError, not TypeError."""
    monkeypatch.setenv("CONCORDANCEMAP_HMAC_KEY", "k" * 16)
    components = {"x": 1}
    chain = compute_chain(components)
    with pytest.raises(TruthCertError, match="must be a str"):
        verify_chain(components, chain.encode("ascii"))
