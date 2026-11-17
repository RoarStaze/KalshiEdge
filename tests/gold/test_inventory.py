from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import httpx
import pytest

from kalshi_edge.gold.inventory import GoldInventoryError, collect_gold_inventory

SERIES = {"series": {"ticker": "KXGOLD15M", "settlement_sources": [
    {"url": "https://app.pyth.com/explore/Metal.Index.1OZGOLD%2FUSD"}
]}}


def record(ticker: str, *, result: str = "", status: str = "initialized") -> dict:
    return {"ticker": f"KXGOLD15M-{ticker}", "status": status, "result": result,
            "rules_primary": "If gold end is at least start, then Yes.",
            "close_time": "2026-11-18T05:00:00Z"}


def transport_responses(*, different_results: bool = False):
    archive1 = record("A", result="yes", status="finalized")
    archive2 = record("B", result="no", status="finalized")
    live2 = record("B", result="yes" if different_results else "no", status="finalized")
    live3 = record("C")
    seen = []
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        cursor = request.url.params.get("cursor")
        if path.endswith("/series/KXGOLD15M"):
            return httpx.Response(200, json=SERIES)
        if path.endswith("/historical/cutoff"):
            return httpx.Response(200, json={"market_settled_ts": "2026-11-01T00:00:00Z"})
        if path.endswith("/historical/markets"):
            return httpx.Response(200, json=({"markets": [archive1], "cursor": "hist2"} if not cursor
                else {"markets": [archive2], "cursor": ""}))
        if path.endswith("/markets"):
            return httpx.Response(200, json=({"markets": [live2], "cursor": "live2"} if not cursor
                else {"markets": [live3], "cursor": ""}))
        return httpx.Response(404)
    return httpx.MockTransport(handle), seen


def test_collects_both_partitions_and_preserves_immutable_raw_pages(tmp_path: Path) -> None:
    transport, seen = transport_responses()
    with httpx.Client(transport=transport) as client:
        report = collect_gold_inventory(tmp_path, client=client, page_size=2, max_pages_per_tier=3)
    assert report["unique_markets"] == 3
    assert report["source_overlap_count"] == 1
    assert report["duplicate_record_count"] == 1
    assert report["tier_page_counts"] == {"historical": 2, "live": 2}
    assert report["page_caps_reached"] == []
    assert report["pagination_exhausted"] is True
    assert report["complete_for_request_window"] is False
    assert report["ready_for_training"] is False
    assert report["ready_for_live_trading"] is False
    assert report["settlement_oracle_verified"] is False
    assert report["qualification_gate"] == "0.69"
    assert report["checkpoint_seconds"] == 480
    assert report["observed_official_yes_results_unverified_against_pyth"] == 1
    assert report["observed_official_no_results_unverified_against_pyth"] == 1
    assert report["finalized_yes_results_unverified_against_pyth"] == 1
    assert report["finalized_no_results_unverified_against_pyth"] == 1
    assert report["status_counts_by_record"]["finalized"] == 3
    assert all(req.method == "GET" for req in seen)
    assert all(req.url.params.get("series_ticker") == "KXGOLD15M" for req in seen
               if req.url.path.endswith("/markets"))
    path = Path(report["artifact_path"])
    raw = path.read_bytes()
    assert sha256(raw).hexdigest() == report["sha256"]
    snapshot = json.loads(raw)
    assert len(snapshot["pages"]["historical"]) == 2
    assert len(snapshot["pages"]["live"]) == 2
    assert snapshot["pages"]["historical"][0]["response"]["cursor"] == "hist2"
    assert snapshot["pages"]["historical"][0]["received_at_ns"] > 0
    assert snapshot["cutoff"]["market_settled_ts"].startswith("2026")
    assert path.parent == tmp_path / "market_inventories"


def test_disagreements_in_official_result_do_not_become_labels(tmp_path: Path) -> None:
    transport, _ = transport_responses(different_results=True)
    with httpx.Client(transport=transport) as client:
        report = collect_gold_inventory(tmp_path, client=client)
    assert report["contradictory_official_result_tickers"] == ["KXGOLD15M-B"]
    assert report["ready_for_training"] is False


def test_page_cap_marks_incomplete_without_fetching_the_next_page(tmp_path: Path) -> None:
    transport, seen = transport_responses()
    with httpx.Client(transport=transport) as client:
        report = collect_gold_inventory(tmp_path, client=client, page_size=2, max_pages_per_tier=1)
    assert report["page_caps_reached"] == ["historical", "live"]
    assert report["pagination_exhausted"] is False
    assert report["unique_markets"] == 2
    assert sum(req.url.path.endswith("/markets") for req in seen) == 2


def test_repeated_cursor_fails_closed_without_writing(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/series/KXGOLD15M"):
            return httpx.Response(200, json=SERIES)
        if req.url.path.endswith("/historical/cutoff"):
            return httpx.Response(200, json={"market_settled_ts": "2026-11-01T00:00:00Z"})
        return httpx.Response(200, json={"markets": [record("A")], "cursor": "repeat"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GoldInventoryError, match="repeated cursor"):
            collect_gold_inventory(tmp_path, client=client, max_pages_per_tier=3)
    assert not (tmp_path / "market_inventories").exists()


def test_wrong_series_market_fails_closed(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/series/KXGOLD15M"):
            return httpx.Response(200, json=SERIES)
        if req.url.path.endswith("/historical/cutoff"):
            return httpx.Response(200, json={"market_settled_ts": "2026-11-01T00:00:00Z"})
        return httpx.Response(200, json={"markets": [{"ticker": "KXBTC15M-FOO"}], "cursor": ""})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GoldInventoryError, match="unexpected ticker"):
            collect_gold_inventory(tmp_path, client=client)
    assert not (tmp_path / "market_inventories").exists()


def test_missing_market_field_fails_closed(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/series/KXGOLD15M"):
            return httpx.Response(200, json=SERIES)
        if req.url.path.endswith("/historical/cutoff"):
            return httpx.Response(200, json={"market_settled_ts": "2026-11-01T00:00:00Z"})
        return httpx.Response(200, json={"markets": None, "cursor": ""})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GoldInventoryError, match="markets array"):
            collect_gold_inventory(tmp_path, client=client)


def test_protects_nested_canonical_raw_path_before_network(tmp_path: Path) -> None:
    def no_network(req: httpx.Request) -> httpx.Response:
        raise AssertionError("unexpected network")
    with httpx.Client(transport=httpx.MockTransport(no_network)) as client:
        with pytest.raises(ValueError, match="canonical Phase 1"):
            collect_gold_inventory(tmp_path / "data" / "raw" / "gold", client=client)


def test_rejects_unbounded_or_empty_inventory_parameters(tmp_path: Path) -> None:
    for limits in ({"page_size": 0}, {"page_size": 1001}, {"max_pages_per_tier": 0},
                   {"max_pages_per_tier": 501}):
        with pytest.raises(ValueError):
            collect_gold_inventory(tmp_path, **limits)


def test_inventory_cli_dispatches_and_rejects_inspection_mode(tmp_path: Path, monkeypatch, capsys) -> None:
    from kalshi_edge.gold.audit import main
    seen = {}
    def fake_inventory(root, *, page_size, max_pages_per_tier):
        seen.update(root=root, page_size=page_size, max_pages_per_tier=max_pages_per_tier)
        return {"ready_for_training": False, "unique_markets": 3}
    monkeypatch.setattr("kalshi_edge.gold.inventory.collect_gold_inventory", fake_inventory)
    assert main(["--inventory", "--output-root", str(tmp_path),
                 "--page-size", "100", "--max-pages-per-tier", "7"]) == 0
    assert seen == {"root": tmp_path, "page_size": 100, "max_pages_per_tier": 7}
    assert json.loads(capsys.readouterr().out)["ready_for_training"] is False
    with pytest.raises(SystemExit):
        main(["--inventory", "--inspect-artifact", "some-snapshot.json"])
