# AdventureWorks 2014 OLTP（PostgreSQL 版）

自転車メーカーの業務DB。68 表、5 スキーマ（person / humanresources / production / purchasing / sales）。仕様 17.4 で「関係検出（中規模）の本命」としているもの。

## 読み込みと確認

```sh
export PATH=/opt/homebrew/opt/postgresql@17/bin:$PATH
evals/datasets/adventureworks/load.sh            # 取得 → 変換 → DB adventureworks を作り直す
psql -X -d adventureworks -f evals/datasets/adventureworks/hello.sql
```

本体は `.cache/datasets/adventureworks/` に取得し、リポジトリには入れない。lorint/AdventureWorks-for-Postgres はコミット固定、Microsoft の zip は sha256 固定。

## このデータセットについての決定

- **geo はソースのまま（`person.address.spatiallocation`、SQL Server geography の bytea）**。lat/lon への変換は、仕様 11章／16.2 の決定的な変換関数として OM 側で扱う。データセットを読み込む段階では足さない
- **lorint が追加した便利ビュー（`pe` / `hr` / `pr` / `pu` / `sa` スキーマ）は残す**。元の AW には無い、表の重複ビュー。データソース指定（仕様 9.2）で除外する対象として扱う
