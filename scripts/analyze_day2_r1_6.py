#!/usr/bin/env python3
"""G3 meeting day 2, races 1-6: trifecta payout and published-line analysis.

Pure stdlib. No changes to original archive. All payout statistics use 100-yen
published winning payout rows; dead heats can generate >1 winning row per race.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date
import csv
import gzip
import io
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import statistics

ROOT = Path(__file__).resolve().parents[1]
DATABASES = ROOT / "data" / "ready"
WORK = ROOT / "data" / "local"
OUTPUT = ROOT / "reports" / "day2_r1_6"
YEARS = (2022, 2023, 2024, 2025, 2026)
BANDS = [
    (0, 1000, "〜999円"),
    (1000, 3000, "1,000〜2,999円"),
    (3000, 5000, "3,000〜4,999円"),
    (5000, 10000, "5,000〜9,999円"),
    (10000, 20000, "10,000〜19,999円"),
    (20000, 50000, "20,000〜49,999円"),
    (50000, 100000, "50,000〜99,999円"),
    (100000, 200000, "100,000〜199,999円"),
    (200000, 500000, "200,000〜499,999円"),
    (500000, float("inf"), "500,000円以上"),
]


def percentile(xs, fraction):
    if not xs:
        return None
    values = sorted(xs)
    position = (len(values) - 1) * fraction
    lo, hi = math.floor(position), math.ceil(position)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def summary(xs):
    xs = list(xs)
    if not xs:
        return {"n": 0}
    n = len(xs)
    freq = Counter(xs)
    max_freq = max(freq.values())
    modes = sorted(v for v, k in freq.items() if k == max_freq)
    lo, hi = int(n * .05), n - int(n * .05)
    sorted_x = sorted(xs)
    return {
        "n": n, "min": min(xs), "max": max(xs),
        "mean": statistics.fmean(xs),
        "median": statistics.median(xs),
        "stddev": statistics.pstdev(xs),
        "trimmed_mean_5pct": statistics.fmean(sorted_x[lo:hi]),
        "p10": percentile(xs, .10), "p25": percentile(xs, .25),
        "p75": percentile(xs, .75), "p90": percentile(xs, .90),
        "p95": percentile(xs, .95), "p99": percentile(xs, .99),
        "mode_frequency": max_freq, "mode_values": modes[:12],
        "mode_tie_count": len(modes),
        "ge_10000": sum(x >= 10000 for x in xs),
        "ge_50000": sum(x >= 50000 for x in xs),
        "ge_100000": sum(x >= 100000 for x in xs),
    }


def norm_car(value):
    try:
        return str(int(value))
    except (ValueError, TypeError):
        return str(value).strip()


def line_map(formation):
    """Return {car_number: (line_id, zero-based position)}; invalid -> None."""
    if not formation or not str(formation).strip():
        return None
    result = {}
    for line_i, segment in enumerate(str(formation).split("/"), start=1):
        cars = [norm_car(c.strip()) for c in segment.split("-")]
        if not cars or any(not re.fullmatch(r"[1-9]", c) for c in cars):
            return None
        for pos, car in enumerate(cars):
            if car in result:
                return None
            result[car] = (line_i, pos)
    return result if result else None


def classify(combo, formation):
    """Interpret '筋' as 1st and 2nd sharing a *published predicted* line."""
    mapping = line_map(formation)
    cars = [norm_car(c) for c in combo.split("-")]
    if not mapping or len(cars) != 3 or len(set(cars)) != 3 or any(c not in mapping for c in cars):
        return {"line_class": "判定不能", "full_line_class": "判定不能", "order_class": "判定不能"}
    a, b, c = (mapping[k] for k in cars)
    same12 = a[0] == b[0]
    if same12:
        line_class = "筋"
        order_class = "順筋" if a[1] < b[1] else "逆筋"
    else:
        line_class = "筋違い"
        order_class = "異ライン"
    if a[0] == b[0] == c[0]:
        full = "AAA（3着まで同ライン）"
    elif a[0] == b[0]:
        full = "AAB（1・2着同ライン）"
    elif a[0] == c[0]:
        full = "ABA（1・3着同ライン）"
    elif b[0] == c[0]:
        full = "ABB（2・3着同ライン）"
    else:
        full = "ABC（3人別ライン）"
    return {"line_class": line_class, "full_line_class": full, "order_class": order_class}


def verify_day(rid, racedate, race_no):
    """Race ID layout: venue(2) + initial date(8) + meeting day(2) + race(4)."""
    if not re.fullmatch(r"\d{16}", str(rid)):
        return False
    try:
        day = int(rid[10:12])
        day_date = date(int(rid[2:6]), int(rid[6:8]), int(rid[8:10]))
        race_date = date.fromisoformat(racedate)
        return (race_date - day_date).days == day - 1 and int(rid[12:16]) == int(race_no)
    except (ValueError, TypeError):
        return False


def extract_year(y):
    archive = DATABASES / f"g3_{y}.sqlite.gz"
    if not archive.exists():
        raise FileNotFoundError(f"Required database not found: {archive}")
    WORK.mkdir(parents=True, exist_ok=True)
    scratch = WORK / f".analysis_day2_{y}.sqlite"
    scratch.unlink(missing_ok=True)
    try:
        with gzip.open(archive, "rb") as inp, scratch.open("wb") as out:
            shutil.copyfileobj(inp, out, 1024 * 1024)
        db = sqlite3.connect(f"file:{scratch}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            rows = db.execute("""
                SELECT r.race_id, r.race_date, r.track, r.race_no,
                       r.race_type, r.entry_count, r.predicted_line_formation,
                       r.line_status, p.combination, p.payout_yen, p.status,
                       p.archive_period
                FROM races AS r
                LEFT JOIN payouts AS p
                  ON p.race_id = r.race_id
                 AND p.ticket_type = '3連単'
                 AND p.status = 'paid'
                WHERE substr(r.race_id, 11, 2) = '02'
                  AND CAST(r.race_no AS INTEGER) BETWEEN 1 AND 6
                  AND EXISTS (SELECT 1 FROM entries e WHERE e.race_id=r.race_id)
                  AND NOT EXISTS (
                    SELECT 1 FROM entries e
                    WHERE e.race_id=r.race_id
                      AND (e."class" IS NULL OR e."class" NOT IN ('S1','S2'))
                  )
                ORDER BY r.race_date, r.track, CAST(r.race_no AS INTEGER), p.combination
            """).fetchall()
            exclusions = [dict(x) for x in db.execute("""
                SELECT r.race_id, r.race_date, r.track, r.race_no, r.race_type
                FROM races r
                WHERE substr(r.race_id,11,2)='02'
                  AND CAST(r.race_no AS INTEGER) BETWEEN 1 AND 6
                  AND (
                    NOT EXISTS (SELECT 1 FROM entries e WHERE e.race_id=r.race_id)
                    OR EXISTS (SELECT 1 FROM entries e
                               WHERE e.race_id=r.race_id
                               AND (e."class" IS NULL OR e."class" NOT IN ('S1','S2')))
                  )
                ORDER BY r.race_date, r.track, CAST(r.race_no AS INTEGER)
            """)]
            results = {}
            for r in db.execute("""
                SELECT e.race_id, e.car_no, e.line_id, e.line_position
                FROM entries e JOIN races r ON r.race_id=e.race_id
                WHERE substr(r.race_id,11,2) = '02'
                  AND CAST(r.race_no AS INTEGER) BETWEEN 1 AND 6
            """):
                results[(r["race_id"], norm_car(r["car_no"]))] = (
                    str(r["line_id"]) if r["line_id"] is not None else None,
                    r["line_position"]
                )
            output = []
            for r in rows:
                line = classify(r["combination"] or "", r["predicted_line_formation"])
                valid = verify_day(r["race_id"], r["race_date"], r["race_no"])
                mapping = line_map(r["predicted_line_formation"])
                line_consistent = True
                if mapping:
                    for car, (li, pos) in mapping.items():
                        entry = results.get((r["race_id"], car))
                        if not entry:
                            line_consistent = False
                            break
                        try:
                            # Provider line_id can be zero- or one-based, compare relative grouping later.
                            if entry[1] is not None and str(entry[1]).strip() != "" and int(entry[1]) not in (pos, pos+1):
                                line_consistent = False
                        except ValueError:
                            pass
                try:
                    payout = int(r["payout_yen"]) if r["payout_yen"] is not None else None
                except ValueError:
                    payout = None
                output.append({
                    "year": y, "race_id": r["race_id"],
                    "race_date": r["race_date"], "track": r["track"],
                    "race_no": int(r["race_no"]), "race_type": r["race_type"],
                    "entry_count": r["entry_count"],
                    "line_formation": r["predicted_line_formation"],
                    "line_status": r["line_status"],
                    "combination": r["combination"] or "",
                    "payout_yen": payout, "archive_period": r["archive_period"] or "",
                    "race_id_date_verified": valid,
                    "entry_line_position_consistent": line_consistent,
                    **line,
                })
            return output, exclusions
        finally:
            db.close()
    finally:
        scratch.unlink(missing_ok=True)


def numeric(rows):
    return [r["payout_yen"] for r in rows if isinstance(r["payout_yen"], int) and r["payout_yen"] > 0]


def group_stats(rows, key):
    groups = defaultdict(list)
    for r in rows:
        if r["payout_yen"] and r["payout_yen"] > 0:
            groups[str(r[key])].append(r["payout_yen"])
    return {k: summary(v) for k, v in sorted(groups.items())}


def cross_stats(rows, a, b):
    """Nested descriptive statistics retaining every paid combination."""
    groups = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["payout_yen"] and r["payout_yen"] > 0:
            groups[str(r[a])][str(r[b])].append(r["payout_yen"])
    return {k: {label: summary(values) for label, values in sorted(group.items())}
            for k, group in sorted(groups.items())}


def bar_distribution(rows):
    values = numeric(rows)
    return [{"band": label, "count": sum(a <= v < b for v in values),
             "percent": 100 * sum(a <= v < b for v in values) / len(values) if values else 0}
            for a, b, label in BANDS]


def money(v):
    if v is None:
        return "—"
    return f"{v:,.0f}円"


def percentage(a, b):
    return f"{a/b*100:.1f}%" if b else "—"


def md_table(headers, lines):
    result = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    result.extend("| " + " | ".join(map(str, row)) + " |" for row in lines)
    return "\n".join(result)


def make_report(rows, exclusions):
    observed = [r for r in rows if r["payout_yen"] and r["payout_yen"] > 0]
    race_ids = {r["race_id"] for r in rows}
    paid_races = {r["race_id"] for r in observed}
    missing = [r for r in rows if r["race_id"] not in paid_races]
    mean = summary(numeric(rows))
    grade_quality = {
        "excluded_non_s_class_races": len(exclusions),
        "excluded_by_race_type": dict(sorted(Counter(r["race_type"] for r in exclusions).items())),
        "selected_races": len(race_ids), "winning_payout_rows": len(observed),
        "races_with_winning_trifecta": len(paid_races),
        "races_without_winning_trifecta": len(race_ids - paid_races),
        "multiple_winning_payout_races": len({rid for rid,c in Counter(r["race_id"] for r in observed).items() if c > 1}),
        "race_id_date_mismatches": sum(not r["race_id_date_verified"] for r in rows),
        "race_line_positions_unmatched": len({r["race_id"] for r in rows if not r["entry_line_position_consistent"]}),
        "unclassified_winning_payout_rows": sum(r["line_class"] == "判定不能" for r in observed),
    }
    exact = sorted(Counter(numeric(rows)).items(), key=lambda t: (-t[1], t[0]))[:15]
    dataset = {
        "scope": "G3 men's S1/S2 class only, 2022 through June 2026, meeting day 2 races 1-6; exclude all Girls/L/A-class",
        "excluded_races": exclusions,
        "payout_basis": "Winning trifecta payout in JPY per 100-yen bet; each paid combination is one sample.",
        "line_basis": "KDreams published predicted_line_formation; line for 1st and 2nd = 筋, different = 筋違い.",
        "quality": grade_quality,
        "overall": mean,
        "by_race_no": group_stats(rows, "race_no"),
        "by_year": group_stats(rows, "year"),
        "by_race_type": group_stats(rows, "race_type"),
        "by_line": group_stats(rows, "line_class"),
        "by_full_line": group_stats(rows, "full_line_class"),
        "by_order": group_stats(rows, "order_class"),
        "by_race_line": cross_stats(rows, "race_no", "line_class"),
        "by_race_full_line": cross_stats(rows, "race_no", "full_line_class"),
        "by_year_line": cross_stats(rows, "year", "line_class"),
        "bands": bar_distribution(rows),
        "most_frequent_exact_payouts": [{"payout_yen": v, "count": n} for v,n in exact],
        "top_payouts": sorted(observed, key=lambda r: (-r["payout_yen"], r["race_date"]))[:20],
        "missing_payout_races": missing[:100],
    }
    lines = [
        "# G3 開催2日目・1〜6R：3連単払戻金の分析",
        "",
        "## 対象・算出方法",
        "",
        "- 対象：2022〜2025年のG3全四半期、および2026年1〜6月。開催2日目（race_idの開催日次=02）の1〜6R。**出走選手全員がS1/S2の男子S級レースのみ**。",
        "- G3開催に混在するガールズ・A級競走は除外し、開催格だけで対象を決めない。『選抜』等の表記でも選手級班でS級なら含める。",
        "- 単位：**3連単の的中組番の払戻金・100円あたり**。同着などによる複数の的中組番は各1件として集計し、レース数とは別に表示。",
        "- 筋：1・2着が同じ公表予想ライン。筋違い：1・2着が別ライン。順筋／逆筋は公表ライン内の先頭→後位／後位→先頭の順番。",
        "- AAA/AAB/ABA/ABB/ABC：1〜3着のライン同一性を表す（AABは1・2着同ラインで3着別ラインなど）。",
        "- **事後の払戻と確定オッズは学習・検証で利用区分を分ける**。本分析は結果観察であり事前予測ではない。",
        "",
        "## 件数・品質",
        "",
        md_table(["項目", "件数"], [[k, f"{v:,}"] for k,v in grade_quality.items()]),
        "",
        "## 全体の配当分布",
        "",
        md_table(["統計量", "値"], [
            ["平均", money(mean.get("mean"))], ["中央値", money(mean.get("median"))],
            ["5%両端除外平均", money(mean.get("trimmed_mean_5pct"))],
            ["標準偏差", money(mean.get("stddev"))],
            ["最小", money(mean.get("min"))], ["最大", money(mean.get("max"))],
            ["10%点", money(mean.get("p10"))], ["25%点", money(mean.get("p25"))],
            ["75%点", money(mean.get("p75"))], ["90%点", money(mean.get("p90"))],
            ["95%点", money(mean.get("p95"))], ["99%点", money(mean.get("p99"))],
            ["1万円以上", f"{mean.get('ge_10000',0):,}件 / {percentage(mean.get('ge_10000',0),mean.get('n',0))}"],
            ["5万円以上", f"{mean.get('ge_50000',0):,}件 / {percentage(mean.get('ge_50000',0),mean.get('n',0))}"],
            ["10万円以上", f"{mean.get('ge_100000',0):,}件 / {percentage(mean.get('ge_100000',0),mean.get('n',0))}"],
        ]),
        "",
        "## 1〜6R別",
        "",
        md_table(["R", "的中配当件数", "平均", "中央値", "25%点", "75%点", "90%点", "1万円以上", "5万円以上"],
                 [[k, f"{s['n']:,}", money(s['mean']), money(s['median']),
                   money(s['p25']), money(s['p75']), money(s['p90']),
                   percentage(s['ge_10000'], s['n']), percentage(s['ge_50000'], s['n'])]
                  for k,s in dataset["by_race_no"].items()]),
        "",
        "## 年別",
        "",
        md_table(["年", "的中配当件数", "平均", "中央値", "90%点", "10万円以上"],
                 [[k, f"{s['n']:,}", money(s["mean"]), money(s["median"]), money(s["p90"]),
                   percentage(s["ge_100000"], s["n"])] for k,s in dataset["by_year"].items()]),
        "",
        "## 価格帯の頻度（最頻帯）",
        "",
        md_table(["配当帯", "件数", "割合"],
                 [[x["band"], f"{x['count']:,}", f"{x['percent']:.1f}%"] for x in dataset["bands"]]),
        "",
        "## 配当の最頻値（100円単位の完全一致値）",
        "",
        f"最頻の同額出現数：{mean.get('mode_frequency',0)}件。最頻値の同率候補数：{mean.get('mode_tie_count',0)}個。",
        "",
        md_table(["配当金額", "出現件数", "全体比"],
                 [[money(x["payout_yen"]), x["count"], percentage(x["count"], mean["n"])]
                  for x in dataset["most_frequent_exact_payouts"]]),
        "",
        "## 筋と筋違い（1着と2着のライン関係）",
        "",
        md_table(["分類", "配当件数", "構成比", "平均", "中央値", "90%点", "5万円以上"],
                 [[k, f"{s['n']:,}", percentage(s["n"],mean["n"]), money(s["mean"]),
                   money(s["median"]), money(s["p90"]), percentage(s["ge_50000"],s["n"])]
                  for k,s in dataset["by_line"].items()]),
        "",
        "## 1〜6R別の筋・筋違い",
        "",
        md_table(["R", "筋件数", "筋%", "筋中央値", "筋違い件数", "筋違い%", "筋違い中央値"],
                 [[race, f"{v.get('筋', {}).get('n', 0):,}",
                   percentage(v.get("筋", {}).get("n", 0), sum(t["n"] for t in v.values())),
                   money(v.get("筋", {}).get("median")),
                   f"{v.get('筋違い', {}).get('n', 0):,}",
                   percentage(v.get("筋違い", {}).get("n", 0), sum(t["n"] for t in v.values())),
                   money(v.get("筋違い", {}).get("median"))]
                  for race,v in dataset["by_race_line"].items()]),
        "",
        "## 年別の筋・筋違い",
        "",
        md_table(["年", "筋", "筋違い", "筋%", "筋中央値", "筋違い中央値"],
                 [[year, f"{v.get('筋', {}).get('n', 0):,}",
                   f"{v.get('筋違い', {}).get('n', 0):,}",
                   percentage(v.get("筋", {}).get("n", 0), sum(t["n"] for t in v.values())),
                   money(v.get("筋", {}).get("median")),
                   money(v.get("筋違い", {}).get("median"))]
                  for year,v in dataset["by_year_line"].items()]),
        "",
        "## 筋の順序（ライン内の前→後ろ／後ろ→前）",
        "",
        md_table(["関係", "配当件数", "構成比", "平均", "中央値"],
                 [[k, f"{s['n']:,}",percentage(s["n"],mean["n"]),money(s["mean"]),money(s["median"])]
                  for k,s in dataset["by_order"].items()]),
        "",
        "## 1〜3着のライン構造",
        "",
        md_table(["構造", "配当件数", "構成比", "平均", "中央値", "90%点"],
                 [[k, f"{s['n']:,}",percentage(s["n"],mean["n"]),money(s["mean"]),
                   money(s["median"]),money(s["p90"])] for k,s in dataset["by_full_line"].items()]),
        "",
        "## レース種別",
        "",
        md_table(["レース種別", "配当件数", "平均", "中央値"],
                 [[k, f"{s['n']:,}", money(s["mean"]), money(s["median"])]
                  for k,s in sorted(dataset["by_race_type"].items(), key=lambda t:-t[1]["n"])]),
        "",
        "## 高配当上位20件",
        "",
        md_table(["日付", "競輪場", "R", "決着", "払戻", "筋/違い", "構造"],
                 [[r["race_date"],r["track"],r["race_no"],r["combination"],
                   money(r["payout_yen"]),r["line_class"],r["full_line_class"]]
                  for r in dataset["top_payouts"]]),
        "",
        "## 検証上の注意",
        "",
        "- 配当分布は的中した組番の分布であり、任意の買い目の期待収益や回収率ではない。",
        "- 出走時点のライン予想は楽天Kドリームス掲載の並び予想。並びの事後改変有無は別途監査対象。",
        "- 発生率と配当は同一ではない。市場誤差の判断には『その組番の締切前の価格』と独自確率の照合が必要。",
        "- 結果・払戻は必ず学習期間と未知検証期間を分けて扱う。",
        "- 元データの重複・欠損・開催網羅性は各年の取り込み監査も併読すること。",
        "",
    ]
    return dataset, "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", type=int, choices=YEARS, help="Optional single-year run")
    args = ap.parse_args()
    years = (args.year,) if args.year else YEARS
    records, exclusions = [], []
    for y in years:
        chunk, excluded = extract_year(y)
        print(f"EXTRACTED {y}: rows={len(chunk)} unique_races={len(set(r['race_id'] for r in chunk))}, excluded_non_S={len(excluded)}", flush=True)
        records.extend(chunk)
        exclusions.extend(excluded)
    dataset, report = make_report(records, exclusions)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    name = str(args.year) if args.year else "all_2022_2026h1"
    (OUTPUT / f"{name}_statistics.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / f"{name}_report.md").write_text(report, encoding="utf-8")
    fieldnames = [
        "year", "race_id", "race_date", "track", "race_no", "race_type", "entry_count",
        "line_formation", "line_status", "combination", "payout_yen", "archive_period",
        "race_id_date_verified", "entry_line_position_consistent",
        "line_class", "order_class", "full_line_class",
    ]
    with (OUTPUT / f"{name}_race_payout_rows.csv").open("w", newline="", encoding="utf-8-sig") as f:
        wr = csv.DictWriter(f, fieldnames=fieldnames)
        wr.writeheader()
        wr.writerows(records)
    print(json.dumps({"output":name, "quality":dataset["quality"],
                      "overall":dataset["overall"],"by_line":dataset["by_line"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
