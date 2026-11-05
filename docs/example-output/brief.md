# NHS England performance: one-page brief

Latest data: A&E to September 2026, waiting lists to August 2026. Built automatically from NHS England's published monthly statistics.

## What the data shows

- **A&E:** 74.4% of patients were seen within 4 hours, compared with 75.1% a year earlier (-0.7 points).
- **Waiting list:** 7.19 million people were waiting for planned treatment, down from 7.35 million a year earlier.
- **18-week waits:** 65.2% had waited under 18 weeks, compared with 60.9% a year earlier (+4.2 points).

## Longer-term trend (since April 2023, allowing for seasonal patterns)

- **A&E seen within 4 hours:** improving by about 1.0 points a year (95% confidence interval 0.7 to 1.3).
- **RTT waiting under 18 weeks:** improving by about 2.3 points a year (95% confidence interval 1.9 to 2.7).

## Will the targets be met?

- **A&E seen within 4 hours:** target 82.0% by March 2027. Getting there needs a rise of 1.3 points every month for 6 months. Only 12% of past months improved that much.
  - Scenarios for March 2027: unfavourable 74.1%, central 75.9%, favourable 78.6%.
- **RTT waiting under 18 weeks:** target 70.0% by March 2027. Getting there needs a rise of 0.7 points every month for 7 months. Only 18% of past months improved that much.
  - Scenarios for March 2027: unfavourable 64.9%, central 66.5%, favourable 68.6%.

## How far to trust these figures

- **Data quality:** 14 of 14 automated checks passed on the latest load.
- **Accuracy:** England figures are checked against NHS England's published headlines.
- **Independent rebuild:** the England series were rebuilt separately in R; 4 of 4 matched the Python figures exactly.
- **Forecasts:** three methods were tested on past months they had not seen, and the most accurate is used: A&E seen within 4 hours: gradient boosting; A&E attendances: gradient boosting; RTT waiting under 18 weeks: gradient boosting; RTT waiting list: same as last month.
- **Scenarios are not forecasts.** They show what would happen if recent month-to-month changes continued at their worse quartile, median or better quartile. They do not allow for new funding or policy changes. See [the assumptions and QA log](../assumptions-and-qa-log.md).
