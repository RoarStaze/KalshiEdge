from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from kalshi_edge.gold.audit import (
    EXPECTED_PYTH_SYMBOL, FINAL_CHECKPOINT_SECONDS, QUALIFICATION_GATE,
    GoldContractError, collect_contract_audit, validate_gold_series,
)

SERIES = {
    "series": {
        "ticker": "KXGOLD15M", "contract_terms_url": "https://assets.kalshi.com/contract_terms/COMMODITIES.pdf",
        "settlement_sources": [{"name": "Pyth - Gold", "url": "https://app.pyth.com/explore/Metal.Index.1OZGOLD%2FUSD?returnUrl=%2Fexplore"}],
    }
}
MARKET = {
    "ticker": "KXGOLD15M-26NOV161400-00", "rules_primary": "Resolves Yes based on a Pyth Gold minute close.",
    "rules_secondary": "Review required", "close_time": "2026-11-16T20:00:00Z", "expiration_time": "2026-11-16T20:30:00Z",
    "floor_strike": 3900.0, "strike_type": "greater", "status": "active",
}


def test_policy_is_480_seconds_and_69_percent() -> None:
    assert FINAL_CHECKPOINT_SECONDS == 480
    assert str(QUALIFICATION_GATE) == "0.69"


def test_exact_pyth_symbol_is_required_and_alternates_are_rejected() -> None:
    assert EXPECTED_PYTH_SYMBOL == "Metal.Index.1OZGOLD/USD"
    assert validate_gold_series(SERIES)["feed_identity_verified"] is True
    for wrong in ("Metal.XAU/USD", "Metal.Index.GOLD/USD"):
        bad = json.loads(json.dumps(SERIES))
        bad["series"]["settlement_sources"][0]["url"] = f"https://app.pyth.com/explore/{wrong}"
        with pytest.raises(GoldContractError, match="settlement source"):
            validate_gold_series(bad)


def test_series_identity_mismatch_is_rejected() -> None:
    bad = json.loads(json.dumps(SERIES))
    bad["series"]["ticker"] = "KXBTC15M"
    with pytest.raises(GoldContractError, match="series ticker"):
        validate_gold_series(bad)


def test_audit_persists_source_responses_with_hash_and_rule_review_flag(tmp_path: Path) -> None:
    paths = []
    requests_seen = []
    def handler(req: httpx.Request) -> httpx.Response:
        requests_seen.append(req)
        paths.append((req.url.path, dict(req.url.params)))
        if req.url.path.endswith("/series/KXGOLD15M"):
            return httpx.Response(200, json=SERIES)
        if req.url.path.endswith("/historical/cutoff"):
            return httpx.Response(200, json={"market_settled_ts": "2026-11-01T00:00:00Z"})
        if req.url.path.endswith("/markets"):
            return httpx.Response(200, json={"markets": [MARKET], "cursor": "next-page"})
        if req.url.path.endswith("/markets/" + MARKET["ticker"]):
            return httpx.Response(200, json={"market": MARKET})
        return httpx.Response(404)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = collect_contract_audit(tmp_path, client=client, sample_markets=2)
    assert report["checkpoint_seconds"] == 480
    assert report["qualification_gate"] == "0.69"
    assert report["sampled_markets"] == 1
    assert report["market_page_truncated"] is True
    assert report["settlement_rule_status"] == "REQUIRES_MANUAL_VERIFICATION"
    assert report["ready_for_training"] is False
    assert report["ready_for_live_trading"] is False
    assert requests_seen and all(req.method == "GET" for req in requests_seen)
    assert any(url.endswith("/historical/cutoff") for url, _ in paths)
    audit_file = Path(report["artifact_path"])
    saved = audit_file.read_bytes()
    assert hashlib.sha256(saved).hexdigest() == report["sha256"]
    stored = json.loads(saved)
    assert stored["raw_responses"]["market_details"][0]["market"]["rules_primary"] == MARKET["rules_primary"]
    assert stored["raw_responses"]["series"] == SERIES
    assert stored["raw_responses"]["cutoff"]["market_settled_ts"].startswith("2026")
    assert audit_file.parent == tmp_path / "contract_audits"


def test_missing_market_rules_never_become_approved_labels(tmp_path: Path) -> None:
    no_rules = {"ticker": "KXGOLD15M-TEST", "close_time": "2026-11-16T20:00:00Z"}
    def handler(req: httpx.Request) -> httpx.Response:
        if "/series/" in req.url.path:
            return httpx.Response(200, json=SERIES)
        if req.url.path.endswith("/markets"):
            return httpx.Response(200, json={"markets": [no_rules]})
        if req.url.path.endswith("/historical/cutoff"):
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"market": no_rules})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = collect_contract_audit(tmp_path, client=client)
    assert result["ready_for_training"] is False
    assert result["settlement_rule_status"] == "REQUIRES_MANUAL_VERIFICATION"
    assert "missing_rules" in result["quality_flags"]


def test_audit_rejects_non_gold_market_from_api(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if "/series/" in req.url.path:
            return httpx.Response(200, json=SERIES)
        if req.url.path.endswith("/markets"):
            return httpx.Response(200, json={"markets": [{"ticker": "KXBTC15M-WRONG"}]})
        return httpx.Response(200, json={})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GoldContractError, match="unexpected market ticker"):
            collect_contract_audit(tmp_path, client=client)


def test_rejects_nested_phase1_raw_output_root_before_requests(tmp_path: Path) -> None:
    def never_call(req: httpx.Request) -> httpx.Response:
        raise AssertionError("audit must never use network when output is inside canonical data/raw")

    with httpx.Client(transport=httpx.MockTransport(never_call)) as client:
        with pytest.raises(ValueError, match="canonical Phase 1 data/raw"):
            collect_contract_audit(tmp_path / "data" / "raw" / "gold", client=client)
