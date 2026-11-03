"""Load the cleaned tables into a SQLite star schema and check them."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

REGIONS = {
    "LON": "London",
    "SE": "South East",
    "SW": "South West",
    "EOE": "East of England",
    "MID": "Midlands",
    "NEY": "North East and Yorkshire",
    "NW": "North West",
}
REGION_WORDS = {
    "LONDON": "LON",
    "SOUTH EAST": "SE",
    "SOUTH WEST": "SW",
    "EAST OF ENGLAND": "EOE",
    "MIDLANDS": "MID",
    "NORTH EAST": "NEY",
    "NORTH WEST": "NW",
}


def region_code(name: object) -> str | None:
    """'NHS ENGLAND NORTH EAST AND YORKSHIRE' -> 'NEY'."""
    if not isinstance(name, str):
        return None
    upper = name.upper()
    for words, code in REGION_WORDS.items():
        if words in upper:
            return code
    return None


def sector(code: str, name: str) -> str:
    """NHS trusts have 3-character codes starting R or T (e.g. RJ1, TAJ)."""
    if len(code) == 3 and code[0] in "RT":
        return "NHS trust"
    if "NHS" in str(name).upper():
        return "Other NHS"
    return "Independent sector"


def month_key(period: str) -> int:
    return int(period.replace("-", ""))


def dim_month(periods: list[str], extra_months: int = 0) -> pd.DataFrame:
    """Calendar rows for every period seen plus `extra_months` ahead for forecasts."""
    first, last = pd.Period(min(periods), "M"), pd.Period(max(periods), "M") + extra_months
    rows = []
    for p in pd.period_range(first, last, freq="M"):
        fy_start = p.year if p.month >= 4 else p.year - 1
        fy_month = (p.month - 4) % 12 + 1
        rows.append({
            "month_key": p.year * 100 + p.month,
            "month_start": p.start_time.date().isoformat(),
            "year": p.year,
            "month": p.month,
            "month_name": p.strftime("%b %Y"),
            "financial_year": f"{fy_start}-{(fy_start + 1) % 100:02d}",
            "fy_month": fy_month,
            "fy_quarter": f"Q{(fy_month - 1) // 3 + 1}",
        })
    return pd.DataFrame(rows)


def dim_provider(ae: pd.DataFrame, rtt: pd.DataFrame) -> pd.DataFrame:
    """One row per organisation, latest name wins, region from A&E then RTT."""
    a = ae.sort_values("period").groupby("provider_code").agg(
        provider_name=("provider_name", "last"),
        region_name=("region_name", "last"),
        has_type1=("attendances_type1", lambda s: int((s > 0).any())),
        first_ae=("period", "min"),
        last_ae=("period", "max"),
    )
    r = rtt.sort_values("period").groupby("provider_code").agg(
        rtt_name=("provider_name", "last"),
        rtt_parent=("provider_parent_name", "last"),
        first_rtt=("period", "min"),
        last_rtt=("period", "max"),
    )
    d = a.join(r, how="outer")
    d["provider_name"] = d["provider_name"].fillna(d["rtt_name"])
    d["region_code"] = d["region_name"].map(region_code)
    d["region_code"] = d["region_code"].fillna(d["rtt_parent"].map(region_code))
    d["sector"] = [sector(c, n) for c, n in zip(d.index, d["provider_name"], strict=True)]
    d["has_type1_ae"] = d["has_type1"].fillna(0).astype(int)
    d["in_ae"] = d["first_ae"].notna().astype(int)
    d["in_rtt"] = d["first_rtt"].notna().astype(int)
    first = d[["first_ae", "first_rtt"]].min(axis=1)
    last = d[["last_ae", "last_rtt"]].max(axis=1)
    d["first_month"] = first.map(month_key)
    d["last_month"] = last.map(month_key)
    d = d.reset_index().rename(columns={"index": "provider_code"})
    return d[[
        "provider_code", "provider_name", "region_code", "sector", "has_type1_ae",
        "in_ae", "in_rtt", "first_month", "last_month",
    ]]


@dataclass
class Check:
    dataset: str
    name: str
    severity: str
    passed: bool
    detail: str = ""


def quality_checks(ae: pd.DataFrame, rtt: pd.DataFrame, rtt_eng: pd.DataFrame) -> list[Check]:
    """Checks run before anything is loaded. Any failed 'error' stops the build."""
    checks: list[Check] = []

    def add(dataset: str, name: str, severity: str, ok: bool, detail: str = "") -> None:
        checks.append(Check(dataset, name, severity, bool(ok), detail))

    for name, df, key in (
        ("ae", ae, ["period", "provider_code"]),
        ("rtt", rtt, ["period", "provider_code", "treatment_function_code", "part"]),
    ):
        dupes = int(df.duplicated(key).sum())
        add(name, "no duplicate keys", "error", dupes == 0, f"{dupes} duplicate rows")
        months = pd.PeriodIndex(sorted(df["period"].unique()), freq="M")
        expected = pd.period_range(months.min(), months.max(), freq="M")
        missing = sorted(set(expected.astype(str)) - set(months.astype(str)))
        add(name, "no missing months", "error", not missing,
            f"{len(months)} months {months.min()} to {months.max()}; missing: {missing or 'none'}")
        counts = df.groupby("period")["provider_code"].nunique()
        median = counts.median()
        low = counts[counts < 0.8 * median]
        add(name, "provider count stable", "warning", low.empty,
            f"median {median:.0f} providers a month; low months: {dict(low) or 'none'}")

    numeric = ae.select_dtypes("number")
    add("ae", "no negative counts", "error", (numeric >= 0).all().all())
    bad = int((ae["over_4hr"] > ae["attendances"]).sum())
    add("ae", "over 4 hours <= attendances", "error", bad == 0, f"{bad} rows")
    bad = int((ae["dta_12_plus"] > ae["emergency_admissions_total"]).sum())
    add("ae", "12-hour waits <= emergency admissions", "warning", bad == 0, f"{bad} rows")
    nat = ae.groupby("period")["attendances"].sum()
    swing = nat.pct_change().abs().max()
    add("ae", "England attendances month-on-month change under 25%", "warning",
        bool(swing < 0.25) if pd.notna(swing) else True, f"largest change {swing:.1%}")

    inc = rtt[(rtt["part"] == "incomplete") & (rtt["treatment_function_code"] == "C_999")]
    add("rtt", "every provider has a total row", "error",
        inc.groupby("period")["provider_code"].nunique().min() > 0)
    by_tf = rtt[(rtt["part"] == "incomplete") & (rtt["treatment_function_code"] != "C_999")]
    lhs = by_tf.groupby("period")["total"].sum()
    rhs = inc.groupby("period")["total"].sum()
    gap = ((lhs - rhs).abs() / rhs).max()
    add("rtt", "treatment functions add up to total", "error", bool(gap < 0.001),
        f"largest gap {gap:.4%}")
    eng = rtt_eng[(rtt_eng["part"] == "incomplete") & (rtt_eng["treatment_function_code"] == "C_999")]
    wl = eng.set_index("period")["total"].sort_index()
    swing = wl.pct_change().abs().max()
    add("rtt", "England waiting list month-on-month change under 5%", "warning",
        bool(swing < 0.05) if pd.notna(swing) else True,
        f"latest {wl.iloc[-1]:,.0f}; largest change {swing:.2%}")
    return checks


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def sql_text(name: str) -> str:
    return resources.files("nhs_pipeline").joinpath("sql", name).read_text()


def load(
    db_path: Path,
    ae: pd.DataFrame,
    rtt: pd.DataFrame,
    rtt_eng: pd.DataFrame,
    targets: pd.DataFrame,
    manifest: list[dict],
    forecast_months: int = 6,
) -> list[Check]:
    """Rebuild the warehouse from scratch. Raises if an 'error' check fails."""
    checks = quality_checks(ae, rtt, rtt_eng)
    failed = [c for c in checks if c.severity == "error" and not c.passed]
    for c in checks:
        log.log(logging.INFO if c.passed else logging.WARNING, "%s: %s - %s %s",
                c.dataset, c.name, "pass" if c.passed else "FAIL", c.detail)
    if failed:
        raise RuntimeError("data quality checks failed: " + "; ".join(c.name for c in failed))

    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = connect(tmp)
    try:
        con.executescript(sql_text("schema.sql"))
        periods = sorted(set(ae["period"]) | set(rtt["period"]))
        frames = {
            "dim_month": dim_month(periods, forecast_months),
            "dim_region": pd.DataFrame(
                [{"region_code": k, "region_name": v} for k, v in REGIONS.items()]
            ),
            "dim_provider": dim_provider(ae, rtt),
            "dim_treatment_function": (
                rtt.sort_values("period")
                .groupby("treatment_function_code")["treatment_function_name"].last()
                .reset_index()
                .assign(is_total=lambda d: (d["treatment_function_code"] == "C_999").astype(int))
            ),
            "fact_ae": ae.assign(month_key=ae["period"].map(month_key)).drop(
                columns=["period", "provider_name", "region_name"]
            ),
            "fact_rtt": rtt.assign(month_key=rtt["period"].map(month_key)).drop(
                columns=["period", "provider_name", "provider_parent_name",
                         "treatment_function_name"]
            ),
            "fact_rtt_england": rtt_eng.assign(month_key=rtt_eng["period"].map(month_key)).drop(
                columns=["period", "treatment_function_name"]
            ),
            "targets": targets,
        }
        for table, frame in frames.items():
            frame.to_sql(table, con, if_exists="append", index=False)
            log.info("loaded %s: %d rows", table, len(frame))

        now = datetime.now(UTC).isoformat(timespec="seconds")
        rows = {"ae": ae.groupby("period").size(), "rtt": rtt.groupby("period").size()}
        con.executemany(
            "INSERT INTO load_log VALUES (?, ?, ?, ?, ?, ?)",
            [
                (e["dataset"], e["period"], e["url"], e["sha256"],
                 int(rows[e["dataset"]].get(e["period"], 0)), now)
                for e in manifest
            ],
        )
        con.executemany(
            "INSERT INTO dq_checks VALUES (?, ?, ?, ?, ?, ?)",
            [(now, c.dataset, c.name, c.severity, int(c.passed), c.detail) for c in checks],
        )
        con.executescript(sql_text("views.sql"))
        con.commit()
    finally:
        con.close()
    tmp.replace(db_path)
    return checks


def query(db_path: Path, sql: str, params: tuple = ()) -> pd.DataFrame:
    con = connect(db_path)
    try:
        return pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()
