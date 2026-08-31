from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Mapping

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def load_private_key(path: str | Path) -> rsa.RSAPrivateKey:
    with Path(path).open("rb") as handle:
        key = serialization.load_pem_private_key(handle.read(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError("Kalshi API key must be an RSA private key")
    return key


def sign_pss_text(private_key: rsa.RSAPrivateKey, text: str) -> str:
    signature = private_key.sign(
        text.encode("utf-8"),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def create_auth_headers(
    *,
    key_id: str,
    private_key: rsa.RSAPrivateKey,
    method: str,
    path: str,
    timestamp_ms: int | None = None,
) -> Mapping[str, str]:
    ts = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    clean_path = path.split("?", 1)[0]
    message = f"{ts}{method.upper()}{clean_path}"
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": sign_pss_text(private_key, message),
        "KALSHI-ACCESS-TIMESTAMP": str(ts),
    }
