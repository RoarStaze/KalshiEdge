"""Historical training export respects point-in-time NOAA receipts and source evidence."""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timedelta, timezone
import pytest

from kalshi_edge.weather.dataset import export_labeled_days


def test_export_uses_only_available_weather_inputs_and_documents_publisher(tmp_path):
    db=tmp_path/"weather.sqlite3"
    T=datetime(2026,11,19,18,tzinfo=timezone.utc)
    run=T-timedelta(hours=2)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE samples(id INTEGER PRIMARY KEY,station TEXT,source TEXT,event_time TEXT,received_at TEXT,payload TEXT)")
        for source in ("HRRR","NBM"):
            for h in (2,3,4,5,6):
                conn.execute("INSERT INTO samples(station,source,event_time,received_at,payload) VALUES(?,?,?,?,?)",
                    ("KMDW",source,run.isoformat(),(run+timedelta(minutes=15)).isoformat(),
                     json.dumps({"model_run_at":run.isoformat(),
                                 "forecast_valid_at":(run+timedelta(hours=h)).isoformat(),
                                 "temperature_k":286.15+h})))
        conn.execute("INSERT INTO samples(station,source,event_time,received_at,payload) VALUES(?,?,?,?,?)",
            ("KMDW","METAR",(T-timedelta(minutes=20)).isoformat(),
             (T-timedelta(minutes=15)).isoformat(),json.dumps({"temp_f":61.0})))
    manifest=tmp_path/"manifest.jsonl"
    manifest.write_text(json.dumps({
        "station":"KMDW","settlement_day":"2026-11-19",
        "asof":T.isoformat(),"actual_max_f":65.0,
        "source_uri":"https://weather.com/kalshi","source_sha256":"a"*64,
        "source_market_ticker":"KXHIGHCHI-26NOV19-T65",
    })+"\n")
    out=tmp_path/"rows.jsonl"
    status=export_labeled_days(db,manifest,out,"KMDW")
    assert status["exported"]==1
    assert status["independently_certified"] is False
    values=json.loads(out.read_text().splitlines()[0])
    assert values["station"]=="KMDW"
    assert values["observed_max_f"]==61.0
    assert values["actual_max_f"]==65.0
    assert values["label_source"].startswith("https://weather.com/kalshi#")
    assert values["asof"]==T.isoformat()


def test_export_rejects_unprovenanced_or_unsafe_labels(tmp_path):
    manifest=tmp_path/"manifest.jsonl"
    manifest.write_text(json.dumps({
        "station":"KMDW","settlement_day":"2026-11-19",
        "asof":"2026-11-19T18:00:00+00:00","actual_max_f":62.0,
        "source_uri":"https://example.com/fake","source_sha256":"x"*64,
    })+"\n")
    with pytest.raises(ValueError):
        export_labeled_days(tmp_path/"db.sqlite3",manifest,tmp_path/"out.jsonl","KMDW")
    assert not (tmp_path/"out.jsonl").exists()
