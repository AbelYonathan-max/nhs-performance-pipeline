-- Star schema for NHS England A&E and RTT monthly performance.
-- Written for SQLite; the same DDL runs on SQL Server or PostgreSQL with
-- only type names changed.

CREATE TABLE dim_month (
    month_key       INTEGER PRIMARY KEY,          -- 202609
    month_start     TEXT    NOT NULL UNIQUE,      -- 2026-09-01
    year            INTEGER NOT NULL,
    month           INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    month_name      TEXT    NOT NULL,             -- Sep 2026
    financial_year  TEXT    NOT NULL,             -- 2026-27
    fy_month        INTEGER NOT NULL CHECK (fy_month BETWEEN 1 AND 12),  -- April = 1
    fy_quarter      TEXT    NOT NULL              -- Q2
);

CREATE TABLE dim_region (
    region_code     TEXT PRIMARY KEY,             -- LON
    region_name     TEXT NOT NULL UNIQUE          -- London
);

CREATE TABLE dim_provider (
    provider_code   TEXT PRIMARY KEY,             -- ODS code, e.g. RJ1
    provider_name   TEXT NOT NULL,
    region_code     TEXT REFERENCES dim_region (region_code),
    sector          TEXT NOT NULL,                -- NHS trust / Independent sector / Other
    has_type1_ae    INTEGER NOT NULL DEFAULT 0,   -- runs a major emergency department
    in_ae           INTEGER NOT NULL DEFAULT 0,
    in_rtt          INTEGER NOT NULL DEFAULT 0,
    first_month     INTEGER,
    last_month      INTEGER
);

CREATE TABLE dim_treatment_function (
    treatment_function_code TEXT PRIMARY KEY,     -- C_110
    treatment_function_name TEXT NOT NULL,
    is_total        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE fact_ae (
    month_key                   INTEGER NOT NULL REFERENCES dim_month (month_key),
    provider_code               TEXT    NOT NULL REFERENCES dim_provider (provider_code),
    attendances                 INTEGER NOT NULL CHECK (attendances >= 0),
    attendances_type1           INTEGER NOT NULL CHECK (attendances_type1 >= 0),
    over_4hr                    INTEGER NOT NULL CHECK (over_4hr >= 0),
    over_4hr_type1              INTEGER NOT NULL CHECK (over_4hr_type1 >= 0),
    within_4hr                  INTEGER NOT NULL,
    dta_4_12                    INTEGER NOT NULL CHECK (dta_4_12 >= 0),
    dta_12_plus                 INTEGER NOT NULL CHECK (dta_12_plus >= 0),
    emergency_admissions_ae     INTEGER NOT NULL CHECK (emergency_admissions_ae >= 0),
    emergency_admissions_total  INTEGER NOT NULL CHECK (emergency_admissions_total >= 0),
    PRIMARY KEY (month_key, provider_code),
    CHECK (over_4hr <= attendances)
);

-- One row per provider, treatment function and RTT part, summed over commissioners.
CREATE TABLE fact_rtt (
    month_key               INTEGER NOT NULL REFERENCES dim_month (month_key),
    provider_code           TEXT    NOT NULL REFERENCES dim_provider (provider_code),
    treatment_function_code TEXT    NOT NULL REFERENCES dim_treatment_function (treatment_function_code),
    part                    TEXT    NOT NULL CHECK (part IN ('completed_admitted', 'completed_non_admitted',
                                        'incomplete', 'incomplete_with_dta', 'new_periods')),
    total                   INTEGER NOT NULL CHECK (total >= 0),
    within_18               INTEGER NOT NULL,
    over_18                 INTEGER NOT NULL,
    over_52                 INTEGER NOT NULL,
    over_65                 INTEGER NOT NULL,
    over_78                 INTEGER NOT NULL,
    over_104                INTEGER NOT NULL,
    unknown_clock_start     INTEGER NOT NULL,
    median_weeks            REAL,
    p92_weeks               REAL,
    PRIMARY KEY (month_key, provider_code, treatment_function_code, part),
    CHECK (within_18 + over_18 = total)
);

-- England totals worked out from summed weekly bands (so medians are exact).
CREATE TABLE fact_rtt_england (
    month_key               INTEGER NOT NULL REFERENCES dim_month (month_key),
    treatment_function_code TEXT    NOT NULL REFERENCES dim_treatment_function (treatment_function_code),
    part                    TEXT    NOT NULL,
    total                   INTEGER NOT NULL,
    within_18               INTEGER NOT NULL,
    over_18                 INTEGER NOT NULL,
    over_52                 INTEGER NOT NULL,
    over_65                 INTEGER NOT NULL,
    over_78                 INTEGER NOT NULL,
    over_104                INTEGER NOT NULL,
    unknown_clock_start     INTEGER NOT NULL,
    median_weeks            REAL,
    p92_weeks               REAL,
    PRIMARY KEY (month_key, treatment_function_code, part)
);

CREATE TABLE targets (
    metric          TEXT NOT NULL,
    financial_year  TEXT NOT NULL,
    target_date     TEXT NOT NULL,
    value           REAL NOT NULL,
    scope           TEXT NOT NULL,
    source          TEXT NOT NULL,
    PRIMARY KEY (metric, financial_year, scope)
);

CREATE TABLE fact_forecast (
    series_id       TEXT NOT NULL,                -- ae_4hr_pct|RJ1
    metric          TEXT NOT NULL,
    provider_code   TEXT NOT NULL,                -- ENG for England
    month_key       INTEGER NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('actual', 'fitted', 'forecast')),
    value           REAL,
    lower_80        REAL,
    upper_80        REAL,
    PRIMARY KEY (series_id, month_key, kind)
);

CREATE TABLE model_backtest (
    metric          TEXT NOT NULL,
    model           TEXT NOT NULL,                -- gbm / last_value / seasonal_naive
    horizon         INTEGER NOT NULL,
    mae             REAL NOT NULL,
    n               INTEGER NOT NULL,
    chosen          INTEGER NOT NULL,             -- 1 = used for the published forecast
    PRIMARY KEY (metric, model, horizon)
);

CREATE TABLE anomalies (
    metric          TEXT NOT NULL,
    provider_code   TEXT NOT NULL,
    month_key       INTEGER NOT NULL,
    value           REAL,
    expected        REAL,
    robust_z        REAL NOT NULL,
    PRIMARY KEY (metric, provider_code, month_key)
);

CREATE TABLE dq_checks (
    run_at          TEXT NOT NULL,
    dataset         TEXT NOT NULL,
    check_name      TEXT NOT NULL,
    severity        TEXT NOT NULL CHECK (severity IN ('error', 'warning', 'info')),
    passed          INTEGER NOT NULL,
    detail          TEXT
);

CREATE TABLE load_log (
    dataset         TEXT NOT NULL,
    period          TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    rows_loaded     INTEGER NOT NULL,
    loaded_at       TEXT NOT NULL,
    PRIMARY KEY (dataset, period)
);
