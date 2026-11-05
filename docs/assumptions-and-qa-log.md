# Assumptions and quality assurance log

This log follows the approach of HM Treasury's *Aqua Book* (guidance on producing
quality analysis for government): what the analysis is for, what it assumes,
how it has been checked, and what it cannot tell you.

## Purpose

Monthly reporting of NHS England A&E and referral-to-treatment (RTT) performance
for trusts, regions and England, with forecasts, scenarios and progress against
the national targets. Intended users: anyone needing a quick, traceable view of
performance (for example a planning or performance team). It is not official
statistics; NHS England's own publications are the authoritative source.

## Data

| Item | Detail |
|---|---|
| Source | NHS England monthly A&E and RTT statistics, Open Government Licence v3.0 |
| Period | April 2023 to the latest published month |
| Traceability | Every source file's URL and SHA-256 hash is kept in `source-manifest.json` |
| Personal data | None: aggregate statistics about organisations only |

## Assumptions

| Area | Assumption | Effect if wrong |
|---|---|---|
| A&E 4-hour % | Trusts that reported no 4-hour figure in a month (14 trusts in April–May 2023 testing new standards) are left out of that month's percentage | England % for those months is slightly different from a figure including them |
| RTT | Rows are summed over commissioners; medians are interpolated from weekly bands | Medians are estimates, accurate to within a week band |
| Regions | Providers only in the RTT file take the region of their integrated care board | A small number of providers could sit in the wrong region |
| Forecasts | The next six months behave like the backtest period | Ranges would be too narrow after a sudden change in policy or recording |
| Trend (R) | A straight-line trend plus a fixed month-of-year pattern | See limitations: misses step changes |
| Scenarios | Future monthly changes look like past ones: worse quartile, median or better quartile | Scenarios do not bound the future; they show plausible paths if history repeats |
| Targets | National ambitions in `config/targets.csv`, measured for England as a whole | Local or trust-level targets differ |

Scenario assumptions are also written to `data/analysis/scenarios_assumptions.csv`
on every run.

## Quality assurance performed

**Verification (is the analysis built right?)**

- **14 automated data quality checks** on every load: duplicate keys, missing months,
  negative counts, 4-hour breaches above attendances, specialties adding up to the
  total, sudden swings. A failed "error" check stops the load.
- **Automated tests** (pytest) build raw files in NHS England's layout and run every
  stage, including the scenarios, brief and R script. They run on every push in
  GitHub Actions.
- **Independent rebuild in R** (`r/england_trends.R`): the England series are rebuilt
  from the trust-level facts in a second language and compared with the Python
  figures. Latest run: all four series matched exactly (`r_reconciliation.csv`).
  A mismatch stops the R script with an error.
- **Excel pack** recalculates with no formula errors (checked with LibreOffice in tests).
- **Power BI** files are checked against Microsoft's schemas and load with the
  Tabular Object Model in CI.

**Validation (is it the right analysis, and is it accurate?)**

- **Published figures:** England results match NHS England's published headlines,
  for example A&E 4-hour performance of 74.6% in April 2023.
- **Forecast backtest:** each method is refitted at 6 past origins and scored 1–6
  months ahead against two simple baselines ("same as last month", "same as a year
  ago"). The most accurate method is used; for the RTT waiting list the simple
  baseline won, so the model is not used there.
- **Forecast ranges** come from the method's own past errors, not from theory.

## Limitations

- **Short history.** About 3.5 years of data gives only three or four values for each
  calendar month, so seasonal patterns are estimated roughly.
- **Step changes.** RTT 18-week performance jumped in early 2026. The straight-line
  trend in R averages over this, so it understates recent improvement (visible in
  `trend_rtt_18wk_pct.png`). The gap-to-target table, which starts from the latest
  month, is the better guide to the target.
- **52-week target.** The "under 1% waiting over 52 weeks" target date (March 2026)
  has passed, so no gap-to-target is calculated; the latest share (1.6%) shows it
  was not met.
- **Not causal.** Nothing here explains why performance changed.

## Change log

| Date | Change |
|---|---|
| 4 Nov 2026 | First full run: 14 checks, forecasts with backtest, Excel pack, Power BI |
| 5 Nov 2026 | Added R independent rebuild and trend analysis, scenarios, gap to target, one-page brief and this log |
