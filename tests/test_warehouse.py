import pandas as pd
import pytest
from conftest import ROOT

from nhs_pipeline import transform, warehouse


def test_region_codes():
    assert warehouse.region_code("NHS ENGLAND NORTH EAST AND YORKSHIRE") == "NEY"
    assert warehouse.region_code("NHS ENGLAND LONDON") == "LON"
    assert warehouse.region_code(None) is None


def test_sector():
    assert warehouse.sector("RJ1", "GUY'S AND ST THOMAS'") == "NHS trust"
    assert warehouse.sector("NT4", "BMI HEALTHCARE") == "Independent sector"


def test_dim_month_financial_year():
    d = warehouse.dim_month(["2026-03", "2026-04"])
    assert d.financial_year.tolist() == ["2025-26", "2026-27"]
    assert d.fy_month.tolist() == [12, 1]


def test_views(built):
    eng = warehouse.query(built, "SELECT * FROM v_ae_england_month ORDER BY month_key")
    assert len(eng) == 30 and eng.pct_4hr.between(0, 1).all()
    league = warehouse.query(built, "SELECT * FROM v_ae_league_latest")
    assert league.rank_4hr.min() == 1 and league.change_pts.notna().all()
    rtt = warehouse.query(built, "SELECT * FROM v_rtt_england_month")
    assert (rtt.waiting_list > 0).all() and rtt.median_weeks.notna().all()


def test_quality_checks_stop_bad_data(raw_dir, tmp_path):
    import json
    manifest = json.loads((raw_dir / "manifest.json").read_text())
    manifest = [e for e in manifest if e["period"] != "2024-06"]  # a missing month
    ae = transform.build_ae(raw_dir, manifest)
    rtt, eng = transform.build_rtt(raw_dir, manifest)
    with pytest.raises(RuntimeError, match="no missing months"):
        warehouse.load(tmp_path / "w.db", ae, rtt, eng, pd.read_csv(ROOT / "config/targets.csv"), manifest)
