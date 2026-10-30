from __future__ import annotations

"""Add read-only causal BRTI risk diagnostics to the existing local V6 shadow.

Intentionally fails closed when the user's locally modified live.py differs from
expected anchors. Creates a source backup; preserves the promoted V5 model and
all immutable final-forecast / lockbox files.
"""

from datetime import datetime, timezone
from pathlib import Path
import os
import shutil

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "src/kalshi_edge/bootstrap/live.py"
FEATURE = ROOT / "src/kalshi_edge/bootstrap/v6_risk.py"
TEST = ROOT / "tests/bootstrap/test_v61_shadow_risk.py"


def replace_once(s: str, old: str, new: str, label: str) -> str:
    n = s.count(old)
    if n != 1:
        raise RuntimeError(f"UNSAFE TO PATCH {label}: expected 1 anchor, found {n}; nothing changed")
    return s.replace(old, new, 1)


def main() -> None:
    if not FEATURE.is_file() or not TEST.is_file():
        raise RuntimeError("Missing V6.1 feature or integration test; nothing changed")
    original = LIVE.read_bytes()
    source = original.decode("utf-8")
    if "brti_shadow_risk_ratio" in source:
        raise RuntimeError("V6.1 shadow fields already found; refusing duplicate modification")
    if "brti_shadow_probability:" not in source or "brti_shadow = (" not in source:
        raise RuntimeError("Expected existing local V6 shadow implementation not found; nothing changed")

    # Preserve the source file's existing line endings.
    nl = "\r\n" if "\r\n" in source else "\n"

    def lines(*parts: str) -> str:
        return nl.join(parts) + nl

    source = replace_once(
        source,
        lines('from .types import FeedQuality, FeatureRow, MarketLabel, PredictionRecord'),
        lines('from .types import FeedQuality, FeatureRow, MarketLabel, PredictionRecord',
              'from .v6_risk import compute_brti_strike_risk'),
        "feature import",
    )

    source = replace_once(
        source,
        lines('    brti_shadow_expected_move_pct: float | None = Field(default=None, ge=0.0)'),
        lines('    brti_shadow_expected_move_pct: float | None = Field(default=None, ge=0.0)',
              '    brti_shadow_risk_ratio: float | None = Field(default=None, ge=0.0)',
              '    brti_shadow_risk_sigma: float | None = Field(default=None, ge=0.0)',
              '    brti_shadow_risk_observed_seconds: float | None = Field(default=None, ge=0.0)',
              '    brti_shadow_risk_observation_count: int | None = Field(default=None, ge=0)'),
        "record fields",
    )

    return_anchor = lines(
        '        return LivePredictionRecord(',
        '            prediction_ts_ns=now_ns,',
        '            market_ticker=market.ticker,',
        '            status="OK",',
    )
    risk_code = lines(
        '        # Research-only diagnostic: never gates, calibrates, or changes V5/V6.',
        '        # The feature filters out observations after now_ns.',
        '        shadow_risk = (',
        '            compute_brti_strike_risk(',
        '                [(item.source_ts_ns, item.value) for item in self.state.brti_observations],',
        '                checkpoint_ts_ns=now_ns,',
        '                brti_value=self.state.latest_brti.value,',
        '                strike=market.strike,',
        '                seconds_remaining=seconds_remaining,',
        '            )',
        '            if seconds_remaining >= 60.0 else None',
        '        )',
        '',
    )
    source = replace_once(source, return_anchor, risk_code + return_anchor, "predict output")

    source = replace_once(
        source,
        lines('            component_disagreement=disagreement,'),
        lines('            component_disagreement=disagreement,',
              '            brti_shadow_risk_ratio=None if shadow_risk is None else shadow_risk.adjusted_distance,',
              '            brti_shadow_risk_sigma=None if shadow_risk is None else shadow_risk.sigma_per_sqrt_second,',
              '            brti_shadow_risk_observed_seconds=None if shadow_risk is None else shadow_risk.observed_seconds,',
              '            brti_shadow_risk_observation_count=None if shadow_risk is None else shadow_risk.observation_count,'),
        "record output values",
    )

    # Fail fast before creating any backup / editing files.
    compile(source, str(LIVE), "exec")

    backup_dir = ROOT / "data/bootstrap/research/v61_source_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_dir / f"live.py.before_v61_{timestamp}.bak"
    shutil.copy2(LIVE, backup_path)

    temporary = LIVE.with_name(f"live.py.v61_{os.getpid()}.tmp")
    try:
        temporary.write_bytes(source.encode("utf-8"))
        temporary.replace(LIVE)
    finally:
        if temporary.exists():
            temporary.unlink()

    print("V6.1 risk diagnostics integrated into local live.py")
    print(f"Original source backup: {backup_path}")
    print("No production model, training artifacts, final forecasts, or lockbox were modified.")


if __name__ == "__main__":
    main()
