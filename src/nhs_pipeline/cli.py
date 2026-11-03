"""Command line entry point: `nhsp <command>`."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from . import fetch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nhsp", description=__doc__)
    parser.add_argument("--raw", type=Path, default=Path("data/raw"))
    parser.add_argument("--first-fy", default="2023-24")
    sub = parser.add_subparsers(dest="command", required=True)
    p_fetch = sub.add_parser("fetch", help="download monthly files from NHS England")
    p_fetch.add_argument("--dataset", action="append", choices=list(fetch.DATASETS))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.command == "fetch":
        entries = fetch.fetch(args.raw, args.first_fy, args.dataset)
        print(f"{len(entries)} files in {args.raw / 'manifest.json'}")
    return 0
