"""Gold historical research CLI is explicit, safe, and never auto-trades."""
from pathlib import Path
import json

import pytest

from kalshi_edge.gold.audit import main


def test_cli_builds_historical_research_only_from_explicit_artifacts(tmp_path, monkeypatch, capsys):
    seen=[]
    monkeypatch.setattr("kalshi_edge.gold.historical_research.assemble_research_dataset",
                        lambda inv, pyth, output_root, **kwargs:
                        seen.append((inv, pyth, output_root, kwargs)) or
                        {"candidate_research_rows":2, "dataset_ready":False})
    assert main([
        "--build-historical-research",str(tmp_path/"inventory.json"),
        "--pyth-artifact",str(tmp_path/"pyth.json"),
        "--output-root",str(tmp_path),
    ]) == 0
    assert seen[0][0] == tmp_path/"inventory.json"
    assert seen[0][1] == [tmp_path/"pyth.json"]
    assert json.loads(capsys.readouterr().out)["dataset_ready"] is False


def test_cli_refuses_build_with_no_pyth_and_network_fetch_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTH_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        main(["--build-historical-research",str(tmp_path/"inventory.json")])
    with pytest.raises(SystemExit):
        main(["--fetch-pyth-history","--pyth-from-ts","1790812800",
              "--pyth-to-ts","1790812860"])


def test_cli_compares_only_non_deployable_artifact(tmp_path, monkeypatch, capsys):
    seen=[]
    monkeypatch.setattr("kalshi_edge.gold.research_models.compare_research_models",
                        lambda path, **kw: seen.append((path,kw)) or {
                            "deployable":False,"models":{"xgboost":{"brier":.22}},
                        })
    assert main(["--compare-historical-research",str(tmp_path/"dataset.json"),
                 "--min-train","10","--min-test","4"]) == 0
    report=json.loads(capsys.readouterr().out)
    assert report["deployable"] is False
    assert seen[0][1] == {"min_train":10,"min_test":4}
