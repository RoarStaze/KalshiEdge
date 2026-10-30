from __future__ import annotations

"""Strict, backup-first, atomic addition of research-only V6.1 probability field.

Never alters the promoted model, V5/V6 probability, or final forecast gate.
Requires the user's previously integrated causal-risk feature and V6 shadow.
"""

from datetime import datetime, timezone
from pathlib import Path
import os
import shutil

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / 'src/kalshi_edge/bootstrap/live.py'
FEATURE = ROOT / 'src/kalshi_edge/bootstrap/v61_shadow_policy.py'
TEST = ROOT / 'tests/bootstrap/test_v61_shadow_policy_live.py'


def main() -> None:
    if not LIVE.is_file() or not FEATURE.is_file() or not TEST.is_file():
        raise RuntimeError('Required files missing; source not modified')
    original = LIVE.read_bytes()
    s = original.decode('utf-8')
    if 'v61_shadow_probability:' in s or 'v61_shadow_policy:' in s:
        raise RuntimeError('Policy integration already present; refusing duplicate patch')
    if 'brti_shadow_risk_ratio:' not in s or 'shadow_risk = (' not in s:
        raise RuntimeError('Causal BRTI risk feature not integrated; refusing patch')
    if 'brti_shadow_probability:' not in s or 'brti_shadow = (' not in s:
        raise RuntimeError('V6 shadow not integrated; refusing patch')

    nl = '\r\n' if '\r\n' in s else '\n'
    def lines(*parts):
        return nl.join(parts) + nl
    def swap(old, new, label):
        nonlocal s
        count = s.count(old)
        if count != 1:
            raise RuntimeError(f'Unsafe patch at {label}: expected one anchor, got {count}; nothing modified')
        s = s.replace(old, new, 1)

    swap(lines('from .v6_risk import compute_brti_strike_risk'),
         lines('from .v6_risk import compute_brti_strike_risk',
               'from .v61_shadow_policy import POLICY_ID, risk_adjusted_shadow_probability'),
         'import')
    swap(lines('    brti_shadow_risk_observation_count: int | None = Field(default=None, ge=0)'),
         lines('    brti_shadow_risk_observation_count: int | None = Field(default=None, ge=0)',
               '    v61_shadow_probability: float | None = Field(default=None, ge=0.0, le=1.0)',
               '    v61_shadow_policy: str | None = None'),
         'record fields')
    # Only touch the OK prediction path. Final forecast uses the original p_yes.
    anchor = lines('        return LivePredictionRecord(',
                   '            prediction_ts_ns=now_ns,',
                   '            market_ticker=market.ticker,',
                   '            status="OK",')
    addition = lines(
        '        # Frozen, research-only confidence shrinker. Not a fitted calibrator.',
        '        # V5 p_yes, V6 shadow, and FinalForecastGate remain unchanged.',
        '        v61_shadow_p = risk_adjusted_shadow_probability(',
        '            None if brti_shadow is None else brti_shadow.probability_above,',
        '            None if shadow_risk is None else shadow_risk.adjusted_distance,',
        '        )',
        '',
    )
    swap(anchor, addition + anchor, 'OK prediction')
    swap(lines('            brti_shadow_risk_observation_count=None if shadow_risk is None else shadow_risk.observation_count,'),
         lines('            brti_shadow_risk_observation_count=None if shadow_risk is None else shadow_risk.observation_count,',
               '            v61_shadow_probability=v61_shadow_p,',
               '            v61_shadow_policy=POLICY_ID if v61_shadow_p is not None else None,'),
         'output values')
    compile(s, str(LIVE), 'exec')
    backup_dir = ROOT / 'data/bootstrap/research/v61_source_backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = backup_dir / f'live.py.before_v61_policy_{timestamp}.bak'
    shutil.copy2(LIVE, backup)
    temporary = LIVE.with_name(f'live.py.v61_policy_{os.getpid()}.tmp')
    try:
        temporary.write_bytes(s.encode('utf-8'))
        temporary.replace(LIVE)
    finally:
        temporary.unlink(missing_ok=True)
    print(f'V6.1 research shadow policy integrated. Backup: {backup}')
    print('No production model, final forecast gate, historical outcomes, or lockbox modified.')


if __name__ == '__main__':
    main()
