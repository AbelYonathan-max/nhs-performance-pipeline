"""Make small raw files in the same layout NHS England publishes.

Used by the tests so they run offline. Numbers are random, not real.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

MONTH_NAMES = [
    "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
    "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
]

PROVIDERS = [
    ("RJ1", "GUY'S AND ST THOMAS' NHS FOUNDATION TRUST", "NHS ENGLAND LONDON"),
    ("RAL", "ROYAL FREE LONDON NHS FOUNDATION TRUST", "NHS ENGLAND LONDON"),
    ("RTH", "OXFORD UNIVERSITY HOSPITALS NHS FOUNDATION TRUST", "NHS ENGLAND SOUTH EAST"),
    ("RR8", "LEEDS TEACHING HOSPITALS NHS TRUST", "NHS ENGLAND NORTH EAST AND YORKSHIRE"),
    ("RRK", "UNIVERSITY HOSPITALS BIRMINGHAM NHS FOUNDATION TRUST", "NHS ENGLAND MIDLANDS"),
]

AE_HEADER = [
    "Period", "Org Code", "Parent Org", "Org name",
    "A&E attendances Type 1", "A&E attendances Type 2", "A&E attendances Other A&E Department",
    "A&E attendances Booked Appointments Type 1", "A&E attendances Booked Appointments Type 2",
    "A&E attendances Booked Appointments Other Department",
    "Attendances over 4hrs Type 1", "Attendances over 4hrs Type 2",
    "Attendances over 4hrs Other Department", "Attendances over 4hrs Booked Appointments Type 1",
    "Attendances over 4hrs Booked Appointments Type 2",
    "Attendances over 4hrs Booked Appointments Other Department",
    "Patients who have waited 4-12 hs from DTA to admission",
    "Patients who have waited 12+ hrs from DTA to admission",
    "Emergency admissions via A&E - Type 1", "Emergency admissions via A&E - Type 2",
    "Emergency admissions via A&E - Other A&E department", "Other emergency admissions",
]

TREATMENT_FUNCTIONS = [("C_100", "General Surgery Service"), ("C_110", "Trauma and Orthopaedic Service")]
PARTS = [("Part_1A", "Completed Pathways For Admitted Patients"),
         ("Part_1B", "Completed Pathways For Non-Admitted Patients"),
         ("Part_2", "Incomplete Pathways"),
         ("Part_2A", "Incomplete Pathways with DTA"),
         ("Part_3", "New RTT Periods - All Patients")]
WEEK_COLS = [f"Gt {i:02d} To {i + 1:02d} Weeks SUM 1" for i in range(104)] + ["Gt 104 Weeks SUM 1"]


def periods(first: str, count: int) -> list[str]:
    start = pd.Period(first, "M")
    return [str(start + i) for i in range(count)]


def ae_csv(period: str, rng: np.random.Generator) -> bytes:
    year, month = map(int, period.split("-"))
    seasonal = 1 + 0.08 * np.cos((month - 1) / 12 * 2 * np.pi)
    rows = []
    for i, (code, name, region) in enumerate(PROVIDERS):
        t1 = int(rng.normal(12000 + 1500 * i, 400) * seasonal)
        other = int(rng.normal(3000, 200))
        perf = np.clip(0.72 + 0.02 * i - 0.04 * (seasonal - 1) * 10 + rng.normal(0, 0.01), 0.4, 0.99)
        over4_t1 = int(t1 * (1 - perf + 0.12))
        row = [f"MSitAE-{MONTH_NAMES[month - 1]}-{year}", code, region, name,
               t1, 0, other, 50, 0, 10, over4_t1, 0, int(other * 0.02), 5, 0, "-",
               int(t1 * 0.04), int(t1 * 0.01), int(t1 * 0.25), 0, 5, int(t1 * 0.1)]
        rows.append(row)
    # NHS England also lists small sites with blanks.
    rows.append([f"MSitAE-{MONTH_NAMES[month - 1]}-{year}", "NLO12", "NHS ENGLAND LONDON",
                 "CENTRAL LONDON COMMUNITY HEALTHCARE", 0, 0, 2500, 0, 0, 0, 0, 0, 1, 0, 0, 0,
                 0, 0, 0, 0, 0, 0])
    frame = pd.DataFrame(rows, columns=AE_HEADER)
    return frame.to_csv(index=False).encode("latin-1")


def rtt_zip(period: str, rng: np.random.Generator) -> bytes:
    year, month = map(int, period.split("-"))
    rows = []
    for i, (code, name, _region) in enumerate(PROVIDERS):
        for commissioner in ("QMJ", "QRV"):
            for part, desc in PARTS:
                decay = 0.06 + 0.005 * i + 0.0005 * (year - 2023) * 12
                weights = np.exp(-decay * np.arange(105))
                size = {"Part_2": 4000, "Part_2A": 800}.get(part, 600)
                by_tf = [rng.poisson(weights / weights.sum() * size) for _ in TREATMENT_FUNCTIONS]
                for (tf_code, tf_name), counts in zip(
                    TREATMENT_FUNCTIONS + [("C_999", "Total")], by_tf + [sum(by_tf)], strict=True
                ):
                    measures = [*counts, counts.sum(), 3, counts.sum() + 3]
                    if part == "Part_3":  # new clock starts: only "Total All" is filled in
                        measures = [""] * (len(counts) + 2) + [counts.sum()]
                    rows.append([f"RTT-{MONTH_NAMES[month - 1].title()}-{year}", "QKK",
                                 "NHS SOUTH EAST LONDON INTEGRATED CARE BOARD", code, name, "QKK",
                                 "NHS SOUTH EAST LONDON INTEGRATED CARE BOARD", commissioner,
                                 "NHS X ICB", part, desc, tf_code, tf_name, *measures])
    header = ["Period", "Provider Parent Org Code", "Provider Parent Name", "Provider Org Code",
              "Provider Org Name", "Commissioner Parent Org Code", "Commissioner Parent Name",
              "Commissioner Org Code", "Commissioner Org Name", "RTT Part Type",
              "RTT Part Description", "Treatment Function Code", "Treatment Function Name",
              *WEEK_COLS, "Total", "Patients with unknown clock start date", "Total All"]
    csv = pd.DataFrame(rows, columns=header).to_csv(index=False).encode("latin-1")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{period}-RTT-full-extract.csv", csv)
    return buffer.getvalue()


def write_raw(raw_dir: Path, first: str = "2023-04", count: int = 30, seed: int = 7) -> list[dict]:
    rng = np.random.default_rng(seed)
    manifest = []
    for period in periods(first, count):
        for dataset, make, suffix in (("ae", ae_csv, ".csv"), ("rtt", rtt_zip, ".zip")):
            path = raw_dir / dataset / f"{period}{suffix}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(make(period, rng))
            manifest.append({
                "dataset": dataset, "period": period, "url": f"https://example.invalid/{path.name}",
                "link_text": "synthetic", "page": "synthetic", "revised": False,
                "path": f"{dataset}/{path.name}", "sha256": "0" * 64, "bytes": path.stat().st_size,
                "downloaded_at": "2026-11-01T00:00:00+00:00",
            })
    (raw_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
