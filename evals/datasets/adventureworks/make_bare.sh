#!/usr/bin/env bash
# 実験の世界：AdventureWorks から「宣言」を抜いた複製を作る（結合の推定・意味型の検出を、宣言に頼らずに測るため）。
# 元の DB（adventureworks）には触らない。複製をテンプレートから作り直す。正解は元の正解 OM（gold）をそのまま使う。
#
# 使い方: evals/datasets/adventureworks/make_bare.sh 段
#   段 1：外部キーの制約を全部外す（主キー・一意・コメントは残す）              → DB aw_bare1
#   段 2：段 1 に加えて、表と列のコメントも消す（LLM が文書に頼れない）          → DB aw_bare2
# 環境変数: PGDATABASE_AW（元。既定 adventureworks）。接続先は libpq の標準の環境変数（PGHOST など）に従う。
set -euo pipefail

LEVEL=${1:?段（1 か 2）を指定する}
case "$LEVEL" in 1|2) ;; *) echo "段は 1 か 2: $LEVEL" >&2; exit 2 ;; esac
SRC=${PGDATABASE_AW:-adventureworks}
DB=aw_bare$LEVEL
SCHEMAS="'person','humanresources','sales','purchasing','production'"

# テンプレートからの複製は、元の DB に接続が残っていると失敗する（黙って待たずにエラーで止まる）
dropdb --if-exists "$DB"
createdb -T "$SRC" "$DB"

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

if [ "$LEVEL" = 2 ]; then
  psql -X -q -v ON_ERROR_STOP=1 -d "$DB" <<SQL
DO \$\$
DECLARE r record;
BEGIN
  FOR r IN SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'v', 'm') AND n.nspname IN ($SCHEMAS)
  LOOP
    EXECUTE format('COMMENT ON TABLE %I.%I IS NULL', r.nspname, r.relname);
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
comments=$(psql -X -At -d "$DB" -c "SELECT count(*) FROM pg_description d JOIN pg_class c ON c.oid = d.objoid
                                    JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname IN ($SCHEMAS)")
echo "$DB: 外部キー $fks · コメント $comments"
[ "$fks" = 0 ] || { echo "外部キーが残っている" >&2; exit 1; }
if [ "$LEVEL" = 2 ]; then [ "$comments" = 0 ] || { echo "コメントが残っている" >&2; exit 1; }; fi
