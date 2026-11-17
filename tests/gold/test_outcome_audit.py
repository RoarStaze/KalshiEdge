from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from kalshi_edge.gold.outcome_audit import summarize_market_outcomes, inspect_inventory_snapshot


def _market(ticker: str, status: str, result: str = "") -> dict:
    return {"ticker": f"KXGOLD15M-{ticker}", "status": status, "result": result,
            "close_time": "2026-11-10T05:00:00Z",
            "rules_primary": "If closing gold is at least the opening reference, resolves Yes"}


def _records():
    return {
        "KXGOLD15M-A": [_market("A", "finalized", "yes"), _market("A", "finalized", "yes")],
        "KXGOLD15M-B": [_market("B", "determined", "no"), _market("B", "finalized", "no")],
        "KXGOLD15M-C": [_market("C", "initialized", "")],
        "KXGOLD15M-D": [_market("D", "finalized", "yes")],
        "KXGOLD15M-E": [_market("E", "active", "yes")],
        "KXGOLD15M-F": [_market("F", "finalized", "yes"), _market("F", "finalized", "no")],
        "KXGOLD15M-G": [_market("G", "closed", "no")],
    }


def _snapshot(tmp_path: Path) -> Path:
    records = _records()
    snap = {
        "schema_version": 1,
        "series": {"series": {"ticker": "KXGOLD15M", "settlement_sources": [
            {"url": "https://app.pyth.com/explore/Metal.Index.1OZGOLD%2FUSD"}]}},
        "cutoff": {"market_settled_ts": "2026-10-01T00:00:00Z"},
        "pages": {
            "historical": [{"received_at_ns": 123, "params": {"series_ticker": "KXGOLD15M", "limit": 1000},
                            "response": {"markets": [records[k][0] for k in (
                                "KXGOLD15M-A", "KXGOLD15M-B", "KXGOLD15M-F")]}}],
            "live": [{"received_at_ns": 124, "params": {"series_ticker": "KXGOLD15M", "limit": 1000},
                      "response": {"markets": [records[k][-1] for k in records]}}],
        },
        "report": {"series_ticker": "KXGOLD15M", "qualification_gate": "0.69",
                   "checkpoint_seconds": 480, "ready_for_training": False},
    }
    raw = (json.dumps(snap, sort_keys=True) + "\n").encode()
    path = tmp_path / f"1234567890-{sha256(raw).hexdigest()[:16]}.json"
    path.write_bytes(raw)
    return path


def test_counts_all_reported_results_but_separates_finalized_and_provisional():
    r = summarize_market_outcomes(_records())
    assert r["observed_official_yes_results_unverified_against_pyth"] == 3
    assert r["observed_official_no_results_unverified_against_pyth"] == 2
    assert r["finalized_yes_results_unverified_against_pyth"] == 2
    assert r["finalized_no_results_unverified_against_pyth"] == 1
    assert r["contradictory_official_result_tickers"] == ["KXGOLD15M-F"]
    assert r["status_counts_by_record"] == {"active": 1, "closed": 1, "determined": 1,
                                           "finalized": 6, "initialized": 1}
    assert r["binary_result_counts_by_status"]["finalized"] == {"yes": 4, "no": 2}
    assert r["binary_result_counts_by_status"]["determined"] == {"yes": 0, "no": 1}
    assert r["nonfinalized_binary_result_tickers"] == [
        "KXGOLD15M-B", "KXGOLD15M-E", "KXGOLD15M-G"]


def test_missing_and_invalid_results_are_never_silently_counted():
    r = summarize_market_outcomes({
        "KXGOLD15M-A": [_market("A", "finalized", "")],
        "KXGOLD15M-B": [_market("B", "finalized", "undetermined")],
        "KXGOLD15M-C": [_market("C", "finalized", "YES")],
    })
    assert r["observed_official_yes_results_unverified_against_pyth"] == 1
    assert r["observed_official_no_results_unverified_against_pyth"] == 0
    assert r["unexpected_nonempty_result_values"] == {"undetermined": 1}
    assert r["finalized_without_binary_result_tickers"] == [
        "KXGOLD15M-A", "KXGOLD15M-B"]


def test_offline_inspection_verifies_hash_and_matches_outcome_summary(tmp_path: Path):
    path = _snapshot(tmp_path)
    r = inspect_inventory_snapshot(path)
    assert r["snapshot_sha256"] == sha256(path.read_bytes()).hexdigest()
    assert r["unique_markets"] == 7
    assert r["total_records"] == 10
    assert r["page_counts"] == {"historical": 1, "live": 1}
    assert r["observed_official_yes_results_unverified_against_pyth"] == 3
    assert r["finalized_yes_results_unverified_against_pyth"] == 2
    assert r["settlement_oracle_verified"] is False
    assert r["ready_for_training"] is False
    assert r["ready_for_live_trading"] is False


def test_hash_tampering_is_rejected(tmp_path: Path):
    path = _snapshot(tmp_path)
    path.write_bytes(path.read_bytes().replace(b"finalized", b"active"))
    with pytest.raises(ValueError, match="SHA-256"):
        inspect_inventory_snapshot(path)


def test_wrong_ticker_or_source_rejected_even_if_hash_valid(tmp_path: Path):
    path = _snapshot(tmp_path)
    data = json.loads(path.read_bytes())
    data["pages"]["live"][0]["response"]["markets"][0]["ticker"] = "KXBTC15M-WRONG"
    raw = (json.dumps(data, sort_keys=True) + "\n").encode()
    changed = tmp_path / ("12345-" + sha256(raw).hexdigest()[:16] + ".json")
    changed.write_bytes(raw)
    with pytest.raises(ValueError, match="ticker"):
        inspect_inventory_snapshot(changed)
    data["pages"]["live"][0]["response"]["markets"][0]["ticker"] = "KXGOLD15M-A"
    data["series"]["series"]["settlement_sources"][0]["url"] = (
        "https://app.pyth.com/explore/Metal.XAU%2FUSD")
    raw = (json.dumps(data, sort_keys=True) + "\n").encode()
    changed = tmp_path / ("54321-" + sha256(raw).hexdigest()[:16] + ".json")
    changed.write_bytes(raw)
    with pytest.raises(ValueError, match="identity"):
        inspect_inventory_snapshot(changed)


def test_wrong_policy_and_malformed_pages_rejected(tmp_path: Path):
    path = _snapshot(tmp_path)
    for field, value in [("qualification_gate", "0.63"), ("checkpoint_seconds", 240)]:
        data = json.loads(path.read_bytes())
        data["report"][field] = value
        raw = (json.dumps(data, sort_keys=True) + "\n").encode()
        changed = tmp_path / ("12345-" + sha256(raw).hexdigest()[:16] + ".json")
        changed.write_bytes(raw)
        with pytest.raises(ValueError, match="policy"):
            inspect_inventory_snapshot(changed)


def test_snapshot_missing_cutoff_rejected(tmp_path: Path):
    path = _snapshot(tmp_path)
    data = json.loads(path.read_bytes())
    data.pop("cutoff")
    raw = (json.dumps(data, sort_keys=True) + "\n").encode()
    changed = tmp_path / ("12345-" + sha256(raw).hexdigest()[:16] + ".json")
    changed.write_bytes(raw)
    with pytest.raises(ValueError, match="cutoff"):
        inspect_inventory_snapshot(changed)


def test_invalid_ticker_in_result_summary_is_rejected():
    with pytest.raises(ValueError, match="ticker"):
        summarize_market_outcomes({"KXBTC15M-FAKE": [_market("A", "finalized", "yes")]})


def test_tricky_result_values_do_not_count_as_finalized():
    r = summarize_market_outcomes({
        "KXGOLD15M-A": [_market("A", "determined", "yes")],
        "KXGOLD15M-B": [_market("B", "closed", "no")],
    })
    assert r["observed_official_yes_results_unverified_against_pyth"] == 1
    assert r["finalized_yes_results_unverified_against_pyth"] == 0
    assert r["finalized_no_results_unverified_against_pyth"] == 0


def test_cli_offline_inventory_inspection(tmp_path: Path, capsys):
    from kalshi_edge.gold.audit import main
    path = _snapshot(tmp_path)
    assert main(["--inspect-inventory", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["unique_markets"] == 7
    assert result["ready_for_training"] is False
