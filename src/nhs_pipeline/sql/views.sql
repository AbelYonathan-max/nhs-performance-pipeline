-- Reporting views. Percentages are fractions (0.78 = 78%).
-- Same month last year is month_key - 100 (202609 -> 202509).

CREATE VIEW v_ae_provider_month AS
SELECT
    f.month_key,
    f.provider_code,
    p.provider_name,
    p.region_code,
    f.attendances,
    f.attendances_type1,
    f.over_4hr,
    f.within_4hr,
    f.reports_4hr,
    f.dta_12_plus,
    f.emergency_admissions_total,
    CASE WHEN f.attendances > 0 AND f.reports_4hr = 1
         THEN 1.0 * f.within_4hr / f.attendances END AS pct_4hr,
    CASE WHEN f.attendances_type1 > 0 AND f.reports_4hr = 1
         THEN 1.0 * (f.attendances_type1 - f.over_4hr_type1) / f.attendances_type1 END AS pct_4hr_type1,
    CASE WHEN f.emergency_admissions_ae > 0
         THEN 1.0 * f.dta_12_plus / f.emergency_admissions_ae END AS pct_12hr_dta
FROM fact_ae f
JOIN dim_provider p ON p.provider_code = f.provider_code;

CREATE VIEW v_ae_region_month AS
SELECT
    f.month_key,
    COALESCE(p.region_code, 'UNK') AS region_code,
    SUM(f.attendances) AS attendances,
    SUM(f.within_4hr) AS within_4hr,
    SUM(f.over_4hr) AS over_4hr,
    SUM(f.dta_12_plus) AS dta_12_plus,
    1.0 * SUM(f.within_4hr) / NULLIF(SUM(f.attendances * f.reports_4hr), 0) AS pct_4hr
FROM fact_ae f
JOIN dim_provider p ON p.provider_code = f.provider_code
GROUP BY f.month_key, COALESCE(p.region_code, 'UNK');

CREATE VIEW v_ae_england_month AS
SELECT
    month_key,
    SUM(attendances) AS attendances,
    SUM(attendances_type1) AS attendances_type1,
    SUM(within_4hr) AS within_4hr,
    SUM(over_4hr) AS over_4hr,
    SUM(dta_4_12) AS dta_4_12,
    SUM(dta_12_plus) AS dta_12_plus,
    SUM(emergency_admissions_total) AS emergency_admissions_total,
    SUM(attendances * reports_4hr) AS attendances_4hr,
    1.0 * SUM(within_4hr) / NULLIF(SUM(attendances * reports_4hr), 0) AS pct_4hr,
    1.0 * SUM((attendances_type1 - over_4hr_type1) * reports_4hr)
        / NULLIF(SUM(attendances_type1 * reports_4hr), 0) AS pct_4hr_type1
FROM fact_ae
GROUP BY month_key;

CREATE VIEW v_rtt_provider_month AS
SELECT
    i.month_key,
    i.provider_code,
    p.provider_name,
    p.region_code,
    p.sector,
    i.total AS waiting_list,
    i.within_18,
    i.over_52,
    i.over_65,
    i.over_78,
    i.over_104,
    i.median_weeks,
    i.p92_weeks,
    1.0 * i.within_18 / NULLIF(i.total, 0) AS pct_18wk,
    1.0 * i.over_52 / NULLIF(i.total, 0) AS share_52wk,
    COALESCE(ca.total, 0) + COALESCE(cn.total, 0) AS clock_stops,
    COALESCE(np.total, 0) AS clock_starts
FROM fact_rtt i
JOIN dim_provider p ON p.provider_code = i.provider_code
LEFT JOIN fact_rtt ca ON ca.month_key = i.month_key AND ca.provider_code = i.provider_code
    AND ca.treatment_function_code = 'C_999' AND ca.part = 'completed_admitted'
LEFT JOIN fact_rtt cn ON cn.month_key = i.month_key AND cn.provider_code = i.provider_code
    AND cn.treatment_function_code = 'C_999' AND cn.part = 'completed_non_admitted'
LEFT JOIN fact_rtt np ON np.month_key = i.month_key AND np.provider_code = i.provider_code
    AND np.treatment_function_code = 'C_999' AND np.part = 'new_periods'
WHERE i.treatment_function_code = 'C_999' AND i.part = 'incomplete';

CREATE VIEW v_rtt_england_month AS
SELECT
    i.month_key,
    i.total AS waiting_list,
    i.within_18,
    i.over_52,
    i.over_65,
    i.over_78,
    i.over_104,
    i.median_weeks,
    i.p92_weeks,
    1.0 * i.within_18 / NULLIF(i.total, 0) AS pct_18wk,
    1.0 * i.over_52 / NULLIF(i.total, 0) AS share_52wk,
    COALESCE(ca.total, 0) + COALESCE(cn.total, 0) AS clock_stops,
    COALESCE(np.total, 0) AS clock_starts
FROM fact_rtt_england i
LEFT JOIN fact_rtt_england ca ON ca.month_key = i.month_key
    AND ca.treatment_function_code = 'C_999' AND ca.part = 'completed_admitted'
LEFT JOIN fact_rtt_england cn ON cn.month_key = i.month_key
    AND cn.treatment_function_code = 'C_999' AND cn.part = 'completed_non_admitted'
LEFT JOIN fact_rtt_england np ON np.month_key = i.month_key
    AND np.treatment_function_code = 'C_999' AND np.part = 'new_periods'
WHERE i.treatment_function_code = 'C_999' AND i.part = 'incomplete';

CREATE VIEW v_rtt_specialty_england_month AS
SELECT
    e.month_key,
    e.treatment_function_code,
    t.treatment_function_name,
    e.total AS waiting_list,
    e.over_52,
    e.median_weeks,
    1.0 * e.within_18 / NULLIF(e.total, 0) AS pct_18wk
FROM fact_rtt_england e
JOIN dim_treatment_function t ON t.treatment_function_code = e.treatment_function_code
WHERE e.part = 'incomplete' AND t.is_total = 0;

-- Latest month league tables, with change on the same month last year.
CREATE VIEW v_ae_league_latest AS
WITH latest AS (SELECT MAX(month_key) AS month_key FROM fact_ae)
SELECT
    cur.month_key,
    cur.provider_code,
    cur.provider_name,
    cur.region_code,
    cur.attendances,
    cur.pct_4hr,
    prev.pct_4hr AS pct_4hr_last_year,
    cur.pct_4hr - prev.pct_4hr AS change_pts,
    RANK() OVER (ORDER BY cur.pct_4hr DESC) AS rank_4hr
FROM v_ae_provider_month cur
JOIN latest l ON l.month_key = cur.month_key
JOIN dim_provider p ON p.provider_code = cur.provider_code AND p.has_type1_ae = 1
LEFT JOIN v_ae_provider_month prev
    ON prev.provider_code = cur.provider_code AND prev.month_key = cur.month_key - 100;

CREATE VIEW v_rtt_league_latest AS
WITH latest AS (SELECT MAX(month_key) AS month_key FROM fact_rtt)
SELECT
    cur.month_key,
    cur.provider_code,
    cur.provider_name,
    cur.region_code,
    cur.waiting_list,
    cur.pct_18wk,
    cur.over_52,
    cur.share_52wk,
    cur.median_weeks,
    prev.pct_18wk AS pct_18wk_last_year,
    cur.pct_18wk - prev.pct_18wk AS change_pts,
    RANK() OVER (ORDER BY cur.pct_18wk DESC) AS rank_18wk
FROM v_rtt_provider_month cur
JOIN latest l ON l.month_key = cur.month_key
LEFT JOIN v_rtt_provider_month prev
    ON prev.provider_code = cur.provider_code AND prev.month_key = cur.month_key - 100
WHERE cur.sector = 'NHS trust' AND cur.waiting_list >= 1000;
