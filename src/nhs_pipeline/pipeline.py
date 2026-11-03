"""Run the stages in order: clean -> warehouse -> forecasts -> published files."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import pandas as pd

from . import forecast, transform, warehouse

log = logging.getLogger(__name__)

# Tables written for Power BI and anyone else. Parquet keeps types and is small.
EXPORTS = {
    "dim_month": "SELECT * FROM dim_month",
    "dim_region": "SELECT * FROM dim_region",
    "dim_provider": "SELECT * FROM dim_provider",
    "dim_treatment_function": "SELECT * FROM dim_treatment_function",
    "targets": "SELECT * FROM targets",
    "fact_ae": "SELECT * FROM fact_ae",
    "fact_rtt_provider": """
        SELECT month_key, provider_code, waiting_list, within_18, over_52, over_65, over_78,
               over_104, median_weeks, p92_weeks, clock_stops, clock_starts
        FROM v_rtt_provider_month""",
    "fact_rtt_specialty": """
        SELECT month_key, provider_code, treatment_function_code, total AS waiting_list,
               within_18, over_52
        FROM fact_rtt
        WHERE part = 'incomplete' AND treatment_function_code <> 'C_999'""",
    "fact_rtt_england": """
        SELECT month_key, treatment_function_code, total AS waiting_list, within_18, over_52,
               over_65, over_78, over_104, median_weeks, p92_weeks
        FROM fact_rtt_england
        WHERE part = 'incomplete'""",
    "fact_forecast": """
        SELECT f.*,
               CASE f.metric
                   WHEN 'ae_4hr_pct' THEN 'A&E seen within 4 hours'
                   WHEN 'ae_attendances' THEN 'A&E attendances'
                   WHEN 'rtt_18wk_pct' THEN 'RTT waiting under 18 weeks'
                   WHEN 'rtt_waiting_list' THEN 'RTT waiting list'
               END AS metric_label,
               COALESCE(p.provider_name, 'England') AS organisation
        FROM fact_forecast f LEFT JOIN dim_provider p USING (provider_code)""",
    "model_backtest": "SELECT * FROM model_backtest",
    "anomalies": "SELECT * FROM anomalies",
    "dq_checks": "SELECT * FROM dq_checks",
    "load_log": "SELECT * FROM load_log",
}

FORECAST_INPUTS = {
    "ae_4hr_pct": """
        SELECT v.month_key, v.provider_code, v.pct_4hr AS value
        FROM v_ae_provider_month v JOIN dim_provider p USING (provider_code)
        WHERE p.has_type1_ae = 1 AND v.attendances_type1 > 0
        UNION ALL SELECT month_key, 'ENG', pct_4hr FROM v_ae_england_month""",
    "ae_attendances": """
        SELECT v.month_key, v.provider_code, v.attendances AS value
        FROM v_ae_provider_month v JOIN dim_provider p USING (provider_code)
        WHERE p.has_type1_ae = 1
        UNION ALL SELECT month_key, 'ENG', attendances FROM v_ae_england_month""",
    "rtt_18wk_pct": """
        SELECT month_key, provider_code, pct_18wk AS value FROM v_rtt_provider_month
        WHERE sector = 'NHS trust' AND waiting_list >= 1000
        UNION ALL SELECT month_key, 'ENG', pct_18wk FROM v_rtt_england_month""",
    "rtt_waiting_list": """
        SELECT month_key, provider_code, waiting_list AS value FROM v_rtt_provider_month
        WHERE sector = 'NHS trust' AND waiting_list >= 1000
        UNION ALL SELECT month_key, 'ENG', waiting_list FROM v_rtt_england_month""",
}


def build(raw_dir: Path, db_path: Path, targets_csv: Path) -> None:
    manifest = json.loads((raw_dir / "manifest.json").read_text())
    ae = transform.build_ae(raw_dir, manifest)
    rtt, rtt_eng = transform.build_rtt(raw_dir, manifest)
    targets = pd.read_csv(targets_csv)
    warehouse.load(db_path, ae, rtt, rtt_eng, targets, manifest, forecast.HORIZON)


def run_forecasts(db_path: Path) -> None:
    inputs = {k: warehouse.query(db_path, sql) for k, sql in FORECAST_INPUTS.items()}
    fc, scores, flags = forecast.run(inputs)
    con = warehouse.connect(db_path)
    try:
        for table in ("fact_forecast", "model_backtest", "anomalies"):
            con.execute(f"DELETE FROM {table}")
        fc.to_sql("fact_forecast", con, if_exists="append", index=False)
        scores.to_sql("model_backtest", con, if_exists="append", index=False)
        if not flags.empty:
            flags.to_sql("anomalies", con, if_exists="append", index=False)
        con.commit()
    finally:
        con.close()
    log.info("forecasts: %d rows, anomalies: %d", len(fc), len(flags))


def export(db_path: Path, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    con = sqlite3.connect(db_path)
    try:
        for name, sql in EXPORTS.items():
            frame = pd.read_sql_query(sql, con)
            path = out_dir / f"{name}.parquet"
            frame.to_parquet(path, index=False)
            written.append(path)
            log.info("wrote %s (%d rows)", path, len(frame))
    finally:
        con.close()
    return written


DATA_README = """# Published data

Written by `nhsp publish` from the `main` branch pipeline. Power BI reads these
Parquet files straight from this branch; the Excel pack is the monthly
reporting pack. Source: NHS England statistics, Open Government Licence v3.0.
"""


def publish(out_dir: Path, raw_dir: Path, branch: str = "data", remote: str = "origin",
            push: bool = True) -> str:
    """Commit data/out to the `data` branch (via a temporary worktree) and push it."""
    import shutil
    import subprocess
    import tempfile

    def git(*args: str, cwd: Path | None = None) -> str:
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                              text=True).stdout.strip()

    files = sorted(p for p in out_dir.iterdir() if p.suffix in (".parquet", ".xlsx"))
    if not files:
        raise RuntimeError(f"nothing to publish in {out_dir}; run `nhsp all` first")
    tmp = Path(tempfile.mkdtemp(prefix="nhsp-data-"))
    work = tmp / "data"
    try:
        exists = subprocess.run(["git", "fetch", remote, branch], capture_output=True).returncode == 0
        if exists:
            git("worktree", "add", "--force", "--detach", str(work), f"{remote}/{branch}")
        else:
            git("worktree", "add", "--force", "--detach", str(work))
            git("checkout", "--orphan", "publish-tmp", cwd=work)
            git("rm", "-rf", "--quiet", ".", cwd=work)
        for old in work.iterdir():
            if old.name != ".git":
                shutil.rmtree(old) if old.is_dir() else old.unlink()
        for f in files:
            shutil.copy2(f, work / f.name)
        shutil.copy2(raw_dir / "manifest.json", work / "source-manifest.json")
        (work / "README.md").write_text(DATA_README)
        git("add", "-A", cwd=work)
        if not git("status", "--porcelain", cwd=work):
            log.info("data branch already up to date")
            return "unchanged"
        latest = max(f.stem for f in files if f.suffix == ".xlsx").removeprefix("NHS-performance-pack-")
        git("commit", "-q", "-m", f"Data to {latest}", cwd=work)
        if push:
            git("push", remote, f"HEAD:refs/heads/{branch}", cwd=work)
        log.info("published %d files to %s/%s", len(files), remote, branch)
        return latest
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(work)], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)
