"""Physical hourly Weather Index ensemble: source and chronology regression tests."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from kalshi_edge.weather.hourly_physics import (
    parse_calibrations, select_calibration, aggregate_index_celsius,
    parse_station_coordinates, collect_hourly_physics_once, read_hourly_physics,
)

NOW=datetime(2026,11,22,17,20,tzinfo=timezone.utc)
TARGET=datetime(2026,11,22,18,tzinfo=timezone.utc)


def payload():
    effective=int(datetime(2026,11,16,tzinfo=timezone.utc).timestamp()*1000)
    published=int(datetime(2026,11,15,tzinfo=timezone.utc).timestamp()*1000)
    return {"city":"chicago","units":"celsius","calibrations":[{
        "config_version":"chicago-temperature-v1.1",
        "effective_at_ms":effective,"published_at_ms":published,
        "city_reference_c":.05,
        "stations":[
            {"station_id":"KMDW1M","weight":.5,"offset_c":.2},
            {"station_id":"KORD1M","weight":.3,"offset_c":-.5},
            {"station_id":"KPWK1M","weight":.2,"offset_c":.5}],
        "change_reason":"weekly",
        "calibration_window_start_ms":effective-7*86400000,
        "calibration_window_end_ms":effective-1,
    }]}


def test_parse_official_configuration_and_exact_offset_formula():
    configs=parse_calibrations(payload(),"chicago")
    assert len(configs)==1
    config=select_calibration(configs,TARGET,NOW)
    assert config["version"]=="chicago-temperature-v1.1"
    assert config["city_reference_c"]==pytest.approx(.05)
    # (20-.2+.05)*.5 + (22+.5+.05)*.3 + (19-.5+.05)*.2
    expected=.5*19.85+.3*22.55+.2*18.55
    actual=aggregate_index_celsius(config,{"KMDW1M":20,"KORD1M":22,"KPWK1M":19})
    assert actual==pytest.approx(expected)
    # Kelvin is converted before applying Celsius offsets; no early F rounding.
    assert 32+1.8*actual==pytest.approx(68.72)


@pytest.mark.parametrize("mutate",[
    lambda p:p.update(units="fahrenheit"),
    lambda p:p["calibrations"][0]["stations"][0].update(weight=.8),
    lambda p:p["calibrations"][0].update(city_reference_c=5),
    lambda p:p["calibrations"][0]["stations"][0].update(station_id="KORD1M"),
    lambda p:p["calibrations"][0]["stations"][1].update(offset_c=float("nan")),
    lambda p:p["calibrations"][0].update(config_version=""),
])
def test_malformed_kalshi_calibrations_fail_closed(mutate):
    p=payload()
    mutate(p)
    with pytest.raises(ValueError):
        parse_calibrations(p,"chicago")


def test_published_and_effective_times_both_matter():
    p=payload()
    p["calibrations"].append({
        **copy.deepcopy(p["calibrations"][0]),
        "config_version":"chicago-temperature-v1.2",
        "effective_at_ms":int(TARGET.timestamp()*1000)-1000,
        "published_at_ms":int(NOW.timestamp()*1000)+60000,
    })
    records=parse_calibrations(p,"chicago")
    assert select_calibration(records,TARGET,NOW) is None
    assert select_calibration(records,TARGET,NOW+timedelta(minutes=2))["version"]=="chicago-temperature-v1.2"
    # A later effective version is never used to rewrite older event minutes.
    assert select_calibration(records,TARGET-timedelta(days=1),NOW)["version"]=="chicago-temperature-v1.1"


def test_missing_station_never_reweights_without_confirmed_quorum():
    config=parse_calibrations(payload(),"chicago")[0]
    with pytest.raises(ValueError,match="INCOMPLETE"):
        aggregate_index_celsius(config,{"KMDW1M":20,"KORD1M":22})
    with pytest.raises(ValueError):
        aggregate_index_celsius(config,{"KMDW1M":20,"KORD1M":22,"KPWK1M":float("inf")})


def test_station_metadata_is_exact_icao_match_not_fabricated_coordinates():
    config=parse_calibrations(payload(),"chicago")[0]
    stationinfo=[{"icaoId":"KMDW","lat":41.79,"lon":-87.75},
                 {"icaoId":"KORD","lat":41.97,"lon":-87.9},
                 {"icaoId":"KPWK","lat":42.11,"lon":-87.9}]
    coords=parse_station_coordinates(stationinfo,[config])
    assert coords["KMDW1M"]==(41.79,-87.75)
    with pytest.raises(ValueError,match="MISSING_STATION"):
        parse_station_coordinates(stationinfo[:2],[config])
    with pytest.raises(ValueError,match="DUPLICATE"):
        parse_station_coordinates(stationinfo+[stationinfo[0]],[config])


def test_two_noaa_model_cycles_use_calibration_and_true_receipt(tmp_path):
    requests=[]
    def api(request):
        requests.append(str(request.url))
        if request.url.path.endswith("/calibrations"):
            assert request.url.path.endswith("/weather/chicago/calibrations")
            return httpx.Response(200,json=payload())
        if request.url.path.endswith("/stationinfo"):
            assert "KMDW" in request.url.params["ids"]
            return httpx.Response(200,json=[
                {"icaoId":"KMDW","lat":41.79,"lon":-87.75},
                {"icaoId":"KORD","lat":41.97,"lon":-87.9},
                {"icaoId":"KPWK","lat":42.11,"lon":-87.9},
            ])
        return httpx.Response(404)
    async def grib(client,model,run,hour,coords):
        assert run+timedelta(hours=hour)==TARGET
        assert set(coords)=={"KMDW1M","KORD1M","KPWK1M"}
        k=273.15+(20 if model=="HRRR" else 21)
        return ({name:k for name in coords},
                "a"*64 if model=="HRRR" else "b"*64)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as client:
            return await collect_hourly_physics_once(
                tmp_path/"weatheredge.sqlite3",client=client,
                cities=("chicago",),clock=lambda:NOW,grib_fetch=grib)
    status=asyncio.run(run())
    assert status["cities"]["chicago"]["state"]=="RESEARCH_READY"
    assert status["cities"]["chicago"]["configuration"]=="chicago-temperature-v1.1"
    with sqlite3.connect(tmp_path/"weatheredge.sqlite3") as conn:
        result=read_hourly_physics(conn,"chicago",TARGET,NOW)
        assert result["status"]=="RESEARCH_ONLY_NOT_PROMOTED"
        assert result["model_count"]==2
        assert result["hrrr_index_f"]==pytest.approx(68.0,abs=.2)
        assert result["nbm_index_f"]==pytest.approx(69.8,abs=.2)
        assert result["ensemble_index_f"]==pytest.approx(
            (result["hrrr_index_f"]+result["nbm_index_f"])/2,abs=.001)
        assert result["forecast_target_utc"]==TARGET.isoformat()
        assert result["real_orders_enabled"] is False
        record=conn.execute("SELECT COUNT(*) FROM weather_hourly_physics_forecasts").fetchone()
        assert record[0]==2
    assert len([r for r in requests if "/calibrations" in r])==1


def test_no_grid_data_reports_unavailable_without_synthetic_temperature(tmp_path):
    def api(request):
        if request.url.path.endswith("/calibrations"):
            return httpx.Response(200,json=payload())
        return httpx.Response(404)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as client:
            return await collect_hourly_physics_once(
                tmp_path/"weatheredge.sqlite3",client=client,
                cities=("chicago",),clock=lambda:NOW)
    status=asyncio.run(run())
    assert status["cities"]["chicago"]["state"]=="UNAVAILABLE"
    assert status["real_orders_enabled"] is False


def test_configuration_switch_blocks_stale_station_weights(tmp_path):
    p=payload()
    newer=copy.deepcopy(p["calibrations"][0])
    newer["config_version"]="chicago-temperature-v1.2"
    newer["effective_at_ms"]=int((TARGET-timedelta(minutes=2)).timestamp()*1000)
    newer["published_at_ms"]=int((NOW-timedelta(minutes=5)).timestamp()*1000)
    newer["stations"][0]["offset_c"]+=.1
    newer["city_reference_c"]+=.05
    configs=parse_calibrations({"city":"chicago","units":"celsius",
                                "calibrations":[p["calibrations"][0],newer]},"chicago")
    assert select_calibration(configs,TARGET,NOW)["version"]=="chicago-temperature-v1.2"


def test_physics_ready_in_standard_hourly_forecast_response_without_auto_trading(tmp_path):
    from kalshi_edge.weather.hourly import read_hourly_forecasts
    def api(request):
        if request.url.path.endswith("/calibrations"):
            return httpx.Response(200,json=payload())
        if request.url.path.endswith("/stationinfo"):
            return httpx.Response(200,json=[
                {"icaoId":"KMDW","lat":41.79,"lon":-87.75},
                {"icaoId":"KORD","lat":41.97,"lon":-87.9},
                {"icaoId":"KPWK","lat":42.11,"lon":-87.9},
            ])
        return httpx.Response(404)
    async def grib(client,model,run,hour,coords):
        return ({sid:294.15 for sid in coords},"c"*64)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as client:
            return await collect_hourly_physics_once(
                tmp_path/"weatheredge.sqlite3",client=client,cities=("chicago",),
                clock=lambda:NOW,grib_fetch=grib)
    asyncio.run(run())
    data=read_hourly_forecasts(tmp_path,db_path=tmp_path/"weatheredge.sqlite3",asof=NOW)
    chicago=next(item for item in data["forecasts"] if item["city"]=="chicago")
    assert chicago["physical_ensemble"]["status"]=="RESEARCH_ONLY_NOT_PROMOTED"
    assert chicago["physical_ensemble"]["model_count"]==2
    assert chicago["real_orders_enabled"] is False


def test_one_command_weather_supervisor_reports_physical_data_status(tmp_path):
    from kalshi_edge.weather.runtime import WeatherSupervisor
    db=tmp_path/"weatheredge.sqlite3"
    async def collect(path):
        assert path==db
        return {"mode":"HOURLY_PHYSICAL_RESEARCH","real_orders_enabled":False,
                "cities":{"chicago":{"state":"RESEARCH_READY"}},
                "model_status":{"HRRR":"READY","NBM":"READY"}}
    worker=WeatherSupervisor(db,hourly_physics_collect=collect,
                             ensure_decoder=lambda:{"state":"AVAILABLE"})
    asyncio.run(worker.poll_hourly_physics())
    health=json.loads((db.parent/"collector-status.json").read_text())
    assert health["hourly_physics"]["cities"]["chicago"]["state"]=="RESEARCH_READY"
    assert health["hourly_physics"]["model_status"]["NBM"]=="READY"
    assert health["real_orders_enabled"] is False


def test_calibration_history_survives_noaa_or_stationinfo_outage(tmp_path):
    def api(request):
        if request.url.path.endswith("/calibrations"):
            return httpx.Response(200,json=payload())
        return httpx.Response(503)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as client:
            return await collect_hourly_physics_once(
                tmp_path/"weatheredge.sqlite3",client=client,
                cities=("chicago",),clock=lambda:NOW)
    result=asyncio.run(run())
    assert result["cities"]["chicago"]["state"]=="UNAVAILABLE"
    with sqlite3.connect(tmp_path/"weatheredge.sqlite3") as conn:
        rows=conn.execute("SELECT config_version,config_sha256 "
                "FROM weather_hourly_physics_calibrations").fetchall()
        assert len(rows)==1 and rows[0][0]=="chicago-temperature-v1.1"
        assert len(rows[0][1])==64


def test_obsolete_configuration_is_not_displayed_after_new_effective_version(tmp_path):
    from kalshi_edge.weather.hourly_physics import _init
    conn=sqlite3.connect(":memory:")
    _init(conn)
    old=parse_calibrations(payload(),"chicago")[0]
    newer=copy.deepcopy(old)
    newer["version"]="chicago-temperature-v2.0"
    newer["effective_at_ms"]=int((TARGET-timedelta(minutes=1)).timestamp()*1000)
    newer["published_at_ms"]=int((NOW-timedelta(minutes=1)).timestamp()*1000)
    newer["config_sha256"]="f"*64
    for c in (old,newer):
        conn.execute("""INSERT INTO weather_hourly_physics_calibrations
            (city,config_version,effective_at_ms,published_at_ms,config_json,
            config_sha256,original_response_sha256,received_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            ("chicago",c["version"],c["effective_at_ms"],
             c["published_at_ms"],json.dumps(c),c["config_sha256"],
             "a"*64,NOW.isoformat()))
    for model in ("HRRR","NBM"):
        conn.execute("""INSERT INTO weather_hourly_physics_forecasts
          (city,target_utc,model,model_run_at,received_at,config_version,
           config_sha256,index_f,nwp_field_sha256,sampled_station_kelvin_json,
           coordinate_source) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
          ("chicago",TARGET.isoformat(),model,
           (NOW-timedelta(hours=1)).isoformat(),NOW.isoformat(),
           old["version"],old["config_sha256"],68,"a"*64,"{}",
           "AWC_ICAO_AIRPORT_PROXY_NOT_HF_SENSOR_LOCATION"))
    assert read_hourly_physics(conn,"chicago",TARGET,NOW)["status"]=="NO_PHYSICAL_GUIDANCE"
