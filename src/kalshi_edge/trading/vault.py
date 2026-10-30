"""Windows user-bound credential vault for Kalshi API keys.

PEM material is encrypted with DPAPI at rest. This module never submits orders.
Do not place the vault path inside the Git repository.
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import os
import re
from pathlib import Path

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa


class VaultError(RuntimeError):
    pass


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi(payload: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise VaultError("Windows DPAPI is required; credentials were not saved")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    func = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    func.restype = wintypes.BOOL
    if decrypt:
        func.argtypes = [
            ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
        ]
    else:
        func.argtypes = [
            ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
        ]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(payload)
    source = _Blob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    dest = _Blob()
    # CRYPTPROTECT_UI_FORBIDDEN: never show an OS interactive dialog from the server.
    if decrypt:
        ok = func(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(dest))
    else:
        ok = func(ctypes.byref(source), "KalshiEdge local trading credentials", None, None, None, 1, ctypes.byref(dest))
    if not ok:
        raise VaultError(f"DPAPI operation failed (Windows error {ctypes.get_last_error()})")
    try:
        return ctypes.string_at(dest.pbData, dest.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(dest.pbData, ctypes.c_void_p))


def default_vault_path() -> Path:
    directory = os.getenv("LOCALAPPDATA")
    if not directory:
        raise VaultError("LOCALAPPDATA is unavailable")
    return Path(directory) / "KalshiEdge" / "autotrader-credentials.dpapi"


def validate_key(api_key_id: str, pem: str) -> None:
    if not isinstance(api_key_id, str) or re.fullmatch(r"[A-Za-z0-9-]{1,150}", api_key_id) is None:
        raise VaultError("invalid API key ID")
    if not isinstance(pem, str) or len(pem) > 16000:
        raise VaultError("invalid private key")
    try:
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except (TypeError, ValueError, OverflowError, UnsupportedAlgorithm) as exc:
        raise VaultError("private key is not a supported unencrypted PEM key") from exc
    if not isinstance(key, (rsa.RSAPrivateKey, ed25519.Ed25519PrivateKey)):
        raise VaultError("only RSA or Ed25519 API keys are supported")


class CredentialVault:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else default_vault_path()

    def exists(self) -> bool:
        return self.path.is_file()

    def save(self, api_key_id: str, pem: str) -> None:
        validate_key(api_key_id, pem)
        payload = json.dumps(
            {"api_key_id": api_key_id, "private_key_pem": pem},
            separators=(",", ":"),
        ).encode("utf-8")
        protected = _dpapi(payload, decrypt=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replacement; never write plaintext to disk.
        temp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        try:
            with temp.open("xb") as stream:
                stream.write(protected)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)

    def load(self) -> tuple[str, str]:
        if not self.exists():
            raise VaultError("no saved credentials")
        try:
            raw = _dpapi(self.path.read_bytes(), decrypt=True)
            data = json.loads(raw)
            validate_key(data["api_key_id"], data["private_key_pem"])
            return data["api_key_id"], data["private_key_pem"]
        except (ValueError, KeyError, UnicodeError, TypeError) as exc:
            raise VaultError("saved credentials are invalid") from exc

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)
