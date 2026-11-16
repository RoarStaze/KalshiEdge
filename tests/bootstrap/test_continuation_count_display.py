"""Regression: continuation results use the GateSnapshot count field names.

The Train page must display authentic qualifying counts for completed
continuation runs, and must not manufacture 0/0 for absent count fields.
No training or exchange access is performed.
"""
from kalshi_edge.bootstrap.dashboard_ui import PAGE


def test_training_comparison_reads_native_continuation_snapshot_counts():
    # Continuation persists GateSnapshot.model_dump(mode="json") with these names;
    # completed full-retrain reports may use the legacy, compact names.
    assert "data.qualified??data.qualified_markets" in PAGE
    assert "data.correct??data.qualified_correct" in PAGE


def test_training_comparison_missing_counts_never_fabricate_zero_forecasts():
    assert "Counts unavailable" in PAGE
    assert "String(data.correct||0)" not in PAGE
    assert "String(data.qualified||0)" not in PAGE
