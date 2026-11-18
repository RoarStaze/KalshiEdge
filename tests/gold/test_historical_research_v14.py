"""Historical gold research: labelled as experimental, never settlement-verified."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math

import pytest

from kalshi_edge.gold.oracle_history import import_pyth_history
from kalshi_edge.gold.historical_research import (
    GoldHistoricalResearchError, assemble_research_dataset,
    inspect_research_dataset, extract_research_rows,
)
from kalshi_edge.gold.research_models import compare_research_models
from kalshi_edge.gold.macro_context import asof_macro_features

BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)
NS = 1_000_000_000


def sources(tmp_path):
    start = int(BASE.timestamp())
    # Four full UTC days of Pyth-format, locally imported OHLC. Those source
    # bytes are NOT independently verified oracle settlement evidence.
    bars = [start + 60 * i for i in range(4 * 1440)]
    closes = [2000 + 0.006 * i + .25 * math.sin(i / 7) for i in range(len(bars))]
    payload = {
        "s": "ok", "t": bars,
        "o": [round(v, 6) for v in closes],
        "h": [round(v + .12, 6) for v in closes],
        "l": [round(v - .12, 6) for v in closes],
        "c": [round(v, 6) for v in closes],
    }
    file = tmp_path / "pyth.json"
    file.write_text(json.dumps(payload), encoding="utf-8")
    evidence = import_pyth_history(
        file, tmp_path / "gold", channel="real_time",
        start_ts=bars[0], end_ts=bars[-1],
    )
    markets = []
    for day in range(4):
        for k in range(14):
            close = BASE + timedelta(days=day, hours=2, minutes=15 * k)
            markets.append({
                "ticker": f"KXGOLD15M-D{day}X{k}",
                "close_time": close.isoformat().replace("+00:00", "Z"),
                "status": "finalized", "result": "yes" if (day+k)%2 else "no",
                "rules_primary": "Research fixture only; rule mapping UNVERIFIED.",
            })
    pages = {"historical": [{
        "params": {"series_ticker": "KXGOLD15M"},
        "response": {"markets": markets, "cursor": ""},
        "received_at_ns": int((BASE + timedelta(days=7)).timestamp()*NS),
    }], "live": []}
    inventory = {
        "schema_version": 1, "series": {"series": {
            "ticker": "KXGOLD15M", "settlement_sources": [{
                "url": "https://app.pyth.com/explore/Metal.Index.1OZGOLD%2FUSD",
            }],
        }},
        "report": {"series_ticker": "KXGOLD15M",
                   "qualification_gate": "0.69",
                   "checkpoint_seconds": 480, "ready_for_training": False},
        "pages": pages,
    }
    raw = (json.dumps(inventory, sort_keys=True) + "\n").encode()
    name = f"123-{sha256(raw).hexdigest()[:16]}.json"
    path = tmp_path / name
    path.write_bytes(raw)
    return path, evidence["artifact_path"], payload


def test_reconstruction_never_promotes_reported_labels_to_verified(tmp_path):
    inventory, pyth, _ = sources(tmp_path)
    result = assemble_research_dataset(inventory, [pyth], tmp_path / "gold")
    assert result["candidate_research_rows"] == 56
    assert result["eligible_training_rows"] == 0
    assert result["dataset_ready"] is False
    assert result["settlement_oracle_verified"] is False
    assert result["label_status"] == "KALSHI_REPORTED_UNVERIFIED"
    doc = inspect_research_dataset(result["artifact_path"])
    assert doc["report"]["dataset_ready"] is False
    rows = extract_research_rows(doc)
    assert len(rows) == 56
    assert len({r.ticker for r in rows}) == 56
    assert all(r.feature_end_ns <= r.decision_ns - 60*NS for r in rows)
    assert all(r.label_available_proxy_ns >= r.close_ns + 86400*NS for r in rows)
    assert all(r.source == "UNVERIFIED_PYTH_HISTORICAL_OHLC" for r in rows)
    assert all(len(r.lag_returns_bps) >= 10 for r in rows)


def test_source_mutation_breaks_integrity_and_research_inspection(tmp_path):
    inventory, pyth, _ = sources(tmp_path)
    result = assemble_research_dataset(inventory, [pyth], tmp_path / "gold")
    path = result["artifact_path"]
    with open(path, "ab") as out:
        out.write(b"\n")
    with pytest.raises(GoldHistoricalResearchError):
        inspect_research_dataset(path)


def test_future_bars_and_broken_history_are_never_used(tmp_path):
    inventory, pyth, payload = sources(tmp_path)
    original = assemble_research_dataset(inventory, [pyth], tmp_path / "gold")
    # Change a bar near a market close, AFTER the 8min decision. This must
    # not perturb that market's 8min research feature snapshot.
    market_close = int((BASE + timedelta(hours=2)).timestamp())
    late_idx = payload["t"].index(market_close - 60)
    payload["c"][late_idx] = 9000
    payload["o"][late_idx] = 9000
    payload["h"][late_idx] = 9000.12
    payload["l"][late_idx] = 8999.88
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(payload))
    revised = import_pyth_history(
        changed, tmp_path / "gold", channel="real_time",
        start_ts=payload["t"][0], end_ts=payload["t"][-1],
    )
    updated = assemble_research_dataset(inventory, [revised["artifact_path"]], tmp_path / "gold")
    one = extract_research_rows(inspect_research_dataset(original["artifact_path"]))[0]
    two = extract_research_rows(inspect_research_dataset(updated["artifact_path"]))[0]
    assert one.ticker == two.ticker
    assert one.features == two.features


def test_ambiguous_final_result_is_excluded_not_relabelled(tmp_path):
    inventory, pyth, _ = sources(tmp_path)
    document = json.loads(inventory.read_text())
    first = document["pages"]["historical"][0]["response"]["markets"][0]
    document["pages"]["historical"][0]["response"]["markets"].append(
        {**first, "result": "yes" if first["result"] == "no" else "no"}
    )
    raw = (json.dumps(document, sort_keys=True)+"\n").encode()
    other = tmp_path/f"124-{sha256(raw).hexdigest()[:16]}.json"
    other.write_bytes(raw)
    report = assemble_research_dataset(other, [pyth], tmp_path / "gold")
    assert report["candidate_research_rows"] == 55
    assert report["excluded"]["CONFLICTING_KALSHI_RESULT"] == 1


def test_purged_comparison_of_three_components_is_research_only(tmp_path):
    pytest.importorskip("xgboost")
    pytest.importorskip("sklearn")
    inventory, pyth, _ = sources(tmp_path)
    dataset = assemble_research_dataset(inventory, [pyth], tmp_path / "gold")
    report = compare_research_models(dataset["artifact_path"], min_train=20, min_test=8)
    assert report["deployable"] is False
    assert report["research_only"] is True
    assert set(report["models"]) >= {"xgboost", "lagged_time_series", "regime_aware"}
    assert report["features_include_future_labels"] is False
    assert report["train_max_label_available_ns"] < report["test_start_ns"]
    assert report["test_count"] >= 8
    assert all(0 <= value["brier"] <= 1 for value in report["models"].values())
    assert report["macro_component"] == "DISABLED_NO_VERIFIED_ASOF_NEWS"


def test_macro_context_fails_closed_on_late_or_ambiguous_publication():
    decision = int(BASE.timestamp()*NS)
    events = [
        {"headline": "Gold demand rises", "published_ns": decision-20*NS,
         "first_observed_ns": decision-10*NS, "score": .8},
        {"headline": "Later news", "published_ns": decision+NS,
         "first_observed_ns": decision+NS, "score": -.9},
        {"headline": "Captured too late", "published_ns": decision-60*NS,
         "first_observed_ns": decision+NS, "score": -.8},
    ]
    result = asof_macro_features(events, decision)
    assert result["count"] == 1
    assert result["sentiment"] == .8
    assert result["available"] is True
    with pytest.raises(ValueError, match="timestamp"):
        asof_macro_features([{"headline":"Missing timestamp","score":.2}], decision)
