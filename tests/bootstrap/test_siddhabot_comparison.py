from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kalshi_edge.bootstrap import final_history
from kalshi_edge.bootstrap import siddhabot_comparison as bench


def test_common_gate_is_directional_probability_not_strength():
    values = [0.64, 0.36, 0.63, 0.37, 0.5, None]
    labels = [1, 0, 1, 0, 1, 1]
    scored = bench._score(values, labels)
    assert scored["qualified"] == 2
    assert scored["correct"] == 2
    assert scored["skipped"] == 4
    assert scored["coverage"] == pytest.approx(2 / 6)
    assert scored["accuracy"] == 1


def test_empty_period_is_not_represented_as_zero_percent(tmp_path: Path):
    result = bench.compare(tmp_path)
    assert result["status"] == "NOT_STARTED"
    assert "kalshiedge_common_gate" not in result
    assert "no comparable results exist yet" in bench.format_report(result)


def _final(index: int, probability: float, forecast: str):
    return SimpleNamespace(
        market_ticker=f"KXBTC15M-26NOV160{index}00-00",
        checkpoint_seconds=480,
        scheduled_ts_ns=1_800_000_000_000_000_000 + index * 900 * bench.NS,
        p_yes=probability,
        forecast=forecast,
    )


def _save_siddha(root: Path, final: SimpleNamespace, probability: float,
                 start_offset_s: int = -1, end_offset_s: int = 1):
    close_ns = final.scheduled_ts_ns + 480 * bench.NS
    path = bench._capture_path(root, final.market_ticker, close_ns)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "benchmark_version": bench.BENCHMARK_VERSION,
        "ticker": final.market_ticker,
        "close_ts_ns": close_ns,
        "started_ts_ns": final.scheduled_ts_ns + start_offset_s * bench.NS,
        "finished_ts_ns": final.scheduled_ts_ns + end_offset_s * bench.NS,
        "source_commit": "0" * 40,
        "model_sha256": "a" * 64,
        "p_yes": probability,
    }), encoding="utf-8")
    return path


def test_paired_comparison_uses_same_official_labels_and_denominator(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    finals = [_final(1, 0.90, "ABOVE"),
              _final(2, 0.70, "ABOVE"),
              _final(3, 0.53, "SKIP")]
    start = tmp_path / "benchmarks" / "siddhabot" / "capture-start.json"
    start.parent.mkdir(parents=True)
    start.write_text(json.dumps({
        "started_ts_ns": finals[0].scheduled_ts_ns - bench.NS
    }), encoding="utf-8")
    _save_siddha(tmp_path, finals[0], 0.80)
    _save_siddha(tmp_path, finals[1], 0.20)
    labels = dict(zip((f.market_ticker for f in finals), ("yes", "no", "yes")))
    monkeypatch.setattr(final_history, "_forecasts", lambda root: finals)
    monkeypatch.setattr(
        final_history, "_load_saved_settlement",
        lambda root, final: SimpleNamespace(
            market_close_ts_ns=final.scheduled_ts_ns + 480 * bench.NS,
            result=labels[final.market_ticker],
        ),
    )
    result = bench.compare(tmp_path)
    assert result["status"] == "READY"
    assert result["scored_officially_settled_markets"] == 3
    assert result["paired_probability_markets"] == 2
    assert result["kalshiedge_common_gate"]["qualified"] == 2
    assert result["kalshiedge_common_gate"]["accuracy"] == pytest.approx(.5)
    assert result["siddhabot_common_gate"]["qualified"] == 2
    assert result["siddhabot_common_gate"]["accuracy"] == 1
    assert result["siddhabot_common_gate"]["coverage"] == pytest.approx(2 / 3)
    assert result["siddhabot_capture_status"]["NOT_CAPTURED"] == 1
    assert result["kalshiedge_actual_locked_policy"]["qualified"] == 2
    assert "Accuracy" in bench.format_report(result)
    assert "Coverage" in bench.format_report(result)


def test_rejects_non_checkpoint_and_tampered_capture(tmp_path: Path):
    final = _final(1, 0.7, "ABOVE")
    _save_siddha(tmp_path, final, 0.7, start_offset_s=-6, end_offset_s=-4)
    probability, reason = bench._read_capture(tmp_path, final)
    assert probability is None
    assert reason == "INVALID_CAPTURE"
    path = _save_siddha(tmp_path, final, 0.7)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["ticker"] = "KXBTC15M-OTHER"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert bench._read_capture(tmp_path, final) == (None, "INVALID_CAPTURE")


def test_missing_or_unsettled_forecasts_do_not_invent_labels(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    final = _final(1, .8, "ABOVE")
    marker = tmp_path / "benchmarks" / "siddhabot" / "capture-start.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"started_ts_ns": final.scheduled_ts_ns - 1}), encoding="utf-8")
    monkeypatch.setattr(final_history, "_forecasts", lambda root: [final])
    monkeypatch.setattr(final_history, "_load_saved_settlement", lambda root, final: None)
    result = bench.compare(tmp_path)
    assert result["status"] == "NO_SETTLED_PAIRED_PERIOD_MARKETS"
    assert result["pending_official_settlements"] == 1
    assert result["paired_probability_markets"] == 0
    assert result["kalshiedge_common_gate"]["accuracy"] is None
    assert result["siddhabot_common_gate"]["coverage"] is None
