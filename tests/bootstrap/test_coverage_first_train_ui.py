"""New training UI must label saved 63% lockbox gates accurately."""
from __future__ import annotations
import json

from kalshi_edge.bootstrap import training_cycle
from kalshi_edge.bootstrap.dashboard_ui import PAGE


def test_fresh_63percent_training_results_are_not_misrepresented_as_69(tmp_path):
    path=tmp_path/"evaluation-test.json"
    path.write_text(json.dumps({"report":{
        "requires_retrain_480_gate":True,
        "metrics":{"retrain_480_candidate":{},"retrain_480_incumbent":{}},
        "decision_metrics":{
            "retrain_480_candidate":{
                "total_markets":1222,"qualified_markets":502,
                "qualified_correct":450,"minimum_confidence":.63
            },
            "retrain_480_incumbent":{
                "total_markets":1222,"qualified_markets":489,
                "qualified_correct":440,"minimum_confidence":.63
            }
        }
    }}))
    r=training_cycle._verified_lockbox_comparison(path)
    assert r["gate_confidence"]==.63
    assert r["candidate_gate"]["qualified"]==502
    assert r["incumbent_gate"]["qualified"]==489
    assert "candidate_69_gate" not in r
    reasons=training_cycle._lockbox_rejection_reasons(("RETRAIN_480_COVERAGE_NOT_INCREASED",),r)
    assert any("63%" in x and "coverage" in x.lower() for x in reasons)
    assert "r.candidate_gate" in PAGE
    assert "r.gate_confidence" in PAGE


def test_old_69percent_training_report_remains_readable(tmp_path):
    path=tmp_path/"evaluation-old.json"
    path.write_text(json.dumps({"report":{
        "requires_retrain_480_gate":True,"metrics":{},"decision_metrics":{
            "retrain_480_candidate":{
                "total_markets":100,"qualified_markets":35,"qualified_correct":30,
                "minimum_confidence":.69
            },
            "retrain_480_incumbent":{
                "total_markets":100,"qualified_markets":31,"qualified_correct":28,
                "minimum_confidence":.69
            }
        }
    }}))
    result=training_cycle._verified_lockbox_comparison(path)
    assert result["candidate_69_gate"]["qualified"]==35
    assert result["gate_confidence"]==.69
    assert result["candidate_gate"]["qualified"]==35


def test_mismatched_gate_threshold_rejected_from_training_display(tmp_path):
    path=tmp_path/"evaluation-corrupted.json"
    path.write_text(json.dumps({"report":{
        "requires_retrain_480_gate":True,"metrics":{},
        "decision_metrics":{
            "retrain_480_candidate":{"total_markets":100,"qualified_markets":35,"qualified_correct":30,"minimum_confidence":.63},
            "retrain_480_incumbent":{"total_markets":100,"qualified_markets":35,"qualified_correct":30,"minimum_confidence":.69}
        },
    }}))
    assert training_cycle._verified_lockbox_comparison(path)=={}
