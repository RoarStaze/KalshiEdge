"""Pyth historical API backfill: authenticated GET; no key stored in gold artifacts."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from kalshi_edge.gold.pyth_fetch import (
    GoldPythFetchError, fetch_pyth_history, fetch_pyth_backfill,
)
from kalshi_edge.gold.oracle_history import inspect_pyth_history_evidence

START = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())


def test_fetch_historical_ohlc_uses_bearer_only_in_request_not_persisted(tmp_path):
    requests = []
    def respond(req):
        requests.append(req)
        assert req.url.host == "pyth.dourolabs.app"
        assert req.url.path == "/v1/real_time/history"
        assert req.url.params["symbol"] == "Metal.Index.1OZGOLD/USD"
        assert req.url.params["resolution"] == "1"
        assert req.headers["Authorization"] == "Bearer SECRET-TEST-KEY"
        return httpx.Response(200,json={
            "s":"ok","t":[START, START+60],
            "o":[2000,2001],"h":[2001,2002],
            "l":[1999,2000],"c":[2000,2001],
        })
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result=fetch_pyth_history(tmp_path, start_ts=START,
                    end_ts=START+60, api_key="SECRET-TEST-KEY", client=client)
    assert len(requests)==1
    assert result["training_ready"] is False
    assert result["settlement_verified"] is False
    artifact=Path(result["artifact_path"])
    assert artifact.exists()
    assert "SECRET-TEST-KEY" not in artifact.read_text()
    evidence=inspect_pyth_history_evidence(artifact)
    assert evidence["request"]["host"]=="pyth.dourolabs.app"


def test_fetch_refuses_auth_errors_and_never_creates_fake_history(tmp_path):
    def respond(req):
        return httpx.Response(401,json={"error":"bad key"})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(GoldPythFetchError,match="HTTP 401"):
            fetch_pyth_history(tmp_path,start_ts=START,end_ts=START+60,
                               api_key="BADKEY",client=client)
    assert not (tmp_path/"pyth_history_imports").exists()


def test_backfill_chunks_and_stays_bounded(tmp_path):
    received=[]
    def respond(req):
        low,high=int(req.url.params["from"]),int(req.url.params["to"])
        received.append((low,high))
        assert high-low <= 86400
        return httpx.Response(200,json={
            "s":"ok","t":[low],
            "o":[2000],"h":[2001],"l":[1999],"c":[2000],
        })
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        report=fetch_pyth_backfill(tmp_path,start_ts=START,
                        end_ts=START+2*86400+60,api_key="X",client=client)
    assert len(received)==3
    assert report["downloaded_requests"]==3
    assert report["training_ready"] is False
    assert len(report["artifact_paths"])==3
    with pytest.raises(GoldPythFetchError,match="bounded"):
        fetch_pyth_backfill(tmp_path,start_ts=START,
                            end_ts=START+45*86400,api_key="X")


def test_credentials_and_ranges_are_explicitly_restricted(tmp_path):
    for kwargs in [
        {"api_key":""},
        {"start_ts":START,"end_ts":START+90*86400},
        {"start_ts":START,"end_ts":START},
        {"start_ts":START+5,"end_ts":START+60},
    ]:
        params={"start_ts":START,"end_ts":START+60,"api_key":"X",**kwargs}
        with pytest.raises(GoldPythFetchError):
            fetch_pyth_history(tmp_path,**params)
