#!/usr/bin/env python3
"""Examine 2025+ G3 meeting-day-2 starts for the nine 2026-11-01 Hofu 5R riders.

Only the archived 2025 full year and 2026 Jan-June datasets are queried.
No results from the target 2026-11-01 meeting enter this report.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import csv
import gzip
import json
from pathlib import Path
import shutil
import sqlite3
import statistics
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
READY = ROOT / "data" / "ready"
CACHE = ROOT / "data" / "local"
DEST = ROOT / "reports" / "hofu_20261101_r5"
AS_OF = "2026-10-31"
RUNNERS = [
    {"car": 1, "name": "鰐淵圭佑", "prefecture": "群馬", "term": 90},
    {"car": 2, "name": "伊勢崎彰大", "prefecture": "千葉", "term": 81},
    {"car": 3, "name": "佐伯亮輔", "prefecture": "岡山", "term": 113},
    {"car": 4, "name": "橋本壮史", "prefecture": "茨城", "term": 119},
    {"car": 5, "name": "田中晴基", "prefecture": "千葉", "term": 90},
    {"car": 6, "name": "谷津田将吾", "prefecture": "福島", "term": 83},
    {"car": 7, "name": "西岡拓朗", "prefecture": "広島", "term": 97},
    {"car": 8, "name": "野村典嗣", "prefecture": "愛媛", "term": 109},
    {"car": 9, "name": "渡邉雅也", "prefecture": "静岡", "term": 117},
]


def norm(value):
    return "".join(unicodedata.normalize("NFKC", str(value or "")).split())


def day2(race_id, race_date):
    if len(str(race_id)) != 16 or not str(race_id).isdigit():
        return False
    rid = str(race_id)
    try:
        first = date(int(rid[2:6]), int(rid[6:8]), int(rid[8:10]))
        raced = date.fromisoformat(race_date)
        day_code = int(rid[10:12])
    except ValueError:
        return False
    return day_code == 2 and (raced - first).days == 1


def extract(year):
    path = READY / f"g3_{year}.sqlite.gz"
    if not path.is_file():
        raise RuntimeError(f"Missing source SQLite: {path}")
    CACHE.mkdir(parents=True, exist_ok=True)
    scratch = CACHE / f".hofu_day2_{year}.sqlite"
    scratch.unlink(missing_ok=True)
    try:
        with gzip.open(path, "rb") as inp, scratch.open("wb") as out:
            shutil.copyfileobj(inp, out, 1024 * 1024)
        with sqlite3.connect(f"file:{scratch}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("""
                SELECT r.race_id,r.race_date,r.track,r.race_no,r.race_type,
                       e.car_no,e.player_name,e.prefecture,e.term,e."class" AS class_at_race,
                       e.style,e.score,e.line_id,e.line_position,e.line_size,
                       e.line_role,e.predicted_line_formation,
                       x.finish_position,x.finish_text,x.winning_method,x.sb,
                       x.last200,x.result_comment
                FROM races r
                JOIN entries e ON e.race_id=r.race_id
                LEFT JOIN results x ON x.race_id=e.race_id AND x.car_no=e.car_no
                WHERE substr(r.race_id,11,2)='02'
                  AND r.race_date BETWEEN '2025-01-01' AND ?
                  AND NOT EXISTS (
                    SELECT 1 FROM entries other
                    WHERE other.race_id=r.race_id
                      AND (other."class" IS NULL OR other."class" NOT IN ('SS','S1','S2'))
                  )
                ORDER BY r.race_date, r.race_no
            """, (AS_OF,)).fetchall()
            return [dict(r) for r in rows]
    finally:
        scratch.unlink(missing_ok=True)


def finish_num(r):
    try:
        p = int(r["finish_position"])
        return p if p >= 1 else None
    except (ValueError, TypeError):
        return None


def stats(rows):
    count = len(rows)
    with_results = [r for r in rows if r["finish_position"] is not None or r["finish_text"] is not None]
    numeric = [v for r in rows if (v := finish_num(r)) is not None]
    v1 = sum(x == 1 for x in numeric)
    v2 = sum(x == 2 for x in numeric)
    v3 = sum(x == 3 for x in numeric)
    winning = Counter(norm(r["winning_method"]) for r in rows if finish_num(r) == 1)
    return {
        "starts": count,
        "recorded_results": len(with_results),
        "numeric_finishes": len(numeric),
        "missing_results": count-len(with_results),
        "first": v1,
        "second": v2,
        "third": v3,
        "fourth_or_worse_and_nonfinish": count-v1-v2-v3,
        "first_rate": round(v1/count*100, 1) if count else None,
        "top2_rate": round((v1+v2)/count*100, 1) if count else None,
        "top3_rate": round((v1+v2+v3)/count*100, 1) if count else None,
        "mean_numeric_finish": round(statistics.fmean(numeric), 2) if numeric else None,
        "win_methods": dict(winning),
    }


def history_view(r):
    return {
        "race_date": r["race_date"],
        "track": r["track"],
        "race_no": int(r["race_no"]),
        "race_type": r["race_type"],
        "race_id": r["race_id"],
        "car_no": r["car_no"],
        "finish_position": r["finish_position"],
        "finish_text": r["finish_text"],
        "winning_method": r["winning_method"],
        "sb": r["sb"],
        "line_id": r["line_id"],
        "line_position": r["line_position"],
        "predicted_line_formation": r["predicted_line_formation"],
        "class_at_race": r["class_at_race"],
        "score_at_race": r["score"],
        "result_comment": r["result_comment"],
    }


def fmt(s):
    return f"{s['first']}-{s['second']}-{s['third']}-{s['fourth_or_worse_and_nonfinish']}" if s["starts"] else "0走"


def mdtable(head, rows):
    return "\n".join(["| "+" | ".join(head)+" |", "| "+" | ".join(["---"]*len(head))+" |"] +
                      ["| "+" | ".join(str(v) for v in row)+" |" for row in rows])


def main():
    raw = extract(2025)+extract(2026)
    selected = {x["car"]: [] for x in RUNNERS}
    ambiguity, bad_daycode = [], []
    for r in raw:
        if not day2(r["race_id"], r["race_date"]):
            bad_daycode.append(r["race_id"])
            continue
        for runner in RUNNERS:
            if norm(r["player_name"]).replace("澤","沢") != norm(runner["name"]).replace("澤","沢"):
                continue
            if norm(r["prefecture"]) != norm(runner["prefecture"]) or norm(r["term"]) != str(runner["term"]):
                ambiguity.append({"runner":runner,"found":{"name":r["player_name"],"prefecture":r["prefecture"],"term":r["term"]},"race_id":r["race_id"]})
                continue
            selected[runner["car"]].append(r)
    if ambiguity:
        raise RuntimeError(f"Unmatched identities; refusing to count without review: {ambiguity}")
    if bad_daycode:
        raise RuntimeError(f"Malformed day2 race identifier/date pairs: {bad_daycode[:12]}")

    all_records = []
    rows = []
    for runner in RUNNERS:
        rides = sorted(selected[runner["car"]], key=lambda x:(x["race_date"],int(x["race_no"])), reverse=True)
        duplicate_ids = [key for key,n in Counter(x["race_id"] for x in rides).items() if n > 1]
        if duplicate_ids:
            raise RuntimeError(f"Duplicate rider-race records for {runner['name']}: {duplicate_ids}")
        early = [x for x in rides if int(x["race_no"]) <= 6]
        exact_r5 = [x for x in rides if int(x["race_no"]) == 5]
        selection = [x for x in rides if "選抜" in norm(x["race_type"])]
        exact_r5_selection = [x for x in exact_r5 if "選抜" in norm(x["race_type"])]
        comparable = [x for x in early if "選抜" in norm(x["race_type"])]
        y2025 = [x for x in rides if x["race_date"].startswith("2025")]
        y2026 = [x for x in rides if x["race_date"].startswith("2026")]
        item = {
            **runner,
            "day2_all": stats(rides),
            "day2_r1_6": stats(early),
            "day2_r5": stats(exact_r5),
            "day2_r5_selection": stats(exact_r5_selection),
            "day2_selection": stats(selection),
            "day2_selection_r1_6": stats(comparable),
            "year_2025": stats(y2025),
            "year_2026_h1": stats(y2026),
            "race_types": dict(Counter(norm(x["race_type"]) for x in rides)),
            "historical_line_roles": dict(Counter(
                ("単騎" if str(x["line_size"]) == "1" else
                 "先頭" if str(x["line_position"]) in ("0","1") and
                           any(str(x["car_no"]) == group.split("-")[0] for group in str(x["predicted_line_formation"]).split("/")) else
                 "番手以降") for x in rides
            )),
            "recent_6": [history_view(x) for x in rides[:6]],
            "selection_recent_5": [history_view(x) for x in selection[:5]],
            "all_historical_day2": [history_view(x) for x in rides],
        }
        rows.append(item)
        for x in rides:
            all_records.append({"today_car":runner["car"],"today_player":runner["name"],**history_view(x)})
    result = {
        "meeting":"2026-11-01 Hofu G3 day 2 5R S-class selection",
        "today_race":"2026-11-01",
        "historical_period":"2025-01-01 to 2026-06-30 inclusive (2026 H2 not in original data)",
        "restriction":"G3 meeting day 2 only; historical 2025+ S-class only. No outcome from 2026-11-01.",
        "today_line_prediction":"9-5-2 / 4-1-6 / 3-7-8",
        "day2_definition":"race_id meeting day code 02, and race_date equals first date + 1 day",
        "identity":"NFKC whitespace-insensitive full name (澤 and 沢 normalized) plus prefecture plus graduating term",
        "sample_note":"Rates count every recorded start, including non-finish/disqualification in denominator. Missing results kept explicit.",
        "source":["data/ready/g3_2025.sqlite.gz","data/ready/g3_2026.sqlite.gz"],
        "target_runners":rows,
        "quality":{"total_rows_2025plus":len(all_records),
                   "riders_with_history":sum(1 for i in rows if i["day2_all"]["starts"]>0),
                   "unmatched_identity":[],"invalid_meeting_day_rows":[]}
    }

    DEST.mkdir(parents=True,exist_ok=True)
    (DEST/"riders_day2_2025_2026h1.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    fields=["today_car","today_player","race_date","track","race_no","race_type","race_id","car_no",
            "finish_position","finish_text","winning_method","sb","line_id","line_position",
            "predicted_line_formation","class_at_race","score_at_race","result_comment"]
    with (DEST/"historical_day2_starts.csv").open("w",encoding="utf-8-sig",newline="") as f:
        wr=csv.DictWriter(f,fieldnames=fields)
        wr.writeheader()
        wr.writerows(all_records)

    report=[
        "# 防府G3 2026-11-01 5R 出走9選手のG3開催2日目実績",
        "",
        "対象：**2025年1月〜2026年6月**の保存済みG3アーカイブのみ。2024年以前、2026年7月〜10月、当日11月1日の結果は対象外。",
        "",
        "同名誤結合防止のため選手名・府県・期別を一致させ、開催2日目をレースIDと日付で検証。",
        "成績表記＝1着-2着-3着-4着以下等（棄権／失格等は4着以下等に含める）。率の分母は出走記録数。",
        "",
        "## G3開催2日目 全レース実績（2025〜2026H1）",
        "",
        mdtable(["今日の車番","選手","出走数","1-2-3-その他","勝率","2連対率","3連対率","平均着順"],
                [[r["car"],r["name"],r["day2_all"]["starts"],fmt(r["day2_all"]),
                  str(r["day2_all"]["first_rate"])+"%" if r["day2_all"]["starts"] else "—",
                  str(r["day2_all"]["top2_rate"])+"%" if r["day2_all"]["starts"] else "—",
                  str(r["day2_all"]["top3_rate"])+"%" if r["day2_all"]["starts"] else "—",
                  r["day2_all"]["mean_numeric_finish"] if r["day2_all"]["mean_numeric_finish"] is not None else "—"]
                 for r in rows]),
        "",
        "## G3開催2日目の1〜6Rに限定",
        "",
        mdtable(["車番","選手","出走数","1-2-3-その他","3連対率"],
                [[r["car"],r["name"],r["day2_r1_6"]["starts"],fmt(r["day2_r1_6"]),
                  str(r["day2_r1_6"]["top3_rate"])+"%" if r["day2_r1_6"]["starts"] else "—"]
                 for r in rows]),
        "",
        "## 開催2日目の5R（全種別）",
        "",
        mdtable(["車番","選手","出走数","1-2-3-その他","3連対率"],
                [[r["car"],r["name"],r["day2_r5"]["starts"],fmt(r["day2_r5"]),
                  str(r["day2_r5"]["top3_rate"])+"%" if r["day2_r5"]["starts"] else "—"]
                 for r in rows]),
        "",
        "## 開催2日目の5R・S級選抜（今回と完全に同じレース種別・番号）",
        "",
        mdtable(["車番","選手","出走数","1-2-3-その他","3連対率"],
                [[r["car"],r["name"],r["day2_r5_selection"]["starts"],fmt(r["day2_r5_selection"]),
                  str(r["day2_r5_selection"]["top3_rate"])+"%" if r["day2_r5_selection"]["starts"] else "—"]
                 for r in rows]),
        "",
        "## 今日と同じ『選抜』の開催2日目（R番号問わず）",
        "",
        mdtable(["車番","選手","出走数","1-2-3-その他","3連対率"],
                [[r["car"],r["name"],r["day2_selection"]["starts"],fmt(r["day2_selection"]),
                  str(r["day2_selection"]["top3_rate"])+"%" if r["day2_selection"]["starts"] else "—"]
                 for r in rows]),
        "",
        "## 『選抜』かつ1〜6R：今日との類似条件",
        "",
        mdtable(["車番","選手","出走数","1-2-3-その他","3連対率"],
                [[r["car"],r["name"],r["day2_selection_r1_6"]["starts"],
                  fmt(r["day2_selection_r1_6"]),
                  str(r["day2_selection_r1_6"]["top3_rate"])+"%" if r["day2_selection_r1_6"]["starts"] else "—"]
                 for r in rows]),
        "",
        "## 本日の補充情報",
        "",
        "- ⑦西岡拓朗は補充出走で、初日の結果は存在しない。過去の通常出走と今回の条件を混同しない。",
        "- 本日の出走表 https://keirin.tokyo-sports.co.jp/list/race/20261031_35912",
        "",
        "## 本日の公表ライン",
        "",
        "- 南関：⑨渡邉 → ⑤田中 → ②伊勢崎",
        "- 関東・北日本：④橋本 → ①鰐淵 → ⑥谷津田",
        "- 中国・四国：③佐伯 → ⑦西岡 → ⑧野村",
        "- 4Rは3車ライン×3本の三分戦。④は補充で初日の着順なし。資料：https://keirin.tokyo-sports.co.jp/list/race/20261031_35912",
        "",
        "## 選手別の直近6件（G3開催2日目）",
        "",
    ]
    for r in rows:
        report += [f"### {r['car']}番 {r['name']}",
                   f"- 2025年：{fmt(r['year_2025'])}、2026年上半期：{fmt(r['year_2026_h1'])}",
                   f"- 1着決まり手：{json.dumps(r['day2_all']['win_methods'],ensure_ascii=False)}",
                   "",
                   mdtable(["開催日","場","R/種別","車番","着順","決まり手","当時の並び予想"],
                           [[x["race_date"], x["track"], f"{x['race_no']}R {x['race_type']}",
                             x["car_no"], x["finish_text"] or x["finish_position"] or "不明",
                             x["winning_method"] or "—", x["predicted_line_formation"] or "—"]
                            for x in r["recent_6"]]) if r["recent_6"] else "該当履歴なし",
                   ""]
    report += [
        "## 注意事項","",
        "- 本報告は2025年以降のG3開催2日目の**過去記録**のみ。今日の勝敗予測、オッズ評価とは別。",
        "- 過去資料は2026年6月まで。2026年7〜10月のG3実績を収録していないため**直近4ヶ月までの完全履歴ではない**。",
        "- 『選抜』等のレース条件別では標本が少なくなり、率が不安定な場合がある。",
        "- G3開催2日目のみを対象にした『G3選抜』の成績。通常開催F1/F2の2日目は混ぜていない。",
        "- ラインはKドリームスの公表並び予想で、事実として確定した隊列ではない。",
        "- 元の出走表・結果はユーザー所有GitHubの既存アーカイブ。別レースの『予想』情報は集計しない。",
        ""
    ]
    (DEST/"riders_day2_2025_2026h1_report.md").write_text("\n".join(report),encoding="utf-8")
    print(json.dumps({"quality":result["quality"],
                      "riders":[{"car":r["car"],"name":r["name"],
                                 "all":r["day2_all"],"early":r["day2_r1_6"],
                                 "selection":r["day2_selection"]} for r in rows]},ensure_ascii=False))


if __name__=="__main__":
    main()
