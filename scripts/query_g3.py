#!/usr/bin/env python3
"""Query G3 year-by-year SQLite data (auto-uncompress verified, read-only database)."""
from __future__ import annotations
import argparse
import csv
import gzip
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
READY = ROOT / "data" / "ready"
CACHE = ROOT / "data" / "local"
YEARS = ("2022", "2023", "2024", "2025", "2026")


def ensure_db(year: str) -> Path:
    gz = READY / f"g3_{year}.sqlite.gz"
    db = CACHE / f"g3_{year}.sqlite"
    if not db.exists():
        if not gz.exists():
            raise FileNotFoundError(f"{gz} missing. Run python scripts/prepare_g3.py all --year {year}")
        db.parent.mkdir(parents=True, exist_ok=True)
        temp = db.with_suffix(".unpacking")
        with gzip.open(gz, "rb") as inp, temp.open("wb") as out:
            shutil.copyfileobj(inp, out)
        temp.replace(db)
    return db


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", default="2023", choices=(*YEARS, "all"))
    ap.add_argument("--sql", default="SELECT race_date, track, race_no, race_type FROM races LIMIT 10")
    ap.add_argument("--output", help="Save CSV results here; otherwise print CSV to stdout")
    args = ap.parse_args()
    # This is a read-only query helper; refuse DML and multi-statements.
    sql = args.sql.strip().rstrip(";")
    if not sql.lower().startswith(("select ", "with ")):
        ap.error("Only SELECT/WITH queries are allowed")
    if ";" in sql:
        ap.error("Multiple statements are not supported")
    if args.year == "all":
        conn = sqlite3.connect(":memory:")
        for year in YEARS:
            path = ensure_db(year)
            conn.execute(f"ATTACH DATABASE ? AS y{year}", (f"file:{path}?mode=ro",))
        print("All years are attached as y2022, y2023, y2024, y2025, y2026. Use e.g. y2023.races.", file=sys.stderr)
    else:
        path = ensure_db(args.year)
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cur = conn.execute(sql)
        if args.output:
            target = Path(args.output)
            target.parent.mkdir(parents=True, exist_ok=True)
            output = target.open("w", encoding="utf-8-sig", newline="")
        else:
            output = sys.stdout
        try:
            writer = csv.writer(output)
            writer.writerow([d[0] for d in cur.description])
            for row in cur:
                writer.writerow(row)
        finally:
            if args.output:
                output.close()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
