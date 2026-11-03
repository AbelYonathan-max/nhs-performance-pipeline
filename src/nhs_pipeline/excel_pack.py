"""Monthly Excel reporting pack.

Every figure on the report sheets is a live formula (SUMIFS / INDEX-MATCH)
over the data sheets at the back, so the pack still works when someone pastes
in next month's rows. Only league-table order and model outputs are written
as values.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.formatting.rule import CellIsRule, ColorScaleRule, DataBarRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo

from . import warehouse

log = logging.getLogger(__name__)

FONT = "Arial"
BLUE = "005EB8"  # NHS blue
DARK = "003087"
GREY = "E8EDEE"
GREEN = "C6EFCE"
RED = "FFC7CE"
AMBER = "FFEB9C"
MONTHS_IN_PACK = 25

H1 = Font(name=FONT, size=16, bold=True, color=DARK)
H2 = Font(name=FONT, size=12, bold=True, color=DARK)
BODY = Font(name=FONT, size=10)
BOLD = Font(name=FONT, size=10, bold=True)
HEAD = Font(name=FONT, size=10, bold=True, color="FFFFFF")
NOTE = Font(name=FONT, size=9, italic=True, color="425563")
LINK = Font(name=FONT, size=10, color="0563C1", underline="single")
HEAD_FILL = PatternFill("solid", fgColor=BLUE)
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
THIN = Border(bottom=Side(style="thin", color="AEB7BD"))

PCT = "0.0%"
INT = "#,##0"
WEEKS = "0.0"


def month_label(key: int) -> str:
    return pd.Period(f"{key // 100}-{key % 100:02d}", "M").strftime("%b %Y")


def style_header(ws, row: int, first: int, last: int) -> None:
    for col in range(first, last + 1):
        cell = ws.cell(row=row, column=col)
        cell.font = HEAD
        cell.fill = HEAD_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 30


def set_widths(ws, widths: dict[str, float]) -> None:
    for col, width in widths.items():
        ws.column_dimensions[col].width = width


def write_table(ws, frame: pd.DataFrame, name: str, formats: dict[str, str]) -> int:
    """Write a DataFrame as an Excel table from A1. Returns the last row."""
    ws.append(list(frame.columns))
    for row in frame.itertuples(index=False):
        ws.append([None if pd.isna(v) else v for v in row])
    last = len(frame) + 1
    ref = f"A1:{get_column_letter(len(frame.columns))}{last}"
    table = Table(displayName=name, ref=ref)
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    ws.add_table(table)
    for idx, col in enumerate(frame.columns, start=1):
        letter = get_column_letter(idx)
        ws.column_dimensions[letter].width = max(10, min(45, len(col) + 4))
        fmt = formats.get(col)
        for r in range(2, last + 1):
            cell = ws.cell(row=r, column=idx)
            cell.font = BODY
            if fmt:
                cell.number_format = fmt
    for idx in range(1, len(frame.columns) + 1):
        ws.cell(row=1, column=idx).font = HEAD
    ws.freeze_panes = "A2"
    return last


def define(wb: Workbook, name: str, sheet: str, col: str, last: int) -> None:
    wb.defined_names[name] = DefinedName(name, attr_text=f"'{sheet}'!${col}$2:${col}${last}")


def fy_formula(key_cell: str) -> str:
    """Financial year label ('2026-27') for a yyyymm month key."""
    start = f"IF(MOD({key_cell},100)>=4,INT({key_cell}/100),INT({key_cell}/100)-1)"
    return f'{start}&"-"&RIGHT({start}+1,2)'


def prev_month(key_cell: str) -> str:
    return f"IF(MOD({key_cell},100)=1,{key_cell}-89,{key_cell}-1)"


def load_frames(db: Path) -> dict[str, pd.DataFrame]:
    q = lambda sql: warehouse.query(db, sql)  # noqa: E731
    ae_last = int(q("SELECT MAX(month_key) AS k FROM fact_ae").k[0])
    rtt_last = int(q("SELECT MAX(month_key) AS k FROM fact_rtt").k[0])
    ae_first = int((pd.Period(f"{ae_last // 100}-{ae_last % 100}", "M") - MONTHS_IN_PACK + 1)
                   .strftime("%Y%m"))
    rtt_first = int((pd.Period(f"{rtt_last // 100}-{rtt_last % 100}", "M") - MONTHS_IN_PACK + 1)
                    .strftime("%Y%m"))
    frames = {
        "ae": q(f"""
            SELECT f.month_key AS "Month key", p.provider_code AS "Code", p.provider_name AS "Organisation",
                   COALESCE(r.region_name, 'Unknown') AS "Region",
                   CASE p.has_type1_ae WHEN 1 THEN 'Y' ELSE 'N' END AS "Major A&E",
                   f.attendances AS "Attendances", f.within_4hr AS "Within 4 hours",
                   f.over_4hr AS "Over 4 hours", f.attendances_type1 AS "Type 1 attendances",
                   f.over_4hr_type1 AS "Type 1 over 4 hours", f.dta_12_plus AS "12+ hours from decision to admit",
                   f.emergency_admissions_total AS "Emergency admissions"
            FROM fact_ae f JOIN dim_provider p USING (provider_code)
            LEFT JOIN dim_region r USING (region_code)
            WHERE f.month_key >= {ae_first}
            ORDER BY f.month_key, p.provider_code"""),
        "rtt": q(f"""
            SELECT v.month_key AS "Month key", v.provider_code AS "Code", v.provider_name AS "Organisation",
                   COALESCE(r.region_name, 'Unknown') AS "Region", v.sector AS "Sector",
                   v.waiting_list AS "Waiting list", v.within_18 AS "Within 18 weeks",
                   v.over_52 AS "Over 52 weeks", v.over_65 AS "Over 65 weeks", v.over_78 AS "Over 78 weeks",
                   v.median_weeks AS "Median wait (weeks)", v.clock_stops AS "Treated (clock stops)"
            FROM v_rtt_provider_month v LEFT JOIN dim_region r USING (region_code)
            WHERE v.month_key >= {rtt_first}
            ORDER BY v.month_key, v.provider_code"""),
        "eng": q("""
            SELECT m.month_key AS "Month key", m.month_name AS "Month",
                   a.attendances AS "A&E attendances", a.within_4hr AS "A&E within 4 hours",
                   a.dta_12_plus AS "12+ hours from decision to admit",
                   r.waiting_list AS "RTT waiting list", r.within_18 AS "RTT within 18 weeks",
                   r.over_52 AS "RTT over 52 weeks", r.median_weeks AS "RTT median wait (weeks)",
                   r.p92_weeks AS "RTT 92nd percentile wait (weeks)"
            FROM dim_month m
            LEFT JOIN v_ae_england_month a USING (month_key)
            LEFT JOIN v_rtt_england_month r USING (month_key)
            WHERE a.month_key IS NOT NULL OR r.month_key IS NOT NULL
            ORDER BY m.month_key"""),
        "targets": q("SELECT metric, financial_year, target_date, value, scope, source FROM targets"),
        "forecast": q("""
            SELECT metric, month_key, kind, value, lower_80, upper_80
            FROM fact_forecast WHERE provider_code = 'ENG' ORDER BY metric, month_key"""),
        "backtest": q("SELECT * FROM model_backtest ORDER BY metric, model, horizon"),
        "anomalies": q("""
            SELECT a.metric, a.provider_code, p.provider_name, a.month_key, a.value, a.expected, a.robust_z
            FROM anomalies a LEFT JOIN dim_provider p USING (provider_code)
            ORDER BY a.month_key DESC, ABS(a.robust_z) DESC"""),
        "dq": q("SELECT dataset, check_name, severity, passed, detail FROM dq_checks"),
        "load_log": q("SELECT dataset, period, rows_loaded, source_url, sha256 FROM load_log ORDER BY dataset, period"),
        "ae_league": q("SELECT provider_code FROM v_ae_league_latest ORDER BY rank_4hr, provider_code"),
        "rtt_league": q("SELECT provider_code FROM v_rtt_league_latest ORDER BY rank_18wk, provider_code"),
    }
    frames["ae_last"], frames["rtt_last"] = ae_last, rtt_last
    return frames


def build(db: Path, out_path: Path) -> Path:
    f = load_frames(db)
    wb = Workbook()
    contents = wb.active
    contents.title = "Contents"
    summary = wb.create_sheet("Summary")
    lookup = wb.create_sheet("Trust lookup")
    ae_league = wb.create_sheet("A&E league table")
    rtt_league = wb.create_sheet("RTT league table")
    regions = wb.create_sheet("Regions")
    fc_ws = wb.create_sheet("Forecast")
    dq_ws = wb.create_sheet("Data quality")
    defs = wb.create_sheet("Definitions")
    d_ae = wb.create_sheet("Data_AE")
    d_rtt = wb.create_sheet("Data_RTT")
    d_eng = wb.create_sheet("Data_England")
    d_tgt = wb.create_sheet("Targets")
    lists = wb.create_sheet("Lists")

    # --- data sheets -------------------------------------------------------
    n_ae = write_table(d_ae, f["ae"], "AE", {c: INT for c in f["ae"].columns[5:]})
    n_rtt = write_table(d_rtt, f["rtt"], "RTT", {
        **{c: INT for c in f["rtt"].columns[5:]}, "Median wait (weeks)": WEEKS})
    eng = f["eng"].copy()
    eng["A&E within 4 hours %"] = None
    eng["A&E target"] = None
    eng["RTT within 18 weeks %"] = None
    eng["RTT target"] = None
    n_eng = write_table(d_eng, eng, "England", {
        **{c: INT for c in eng.columns[2:8]},
        "RTT median wait (weeks)": WEEKS, "RTT 92nd percentile wait (weeks)": WEEKS,
        "A&E within 4 hours %": PCT, "A&E target": PCT, "RTT within 18 weeks %": PCT,
        "RTT target": PCT})
    tgt = f["targets"].copy()
    tgt.insert(0, "key", tgt["metric"] + "|" + tgt["financial_year"])
    n_tgt = write_table(d_tgt, tgt, "Targets", {"value": PCT})

    for name, col in (("AE_Key", "A"), ("AE_Code", "B"), ("AE_Region", "D"), ("AE_Major", "E"),
                      ("AE_Att", "F"), ("AE_Within", "G"), ("AE_T1", "I"), ("AE_T1Over", "J"),
                      ("AE_DTA12", "K")):
        define(wb, name, "Data_AE", col, n_ae)
    for name, col in (("RTT_Key", "A"), ("RTT_Code", "B"), ("RTT_Region", "D"), ("RTT_WL", "F"),
                      ("RTT_Within", "G"), ("RTT_52", "H"), ("RTT_Median", "K")):
        define(wb, name, "Data_RTT", col, n_rtt)
    for name, col in (("ENG_Key", "A"), ("ENG_Median", "I")):
        define(wb, name, "Data_England", col, n_eng)
    define(wb, "TGT_Key", "Targets", "A", n_tgt)
    define(wb, "TGT_Value", "Targets", "E", n_tgt)

    # England percentages and the target that applied each month.
    for r in range(2, n_eng + 1):
        d_eng[f"K{r}"] = f'=IF(C{r}="","",D{r}/C{r})'
        d_eng[f"L{r}"] = (f'=IFERROR(INDEX(TGT_Value,MATCH("ae_4hr_pct|"&{fy_formula(f"A{r}")},TGT_Key,0)),"")')
        d_eng[f"M{r}"] = f'=IF(F{r}="","",G{r}/F{r})'
        d_eng[f"N{r}"] = (f'=IFERROR(INDEX(TGT_Value,MATCH("rtt_18wk_pct|"&{fy_formula(f"A{r}")},TGT_Key,0)),"")')
        for c in "KLMN":
            d_eng[f"{c}{r}"].number_format = PCT
            d_eng[f"{c}{r}"].font = BODY

    # --- lists for the dropdown -------------------------------------------
    orgs = (
        pd.concat([f["ae"][f["ae"]["Major A&E"] == "Y"][["Organisation", "Code"]],
                   f["rtt"][f["rtt"]["Sector"] == "NHS trust"][["Organisation", "Code"]]])
        .drop_duplicates("Code").drop_duplicates("Organisation").sort_values("Organisation")
    )
    lists.append(["Organisation", "Code"])
    for row in orgs.itertuples(index=False):
        lists.append(list(row))
    define(wb, "List_Org", "Lists", "A", len(orgs) + 1)
    define(wb, "List_Code", "Lists", "B", len(orgs) + 1)
    lists.sheet_state = "hidden"

    # --- contents ---------------------------------------------------------
    ae_last, rtt_last = f["ae_last"], f["rtt_last"]
    contents["A1"] = "NHS England performance pack: A&E and elective waiting times"
    contents["A1"].font = H1
    contents["A3"] = "A&E data to"
    contents["B3"] = month_label(ae_last)
    contents["A4"] = "Waiting list (RTT) data to"
    contents["B4"] = month_label(rtt_last)
    contents["A5"] = "Built"
    contents["B5"] = datetime.now(UTC).strftime("%d %b %Y %H:%M UTC")
    contents["A6"] = "Source"
    contents["B6"] = ("NHS England Statistical Work Areas: A&E Attendances and Emergency Admissions; "
                      "Consultant-led Referral to Treatment Waiting Times. Open Government Licence v3.0.")
    for r in range(3, 7):
        contents[f"A{r}"].font = BOLD
        contents[f"B{r}"].font = BODY
    sheets = [
        ("Summary", "England headline measures against target, with trends"),
        ("Trust lookup", "Pick a trust from the dropdown to see its last 13 months"),
        ("A&E league table", "Major A&E departments ranked on the 4-hour standard"),
        ("RTT league table", "NHS trusts ranked on patients waiting under 18 weeks"),
        ("Regions", "4-hour and 18-week performance by NHS England region"),
        ("Forecast", "Six-month England forecasts with 80% ranges, and how the model scored"),
        ("Data quality", "Checks run on every load, unusual months, and the source file log"),
        ("Definitions", "What each measure means and how it is calculated"),
    ]
    contents["A8"] = "Sheet"
    contents["B8"] = "What it shows"
    style_header(contents, 8, 1, 2)
    for i, (sheet, text) in enumerate(sheets, start=9):
        cell = contents.cell(row=i, column=1, value=sheet)
        cell.hyperlink = f"#'{sheet}'!A1"
        cell.font = LINK
        contents.cell(row=i, column=2, value=text).font = BODY
    contents["A18"] = ("Yellow cells are inputs you can change. Every other figure is a formula over the "
                       "Data_ sheets, so pasting in new rows updates the pack.")
    contents["A18"].font = NOTE
    set_widths(contents, {"A": 28, "B": 95})

    # --- summary ----------------------------------------------------------
    s = summary
    s["A1"] = "England summary"
    s["A1"].font = H1
    s["A3"], s["A4"] = "A&E month (key)", "RTT month (key)"
    s["B3"], s["B4"] = "=MAX(AE_Key)", "=MAX(RTT_Key)"
    for c in ("B3", "B4"):
        s[c].fill = INPUT_FILL
        s[c].font = BOLD
    s["C3"] = '=TEXT(DATE(INT(B3/100),MOD(B3,100),1),"mmm yyyy")'
    s["C4"] = '=TEXT(DATE(INT(B4/100),MOD(B4,100),1),"mmm yyyy")'
    s["D3"] = "Change the yellow cells to report an earlier month."
    s["D3"].font = NOTE
    s["A3"].font = s["A4"].font = BOLD
    headers = ["Measure", "Latest month", "Previous month", "Same month last year",
               "Change on last year", "Target this year", "Status"]
    for i, h in enumerate(headers, start=1):
        s.cell(row=6, column=i, value=h)
    style_header(s, 6, 1, 7)

    def ae_sum(rng: str, key: str) -> str:
        return f"SUMIFS({rng},AE_Key,{key})"

    def rtt_sum(rng: str, key: str) -> str:
        return f"SUMIFS({rng},RTT_Key,{key})"

    ae_keys = ["$B$3", prev_month("$B$3"), "($B$3-100)"]
    rtt_keys = ["$B$4", prev_month("$B$4"), "($B$4-100)"]
    rows = [
        ("A&E: seen within 4 hours", [f"={ae_sum('AE_Within', k)}/{ae_sum('AE_Att', k)}" for k in ae_keys],
         PCT, "ae_4hr_pct", "$B$3", True),
        ("A&E: type 1 (major A&E) within 4 hours",
         [f"=1-{ae_sum('AE_T1Over', k)}/{ae_sum('AE_T1', k)}" for k in ae_keys], PCT, None, None, True),
        ("A&E: attendances", [f"={ae_sum('AE_Att', k)}" for k in ae_keys], INT, None, None, None),
        ("A&E: waits of 12+ hours from decision to admit",
         [f"={ae_sum('AE_DTA12', k)}" for k in ae_keys], INT, None, None, False),
        ("RTT: waiting list", [f"={rtt_sum('RTT_WL', k)}" for k in rtt_keys], INT, None, None, False),
        ("RTT: waiting under 18 weeks",
         [f"={rtt_sum('RTT_Within', k)}/{rtt_sum('RTT_WL', k)}" for k in rtt_keys],
         PCT, "rtt_18wk_pct", "$B$4", True),
        ("RTT: waiting over 52 weeks", [f"={rtt_sum('RTT_52', k)}" for k in rtt_keys], INT, None, None, False),
        ("RTT: share of list over 52 weeks",
         [f"={rtt_sum('RTT_52', k)}/{rtt_sum('RTT_WL', k)}" for k in rtt_keys],
         "0.00%", "rtt_52wk_share", "$B$4", False),
        ("RTT: median wait (weeks)",
         [f"=IFERROR(INDEX(ENG_Median,MATCH({k},ENG_Key,0)),\"\")" for k in rtt_keys],
         WEEKS, None, None, False),
    ]
    for r, (label, formulas, fmt, target_metric, key_cell, higher_better) in enumerate(rows, start=7):
        s.cell(row=r, column=1, value=label).font = BODY
        for c, formula in enumerate(formulas, start=2):
            cell = s.cell(row=r, column=c, value=f"=IFERROR({formula[1:]},\"\")")
            cell.number_format = fmt
            cell.font = BODY
        change = s.cell(row=r, column=5, value=f'=IFERROR(B{r}-D{r},"")')
        change.number_format = "+0.0%;-0.0%;0.0%" if fmt in (PCT, "0.00%") else (
            "+#,##0;-#,##0;0" if fmt == INT else "+0.0;-0.0;0.0")
        change.font = BODY
        if target_metric:
            t = s.cell(row=r, column=6, value=(
                f'=IFERROR(INDEX(TGT_Value,MATCH("{target_metric}|"&{fy_formula(key_cell)},TGT_Key,0)),"")'))
            t.number_format = fmt
            t.font = BODY
            op = ">=" if higher_better else "<="
            s.cell(row=r, column=7,
                   value=f'=IF(F{r}="","",IF(B{r}{op}F{r},"Met","Not met"))').font = BOLD
        for c in range(1, 8):
            s.cell(row=r, column=c).border = THIN
    last_kpi = 6 + len(rows)
    s.conditional_formatting.add(f"G7:G{last_kpi}", CellIsRule(
        operator="equal", formula=['"Met"'], fill=PatternFill("solid", fgColor=GREEN)))
    s.conditional_formatting.add(f"G7:G{last_kpi}", CellIsRule(
        operator="equal", formula=['"Not met"'], fill=PatternFill("solid", fgColor=RED)))
    s[f"A{last_kpi + 1}"] = ("Targets: national ambitions from NHS England planning guidance, applied to the "
                             "financial year of the month shown. See the Targets sheet for sources.")
    s[f"A{last_kpi + 1}"].font = NOTE
    set_widths(s, {"A": 46, "B": 15, "C": 15, "D": 18, "E": 16, "F": 15, "G": 11})

    chart_row = last_kpi + 3
    c1 = LineChart()
    c1.title = "England: A&E patients seen within 4 hours"
    c1.y_axis.number_format = "0%"
    c1.y_axis.title = None
    c1.height, c1.width = 7.5, 16
    c1.add_data(Reference(d_eng, min_col=11, max_col=12, min_row=1, max_row=n_eng), titles_from_data=True)
    c1.set_categories(Reference(d_eng, min_col=2, min_row=2, max_row=n_eng))
    s.add_chart(c1, f"A{chart_row}")
    c2 = LineChart()
    c2.title = "England: RTT waiting list"
    c2.y_axis.number_format = "#,##0"
    c2.height, c2.width = 7.5, 16
    c2.add_data(Reference(d_eng, min_col=6, min_row=1, max_row=n_eng), titles_from_data=True)
    c2.set_categories(Reference(d_eng, min_col=2, min_row=2, max_row=n_eng))
    s.add_chart(c2, f"D{chart_row}")

    # --- trust lookup -----------------------------------------------------
    t = lookup
    t["A1"] = "Trust lookup"
    t["A1"].font = H1
    t["A3"] = "Organisation"
    t["A3"].font = BOLD
    first_org = orgs["Organisation"].iloc[0] if len(orgs) else ""
    pick = orgs[orgs["Code"] == "RJ1"]["Organisation"]
    t["B3"] = pick.iloc[0] if len(pick) else first_org
    t["B3"].fill = INPUT_FILL
    t["B3"].font = BOLD
    dv = DataValidation(type="list", formula1="=List_Org", allow_blank=False)
    dv.error, dv.errorTitle = "Pick an organisation from the list.", "Not in list"
    t.add_data_validation(dv)
    dv.add("B3")
    t["A4"] = "Code"
    t["A4"].font = BOLD
    t["B4"] = '=IFERROR(INDEX(List_Code,MATCH(B3,List_Org,0)),"")'
    t["D3"] = "Pick from the dropdown in the yellow cell."
    t["D3"].font = NOTE
    cols = ["Month key", "Month", "A&E attendances", "A&E within 4 hours", "12+ hours from decision to admit",
            "RTT waiting list", "RTT within 18 weeks", "RTT over 52 weeks", "RTT median wait (weeks)"]
    for i, h in enumerate(cols, start=1):
        t.cell(row=6, column=i, value=h)
    style_header(t, 6, 1, len(cols))
    for i in range(13):
        r = 7 + i
        t[f"A{r}"] = "=MAX(AE_Key)" if i == 0 else f"={prev_month(f'A{r - 1}')}"
        t[f"B{r}"] = f'=DATE(INT(A{r}/100),MOD(A{r},100),1)'
        t[f"B{r}"].number_format = "mmm yyyy"
        t[f"C{r}"] = f'=IF(SUMIFS(AE_Att,AE_Code,$B$4,AE_Key,A{r})=0,"",SUMIFS(AE_Att,AE_Code,$B$4,AE_Key,A{r}))'
        t[f"D{r}"] = f'=IFERROR(SUMIFS(AE_Within,AE_Code,$B$4,AE_Key,A{r})/C{r},"")'
        t[f"E{r}"] = f'=IF(C{r}="","",SUMIFS(AE_DTA12,AE_Code,$B$4,AE_Key,A{r}))'
        t[f"F{r}"] = f'=IF(SUMIFS(RTT_WL,RTT_Code,$B$4,RTT_Key,A{r})=0,"",SUMIFS(RTT_WL,RTT_Code,$B$4,RTT_Key,A{r}))'
        t[f"G{r}"] = f'=IFERROR(SUMIFS(RTT_Within,RTT_Code,$B$4,RTT_Key,A{r})/F{r},"")'
        t[f"H{r}"] = f'=IF(F{r}="","",SUMIFS(RTT_52,RTT_Code,$B$4,RTT_Key,A{r}))'
        t[f"I{r}"] = f'=IF(F{r}="","",SUMIFS(RTT_Median,RTT_Code,$B$4,RTT_Key,A{r}))'
        for col, fmt in zip("ACDEFGHI", ["0", INT, PCT, INT, INT, PCT, INT, WEEKS], strict=True):
            t[f"{col}{r}"].number_format = fmt
        for col in "ABCDEFGHI":
            t[f"{col}{r}"].font = BODY
    t["A21"] = "Blank cells: the organisation did not report that measure that month (or the RTT month is not out yet)."
    t["A21"].font = NOTE
    for col, title, fmt in (("D", "4-hour performance", "0%"), ("G", "Waiting under 18 weeks", "0%")):
        ch = LineChart()
        ch.title = title
        ch.y_axis.number_format = fmt
        ch.height, ch.width = 7, 12
        idx = "ABCDEFGHI".index(col) + 1
        ch.add_data(Reference(t, min_col=idx, min_row=6, max_row=19), titles_from_data=True)
        ch.set_categories(Reference(t, min_col=2, min_row=7, max_row=19))
        ch.x_axis.scaling.orientation = "maxMin"
        t.add_chart(ch, "A23" if col == "D" else "F23")
    set_widths(t, {"A": 12, "B": 46, "C": 14, "D": 14, "E": 16, "F": 14, "G": 14, "H": 14, "I": 14})

    # --- league tables ----------------------------------------------------
    def league(ws, title: str, codes: list[str], spec: list[tuple[str, str, str]], key: str,
               rank_col: str, target_metric: str, note: str) -> None:
        ws["A1"] = title
        ws["A1"].font = H1
        ws["A2"] = f'="Month: "&TEXT(DATE(INT({key}/100),MOD({key},100),1),"mmm yyyy")'
        ws["A2"].font = BOLD
        ws["A3"] = note
        ws["A3"].font = NOTE
        heads = ["Rank", "Code", "Organisation", "Region"] + [h for h, _, _ in spec]
        for i, h in enumerate(heads, start=1):
            ws.cell(row=5, column=i, value=h)
        style_header(ws, 5, 1, len(heads))
        last = 5 + len(codes)
        for i, code in enumerate(codes, start=6):
            ws[f"B{i}"] = code
            ws[f"C{i}"] = f'=IFERROR(INDEX(List_Org,MATCH(B{i},List_Code,0)),B{i})'
            ws[f"A{i}"] = f'=IFERROR(RANK({rank_col}{i},${rank_col}$6:${rank_col}${last},0),"")'
            for j, (_, formula, fmt) in enumerate(spec, start=5):
                cell = ws.cell(row=i, column=j, value=formula.format(r=i, key=key))
                cell.number_format = fmt
            for j in range(1, len(heads) + 1):
                ws.cell(row=i, column=j).font = BODY
        return last

    ae_spec = [
        ("Attendances", "=SUMIFS(AE_Att,AE_Code,$B{r},AE_Key,{key})", INT),
        ("Within 4 hours", '=IFERROR(SUMIFS(AE_Within,AE_Code,$B{r},AE_Key,{key})/E{r},"")', PCT),
        ("Same month last year",
         '=IFERROR(SUMIFS(AE_Within,AE_Code,$B{r},AE_Key,{key}-100)/SUMIFS(AE_Att,AE_Code,$B{r},AE_Key,{key}-100),"")',
         PCT),
        ("Change (points)", '=IFERROR(F{r}-G{r},"")', "+0.0%;-0.0%;0.0%"),
        ("12+ hours from decision to admit", "=SUMIFS(AE_DTA12,AE_Code,$B{r},AE_Key,{key})", INT),
    ]
    key_ae = "Summary!$B$3"
    n = league(ae_league, "A&E league table: 4-hour standard", f["ae_league"].provider_code.tolist(),
               ae_spec, key_ae, "F", "ae_4hr_pct",
               "Trusts with a major (type 1) A&E. Green = at or above this year's national target.")
    for i in range(6, n + 1):
        ae_league[f"D{i}"] = f'=INDEX(Data_AE!$D$2:$D${n_ae},MATCH(B{i},AE_Code,0))'
    ae_league["K5"] = "Target"
    style_header(ae_league, 5, 11, 11)
    ae_league["K6"] = f'=IFERROR(INDEX(TGT_Value,MATCH("ae_4hr_pct|"&{fy_formula(key_ae)},TGT_Key,0)),"")'
    ae_league["K6"].number_format = PCT
    ae_league.conditional_formatting.add(f"F6:F{n}", FormulaRule(
        formula=['AND(ISNUMBER(F6),F6>=$K$6)'], fill=PatternFill("solid", fgColor=GREEN)))
    ae_league.conditional_formatting.add(f"F6:F{n}", FormulaRule(
        formula=['AND(ISNUMBER(F6),F6<$K$6)'], fill=PatternFill("solid", fgColor=RED)))
    ae_league.conditional_formatting.add(f"H6:H{n}", ColorScaleRule(
        start_type="num", start_value=-0.1, start_color="F8696B", mid_type="num", mid_value=0,
        mid_color="FFFFFF", end_type="num", end_value=0.1, end_color="63BE7B"))
    ae_league.conditional_formatting.add(f"I6:I{n}", DataBarRule(
        start_type="min", end_type="max", color="DA291C"))
    ae_league.freeze_panes = "D6"
    ae_league.auto_filter.ref = f"A5:I{n}"
    set_widths(ae_league, {"A": 7, "B": 8, "C": 48, "D": 26, "E": 13, "F": 12, "G": 13, "H": 12, "I": 16,
                           "K": 10})

    rtt_spec = [
        ("Waiting list", "=SUMIFS(RTT_WL,RTT_Code,$B{r},RTT_Key,{key})", INT),
        ("Within 18 weeks", '=IFERROR(SUMIFS(RTT_Within,RTT_Code,$B{r},RTT_Key,{key})/E{r},"")', PCT),
        ("Same month last year",
         '=IFERROR(SUMIFS(RTT_Within,RTT_Code,$B{r},RTT_Key,{key}-100)/SUMIFS(RTT_WL,RTT_Code,$B{r},RTT_Key,{key}-100),"")',
         PCT),
        ("Change (points)", '=IFERROR(F{r}-G{r},"")', "+0.0%;-0.0%;0.0%"),
        ("Over 52 weeks", "=SUMIFS(RTT_52,RTT_Code,$B{r},RTT_Key,{key})", INT),
        ("Share over 52 weeks", '=IFERROR(I{r}/E{r},"")', "0.0%"),
        ("Median wait (weeks)", "=SUMIFS(RTT_Median,RTT_Code,$B{r},RTT_Key,{key})", WEEKS),
    ]
    key_rtt = "Summary!$B$4"
    n = league(rtt_league, "RTT league table: patients waiting under 18 weeks",
               f["rtt_league"].provider_code.tolist(), rtt_spec, key_rtt, "F", "rtt_18wk_pct",
               "NHS trusts with 1,000+ patients waiting. Green = at or above this year's national ambition.")
    for i in range(6, n + 1):
        rtt_league[f"D{i}"] = f'=INDEX(Data_RTT!$D$2:$D${n_rtt},MATCH(B{i},RTT_Code,0))'
    rtt_league["M5"] = "Target"
    style_header(rtt_league, 5, 13, 13)
    rtt_league["M6"] = f'=IFERROR(INDEX(TGT_Value,MATCH("rtt_18wk_pct|"&{fy_formula(key_rtt)},TGT_Key,0)),"")'
    rtt_league["M6"].number_format = PCT
    rtt_league.conditional_formatting.add(f"F6:F{n}", FormulaRule(
        formula=['AND(ISNUMBER(F6),F6>=$M$6)'], fill=PatternFill("solid", fgColor=GREEN)))
    rtt_league.conditional_formatting.add(f"F6:F{n}", FormulaRule(
        formula=['AND(ISNUMBER(F6),F6<$M$6)'], fill=PatternFill("solid", fgColor=RED)))
    rtt_league.conditional_formatting.add(f"J6:J{n}", ColorScaleRule(
        start_type="min", start_color="FFFFFF", end_type="max", end_color="F8696B"))
    rtt_league.conditional_formatting.add(f"H6:H{n}", ColorScaleRule(
        start_type="num", start_value=-0.05, start_color="F8696B", mid_type="num", mid_value=0,
        mid_color="FFFFFF", end_type="num", end_value=0.05, end_color="63BE7B"))
    rtt_league.freeze_panes = "D6"
    rtt_league.auto_filter.ref = f"A5:K{n}"
    set_widths(rtt_league, {"A": 7, "B": 8, "C": 48, "D": 26, "E": 13, "F": 12, "G": 13, "H": 12,
                            "I": 13, "J": 12, "K": 12, "M": 10})

    # --- regions ----------------------------------------------------------
    rg = regions
    rg["A1"] = "Regions"
    rg["A1"].font = H1
    region_names = list(warehouse.REGIONS.values())

    def matrix(top: int, title: str, num: str, den: str, key_name: str, region_name: str, key_cell: str):
        rg.cell(row=top, column=1, value=title).font = H2
        rg.cell(row=top + 1, column=1, value="Region")
        for i in range(13):
            col = 14 - i  # oldest on the left
            ref = f"{get_column_letter(col)}{top + 1}"
            if i == 0:
                formula = f"={key_cell}"
            else:
                nxt = f"{get_column_letter(col + 1)}{top + 1}"
                formula = f"={prev_month(nxt)}"
            rg[ref] = formula
            rg[ref].number_format = "0"
        style_header(rg, top + 1, 1, 14)
        rg.cell(row=top + 2, column=1, value="Month").font = BOLD
        for col in range(2, 15):
            ref = f"{get_column_letter(col)}{top + 1}"
            c = rg.cell(row=top + 2, column=col, value=f"=DATE(INT({ref}/100),MOD({ref},100),1)")
            c.number_format = "mmm yy"
            c.font = BOLD
        for j, name in enumerate(region_names + ["England"], start=top + 3):
            rg.cell(row=j, column=1, value=name).font = BOLD if name == "England" else BODY
            for col in range(2, 15):
                k = f"{get_column_letter(col)}${top + 1}"
                if name == "England":
                    f_ = f'=IFERROR(SUMIFS({num},{key_name},{k})/SUMIFS({den},{key_name},{k}),"")'
                else:
                    f_ = (f'=IFERROR(SUMIFS({num},{region_name},$A{j},{key_name},{k})/'
                          f'SUMIFS({den},{region_name},$A{j},{key_name},{k}),"")')
                cell = rg.cell(row=j, column=col, value=f_)
                cell.number_format = "0.0%"
                cell.font = BODY
        last = top + 3 + len(region_names)
        rg.conditional_formatting.add(f"B{top + 3}:N{last - 1}", ColorScaleRule(
            start_type="min", start_color="F8696B", mid_type="percentile", mid_value=50,
            mid_color="FFEB84", end_type="max", end_color="63BE7B"))
        return last

    last = matrix(3, "A&E: seen within 4 hours", "AE_Within", "AE_Att", "AE_Key", "AE_Region", "Summary!$B$3")
    matrix(last + 3, "RTT: waiting under 18 weeks", "RTT_Within", "RTT_WL", "RTT_Key", "RTT_Region",
           "Summary!$B$4")
    rg.column_dimensions["A"].width = 28
    for col in range(2, 15):
        rg.column_dimensions[get_column_letter(col)].width = 9

    # --- forecast ---------------------------------------------------------
    fw = fc_ws
    fw["A1"] = "England forecasts"
    fw["A1"].font = H1
    fw["A2"] = ("Model output (values, not formulas). The 80% range is where 8 in 10 outcomes fell when the "
                "same method was tested on the past six months it had not seen.")
    fw["A2"].font = NOTE
    fc = f["forecast"]
    labels = {"ae_4hr_pct": ("A&E within 4 hours", PCT), "ae_attendances": ("A&E attendances", INT),
              "rtt_18wk_pct": ("RTT within 18 weeks", PCT), "rtt_waiting_list": ("RTT waiting list", INT)}
    row = 4
    for metric, (label, fmt) in labels.items():
        part = fc[fc.metric == metric]
        if part.empty:
            continue
        recent = part[part.kind == "actual"].tail(6)
        ahead = part[part.kind == "forecast"]
        fw.cell(row=row, column=1, value=label).font = H2
        for i, h in enumerate(["Month", "Type", "Value", "Low (80%)", "High (80%)"], start=1):
            fw.cell(row=row + 1, column=i, value=h)
        style_header(fw, row + 1, 1, 5)
        r = row + 2
        for rec in pd.concat([recent, ahead]).itertuples(index=False):
            fw.cell(row=r, column=1, value=month_label(int(rec.month_key))).font = BODY
            fw.cell(row=r, column=2, value="Actual" if rec.kind == "actual" else "Forecast").font = BODY
            for c, v in ((3, rec.value), (4, rec.lower_80), (5, rec.upper_80)):
                cell = fw.cell(row=r, column=c, value=None if pd.isna(v) else float(v))
                cell.number_format = fmt
                cell.font = BOLD if rec.kind == "forecast" and c == 3 else BODY
            r += 1
        row = r + 2
    bt = f["backtest"]
    if not bt.empty:
        fw.cell(row=row, column=1, value="How each method scored (mean absolute error, past 6 months)").font = H2
        piv = bt.pivot_table(index=["metric", "horizon"], columns="model", values="mae").reset_index()
        chosen = bt[bt.chosen == 1].groupby("metric")["model"].first()
        heads = ["Measure", "Months ahead"] + [c for c in piv.columns[2:]] + ["Used"]
        for i, h in enumerate(heads, start=1):
            fw.cell(row=row + 1, column=i, value=h)
        style_header(fw, row + 1, 1, len(heads))
        r = row + 2
        for rec in piv.itertuples(index=False):
            fw.cell(row=r, column=1, value=labels.get(rec[0], (rec[0],))[0]).font = BODY
            fw.cell(row=r, column=2, value=int(rec[1])).font = BODY
            fmt = "0.00%"
            for i, v in enumerate(rec[2:], start=3):
                cell = fw.cell(row=r, column=i, value=float(v))
                cell.number_format = fmt
                cell.font = BODY
            fw.cell(row=r, column=len(heads), value=chosen.get(rec[0], "")).font = BODY
            r += 1
        fw.cell(row=r, column=1, value=("Errors are percentage points for rates and % of the actual value for "
                                        "counts. gbm = gradient boosting model; last_value = same as latest "
                                        "month; seasonal_naive = same as a year earlier.")).font = NOTE
    set_widths(fw, {"A": 30, "B": 14, "C": 14, "D": 14, "E": 16, "F": 10})

    # --- data quality -----------------------------------------------------
    q = dq_ws
    q["A1"] = "Data quality"
    q["A1"].font = H1
    q["A3"] = "Checks run on this load (an 'error' that fails stops the pipeline)"
    q["A3"].font = H2
    for i, h in enumerate(["Dataset", "Check", "Severity", "Result", "Detail"], start=1):
        q.cell(row=4, column=i, value=h)
    style_header(q, 4, 1, 5)
    r = 5
    for rec in f["dq"].itertuples(index=False):
        vals = [rec.dataset.upper(), rec.check_name, rec.severity, "Pass" if rec.passed else "Fail", rec.detail]
        for c, v in enumerate(vals, start=1):
            q.cell(row=r, column=c, value=v).font = BODY
        r += 1
    q.conditional_formatting.add(f"D5:D{r}", CellIsRule(
        operator="equal", formula=['"Pass"'], fill=PatternFill("solid", fgColor=GREEN)))
    q.conditional_formatting.add(f"D5:D{r}", CellIsRule(
        operator="equal", formula=['"Fail"'], fill=PatternFill("solid", fgColor=AMBER)))
    r += 2
    q.cell(row=r, column=1, value="Unusual months (change far outside the organisation's normal range)").font = H2
    an = f["anomalies"].head(60)
    heads = ["Measure", "Code", "Organisation", "Month", "Value", "Expected", "Robust z"]
    for i, h in enumerate(heads, start=1):
        q.cell(row=r + 1, column=i, value=h)
    style_header(q, r + 1, 1, len(heads))
    r += 2
    if an.empty:
        q.cell(row=r, column=1, value="None flagged.").font = BODY
        r += 1
    for rec in an.itertuples(index=False):
        fmt = PCT if rec.metric.endswith("pct") else INT
        vals = [labels.get(rec.metric, (rec.metric,))[0], rec.provider_code,
                rec.provider_name or "England", month_label(int(rec.month_key)), rec.value, rec.expected,
                round(rec.robust_z, 1)]
        for c, v in enumerate(vals, start=1):
            cell = q.cell(row=r, column=c, value=v)
            cell.font = BODY
            if c in (5, 6):
                cell.number_format = fmt
        r += 1
    r += 2
    q.cell(row=r, column=1, value="Source files loaded").font = H2
    heads = ["Dataset", "Month", "Rows", "Source URL", "SHA-256"]
    for i, h in enumerate(heads, start=1):
        q.cell(row=r + 1, column=i, value=h)
    style_header(q, r + 1, 1, len(heads))
    r += 2
    for rec in f["load_log"].itertuples(index=False):
        for c, v in enumerate([rec.dataset.upper(), rec.period, rec.rows_loaded, rec.source_url, rec.sha256],
                              start=1):
            q.cell(row=r, column=c, value=v).font = BODY
        r += 1
    set_widths(q, {"A": 22, "B": 44, "C": 40, "D": 60, "E": 30, "F": 12, "G": 10})

    # --- definitions ------------------------------------------------------
    rows_def = [
        ("Measure", "Definition", "Source"),
        ("A&E: seen within 4 hours", "Attendances at all A&E types (including booked appointments) where the "
         "patient was admitted, transferred or discharged within 4 hours of arrival, divided by all "
         "attendances.", "A&E Attendances and Emergency Admissions (monthly provider CSV)"),
        ("A&E: type 1 within 4 hours", "As above for major emergency departments (type 1) only.", "As above"),
        ("12+ hours from decision to admit", "Patients admitted via A&E who waited more than 12 hours from the "
         "decision to admit to being admitted (a 'trolley wait').", "As above"),
        ("RTT waiting list", "Incomplete consultant-led referral to treatment pathways at month end (RTT part "
         "2), all treatment functions, summed over commissioners.", "RTT Waiting Times full CSV extract"),
        ("RTT: waiting under 18 weeks", "Share of the waiting list in the weekly bands below 18 weeks.",
         "As above"),
        ("RTT: over 52 weeks", "Pathways waiting 52 weeks or more.", "As above"),
        ("RTT median wait", "Interpolated from the weekly bands. England medians are worked out from England's "
         "summed bands, not by averaging trust medians.", "As above"),
        ("Region", "NHS England region of the organisation, taken from the A&E file's parent organisation.",
         "A&E file"),
        ("Forecast", "Best of three methods in a rolling backtest: gradient boosting (scikit-learn) on recent "
         "changes and seasonality, same as last month, or same as a year ago.", "This pipeline"),
        ("Data protection", "All inputs are published aggregate statistics for organisations. No patient-level "
         "or personal data is collected, stored or derived, so UK GDPR special category rules are not "
         "engaged. Figures are reproduced as published; NHS England already applies its own disclosure "
         "control.", "Open Government Licence v3.0"),
    ]
    defs["A1"] = "Definitions"
    defs["A1"].font = H1
    for r, row_vals in enumerate(rows_def, start=3):
        for c, v in enumerate(row_vals, start=1):
            cell = defs.cell(row=r, column=c, value=v)
            cell.font = BODY
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    style_header(defs, 3, 1, 3)
    set_widths(defs, {"A": 32, "B": 90, "C": 45})

    for ws in wb.worksheets:
        ws.sheet_view.showGridLines = ws.title.startswith("Data_") or ws.title in ("Targets", "Lists")
    summary.sheet_properties.tabColor = BLUE
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    log.info("wrote %s", out_path)
    return out_path
