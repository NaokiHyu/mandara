#!/usr/bin/env bash
# AdventureWorks 2014 OLTP をローカルの PostgreSQL に読み込む。
# 本体はリポジトリに入れず、外部から取得する（仕様 17.5）。
#
# 使い方: evals/datasets/adventureworks/load.sh [キャッシュディレクトリ]
# 環境変数: PGDATABASE_AW（既定 adventureworks）。接続先は libpq の標準の環境変数（PGHOST など）に従う。
set -euo pipefail

LORINT_REPO=https://github.com/lorint/AdventureWorks-for-Postgres.git
LORINT_COMMIT=b474991f0df1c4bf55ca4735eb0254ca0709eed2
MS_ZIP_URL=https://github.com/Microsoft/sql-server-samples/releases/download/adventureworks/AdventureWorks-oltp-install-script.zip
MS_ZIP_SHA256=58962e94ea386ef7cd3d8a08211bfd42a79d9b81bdd68fd4b6b0051de6c5bd42

ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
CACHE=${1:-$ROOT/.cache/datasets/adventureworks}
DB=${PGDATABASE_AW:-adventureworks}

mkdir -p "$CACHE"

# 1. 変換スクリプトと DDL（コミット固定）
if [ ! -d "$CACHE/lorint" ]; then
  git clone --quiet "$LORINT_REPO" "$CACHE/lorint"
fi
git -C "$CACHE/lorint" -c advice.detachedHead=false checkout --quiet "$LORINT_COMMIT"

# 2. Microsoft 配布の CSV（ハッシュ固定）
if [ ! -f "$CACHE/aw.zip" ]; then
  curl -fsSL -o "$CACHE/aw.zip.part" "$MS_ZIP_URL"
  mv "$CACHE/aw.zip.part" "$CACHE/aw.zip"
fi
actual=$(shasum -a 256 "$CACHE/aw.zip" | cut -d' ' -f1)
if [ "$actual" != "$MS_ZIP_SHA256" ]; then
  echo "aw.zip のハッシュが一致しない: expected=$MS_ZIP_SHA256 actual=$actual" >&2
  exit 1
fi

# 3. 作業ディレクトリを毎回作り直す（update_csvs.rb は CSV をその場で書き換えるため）
WORK="$CACHE/work"
rm -rf "$WORK"
mkdir -p "$WORK"
unzip -q -j "$CACHE/aw.zip" '*.csv' -d "$WORK"
cp "$CACHE/lorint/update_csvs.rb" "$CACHE/lorint/install.sql" "$WORK/"
(cd "$WORK" && ruby update_csvs.rb)

# 4. DB を作り直して読み込む（install.sql の \copy は相対パスなので WORK で実行する）
dropdb --if-exists "$DB"
createdb "$DB"
(cd "$WORK" && psql -X -q -v ON_ERROR_STOP=1 -d "$DB" -f install.sql)

echo "loaded: $DB"
