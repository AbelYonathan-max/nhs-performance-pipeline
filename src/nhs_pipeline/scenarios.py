"""Scenario projections and gap-to-target analysis for England.

The forecasts in forecast.py answer "what is most likely next?". This module
answers "what if?": it projects each England series under three stated
assumptions and shows what each would mean for the national targets.

Assumptions (also written to scenarios_assumptions.csv):

- Each month's change is drawn from the series' own history since April 2023.
  "Central" repeats the median monthly change, "favourable" the better quartile
  and "unfavourable" the worse quartile. Same-month seasonality is kept by
  adding the average gap between that calendar month and the series' mean change.
- Rates use the change in percentage points; counts use the change in logs
  (so a waiting list grows or shrinks by a percentage, not a fixed number).
- Projections run to each metric's next national target date (12 months ahead
  when there is none). A&E attendances are left out: more or fewer attendances
  is not in itself better or worse.

Gap to target: the steady monthly change needed from the latest month to land
exactly on the target, compared with the history of monthly changes. The share
of past months that improved by at least that much shows how demanding the
target is ("needed every month, achieved in x% of past months").
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

SCENARIOS = {"unfavourable": 0.25, "central": 0.50, "favourable": 0.75}


@dataclass(frozen=True)
class Series:
    key: str
    label: str
    kind: str                # "pct" or "count"
    higher_is_better: bool


SERIES = {
    "ae_4hr_pct": Series("ae_4hr_pct", "A&E seen within 4 hours", "pct", True),
    "rtt_18wk_pct": Series("rtt_18wk_pct", "RTT waiting under 18 weeks", "pct", True),
    "rtt_waiting_list": Series("rtt_waiting_list", "RTT waiting list", "count", False),
}


def monthly_changes(spec: Series, y: pd.Series) -> pd.Series:
    """Change into each month: points for rates, log ratio for counts."""
    v = y.astype(float)
    d = v.diff() if spec.kind == "pct" else np.log(v).diff()
    return d.dropna()


def seasonal_offsets(changes: pd.Series) -> dict[int, float]:
    """Average gap between each calendar month's change and the overall mean."""
    mean = changes.mean()
    by_month = changes.groupby(changes.index.month).mean() - mean
    return {m: float(by_month.get(m, 0.0)) for m in range(1, 13)}


def step(spec: Series, level: float, change: float) -> float:
    if spec.kind == "pct":
        return float(np.clip(level + change, 0.0, 1.0))
    return float(level * np.exp(change))


def project(spec: Series, y: pd.Series, until: pd.Period) -> pd.DataFrame:
    """Project y (PeriodIndex, monthly) to `until` under each scenario."""
    changes = monthly_changes(spec, y)
    offsets = seasonal_offsets(changes)
    deseason = changes - changes.index.month.map(offsets).to_numpy()
    rows = []
    for name, q in SCENARIOS.items():
        # "Favourable" means the better quartile, which is the upper one when
        # higher is better and the lower one otherwise.
        quantile = q if spec.higher_is_better else 1 - q
        base = float(np.quantile(deseason, quantile))
        level = float(y.iloc[-1])
        month = y.index[-1]
        while month < until:
            month += 1
            level = step(spec, level, base + offsets[month.month])
            rows.append({"metric": spec.key, "scenario": name, "month_key": int(month.strftime("%Y%m")),
                         "value": level, "assumed_monthly_change": base})
    return pd.DataFrame(rows)


def gap_to_target(spec: Series, y: pd.Series, target: float, target_month: pd.Period) -> dict:
    """Steady monthly change needed to hit the target, against past changes."""
    months_left = (target_month - y.index[-1]).n
    latest = float(y.iloc[-1])
    if spec.kind == "pct":
        needed = (target - latest) / months_left
    else:
        needed = float(np.log(target / latest) / months_left)
    changes = monthly_changes(spec, y)
    achieved = (changes >= needed) if needed >= 0 else (changes <= needed)
    return {
        "metric": spec.key, "label": spec.label, "latest_month": int(y.index[-1].strftime("%Y%m")),
        "latest_value": latest, "target_value": target,
        "target_month": int(target_month.strftime("%Y%m")), "months_left": months_left,
        "needed_monthly_change": needed,
        "median_past_monthly_change": float(changes.median()),
        "share_of_past_months_achieving_it": float(achieved.mean()),
    }


def england_series(forecasts: pd.DataFrame) -> dict[str, pd.Series]:
    """England actuals from fact_forecast, one PeriodIndex series per metric."""
    eng = forecasts[(forecasts["provider_code"] == "ENG") & (forecasts["kind"] == "actual")]
    out = {}
    for key, g in eng.groupby("metric"):
        if key not in SERIES:
            continue
        idx = pd.PeriodIndex([f"{str(k)[:4]}-{str(k)[4:]}" for k in g["month_key"]], freq="M")
        out[key] = pd.Series(g["value"].to_numpy(dtype=float), index=idx).sort_index()
    return out


def run(forecasts: pd.DataFrame, targets: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (scenario projections, gap-to-target table)."""
    projections, gaps = [], []
    for key, y in england_series(forecasts).items():
        spec = SERIES[key]
        future = targets[(targets["metric"] == key)
                         & (pd.to_datetime(targets["target_date"]) > y.index[-1].to_timestamp())]
        future = future.sort_values("target_date")
        if len(future):
            target_month = pd.Period(pd.to_datetime(future["target_date"].iloc[0]), freq="M")
            gaps.append(gap_to_target(spec, y, float(future["value"].iloc[0]), target_month))
        else:
            target_month = y.index[-1] + 12
        projections.append(project(spec, y, target_month))
    return pd.concat(projections, ignore_index=True), pd.DataFrame(gaps)


def assumptions() -> pd.DataFrame:
    return pd.DataFrame([
        ("history", "Monthly changes from April 2023 to the latest month are taken as representative of the near future."),
        ("scenarios", "Unfavourable, central and favourable repeat the worse quartile, median and better quartile of past monthly changes."),
        ("seasonality", "Each calendar month keeps its average difference from the mean change."),
        ("rates", "Rates move in percentage points and are kept between 0% and 100%."),
        ("counts", "Counts move by a percentage (log change), so they cannot go below zero."),
        ("policy", "No allowance is made for new funding, policy or recording changes after the latest month."),
        ("targets", "Targets are the national ambitions in config/targets.csv, measured for England as a whole."),
    ], columns=["assumption", "detail"])
