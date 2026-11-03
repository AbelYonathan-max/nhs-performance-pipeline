import numpy as np
import pandas as pd
import pytest
import synthetic

from nhs_pipeline import transform


def test_norm():
    assert transform.norm("A&E attendances  Type 1") == "ae attendances type 1"
    assert transform.norm("Patients who have waited 12+ hrs from DTA to admission") == (
        "patients who have waited 12 plus hrs from dta to admission")


def test_to_number_handles_markers():
    out = transform.to_number(pd.Series(["1,234", "-", "*", "", " 7 "]))
    assert out.tolist()[0] == 1234 and out.isna().tolist() == [False, True, True, True, False]


def test_parse_ae_totals_and_percentages():
    rng = np.random.default_rng(1)
    df = transform.parse_ae(synthetic.ae_csv("2026-09", rng), "2026-09")
    assert list(df.columns) == transform.AE_COLUMNS
    assert len(df) == len(synthetic.PROVIDERS) + 1
    row = df[df.provider_code == "NLO12"].iloc[0]
    assert row.attendances == 2500 and row.over_4hr == 1 and row.attendances_type1 == 0
    assert (df.within_4hr + df.over_4hr == df.attendances).all()


def test_parse_ae_missing_column_is_an_error():
    csv = b"Period,Org Code,Parent Org,Org name\nx,RJ1,LONDON,Trust\n"
    with pytest.raises(transform.SchemaError):
        transform.parse_ae(csv, "2026-09")


def test_percentile_weeks():
    edges = np.arange(5, dtype=float)
    counts = np.array([[10, 10, 10, 10, 10], [0, 0, 0, 0, 0]], dtype=float)
    median = transform.percentile_weeks(counts, edges, 0.5)
    assert median[0] == pytest.approx(2.5)
    assert np.isnan(median[1])


def test_parse_rtt_sums_commissioners_and_builds_england():
    rng = np.random.default_rng(2)
    providers, england = transform.parse_rtt(synthetic.rtt_zip("2026-08", rng), "2026-08")
    assert not providers.duplicated(["provider_code", "treatment_function_code", "part"]).any()
    total = providers[(providers.part == "incomplete") & (providers.treatment_function_code == "C_999")]
    eng = england[(england.part == "incomplete") & (england.treatment_function_code == "C_999")]
    assert eng.total.iloc[0] == total.total.sum()
    assert (providers.within_18 + providers.over_18 == providers.total).all()
    assert (providers.over_52 >= providers.over_65).all()
    assert set(providers.part) == set(transform.RTT_PARTS.values())
