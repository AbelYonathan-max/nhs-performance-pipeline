import numpy as np
import pandas as pd
import pytest

from nhs_pipeline import forecast, warehouse


def test_change_round_trip():
    m = forecast.METRICS["rtt_waiting_list"]
    y = np.array([100.0, 110.0])
    assert forecast.apply_change(m, 100.0, forecast.to_change(m, y)[0]) == pytest.approx(110.0)
    p = forecast.METRICS["ae_4hr_pct"]
    assert forecast.apply_change(p, 0.99, 0.05) == 1.0


def test_band_for_counts_contains_value():
    m = forecast.METRICS["ae_attendances"]
    lo, hi = forecast.band(m, 1000.0, (-0.05, 0.04))
    assert lo < 1000 < hi


def test_anomaly_flags_a_spike():
    idx = pd.period_range("2023-04", periods=24, freq="M")
    values = 0.75 + 0.002 * np.sin(np.arange(24))
    values[15] = 0.55
    flags = forecast.anomalies(forecast.METRICS["ae_4hr_pct"], {"RJ1": pd.Series(values, index=idx)})
    assert 202407 in flags.month_key.tolist()


def test_forecasts_and_backtest(built):
    fc = warehouse.query(built, "SELECT * FROM fact_forecast WHERE kind = 'forecast'")
    assert set(fc.metric) == set(forecast.METRICS)
    per_series = fc.groupby("series_id").size()
    assert (per_series == forecast.HORIZON).all()
    assert (fc.lower_80 <= fc.value + 1e-9).all() and (fc.value <= fc.upper_80 + 1e-9).all()
    bt = warehouse.query(built, "SELECT * FROM model_backtest")
    assert set(bt.model) == {"gbm", "last_value", "seasonal_naive"}
    assert bt.groupby("metric").chosen.sum().eq(forecast.HORIZON).all()
