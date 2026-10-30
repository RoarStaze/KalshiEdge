from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi_edge.auth import create_auth_headers, load_private_key


def test_auth_headers_sign_timestamp_method_and_path_without_query(tmp_path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes_raw() if False else None
    from cryptography.hazmat.primitives import serialization
    key_path = tmp_path / "kalshi.pem"
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    loaded = load_private_key(key_path)
    headers = create_auth_headers(
        key_id="kid-123",
        private_key=loaded,
        method="GET",
        path="/trade-api/v2/portfolio/orders?limit=5",
        timestamp_ms=1_700_000_000_123,
    )

    assert headers["KALSHI-ACCESS-KEY"] == "kid-123"
    assert headers["KALSHI-ACCESS-TIMESTAMP"] == "1700000000123"

    import base64
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    key.public_key().verify(
        signature,
        b"1700000000123GET/trade-api/v2/portfolio/orders",
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )

def test_ed25519_headers_sign_direct_message_and_load_pkcs8(tmp_path: Path) -> None:
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from kalshi_edge.auth import load_private_key_pem

    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    key_path = tmp_path / "kalshi-ed25519.pem"
    key_path.write_bytes(pem)

    for loaded in (load_private_key(key_path), load_private_key_pem(pem.decode())):
        headers = create_auth_headers(
            key_id="new-kid", private_key=loaded,
            method="GET", path="/trade-api/ws/v2?test=1",
            timestamp_ms=1_700_000_123_456,
        )
        signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
        assert len(signature) == 64
        private.public_key().verify(
            signature, b"1700000123456GET/trade-api/ws/v2"
        )


def test_reject_unsupported_private_key_type() -> None:
    import pytest
    with pytest.raises(TypeError, match="RSA or Ed25519"):
        create_auth_headers(
            key_id="id", private_key=object(),
            method="GET", path="/trade-api/ws/v2", timestamp_ms=1234,
        )
