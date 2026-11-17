from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import httpx
import pytest

from kalshi_edge.gold.candle_probe import probe_checkpoint_candles, GoldCandleProbeError


def market(name: str, close: str, *, status="finalized", result="yes") -> dict:
    return {"ticker": f"KXGOLD15M-{name}", "close_time": close, "status": status, "result": result}


def artifact(tmp_path: Path, *, bad_series=False) -> Path:
    historical = [market("OLD", "2026-11-11T00:15:00Z"), market("SHARED", "2026-11-11T00:30:00Z")]
    live = [market("NEW", "2026-11-16T05:15:00Z", result="no"),
            market("SHARED", "2026-11-11T00:30:00Z"),
            market("PENDING", "2026-11-16T05:30:00Z", status="initialized", result="")]
    snap = {"schema_version": 1,
            "series": {"series": {"ticker": "WRONG" if bad_series else "KXGOLD15M", "settlement_sources": [
                {"url": "https://app.pyth.com/explore/Metal.Index.1OZGOLD%2FUSD"}]}},
            "cutoff": {"market_settled_ts": "2026-11-14T00:00:00Z"},
            "report": {"series_ticker": "KXGOLD15M", "qualification_gate": "0.69", "checkpoint_seconds": 480,
                       "ready_for_training": False},
            "pages": {
                "historical": [{"params": {"series_ticker": "KXGOLD15M"}, "response": {"markets": historical}}],
                "live": [{"params": {"series_ticker": "KXGOLD15M"}, "response": {"markets": live}}]}}
    content = (json.dumps(snap, sort_keys=True) + "\n").encode()
    p = tmp_path / f"123-{sha256(content).hexdigest()[:16]}.json"
    p.write_bytes(content)
    return p


def test_probe_uses_both_correct_endpoints_and_precheckpoint_windows(tmp_path: Path):
    requests = []
    def handle(req: httpx.Request):
        requests.append(req)
        assert req.method == "GET"
        assert req.url.params.get("period_interval") == "1"
        start = int(req.url.params["start_ts"])
        end = int(req.url.params["end_ts"])
        assert end - start == 7 * 60
        if req.url.path.endswith("/KXGOLD15M-OLD/candlesticks"):
            assert req.url.path.startswith("/trade-api/v2/historical/markets/")
            return httpx.Response(200, json={"ticker": "KXGOLD15M-OLD", "candlesticks": [
                {"end_period_ts": end - 60, "yes_bid": {"close_dollars": "0.53"}},
                {"end_period_ts": end, "yes_bid": {"close_dollars": "0.54"}}]})
        if req.url.path.endswith("/KXGOLD15M-NEW/candlesticks"):
            assert req.url.path.startswith("/trade-api/v2/series/KXGOLD15M/markets/")
            return httpx.Response(200, json={"ticker": "KXGOLD15M-NEW", "candlesticks": []})
        raise AssertionError(req.url.path)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        report = probe_checkpoint_candles(artifact(tmp_path), tmp_path, client=client,
                                          sample_per_tier=2, lookback_minutes=7)
    assert report["market_count"] == 2
    assert report["tiers"] == {"historical": 1, "live": 1}
    assert report["candlesticks_total"] == 2
    assert report["markets_with_no_candles"] == 1
    assert report["overlapping_market_tickers_excluded"] == 1
    assert report["ready_for_training"] is False
    assert report["ready_for_live_trading"] is False
    assert report["checkpoint_verified"] is False
    assert report["point_in_time_publication_verified"] is False
    assert len(requests) == 2
    p = Path(report["artifact_path"])
    assert sha256(p.read_bytes()).hexdigest() == report["sha256"]
    snap = json.loads(p.read_bytes())
    assert snap["inventory_sha256"] == sha256(Path(report["inventory_path"]).read_bytes()).hexdigest()
    assert [m["route"] for m in snap["market_candles"]] == ["historical", "live"]
    assert snap["market_candles"][0]["raw_response"]["candlesticks"][1]["end_period_ts"] <= snap["market_candles"][0]["candidate_checkpoint_unix"]


def test_rejects_tampered_inventory_before_requests(tmp_path: Path):
    p = artifact(tmp_path)
    p.write_bytes(p.read_bytes().replace(b"OLD", b"BAD"))
    def no_network(req):
        raise AssertionError("network")
    with httpx.Client(transport=httpx.MockTransport(no_network)) as client:
        with pytest.raises(GoldCandleProbeError, match="SHA-256"):
            probe_checkpoint_candles(p, tmp_path, client=client)


def test_rejects_wrong_series_identity_before_requests(tmp_path: Path):
    p = artifact(tmp_path, bad_series=True)
    def no_network(req):
        raise AssertionError("network")
    with httpx.Client(transport=httpx.MockTransport(no_network)) as client:
        with pytest.raises(GoldCandleProbeError, match="source identity"):
            probe_checkpoint_candles(p, tmp_path, client=client)


def test_response_ticker_mismatch_fails_without_artifact(tmp_path: Path):
    def handler(req):
        return httpx.Response(200, json={"ticker": "KXBTC15M-WRONG", "candlesticks": []})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GoldCandleProbeError, match="ticker mismatch"):
            probe_checkpoint_candles(artifact(tmp_path), tmp_path, client=client)
    assert not (tmp_path / "candle_probes").exists()


def test_future_candle_is_never_used_and_fails_closed(tmp_path: Path):
    def handler(req):
        ticker = req.url.path.split("/")[-2]
        return httpx.Response(200, json={"ticker": ticker, "candlesticks": [
            {"end_period_ts": int(req.url.params["end_ts"]) + 60}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GoldCandleProbeError, match="outside requested checkpoint window"):
            probe_checkpoint_candles(artifact(tmp_path), tmp_path, client=client)
    assert not (tmp_path / "candle_probes").exists()


def test_invalid_candle_ts_and_response_shape_fail_closed(tmp_path: Path):
    for candles in ([{"end_period_ts": True}], [{}], "not an array"):
        def handler(req):
            return httpx.Response(200, json={"ticker": req.url.path.split("/")[-2], "candlesticks": candles})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(GoldCandleProbeError, match="candlestick"):
                probe_checkpoint_candles(artifact(tmp_path), tmp_path, client=client)


def test_invalid_parameters_and_protected_path_reject_without_network(tmp_path: Path):
    p = artifact(tmp_path)
    for args in ({"sample_per_tier": 0}, {"sample_per_tier": 21}, {"lookback_minutes": 0},
                 {"lookback_minutes": 101}):
        with pytest.raises(ValueError):
            probe_checkpoint_candles(p, tmp_path, **args)
    with pytest.raises(ValueError, match="canonical Phase 1"):
        probe_checkpoint_candles(p, tmp_path / "data" / "raw" / "gold")


def test_conflicting_market_results_are_excluded(tmp_path: Path):
    p = artifact(tmp_path)
    data = json.loads(p.read_bytes())
    data["pages"]["live"][0]["response"]["markets"][1]["result"] = "no"
    raw = (json.dumps(data, sort_keys=True) + "\n").encode()
    p2 = tmp_path / f"321-{sha256(raw).hexdigest()[:16]}.json"
    p2.write_bytes(raw)
    def handler(req):
        return httpx.Response(200, json={"ticker": req.url.path.split("/")[-2], "candlesticks": []})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = probe_checkpoint_candles(p2, tmp_path, client=client)
    assert report["market_count"] == 2
    assert report["conflicted_market_tickers_excluded"] == 1
