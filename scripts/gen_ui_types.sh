#!/usr/bin/env bash
# OM メタモデル①（LinkML）から、UI（TypeScript）が使う生成物を作る。生成物は手で編集しない。
#   - model.ts        … TypeScript の型（gen-typescript）
#   - om.schema.json    … OntologyDocument の JSON Schema（gen-json-schema）。UI は API の応答をこれで検証する
#   - index.schema.json … OntologyIndex（見られる OM の一覧）の JSON Schema
# 使い方: scripts/gen_ui_types.sh [出力ディレクトリ]（既定 ui/src/om）
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT_DIR=${1:-$ROOT/ui/src/om}
SCHEMA_REL=src/mandara/om/schema/om.yaml
mkdir -p "$OUT_DIR"
{
  echo "// 生成物。手で編集しない。元: $SCHEMA_REL / 生成: scripts/gen_ui_types.sh"
  # gen-typescript は LinkML の date 型を出すのに、その定義を出さない。JSON では ISO 8601 の日付の文字列（om.schema.json も format: date）
  echo "export type date = string;"
  (cd "$ROOT" && uv run gen-typescript "$SCHEMA_REL")
} | sed "s|$ROOT/||g" > "$OUT_DIR/model.ts"
(cd "$ROOT" && uv run gen-json-schema --top-class OntologyDocument "$SCHEMA_REL") | sed "s|$ROOT/||g" > "$OUT_DIR/om.schema.json"
(cd "$ROOT" && uv run gen-json-schema --top-class OntologyIndex "$SCHEMA_REL") | sed "s|$ROOT/||g" > "$OUT_DIR/index.schema.json"
