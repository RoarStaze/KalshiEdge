"""Train Now must continue the deployed model, not an unusable research challenger."""
from __future__ import annotations

import json

from kalshi_edge.bootstrap import training_cycle, model_continuation
from kalshi_edge.bootstrap.dashboard_ui import PAGE


def active_pointer(root):
    p=root/"models"/"default.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "model_number":2,"bundle_sha256":"a"*64,
        "path":"models/promoted/"+"a"*64+".json",
        "previous_model_hash":"b"*64,
        "promotion_rule":"kalshi-manual-accuracy-override-v1",
    }))


def test_train_now_continues_active_model_even_when_research_and_baseline_ready(tmp_path,monkeypatch):
    active_pointer(tmp_path)
    controller=training_cycle.TrainingController(tmp_path,project_root=tmp_path)
    monkeypatch.setattr(controller,"_readiness",lambda force=False:{"ready":False})
    from kalshi_edge.bootstrap import forward_learning_480
    monkeypatch.setattr(forward_learning_480,"inspect_forward_training_readiness",
                        lambda root:{"ready":True})
    monkeypatch.setattr(training_cycle,"inspect_baseline_training_readiness",
                        lambda root:{"ready":True})
    monkeypatch.setattr(model_continuation,"inspect_continuation_readiness",
                        lambda root:{"ready":True,"mode":"continue_live","eligible_markets":150})
    monkeypatch.setattr(controller,"_git_sha",lambda:"c"*40)
    calls=[]
    monkeypatch.setattr(controller,"_launch_existing",lambda saved,*,resume:
                        calls.append(dict(saved)) or {"started":True,"mode":saved["mode"]})
    result=controller.start()
    assert result["mode"]=="continue_live"
    assert calls[0]["round_number"]==0


def test_no_fresh_active_cohort_prevents_repeated_research_only_training(tmp_path,monkeypatch):
    active_pointer(tmp_path)
    controller=training_cycle.TrainingController(tmp_path,project_root=tmp_path)
    monkeypatch.setattr(controller,"_readiness",lambda force=False:{"ready":False})
    from kalshi_edge.bootstrap import forward_learning_480
    monkeypatch.setattr(forward_learning_480,"inspect_forward_training_readiness",
                        lambda root:{"ready":True,"eligible_480_markets":634})
    monkeypatch.setattr(training_cycle,"inspect_baseline_training_readiness",
                        lambda root:{"ready":False,"reason":"missing fresh full lockbox"})
    monkeypatch.setattr(model_continuation,"inspect_continuation_readiness",
                        lambda root:{"ready":False,"eligible_markets":8,"required_markets":130,
                                     "reason":"8/130 new active-model markets"})
    result=controller.status()
    assert result["selected_mode"] is None
    assert result["train_available"] is False
    assert result["continuation"]["eligible_markets"]==8
    assert result["train_reason"]=="8/130 new active-model markets"


def test_continued_candidate_is_checkpointed_without_false_failed_training(tmp_path,monkeypatch):
    from kalshi_edge.bootstrap import model_continuation_run
    monkeypatch.setattr(model_continuation,"inspect_continuation_readiness",
                        lambda root:{"ready":True,"eligible_markets":160})
    def fake(root,*,git_sha,progress):
        for stage in ("FITTING","SCORING","COMPARING","PROMOTION_GATE"):
            progress(stage)
        return {
            "status":"TRAINED_NOT_PROMOTED","promoted":False,
            "experiment_sha256":"d"*64,
            "evaluation_report":str(root / "reports" / ("evaluation-"+"d"*64+".json")),
            "reason_codes":["RETRAIN_480_COVERAGE_NOT_INCREASED"],
            "gate_confidence":.63,
            "candidate_gate":{"qualified":20},"incumbent_gate":{"qualified":21},
        }
    monkeypatch.setattr(model_continuation_run,"run_continuation",fake)
    assert training_cycle.run_continuation_worker(tmp_path,git_sha="c"*40)==0
    state=training_cycle._run_state(tmp_path)
    assert state["stage"]=="COMPLETE"
    assert state["decision"]=="TRAINED_NOT_PROMOTED"
    assert state["promoted"] is False
    assert state["progress_percent"]==100
    assert state["results"]["gate_confidence"]==.63
    assert "checkpoint" in " ".join(state["reasons"]).lower()


def test_train_page_tells_truth_about_continuation_not_full_retrain():
    assert 'info.continuation' in PAGE
    assert 'mode==="continue_live"' in PAGE
    assert 'Continue Training' in PAGE
    assert 'run.decision==="TRAINED_NOT_PROMOTED"' in PAGE
    assert "No full retrain" in PAGE
    assert "63% confidence" in PAGE
