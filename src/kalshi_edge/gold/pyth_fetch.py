"""Bounded, authenticated Pyth Pro historical OHLC retrieval, not oracle proof.

Authorization is only an HTTP header, never written into artifacts, logs or
query parameters. Successful HTTPS GET is not a signed price attestation and
does not verify the exact Kalshi settlement-candle channel/alignment.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any

import httpx

from .oracle_history import (
    PYTH_BASE, MAX_INPUT_BYTES, pyth_history_request, import_pyth_history,
    GoldOracleEvidenceError,
)
from .prospective import _checked_gold_root


class GoldPythFetchError(ValueError):
    """Authenticated historical price fetch failed safely."""


def fetch_pyth_history(
    output_root: Path, *, start_ts: int, end_ts: int,
    api_key: str, channel: str = "real_time",
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Download at most one UTC day of one-minute Pyth gold OHLC."""
    if (not isinstance(api_key, str) or not api_key.strip()
            or len(api_key) > 4096 or "\n" in api_key or "\r" in api_key):
        raise GoldPythFetchError("nonempty Pyth bearer key required")
    if (type(start_ts) is not int or type(end_ts) is not int
            or start_ts % 60 or end_ts % 60
            or end_ts <= start_ts or end_ts-start_ts >= 86400):
        raise GoldPythFetchError("invalid bounded minute-aligned Pyth request")
    try:
        req = pyth_history_request(start_ts, end_ts, channel=channel)
    except GoldOracleEvidenceError as exc:
        raise GoldPythFetchError("unsupported Pyth history request") from exc
    root = _checked_gold_root(Path(output_root))
    owns = client is None
    session = client if client is not None else httpx.Client(timeout=30, trust_env=False)
    try:
        response = session.get(
            PYTH_BASE + req["path"],
            params=req["params"], headers={
                "Authorization": "Bearer " + api_key.strip(),
                "Accept": "application/json",
            },
        )
        if response.status_code != 200:
            # Do NOT include body, request/response object or Authorization
            # header in the exception; they can expose secrets.
            raise GoldPythFetchError(f"Pyth historical API HTTP {response.status_code}")
        if not 1 <= len(response.content) <= MAX_INPUT_BYTES:
            raise GoldPythFetchError("historical response is empty or exceeds size bound")
        try:
            json.loads(response.content)
        except (ValueError, UnicodeDecodeError) as exc:
            raise GoldPythFetchError("Pyth response is not valid JSON") from exc
        with tempfile.TemporaryDirectory(prefix="gold-pyth-") as temporary:
            path = Path(temporary) / "history.json"
            path.write_bytes(response.content)
            try:
                result = import_pyth_history(
                    path, root, channel=channel,
                    start_ts=start_ts, end_ts=end_ts,
                )
            except GoldOracleEvidenceError as exc:
                raise GoldPythFetchError("Pyth response failed OHLC validation") from exc
        return {**result, "retrieval": "DIRECT_AUTHENTICATED_HTTPS_NOT_SIGNED_ORACLE"}
    except httpx.HTTPError as exc:
        raise GoldPythFetchError("Pyth history network error") from exc
    finally:
        if owns:
            session.close()


def fetch_pyth_backfill(
    output_root: Path, *, start_ts: int, end_ts: int, api_key: str,
    channel: str = "real_time", client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Bounded batch (up to 30 days), preserving each successful chunk.

    Re-run explicit ranges after a network failure; the immutable source
    files already completed in prior chunks are not erased.
    """
    if (type(start_ts) is not int or type(end_ts) is not int
            or start_ts % 60 or end_ts % 60
            or end_ts <= start_ts or end_ts-start_ts > 30*86400):
        raise GoldPythFetchError("Pyth backfill must be a bounded, aligned interval <=30 days")
    owns = client is None
    session = client if client is not None else httpx.Client(timeout=30, trust_env=False)
    collected: list[str] = []
    try:
        cursor = start_ts
        while cursor <= end_ts:
            upper = min(cursor + 86340, end_ts)
            lower = cursor if upper > cursor else cursor - 60
            report = fetch_pyth_history(
                output_root, start_ts=lower, end_ts=upper,
                api_key=api_key, channel=channel, client=session,
            )
            collected.append(report["artifact_path"])
            cursor = upper + 60
    finally:
        if owns:
            session.close()
    return {
        "series_ticker": "KXGOLD15M",
        "downloaded_requests": len(collected),
        "artifact_paths": collected,
        "settlement_verified": False,
        "training_ready": False,
        "note": "HTTPS data origin does not independently authenticate settlement candle/time alignment.",
    }
