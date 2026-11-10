"""Unified Train Now must actually start the eligible model worker.

Offline fixture tests; no live Kalshi API, order execution or model promotion.
"""
from __future__ import annotations

from types import SimpleNamespace

from kalshi_edge.bootstrap import training_cycle, forward_learning_480
from kalshi_edge.bootstrap.dashboard_ui import PAGE


def test_one_training_button_forward_when_legacy_40_percent_and_forward_ready():
    chosen=training_cycle._select_training_mode(
        legacy={"ready":False,"progress_percent":40,"reason":"0/12 eligible mistakes"},
        forward={"ready":True,"eligible_480_markets":303,"reason":"ready"},
    )
    assert chosen=="forward_480"


def test_legacy_still_uses_actual_hash_sealed_model_promotion_when_ready():
    chosen=training_cycle._select_training_mode(
        legacy={"ready":True,"mode":"mistake"},
        forward={"ready":True},
    )
    assert chosen=="mistake"


def test_no_data_cannot_start_a_fake_training_run():
    assert training_cycle._select_training_mode(
        legacy={"ready":False},forward={"ready":False},
    ) is None


def test_forward_worker_persists_real_stages_and_validation_failure(tmp_path,monkeypatch):
    rows=[object()]
    monkeypatch.setattr(forward_learning_480,"inspect_forward_training_readiness",
                        lambda root:{"ready":True,"reason":"ready"})
    calls=[]
    def fake_eval(root, *, progress=None):
        assert progress is not None
        for stage in ("SPLITTING","FITTING","SCORING","COMPARING"):
            progress(stage)
            calls.append(stage)
        return {
            "status":"NO_VALIDATED_JOINT_GAIN",
            "training_markets":212,
            "validation_markets":91,
            "matched_validation_markets":91,
            "candidate_69_gate":{"qualified":15,"correct":15,
                "coverage":15/91,"qualified_accuracy":1.0},
            "incumbent_69_gate":{"qualified":43,"correct":40,
                "coverage":43/91,"qualified_accuracy":40/43},
            "candidate_probability_metrics":{"brier":0.2,"log_loss":0.5},
            "incumbent_probability_metrics":{"brier":0.1,"log_loss":0.4},
        }
    monkeypatch.setattr(forward_learning_480,"evaluate_verified_forward_candidate",fake_eval)
    status=training_cycle.run_forward_training_worker(tmp_path)
    assert status==0
    persisted=training_cycle._run_state(tmp_path)
    assert persisted["stage"]=="COMPLETE"
    assert persisted["progress_percent"]==100
    assert persisted["decision"]=="VALIDATION_FAILED"
    assert persisted["promoted"] is False
    assert "coverage" in " ".join(persisted["reasons"]).lower()
    assert persisted["results"]["candidate_69_gate"]["qualified"]==15
    assert calls==["SPLITTING","FITTING","SCORING","COMPARING"]


def test_favorable_research_validation_is_never_misreported_as_promoted(tmp_path,monkeypatch):
    monkeypatch.setattr(forward_learning_480,"inspect_forward_training_readiness",
                        lambda root:{"ready":True})
    monkeypatch.setattr(forward_learning_480,"evaluate_verified_forward_candidate",
                        lambda root,*,progress=None:{"status":"VALIDATION_JOINT_GAIN",
                            "candidate_69_gate":{"qualified":65,"qualified_accuracy":0.95},
                            "incumbent_69_gate":{"qualified":42,"qualified_accuracy":0.92}})
    assert training_cycle.run_forward_training_worker(tmp_path)==0
    state=training_cycle._run_state(tmp_path)
    assert state["decision"]=="NOT_DEPLOYABLE"
    assert state["promoted"] is False
    assert state["stage"]=="COMPLETE"
    assert "prospective" in " ".join(state["reasons"]).lower()


def test_forward_worker_fails_with_diagnostic_not_progress_100(tmp_path,monkeypatch):
    monkeypatch.setattr(forward_learning_480,"inspect_forward_training_readiness",
                        lambda root:{"ready":True})
    def fail(root,*,progress=None):
        raise ValueError("Causal checkpoint feature hash mismatch")
    monkeypatch.setattr(forward_learning_480,"evaluate_verified_forward_candidate",fail)
    assert training_cycle.run_forward_training_worker(tmp_path)==2
    state=training_cycle._run_state(tmp_path)
    assert state["stage"]=="FAILED"
    assert state["progress_percent"]<100
    assert "hash mismatch" in state["error"]


def test_train_page_has_exactly_one_primary_train_action_and_live_timeline():
    assert PAGE.count('id="train-now"')==1
    assert 'id="forward-480-evaluate"' not in PAGE
    assert 'id="training-worker-progress"' in PAGE
    assert 'id="training-stage-list"' in PAGE
    assert 'id="training-comparison"' in PAGE
    assert 'id="training-outcome"' in PAGE
    assert "info.forward_480" in PAGE
    assert "forward-480-evaluate" not in PAGE
    assert "run.progress_percent" in PAGE
    assert "run.reasons" in PAGE


def test_old_sync_forward_evaluate_endpoint_is_deprecated(tmp_path):
    import httpx
    import threading
    from http.server import ThreadingHTTPServer
    from kalshi_edge.bootstrap.final_history import _make_handler
    server=ThreadingHTTPServer(("127.0.0.1",0),_make_handler(tmp_path))
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        with httpx.Client(timeout=5.0) as client:
            base=f"http://127.0.0.1:{server.server_port}"
            resp=client.post(base+"/api/training/forward480/evaluate",json={},
                headers={"Origin":base,"X-KalshiEdge-Action":"train"})
            assert resp.status_code==404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_forward_worker_interruption_is_detected_after_dashboard_restart(tmp_path,monkeypatch):
    training_cycle._write_worker_state(
        tmp_path,stage="FITTING",mode="forward_480",round_number=0,
    )
    controller=training_cycle.TrainingController(tmp_path,project_root=tmp_path)
    monkeypatch.setattr(controller,"_readiness",lambda force=False:{"ready":False})
    monkeypatch.setattr(training_cycle, "_run_state",
                        lambda root: {"stage":"FITTING", "mode":"forward_480",
                                      "progress_percent":40, "round_number":0})
    status=controller.status()
    assert status["running"] is False
    assert status["run"]["stage"]=="INTERRUPTED"
    assert "restarted" in status["run"]["error"].lower()


def test_one_train_now_launches_forward_worker_when_legacy_blocked(tmp_path,monkeypatch):
    from types import SimpleNamespace
    controller=training_cycle.TrainingController(tmp_path,project_root=tmp_path)
    monkeypatch.setattr(controller,"_readiness",lambda force=False:{
        "ready":False,"mode":"mistake","reason":"0/12 mistake cases",
        "progress_percent":40,
    })
    monkeypatch.setattr(forward_learning_480,"inspect_forward_training_readiness",
                        lambda root:{"ready":True,"reason":"303/120 verified"})
    monkeypatch.setattr(training_cycle.subprocess,"run",
                        lambda *args,**kwargs:SimpleNamespace(stdout="a"*40))
    launched=[]
    class FakeProcess:
        pid=998
        def poll(self):return None
        def terminate(self):pass
        def wait(self,timeout=None):return 0
    def fake_popen(args,**kwargs):
        launched.append(args)
        return FakeProcess()
    monkeypatch.setattr(training_cycle.subprocess,"Popen",fake_popen)
    result=controller.start()
    assert result["started"] is True
    assert result["mode"]=="forward_480"
    assert launched and launched[0][launched[0].index("--mode")+1]=="forward_480"
    assert training_cycle._run_state(tmp_path)["stage"]=="STARTING"
    controller.stop()
