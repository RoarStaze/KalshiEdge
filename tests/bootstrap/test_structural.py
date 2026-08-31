from __future__ import annotations

import importlib.util


def test_structural_module_exists() -> None:
    assert importlib.util.find_spec("kalshi_edge.bootstrap.structural") is not None
