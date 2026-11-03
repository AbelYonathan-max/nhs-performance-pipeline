"""Six-month forecasts, backtests and anomaly flags with scikit-learn.

One gradient boosting model per metric is trained across every provider at
once (a "global" model). It predicts next month's change rather than the
level, so a small trust and England can share one model. Forecasts are made
one month at a time and fed back in. Ranges come from the errors the same
method made in a rolling backtest, so they show how wrong it has really been.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

log = logging.getLogger(__name__)

HORIZON = 6
BACKTEST_ORIGINS = 6
MIN_HISTORY = 15
ENGLAND = "ENG"


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    kind: str  # "pct" (0-1, model the difference) or "count" (model log ratio)


METRICS = {
    "ae_4hr_pct": Metric("ae_4hr_pct", "A&E seen within 4 hours", "pct"),
    "ae_attendances": Metric("ae_attendances", "A&E attendances", "count"),
    "rtt_18wk_pct": Metric("rtt_18wk_pct", "RTT waiting under 18 weeks", "pct"),
    "rtt_waiting_list": Metric("rtt_waiting_list", "RTT waiting list", "count"),
}


def to_change(metric: Metric, y: np.ndarray) -> np.ndarray:
    if metric.kind == "pct":
        return np.diff(y)
    return np.diff(np.log(np.maximum(y, 1.0)))


def apply_change(metric: Metric, last: float, change: float) -> float:
    if metric.kind == "pct":
        return float(np.clip(last + change, 0.0, 1.0))
    return float(last * np.exp(change))


def features(metric: Metric, y: np.ndarray, target_month: int) -> list[float]:
    """Features for predicting the change into the month after y[-1]."""
    d = to_change(metric, y)
    level = y[-1] if metric.kind == "pct" else np.log(max(y[-1], 1.0))
    return [
        d[-1], d[-2], d[-3],
        d[-12],                       # change into the same month last year
        d[-11] if len(d) >= 11 else 0.0,
        float(np.mean(d[-6:])),
        float(y[-1] - y[-13]) if metric.kind == "pct" else float(np.log(max(y[-1], 1) / max(y[-13], 1))),
        level,
        np.sin(2 * np.pi * target_month / 12),
        np.cos(2 * np.pi * target_month / 12),
    ]


def training_rows(metric: Metric, series: dict[str, pd.Series], until: pd.Period | None):
    X, target = [], []
    for s in series.values():
        if until is not None:
            s = s[s.index <= until]
        y = s.to_numpy(dtype=float)
        months = s.index
        for t in range(14, len(y)):
            X.append(features(metric, y[:t], months[t].month))
            target.append(to_change(metric, y[t - 1 : t + 1])[0])
    return np.array(X), np.array(target)


def fit(metric: Metric, series: dict[str, pd.Series], until: pd.Period | None = None):
    X, target = training_rows(metric, series, until)
    model = HistGradientBoostingRegressor(
        max_iter=300, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=20,
        l2_regularization=1.0, random_state=0,
    )
    model.fit(X, target)
    return model


def roll_forward(metric: Metric, model, s: pd.Series, horizon: int) -> list[float]:
    y = list(s.to_numpy(dtype=float))
    month = s.index[-1]
    out = []
    for h in range(1, horizon + 1):
        target_month = (month + h).month
        change = float(model.predict(np.array([features(metric, np.array(y), target_month)]))[0])
        y.append(apply_change(metric, y[-1], change))
        out.append(y[-1])
    return out


def seasonal_naive(s: pd.Series, h: int) -> float:
    """Value 12 months before the target month."""
    return float(s.iloc[len(s) - 12 + h - 1]) if len(s) >= 12 else float(s.iloc[-1])


def error(metric: Metric, actual: float, predicted: float) -> float:
    """Percentage points for rates, relative error for counts."""
    if metric.kind == "pct":
        return actual - predicted
    return (actual - predicted) / actual if actual else 0.0


def backtest(metric: Metric, series: dict[str, pd.Series]):
    """Rolling origin: refit at each of the last origins, forecast 1..6 ahead."""
    last = max(s.index[-1] for s in series.values())
    origins = [last - HORIZON - i for i in range(BACKTEST_ORIGINS)]
    errors: dict[tuple[str, int], list[float]] = {}
    for origin in origins:
        model = fit(metric, series, until=origin)
        for s in series.values():
            hist = s[s.index <= origin]
            if len(hist) < MIN_HISTORY:
                continue
            preds = roll_forward(metric, model, hist, HORIZON)
            for h in range(1, HORIZON + 1):
                when = origin + h
                if when not in s.index:
                    continue
                actual = float(s[when])
                for name, value in (
                    ("gbm", preds[h - 1]),
                    ("last_value", float(hist.iloc[-1])),
                    ("seasonal_naive", seasonal_naive(hist, h)),
                ):
                    errors.setdefault((name, h), []).append(error(metric, actual, value))
    rows = [
        {"metric": metric.key, "model": name, "horizon": h,
         "mae": float(np.mean(np.abs(e))), "n": len(e)}
        for (name, h), e in sorted(errors.items())
    ]
    bands = {
        h: (float(np.quantile(errors[("gbm", h)], 0.1)), float(np.quantile(errors[("gbm", h)], 0.9)))
        for h in range(1, HORIZON + 1)
        if ("gbm", h) in errors
    }
    return pd.DataFrame(rows), bands


def band(metric: Metric, value: float, err: tuple[float, float]) -> tuple[float, float]:
    """Turn the 10th/90th percentile backtest errors into an 80% range."""
    lo_err, hi_err = err
    if metric.kind == "pct":
        return float(np.clip(value + lo_err, 0, 1)), float(np.clip(value + hi_err, 0, 1))
    # error = (actual - pred) / actual  ->  actual = pred / (1 - error)
    return value / (1 - lo_err), value / (1 - hi_err)


def anomalies(metric: Metric, series: dict[str, pd.Series], z_limit: float = 4.0) -> pd.DataFrame:
    """Months whose change is far outside the series' usual month-to-month change."""
    rows = []
    for key, s in series.items():
        y = s.to_numpy(dtype=float)
        if len(y) < MIN_HISTORY:
            continue
        d = to_change(metric, y)
        med = np.median(d)
        mad = np.median(np.abs(d - med)) * 1.4826
        if mad == 0:
            continue
        z = (d - med) / mad
        for i in np.flatnonzero(np.abs(z) > z_limit):
            expected = apply_change(metric, y[i], med)
            rows.append({
                "metric": metric.key, "provider_code": key,
                "month_key": int(s.index[i + 1].strftime("%Y%m")),
                "value": float(y[i + 1]), "expected": expected, "robust_z": float(z[i]),
            })
    return pd.DataFrame(rows, columns=["metric", "provider_code", "month_key", "value",
                                       "expected", "robust_z"])


def series_from(frame: pd.DataFrame, value: str) -> dict[str, pd.Series]:
    """Long table (month_key, provider_code, value) -> one complete series each."""
    out = {}
    for code, g in frame.groupby("provider_code"):
        s = g.set_index(pd.PeriodIndex([str(k)[:4] + "-" + str(k)[4:] for k in g["month_key"]],
                                       freq="M"))[value].astype(float).sort_index()
        s = s.dropna()
        if len(s) < MIN_HISTORY:
            continue
        full = pd.period_range(s.index[0], s.index[-1], freq="M")
        if len(full) != len(s):
            # Gaps break the lag features; keep the latest unbroken run.
            breaks = np.flatnonzero(np.diff(s.index.asi8) != 1)
            s = s.iloc[breaks[-1] + 1 :]
            if len(s) < MIN_HISTORY:
                continue
        out[code] = s
    return out


def run(inputs: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """inputs: metric key -> DataFrame(month_key, provider_code, value)."""
    forecasts, backtests, flags = [], [], []
    for key, frame in inputs.items():
        metric = METRICS[key]
        series = series_from(frame, "value")
        if not series:
            log.warning("no series long enough for %s", key)
            continue
        scores, bands = backtest(metric, series)
        backtests.append(scores)
        gbm = scores[scores.model == "gbm"].mae.mean()
        naive = scores[scores.model == "seasonal_naive"].mae.mean()
        last = scores[scores.model == "last_value"].mae.mean()
        log.info("%s: %d series, mean abs error gbm %.4f, last value %.4f, seasonal naive %.4f",
                 key, len(series), gbm, last, naive)
        model = fit(metric, series)
        latest = max(s.index[-1] for s in series.values())
        for code, s in series.items():
            sid = f"{key}|{code}"
            for when, value in s.items():
                forecasts.append((sid, key, code, int(when.strftime("%Y%m")), "actual",
                                  float(value), None, None))
            if s.index[-1] != latest:
                continue  # stopped reporting; don't forecast it
            for h, value in enumerate(roll_forward(metric, model, s, HORIZON), start=1):
                lo, hi = band(metric, value, bands.get(h, (0.0, 0.0)))
                when = s.index[-1] + h
                forecasts.append((sid, key, code, int(when.strftime("%Y%m")), "forecast",
                                  value, lo, hi))
        flags.append(anomalies(metric, series))
    columns = ["series_id", "metric", "provider_code", "month_key", "kind", "value",
               "lower_80", "upper_80"]
    return (
        pd.DataFrame(forecasts, columns=columns),
        pd.concat(backtests, ignore_index=True) if backtests else pd.DataFrame(),
        pd.concat(flags, ignore_index=True) if flags else pd.DataFrame(),
    )
