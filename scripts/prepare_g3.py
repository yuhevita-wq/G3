#!/usr/bin/env python3
"""Reproducible, lossless G3 ZIP -> indexed year-by-year SQLite preparation."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import re
import shutil
import sqlite3
import sys
import time
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "source_manifest.csv"
ARCHIVES = ROOT / "data" / "archives"
LOCAL = ROOT / "data" / "local"
READY = ROOT / "data" / "ready"
REPORTS = ROOT / "reports" / "ingestion"
SOURCE_REPOSITORY = "yuhevita-wq/keirin-market-analysis-"
TABLE_FILES = (
    "races.csv", "entries.csv", "results.csv", "payouts.csv",
    "trifecta_final_odds.csv", "trio_final_odds.csv",
    "failures.csv", "line_failures.csv", "result_failures.csv",
    "odds_failures.csv", "excluded_girls.csv",
)
REQUIRED = set(TABLE_FILES[:6])
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")


def quote(name: str) -> str:
    if not IDENTIFIER.fullmatch(name):
        raise ValueError(f"Unexpected CSV column name: {name!r}")
    return '"' + name + '"'


def manifest_rows() -> list[dict[str, str]]:
    with MANIFEST.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    periods = [r["period"] for r in rows]
    if len(rows) != 18 or len(periods) != len(set(periods)):
        raise ValueError("Expected exactly 18 distinct G3 quarterly archives")
    return rows


def choose(rows: list[dict[str, str]], year: str) -> list[dict[str, str]]:
    if year == "all":
        return rows
    chosen = [r for r in rows if r["year"] == year]
    if not chosen:
        raise ValueError(f"No source rows for year {year}")
    return chosen


def archive_path(r: dict[str, str]) -> Path:
    return ARCHIVES / r["year"] / Path(r["source_path"]).name


def hash_file(p: Path) -> str:
    digest = hashlib.sha256()
    with p.open("rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(r: dict[str, str], path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size != int(r["bytes"]):
        raise RuntimeError(f"Byte size mismatch: {path}")
    if hash_file(path).lower() != r["sha256"].lower():
        raise RuntimeError(f"SHA-256 mismatch: {path}")
    with zipfile.ZipFile(path) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError(f"Broken ZIP member: {path}: {bad}")


def sync(rows: list[dict[str, str]]) -> None:
    for r in rows:
        dst = archive_path(r)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            verify(r, dst)
            print(f"VERIFIED {r['period']} {dst.relative_to(ROOT)}", flush=True)
            continue
        url = "https://raw.githubusercontent.com/" + SOURCE_REPOSITORY + "/main/" + r["source_path"]
        temp = dst.with_suffix(dst.suffix + ".partial")
        for attempt in range(1, 5):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "G3-archive-sync/1.0"})
                with urllib.request.urlopen(request, timeout=90) as src, temp.open("wb") as out:
                    shutil.copyfileobj(src, out)
                verify(r, temp)
                temp.replace(dst)
                break
            except Exception:
                temp.unlink(missing_ok=True)
                if attempt == 4:
                    raise
                time.sleep(attempt * 2)
        print(f"DOWNLOADED {r['period']} {dst.relative_to(ROOT)}", flush=True)


def csv_members(z: zipfile.ZipFile) -> dict[str, str]:
    members = {}
    for item in z.infolist():
        if item.is_dir():
            continue
        base = Path(item.filename).name
        if base in TABLE_FILES:
            if base in members:
                raise ValueError(f"Ambiguous duplicate ZIP member: {base}")
            members[base] = item.filename
    if not REQUIRED.issubset(members):
        raise RuntimeError(f"Required CSV files missing: {sorted(REQUIRED - set(members))}")
    return members


def import_member(db: sqlite3.Connection, z: zipfile.ZipFile, member: str,
                  table: str, archive: dict[str, str]) -> int:
    with z.open(member) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as txt:
        reader = csv.DictReader(txt)
        columns = reader.fieldnames
        if not columns or len(columns) != len(set(columns)):
            raise ValueError(f"Malformed CSV header: {member}")
        for name in columns:
            quote(name)
        if "race_id" not in columns and table not in ("failures", "excluded_girls"):
            raise ValueError(f"race_id missing in {member}")
        existing = {row[1] for row in db.execute(f"PRAGMA table_info({quote(table)})")}
        if not existing:
            schema = ", ".join(quote(c) + " TEXT" for c in columns)
            db.execute(
                f"CREATE TABLE {quote(table)} ({schema}, "
                "archive_period TEXT NOT NULL, archive_source_path TEXT NOT NULL)"
            )
        else:
            for c in columns:
                if c not in existing:
                    db.execute(f"ALTER TABLE {quote(table)} ADD COLUMN {quote(c)} TEXT")
        fields = columns + ["archive_period", "archive_source_path"]
        statement = (
            f"INSERT INTO {quote(table)} ("
            + ",".join(quote(f) for f in fields)
            + ") VALUES (" + ",".join("?" for _ in fields) + ")"
        )
        batch, count = [], 0
        for row in reader:
            if None in row:
                raise ValueError(f"Extra CSV fields in {member}, row {count+2}")
            batch.append([row[c] for c in columns] + [archive["period"], archive["source_path"]])
            if len(batch) >= 6000:
                db.executemany(statement, batch)
                count += len(batch)
                batch.clear()
        if batch:
            db.executemany(statement, batch)
            count += len(batch)
        return count


def build_year(year: str, archives: list[dict[str, str]]) -> dict:
    LOCAL.mkdir(parents=True, exist_ok=True)
    READY.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    db_path = LOCAL / f"g3_{year}.sqlite"
    temp_path = LOCAL / f"g3_{year}.building.sqlite"
    temp_path.unlink(missing_ok=True)
    report: dict = {
        "year": year,
        "source_repository": SOURCE_REPOSITORY,
        "archive_periods": [r["period"] for r in archives],
        "source_files": [],
        "raw_row_counts_by_period": {},
        "odds_warning": "Only FINAL post-race odds. Never assume pre-bet/pre-deadline availability.",
        "line_warning": "Predicted lines are provider-published estimates, not official formations.",
    }
    db = sqlite3.connect(temp_path)
    try:
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA cache_size=-60000")
        db.execute("PRAGMA temp_store=MEMORY")
        for a in archives:
            p = archive_path(a)
            verify(a, p)
            counts = {}
            with zipfile.ZipFile(p) as z:
                members = csv_members(z)
                for filename, member in members.items():
                    table = filename.removesuffix(".csv")
                    counts[table] = import_member(db, z, member, table, a)
                report["source_files"].append({
                    "period": a["period"], "path": str(p.relative_to(ROOT)),
                    "bytes": p.stat().st_size, "sha256": a["sha256"],
                    "members": sorted(members),
                })
            db.commit()
            report["raw_row_counts_by_period"][a["period"]] = counts
            print(f"IMPORTED {a['period']}: {counts}", flush=True)

        index_statements = (
            'CREATE UNIQUE INDEX uq_races ON races(race_id)',
            'CREATE UNIQUE INDEX uq_entries ON entries(race_id,car_no)',
            'CREATE UNIQUE INDEX uq_results ON results(race_id,car_no)',
            'CREATE UNIQUE INDEX uq_trifecta ON trifecta_final_odds(race_id,combination)',
            'CREATE UNIQUE INDEX uq_trio ON trio_final_odds(race_id,combination)',
        )
        for sql in index_statements:
            db.execute(sql)
        for table in TABLE_FILES:
            key = table.removesuffix(".csv")
            present = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (key,)).fetchone()
            if not present:
                continue
            if key in ("races", "entries", "results", "payouts", "trifecta_final_odds", "trio_final_odds"):
                db.execute(f"CREATE INDEX {quote('ix_' + key + '_date')} ON {quote(key)}(race_date)")
                if key != "races":
                    db.execute(f"CREATE INDEX {quote('ix_' + key + '_race')} ON {quote(key)}(race_id)")
        db.executescript("""
            CREATE VIEW v_pre_race_races AS SELECT * FROM races;
            CREATE VIEW v_pre_race_entries AS SELECT * FROM entries;
            CREATE VIEW v_results AS SELECT * FROM results;
            CREATE VIEW v_paid_trifecta AS
              SELECT race_id, race_date, combination,
                     CAST(payout_yen AS INTEGER) AS payout_yen,
                     popularity, archive_period
              FROM payouts WHERE ticket_type='3連単' AND status='paid';
            CREATE VIEW v_trifecta_final_odds AS
              SELECT race_id, race_date, combination,
                     CASE WHEN trim(odds)='' THEN NULL ELSE CAST(odds AS REAL) END AS odds,
                     market_rank, odds_phase, odds_as_of, archive_period
              FROM trifecta_final_odds;
            CREATE VIEW v_trio_final_odds AS
              SELECT race_id, race_date, combination,
                     CASE WHEN trim(odds)='' THEN NULL ELSE CAST(odds AS REAL) END AS odds,
                     market_rank, odds_phase, odds_as_of, archive_period
              FROM trio_final_odds;
        """)
        db.commit()

        counts = {}
        for f in TABLE_FILES:
            tab = f.removesuffix(".csv")
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tab,)).fetchone()
            counts[tab] = int(db.execute(f"SELECT COUNT(*) FROM {quote(tab)}").fetchone()[0]) if exists else 0
        qc = {}
        for name, table in [
            ("races_missing_entries", "entries"),
            ("races_missing_results", "results"),
            ("races_missing_trifecta_odds", "trifecta_final_odds"),
            ("races_missing_trio_odds", "trio_final_odds"),
            ("races_missing_payouts", "payouts"),
        ]:
            qc[name] = db.execute(
                f"SELECT COUNT(*) FROM races r WHERE NOT EXISTS "
                f"(SELECT 1 FROM {quote(table)} t WHERE t.race_id=r.race_id)"
            ).fetchone()[0]
        for t in ("entries", "results", "payouts", "trifecta_final_odds", "trio_final_odds"):
            qc[f"orphan_{t}_rows"] = db.execute(
                f"SELECT COUNT(*) FROM {quote(t)} t WHERE NOT EXISTS "
                "(SELECT 1 FROM races r WHERE r.race_id=t.race_id)"
            ).fetchone()[0]
        qc["non_g3_races"] = db.execute(
            "SELECT COUNT(*) FROM races WHERE meeting_grade IS NOT NULL AND meeting_grade NOT IN ('G3','Ｇ３')"
        ).fetchone()[0]
        qc["race_date_outside_year"] = db.execute(
            "SELECT COUNT(*) FROM races WHERE substr(race_date,1,4)!=?", (year,)
        ).fetchone()[0]
        qc["race_line_missing"] = db.execute(
            "SELECT COUNT(*) FROM races WHERE predicted_line_formation IS NULL OR trim(predicted_line_formation)=''"
        ).fetchone()[0]
        qc["odds_non_final_rows"] = db.execute(
            "SELECT COUNT(*) FROM trifecta_final_odds WHERE odds_phase!='final' OR odds_phase IS NULL"
        ).fetchone()[0]
        qc["race_min_date"], qc["race_max_date"] = db.execute(
            "SELECT MIN(race_date),MAX(race_date) FROM races"
        ).fetchone()
        report["row_counts"] = counts
        report["quality"] = qc

        # Critical integrity errors abort rather than silently changing source observations.
        if any(qc[x] for x in qc if x.startswith("orphan_")) or qc["non_g3_races"] or qc["race_date_outside_year"]:
            raise RuntimeError(f"Critical race-key/date/grade integrity errors: {qc}")
        db.execute("VACUUM")
        db.close()
        db = None
        temp_path.replace(db_path)
        compressed = READY / f"g3_{year}.sqlite.gz"
        partial = compressed.with_suffix(compressed.suffix + ".partial")
        with db_path.open("rb") as inp, gzip.open(partial, "wb", compresslevel=6) as out:
            shutil.copyfileobj(inp, out, 1024 * 1024)
        partial.replace(compressed)
        report["sqlite_uncompressed_bytes"] = db_path.stat().st_size
        report["sqlite_gzip_bytes"] = compressed.stat().st_size
        report["sqlite_gzip_sha256"] = hash_file(compressed)
        (REPORTS / f"{year}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"BUILT {year}: races={counts['races']} trifecta={counts['trifecta_final_odds']} "
              f"sqlite_gz={report['sqlite_gzip_bytes']}", flush=True)
        return report
    finally:
        if db is not None:
            db.close()
        temp_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("sync", "build", "all", "verify"))
    parser.add_argument("--year", default="all", choices=["all", "2022", "2023", "2024", "2025", "2026"])
    args = parser.parse_args()
    selected = choose(manifest_rows(), args.year)
    if args.command in ("sync", "all"):
        sync(selected)
    if args.command == "verify":
        for row in selected:
            verify(row, archive_path(row))
            print("OK", row["period"])
    if args.command in ("build", "all"):
        if args.command == "build":
            for row in selected:
                verify(row, archive_path(row))
        groups = defaultdict(list)
        for row in selected:
            groups[row["year"]].append(row)
        results = [build_year(year, items) for year, items in sorted(groups.items())]
        print(json.dumps({
            "years": list(sorted(groups)),
            "total_races": sum(x["row_counts"]["races"] for x in results),
            "total_trifecta_odds": sum(x["row_counts"]["trifecta_final_odds"] for x in results),
        }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
