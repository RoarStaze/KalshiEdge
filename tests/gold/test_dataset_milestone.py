"""KXGOLD15M prospective market receipts, evidence gates and XGBoost preparation.

Fixture data are SYNTHETIC. Assertions never represent live model accuracy.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import httpx
import pytest

from kalshi_edge.gold.prospective import (
    GoldCaptureError, capture_gold_quotes, inspect_gold_quote_snapshot,
)
from kalshi_edge.gold.dataset_readiness import (
    GoldDatasetError, audit_gold_dataset, inspect_gold_dataset_audit,
)
from kalshi_edge.gold.xgboost_baseline import (
    GoldModelRow, GoldBaselineError, chronological_gold_split, fit_research_baseline,
    train_from_gold_audit,
)

SERIES = {"series": {"ticker": "KXGOLD15M", "settlement_sources": [
    {"url": "https://app.pyth.com/explore/Metal.Index.1OZGOLD%2FUSD"}
]}}


def market(suffix: str, *, close: str = "2099-11-17T15:15:00Z",
           result: str = "", status: str = "active", bid: str = "0.4300",
           ask: str = "0.4700"):
    return {
        "ticker": "KXGOLD15M-" + suffix, "close_time": close,
        "result": result, "status": status,
        "yes_bid_dollars": bid, "yes_ask_dollars": ask,
        "last_price_dollars": "0.4500", "volume_fp": "12.00",
        "rules_primary": "The gold index is at least its opening reading.",
    }


def quote_transport(*, market_record=None, page_cursor=""):
    seen = []
    def respond(req: httpx.Request):
        seen.append(req)
        if req.url.path.endswith("/series/KXGOLD15M"):
            return httpx.Response(200, json=SERIES)
        if req.url.path.endswith("/markets"):
            return httpx.Response(200, json={
                "markets": [market_record or market("A")], "cursor": page_cursor,
            })
        raise AssertionError("unexpected request " + str(req.url))
    return httpx.MockTransport(respond), seen


def hashed(path: Path, value: dict) -> Path:
    blob = (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")
    p = path / f"123-{sha256(blob).hexdigest()[:16]}.json"
    p.write_bytes(blob)
    return p


def archived_inventory(tmp_path: Path) -> Path:
    m = market("A", close="2026-11-11T00:15:00Z", status="finalized", result="yes")
    return hashed(tmp_path, {
        "schema_version": 1, "series": SERIES,
        "report": {"series_ticker": "KXGOLD15M", "qualification_gate": "0.69",
                   "checkpoint_seconds": 480, "ready_for_training": False},
        "pages": {"historical": [{"params": {"series_ticker": "KXGOLD15M"},
                "response": {"markets": [m], "cursor": ""}}],
                  "live": []},
    })


def test_prospective_capture_uses_public_get_with_receipt_and_immutable_hash(tmp_path):
    transport, seen = quote_transport()
    with httpx.Client(transport=transport) as client:
        report = capture_gold_quotes(tmp_path, client=client)
    assert all(req.method == "GET" for req in seen)
    assert all(req.url.params.get("series_ticker") == "KXGOLD15M"
               for req in seen if req.url.path.endswith("/markets"))
    assert report["series_ticker"] == "KXGOLD15M"
    assert report["settlement_oracle_verified"] is False
    assert report["ready_for_training"] is False
    assert report["market_records"] == 1
    path = Path(report["artifact_path"])
    assert sha256(path.read_bytes()).hexdigest() == report["sha256"]
    snapshot = inspect_gold_quote_snapshot(path)
    assert snapshot["series"]["series"]["ticker"] == "KXGOLD15M"
    receipt = snapshot["pages"][0]
    assert receipt["request_started_ns"] <= receipt["response_received_ns"]
    assert receipt["response"]["markets"][0]["yes_bid_dollars"] == "0.4300"
    assert receipt["response"]["markets"][0]["yes_ask_dollars"] == "0.4700"
    assert receipt["response_received_ns"] > 0
    assert snapshot["report"]["point_in_time_publication_verified"] is False


@pytest.mark.parametrize("bad", [
    {"ticker": "KXBTC15M-FAKE"},
    {"yes_bid_dollars": "NaN"},
    {"yes_bid_dollars": "1.2"},
    {"yes_bid_dollars": "0.90", "yes_ask_dollars": "0.10"},
    {"close_time": "not-an-iso-datetime"},
    {"result": "yes", "status": "finalized"},
])
def test_capture_rejects_malformed_market_or_nonlive_input(tmp_path, bad):
    item = {**market("A"), **bad}
    transport, _ = quote_transport(market_record=item)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(GoldCaptureError):
            capture_gold_quotes(tmp_path, client=client)
    assert not (tmp_path / "prospective_quotes").exists()


def test_prospective_capture_does_not_claim_completed_pagination(tmp_path):
    transport, _ = quote_transport(page_cursor="more")
    with httpx.Client(transport=transport) as client:
        report = capture_gold_quotes(tmp_path, client=client, max_pages=1)
    assert report["pagination_capped"] is True
    assert report["ready_for_training"] is False


def test_damaged_or_forged_quote_snapshot_rejected(tmp_path):
    transport, _ = quote_transport()
    with httpx.Client(transport=transport) as client:
        p = Path(capture_gold_quotes(tmp_path, client=client)["artifact_path"])
    p.write_bytes(p.read_bytes().replace(b"0.4300", b"0.9900"))
    with pytest.raises(GoldCaptureError, match="SHA-256"):
        inspect_gold_quote_snapshot(p)


def test_dataset_audit_preserves_input_hash_and_has_zero_training_rows(tmp_path):
    inventory = archived_inventory(tmp_path)
    transport, _ = quote_transport()
    with httpx.Client(transport=transport) as client:
        quote_path = Path(capture_gold_quotes(tmp_path, client=client)["artifact_path"])
    report = audit_gold_dataset(inventory, tmp_path, quote_paths=[quote_path])
    assert report["dataset_ready"] is False
    assert report["eligible_training_rows"] == 0
    assert report["label_status"] == "ORACLE_UNVERIFIED"
    assert report["candidate_market_count"] >= 1
    assert "oracle_settlement_not_reconstructed" in report["blockers"]
    assert "contract_checkpoint_semantics_unverified" in report["blockers"]
    assert report["inventory_sha256"] == sha256(inventory.read_bytes()).hexdigest()
    saved = Path(report["artifact_path"])
    assert sha256(saved.read_bytes()).hexdigest() == report["sha256"]
    doc = inspect_gold_dataset_audit(saved)
    assert doc["report"]["dataset_ready"] is False
    assert doc["training_rows"] == []
    assert doc["source_hashes"]["inventory"] == report["inventory_sha256"]
    with pytest.raises(GoldBaselineError, match="not training-ready"):
        train_from_gold_audit(saved, tmp_path)
    assert not (tmp_path / "gold_models").exists()


def test_audit_rejects_invalid_provenance_and_forged_readiness(tmp_path):
    inventory = archived_inventory(tmp_path)
    transport, _ = quote_transport()
    with httpx.Client(transport=transport) as client:
        p = Path(capture_gold_quotes(tmp_path, client=client)["artifact_path"])
    document = json.loads(p.read_text())
    document["report"]["ready_for_training"] = True
    forged = hashed(tmp_path, document)
    with pytest.raises(GoldDatasetError, match="readiness"):
        audit_gold_dataset(inventory, tmp_path, quote_paths=[forged])
    assert not (tmp_path / "dataset_audits").exists()


def test_audit_rejects_tampered_inventory_and_old_probe_mismatch(tmp_path):
    inventory = archived_inventory(tmp_path)
    inventory.write_bytes(inventory.read_bytes().replace(b"KXGOLD15M-A", b"KXGOLD15M-B"))
    with pytest.raises(GoldDatasetError, match="SHA-256"):
        audit_gold_dataset(inventory, tmp_path)
    assert not (tmp_path / "dataset_audits").exists()


def test_research_split_purges_label_latency_and_separates_sessions():
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rows = []
    for day in range(12):
        t = int((base + timedelta(days=day)).timestamp() * 1_000_000_000)
        rows.append(GoldModelRow(
            ticker=f"KXGOLD15M-TEST{day}", decision_ns=t,
            feature_receipt_ns=t, label_known_ns=t + 16 * 60 * 1_000_000_000,
            label=day % 2,
            features=(0.43 + day * .001, 0.47 + day * .001, float(day + 1)),
        ))
    train, test = chronological_gold_split(rows, min_train=5, min_test=3)
    assert len(train) >= 5
    assert len(test) >= 3
    assert max(r.label_known_ns for r in train) < min(r.decision_ns for r in test)
    assert set(r.ticker for r in train).isdisjoint(r.ticker for r in test)
    assert max(r.decision_ns for r in train) < min(r.decision_ns for r in test)


@pytest.mark.parametrize("mutate", [
    {"feature_receipt_ns": 10_000}, {"label": 3},
    {"features": (float("nan"), 0.5, 2.0)},
])
def test_research_baseline_rejects_leakage_and_invalid_data(mutate):
    data = [
        GoldModelRow("KXGOLD15M-T", 2_000, 1_000, 2_500, 1, (0.4, 0.5, 2.0)),
    ]
    fields = {**data[0].__dict__, **mutate}
    if "feature_receipt_ns" in mutate:
        fields["feature_receipt_ns"] = fields["decision_ns"] + 1
    with pytest.raises(GoldBaselineError):
        chronological_gold_split([GoldModelRow(**fields)], min_train=1, min_test=1)


def test_baseline_evaluation_on_synthetic_rows_is_research_only():
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rows = []
    for day in range(24):
        t = int((base + timedelta(days=day)).timestamp() * 1_000_000_000)
        rows.append(GoldModelRow(
            ticker=f"KXGOLD15M-DEMO{day}", decision_ns=t,
            feature_receipt_ns=t, label_known_ns=t + 20 * 60 * 1_000_000_000,
            label=day % 2,
            features=(0.2 + day * .01, 0.3 + day * .01, float(day)),
        ))
    result = fit_research_baseline(rows, min_train=10, min_test=5)
    assert result["research_only"] is True
    assert result["deployable"] is False
    assert result["source"] == "SUPPLIED_ROWS_NOT_VERIFIED_ORACLE"
    assert 0 <= result["accuracy"] <= 1
    assert 0 <= result["brier"] <= 1
    assert result["test_count"] >= 5
    assert result["train_count"] >= 10
