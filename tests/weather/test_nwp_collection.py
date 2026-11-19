"""NOAA indexed HRRR/NBM range-download contracts, no live network in CI."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from kalshi_edge.weather.nwp_collection import (
    nwp_url, parse_index, select_2m_temperature, collect_cycle,
)
from kalshi_edge.weather.predictor import STATIONS


def test_urls_are_noaa_official_conventions():
    run=datetime(2026,11,19,12,tzinfo=timezone.utc)
    assert nwp_url("HRRR",run,5).endswith("/hrrr.20261119/conus/hrrr.t12z.wrfsfcf05.grib2")
    assert nwp_url("NBM",run,5).endswith("/blend.20261119/12/core/blend.t12z.core.f005.co.grib2")
    with pytest.raises(ValueError):
        nwp_url("NCEP",run,1)
    with pytest.raises(ValueError):
        nwp_url("NBM",run,49)


def test_index_selects_exact_single_2m_temperature():
    entries=parse_index("1:0:d=2026111912:TMP:2 m above ground:5 hour fcst\n"
                        "2:12:d=2026111912:DPT:2 m above ground:5 hour fcst")
    selected=select_2m_temperature(entries)
    assert selected.start==0 and selected.end==11
    with pytest.raises(ValueError):
        select_2m_temperature(entries[:0])


def test_atomic_nwp_cycle_writes_station_points_and_true_receipt(tmp_path):
    run=datetime(2026,11,19,12,tzinfo=timezone.utc)
    received=run+timedelta(hours=1)
    index="1:0:d=2026111912:TMP:2 m above ground:5 hour fcst\n" + \
          "2:12:d=2026111912:DPT:2 m above ground:5 hour fcst"
    grib=b"GRIB12345678"
    def transport(request):
        if str(request.url).endswith(".idx"):
            return httpx.Response(200,text=index)
        assert request.headers.get("range")=="bytes=0-11"
        return httpx.Response(206,content=grib,headers={"Content-Range":"bytes 0-11/24"})

    async def run_collection():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await collect_cycle(
                client, tmp_path/"w.sqlite3", "HRRR", run,
                [4,5,6], clock=lambda:received,
                decode=lambda blob, stations: {s:288.15 for s in stations},
            )
    result=asyncio.run(run_collection())
    assert result["points"]==12
    with sqlite3.connect(tmp_path/"w.sqlite3") as conn:
        rows=conn.execute(
            "SELECT station,source,event_time,received_at,payload FROM samples"
        ).fetchall()
    assert len(rows)==12
    assert all(row[1]=="HRRR" for row in rows)
    assert all(row[2]==run.isoformat() for row in rows)
    assert all(row[3]==received.isoformat() for row in rows)
    assert {row[0] for row in rows}==set(STATIONS)


def test_bad_range_aborts_entire_cycle_without_partial_database_rows(tmp_path):
    run=datetime(2026,11,19,12,tzinfo=timezone.utc)
    def transport(request):
        if str(request.url).endswith(".idx"):
            return httpx.Response(200,text="1:0:d=2026111912:TMP:2 m above ground:5 hour fcst\n"
                                                "2:12:d=2026111912:DPT:2 m above ground:5 hour fcst")
        return httpx.Response(200,content=b"GRIB12345678")
    async def run_collection():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await collect_cycle(client,tmp_path/"db.sqlite3","NBM",run,[5],
                    clock=lambda:run+timedelta(hours=1),
                    decode=lambda blob, stations: {s:288.15 for s in stations})
    with pytest.raises(ValueError,match="206"):
        asyncio.run(run_collection())
    assert not (tmp_path/"db.sqlite3").exists()
