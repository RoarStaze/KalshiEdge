"""Model #1, #2... labels are tied to promoted model pointer, never to failed fits."""
import json

from kalshi_edge.bootstrap.model_identity import (
    current_model_identity, next_promoted_model_number,
)


def pointer(root,sha,**more):
    p=root/"models"/"default.json"
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps({"bundle_sha256":sha,
            "path":f"models/promoted/{sha}.json",**more}))
    return p


def test_first_existing_current_model_is_number_one(tmp_path):
    a="a"*64
    pointer(tmp_path,a)
    identity=current_model_identity(tmp_path)
    assert identity["model_number"]==1
    assert identity["promoted_hash"]==a
    assert identity["number_source"]=="legacy_default"
    assert next_promoted_model_number(tmp_path, "b"*64)==2


def test_successive_promotions_increment_once_even_with_restarts(tmp_path):
    a="a"*64;b="b"*64;c="c"*64
    pointer(tmp_path,a)
    assert next_promoted_model_number(tmp_path,b)==2
    pointer(tmp_path,b,model_number=2,previous_model_hash=a)
    identity=current_model_identity(tmp_path)
    assert identity["model_number"]==2
    assert identity["previous_model_number"]==1
    assert identity["previous_model_hash"]==a
    assert next_promoted_model_number(tmp_path,b)==2
    assert next_promoted_model_number(tmp_path,c)==3


def test_invalid_active_pointer_or_number_never_fabricates_valid_version(tmp_path):
    assert current_model_identity(tmp_path)["model_number"] is None
    pointer(tmp_path,"invalid",model_number=50)
    assert current_model_identity(tmp_path)["model_number"] is None
    pointer(tmp_path,"a"*64,model_number=-20)
    assert current_model_identity(tmp_path)["model_number"] is None


def test_model_count_in_UI_uses_actual_live_hash_not_only_installed_pointer():
    from kalshi_edge.bootstrap.dashboard_ui import PAGE
    assert 'id="active-model-number"' in PAGE
    assert 'id="installed-model-number"' in PAGE
    assert "current_model" in PAGE
    assert "model_hash" in PAGE
    assert "Restart" in PAGE
