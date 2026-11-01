# G3 競輪過去データ：独立研究ワークスペース

G3（記念）データの原本を別リポジトリから取り込み、年別の検索可能なSQLiteに整形します。研究・モデル・買い目の追加はこのリポジトリに限定します。元の `keirin-market-analysis-` は変更しません。

## 元データと収録期間

- [原本: yuhevita-wq/keirin-market-analysis-/data/grade_races/g3](https://github.com/yuhevita-wq/keirin-market-analysis-/tree/main/data/grade_races/g3)
- [GitHub Release バックアップ](https://github.com/yuhevita-wq/keirin-market-analysis-/releases/tag/grade-race-databank-2026-09-02)
- 2022・2023・2024・2025年の全四半期、2026年1〜6月：計18アーカイブ（約56MB）
- 原本の保存・識別は `data/source_manifest.csv` にあるSHA-256およびバイト数で検証

## 保存ディレクトリ

| 保存先 | 用途 | 状態 |
|---|---|---|
| `data/source_manifest.csv` | 原本18個のパス・SHA-256 | Git管理 |
| `data/archives/YYYY/*.zip` | 原本ZIPの完全な複製 | Git管理 |
| `data/ready/g3_YYYY.sqlite.gz` | 解析用SQLite圧縮版（年別） | Git管理 |
| `data/local/g3_YYYY.sqlite` | 自動展開した作業用DB | Git対象外 |
| `reports/ingestion/YYYY.json` | 件数・欠損・整合性監査 | Git管理 |

※2026年は**上半期のみ**。年別SQLiteにはファイル由来を示す `archive_period` と `archive_source_path` を保持します。

## 使い方

GitHubの **Actions → Build verified G3 datasets** がデータの取り込み、検証、SQLite生成、Gitへの保存を実行します。`main` の当該workflow追加時にも実行されます。

ダウンロードしたリポジトリ内で標準Python（追加ライブラリ不要）を使います。

```bash
# 2023年のレース一覧（自動的にSQLite.gzを展開）
python scripts/query_g3.py --year 2023 \
  --sql "SELECT race_date,track,race_no,race_type,predicted_line_formation FROM races LIMIT 10"

# 2023年の3連単確定オッズの総件数
python scripts/query_g3.py --year 2023 \
  --sql "SELECT COUNT(*) AS n FROM trifecta_final_odds"

# 特定のレースの出走表（結果・オッズを含まない）
python scripts/query_g3.py --year 2023 \
  --sql "SELECT race_id,car_no,player_name,age,score,line_id,line_position FROM v_pre_race_entries LIMIT 10"

# 任意のSQLの結果をCSVへ
python scripts/query_g3.py --year 2023 \
  --sql "SELECT * FROM v_paid_trifecta LIMIT 20" --output sample.csv

# 5年分のDBをアタッチして横断検索（テーブル名に年を付ける）
python scripts/query_g3.py --year all \
  --sql "SELECT race_date,track FROM y2023.races LIMIT 5"

# ローカルで全アーカイブを原本から再取り込み・再構築
python scripts/prepare_g3.py all --year all
# 既存ZIPのハッシュと整合性だけ再検証
python scripts/prepare_g3.py verify --year all
```

## データテーブルと利用上の区別

- `races`: 開催日、レース種別、並び予想など（1レース1行）
- `entries`: 事前出走表、車番・選手・得点・ライン内役割
- `results`: 着順・決まり手（**事後情報**）
- `payouts`: 7券種の払戻金（**事後情報**）
- `trifecta_final_odds`: 3連単確定オッズ（**事後に確認された価格**）
- `trio_final_odds`: 3連複確定オッズ（**事後に確認された価格**）
- `failures`, `line_failures`, `result_failures`, `odds_failures`, `excluded_girls`: 失敗・除外の観測ログ

分析用ビュー：`v_pre_race_races`, `v_pre_race_entries`, `v_paid_trifecta`, `v_trifecta_final_odds`, `v_trio_final_odds`。

**重要な制限：** オッズは`odds_phase=final`の確定値です。締切前、T-10分などのスナップショットとして購入の事前判定に使わないでください。ラインは公開された「並び予想」であって公式確定ラインではありません。出走表データを過去に遡って取得した時点とレース当日公開状態の完全一致も別途監査が必要です。

## 品質保証

1. 18個のZIPを `data/source_manifest.csv` で**バイト数・SHA-256照合**する。
2. ZIPメンバーCRCを検証し、CSVを年別SQLiteに原文文字列として取り込む（推測補完・勝手な除外なし）。
3. `race_id`、`race_id+car_no`、`race_id+combination` の重複を一意制約で検出する（違反時は停止）。
4. 原本レース外の参照（孤児）、非G3、対象年外の観測を検出し、異常があれば停止。
5. 取得できなかったレース／オッズの件数を年別監査レポートに残す（欠損は埋めない）。
6. SQLiteを年別に圧縮し、完全再構築可能な元ZIPと共にGit管理する。

この取り込み処理は「G3全開催の完全網羅」「全レース事前時点の購入可能オッズ」を保証するものではありません。データ完全性は監査結果で判定します。
