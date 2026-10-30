"""Tests for single-credential source shared by main website and live Kalshi feed."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from kalshi_edge.bootstrap.live_kalshi import resolve_live_credentials
from kalshi_edge.trading.vault import VaultError


def pem() -> str:
    return Ed25519PrivateKey.generate().private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


class VaultStub:
    def __init__(self, exists: bool, private: str | None = None, fail: bool = False):
        self.present = exists
        self.private = private
        self.fail = fail
    def exists(self):
        return self.present
    def load(self):
        if self.fail:
            raise VaultError("encrypted vault cannot be decrypted")
        return "new-api-key-id", self.private


def test_saved_main_website_credentials_override_deleted_env_key() -> None:
    saved = pem()
    def obsolete_credentials():
        raise AssertionError("old revoked .env key must not be loaded")
    settings = SimpleNamespace(require_credentials=obsolete_credentials)
    kid, key = resolve_live_credentials(settings, vault=VaultStub(True, saved))
    assert kid == "new-api-key-id"
    assert isinstance(key, Ed25519PrivateKey)


def test_legacy_env_path_still_supported_when_no_vault(tmp_path: Path) -> None:
    text = pem()
    path = tmp_path / "existing.pem"
    path.write_text(text, encoding="utf-8")
    settings = SimpleNamespace(require_credentials=lambda: ("legacy-id", path))
    kid, key = resolve_live_credentials(settings, vault=VaultStub(False))
    assert kid == "legacy-id"
    assert isinstance(key, Ed25519PrivateKey)


def test_broken_saved_vault_fails_closed_without_using_old_key() -> None:
    settings = SimpleNamespace(
        require_credentials=lambda: (_ for _ in ()).throw(AssertionError("fallback not allowed"))
    )
    with pytest.raises(VaultError, match="cannot be decrypted"):
        resolve_live_credentials(settings, vault=VaultStub(True, fail=True))


def test_bad_vault_key_fails_closed() -> None:
    settings = SimpleNamespace(
        require_credentials=lambda: (_ for _ in ()).throw(AssertionError("fallback not allowed"))
    )
    with pytest.raises((TypeError, ValueError)):
        resolve_live_credentials(settings, vault=VaultStub(True, "invalid key"))
