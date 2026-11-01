#!/usr/bin/env python3
"""Hofu 333m bank diagnostic: 2025+ S-class nine-starter G3 race results.

Finish-line line patterns are NOT track positions during the race.
Original ZIP archives and the SQLite archive are never modified.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import date
import csv
import gzip
import json
import math
from pathlib import Path
import shutil
import sqlite3
import statistics

ROOT = Path(__file__).resolve().parents[1]
READY = ROOT / "data" / "ready"
TEMP = ROOT / "data" / "local"
OUT = ROOT / "reports" / "hofu_bank_2025plus"
OTHERS_33 = ("松戸", "小田原", "伊東", "富山", "奈良", "前橋")


def classify(combo, line):
    groups = [v.split("-") for v in (line or "").split("/") if v]
    mapping = {}
    for index, group in enumerate(groups):
        for position, car in enumerate(group):
            car = car.strip()
            if not car.isdigit() or car in mapping:
                return None
            mapping[car] = (index, position, len(group))
    cars = [v.strip() for v in combo.split("-")]
    if len(cars) != 3 or len(set(cars)) != 3 or any(c not in mapping for c in cars):
        return None
    a,b,c = (mapping[x] for x in cars)
    if a[0]==b[0]==c[0]:
        pattern = "AAA"
    elif a[0]==b[0]:
        pattern = "AAB"
    elif a[0]==c[0]:
        pattern = "ABA"
    elif b[0]==c[0]:
        pattern = "ABB"
    else:
        pattern = "ABC"
    role = "単騎" if a[2] == 1 else "ライン先頭" if a[1] == 0 else "番手" if a[1] == 1 else "3番手以降"
    return {
        "pattern":pattern,
        "same_first_second":a[0] == b[0],
        "winner_role":role,
        "winner_line_size":a[2],
        "first_second_reverse": a[0]==b[0] and a[1] > b[1],
        "winner_only_podium":a[2] >= 2 and a[0]!=b[0] and a[0]!=c[0],
        "leaders_in_first_three":sum(mapping[x][1] == 0 for x in cars),
        "solo_riders_in_first_three":sum(mapping[x][2] == 1 for x in cars),
    }


def unpack(y):
    src = READY / f"g3_{y}.sqlite.gz"
    if not src.is_file():
        raise FileNotFoundError(src)
    TEMP.mkdir(parents=True, exist_ok=True)
    local = TEMP / f".hofu_bank_{y}.sqlite"
    local.unlink(missing_ok=True)
    with gzip.open(src, "rb") as input, local.open("wb") as output:
        shutil.copyfileobj(input, output, 1024*1024)
    return local


def collect(year):
    local = unpack(year)
    try:
        with sqlite3.connect(f"file:{local}?mode=ro",uri=True) as db:
            db.row_factory = sqlite3.Row
            all_races = {}
            for row in db.execute("""
                SELECT r.race_id,r.race_date,r.track,r.race_no,r.race_type,
                       r.predicted_line_formation,
                       e.car_no,e.player_name,e.score,e."class" AS class_at_race,
                       x.finish_position,x.winning_method
                FROM races r
                JOIN entries e ON r.race_id=e.race_id
                LEFT JOIN results x ON x.race_id=e.race_id AND x.car_no=e.car_no
                WHERE r.race_date BETWEEN '2025-01-01' AND '2026-06-30'
                ORDER BY r.race_id
            """):
                d=dict(row)
                rid=d["race_id"]
                if rid not in all_races:
                    all_races[rid]={"race_id":rid,"year":year,"date":d["race_date"],
                                    "track":d["track"],"no":int(d["race_no"]),
                                    "type":d["race_type"],"line":d["predicted_line_formation"],
                                    "entries":[],"paid":[]}
                all_races[rid]["entries"].append(d)
            for row in db.execute("""
                SELECT p.race_id,p.combination,p.payout_yen
                FROM payouts p JOIN races r ON r.race_id=p.race_id
                WHERE p.ticket_type='3連単' AND p.status='paid'
                  AND r.race_date BETWEEN '2025-01-01' AND '2026-06-30'
            """):
                rid=row["race_id"]
                if rid in all_races:
                    try:
                        yen=int(row["payout_yen"])
                    except (ValueError,TypeError):
                        continue
                    all_races[rid]["paid"].append({"combination":row["combination"],"payout_yen":yen})
            return list(all_races.values())
    finally:
        local.unlink(missing_ok=True)


def safe_mean(values):
    return round(statistics.fmean(values),1) if values else None


def median(values):
    return round(statistics.median(values),1) if values else None


def rate(n,d):
    return round(n*100/d,1) if d else None


def group_metrics(group):
    races=len(group)
    all_win=[c for r in group for c in r["paid"]]
    valid=[v for v in all_win if v.get("pattern")]
    values=[v["payout_yen"] for v in all_win]
    patterns=Counter(c["pattern"] for c in valid)
    roles=Counter(c["winner_role"] for c in valid)
    methods=Counter(c["winning_method"] for c in valid if c.get("winning_method"))
    return {
        "races":races,"winning_combinations":len(all_win),"classified_combinations":len(valid),
        "races_missing_paid":sum(not r["paid"] for r in group),
        "mean_yen":safe_mean(values),"median_yen":median(values),
        "ge_10000":sum(v>=10000 for v in values),
        "ge_50000":sum(v>=50000 for v in values),
        "ge_100000":sum(v>=100000 for v in values),
        "ge_50000_pct":rate(sum(v>=50000 for v in values),len(values)),
        "ge_100000_pct":rate(sum(v>=100000 for v in values),len(values)),
        "max_yen":max(values) if values else None,
        "line_patterns":dict(sorted(patterns.items())),
        "line_pattern_pct":{p:rate(patterns[p],len(valid)) for p in ("AAA","AAB","ABA","ABB","ABC")},
        "first_second_split_pct":rate(sum(not c["same_first_second"] for c in valid),len(valid)),
        "abc_pct":rate(patterns["ABC"],len(valid)),
        "winner_only_podium_pct":rate(sum(c["winner_only_podium"] for c in valid),len(valid)),
        "reversed_12_pct":rate(sum(c["first_second_reverse"] for c in valid),len(valid)),
        "winner_roles":dict(sorted(roles.items())),
        "winner_role_pct":{k:rate(roles[k],len(valid)) for k in ("ライン先頭","番手","3番手以降","単騎")},
        "winning_methods":dict(sorted(methods.items())),
        "winning_methods_pct":{k:rate(methods[k],sum(methods.values())) for k in ("逃","捲","差","マ","逃げ","捲り","差し")},
    }


def mdtable(headers, rows):
    return "\n".join(["| "+" | ".join(headers)+" |", "| "+" | ".join(["---"]*len(headers))+" |"] +
                     ["| "+" | ".join("—" if x is None else str(x) for x in a)+" |" for a in rows])


def fmtmoney(v):
    return f"{v:,.0f}円" if v is not None else "—"


def main():
    raw = collect(2025)+collect(2026)
    quality=Counter()
    races=[]
    for r in raw:
        quality["all_races"]+=1
        if len(r["entries"])!=9:
            quality["excluded_non9_entry_races"]+=1
            continue
        if any(e["class_at_race"] not in ("SS","S1","S2") for e in r["entries"]):
            quality["excluded_nonsclass_races"]+=1
            continue
        if not r["line"]:
            quality["excluded_missing_line_races"]+=1
            continue
        if not r["paid"]:
            quality["excluded_missing_paid_races"]+=1
            continue
        for c in r["paid"]:
            d=classify(c["combination"],r["line"])
            if d is None:
                quality["unclassified_winning_combo"]+=1
                continue
            c.update(d)
            firstcar=c["combination"].split("-")[0]
            winner = [e for e in r["entries"] if str(e["car_no"])==firstcar]
            c["winner_name"]=winner[0]["player_name"] if winner else "不明"
            c["winning_method"]=winner[0]["winning_method"] if winner else ""
        quality["included_races"]+=1
        races.append(r)

    def is_hofu(r): return "防府" in r["track"]
    def is_other33(r): return any(x in r["track"] for x in OTHERS_33)
    def is2025(r):return r["year"]==2025
    def is_day2_early(r):return r["race_id"][10:12]=="02" and 1<=r["no"]<=6

    sets={
        "Hofu_2025plus":[r for r in races if is_hofu(r)],
        "Other33_2025":[r for r in races if is_other33(r) and is2025(r)],
        "Other33_2025plus":[r for r in races if is_other33(r)],
        "Other_nonHofu_2025":[r for r in races if not is_hofu(r) and is2025(r)],
        "Other_nonHofu_2025plus":[r for r in races if not is_hofu(r)],
        "Hofu_Day2_R1_6":[r for r in races if is_hofu(r) and is_day2_early(r)],
        "Other33_2025_Day2_R1_6":[r for r in races if is_other33(r) and is2025(r) and is_day2_early(r)],
        "Other_2025_Day2_R1_6":[r for r in races if not is_hofu(r) and is2025(r) and is_day2_early(r)],
    }
    stats={k:group_metrics(v) for k,v in sets.items()}
    hofuraces=sets["Hofu_2025plus"]
    for r in hofuraces:
        r["winning_outcomes"]=[
            {k:v for k,v in c.items() if k not in ("winner_line_size","solo_riders_in_first_three","leaders_in_first_three")}
            for c in r["paid"]]
    # Assemble distinct-day results and 1R special study
    by_day={}
    for d in (1,2,3,4):
        by_day[str(d)]=group_metrics([r for r in sets["Hofu_2025plus"] if r["race_id"][10:12]==f"{d:02d}"])
    details=[{k:v for k,v in r.items() if k not in ("line",)}
             for r in sorted(hofuraces,key=lambda r:(r["date"],r["no"]))]
    one_races=[r for r in details if r["race_id"][10:12]=="02" and r["no"]<=6]
    top=sorted(
        [{"date":r["date"],"race_no":r["no"],"race_type":r["type"],
          "combination":c["combination"],"payout_yen":c["payout_yen"],
          "pattern":c["pattern"],"winner_role":c["winner_role"],
          "winning_method":c["winning_method"],"winner":c["winner_name"],
          "predicted_line":r["line"] }
         for r in hofuraces for c in r["winning_outcomes"]],
        key=lambda x:-x["payout_yen"]
    )
    by_track={k:group_metrics(v) for k,v in sorted(
        ((t,[r for r in races if r["track"]==t and is2025(r)])
        for t in sorted({r["track"] for r in races if is2025(r)})),key=lambda x:x[0])}
    output={
        "scope":"2025-01-01 to 2026-06-30, S-class G3 with nine starters and valid predicted formation",
        "Hofu_observation":"2025 Hofu G3 only, as 2026 H1 source has no Oct-Nov Hofu G3. 2026-11-01 meeting intentionally excluded",
        "warning":"Published predicted line != observed formation or actual 4th corner position; finish-line structure only. Cannot infer lead changes, blocking or wind causality.",
        "denominator":"Line pattern/payout statistics use winning paid combinations; multiple winning combos in dead heat may each count. Race count separate.",
        "same_year_benchmark":"2025 non-Hofu G3; 2025 other 333/335m G3",
        "quality":dict(quality),
        "cohorts":stats,
        "hofu_by_meeting_day":by_day,
        "hofu_day2_r1_6":one_races,
        "hofu_top_payouts":top[:30],
        "by_track_2025":by_track,
    }
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/"hofu_bank_results_2025plus.json").write_text(
        json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    fields=["date","race_no","race_type","race_id","track","line","combination","payout_yen",
            "pattern","same_first_second","winner_role","first_second_reverse",
            "winner_only_podium","winner_name","winning_method"]
    with (OUT/"hofu_2025_all_g3_outcomes.csv").open("w",encoding="utf-8-sig",newline="") as f:
        wr=csv.DictWriter(f,fieldnames=fields)
        wr.writeheader()
        for r in hofuraces:
            for c in r["winning_outcomes"]:
                wr.writerow({
                    "date":r["date"],"race_no":r["no"],"race_type":r["type"],
                    "race_id":r["race_id"],"track":r["track"],"line":r["line"],
                    **{k:v for k,v in c.items() if k in fields},
                })

    report=[
        "# 防府333mバンク：2025年以降のG3決着ライン崩れ・比較",
        "",
        "## 分析の定義",
        "",
        "- 対象：2025年以降のG3男子S級9車レース。比較元は2025年通年と2026年上半期のアーカイブ。",
        "- **防府のG3開催は実質2025年の収録分**。現在開催中の2026年11月1日は含まない。",
        "- 競走中の先行ライン、4角の隊列、風の影響は、フィニッシュ順位＋予想ラインだけでは直接確定できない。",
        "- 『崩れ』は1〜3着の公表予想ラインの組み合わせとし、レース映像による展開認定とは区別。",
        "",
        "## 母数監査",
        "",
        mdtable(["項目","件数"],[[k,v] for k,v in sorted(quality.items())]),
        "",
        "## 同年度の他場と比較",
        "",
        mdtable(["群","対象R","的中配当行","筋違い%","3人別(ABC)%","勝者ライン孤立%","5万円以上%","平均払戻","中央払戻"],
                [[group, st["races"],st["winning_combinations"],st["first_second_split_pct"],
                  st["abc_pct"],st["winner_only_podium_pct"],st["ge_50000_pct"],
                  fmtmoney(st["mean_yen"]),fmtmoney(st["median_yen"])]
                 for group,st in stats.items()]),
        "",
        "## 1着選手の予想ライン上の位置",
        "",
        mdtable(["群","ライン先頭%","番手%","3番手以降%","単騎%","逃","捲","差"],
                [[k,*[v["winner_role_pct"].get(z) for z in ("ライン先頭","番手","3番手以降","単騎")],
                  *[v["winning_methods"].get(z,0) for z in ("逃","捲","差")]]
                 for k,v in stats.items()]),
        "",
        "## 1〜3着の公表予想ライン構造",
        "",
        mdtable(["群","AAA","AAB","ABA","ABB","ABC"],
                [[k,*[v["line_patterns"].get(p,0) for p in ("AAA","AAB","ABA","ABB","ABC")]]
                 for k,v in stats.items()]),
        "",
        "## 防府・日別の特徴",
        "",
        mdtable(["開催何日目","対象R","筋違い%","ABC%","5万円以上%","中央値"],
                [[k,v["races"],v["first_second_split_pct"],v["abc_pct"],v["ge_50000_pct"],fmtmoney(v["median_yen"])]
                 for k,v in by_day.items()]),
        "",
        "## 防府の開催2日目1〜6R: 個別決着",
        "",
        mdtable(["日付","R","確定組番","100円払戻","決着構造","1着のライン位置","1着決まり手"],
                [[r["date"],r["no"],c["combination"],fmtmoney(c["payout_yen"]),c["pattern"],
                  c["winner_role"],c["winning_method"]]
                 for r in one_races for c in r["winning_outcomes"]]),
        "",
        "## 防府G3高配当トップ15",
        "",
        mdtable(["日付","R","決着","配当","パターン","勝者役割","決まり手"],
                [[r["date"],r["race_no"],r["combination"],fmtmoney(r["payout_yen"]),
                  r["pattern"],r["winner_role"],r["winning_method"]]
                 for r in top[:15]]),
        "",
        "## 2025年の他場別比較：G3・男子S級9車",
        "",
        mdtable(["競輪場","R数","筋違い%","ABC%","5万円以上%","中央値"],
                [[k,v["races"],v["first_second_split_pct"],v["abc_pct"],v["ge_50000_pct"],
                  fmtmoney(v["median_yen"])] for k,v in by_track.items()]),
        "",
        "## 診断上の制限",
        "",
        "- 『異常』は比較群との差分を実測したうえで、標本数・年度・開催日次・レース級班・ライン人数に注意して判定すること。",
        "- 個別のラインが競走中に千切れたか、捲りを受けたか、番手が離れたか等は予想並びと確定結果だけでは断言できない。",
        "- 2024年改修後の防府に限定するため2024年以前のG3を混ぜない。2026年上半期データでは防府開催なし。",
        "- 他333m場は前橋(335m)、松戸・小田原・伊東・富山・奈良(333m)の6場。",
        "- すべての値は実際の決着後の集計であり、払戻/確定オッズで現在のレースを予測したものではない。",
        "",
    ]
    (OUT/"hofu_bank_results_2025plus_report.md").write_text("\n".join(report),encoding="utf-8")
    print(json.dumps({"quality":output["quality"],
                      "cohorts":output["cohorts"],
                      "hofu_by_meeting_day":output["hofu_by_meeting_day"],
                      "hofu_day2_r1_6":output["hofu_day2_r1_6"],
                      "top5":output["hofu_top_payouts"][:5]},ensure_ascii=False))


if __name__ == "__main__":
    main()
