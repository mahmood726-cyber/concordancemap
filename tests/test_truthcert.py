"""Tests for pipeline.truthcert — SHA-256 provenance chain."""
from __future__ import annotations

import pytest

from pipeline.truthcert import TruthCertError, get_hmac_key


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
