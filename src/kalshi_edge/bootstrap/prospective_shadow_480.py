"""Read-only prospective settled-market report for the frozen 480s specialist.

Only official, locally verified settlements and time-locked, hash-validated
future shadow observations are scored. No network, trading, fitting or
model-promotion action is possible from this CLI.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

from .final_forecast import FINAL_CHECKPOINT_SECONDS, FinalForecastRecord
from .final_history import _load_saved_settlement, SettlementIntegrityError
from .frozen_specialist_480 import FILENAME, load_frozen_specialist
from .paired_gate_480 import MIN_CONFIDENCE, summarize_gate_overlap
from .shadow_live_480 import SHADOW_DIR, load_shadow_record

NS = 1_000_000_000
MIN_SETTLED_MARKETS = 80


def _probability_metric(probabilities: list[tuple[int, float]]) -> dict[str, float | int | None]:
    if not probabilities:
        return {"markets": 0, "accuracy": None, "brier": None, "log_loss": None}
    total = len(probabilities)
    correct = sum(int((p >= .5) == bool(label)) for label, p in probabilities)
    brier = sum((p-label)**2 for label,p in probabilities) / total
    eps=1e-15
    loss=-sum(label*math.log(max(eps,min(1-eps,p))) +
              (1-label)*math.log(max(eps,min(1-eps,1-p)))
              for label,p in probabilities) / total
    return {"markets": total, "accuracy": correct/total, "brier": brier, "log_loss": loss}


def _gate_metric(total_settled: int, qualified: int, correct: int,
                 probabilities: list[tuple[int, float]]) -> dict[str, Any]:
    return {
        "settled_markets": total_settled,
        "qualified": qualified,
        "correct": correct,
        "skipped": total_settled-qualified,
        "coverage": qualified/total_settled if total_settled else None,
        "qualified_accuracy": correct/qualified if qualified else None,
        "available_probability_metrics": _probability_metric(probabilities),
    }


def _load_finals(root: Path, frozen_at_ns: int) -> tuple[list[FinalForecastRecord], int]:
    forecasts: list[FinalForecastRecord] = []
    invalid = 0
    found: dict[str, FinalForecastRecord] = {}
    duplicates: set[str] = set()
    for path in sorted((root/"final_forecasts").glob("*/*.json")):
        try:
            final=FinalForecastRecord.model_validate_json(path.read_text(encoding="utf-8"))
            date=datetime.fromtimestamp(
                (final.scheduled_ts_ns+final.checkpoint_seconds*NS)/NS,
                tz=timezone.utc,
            ).date().isoformat()
            if path.name != f"{final.market_ticker}.json" or path.parent.name != date:
                raise ValueError("forecast file path and signed checkpoint disagree")
            if final.market_ticker in found:
                duplicates.add(final.market_ticker)
            else:
                found[final.market_ticker] = final
        except (ValueError, OSError, OverflowError):
            invalid += 1
    for ticker in duplicates:
        del found[ticker]
    invalid += len(duplicates)*2
    for record in found.values():
        if (record.checkpoint_seconds == FINAL_CHECKPOINT_SECONDS
                and record.scheduled_ts_ns > frozen_at_ns):
            forecasts.append(record)
    forecasts.sort(key=lambda x: (x.scheduled_ts_ns, x.market_ticker))
    return forecasts,invalid


def _validated_shadow(root: Path, final: FinalForecastRecord,
                      frozen_sha: str, frozen_at_ns: int):
    day=datetime.fromtimestamp(final.scheduled_ts_ns/NS+FINAL_CHECKPOINT_SECONDS,
                               tz=timezone.utc).date().isoformat()
    path=root/SHADOW_DIR/day/f"{final.market_ticker}.json"
    if not path.exists():
        return None, "MISSING_SHADOW"
    try:
        shadow=load_shadow_record(path)
        if (shadow.market_ticker != final.market_ticker
                or shadow.scheduled_ts_ns != final.scheduled_ts_ns
                or shadow.prediction_ts_ns != final.prediction_ts_ns
                or shadow.market_close_ts_ns != final.scheduled_ts_ns+FINAL_CHECKPOINT_SECONDS*NS
                or shadow.frozen_model_sha256 != frozen_sha
                or shadow.frozen_at_ns != frozen_at_ns
                or shadow.incumbent_model_hash != final.model_hash
                or shadow.incumbent_p_yes != final.p_yes):
            raise ValueError("shadow/incumbent/frozen identities do not match")
        return shadow,None
    except (ValueError, OSError, TypeError):
        return None,"INVALID_SHADOW"


def evaluate_prospective_shadow(
    root: Path, *, minimum_settled: int = MIN_SETTLED_MARKETS,
) -> dict[str, Any]:
    """Score all eligible settled future 480s finals, including shadow skips.

    A market without a saved official outcome is pending. A malformed
    settlement is excluded, never counted as a successful or failed trade.
    An absent or invalid shadow is a counted SKIP in the settled denominator.
    """
    if type(minimum_settled) is not int or minimum_settled < 1:
        raise ValueError("minimum_settled must be a positive integer")
    root = Path(root).resolve()
    frozen=load_frozen_specialist(root/FILENAME)
    eligible, invalid_final = _load_finals(root, frozen["frozen_at_ns"])

    counts=Counter()
    counts["final_480_after_freeze"]=len(eligible)
    counts["invalid_final_records"]=invalid_final
    incumbent_predictions: list[tuple[int,float]]=[]
    specialist_predictions: list[tuple[int,float]]=[]
    incumbent_qualified=incumbent_correct=0
    specialist_qualified=specialist_correct=0
    paired: list[tuple[int,float,float]]=[]
    shadow_skip_reasons=Counter()

    for final in eligible:
        try:
            settlement=_load_saved_settlement(root,final)
        except SettlementIntegrityError:
            counts["invalid_settlements"]+=1
            continue
        if settlement is None:
            counts["pending_settlements"]+=1
            continue
        counts["settled_market_count"]+=1
        label=1 if settlement.actual_side=="ABOVE" else 0
        incumbent_p=final.p_yes
        if incumbent_p is not None:
            incumbent_predictions.append((label,incumbent_p))
        if final.forecast in {"ABOVE","BELOW"}:
            incumbent_qualified+=1
            incumbent_correct+=int((final.forecast=="ABOVE")==bool(label))

        shadow,problem=_validated_shadow(
            root,final,frozen["sha256"],frozen["frozen_at_ns"],
        )
        if problem is not None:
            if problem=="MISSING_SHADOW":
                counts["missing_shadow_records"]+=1
            else:
                counts["invalid_shadow_records"]+=1
            shadow_skip_reasons[problem]+=1
            continue
        if shadow.status=="SKIP":
            counts["shadow_status_skips"]+=1
            shadow_skip_reasons[shadow.reason or "UNSPECIFIED"]+=1
            continue

        assert shadow.p_yes is not None
        p=shadow.p_yes
        specialist_predictions.append((label,p))
        if shadow.gate_qualified:
            specialist_qualified+=1
            specialist_correct+=int((p>=.5)==bool(label))
        if incumbent_p is not None:
            paired.append((label,p,incumbent_p))

    total=counts["settled_market_count"]
    incumbent=_gate_metric(
        total,incumbent_qualified,incumbent_correct,incumbent_predictions,
    )
    specialist=_gate_metric(
        total,specialist_qualified,specialist_correct,specialist_predictions,
    )
    paired_candidate=_probability_metric([(label,p) for label,p,_ in paired])
    paired_incumbent=_probability_metric([(label,p) for label,_,p in paired])
    paired_gate=(summarize_gate_overlap(
        [label for label,_,_ in paired],
        [p for _,p,_ in paired],
        [p for _,_,p in paired],
    ) if paired else None)

    return {
        "report": "KXBTC15M_FROZEN_480S_PROSPECTIVE_SETTLED_V1",
        "status": ("DESCRIPTIVE_ONLY" if total>=minimum_settled
                   else "INSUFFICIENT_SETTLED_MARKETS"),
        "frozen_model_sha256": frozen["sha256"],
        "frozen_at_ns": frozen["frozen_at_ns"],
        "minimum_required_settled_markets":minimum_settled,
        "confidence_gate":MIN_CONFIDENCE,
        "final_480_after_freeze":counts["final_480_after_freeze"],
        "settled_market_count":total,
        "pending_settlements":counts["pending_settlements"],
        "invalid_settlements":counts["invalid_settlements"],
        "invalid_final_records":counts["invalid_final_records"],
        "missing_shadow_records":counts["missing_shadow_records"],
        "invalid_shadow_records":counts["invalid_shadow_records"],
        "shadow_status_skips":counts["shadow_status_skips"],
        "shadow_skip_reasons":dict(shadow_skip_reasons),
        "incumbent":incumbent,
        "specialist":specialist,
        "paired_probability_markets":len(paired),
        "paired_probability_metrics":{
            "incumbent":paired_incumbent,
            "specialist":paired_candidate,
            "brier_improvement":(
                paired_incumbent["brier"]-paired_candidate["brier"]
                if paired else None
            ),
            "log_loss_improvement":(
                paired_incumbent["log_loss"]-paired_candidate["log_loss"]
                if paired else None
            ),
        },
        "paired_gate_cohorts":paired_gate,
        "ready_to_promote":False,
        "independent_prospective_evidence":total>=minimum_settled,
        "note":(
            "Read-only historical report of future-after-freeze time-locked "
            "local observations. Valid official settlements only; no fees, "
            "fills or profit established. Unknown/missing specialist shadow "
            "is counted as SKIP. Coverage uses ACTUAL immutable incumbent "
            "decisions, not hypothetical probability thresholds. Paired gate "
            "cohorts are probability-threshold diagnostics on the smaller "
            "fully matched subset. Minimum sample alone never authorizes "
            "model promotion or proves an after-fee advantage."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    from .config import BootstrapSettings
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=BootstrapSettings().bootstrap_dir)
    parser.add_argument("--minimum-settled",type=int,default=MIN_SETTLED_MARKETS)
    args=parser.parse_args(argv)
    try:
        result=evaluate_prospective_shadow(args.root,
                                          minimum_settled=args.minimum_settled)
    except (OSError,ValueError) as exc:
        result={"status":"EVALUATION_REFUSED","error":str(exc),
                "ready_to_promote":False}
        print(json.dumps(result,indent=2,allow_nan=False))
        return 2
    print(json.dumps(result,indent=2,allow_nan=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
