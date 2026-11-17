"""GoldPredict V1.2 oracle evidence tests.

Every price/rule/market below is SYNTHETIC and not a verified Kalshi settlement.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import pytest

from kalshi_edge.gold.oracle_history import (
    GoldOracleEvidenceError, import_pyth_history, inspect_pyth_history_evidence,
    pyth_history_request,
)
from kalshi_edge.gold.settlement_evidence import (
    GoldSettlementEvidenceError, audit_settlement_evidence,
    inspect_settlement_evidence_audit,
)
from kalshi_edge.gold.dataset_readiness import audit_gold_dataset
from kalshi_edge.gold.xgboost_baseline import GoldBaselineError, train_from_gold_audit

SERIES = {"series": {"ticker": "KXGOLD15M", "settlement_sources": [
    {"url": "https://app.pyth.com/explore/Metal.Index.1OZGOLD%2FUSD"}
]}}


def hashed(root: Path, document: dict, *, filename_epoch: int = 123) -> Path:
    body = (json.dumps(document, sort_keys=True) + "\n").encode()
    path = root / f"{filename_epoch}-{sha256(body).hexdigest()[:16]}.json"
    path.write_bytes(body)
    return path


def synthetic_history(root: Path) -> tuple[Path, int, int]:
    start = 1794900000
    payload = {
        "s": "ok",
        "t": [start, start + 60, start + 120, start + 180],
        "o": [4100.1, 4100.3, 4100.5, 4100.7],
        "h": [4100.5, 4100.6, 4100.9, 4100.9],
        "l": [4099.9, 4100.1, 4100.4, 4100.5],
        "c": [4100.3, 4100.5, 4100.7, 4100.8],
        "v": [0, 0, 0, 0],
    }
    path = root / "input-pyth-history.json"
    path.write_text(json.dumps(payload))
    return path, start, start + 180


def inventory(root: Path, *, rule=None, result="yes", status="finalized", duplicate=False) -> Path:
    if rule is None:
        rule = (
            "If the close price of the 1-minute Pyth GOLD candlestick at "
            "8:45 AM EST is at least the close price of the 1-minute Pyth "
            "GOLD candlestick at 8:30 AM EDT, then resolves Yes."
        )
    item = {
        "ticker": "KXGOLD15M-SYNTHETIC",
        "rules_primary": rule,
        "rules_secondary": "",
        "close_time": "2026-11-17T15:45:00Z",
        "status": status, "result": result,
    }
    pages = {
        "historical": [{
            "params": {"series_ticker": "KXGOLD15M"},
            "response": {"markets": [item], "cursor": ""}
        }],
        "live": [],
    }
    if duplicate:
        pages["live"].append({
            "params": {"series_ticker": "KXGOLD15M"},
            "response": {"markets": [{**item, "result": "no"}], "cursor": ""}
        })
    return hashed(root, {
        "schema_version": 1, "series": SERIES, "pages": pages,
        "report": {"series_ticker": "KXGOLD15M", "checkpoint_seconds": 480,
                   "qualification_gate": "0.69", "ready_for_training": False},
    })


def test_pyth_request_plan_never_issues_unauthenticated_price_request():
    request = pyth_history_request(1794900000, 1794900180, channel="real_time")
    assert request["method"] == "GET"
    assert request["host"] == "pyth.dourolabs.app"
    assert request["path"] == "/v1/real_time/history"
    assert request["params"]["symbol"] == "Metal.Index.1OZGOLD/USD"
    assert request["params"]["resolution"] == "1"
    assert request["requires_api_key"] is True
    assert "Authorization" not in json.dumps(request)


def test_pyth_history_import_preserves_raw_sha_and_never_attests_provenance(tmp_path: Path):
    source, start, end = synthetic_history(tmp_path)
    report = import_pyth_history(source, tmp_path, channel="real_time",
                                 start_ts=start, end_ts=end)
    assert report["bar_count"] == 4
    assert report["source_sha256"] == sha256(source.read_bytes()).hexdigest()
    assert report["settlement_verified"] is False
    assert report["training_ready"] is False
    assert report["origin_authenticated"] is False
    assert "PYTH_HISTORY_CANDLE_ALIGNMENT_UNVERIFIED" in report["blockers"]
    artifact = Path(report["artifact_path"])
    assert sha256(artifact.read_bytes()).hexdigest() == report["sha256"]
    inspected = inspect_pyth_history_evidence(artifact)
    assert inspected["raw_pyth_response"]["t"][0] == start
    assert inspected["request"]["params"]["symbol"] == "Metal.Index.1OZGOLD/USD"
    assert inspected["request"]["params"]["resolution"] == "1"
    assert inspected["report"]["origin_authenticated"] is False


@pytest.mark.parametrize("change", [
    {"s": "error"},
    {"t": [1794900000, 1794900000, 1794900120, 1794900180]},
    {"t": [1794900001, 1794900060, 1794900120, 1794900180]},
    {"t": [1794900000, 1794900060, 1794900120, 1794900999]},
    {"c": [4100, "nan", 4100, 4100]},
    {"h": [4090, 4100.6, 4100.9, 4100.9]},
    {"l": [4100.5, 4100.1, 4100.4, 4100.5]},
    {"v": [0, 0, 0]},
    {"o": []},
])
def test_pyth_history_import_rejects_invalid_ohlc_and_time(tmp_path: Path, change):
    source, start, end = synthetic_history(tmp_path)
    payload = json.loads(source.read_text())
    payload.update(change)
    source.write_text(json.dumps(payload))
    with pytest.raises(GoldOracleEvidenceError):
        import_pyth_history(source, tmp_path, channel="real_time",
                            start_ts=start, end_ts=end)
    assert not (tmp_path / "pyth_history_imports").exists()


def test_pyth_history_import_tampering_or_forged_claims_fail_closed(tmp_path: Path):
    source, start, end = synthetic_history(tmp_path)
    report = import_pyth_history(source, tmp_path, channel="real_time",
                                 start_ts=start, end_ts=end)
    artifact = Path(report["artifact_path"])
    forged = json.loads(artifact.read_text())
    forged["report"]["origin_authenticated"] = True
    forged_path = hashed(tmp_path, forged)
    with pytest.raises(GoldOracleEvidenceError, match="unattested|provenance"):
        inspect_pyth_history_evidence(forged_path)
    artifact.write_bytes(artifact.read_bytes().replace(b"4100.8", b"4200.8"))
    with pytest.raises(GoldOracleEvidenceError, match="SHA-256"):
        inspect_pyth_history_evidence(artifact)


def test_settlement_evidence_flags_mixed_timezone_and_never_promotes_kalshi_results(tmp_path: Path):
    inv = inventory(tmp_path)
    source, start, end = synthetic_history(tmp_path)
    oracle = Path(import_pyth_history(source, tmp_path, channel="real_time",
                                      start_ts=start, end_ts=end)["artifact_path"])
    report = audit_settlement_evidence(inv, oracle, tmp_path)
    assert report["series_ticker"] == "KXGOLD15M"
    assert report["reported_finalized_results_not_reconstructed"] == 1
    assert report["settlement_verified_count"] == 0
    assert report["training_eligible_labels"] == 0
    assert report["outcome_comparisons_performed"] == 0
    assert "CONTRACT_TIMEZONE_CONFLICT" in report["blockers"]
    assert "PYTH_ORIGIN_UNATTESTED" in report["blockers"]
    artifact = Path(report["artifact_path"])
    assert sha256(artifact.read_bytes()).hexdigest() == report["sha256"]
    doc = inspect_settlement_evidence_audit(artifact)
    assert doc["market_assessments"][0]["status"] == "CONTRACT_TIMEZONE_CONFLICT"
    assert doc["verified_labels"] == []
    assert doc["source_hashes"]["inventory"] == sha256(inv.read_bytes()).hexdigest()


def test_utc_rules_still_need_timestamp_mapping_and_independent_oracle_attestation(tmp_path: Path):
    inv = inventory(tmp_path, rule=(
        "If the close price of the 1-minute Pyth GOLD candlestick at "
        "15:45 UTC is at least the close price at 15:30 UTC, resolves Yes."))
    source, start, end = synthetic_history(tmp_path)
    oracle = Path(import_pyth_history(source, tmp_path, channel="real_time",
                                      start_ts=start, end_ts=end)["artifact_path"])
    report = audit_settlement_evidence(inv, oracle, tmp_path)
    doc = inspect_settlement_evidence_audit(Path(report["artifact_path"]))
    assert doc["market_assessments"][0]["status"] == "RULE_AND_CANDLE_ALIGNMENT_UNVERIFIED"
    assert report["settlement_verified_count"] == 0


def test_conflicting_official_outcomes_rejected_not_counted_as_verified(tmp_path: Path):
    inv = inventory(tmp_path, duplicate=True)
    source, start, end = synthetic_history(tmp_path)
    oracle = Path(import_pyth_history(source, tmp_path, channel="real_time",
                                      start_ts=start, end_ts=end)["artifact_path"])
    report = audit_settlement_evidence(inv, oracle, tmp_path)
    doc = inspect_settlement_evidence_audit(Path(report["artifact_path"]))
    assert doc["market_assessments"][0]["status"] == "CONFLICTING_KALSHI_RESULTS"
    assert report["settlement_verified_count"] == 0


def test_dataset_readiness_ingests_oracle_evidence_but_stays_zero_rows(tmp_path: Path):
    inv = inventory(tmp_path)
    source, start, end = synthetic_history(tmp_path)
    oracle = Path(import_pyth_history(source, tmp_path, channel="real_time",
                                      start_ts=start, end_ts=end)["artifact_path"])
    from kalshi_edge.gold.dataset_readiness import inspect_gold_dataset_audit
    report = audit_gold_dataset(inv, tmp_path, oracle_history_path=oracle)
    assert report["dataset_ready"] is False
    assert report["eligible_training_rows"] == 0
    assert report["pyth_history_bars_unattested"] == 4
    assert "pyth_history_import_unattested" in report["blockers"]
    doc = inspect_gold_dataset_audit(Path(report["artifact_path"]))
    assert doc["source_hashes"]["pyth_history"] == sha256(oracle.read_bytes()).hexdigest()
    with pytest.raises(GoldBaselineError, match="not training-ready"):
        train_from_gold_audit(Path(report["artifact_path"]), tmp_path)


def test_cli_offline_pyth_import_and_settlement_audit_modes(tmp_path: Path, monkeypatch, capsys):
    from kalshi_edge.gold.audit import main
    calls = []
    monkeypatch.setattr(
        "kalshi_edge.gold.oracle_history.import_pyth_history",
        lambda source, root, **kw: calls.append(("import", source, root, kw)) or {"training_ready": False},
    )
    monkeypatch.setattr(
        "kalshi_edge.gold.settlement_evidence.audit_settlement_evidence",
        lambda inv, oracle, root: calls.append(("audit", inv, oracle, root)) or {"settlement_verified_count": 0},
    )
    assert main(["--import-pyth-history", "history.json", "--pyth-from-ts", "1794900000",
                 "--pyth-to-ts", "1794900180", "--output-root", str(tmp_path)]) == 0
    assert main(["--verify-settlement-evidence", "oracle.json", "--inventory-artifact",
                 "inventory.json", "--output-root", str(tmp_path)]) == 0
    assert calls[0][:3] == ("import", Path("history.json"), tmp_path)
    assert calls[0][3]["channel"] == "real_time"
    assert calls[1][:3] == ("audit", Path("inventory.json"), Path("oracle.json"))
    with pytest.raises(SystemExit):
        main(["--import-pyth-history", "history.json"])  # missing range
    with pytest.raises(SystemExit):
        main(["--verify-settlement-evidence", "oracle.json"])  # missing inventory
