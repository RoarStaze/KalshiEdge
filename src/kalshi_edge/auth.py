from __future__ import annotations

"""Kalshi request authentication for RSA-PSS and Ed25519 API keys.

This module signs requests only. It never places orders or logs secrets.
"""

import base64
import time
from pathlib import Path
from typing import Mapping, TypeAlias

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa


KalshiPrivateKey: TypeAlias = rsa.RSAPrivateKey | ed25519.Ed25519PrivateKey


def load_private_key_pem(pem: str | bytes) -> KalshiPrivateKey:
    """Validate a PEM private key without writing it to disk."""
    content = pem.encode("utf-8") if isinstance(pem, str) else pem
    key = serialization.load_pem_private_key(content, password=None)
    if not isinstance(key, (rsa.RSAPrivateKey, ed25519.Ed25519PrivateKey)):
        raise TypeError("Kalshi API key must be RSA or Ed25519")
    return key


def load_private_key(path: str | Path) -> KalshiPrivateKey:
    """Read an existing PEM file; supports both Kalshi key algorithms."""
    with Path(path).open("rb") as handle:
        return load_private_key_pem(handle.read())


def sign_pss_text(private_key: rsa.RSAPrivateKey, text: str) -> str:
    """Preserve legacy RSA-PSS SHA-256 signing behavior."""
    signature = private_key.sign(
        text.encode("utf-8"),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def sign_request_text(private_key: KalshiPrivateKey, text: str) -> str:
    """Sign a Kalshi pre-sign text using its registered private key type."""
    if isinstance(private_key, ed25519.Ed25519PrivateKey):
        signature = private_key.sign(text.encode("utf-8"))
        return base64.b64encode(signature).decode("ascii")
    if isinstance(private_key, rsa.RSAPrivateKey):
        return sign_pss_text(private_key, text)
    raise TypeError("Kalshi API key must be RSA or Ed25519")


def create_auth_headers(
    *,
    key_id: str,
    private_key: KalshiPrivateKey,
    method: str,
    path: str,
    timestamp_ms: int | None = None,
) -> Mapping[str, str]:
    ts = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    clean_path = path.split("?", 1)[0]
    message = f"{ts}{method.upper()}{clean_path}"
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": sign_request_text(private_key, message),
        "KALSHI-ACCESS-TIMESTAMP": str(ts),
    }
