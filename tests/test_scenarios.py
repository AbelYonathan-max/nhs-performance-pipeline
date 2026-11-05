import shutil
import subprocess

import numpy as np
import pandas as pd
import pytest

from nhs_pipeline import brief, pipeline, scenarios

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


def steady(start: float, change: float, n: int = 24) -> pd.Series:
    idx = pd.period_range("2023-04", periods=n, freq="M")
    return pd.Series(start + change * np.arange(n), index=idx)


def test_steady_series_projects_its_own_change():
    spec = scenarios.SERIES["ae_4hr_pct"]
    y = steady(0.70, 0.002)
    p = scenarios.project(spec, y, y.index[-1] + 6)
    # Every past change is the same, so all three scenarios agree.
    last = p[p.month_key == int((y.index[-1] + 6).strftime("%Y%m"))]
    assert last.value.to_numpy() == pytest.approx([y.iloc[-1] + 6 * 0.002] * 3)


def test_scenarios_are_ordered_for_rates_and_counts():
    rng = np.random.default_rng(0)
    idx = pd.period_range("2023-04", periods=36, freq="M")
    rate = pd.Series(0.6 + np.cumsum(rng.normal(0.001, 0.01, 36)), index=idx)
    count = pd.Series(7e6 * np.exp(np.cumsum(rng.normal(0, 0.01, 36))), index=idx)
    for key, y in (("rtt_18wk_pct", rate), ("rtt_waiting_list", count)):
        p = scenarios.project(scenarios.SERIES[key], y, y.index[-1] + 12)
        end = p[p.month_key == p.month_key.max()].set_index("scenario").value
        if scenarios.SERIES[key].higher_is_better:
            assert end["unfavourable"] < end["central"] < end["favourable"]
        else:  # a shorter waiting list is favourable
            assert end["favourable"] < end["central"] < end["unfavourable"]


def test_rates_stay_between_0_and_1():
    p = scenarios.project(scenarios.SERIES["ae_4hr_pct"], steady(0.95, 0.02), pd.Period("2027-03", "M"))
    assert p.value.between(0, 1).all()


def test_gap_to_target():
    y = steady(0.60, 0.005, n=12)                 # latest 0.655
    g = scenarios.gap_to_target(scenarios.SERIES["rtt_18wk_pct"], y, 0.70, y.index[-1] + 9)
    assert g["months_left"] == 9
    assert g["needed_monthly_change"] == pytest.approx(0.005)
    assert g["share_of_past_months_achieving_it"] == pytest.approx(1.0)
    g = scenarios.gap_to_target(scenarios.SERIES["rtt_18wk_pct"], y, 0.80, y.index[-1] + 9)
    assert g["share_of_past_months_achieving_it"] == 0.0


def test_brief_and_r_check_on_synthetic_outputs(built, tmp_path):
    out = tmp_path / "out"
    pipeline.export(built, out)
    analysis = tmp_path / "analysis"
    if shutil.which("Rscript"):
        subprocess.run(["Rscript", str(ROOT / "r" / "england_trends.R"), str(out), str(analysis)], check=True)
        recon = pd.read_csv(analysis / "r_reconciliation.csv")
        assert recon.passed.all(), recon
        assert (analysis / "trend_ae_4hr_pct.png").exists()
    path = brief.write(out, analysis)
    text = path.read_text(encoding="utf-8")
    assert "## What the data shows" in text and "## Will the targets be met?" in text
    assert "nan" not in text.lower()
    assert (analysis / "scenarios.csv").exists() and (analysis / "gap_to_target.csv").exists()
