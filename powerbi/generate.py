"""Write the Power BI project (PBIP: TMDL semantic model + PBIR report).

The model and report are plain text so they can be reviewed in git. This
script is the single source for both; run it after changing a table, measure
or visual:

    python powerbi/generate.py

Power BI Desktop opens "powerbi/NHS Performance.pbip". The model reads the
Parquet files the pipeline publishes to the repo's `data` branch, so it
refreshes in the Power BI service without a gateway.
"""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "NHS Performance"
MODEL_DIR = ROOT / f"{NAME}.SemanticModel"
REPORT_DIR = ROOT / f"{NAME}.Report"
DATA_ROOT = "https://raw.githubusercontent.com/AbelYonathan-max/nhs-performance-pipeline/data/"
NS = uuid.UUID("6f1c2a5e-5d0c-4c1e-9a54-6a3b7c1d2e90")

SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/"
V_VISUAL = SCHEMA + "item/report/definition/visualContainer/2.5.0/schema.json"
V_PAGE = SCHEMA + "item/report/definition/page/2.0.0/schema.json"
V_PAGES = SCHEMA + "item/report/definition/pagesMetadata/1.0.0/schema.json"
V_REPORT = SCHEMA + "item/report/definition/report/3.1.0/schema.json"
V_VERSION = SCHEMA + "item/report/definition/versionMetadata/1.0.0/schema.json"
V_PBIR = SCHEMA + "item/report/definitionProperties/2.0.0/schema.json"
V_PBISM = SCHEMA + "item/semanticModel/definitionProperties/1.0.0/schema.json"
V_PBIP = SCHEMA + "pbip/pbipProperties/1.0.0/schema.json"
V_PLATFORM = SCHEMA + "gitIntegration/platformProperties/2.0.0/schema.json"

DEFAULT_TRUST = "GUY'S AND ST THOMAS' NHS FOUNDATION TRUST"
NHS_BLUE = "#005EB8"
NHS_DARK = "#003087"
NHS_GREEN = "#009639"
NHS_RED = "#DA291C"
NHS_GREY = "#425563"
NHS_WARM = "#ED8B00"


def tag(*parts: str) -> str:
    return str(uuid.uuid5(NS, "|".join(parts)))


def q(name: str) -> str:
    """Quote a TMDL object name when it needs it."""
    if name.replace("_", "").isalnum() and not name[0].isdigit():
        return name
    return "'" + name.replace("'", "''") + "'"


# ---------------------------------------------------------------------------
# Semantic model
# ---------------------------------------------------------------------------

@dataclass
class Column:
    name: str
    source: str
    type: str  # int64 / double / string / dateTime / boolean
    hidden: bool = False
    fmt: str | None = None
    sort_by: str | None = None
    folder: str | None = None
    summarize: str = "none"


@dataclass
class Measure:
    name: str
    expression: str
    fmt: str | None = None
    folder: str | None = None
    description: str | None = None


@dataclass
class TableDef:
    name: str
    source: str | None  # parquet file name, or None for M below
    columns: list[Column]
    measures: list[Measure] = field(default_factory=list)
    m: str | None = None
    hidden: bool = False
    description: str | None = None


M_TYPES = {"int64": "Int64.Type", "double": "type number", "string": "type text",
           "dateTime": "type date", "boolean": "type logical"}


def key(name: str, source: str) -> Column:
    return Column(name, source, "int64" if source == "month_key" else "string", hidden=True)


def num(name: str, source: str, fmt: str = "#,0", hidden: bool = True) -> Column:
    return Column(name, source, "double", hidden=hidden, fmt=fmt, summarize="sum")


# Power BI Desktop refuses a table named "Measures".
MEASURE_TABLE = "Key measures"

LATEST_AE = "CALCULATE ( MAX ( 'A&E'[month_key] ) )"
LATEST_RTT = "CALCULATE ( MAX ( RTT[month_key] ) )"


def snapshot(column: str, table: str = "RTT") -> str:
    """Waiting-list counts are month-end snapshots: show the latest month, never a sum over months."""
    return (
        f"VAR k = CALCULATE ( MAX ( {table}[month_key] ) )\n"
        f"RETURN CALCULATE ( SUM ( {table}[{column}] ), {table}[month_key] = k )"
    )


def target(metric: str) -> str:
    return (
        "VAR fy = MAX ( 'Month'[Financial year] )\n"
        f"RETURN LOOKUPVALUE ( Targets[value], Targets[metric], \"{metric}\", Targets[financial_year], fy )"
    )


def at_latest(measure: str, latest: str, offset: int = 0) -> str:
    shift = f" - {offset}" if offset else ""
    return (
        f"VAR k = {latest}\n"
        f"RETURN CALCULATE ( [{measure}], REMOVEFILTERS ( 'Month' ), 'Month'[month_key] = k{shift} )"
    )


def latest_target(metric: str, latest: str) -> str:
    return (
        f"VAR k = {latest}\n"
        "VAR fy = CALCULATE ( MAX ( 'Month'[Financial year] ), REMOVEFILTERS ( 'Month' ), 'Month'[month_key] = k )\n"
        f"RETURN LOOKUPVALUE ( Targets[value], Targets[metric], \"{metric}\", Targets[financial_year], fy )"
    )


MEASURES = [
    # A&E
    Measure("Attendances", "SUM ( 'A&E'[attendances] )", "#,0", "A&E"),
    Measure("Within 4 hours", "SUM ( 'A&E'[within_4hr] )", "#,0", "A&E"),
    Measure("A&E 4hr %", "DIVIDE ( [Within 4 hours], CALCULATE ( [Attendances], 'A&E'[reports_4hr] = 1 ) )",
            "0.0%", "A&E",
            "Share of all A&E attendances admitted, transferred or discharged within 4 hours. "
            "Sites that did not report the 4-hour measure that month are left out."),
    Measure("Type 1 4hr %", "CALCULATE ( DIVIDE ( SUM ( 'A&E'[attendances_type1] ) - SUM ( 'A&E'[over_4hr_type1] ), "
            "SUM ( 'A&E'[attendances_type1] ) ), 'A&E'[reports_4hr] = 1 )", "0.0%", "A&E",
            "As A&E 4hr % but major emergency departments (type 1) only."),
    Measure("12hr trolley waits", "SUM ( 'A&E'[dta_12_plus] )", "#,0", "A&E",
            "Patients who waited over 12 hours from decision to admit to admission."),
    Measure("Emergency admissions", "SUM ( 'A&E'[emergency_admissions_total] )", "#,0", "A&E"),
    Measure("A&E target", target("ae_4hr_pct"), "0%", "A&E",
            "National 4-hour ambition for the financial year of the month."),
    Measure("A&E gap to target", "IF ( NOT ISBLANK ( [A&E 4hr %] ), [A&E 4hr %] - [A&E target] )",
            "+0.0%;-0.0%;0.0%", "A&E"),
    Measure("Latest A&E month", "VAR k = " + LATEST_AE + "\nRETURN FORMAT ( DATE ( INT ( k / 100 ), MOD ( k, 100 ), 1 ), \"mmm yyyy\" )",
            None, "A&E"),
    Measure("A&E 4hr % (latest)", at_latest("A&E 4hr %", LATEST_AE), "0.0%", "A&E"),
    Measure("A&E 4hr % (year before)", at_latest("A&E 4hr %", LATEST_AE, 100), "0.0%", "A&E"),
    Measure("A&E 4hr change on year", "IF ( NOT ISBLANK ( [A&E 4hr % (year before)] ), "
            "[A&E 4hr % (latest)] - [A&E 4hr % (year before)] )", "+0.0%;-0.0%;0.0%", "A&E"),
    Measure("Attendances (latest)", at_latest("Attendances", LATEST_AE), "#,0", "A&E"),
    Measure("12hr trolley waits (latest)", at_latest("12hr trolley waits", LATEST_AE), "#,0", "A&E"),
    Measure("A&E target (latest)", latest_target("ae_4hr_pct", LATEST_AE), "0%", "A&E"),
    Measure("A&E rank",
            "IF ( HASONEVALUE ( Organisation[Code] ) && NOT ISBLANK ( [A&E 4hr % (latest)] ),\n"
            "    RANKX ( FILTER ( ALLSELECTED ( Organisation ), Organisation[Major A&E] = \"Yes\" ), "
            "[A&E 4hr % (latest)], , DESC ) )", "0", "A&E"),
    Measure("Trusts meeting 4hr target",
            "VAR t = [A&E target (latest)]\n"
            "RETURN COUNTROWS ( FILTER ( VALUES ( Organisation[Code] ), "
            "[A&E 4hr % (latest)] >= t && CALCULATE ( MAX ( Organisation[Major A&E] ) ) = \"Yes\" ) ) + 0",
            "0", "A&E"),
    # RTT
    Measure("Waiting list", snapshot("waiting_list"), "#,0", "RTT",
            "Patients waiting to start consultant-led treatment at month end (latest month in view)."),
    Measure("Within 18 weeks", snapshot("within_18"), "#,0", "RTT"),
    Measure("Over 52 weeks", snapshot("over_52"), "#,0", "RTT"),
    Measure("Over 65 weeks", snapshot("over_65"), "#,0", "RTT"),
    Measure("Over 78 weeks", snapshot("over_78"), "#,0", "RTT"),
    Measure("RTT 18wk %", "DIVIDE ( [Within 18 weeks], [Waiting list] )", "0.0%", "RTT",
            "Share of the waiting list waiting under 18 weeks."),
    Measure("Share over 52 weeks", "DIVIDE ( [Over 52 weeks], [Waiting list] )", "0.00%", "RTT"),
    Measure("Treated (clock stops)", "SUM ( RTT[clock_stops] )", "#,0", "RTT"),
    Measure("New referrals (clock starts)", "SUM ( RTT[clock_starts] )", "#,0", "RTT"),
    Measure("Median wait (weeks)",
            "VAR k = " + LATEST_RTT + "\n"
            "VAR tf = SELECTEDVALUE ( Specialty[Code], \"C_999\" )\n"
            "RETURN\n"
            "    IF (\n"
            "        NOT ISFILTERED ( Organisation ) && NOT ISFILTERED ( Region ),\n"
            "        CALCULATE ( MAX ( 'RTT England'[median_weeks] ), 'RTT England'[month_key] = k,\n"
            "            'RTT England'[treatment_function_code] = tf ),\n"
            "        IF ( HASONEVALUE ( Organisation[Code] ) && tf = \"C_999\",\n"
            "            CALCULATE ( MAX ( RTT[median_weeks] ), RTT[month_key] = k ) )\n"
            "    )", "0.0", "RTT",
            "Median wait in weeks. England figures come from summed weekly bands; one organisation at a time otherwise."),
    Measure("RTT target", target("rtt_18wk_pct"), "0%", "RTT"),
    Measure("RTT gap to target", "IF ( NOT ISBLANK ( [RTT 18wk %] ), [RTT 18wk %] - [RTT target] )",
            "+0.0%;-0.0%;0.0%", "RTT"),
    Measure("Latest RTT month", "VAR k = " + LATEST_RTT + "\nRETURN FORMAT ( DATE ( INT ( k / 100 ), MOD ( k, 100 ), 1 ), \"mmm yyyy\" )",
            None, "RTT"),
    Measure("RTT 18wk % (year before)",
            "VAR k = " + LATEST_RTT + "\n"
            "RETURN CALCULATE ( [RTT 18wk %], REMOVEFILTERS ( 'Month' ), 'Month'[month_key] = k - 100 )",
            "0.0%", "RTT"),
    Measure("RTT 18wk change on year", "IF ( NOT ISBLANK ( [RTT 18wk % (year before)] ), "
            "[RTT 18wk %] - [RTT 18wk % (year before)] )", "+0.0%;-0.0%;0.0%", "RTT"),
    Measure("Waiting list change on year",
            "VAR k = " + LATEST_RTT + "\n"
            "VAR before = CALCULATE ( SUM ( RTT[waiting_list] ), REMOVEFILTERS ( 'Month' ), RTT[month_key] = k - 100 )\n"
            "RETURN IF ( NOT ISBLANK ( before ), [Waiting list] - before )", "+#,0;-#,0;0", "RTT"),
    Measure("RTT target (latest)", latest_target("rtt_18wk_pct", LATEST_RTT), "0%", "RTT"),
    Measure("Specialty waiting list",
            "VAR k = CALCULATE ( MAX ( 'RTT specialty'[month_key] ) )\n"
            "RETURN CALCULATE ( SUM ( 'RTT specialty'[waiting_list] ), 'RTT specialty'[month_key] = k )",
            "#,0", "RTT"),
    Measure("Specialty over 52 weeks",
            "VAR k = CALCULATE ( MAX ( 'RTT specialty'[month_key] ) )\n"
            "RETURN CALCULATE ( SUM ( 'RTT specialty'[over_52] ), 'RTT specialty'[month_key] = k )",
            "#,0", "RTT"),
    Measure("Specialty 18wk %",
            "VAR k = CALCULATE ( MAX ( 'RTT specialty'[month_key] ) )\n"
            "RETURN DIVIDE ( CALCULATE ( SUM ( 'RTT specialty'[within_18] ), 'RTT specialty'[month_key] = k ), "
            "[Specialty waiting list] )", "0.0%", "RTT"),
    Measure("RTT rank",
            "IF ( HASONEVALUE ( Organisation[Code] ) && NOT ISBLANK ( [RTT 18wk %] ),\n"
            "    RANKX ( FILTER ( ALLSELECTED ( Organisation ), Organisation[Sector] = \"NHS trust\" "
            "&& [Waiting list] >= 1000 ), [RTT 18wk %], , DESC ) )", "0", "RTT"),
    # Forecast
    Measure("Actual", "CALCULATE ( SUM ( Forecast[value] ), Forecast[kind] = \"actual\" )", None, "Forecast"),
    Measure("Forecast", "CALCULATE ( SUM ( Forecast[value] ), Forecast[kind] = \"forecast\" )", None, "Forecast"),
    Measure("Forecast low (80%)", "CALCULATE ( SUM ( Forecast[lower_80] ), Forecast[kind] = \"forecast\" )",
            None, "Forecast"),
    Measure("Forecast high (80%)", "CALCULATE ( SUM ( Forecast[upper_80] ), Forecast[kind] = \"forecast\" )",
            None, "Forecast"),
    # Data quality
    Measure("Checks passed", "CALCULATE ( COUNTROWS ( 'Data checks' ), 'Data checks'[passed] = 1 ) + 0", "0",
            "Data quality"),
    Measure("Checks run", "COUNTROWS ( 'Data checks' )", "0", "Data quality"),
    Measure("Unusual months", "COUNTROWS ( 'Unusual months' ) + 0", "0", "Data quality"),
    Measure("Source files", "COUNTROWS ( 'Load log' )", "0", "Data quality"),
    Measure("Last loaded", "FORMAT ( MAX ( 'Load log'[loaded_at] ), \"d mmm yyyy\" )", None, "Data quality"),
]

TABLES = [
    TableDef("Month", "dim_month", [
        key("month_key", "month_key"),
        Column("Month", "month_start", "dateTime", fmt="mmm yyyy"),
        Column("Month name", "month_name", "string", sort_by="month_key"),
        Column("Year", "year", "int64", fmt="0"),
        Column("Financial year", "financial_year", "string"),
        Column("Financial year month", "fy_month", "int64", fmt="0"),
        Column("Quarter", "fy_quarter", "string"),
    ], description="One row per calendar month, including six months ahead for forecasts."),
    TableDef("Region", "dim_region", [
        key("region_code", "region_code"),
        Column("Region", "region_name", "string"),
    ]),
    TableDef("Organisation", "dim_provider", [
        Column("Code", "provider_code", "string"),
        Column("Organisation", "provider_name", "string"),
        key("region_code", "region_code"),
        Column("Sector", "sector", "string"),
        Column("Major A&E", "major_ae", "string"),
        Column("In A&E data", "in_ae", "int64", hidden=True),
        Column("In RTT data", "in_rtt", "int64", hidden=True),
    ], m=(
        "let\n"
        "    Source = LoadParquet(\"dim_provider\"),\n"
        "    Flag = Table.AddColumn(Source, \"major_ae\", each if [has_type1_ae] = 1 then \"Yes\" else \"No\", type text),\n"
        "    Kept = Table.SelectColumns(Flag, {\"provider_code\", \"provider_name\", \"region_code\", \"sector\", \"major_ae\", \"in_ae\", \"in_rtt\"}),\n"
        "    Typed = Table.TransformColumnTypes(Kept, {{\"provider_code\", type text}, {\"provider_name\", type text}, "
        "{\"region_code\", type text}, {\"sector\", type text}, {\"major_ae\", type text}, {\"in_ae\", Int64.Type}, {\"in_rtt\", Int64.Type}})\n"
        "in\n"
        "    Typed"
    ), description="NHS trusts and other organisations that report A&E or RTT data."),
    TableDef("Specialty", "dim_treatment_function", [
        Column("Code", "treatment_function_code", "string"),
        Column("Specialty", "treatment_function_name", "string"),
        Column("Is total", "is_total", "int64", hidden=True),
    ], description="RTT treatment functions."),
    TableDef("A&E", "fact_ae", [
        key("month_key", "month_key"), key("provider_code", "provider_code"),
        num("attendances", "attendances"), num("attendances_type1", "attendances_type1"),
        num("over_4hr", "over_4hr"), num("over_4hr_type1", "over_4hr_type1"),
        num("within_4hr", "within_4hr"), num("reports_4hr", "reports_4hr"), num("dta_4_12", "dta_4_12"), num("dta_12_plus", "dta_12_plus"),
        num("emergency_admissions_ae", "emergency_admissions_ae"),
        num("emergency_admissions_total", "emergency_admissions_total"),
    ], description="Monthly A&E attendances and 4-hour performance by organisation."),
    TableDef("RTT", "fact_rtt_provider", [
        key("month_key", "month_key"), key("provider_code", "provider_code"),
        num("waiting_list", "waiting_list"), num("within_18", "within_18"), num("over_52", "over_52"),
        num("over_65", "over_65"), num("over_78", "over_78"), num("over_104", "over_104"),
        num("median_weeks", "median_weeks", "0.0"), num("p92_weeks", "p92_weeks", "0.0"),
        num("clock_stops", "clock_stops"), num("clock_starts", "clock_starts"),
    ], description="Month-end RTT waiting list by organisation (all specialties)."),
    TableDef("RTT specialty", "fact_rtt_specialty", [
        key("month_key", "month_key"), key("provider_code", "provider_code"),
        key("treatment_function_code", "treatment_function_code"),
        num("waiting_list", "waiting_list"), num("within_18", "within_18"), num("over_52", "over_52"),
    ], description="Month-end RTT waiting list by organisation and specialty."),
    TableDef("RTT England", "fact_rtt_england", [
        key("month_key", "month_key"), key("treatment_function_code", "treatment_function_code"),
        num("waiting_list", "waiting_list"), num("within_18", "within_18"), num("over_52", "over_52"),
        num("median_weeks", "median_weeks", "0.0"), num("p92_weeks", "p92_weeks", "0.0"),
    ], description="England RTT totals with medians from summed weekly bands."),
    TableDef("Forecast", "fact_forecast", [
        Column("series_id", "series_id", "string", hidden=True),
        Column("metric", "metric", "string", hidden=True),
        Column("Measure", "metric_label", "string"),
        Column("Organisation", "organisation", "string"),
        Column("provider_code", "provider_code", "string", hidden=True),
        key("month_key", "month_key"),
        Column("kind", "kind", "string", hidden=True),
        num("value", "value", "#,0.000"), num("lower_80", "lower_80", "#,0.000"),
        num("upper_80", "upper_80", "#,0.000"),
    ], description="Actuals and six-month forecasts with 80% ranges."),
    TableDef("Model scores", "model_backtest", [
        Column("metric", "metric", "string"),
        Column("Method", "model", "string"),
        Column("Months ahead", "horizon", "int64", fmt="0"),
        Column("Mean absolute error", "mae", "double", fmt="0.00%"),
        Column("Forecasts scored", "n", "int64", fmt="#,0"),
        Column("Used", "chosen", "int64", fmt="0"),
    ]),
    TableDef("Targets", "targets", [
        Column("metric", "metric", "string"),
        Column("financial_year", "financial_year", "string"),
        Column("target_date", "target_date", "string"),
        Column("value", "value", "double", fmt="0%"),
        Column("scope", "scope", "string"),
        Column("source", "source", "string"),
    ], hidden=True),
    TableDef("Data checks", "dq_checks", [
        Column("Run at", "run_at", "string"),
        Column("Dataset", "dataset", "string"),
        Column("Check", "check_name", "string"),
        Column("Severity", "severity", "string"),
        Column("passed", "passed", "int64", hidden=True),
        Column("Detail", "detail", "string"),
    ]),
    TableDef("Unusual months", "anomalies", [
        Column("metric", "metric", "string"),
        Column("Code", "provider_code", "string"),
        Column("month_key", "month_key", "int64"),
        Column("Value", "value", "double", fmt="#,0.000"),
        Column("Expected", "expected", "double", fmt="#,0.000"),
        Column("Robust z", "robust_z", "double", fmt="0.0"),
    ]),
    TableDef("Load log", "load_log", [
        Column("Dataset", "dataset", "string"),
        Column("Period", "period", "string"),
        Column("Source URL", "source_url", "string"),
        Column("SHA-256", "sha256", "string"),
        Column("Rows", "rows_loaded", "int64", fmt="#,0"),
        Column("loaded_at", "loaded_at", "string", hidden=True),
    ]),
]

RELATIONSHIPS = [
    ("A&E", "month_key", "Month", "month_key"),
    ("A&E", "provider_code", "Organisation", "Code"),
    ("RTT", "month_key", "Month", "month_key"),
    ("RTT", "provider_code", "Organisation", "Code"),
    ("RTT specialty", "month_key", "Month", "month_key"),
    ("RTT specialty", "provider_code", "Organisation", "Code"),
    ("RTT specialty", "treatment_function_code", "Specialty", "Code"),
    ("RTT England", "month_key", "Month", "month_key"),
    ("RTT England", "treatment_function_code", "Specialty", "Code"),
    ("Organisation", "region_code", "Region", "region_code"),
    ("Forecast", "month_key", "Month", "month_key"),
]


def partition_m(t: TableDef) -> str:
    if t.m:
        return t.m
    types = ", ".join(f'{{"{c.source}", {M_TYPES[c.type]}}}' for c in t.columns)
    return (
        "let\n"
        f"    Source = LoadParquet(\"{t.source}\"),\n"
        f"    Kept = Table.SelectColumns(Source, {{{', '.join(json.dumps(c.source) for c in t.columns)}}}),\n"
        f"    Typed = Table.TransformColumnTypes(Kept, {{{types}}})\n"
        "in\n"
        "    Typed"
    )


def indent(text: str, tabs: int) -> str:
    pad = "\t" * tabs
    return "\n".join(pad + line if line else line for line in text.splitlines())


def measure_tmdl(m: Measure) -> str:
    lines = []
    if m.description:
        lines.append(f"\t/// {m.description}")
    if "\n" in m.expression:
        lines.append(f"\tmeasure {q(m.name)} =")
        lines.append(indent(m.expression, 3))
    else:
        lines.append(f"\tmeasure {q(m.name)} = {m.expression}")
    if m.fmt:
        lines.append(f"\t\tformatString: {m.fmt}")
    if m.folder:
        lines.append(f"\t\tdisplayFolder: {m.folder}")
    lines.append(f"\t\tlineageTag: {tag('measure', m.name)}")
    return "\n".join(lines)


def table_tmdl(t: TableDef) -> str:
    out = []
    if t.description:
        out.append(f"/// {t.description}")
    out.append(f"table {q(t.name)}")
    if t.hidden:
        out.append("\tisHidden")
    out.append(f"\tlineageTag: {tag('table', t.name)}")
    out.append("")
    for m in t.measures:
        out.append(measure_tmdl(m))
        out.append("")
    for c in t.columns:
        out.append(f"\tcolumn {q(c.name)}")
        out.append(f"\t\tdataType: {c.type}")
        if c.hidden:
            out.append("\t\tisHidden")
        if c.fmt:
            out.append(f"\t\tformatString: {c.fmt}")
        out.append(f"\t\tlineageTag: {tag('column', t.name, c.name)}")
        out.append(f"\t\tsummarizeBy: {c.summarize if c.type in ('int64', 'double') else 'none'}")
        out.append(f"\t\tsourceColumn: {c.source}")
        if c.sort_by:
            sort_col = next(x.name for x in t.columns if x.source == c.sort_by)
            out.append(f"\t\tsortByColumn: {q(sort_col)}")
        out.append("")
        out.append("\t\tannotation SummarizationSetBy = Automatic")
        out.append("")
    out.append(f"\tpartition {q(t.name)} = m")
    out.append("\t\tmode: import")
    out.append("\t\tsource =")
    out.append(indent(partition_m(t), 4))
    out.append("")
    out.append("\tannotation PBI_ResultType = Table")
    return "\n".join(out) + "\n"


def measures_table() -> TableDef:
    t = TableDef(MEASURE_TABLE, None, [Column("Placeholder", "Placeholder", "string", hidden=True)],
                 measures=MEASURES, description="All report measures, grouped in folders.")
    t.m = 'let\n    Source = #table(type table [Placeholder = text], {})\nin\n    Source'
    return t


def write_model() -> None:
    if MODEL_DIR.exists():
        shutil.rmtree(MODEL_DIR)
    d = MODEL_DIR / "definition"
    (d / "tables").mkdir(parents=True)
    all_tables = [measures_table(), *TABLES]
    (MODEL_DIR / "definition.pbism").write_text(json.dumps(
        {"$schema": V_PBISM, "version": "4.2", "settings": {}}, indent=2) + "\n")
    (MODEL_DIR / ".platform").write_text(json.dumps({
        "$schema": V_PLATFORM,
        "metadata": {"type": "SemanticModel", "displayName": NAME},
        "config": {"version": "2.0", "logicalId": tag("model")},
    }, indent=2) + "\n")
    (d / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1600\n")
    order = ", ".join(json.dumps(t.name) for t in all_tables)
    (d / "model.tmdl").write_text(
        "model Model\n"
        "\tculture: en-GB\n"
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3\n"
        "\tsourceQueryCulture: en-GB\n"
        "\tdataAccessOptions\n"
        "\t\tlegacyRedirects\n"
        "\t\treturnErrorValuesAsNull\n\n"
        f"annotation PBI_QueryOrder = [\"DataRoot\", \"LoadParquet\", {order}]\n\n"
        "annotation __PBI_TimeIntelligenceEnabled = 0\n\n"
        + "".join(f"ref table {q(t.name)}\n" for t in all_tables)
    )
    (d / "expressions.tmdl").write_text(
        f"/// Folder URL the pipeline publishes Parquet files to (the repo's data branch).\n"
        f"expression DataRoot = \"{DATA_ROOT}\" meta [IsParameterQuery=true, Type=\"Text\", "
        "IsParameterQueryRequired=true]\n"
        f"\tlineageTag: {tag('expr', 'DataRoot')}\n\n"
        "\tannotation PBI_ResultType = Text\n\n"
        "/// Read one published Parquet file. RelativePath keeps the source static so the\n"
        "/// Power BI service can refresh it on a schedule without a gateway.\n"
        "expression LoadParquet =\n"
        "\t\t(name as text) as table =>\n"
        "\t\tlet\n"
        "\t\t    Bytes = Web.Contents(DataRoot, [RelativePath = name & \".parquet\"]),\n"
        "\t\t    Table = Parquet.Document(Bytes)\n"
        "\t\tin\n"
        "\t\t    Table\n"
        f"\tlineageTag: {tag('expr', 'LoadParquet')}\n\n"
        "\tannotation PBI_ResultType = Function\n"
    )
    rel = []
    for from_t, from_c, to_t, to_c in RELATIONSHIPS:
        def col_name(table: str, source_or_name: str) -> str:
            t = next(x for x in TABLES if x.name == table)
            c = next((c for c in t.columns if c.name == source_or_name), None)
            return c.name if c else source_or_name
        rel.append(
            f"relationship {tag('rel', from_t, from_c)}\n"
            f"\tfromColumn: {q(from_t)}.{q(col_name(from_t, from_c))}\n"
            f"\ttoColumn: {q(to_t)}.{q(col_name(to_t, to_c))}\n"
        )
    (d / "relationships.tmdl").write_text("\n".join(rel))
    for t in all_tables:
        safe = t.name.replace("&", "and").replace(" ", "_")
        (d / "tables" / f"{safe}.tmdl").write_text(table_tmdl(t))


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def lit(value) -> dict:
    if isinstance(value, bool):
        v = "true" if value else "false"
    elif isinstance(value, (int, float)):
        v = f"{value}D"
    else:
        v = "'" + str(value).replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": v}}}


def color(hex_code: str) -> dict:
    return {"solid": {"color": lit(hex_code)}}


def field_ref(entity: str, prop: str, kind: str) -> dict:
    return {kind: {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def proj(entity: str, prop: str, kind: str = "Measure", display: str | None = None) -> dict:
    p = {"field": field_ref(entity, prop, kind), "queryRef": f"{entity}.{prop}", "nativeQueryRef": prop}
    if display:
        p["displayName"] = display
    return p


def M(prop: str, display: str | None = None) -> dict:
    return proj(MEASURE_TABLE, prop, "Measure", display)


def C(entity: str, prop: str, display: str | None = None) -> dict:
    return proj(entity, prop, "Column", display)


def container(title: str | None, subtitle: str | None = None, border: bool = True) -> dict:
    objs: dict = {
        "background": [{"properties": {"show": lit(True), "color": color("#FFFFFF"),
                                       "transparency": lit(0)}}],
        "border": [{"properties": {"show": lit(border), "color": color("#D8DDE0"),
                                   "radius": lit(6)}}],
        "dropShadow": [{"properties": {"show": lit(False)}}],
    }
    if title:
        objs["title"] = [{"properties": {"show": lit(True), "text": lit(title),
                                         "fontColor": color(NHS_DARK), "fontSize": lit(12),
                                         "bold": lit(True)}}]
    else:
        objs["title"] = [{"properties": {"show": lit(False)}}]
    if subtitle:
        objs["subTitle"] = [{"properties": {"show": lit(True), "text": lit(subtitle),
                                            "fontColor": color(NHS_GREY), "fontSize": lit(9)}}]
    return objs


@dataclass
class Visual:
    name: str
    type: str
    x: int
    y: int
    w: int
    h: int
    roles: dict[str, list[dict]] = field(default_factory=dict)
    title: str | None = None
    subtitle: str | None = None
    objects: dict = field(default_factory=dict)
    filters: list[dict] = field(default_factory=list)
    sort: list[dict] | None = None


def card(name, x, y, measure, title, subtitle=None, w=196, h=110, fmt_color=NHS_DARK) -> Visual:
    return Visual(name, "card", x, y, w, h, {"Values": [M(measure)]}, title, subtitle, {
        "labels": [{"properties": {"color": color(fmt_color), "fontSize": lit(26)}}],
        "categoryLabels": [{"properties": {"show": lit(False)}}],
    })


def text(name, x, y, w, h, title, subtitle) -> Visual:
    runs = [{"textRuns": [{"value": title, "textStyle": {"fontSize": "20pt", "fontWeight": "bold",
                                                         "color": NHS_DARK}}]}]
    if subtitle:
        runs.append({"textRuns": [{"value": subtitle, "textStyle": {"fontSize": "10pt",
                                                                    "color": NHS_GREY}}]})
    v = Visual(name, "textbox", x, y, w, h, objects={"general": [{"properties": {"paragraphs": runs}}]})
    return v


def slicer(name, x, y, w, entity, prop, title, dropdown=True, single=False, default=None) -> Visual:
    objs = {"data": [{"properties": {"mode": lit("Dropdown" if dropdown else "Basic")}}],
            "header": [{"properties": {"show": lit(True), "fontColor": color(NHS_DARK)}}]}
    if single:
        objs["selection"] = [{"properties": {"strictSingleSelect": lit(True)}}]
    if default is not None:
        f = basic_filter(entity, prop, [default])["filter"]
        objs["general"] = [{"properties": {"filter": {"filter": f}}}]
    return Visual(name, "slicer", x, y, w, 64, {"Values": [C(entity, prop)]}, None, None, objs)


def line(name, x, y, w, h, category, measures, title, subtitle=None, colors=None, pct=False) -> Visual:
    objs: dict = {"legend": [{"properties": {"show": lit(True), "position": lit("Top")}}]}
    if colors:
        objs["dataPoint"] = [
            {"properties": {"fill": color(c)}, "selector": {"metadata": f"{MEASURE_TABLE}.{m}"}}
            for m, c in zip(measures, colors, strict=True)
        ]
    if pct:
        objs["valueAxis"] = [{"properties": {"labelDisplayUnits": lit(0)}}]
    return Visual(name, "lineChart", x, y, w, h,
                  {"Category": [C(*category)], "Y": [M(m) for m in measures]}, title, subtitle, objs)


def bar(name, x, y, w, h, category, measure, title, subtitle=None, kind="clusteredBarChart",
        sort_desc=True) -> Visual:
    v = Visual(name, kind, x, y, w, h, {"Category": [C(*category)], "Y": [M(measure)]}, title, subtitle,
               {"dataPoint": [{"properties": {"fill": color(NHS_BLUE)}}],
                "labels": [{"properties": {"show": lit(True)}}]})
    v.sort = [{"field": field_ref(MEASURE_TABLE, measure, "Measure"),
               "direction": "Descending" if sort_desc else "Ascending"}]
    return v


def table(name, x, y, w, h, columns, measures, title, subtitle=None, sort_measure=None, desc=True) -> Visual:
    v = Visual(name, "tableEx", x, y, w, h,
               {"Values": [C(*c) for c in columns] + [M(m) for m in measures]}, title, subtitle,
               {"grid": [{"properties": {"rowPadding": lit(3)}}],
                "columnHeaders": [{"properties": {"fontColor": color("#FFFFFF"),
                                                  "backColor": color(NHS_BLUE), "bold": lit(True)}}]})
    if sort_measure:
        v.sort = [{"field": field_ref(MEASURE_TABLE, sort_measure, "Measure"),
                   "direction": "Descending" if desc else "Ascending"}]
    return v


def col_table(name, x, y, w, h, columns, title, subtitle=None) -> Visual:
    return Visual(name, "tableEx", x, y, w, h, {"Values": [C(*c) for c in columns]}, title, subtitle,
                  {"columnHeaders": [{"properties": {"fontColor": color("#FFFFFF"),
                                                     "backColor": color(NHS_BLUE), "bold": lit(True)}}]})


def basic_filter(entity: str, prop: str, values: list[str], kind: str = "Column") -> dict:
    alias = "t"
    return {
        "name": tag("filter", entity, prop, *values)[:20].replace("-", ""),
        "field": field_ref(entity, prop, kind),
        "type": "Categorical",
        "filter": {
            "Version": 2,
            "From": [{"Name": alias, "Entity": entity, "Type": 0}],
            "Where": [{"Condition": {"In": {
                "Expressions": [{kind: {"Expression": {"SourceRef": {"Source": alias}}, "Property": prop}}],
                "Values": [[{"Literal": {"Value": "'" + v.replace("'", "''") + "'"}}] for v in values],
            }}}],
        },
        "howCreated": "User",
    }


def visual_json(v: Visual, z: int) -> dict:
    body: dict = {"visualType": v.type}
    if v.roles:
        body["query"] = {"queryState": {r: {"projections": p} for r, p in v.roles.items()}}
        if v.sort:
            body["query"]["sortDefinition"] = {"sort": v.sort, "isDefaultSort": True}
    if v.objects:
        body["objects"] = v.objects
    if v.type != "textbox":
        body["visualContainerObjects"] = container(v.title, v.subtitle, border=v.type != "slicer")
    body["drillFilterOtherVisuals"] = True
    out = {
        "$schema": V_VISUAL,
        "name": v.name,
        "position": {"x": v.x, "y": v.y, "z": z * 1000, "height": v.h, "width": v.w, "tabOrder": z * 1000},
        "visual": body,
    }
    if v.filters:
        out["filterConfig"] = {"filters": v.filters}
    return out


W = 1280
GAP = 16


def header(page: str, title: str, subtitle: str) -> list[Visual]:
    return [text(f"{page}Title", GAP, 8, 860, 64, title, subtitle)]


def pages() -> list[tuple[str, str, list[Visual]]]:
    out = []

    # 1. Overview
    v = header("overview", "NHS England performance",
               "A&E 4-hour standard and elective (RTT) waiting times. Latest month in view; "
               "filter by region or financial year.")
    v += [slicer("overviewRegion", 900, 8, 175, "Region", "Region", "Region"),
          slicer("overviewFy", 1090, 8, 175, "Month", "Financial year", "Financial year")]
    cw, y = 196, 84
    v += [
        card("kpiAe", GAP, y, "A&E 4hr % (latest)", "A&E within 4 hours", "Latest month", cw),
        card("kpiAeTarget", GAP + (cw + 12), y, "A&E target (latest)", "4-hour target", "This financial year",
             cw, fmt_color=NHS_GREY),
        card("kpiAeYoY", GAP + 2 * (cw + 12), y, "A&E 4hr change on year", "Change on a year ago",
             "Percentage points", cw),
        card("kpiRtt", GAP + 3 * (cw + 12), y, "RTT 18wk %", "Waiting under 18 weeks", "Latest month", cw),
        card("kpiWl", GAP + 4 * (cw + 12), y, "Waiting list", "RTT waiting list", "Latest month", cw),
        card("kpi52", GAP + 5 * (cw + 12), y, "Over 52 weeks", "Waiting over 52 weeks", "Latest month", cw,
             fmt_color=NHS_RED),
        line("overviewAeTrend", GAP, 210, 616, 250, ("Month", "Month"), ["A&E 4hr %", "A&E target"],
             "A&E: seen within 4 hours", "Monthly, against the national target", [NHS_BLUE, NHS_WARM]),
        line("overviewRttTrend", 648, 210, 616, 250, ("Month", "Month"), ["RTT 18wk %", "RTT target"],
             "RTT: waiting under 18 weeks", "Month end, against the national ambition", [NHS_BLUE, NHS_WARM]),
        bar("overviewRegionBar", GAP, 476, 400, 228, ("Region", "Region"), "A&E 4hr % (latest)",
            "A&E within 4 hours by region", "Latest month"),
        line("overviewWl", 432, 476, 416, 228, ("Month", "Month"), ["Waiting list"], "RTT waiting list",
             "Patients waiting at month end", [NHS_BLUE]),
        line("overviewTrolley", 864, 476, 400, 228, ("Month", "Month"), ["12hr trolley waits"],
             "12-hour trolley waits", "Decision to admit to admission over 12 hours", [NHS_RED]),
    ]
    out.append(("overview", "Overview", v))

    # 2. A&E
    v = header("ae", "A&E: 4-hour standard", "Major emergency departments ranked on the latest month.")
    v += [slicer("aeRegion", 900, 8, 175, "Region", "Region", "Region"),
          slicer("aeFy", 1090, 8, 175, "Month", "Financial year", "Financial year")]
    t = table("aeLeague", GAP, 84, 800, 620,
              [("Organisation", "Organisation"), ("Region", "Region")],
              ["A&E rank", "A&E 4hr % (latest)", "A&E 4hr % (year before)", "A&E 4hr change on year",
               "Attendances (latest)", "12hr trolley waits (latest)"],
              "League table", "Trusts with a major A&E. Latest month in view.", "A&E 4hr % (latest)")
    t.filters = [basic_filter("Organisation", "Major A&E", ["Yes"])]
    v += [t,
          card("aeMeeting", 832, 84, "Trusts meeting 4hr target", "Trusts meeting target", "Latest month", 210),
          card("aeMonth", 1054, 84, "Latest A&E month", "Data to", None, 210, fmt_color=NHS_GREY),
          line("aeAttend", 832, 210, 432, 240, ("Month", "Month"), ["Attendances"], "Attendances",
               "All A&E types", [NHS_BLUE]),
          line("aeType1", 832, 464, 432, 240, ("Month", "Month"), ["A&E 4hr %", "Type 1 4hr %"],
               "All types vs major A&E", "Seen within 4 hours", [NHS_BLUE, NHS_DARK])]
    out.append(("ae", "A&E", v))

    # 3. Waiting lists
    v = header("rtt", "Elective waiting lists (RTT)",
               "Referral to treatment: month-end waiting list, 18-week performance and long waits.")
    v += [slicer("rttRegion", 900, 8, 175, "Region", "Region", "Region"),
          slicer("rttSpecialty", 1090, 8, 175, "Specialty", "Specialty", "Specialty")]
    v += [card("rttMedian", GAP, 84, "Median wait (weeks)", "Median wait (weeks)", "England or one trust", 196),
          card("rttChange", 224, 84, "Waiting list change on year", "Change in list on a year ago", None, 196),
          card("rttMonth", 432, 84, "Latest RTT month", "Data to", None, 196, fmt_color=NHS_GREY)]
    t = table("rttLeague", GAP, 210, 812, 494,
              [("Organisation", "Organisation"), ("Region", "Region")],
              ["RTT rank", "Waiting list", "RTT 18wk %", "RTT 18wk change on year", "Over 52 weeks",
               "Share over 52 weeks", "Median wait (weeks)"],
              "League table", "NHS trusts, latest month in view.", "RTT 18wk %")
    t.filters = [basic_filter("Organisation", "Sector", ["NHS trust"])]
    v += [t,
          bar("rttSpecialtyBar", 844, 84, 420, 330, ("Specialty", "Specialty"), "Specialty waiting list",
              "Waiting list by specialty", "Latest month"),
          line("rtt52", 844, 430, 420, 274, ("Month", "Month"), ["Over 52 weeks", "Over 65 weeks"],
               "Long waits", "Patients waiting over 52 and 65 weeks", [NHS_RED, NHS_WARM])]
    v[-2].filters = [basic_filter("Specialty", "Is total", ["0"])]
    v[-2].filters[0]["filter"]["Where"][0]["Condition"]["In"]["Values"] = [[{"Literal": {"Value": "0L"}}]]
    out.append(("rtt", "Waiting lists", v))

    # 4. Trust view
    v = header("trust", "Trust view", "Pick one organisation to see its trends.")
    v += [slicer("trustPick", 900, 8, 365, "Organisation", "Organisation", "Organisation", single=True,
                 default=DEFAULT_TRUST)]
    v += [card("trustAe", GAP, 84, "A&E 4hr % (latest)", "A&E within 4 hours", "Latest month", 240),
          card("trustAeRank", 268, 84, "A&E rank", "A&E rank", "Among major A&Es", 240),
          card("trustWl", 520, 84, "Waiting list", "RTT waiting list", "Latest month", 240),
          card("trustRtt", 772, 84, "RTT 18wk %", "Waiting under 18 weeks", "Latest month", 240),
          card("trustMedian", 1024, 84, "Median wait (weeks)", "Median wait (weeks)", "Latest month", 240),
          line("trustAeTrend", GAP, 210, 616, 240, ("Month", "Month"), ["A&E 4hr %", "A&E target"],
               "A&E within 4 hours", None, [NHS_BLUE, NHS_WARM]),
          line("trustRttTrend", 648, 210, 616, 240, ("Month", "Month"), ["RTT 18wk %", "RTT target"],
               "RTT waiting under 18 weeks", None, [NHS_BLUE, NHS_WARM]),
          bar("trustSpecialty", GAP, 464, 616, 240, ("Specialty", "Specialty"), "Specialty over 52 weeks",
              "Over 52 weeks by specialty", "Latest month"),
          line("trustWlTrend", 648, 464, 616, 240, ("Month", "Month"), ["Waiting list"], "Waiting list", None,
               [NHS_BLUE])]
    v[-2].filters = [basic_filter("Specialty", "Is total", ["0"])]
    v[-2].filters[0]["filter"]["Where"][0]["Condition"]["In"]["Values"] = [[{"Literal": {"Value": "0L"}}]]
    out.append(("trust", "Trust view", v))

    # 5. Forecast
    v = header("forecast", "Forecasts", "Six months ahead with an 80% range from a rolling backtest.")
    v += [slicer("forecastMeasure", 700, 8, 280, "Forecast", "Measure", "Measure", single=True,
                 default="A&E seen within 4 hours"),
          slicer("forecastOrg", 990, 8, 275, "Forecast", "Organisation", "Organisation", single=True,
                 default="England")]
    v += [line("forecastChart", GAP, 84, 1248, 380, ("Month", "Month"),
               ["Actual", "Forecast", "Forecast low (80%)", "Forecast high (80%)"],
               "Actual and forecast", "Pick one measure and one organisation above",
               [NHS_BLUE, NHS_WARM, "#AEB7BD", "#AEB7BD"]),
          col_table("forecastScores", GAP, 480, 1248, 224,
                    [("Model scores", "metric"), ("Model scores", "Method"), ("Model scores", "Months ahead"),
                     ("Model scores", "Mean absolute error"), ("Model scores", "Forecasts scored"),
                     ("Model scores", "Used")],
                    "How each method scored", "Mean absolute error over the last six backtest origins; "
                    "the method used has Used = 1")]
    out.append(("forecast", "Forecast", v))

    # 6. Data quality
    v = header("quality", "Data quality and sources", "Checks run on every load, unusual months, file log.")
    v += [card("qPassed", GAP, 84, "Checks passed", "Checks passed", None, 240, fmt_color=NHS_GREEN),
          card("qRun", 268, 84, "Checks run", "Checks run", None, 240, fmt_color=NHS_GREY),
          card("qUnusual", 520, 84, "Unusual months", "Unusual months flagged", None, 240, fmt_color=NHS_WARM),
          card("qFiles", 772, 84, "Source files", "Source files loaded", None, 240, fmt_color=NHS_GREY),
          card("qLoaded", 1024, 84, "Last loaded", "Last loaded", None, 240, fmt_color=NHS_GREY),
          col_table("qChecks", GAP, 210, 620, 494,
                    [("Data checks", "Dataset"), ("Data checks", "Check"), ("Data checks", "Severity"),
                     ("Data checks", "Detail")], "Checks", "An 'error' check that fails stops the pipeline"),
          col_table("qAnomalies", 652, 210, 612, 240,
                    [("Unusual months", "metric"), ("Unusual months", "Code"), ("Unusual months", "month_key"),
                     ("Unusual months", "Value"), ("Unusual months", "Expected"), ("Unusual months", "Robust z")],
                    "Unusual months", "Month-on-month change far outside the organisation's normal range"),
          col_table("qLog", 652, 464, 612, 240,
                    [("Load log", "Dataset"), ("Load log", "Period"), ("Load log", "Rows"),
                     ("Load log", "Source URL")], "Source files", "NHS England files loaded, with row counts")]
    out.append(("quality", "Data quality", v))
    return out


THEME = {
    "name": "NHS",
    "dataColors": [NHS_BLUE, NHS_WARM, NHS_DARK, NHS_GREEN, "#330072", NHS_RED, "#00A499", NHS_GREY],
    "background": "#FFFFFF",
    "foreground": "#212B32",
    "tableAccent": NHS_BLUE,
    "good": NHS_GREEN,
    "neutral": NHS_WARM,
    "bad": NHS_RED,
    "textClasses": {
        "label": {"fontFace": "Segoe UI", "color": "#212B32", "fontSize": 10},
        "title": {"fontFace": "Segoe UI Semibold", "color": NHS_DARK, "fontSize": 12},
        "header": {"fontFace": "Segoe UI Semibold", "color": NHS_DARK, "fontSize": 12},
        "callout": {"fontFace": "Segoe UI Semibold", "color": NHS_DARK, "fontSize": 26},
    },
    "visualStyles": {"page": {"*": {"background": [{"color": {"solid": {"color": "#F0F4F5"}},
                                                     "transparency": 0}]}}},
}


def write_report(base_theme: Path) -> None:
    if REPORT_DIR.exists():
        shutil.rmtree(REPORT_DIR)
    d = REPORT_DIR / "definition"
    (d / "pages").mkdir(parents=True)
    (REPORT_DIR / "definition.pbir").write_text(json.dumps({
        "$schema": V_PBIR, "version": "4.0",
        "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}},
    }, indent=2) + "\n")
    (REPORT_DIR / ".platform").write_text(json.dumps({
        "$schema": V_PLATFORM,
        "metadata": {"type": "Report", "displayName": NAME},
        "config": {"version": "2.0", "logicalId": tag("report")},
    }, indent=2) + "\n")
    shared = REPORT_DIR / "StaticResources" / "SharedResources" / "BaseThemes"
    shared.mkdir(parents=True)
    shutil.copy(base_theme, shared / "CY24SU10.json")
    reg = REPORT_DIR / "StaticResources" / "RegisteredResources"
    reg.mkdir(parents=True)
    (reg / "NHS.json").write_text(json.dumps(THEME, indent=2) + "\n")
    (d / "version.json").write_text(json.dumps({"$schema": V_VERSION, "version": "2.0.0"}, indent=2) + "\n")
    (d / "report.json").write_text(json.dumps({
        "$schema": V_REPORT,
        "themeCollection": {
            "baseTheme": {"name": "CY24SU10",
                          "reportVersionAtImport": {"visual": "1.8.95", "report": "2.0.95", "page": "1.3.95"},
                          "type": "SharedResources"},
            "customTheme": {"name": "NHS.json",
                            "reportVersionAtImport": {"visual": "2.1.0", "report": "2.1.0", "page": "2.0.0"},
                            "type": "RegisteredResources"},
        },
        "resourcePackages": [
            {"name": "SharedResources", "type": "SharedResources",
             "items": [{"name": "CY24SU10", "path": "BaseThemes/CY24SU10.json", "type": "BaseTheme"}]},
            {"name": "RegisteredResources", "type": "RegisteredResources",
             "items": [{"name": "NHS.json", "path": "NHS.json", "type": "CustomTheme"}]},
        ],
        "settings": {"useStylableVisualContainerHeader": True, "exportDataMode": "AllowSummarized",
                     "defaultDrillFilterOtherVisuals": True, "allowChangeFilterTypes": True,
                     "useEnhancedTooltips": True, "useDefaultAggregateDisplayName": True},
    }, indent=2) + "\n")
    order = []
    for name, display, visuals in pages():
        order.append(name)
        pd_ = d / "pages" / name
        (pd_ / "visuals").mkdir(parents=True)
        (pd_ / "page.json").write_text(json.dumps({
            "$schema": V_PAGE, "name": name, "displayName": display, "displayOption": "FitToPage",
            "height": 720, "width": W,
        }, indent=2) + "\n")
        names = set()
        for z, v in enumerate(visuals):
            assert v.name not in names, v.name
            names.add(v.name)
            vd = pd_ / "visuals" / v.name
            vd.mkdir()
            (vd / "visual.json").write_text(json.dumps(visual_json(v, z), indent=2) + "\n")
    (d / "pages" / "pages.json").write_text(json.dumps(
        {"$schema": V_PAGES, "pageOrder": order, "activePageName": order[0]}, indent=2) + "\n")


def write_pbip() -> None:
    (ROOT / f"{NAME}.pbip").write_text(json.dumps({
        "$schema": V_PBIP, "version": "1.0",
        "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
        "settings": {"enableAutoRecovery": True},
    }, indent=2) + "\n")
    (ROOT / ".gitignore").write_text("**/.pbi/localSettings.json\n**/.pbi/cache.abf\n")


def main() -> None:
    base_theme = ROOT / "base-theme" / "CY24SU10.json"
    write_model()
    write_report(base_theme)
    write_pbip()
    print(f"wrote {MODEL_DIR.name}, {REPORT_DIR.name} and {NAME}.pbip")


if __name__ == "__main__":
    main()
