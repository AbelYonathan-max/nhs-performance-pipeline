"""nhsp: NHS England A&E and RTT performance pipeline.

  nhsp fetch      download new monthly files from NHS England
  nhsp build      clean the files, run the quality checks, load data/warehouse.db
  nhsp forecast   backtest and forecast, flag unusual months
  nhsp export     write Parquet files for Power BI to data/out
  nhsp excel      write the Excel reporting pack
  nhsp all        every step above in order
  nhsp publish    commit data/out to the repo's `data` branch and push it
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from . import excel_pack, fetch, pipeline, warehouse


def pack_name(db: Path) -> str:
    key = int(warehouse.query(db, "SELECT MAX(month_key) AS k FROM fact_ae").k[0])
    return f"NHS-performance-pack-{key // 100}-{key % 100:02d}.xlsx"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nhsp", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--raw", type=Path, default=Path("data/raw"), help="raw file folder")
    parser.add_argument("--db", type=Path, default=Path("data/warehouse.db"), help="SQLite file")
    parser.add_argument("--out", type=Path, default=Path("data/out"), help="published files folder")
    parser.add_argument("--targets", type=Path, default=Path("config/targets.csv"))
    parser.add_argument("--first-fy", default="2023-24", help="first financial year to fetch")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    p_fetch = sub.add_parser("fetch", help="download monthly files from NHS England")
    p_fetch.add_argument("--dataset", action="append", choices=list(fetch.DATASETS))
    for name in ("build", "forecast", "export", "excel", "all"):
        sub.add_parser(name)
    p_pub = sub.add_parser("publish", help="push data/out to the data branch")
    p_pub.add_argument("--no-push", action="store_true", help="commit only")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    steps = ["fetch", "build", "forecast", "export", "excel"] if args.command == "all" else [args.command]
    for step in steps:
        if step == "fetch":
            entries = fetch.fetch(args.raw, args.first_fy, getattr(args, "dataset", None))
            print(f"{len(entries)} files in {args.raw / 'manifest.json'}")
        elif step == "build":
            pipeline.build(args.raw, args.db, args.targets)
        elif step == "forecast":
            pipeline.run_forecasts(args.db)
        elif step == "export":
            pipeline.export(args.db, args.out)
        elif step == "excel":
            for old in args.out.glob("NHS-performance-pack-*.xlsx"):
                old.unlink()
            path = excel_pack.build(args.db, args.out / pack_name(args.db))
            print(f"Excel pack: {path}")
        elif step == "publish":
            print(f"published: {pipeline.publish(args.out, args.raw, push=not args.no_push)}")
    return 0
