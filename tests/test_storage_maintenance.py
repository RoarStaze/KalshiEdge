from pathlib import Path
import json
import os
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from kalshi_edge.storage_maintenance import scan_storage, clean_caches, main


def fixture_root(tmp_path: Path) -> Path:
    root = tmp_path / 'KalshiEdge'
    (root / 'data' / 'bootstrap' / 'raw' / 'binance' / 'archive').mkdir(parents=True)
    (root / 'data' / 'bootstrap' / 'derived').mkdir(parents=True)
    (root / 'artifacts' / 'promoted').mkdir(parents=True)
    (root / '.venv' / 'Lib' / 'site-packages' / '__pycache__').mkdir(parents=True)
    (root / 'src' / 'kalshi_edge' / '__pycache__').mkdir(parents=True)
    (root / 'data' / 'bootstrap' / 'raw' / 'binance' / 'archive' / 'btc.zip').write_bytes(b'x' * 17)
    (root / 'data' / 'bootstrap' / 'derived' / 'features.parquet').write_bytes(b'y' * 31)
    (root / 'artifacts' / 'promoted' / 'model.pkl').write_bytes(b'z' * 23)
    (root / '.venv' / 'Lib' / 'site-packages' / '__pycache__' / 'lib.pyc').write_bytes(b'a' * 7)
    (root / 'src' / 'kalshi_edge' / '__pycache__' / 'code.pyc').write_bytes(b'b' * 5)
    (root / '.env').write_text('PRIVATE=KEY')
    return root


def test_scan_counts_and_classifies_without_mutation(tmp_path):
    root = fixture_root(tmp_path)
    data = scan_storage(root, top=2)
    assert data['total_bytes'] == 17 + 31 + 23 + 7 + 5 + len('PRIVATE=KEY')
    assert data['first_level_bytes']['data'] == 48
    assert data['first_level_bytes']['artifacts'] == 23
    assert data['deep_folder_bytes']['data/bootstrap/raw/binance/archive'] == 17
    assert data['deep_folder_bytes']['data/bootstrap/derived'] == 31
    assert data['cache_bytes'] == 12
    assert data['files'] == 6
    assert len(data['largest_files']) == 2
    assert (root / 'data' / 'bootstrap' / 'derived' / 'features.parquet').exists()


def test_clean_preview_then_apply_preserves_all_durable_files(tmp_path):
    root = fixture_root(tmp_path)
    preview = clean_caches(root, apply=False)
    assert preview['deleted_bytes'] == 0
    assert preview['candidate_bytes'] == 12
    assert (root / 'src' / 'kalshi_edge' / '__pycache__').exists()
    result = clean_caches(root, apply=True)
    assert result['deleted_bytes'] == 12
    assert not (root / 'src' / 'kalshi_edge' / '__pycache__').exists()
    assert not (root / '.venv' / 'Lib' / 'site-packages' / '__pycache__').exists()
    assert (root / '.env').read_text() == 'PRIVATE=KEY'
    assert (root / 'data' / 'bootstrap' / 'raw' / 'binance' / 'archive' / 'btc.zip').exists()
    assert (root / 'data' / 'bootstrap' / 'derived' / 'features.parquet').exists()
    assert (root / 'artifacts' / 'promoted' / 'model.pkl').exists()


def test_symlinked_external_directory_not_scanned_or_deleted(tmp_path):
    root = fixture_root(tmp_path)
    external = tmp_path / 'elsewhere'
    (external / '__pycache__').mkdir(parents=True)
    (external / '__pycache__' / 'keep.pyc').write_bytes(b'q' * 400)
    try:
        (root / 'link').symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip('Windows symlink creation not available')
    report = scan_storage(root)
    assert report['cache_bytes'] == 12
    clean_caches(root, apply=True)
    assert (external / '__pycache__' / 'keep.pyc').exists()


def test_refuse_project_root_symlink(tmp_path):
    root = fixture_root(tmp_path)
    link = tmp_path / 'link'
    try:
        link.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip('Windows symlink creation not available')
    with pytest.raises(ValueError, match='symbolic link'):
        scan_storage(link)


def test_json_cli_and_no_default_deletion(tmp_path, capsys):
    root = fixture_root(tmp_path)
    assert main(['scan', '--root', str(root), '--json', '--top', '3']) == 0
    data = json.loads(capsys.readouterr().out)
    assert data['total_bytes'] == 94
    assert data['root'] == str(root.resolve())
    assert main(['clean', '--root', str(root), '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['deleted_bytes'] == 0
    assert (root / 'src' / 'kalshi_edge' / '__pycache__').exists()


def test_nonexistent_project_rejected(tmp_path):
    with pytest.raises(ValueError, match='not a directory'):
        scan_storage(tmp_path / 'nope')


def test_does_not_delete_other_reproducibility_files(tmp_path):
    root = fixture_root(tmp_path)
    (root / '.git' / 'objects' / '__pycache__').mkdir(parents=True)
    (root / '.git' / 'objects' / '__pycache__' / 'keep.pyc').write_bytes(b'g' * 6)
    (root / 'data' / 'bootstrap' / 'training' / 'checkpoints').mkdir(parents=True)
    (root / 'data' / 'bootstrap' / 'training' / 'checkpoints' / 'current.tmp').write_bytes(b't' * 9)
    result = clean_caches(root, apply=True)
    assert result['deleted_bytes'] == 12
    assert (root / '.git' / 'objects' / '__pycache__' / 'keep.pyc').exists()
    assert (root / 'data' / 'bootstrap' / 'training' / 'checkpoints' / 'current.tmp').exists()


def test_apply_is_only_valid_for_clean(tmp_path, capsys):
    root = fixture_root(tmp_path)
    with pytest.raises(SystemExit) as error:
        main(['scan', '--apply', '--root', str(root)])
    assert error.value.code == 2
    assert (root / 'src' / 'kalshi_edge' / '__pycache__').exists()
