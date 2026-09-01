from __future__ import annotations

from pathlib import Path


def test_research_lock_is_closed_for_scientific_stack_and_runtime_stays_clean() -> None:
    research_lock = Path("requirements.research.lock").read_text(encoding="utf-8")
    runtime_lock = Path("requirements.runtime.lock").read_text(encoding="utf-8")

    expected_research_pins = {
        "duckdb==1.5.5",
        "joblib==1.5.3",
        "narwhals==2.25.0",
        "numpy==2.5.2",
        "pyarrow==21.0.0",
        "scikit-learn==1.9.0",
        "scipy==1.18.1",
        "threadpoolctl==3.6.0",
        "xgboost-cpu==3.4.1",
    }
    installed_lines = {
        line.strip()
        for line in research_lock.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert installed_lines == expected_research_pins
    for package in ("numpy", "scikit-learn", "scipy", "xgboost", "duckdb", "pyarrow"):
        assert package not in runtime_lock.lower()
