"""A one-page plain-English brief for non-specialist readers.

Every number in the brief is read from the published outputs (and the R trend
table when it has been run), so the brief is rebuilt with the data each month
and never typed by hand.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import forecast, scenarios

MODEL_NAMES = {"gbm": "gradient boosting", "last_value": "same as last month",
               "seasonal_naive": "same as a year ago"}

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def month_name(key: int) -> str:
    return f"{MONTHS[key % 100 - 1]} {key // 100}"


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def points(x: float) -> str:
    return f"{100 * x:+.1f} points"


def millions(x: float) -> str:
    return f"{x / 1e6:.2f} million"


def build(out_dir: Path, analysis_dir: Path) -> str:
    fc = pd.read_parquet(out_dir / "fact_forecast.parquet")
    targets = pd.read_parquet(out_dir / "targets.parquet")
    dq = pd.read_parquet(out_dir / "dq_checks.parquet")
    backtest = pd.read_parquet(out_dir / "model_backtest.parquet")
    projections, gaps = scenarios.run(fc, targets)
    series = scenarios.england_series(fc)
    trends_path = analysis_dir / "r_trends.csv"
    trends = pd.read_csv(trends_path).set_index("metric") if trends_path.exists() else None
    recon_path = analysis_dir / "r_reconciliation.csv"

    def year_ago(key: str) -> tuple[float, float]:
        y = series[key]
        return float(y.iloc[-1]), float(y[y.index[-1] - 12]) if (y.index[-1] - 12) in y.index else float("nan")

    ae_now, ae_then = year_ago("ae_4hr_pct")
    rtt_now, rtt_then = year_ago("rtt_18wk_pct")
    wl_now, wl_then = year_ago("rtt_waiting_list")
    ae_month = int(series["ae_4hr_pct"].index[-1].strftime("%Y%m"))
    rtt_month = int(series["rtt_waiting_list"].index[-1].strftime("%Y%m"))

    lines = [
        "# NHS England performance: one-page brief",
        "",
        f"Latest data: A&E to {month_name(ae_month)}, waiting lists to {month_name(rtt_month)}. "
        "Built automatically from NHS England's published monthly statistics.",
        "",
        "## What the data shows",
        "",
        f"- **A&E:** {pct(ae_now)} of patients were seen within 4 hours, compared with {pct(ae_then)} "
        f"a year earlier ({points(ae_now - ae_then)}).",
        f"- **Waiting list:** {millions(wl_now)} people were waiting for planned treatment, "
        f"{'down' if wl_now < wl_then else 'up'} from {millions(wl_then)} a year earlier.",
        f"- **18-week waits:** {pct(rtt_now)} had waited under 18 weeks, compared with {pct(rtt_then)} "
        f"a year earlier ({points(rtt_now - rtt_then)}).",
    ]
    if trends is not None:
        lines.append("")
        lines.append("## Longer-term trend (since April 2023, allowing for seasonal patterns)")
        lines.append("")
        for key in ("ae_4hr_pct", "rtt_18wk_pct"):
            if key in trends.index:
                t = trends.loc[key]
                lines.append(
                    f"- **{t['label']}:** improving by about {100 * t['trend_per_year']:.1f} points a year "
                    f"(95% confidence interval {100 * t['trend_ci95_low']:.1f} to {100 * t['trend_ci95_high']:.1f})."
                )

    lines += ["", "## Will the targets be met?", ""]
    for _, g in gaps.iterrows():
        lines.append(
            f"- **{g['label']}:** target {pct(g['target_value'])} by {month_name(int(g['target_month']))}. "
            f"Getting there needs a rise of {100 * g['needed_monthly_change']:.1f} points every month for "
            f"{int(g['months_left'])} months. Only {100 * g['share_of_past_months_achieving_it']:.0f}% of past "
            "months improved that much."
        )
        p = projections[(projections["metric"] == g["metric"])
                        & (projections["month_key"] == g["target_month"])].set_index("scenario")["value"]
        if len(p) == 3:
            lines.append(
                f"  - Scenarios for {month_name(int(g['target_month']))}: unfavourable {pct(p['unfavourable'])}, "
                f"central {pct(p['central'])}, favourable {pct(p['favourable'])}."
            )

    chosen = backtest[backtest["chosen"] == 1].groupby("metric")["model"].first()
    passed = int(dq["passed"].sum())
    lines += [
        "",
        "## How far to trust these figures",
        "",
        f"- **Data quality:** {passed} of {len(dq)} automated checks passed on the latest load.",
        "- **Accuracy:** England figures are checked against NHS England's published headlines.",
    ]
    if recon_path.exists():
        recon = pd.read_csv(recon_path)
        lines.append(
            f"- **Independent rebuild:** the England series were rebuilt separately in R; "
            f"{int(recon['passed'].sum())} of {len(recon)} matched the Python figures exactly."
        )
    lines += [
        "- **Forecasts:** three methods were tested on past months they had not seen, and the most "
        "accurate is used: "
        + "; ".join(f"{forecast.METRICS[m].label if m in forecast.METRICS else m}: {MODEL_NAMES.get(chosen[m], chosen[m])}"
                    for m in chosen.index) + ".",
        "- **Scenarios are not forecasts.** They show what would happen if recent month-to-month changes "
        "continued at their worse quartile, median or better quartile. They do not allow for new funding "
        "or policy changes. See docs/assumptions-and-qa-log.md.",
        "",
    ]
    return "\n".join(lines)


def write(out_dir: Path, analysis_dir: Path) -> Path:
    analysis_dir.mkdir(parents=True, exist_ok=True)
    fc = pd.read_parquet(out_dir / "fact_forecast.parquet")
    targets = pd.read_parquet(out_dir / "targets.parquet")
    projections, gaps = scenarios.run(fc, targets)
    projections.to_csv(analysis_dir / "scenarios.csv", index=False)
    gaps.to_csv(analysis_dir / "gap_to_target.csv", index=False)
    scenarios.assumptions().to_csv(analysis_dir / "scenarios_assumptions.csv", index=False)
    path = analysis_dir / "brief.md"
    path.write_text(build(out_dir, analysis_dir), encoding="utf-8")
    return path
