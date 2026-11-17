from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from kalshi_edge.gold.rule_inspection import RuleInspectionError, inspect_contract_snapshot


def _snapshot(tmp_path: Path, markets: list[dict] | None = None) -> Path:
    markets = markets if markets is not None else [
        {
            "ticker": "KXGOLD15M-26NOV050300-00",
            "rules_primary": "If 1-minute close for Gold on Nov 5, 2026 at 3:00 AM EST is at least 1-minute close at 2:45 AM EDT, resolves Yes.",
            "rules_secondary": "Settlement value rounded to nearest 2 decimal places; no data use most recently available published data.",
            "close_time": "2026-11-05T08:00:00Z",
            "expiration_time": "2026-11-05T08:30:00Z",
            "status": "finalized",
            "result": "yes",
        },
        {
            "ticker": "KXGOLD15M-26NOV050315-15",
            "rules_primary": "If 1-minute close for Gold at 3:15 AM EST is at least 1-minute close at 3:00 AM EST, resolves Yes.",
            "rules_secondary": "Settlement value rounded to nearest 2 decimal places; no data use most recently available published data.",
            "close_time": "2026-11-05T08:15:00Z",
            "expiration_time": "2026-11-05T08:45:00Z",
            "status": "settled",
            "result": "no",
        },
    ]
    snap = {
        "schema_version": 1,
        "raw_responses": {
            "series": {"series": {"ticker": "KXGOLD15M", "settlement_sources": [
                {"name": "Pyth - Gold", "url": "https://app.pyth.com/explore/Metal.Index.1OZGOLD%2FUSD"}
            ]}},
            "markets_page": {"markets": markets, "cursor": "next-page"},
            "market_details": [{"market": m} for m in markets],
            "cutoff": {"market_settled_ts": "2026-11-01T00:00:00Z"},
        },
        "audit_report": {"checkpoint_seconds": 480, "qualification_gate": "0.69"},
    }
    raw = (json.dumps(snap, sort_keys=True) + "\n").encode()
    digest = hashlib.sha256(raw).hexdigest()
    path = tmp_path / f"123456789-{digest[:16]}.json"
    path.write_bytes(raw)
    return path


def test_reports_rule_text_and_conflicting_est_edt_without_approving_settlement(tmp_path: Path) -> None:
    report = inspect_contract_snapshot(_snapshot(tmp_path))
    assert report["series_ticker"] == "KXGOLD15M"
    assert report["checkpoint_seconds"] == 480
    assert report["qualification_gate"] == "0.69"
    assert report["unique_markets"] == 2
    assert report["rule_variants"] == 2
    assert report["full_market_coverage"] is False
    assert report["settlement_verified"] is False
    assert report["training_ready"] is False
    assert report["markets"][0]["ticker"] == "KXGOLD15M-26NOV050300-00"
    assert report["markets"][0]["timezone_abbreviations"] == ["EDT", "EST"]
    assert "CONFLICTING_TZ_ABBREVIATIONS" in report["markets"][0]["flags"]
    assert report["markets"][0]["close_time_utc"] == "2026-11-05T08:00:00+00:00"
    assert report["markets"][0]["checkpoint_from_api_close_time_utc"] == "2026-11-05T07:52:00+00:00"
    assert "timezone_mapping_not_verified" in report["unresolved"]
    assert "market_results_not_reconstructed" in report["unresolved"]
    assert report["markets"][0]["rules_primary"].startswith("If 1-minute close")


def test_rejects_tampered_snapshot(tmp_path: Path) -> None:
    path = _snapshot(tmp_path)
    path.write_text(path.read_text().replace("finalized", "active"), encoding="utf-8")
    with pytest.raises(RuleInspectionError, match="SHA-256"):
        inspect_contract_snapshot(path)


def test_rejects_duplicate_market_ticker(tmp_path: Path) -> None:
    one = {"ticker": "KXGOLD15M-DUPLICATE", "rules_primary": "at least",
           "close_time": "2026-11-05T08:00:00Z"}
    with pytest.raises(RuleInspectionError, match="duplicate"):
        inspect_contract_snapshot(_snapshot(tmp_path, [one, one]))


def test_rejects_wrong_series_even_with_valid_hash(tmp_path: Path) -> None:
    data = json.loads(_snapshot(tmp_path).read_text())
    data["raw_responses"]["series"]["series"]["ticker"] = "KXBTC15M"
    raw = (json.dumps(data, sort_keys=True) + "\n").encode()
    filename = tmp_path / ("654321-" + hashlib.sha256(raw).hexdigest()[:16] + ".json")
    filename.write_bytes(raw)
    with pytest.raises(RuleInspectionError, match="series"):
        inspect_contract_snapshot(filename)


def test_missing_rules_and_close_time_fail_closed(tmp_path: Path) -> None:
    report = inspect_contract_snapshot(
        _snapshot(tmp_path, [{"ticker": "KXGOLD15M-NORULES"}])
    )
    assert report["training_ready"] is False
    assert report["markets"][0]["flags"] == ["MISSING_RULES", "MISSING_OR_INVALID_CLOSE_TIME"]
    assert report["markets"][0]["checkpoint_from_api_close_time_utc"] is None


def test_expiration_before_close_is_an_anomaly(tmp_path: Path) -> None:
    report = inspect_contract_snapshot(_snapshot(tmp_path, [{
        "ticker": "KXGOLD15M-WRONGTIMES", "rules_primary": "at least",
        "close_time": "2026-11-05T09:00:00Z",
        "expiration_time": "2026-11-05T08:30:00Z",
    }]))
    assert "EXPIRATION_BEFORE_TRADING_CLOSE" in report["markets"][0]["flags"]


def test_cli_inspects_existing_snapshot_without_network(tmp_path: Path, capsys) -> None:
    from kalshi_edge.gold.audit import main
    path = _snapshot(tmp_path)
    assert main(["--inspect-artifact", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["snapshot_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report["training_ready"] is False
