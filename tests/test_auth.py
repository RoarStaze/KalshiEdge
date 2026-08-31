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
