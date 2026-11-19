"""Live METAR event/receipt timestamps must be independent of collection time."""
from __future__ import annotations
import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from kalshi_edge.weather.collection import parse_awc_metar, collect_metars_once


def test_metar_preserves_awc_event_time_not_download_receipt():
    receipt=datetime(2026,11,19,18,tzinfo=timezone.utc)
    point=parse_awc_metar(
        {"icaoId":"KORD","temp":15.0,"obsTime":receipt.timestamp()-900,"rawOb":"KORD 191745Z"},
        receipt,
    )
    assert point["station"]=="KORD"
    assert point["event_time"]==receipt-timedelta(minutes=15)
    assert point["received_at"]==receipt
    assert point["temp_f"]==59.0


@pytest.mark.parametrize("input",[
    {"icaoId":"KORD","temp":float("nan"),"obsTime":1795000000},
    {"icaoId":"KORD","temp":300,"obsTime":1795000000},
    {"icaoId":"XXXX","temp":20,"obsTime":1795000000},
    {"icaoId":"KORD","temp":20},
])
def test_metar_rejects_unusable_observations(input):
    with pytest.raises((ValueError,KeyError,TypeError)):
        parse_awc_metar(input,datetime(2026,11,19,18,tzinfo=timezone.utc))


def test_metar_collection_deduplicates_and_writes_compatible_sqlite(tmp_path):
    now=datetime(2026,11,19,18,tzinfo=timezone.utc)
    def mock(request):
        assert request.url.host=="aviationweather.gov"
        assert request.url.path=="/api/data/metar"
        return httpx.Response(200,json=[{
            "icaoId":"KORD","temp":15.0,
            "obsTime":now.timestamp()-600,"rawOb":"KORD 191750Z"
        }])
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as client:
            a=await collect_metars_once(tmp_path/"weather.sqlite3",client=client,stations=("KORD",),clock=lambda:now)
            b=await collect_metars_once(tmp_path/"weather.sqlite3",client=client,stations=("KORD",),clock=lambda:now)
            return a,b
    a,b=asyncio.run(run())
    assert a["inserted"]==1
    assert b["inserted"]==0
    with sqlite3.connect(tmp_path/"weather.sqlite3") as conn:
        row=conn.execute("SELECT station,source,received_at,payload FROM samples").fetchone()
    assert row[0]=="KORD"
    assert row[1]=="METAR"
    assert row[2]==now.isoformat()
    assert '"temp_f": 59.0' in row[3]
