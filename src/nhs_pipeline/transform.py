"""Clean the raw NHS England files into tidy tables.

Column names in the published files drift a little between years (spacing,
capitals, the odd typo such as "4-12 hs"), so every column is matched on a
normalised name and each table has a fixed output schema.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


class SchemaError(ValueError):
    """A raw file is missing a column the pipeline needs."""


def norm(name: str) -> str:
    """'A&E attendances  Type 1' -> 'ae attendances type 1'."""
    text = str(name).lower().replace("&", "").replace("+", " plus ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def to_number(series: pd.Series) -> pd.Series:
    """Published files use '-', '*' or blanks for nothing/suppressed."""
    cleaned = (
        series.astype("string")
        .str.replace(",", "", regex=False)
        .str.strip()
        .replace({"-": pd.NA, "*": pd.NA, "": pd.NA, "..": pd.NA})
    )
    return pd.to_numeric(cleaned, errors="coerce")


def pick(columns: dict[str, str], *patterns: str, required: bool = True) -> str | None:
    """Find the raw column whose normalised name matches one of the regexes."""
    for pattern in patterns:
        regex = re.compile(pattern)
        hits = [raw for key, raw in columns.items() if regex.fullmatch(key)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise SchemaError(f"pattern {pattern!r} matches several columns: {hits}")
    if required:
        raise SchemaError(f"no column matches {patterns}")
    return None


# ---------------------------------------------------------------------------
# A&E
# ---------------------------------------------------------------------------

AE_MEASURES: dict[str, tuple[str, ...]] = {
    "att_type1": (r"ae attendances type 1",),
    "att_type2": (r"ae attendances type 2",),
    "att_other": (r"ae attendances other ae department", r"ae attendances other department"),
    "att_booked_type1": (r"ae attendances booked appointments type 1",),
    "att_booked_type2": (r"ae attendances booked appointments type 2",),
    "att_booked_other": (r"ae attendances booked appointments other (ae )?department",),
    "over4_type1": (r"attendances over 4hrs type 1",),
    "over4_type2": (r"attendances over 4hrs type 2",),
    "over4_other": (r"attendances over 4hrs other (ae )?department",),
    "over4_booked_type1": (r"attendances over 4hrs booked appointments type 1",),
    "over4_booked_type2": (r"attendances over 4hrs booked appointments type 2",),
    "over4_booked_other": (r"attendances over 4hrs booked appointments other (ae )?department",),
    "dta_4_12": (r"patients who have waited 4 12 h(r)?s from dta to admission",),
    "dta_12_plus": (r"patients who have waited 12 plus h(r)?s from dta to admission",),
    "emerg_adm_type1": (r"emergency admissions via ae type 1",),
    "emerg_adm_type2": (r"emergency admissions via ae type 2",),
    "emerg_adm_other": (r"emergency admissions via ae other ae department",),
    "emerg_adm_not_ae": (r"other emergency admissions",),
}

AE_OPTIONAL = {k for k in AE_MEASURES if "booked" in k}

AE_COLUMNS = [
    "period", "provider_code", "provider_name", "region_name",
    "attendances", "attendances_type1", "over_4hr", "over_4hr_type1",
    "within_4hr", "dta_4_12", "dta_12_plus",
    "emergency_admissions_ae", "emergency_admissions_total",
]


def parse_ae(raw: bytes | str | Path, period: str) -> pd.DataFrame:
    """One monthly A&E CSV -> one row per provider."""
    source = io.BytesIO(raw) if isinstance(raw, bytes) else raw
    df = pd.read_csv(source, dtype=str, encoding="latin-1")
    df = df.dropna(how="all")
    columns = {norm(c): c for c in df.columns}
    code = pick(columns, r"org code")
    name = pick(columns, r"org name")
    parent = pick(columns, r"parent org")

    out = pd.DataFrame({
        "period": period,
        "provider_code": df[code].str.strip().str.upper(),
        "provider_name": df[name].str.strip(),
        "region_name": df[parent].str.strip(),
    })
    values = {}
    for key, patterns in AE_MEASURES.items():
        raw_col = pick(columns, *patterns, required=key not in AE_OPTIONAL)
        values[key] = to_number(df[raw_col]) if raw_col else pd.Series(0.0, index=df.index)
    v = pd.DataFrame(values).fillna(0.0)

    # Drop totals rows if a file ever carries them; totals are rebuilt in SQL.
    keep = ~out["provider_code"].fillna("").str.contains(r"^(?:TOTAL|-)?$", regex=True)
    keep &= ~out["provider_name"].fillna("").str.upper().str.startswith("TOTAL")
    out, v = out[keep], v[keep]

    att_cols = [c for c in v if c.startswith("att_")]
    over_cols = [c for c in v if c.startswith("over4_")]
    out = out.assign(
        attendances=v[att_cols].sum(axis=1),
        attendances_type1=v["att_type1"] + v["att_booked_type1"],
        over_4hr=v[over_cols].sum(axis=1),
        over_4hr_type1=v["over4_type1"] + v["over4_booked_type1"],
        dta_4_12=v["dta_4_12"],
        dta_12_plus=v["dta_12_plus"],
        emergency_admissions_ae=v[["emerg_adm_type1", "emerg_adm_type2", "emerg_adm_other"]].sum(
            axis=1
        ),
    )
    out["within_4hr"] = out["attendances"] - out["over_4hr"]
    out["emergency_admissions_total"] = out["emergency_admissions_ae"] + v["emerg_adm_not_ae"]
    out = out[out["provider_code"].notna()]
    return out[AE_COLUMNS].reset_index(drop=True)


# ---------------------------------------------------------------------------
# RTT
# ---------------------------------------------------------------------------

RTT_PARTS = {
    "part_1a": "completed_admitted",
    "part_1b": "completed_non_admitted",
    "part_2": "incomplete",
    "part_2a": "incomplete_with_dta",
    "part_3": "new_periods",
}
TOTAL_FUNCTION = "C_999"
BAND = re.compile(r"gt (\d+) to (\d+) weeks sum 1")
OVER_104 = re.compile(r"gt 104 weeks sum 1")

RTT_COLUMNS = [
    "period", "provider_code", "provider_name", "provider_parent_name",
    "treatment_function_code", "treatment_function_name", "part",
    "total", "within_18", "over_18", "over_52", "over_65", "over_78", "over_104",
    "unknown_clock_start", "median_weeks", "p92_weeks",
]
RTT_NATIONAL_COLUMNS = [c for c in RTT_COLUMNS if not c.startswith("provider")]


def week_bands(columns: dict[str, str]) -> list[tuple[int, int, str]]:
    bands = []
    for key, raw in columns.items():
        match = BAND.fullmatch(key)
        if match:
            bands.append((int(match[1]), int(match[2]), raw))
        elif OVER_104.fullmatch(key):
            bands.append((104, 999, raw))
    bands.sort()
    if not bands or bands[0][0] != 0:
        raise SchemaError("RTT file has no weekly band columns")
    return bands


def percentile_weeks(counts: np.ndarray, edges: np.ndarray, q: float) -> np.ndarray:
    """Interpolated percentile wait (in weeks) from weekly band counts.

    counts: rows x bands. edges: lower edge of each band. Bands are one week
    wide apart from the open 104+ band, which is capped at 104 weeks.
    """
    totals = counts.sum(axis=1)
    cum = counts.cumsum(axis=1)
    target = totals * q
    idx = (cum < target[:, None]).sum(axis=1)
    idx = np.minimum(idx, counts.shape[1] - 1)
    rows = np.arange(len(counts))
    before = np.where(idx > 0, cum[rows, np.maximum(idx - 1, 0)], 0.0)
    in_band = counts[rows, idx]
    with np.errstate(divide="ignore", invalid="ignore"):
        frac = np.where(in_band > 0, (target - before) / in_band, 0.0)
    width = np.where(edges[idx] >= 104, 0.0, 1.0)
    result = edges[idx] + frac * width
    return np.where(totals > 0, np.round(result, 2), np.nan)


def read_rtt_csv(raw: bytes | Path) -> pd.DataFrame:
    """The RTT download is a zip holding one CSV."""
    data = raw.read_bytes() if isinstance(raw, Path) else raw
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if len(names) != 1:
                raise SchemaError(f"expected one CSV in RTT zip, found {names}")
            data = zf.read(names[0])
    return pd.read_csv(io.BytesIO(data), dtype=str, encoding="latin-1")


def parse_rtt(raw: bytes | Path, period: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One monthly RTT full CSV -> (provider table, England table).

    Rows are provider x treatment function x RTT part. The file is split by
    commissioner too; those rows are summed so each provider appears once.
    England medians are worked out from the summed weekly bands, not by
    averaging provider medians.
    """
    df = read_rtt_csv(raw).dropna(how="all")
    columns = {norm(c): c for c in df.columns}
    code = pick(columns, r"provider org code")
    name = pick(columns, r"provider org name")
    parent = pick(columns, r"provider parent name", required=False)
    part = pick(columns, r"rtt part type")
    tf_code = pick(columns, r"treatment function code")
    tf_name = pick(columns, r"treatment function name")
    unknown = pick(columns, r"patients with unknown clock start date", required=False)
    bands = week_bands(columns)

    keys = pd.DataFrame({
        "provider_code": df[code].str.strip().str.upper(),
        "provider_name": df[name].str.strip(),
        "provider_parent_name": df[parent].str.strip() if parent else pd.NA,
        "part": df[part].str.strip().str.lower().map(RTT_PARTS),
        "treatment_function_code": df[tf_code].str.strip().str.upper(),
        "treatment_function_name": df[tf_name].str.strip(),
    })
    band_values = pd.DataFrame(
        {f"w{lo:03d}": to_number(df[col]).fillna(0.0) for lo, _, col in bands}
    )
    band_values["unknown_clock_start"] = (
        to_number(df[unknown]).fillna(0.0) if unknown else 0.0
    )
    frame = pd.concat([keys, band_values], axis=1)
    frame = frame[frame["part"].notna() & frame["provider_code"].notna()]

    group = ["provider_code", "treatment_function_code", "part"]
    band_cols = list(band_values.columns)
    names = frame.groupby(group, sort=False)[
        ["provider_name", "provider_parent_name", "treatment_function_name"]
    ].first()
    provider = names.join(frame.groupby(group, sort=False)[band_cols].sum()).reset_index()
    national = (
        provider.groupby(["treatment_function_code", "part"], sort=False)
        .agg({"treatment_function_name": "first", **{c: "sum" for c in band_cols}})
        .reset_index()
    )
    edges = np.array([lo for lo, _, _ in bands], dtype=float)
    return (
        derive_waits(provider, edges, period)[RTT_COLUMNS],
        derive_waits(national, edges, period)[RTT_NATIONAL_COLUMNS],
    )


def derive_waits(frame: pd.DataFrame, edges: np.ndarray, period: str) -> pd.DataFrame:
    band_cols = [f"w{int(lo):03d}" for lo in edges]
    counts = frame[band_cols].to_numpy(dtype=float)

    def from_week(week: int) -> np.ndarray:
        return counts[:, edges >= week].sum(axis=1)

    frame = frame.copy()
    frame["total"] = counts.sum(axis=1)
    frame["within_18"] = counts[:, edges < 18].sum(axis=1)
    frame["over_18"] = from_week(18)
    frame["over_52"] = from_week(52)
    frame["over_65"] = from_week(65)
    frame["over_78"] = from_week(78)
    frame["over_104"] = from_week(104)
    frame["median_weeks"] = percentile_weeks(counts, edges, 0.5)
    frame["p92_weeks"] = percentile_weeks(counts, edges, 0.92)
    frame["period"] = period
    return frame


def build_ae(raw_dir: Path, manifest: list[dict]) -> pd.DataFrame:
    frames = []
    for entry in manifest:
        if entry["dataset"] == "ae":
            frames.append(parse_ae((raw_dir / entry["path"]).read_bytes(), entry["period"]))
    if not frames:
        raise RuntimeError("no A&E files in the manifest")
    return pd.concat(frames, ignore_index=True)


def build_rtt(raw_dir: Path, manifest: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    providers, national = [], []
    for entry in manifest:
        if entry["dataset"] == "rtt":
            log.debug("parsing RTT %s", entry["period"])
            p, n = parse_rtt(raw_dir / entry["path"], entry["period"])
            providers.append(p)
            national.append(n)
    if not providers:
        raise RuntimeError("no RTT files in the manifest")
    return pd.concat(providers, ignore_index=True), pd.concat(national, ignore_index=True)
