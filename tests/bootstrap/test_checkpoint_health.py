"""A read-only checkpoint health report must not alter live trading decisions."""
from pathlib import Path
import json

import pytest

from kalshi_edge.bootstrap.checkpoint_health import (
    summarize_checkpoint_health, read_checkpoint_health,
)
from kalshi_edge.bootstrap.final_forecast import FinalForecastRecord

NS = 1_000_000_000
START = 1_900_000_000 * NS


def forecast(ticker, *, minute=0, delay_ms=100, direction="ABOVE",
             reason=None, bid=.70, ask=.72):
    scheduled = START + minute * 15 * 60 * NS
    return FinalForecastRecord(
        market_ticker=ticker, strike=60000,
        scheduled_ts_ns=scheduled,
        prediction_ts_ns=scheduled + delay_ms * 1_000_000,
        seconds_remaining=240 - delay_ms / 1000,
        forecast=direction, reason=reason,
        kalshi_yes_bid=bid, kalshi_yes_ask=ask,
    )


def test_report_distinguishes_timing_misses_and_healthy_quotes():
    report = summarize_checkpoint_health([
        forecast("GOOD"),
        forecast("LOW_CONFIDENCE", minute=1, direction="SKIP",
                 reason="CONFIDENCE_BELOW_69_PERCENT", bid=.40, ask=.45),
        forecast("MISSED", minute=2, delay_ms=2900, direction="SKIP",
                 reason="MISSED_240S_CHECKPOINT", bid=None, ask=None),
        forecast("UNHEALTHY", minute=3, direction="SKIP",
                 reason="STALE_KALSHI_BOOK", bid=None, ask=None),
    ], window=10)
    assert report["markets_examined"] == 4
    assert report["directional_forecasts"] == 1
    assert report["skipped_markets"] == 3
    assert report["missed_checkpoint"] == 1
    assert report["feed_or_integrity_skips"] == 1
    assert report["low_confidence_skips"] == 1
    assert report["valid_two_sided_quotes"] == 2
    assert report["quotes_missing_or_invalid"] == 2
    assert report["on_time_checkpoints"] == 3
    assert report["skip_reasons"]["MISSED_240S_CHECKPOINT"] == 1
    assert report["scope"] == "RECORDED_FINAL_FORECASTS_ONLY"
    assert report["last_markets"][0]["ticker"] == "UNHEALTHY"
    assert report["last_markets"][1]["ticker"] == "MISSED"


def test_window_selects_latest_markets_by_checkpoint_not_input_order():
    rows = [
        forecast("NEW", minute=3, delay_ms=1900),
        forecast("OLD", minute=1, delay_ms=0),
        forecast("MIDDLE", minute=2, delay_ms=50),
    ]
    report = summarize_checkpoint_health(rows, window=2)
    assert report["markets_examined"] == 2
    assert report["last_markets"][0]["ticker"] == "NEW"
    assert report["last_markets"][1]["ticker"] == "MIDDLE"
    assert report["on_time_checkpoints"] == 2


def test_duplicate_markets_are_excluded_instead_of_double_counted():
    rows = [
        forecast("DUP", delay_ms=100),
        forecast("DUP", delay_ms=1800, direction="SKIP", reason="MISSING_BRTI"),
        forecast("UNIQUE", minute=1),
    ]
    report = summarize_checkpoint_health(rows)
    assert report["duplicate_records_excluded"] == 2
    assert report["markets_examined"] == 1
    assert report["last_markets"][0]["ticker"] == "UNIQUE"


def test_empty_and_invalid_windows():
    report = summarize_checkpoint_health([])
    assert report["markets_examined"] == 0
    assert report["on_time_checkpoints"] == 0
    assert report["quote_coverage"] is None
    for v in (0, -1, True):
        with pytest.raises(ValueError):
            summarize_checkpoint_health([], window=v)


def test_file_reader_rejects_damaged_json_but_preserves_healthy_records(tmp_path):
    root = tmp_path / "bootstrap"
    folder = root / "final_forecasts" / "2030-01-01"
    folder.mkdir(parents=True)
    (folder / "GOOD.json").write_text(
        forecast("GOOD").model_dump_json(), encoding="utf-8")
    (folder / "BAD.json").write_text("{this is not JSON", encoding="utf-8")
    report = read_checkpoint_health(root, window=10)
    assert report["markets_examined"] == 1
    assert report["invalid_files"] == 1
    assert report["valid_two_sided_quotes"] == 1


def test_skip_with_no_quote_is_not_fabricated_or_used_for_model_skill():
    report = summarize_checkpoint_health([
        forecast("MISS", delay_ms=4500, direction="SKIP",
                 reason="MISSED_240S_CHECKPOINT", bid=None, ask=None),
    ])
    assert report["quote_coverage"] == 0
    assert report["missed_checkpoint"] == 1
    assert report["warning"].startswith("Read-only")
