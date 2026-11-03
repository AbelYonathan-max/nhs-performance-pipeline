import shutil
import subprocess
import sys

import pandas as pd
import pytest
from openpyxl import load_workbook

from nhs_pipeline import excel_pack, pipeline

RECALC = "/mnt/skills/public/xlsx/scripts/recalc.py"


def test_export_parquet(built, tmp_path):
    files = pipeline.export(built, tmp_path)
    assert {p.stem for p in files} == set(pipeline.EXPORTS)
    fc = pd.read_parquet(tmp_path / "fact_forecast.parquet")
    assert fc.metric_label.notna().all() and "England" in set(fc.organisation)


def test_excel_pack(built, tmp_path):
    path = excel_pack.build(built, tmp_path / "pack.xlsx")
    wb = load_workbook(path)
    assert wb.sheetnames[:3] == ["Contents", "Summary", "Trust lookup"]
    formulas = sum(
        1 for ws in wb.worksheets for row in ws.iter_rows() for c in row
        if isinstance(c.value, str) and c.value.startswith("=")
    )
    assert formulas > 300
    assert "AE_Within" in wb.defined_names


@pytest.mark.skipif(not shutil.which("soffice"), reason="LibreOffice not installed")
def test_excel_pack_recalculates_without_errors(built, tmp_path):
    import json
    from pathlib import Path

    path = excel_pack.build(built, tmp_path / "pack.xlsx")
    script = Path(RECALC)
    if not script.exists():
        pytest.skip("recalc helper not available")
    result = subprocess.run([sys.executable, str(script), str(path), "120"], capture_output=True, text=True)
    report = json.loads(result.stdout)
    assert report.get("status") == "success", report
