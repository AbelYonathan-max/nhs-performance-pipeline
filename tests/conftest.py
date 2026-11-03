from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import synthetic  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def raw_dir(tmp_path_factory) -> Path:
    raw = tmp_path_factory.mktemp("raw")
    synthetic.write_raw(raw, first="2023-04", count=30)
    return raw


@pytest.fixture(scope="session")
def built(raw_dir, tmp_path_factory) -> Path:
    from nhs_pipeline import pipeline

    db = tmp_path_factory.mktemp("db") / "warehouse.db"
    pipeline.build(raw_dir, db, ROOT / "config" / "targets.csv")
    pipeline.run_forecasts(db)
    return db
