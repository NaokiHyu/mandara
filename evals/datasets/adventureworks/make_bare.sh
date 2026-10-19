#!/usr/bin/env bash
# 実験の世界：AdventureWorks から「宣言」を抜いた複製を作る（結合の推定・意味型の検出を、宣言に頼らずに測るため）。
# 元の DB（adventureworks）には触らない。複製をテンプレートから作り直す。正解は元の正解 OM（gold。段 3 は列名を変えたもの）。
#
# 使い方: evals/datasets/adventureworks/make_bare.sh 段
#   段 1：外部キーの制約を全部外す（主キー・一意・コメントは残す）              → DB aw_bare1
#   段 2：段 1 に加えて、表と列のコメントも消す（LLM が文書に頼れない）          → DB aw_bare2
#   段 3：段 2 に加えて、列名を意味の無い名前にする（c_ ＋ md5(スキーマ.表.列) の先頭 6 桁）→ DB aw_bare3
#         外部キーを外す前の複製（名前だけ変えた正解）を aw_truth3 に作る（eval-joins / eval-keys の --truth）。
#         正解 OM は rename_gold.py で同じ名前に変える
#   段 4：段 3 に加えて、主キー・一意の制約と一意インデックスを外す（NOT NULL は残す）→ DB aw_bare4
#         名前の変え方は段 3 と同じなので、正解は段 3 と同じ（aw_truth3 と work/gold_bare3。aw_truth3 も作り直す）
# 環境変数: PGDATABASE_AW（元。既定 adventureworks）。接続先は libpq の標準の環境変数（PGHOST など）に従う。
set -euo pipefail

LEVEL=${1:?段（1〜4）を指定する}
case "$LEVEL" in 1|2|3|4) ;; *) echo "段は 1〜4: $LEVEL" >&2; exit 2 ;; esac
SRC=${PGDATABASE_AW:-adventureworks}
DB=aw_bare$LEVEL
SCHEMAS="'person','humanresources','sales','purchasing','production'"

# テンプレートからの複製は、元の DB に接続が残っていると失敗する（黙って待たずにエラーで止まる）
dropdb --if-exists "$DB"
createdb -T "$SRC" "$DB"

if [ "$LEVEL" -ge 3 ]; then
  # 列名を変える（表・ビュー・マテリアライズドビュー）。ビューの定義と CHECK・既定値の式は、PostgreSQL が新しい名前で持ち直す
  psql -X -q -v ON_ERROR_STOP=1 -d "$DB" <<SQL
DO \$\$
DECLARE r record;
BEGIN
  FOR r IN SELECT n.nspname, c.relname, a.attname,
                  CASE c.relkind WHEN 'm' THEN 'MATERIALIZED VIEW' ELSE 'TABLE' END AS kind
             FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'v', 'm') AND n.nspname IN ($SCHEMAS) AND a.attnum > 0 AND NOT a.attisdropped
  LOOP
    EXECUTE format('ALTER %s %I.%I RENAME COLUMN %I TO %I', r.kind, r.nspname, r.relname, r.attname,
                   'c_' || substr(md5(r.nspname || '.' || r.relname || '.' || r.attname), 1, 6));
  END LOOP;
END \$\$;
SQL
  dropdb --if-exists aw_truth3
  createdb -T "$DB" aw_truth3
fi

psql -X -q -v ON_ERROR_STOP=1 -d "$DB" <<SQL
DO \$\$
DECLARE r record;
BEGIN
  FOR r IN SELECT n.nspname, t.relname, c.conname
             FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE c.contype = 'f' AND n.nspname IN ($SCHEMAS)
  LOOP
    EXECUTE format('ALTER TABLE %I.%I DROP CONSTRAINT %I', r.nspname, r.relname, r.conname);
  END LOOP;
END \$\$;
SQL

if [ "$LEVEL" = 4 ]; then
  # 主キー・一意の制約（外部キーは上で外した）と、制約でない一意インデックス
  psql -X -q -v ON_ERROR_STOP=1 -d "$DB" <<SQL
DO \$\$
DECLARE r record;
BEGIN
  FOR r IN SELECT n.nspname, t.relname, c.conname
             FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE c.contype IN ('p', 'u') AND n.nspname IN ($SCHEMAS)
  LOOP
    EXECUTE format('ALTER TABLE %I.%I DROP CONSTRAINT %I', r.nspname, r.relname, r.conname);
  END LOOP;
  FOR r IN SELECT n.nspname, c.relname
             FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE i.indisunique AND n.nspname IN ($SCHEMAS)
  LOOP
    EXECUTE format('DROP INDEX %I.%I', r.nspname, r.relname);
  END LOOP;
END \$\$;
SQL
fi

if [ "$LEVEL" -ge 2 ]; then
  psql -X -q -v ON_ERROR_STOP=1 -d "$DB" <<SQL
DO \$\$
DECLARE r record;
BEGIN
  -- COMMENT ON の対象の種類は relkind で変わる（ビューに TABLE を使うとエラー）
  FOR r IN SELECT n.nspname, c.relname,
                  CASE c.relkind WHEN 'r' THEN 'TABLE' WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW' END AS kind
             FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'v', 'm') AND n.nspname IN ($SCHEMAS)
  LOOP
    EXECUTE format('COMMENT ON %s %I.%I IS NULL', r.kind, r.nspname, r.relname);
  END LOOP;
  FOR r IN SELECT n.nspname, c.relname, a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
             JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'v', 'm') AND n.nspname IN ($SCHEMAS) AND a.attnum > 0 AND NOT a.attisdropped
              AND col_description(c.oid, a.attnum) IS NOT NULL
  LOOP
    EXECUTE format('COMMENT ON COLUMN %I.%I.%I IS NULL', r.nspname, r.relname, r.attname);
  END LOOP;
END \$\$;
SQL
fi

# 確かめる：外部キーが 0、（段 2 なら）コメントが 0
fks=$(psql -X -At -d "$DB" -c "SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
                               WHERE c.contype = 'f' AND n.nspname IN ($SCHEMAS)")
# 確かめる範囲は消す範囲と同じ（表・ビュー・マテリアライズドビューとその列のコメント）
comments=$(psql -X -At -d "$DB" -c "SELECT count(*) FROM pg_description d JOIN pg_class c ON c.oid = d.objoid
                                    JOIN pg_namespace n ON n.oid = c.relnamespace
                                    WHERE d.classoid = 'pg_class'::regclass AND c.relkind IN ('r', 'v', 'm')
                                      AND n.nspname IN ($SCHEMAS)")
named=$(psql -X -At -d "$DB" -c "SELECT count(*) FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
                                 JOIN pg_namespace n ON n.oid = c.relnamespace
                                 WHERE c.relkind IN ('r', 'v', 'm') AND n.nspname IN ($SCHEMAS) AND a.attnum > 0
                                   AND NOT a.attisdropped AND a.attname !~ '^c_[0-9a-f]{6}$'")
keys=$(psql -X -At -d "$DB" -c "SELECT (SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
                                        WHERE c.contype IN ('p', 'u') AND n.nspname IN ($SCHEMAS))
                              + (SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
                                 JOIN pg_namespace n ON n.oid = c.relnamespace
                                 WHERE i.indisunique AND n.nspname IN ($SCHEMAS))")
echo "$DB: 外部キー $fks · コメント $comments · 元の名前の列 $named · 主キー・一意 $keys"
[ "$fks" = 0 ] || { echo "外部キーが残っている" >&2; exit 1; }
if [ "$LEVEL" -ge 2 ]; then [ "$comments" = 0 ] || { echo "コメントが残っている" >&2; exit 1; }; fi
if [ "$LEVEL" -ge 3 ]; then [ "$named" = 0 ] || { echo "元の名前の列が残っている" >&2; exit 1; }; fi
if [ "$LEVEL" = 4 ]; then [ "$keys" = 0 ] || { echo "主キー・一意が残っている" >&2; exit 1; }; fi
