from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from kalshi_edge.trading import vault


def sample_pem():
    key = Ed25519PrivateKey.generate()
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()
    ).decode("utf-8")


def test_dpapi_vault_never_writes_plaintext(tmp_path, monkeypatch):
    # Mock DPAPI only; actual Windows encryption is not exercised in this unit test.
    def reversible(payload, *, decrypt):
        return bytes(b ^ 0x5A for b in payload)
    monkeypatch.setattr(vault, "_dpapi", reversible)
    path = tmp_path / "credentials.dpapi"
    store = vault.CredentialVault(path)
    pem = sample_pem()
    store.save("key-id-001", pem)
    raw = path.read_bytes()
    assert b"BEGIN PRIVATE KEY" not in raw
    assert b"key-id-001" not in raw
    assert store.load() == ("key-id-001", pem)
    store.delete()
    assert not store.exists()


def test_invalid_key_rejected_before_disk_write(tmp_path):
    store = vault.CredentialVault(tmp_path / "creds.dpapi")
    with pytest.raises(vault.VaultError, match="private key"):
        store.save("id", "not a PEM")
    assert not store.exists()


def test_portable_fail_closed_when_dpapi_unavailable():
    if vault.os.name != "nt":
        with pytest.raises(vault.VaultError, match="Windows DPAPI"):
            vault._dpapi(b"secret", decrypt=False)

def test_malformed_api_id_never_persists(tmp_path):
    store = vault.CredentialVault(tmp_path / "creds.dpapi")
    with pytest.raises(vault.VaultError, match="API key ID"):
        store.save("bad\\r\\nX-Header: injection", sample_pem())
    assert not store.exists()
