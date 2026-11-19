"""Automatic settlement evidence: source-level agreement, not NOAA proxy labels."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from kalshi_edge.weather.settlement_collector import (
    extract_twc_high, verify_kalshi_markets, collect_station_day, poll_settlements,
)
from kalshi_edge.weather.calibration import audit_settlement_evidence


DAY=date(2026,11,18)
DATE="2026-11-18"
TWC_URL="https://weather.com/kalshi/api/climate/primary?date=2026-11-18"
EVENT="KXHIGHCHI-26NOV18"


def publisher(value=62, status="Final", station="CLIMDW", day=DATE):
    # Illustrative explicit-schema HTTPX fixture, NOT a copied TWC response.
    return json.dumps({"data":[{"climateId":station,"date":day,
                               "highF":value,"status":status}]}).encode()


def market(suffix, rule, yes):
    return {
        "ticker":f"{EVENT}-{suffix}","event_ticker":EVENT,
        "status":"settled","result":"yes" if yes else "no",
        "settlement_ts":"2026-11-19T14:00:00Z",
        "rules_primary":f"If the maximum temperature recorded at Chicago (CLIMDW) for Nov 18, 2026, is {rule} according to The Weather Company, then the market resolves to Yes.",
    }


def markets():
    return [
        market("T65","less than 65° fahrenheit",True),
        market("T61","less than 61° fahrenheit",False),
        market("B62.5","between 62° and 63° fahrenheit",True),
    ]


def test_twc_publisher_requires_explicit_final_day_station_and_units():
    assert extract_twc_high(publisher(), DAY, "KMDW") == 62
    for blob in (
        publisher(status="Preliminary"), publisher(station="CLINYC"),
        publisher(day="2026-11-17"), publisher(value=62.7),
        publisher(value=None), b"not json",
        json.dumps({"data":[{"climateId":"CLIMDW","date":DATE,
                              "highF":62,"status":"Final","unit":"C"}]}).encode(),
    ):
        with pytest.raises(ValueError):
            extract_twc_high(blob,DAY,"KMDW")


def test_kalshi_bracket_results_and_rule_source_have_to_agree():
    assert verify_kalshi_markets(markets(),"KMDW",DAY,62) == f"{EVENT}-T65"
    for edited in (
        [{**markets()[0],"result":"no"},*markets()[1:]],
        [{**markets()[0],"rules_primary":markets()[0]["rules_primary"].replace(
          "The Weather Company","National Weather Service")},*markets()[1:]],
        [{**markets()[0],"status":"open"},*markets()[1:]],
        [{**markets()[0],"ticker":"KXHIGHCHI-26NOV17-T65"},*markets()[1:]],
        [{**markets()[0],"rules_primary":markets()[0]["rules_primary"].replace("CLIMDW","CLIORD")},*markets()[1:]],
    ):
        with pytest.raises(ValueError):
            verify_kalshi_markets(edited,"KMDW",DAY,62)


def test_collect_first_party_files_and_revalidate_against_bytes(tmp_path):
    root=tmp_path/"weather"
    def handler(request):
        if request.url.host=="weather.com":
            assert str(request.url)==TWC_URL
            return httpx.Response(200,content=publisher())
        assert request.url.host=="external-api.kalshi.com"
        assert request.url.path=="/trade-api/v2/markets"
        assert request.url.params["event_ticker"]==EVENT
        return httpx.Response(200,json={"markets":markets(),"cursor":""})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await collect_station_day(root,"KMDW",DAY,client=client,
                clock=lambda:datetime(2026,11,20,12,tzinfo=timezone.utc))
    result=asyncio.run(run())
    assert result["state"]=="CROSSCHECKED_UNCERTIFIED"
    assert result["station"]=="KMDW"
    accepted,rejected=audit_settlement_evidence(root)
    assert rejected==[]
    assert len(accepted)==1
    assert accepted[0]["actual_max_f"]==62
    assert accepted[0]["evidence_state"]=="AUTO_CORROBORATED_UNCERTIFIED"
    assert accepted[0]["station"]=="KMDW"
    manifest=root/"settlement_auto.jsonl"
    row=json.loads(manifest.read_text().splitlines()[0])
    assert row["source_capture_file"].startswith("KMDW_2026-11-18_twc_")
    assert row["kalshi_capture_file"].startswith("KMDW_2026-11-18_kalshi_")
    # Bytes are independently rehashed and re-parsed on every calibration run.
    (root/"evidence"/row["source_capture_file"]).write_bytes(b"tamper")
    accepted,rejected=audit_settlement_evidence(root)
    assert accepted==[]
    assert len(rejected)==1


def test_unavailable_or_preliminary_twc_never_creates_label(tmp_path):
    root=tmp_path/"weather"
    def handler(request):
        if request.url.host=="weather.com":
            return httpx.Response(200,content=publisher(status="Preliminary"))
        return httpx.Response(200,json={"markets":markets(),"cursor":""})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await collect_station_day(root,"KMDW",DAY,client=client,
                clock=lambda:datetime(2026,11,20,12,tzinfo=timezone.utc))
    result=asyncio.run(run())
    assert result["state"]=="UNVERIFIED"
    assert not (root/"settlement_auto.jsonl").exists()


def test_wrong_temperature_or_bad_market_outcome_never_creates_label(tmp_path):
    root=tmp_path/"weather"
    def handler(request):
        if request.url.host=="weather.com":
            return httpx.Response(200,content=publisher(value=70))
        return httpx.Response(200,json={"markets":markets(),"cursor":""})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await collect_station_day(root,"KMDW",DAY,client=client,
                clock=lambda:datetime(2026,11,20,12,tzinfo=timezone.utc))
    result=asyncio.run(run())
    assert result["state"]=="UNVERIFIED"
    assert audit_settlement_evidence(root)[0]==[]


def test_do_not_collect_before_climate_day_end(tmp_path):
    root=tmp_path/"weather"
    result=asyncio.run(collect_station_day(root,"KMDW",DAY,
        clock=lambda:datetime(2026,11,18,18,tzinfo=timezone.utc)))
    assert result["state"]=="NOT_FINAL"
    assert not root.exists()


def test_auto_collector_ignores_research_only_ohare(tmp_path):
    # There are no official KXHIGHCHI outcomes for KORD: never create labels.
    root=tmp_path/"weather"
    result=asyncio.run(collect_station_day(root,"KORD",DAY))
    assert result["state"]=="UNSUPPORTED_STATION"
    assert not root.exists()



def test_revised_final_publisher_value_revokes_prior_unconfirmed_auto_label(tmp_path):
    root=tmp_path/"weather"
    state={"temp":62}
    def transport(request):
        if request.url.host=="weather.com":
            return httpx.Response(200,content=publisher(value=state["temp"],status="Revised"))
        return httpx.Response(200,json={"markets":markets(),"cursor":""})
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            one=await collect_station_day(root,"KMDW",DAY,client=client,
                clock=lambda:datetime(2026,11,20,12,tzinfo=timezone.utc))
            state["temp"]=70
            two=await collect_station_day(root,"KMDW",DAY,client=client,
                clock=lambda:datetime(2026,11,20,16,tzinfo=timezone.utc))
            return one,two
    one,two=asyncio.run(check())
    assert one["state"]=="CROSSCHECKED_UNCERTIFIED"
    assert two["state"]=="UNVERIFIED"
    # A revision that contradicts settled markets cannot retain its old label
    # in forward calibration as if it were still corroborated.
    assert audit_settlement_evidence(root)[0]==[]
    assert len(list((root/"evidence").glob("*twc*")))>=1



def test_archived_kalshi_event_uses_official_historical_market_endpoint(tmp_path):
    root=tmp_path/"weather"
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        if request.url.host=="weather.com":
            return httpx.Response(200,content=publisher())
        if request.url.path=="/trade-api/v2/markets":
            return httpx.Response(200,json={"markets":[],"cursor":""})
        assert request.url.path=="/trade-api/v2/historical/markets"
        return httpx.Response(200,json={"markets":markets(),"cursor":""})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await collect_station_day(
                root,"KMDW",DAY,client=client,
                clock=lambda:datetime(2026,11,20,12,tzinfo=timezone.utc))
    out=asyncio.run(run())
    assert out["state"]=="CROSSCHECKED_UNCERTIFIED"
    assert "/trade-api/v2/historical/markets" in calls
    accepted,rejected=audit_settlement_evidence(root)
    assert len(accepted)==1 and rejected==[]


def test_settlement_poller_requires_actual_frozen_forecasts(tmp_path):
    db=tmp_path/"weather"/"weatheredge.sqlite3"
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda req: (_ for _ in ()).throw(AssertionError("no HTTP without journal")))
        ) as client:
            return await poll_settlements(db,client=client)
    report=asyncio.run(run())
    assert report["state"]=="NO_ELIGIBLE_FORECAST_DAYS"
    assert report["crosschecked"]==0



def test_settlement_poll_never_limits_journal_to_oldest_800(monkeypatch,tmp_path):
    from kalshi_edge.weather import settlement_collector as module
    limits=[]
    def snapshots(db,*,limit):
        limits.append(limit)
        return []
    monkeypatch.setattr(module,"read_station_snapshots",snapshots)
    result=asyncio.run(module.poll_settlements(tmp_path/"unused.sqlite3"))
    assert result["state"]=="NO_ELIGIBLE_FORECAST_DAYS"
    assert limits and limits[0]>=100_000



def test_weather_dashboard_shows_per_station_settlement_failure_reason():
    from kalshi_edge.weather.research_ui import render_weather_page
    html=render_weather_page("forecast")
    assert "collector.settlement" in html
    assert "outcome.details" in html
    assert "UNVERIFIED" in html
    assert "/api/manual-trade" not in html
