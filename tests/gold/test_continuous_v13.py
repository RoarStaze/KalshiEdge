"""Synthetic regression coverage for read-only GoldPredict V1.3 collection."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import httpx
import pytest

from kalshi_edge.gold.continuous import (
    GoldContinuousError, capture_gold_microstructure,
    inspect_gold_microstructure_snapshot, inspect_gold_collector_status,
    run_continuous_gold_collection,
)
from kalshi_edge.gold.prospective import capture_gold_quotes

SERIES = {"series": {"ticker": "KXGOLD15M", "settlement_sources": [
    {"url": "https://app.pyth.com/explore/Metal.Index.1OZGOLD%2FUSD"}
]}}


def market():
    return {
        "ticker": "KXGOLD15M-TEST", "close_time": "2099-11-17T15:15:00Z",
        "result": "", "status": "active", "yes_bid_dollars": "0.4300",
        "yes_ask_dollars": "0.4700", "last_price_dollars": "0.4500",
        "volume_fp": "12.00",
    }


def transport(*, bad_book=False, rate_limit_first=False, second_market=False,
              rate_limit_attempts=(), retry_after="1"):
    requests = []
    state = {"series_attempts": 0}

    def respond(req: httpx.Request):
        requests.append(req)
        assert req.method == "GET"
        assert req.url.host == "external-api.kalshi.com"
        path = req.url.path
        if path.endswith("/series/KXGOLD15M"):
            state["series_attempts"] += 1
            if (rate_limit_first and state["series_attempts"] == 1
                    or state["series_attempts"] in rate_limit_attempts):
                return httpx.Response(429, headers={"Retry-After": retry_after})
            return httpx.Response(200, json=SERIES)
        if path.endswith("/markets"):
            assert req.url.params["series_ticker"] == "KXGOLD15M"
            records = [market()]
            if second_market:
                records.append({**market(), "ticker": "KXGOLD15M-SECOND"})
            return httpx.Response(200, json={"markets": records, "cursor": ""})
        if path.endswith("/orderbook"):
            return httpx.Response(200, json={"orderbook_fp": {
                "yes_dollars": [["0.4400", "-1.00" if bad_book else "2.00"]],
                "no_dollars": [["0.5200", "3.00"]],
            }})
        if path.endswith("/markets/trades"):
            return httpx.Response(200, json={"trades": [
                {"trade_id": "T1", "ticker": req.url.params["ticker"],
                 "count_fp": "2.00", "yes_price_dollars": "0.4600",
                 "created_time": "2026-11-17T10:00:00Z"},
            ], "cursor": "MORE"})
        raise AssertionError("unexpected endpoint " + str(req.url))
    return httpx.MockTransport(respond), requests


def test_microstructure_is_immutable_bounded_and_not_a_verified_label(tmp_path):
    adapter, requests = transport(second_market=True)
    with httpx.Client(transport=adapter) as client:
        quote = capture_gold_quotes(tmp_path, client=client)
        detail = capture_gold_microstructure(
            tmp_path, Path(quote["artifact_path"]), client=client, max_markets=1,
        )
    assert detail["captured_market_count"] == 1
    assert detail["markets_capped"] is True
    assert detail["trade_pages_capped"] == 1
    assert detail["settlement_oracle_verified"] is False
    assert detail["ready_for_training"] is False
    path = Path(detail["artifact_path"])
    doc = inspect_gold_microstructure_snapshot(path)
    assert doc["quote_sha256"] == quote["sha256"]
    assert doc["records"][0]["ticker"] == "KXGOLD15M-SECOND" or doc["records"][0]["ticker"] == "KXGOLD15M-TEST"
    assert doc["records"][0]["orderbook"]["response"]["orderbook_fp"]["yes_dollars"]
    assert doc["records"][0]["trades"]["response"]["trades"]
    assert doc["records"][0]["trades"]["pagination_capped"] is True
    assert sha256(path.read_bytes()).hexdigest() == detail["sha256"]
    assert len([r for r in requests if r.url.path.endswith("/orderbook")]) == 1
    assert len([r for r in requests if r.url.path.endswith("/markets/trades")]) == 1


def test_microstructure_rejects_wrong_book_and_does_not_write_false_evidence(tmp_path):
    adapter, _ = transport(bad_book=True)
    with httpx.Client(transport=adapter) as client:
        quote = capture_gold_quotes(tmp_path, client=client)
        with pytest.raises(GoldContinuousError, match="orderbook"):
            capture_gold_microstructure(tmp_path, Path(quote["artifact_path"]), client=client)
    assert not (tmp_path / "gold_microstructure").exists()


def test_bounded_collector_writes_independent_cycle_events_and_status(tmp_path):
    adapter, requests = transport()
    events = []
    with httpx.Client(transport=adapter) as client:
        report = run_continuous_gold_collection(
            tmp_path, client=client, max_cycles=2, interval_seconds=10,
            sleep=lambda _: None, on_event=events.append,
        )
    assert report["cycles"] == 2
    assert report["successful_cycles"] == 2
    assert report["failed_cycles"] == 0
    assert len(events) == 2
    assert events[0]["status"] == "CAPTURED"
    assert events[0]["event_sha256"] == events[1]["previous_event_sha256"]
    assert all(e["ready_for_training"] is False for e in events)
    assert all(r.method == "GET" for r in requests)
    summary = inspect_gold_collector_status(tmp_path)
    assert summary["cycles"] == 2
    assert summary["successful_cycles"] == 2
    assert summary["latest_quote_path"]
    assert summary["dataset_ready"] is False
    assert not (tmp_path / "gold_models").exists()


def test_collector_records_429_then_recovers_without_fabricating_data(tmp_path):
    adapter, _ = transport(rate_limit_first=True)
    events = []
    sleeps = []
    with httpx.Client(transport=adapter) as client:
        report = run_continuous_gold_collection(
            tmp_path, client=client, interval_seconds=10, max_cycles=2,
            sleep=sleeps.append, on_event=events.append,
        )
    assert report["successful_cycles"] == 1
    assert report["failed_cycles"] == 1
    assert events[0]["status"] == "FAILED"
    assert events[0]["quote_artifact"] is None
    assert events[0]["failure_code"] == "HTTP_429"
    assert events[1]["status"] == "CAPTURED"
    assert sleeps and sleeps[0] >= 10
    assert inspect_gold_collector_status(tmp_path)["failed_cycles"] == 1


def test_collector_respects_server_retry_after(tmp_path):
    adapter, _ = transport(rate_limit_first=True, retry_after="85")
    sleeps = []
    with httpx.Client(transport=adapter) as client:
        run_continuous_gold_collection(
            tmp_path, client=client, interval_seconds=10, max_cycles=2,
            sleep=sleeps.append,
        )
    assert sleeps[0] >= 85


def test_collector_resets_exponential_backoff_after_success(tmp_path):
    adapter, _ = transport(rate_limit_attempts=(1, 3))
    sleeps = []
    with httpx.Client(transport=adapter) as client:
        report = run_continuous_gold_collection(
            tmp_path, client=client, interval_seconds=10, max_cycles=4,
            sleep=sleeps.append,
        )
    assert report["failed_cycles"] == 2
    assert sleeps == [20, 10, 20]


def test_collector_status_rejects_tampered_even_if_filename_unchanged(tmp_path):
    adapter, _ = transport()
    with httpx.Client(transport=adapter) as client:
        run_continuous_gold_collection(
            tmp_path, client=client, interval_seconds=10, max_cycles=1,
        )
    p = next((tmp_path / "collector_events").glob("*.json"))
    p.write_bytes(p.read_bytes().replace(b"CAPTURED", b"FAKE____"))
    with pytest.raises(GoldContinuousError, match="SHA-256"):
        inspect_gold_collector_status(tmp_path)


def test_collector_detects_removed_cycle_in_digest_chain(tmp_path):
    adapter, _ = transport()
    with httpx.Client(transport=adapter) as client:
        run_continuous_gold_collection(
            tmp_path, client=client, interval_seconds=10, max_cycles=2,
            sleep=lambda _: None,
        )
    files = sorted((tmp_path / "collector_events").glob("*.json"))
    assert len(files) == 2
    files[0].unlink()
    with pytest.raises(GoldContinuousError, match="chain"):
        inspect_gold_collector_status(tmp_path)


def test_collector_rejects_unbounded_or_overaggressive_configuration(tmp_path):
    with pytest.raises(ValueError):
        run_continuous_gold_collection(tmp_path, interval_seconds=1, max_cycles=1)
    with pytest.raises(ValueError):
        run_continuous_gold_collection(tmp_path, interval_seconds=10, max_cycles=0)
    with pytest.raises(ValueError):
        run_continuous_gold_collection(tmp_path, interval_seconds=10, max_markets=101)


def test_gold_cli_exposes_continuous_collection_and_status(tmp_path, monkeypatch, capsys):
    from kalshi_edge.gold.audit import main
    invocations = []
    monkeypatch.setattr(
        "kalshi_edge.gold.continuous.run_continuous_gold_collection",
        lambda root, **kwargs: invocations.append((root, kwargs)) or {"cycles": 1},
    )
    monkeypatch.setattr(
        "kalshi_edge.gold.continuous.inspect_gold_collector_status",
        lambda root: {"cycles": 1},
    )
    assert main(["--collect-continuously", "--output-root", str(tmp_path),
                 "--interval-seconds", "20", "--max-cycles", "2",
                 "--max-markets", "1"]) == 0
    assert invocations[0][0] == tmp_path
    assert invocations[0][1]["interval_seconds"] == 20
    assert invocations[0][1]["max_cycles"] == 2
    assert invocations[0][1]["max_markets"] == 1
    assert main(["--collector-status", "--output-root", str(tmp_path)]) == 0
    with pytest.raises(SystemExit):
        main(["--collect-continuously", "--capture-quotes"])
