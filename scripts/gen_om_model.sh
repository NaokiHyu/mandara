#!/usr/bin/env bash
# OM メタモデル①（LinkML）から、Domain が使う生成物を作る。生成物は手で編集しない。
#   - model.py           … pydantic の型（gen-pydantic）
#   - relation_traits.py … 関係型の推移性と逆関係（①の enum の annotations。gen-pydantic は落とすので別に出す）
#   - encoding_traits.py … 物理的な表現ごとの変換先の型（同上）
# 使い方: scripts/gen_om_model.sh [出力ディレクトリ]（既定 src/mandara/om）
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT_DIR=${1:-$ROOT/src/mandara/om}
SCHEMA_REL=src/mandara/om/schema/om.yaml
HEADER="# 生成物。手で編集しない。元: $SCHEMA_REL / 生成: scripts/gen_om_model.sh"

# 生成器はスキーマの絶対パスを埋め込むので、リポジトリ相対に直す（チェックアウト位置に依存させない）
{
  echo "$HEADER"
  (cd "$ROOT" && uv run gen-pydantic "$SCHEMA_REL")
} | sed "s|$ROOT/||g" > "$OUT_DIR/model.py"

(cd "$ROOT" && uv run python - "$SCHEMA_REL" "$HEADER" > "$OUT_DIR/relation_traits.py") <<'PY'
import sys
import yaml

schema_path, header = sys.argv[1], sys.argv[2]
schema = yaml.safe_load(open(schema_path, encoding="utf-8"))
values = schema["enums"]["RelationType"]["permissible_values"]
print(header)
print('"""関係型の性質（仕様 4.1）。キーは RelationType の値。"""')
print()
print("from dataclasses import dataclass")
print()
print()
print("@dataclass(frozen=True)")
print("class RelationTraits:")
print("    transitive: bool")
print("    inverse: str")
print()
print()
print("RELATION_TRAITS: dict[str, RelationTraits] = {")
for name, body in values.items():
    ann = body["annotations"]
    transitive, inverse = ann["transitive"], ann["inverse"]
    if not isinstance(transitive, bool) or not isinstance(inverse, str):
        raise SystemExit(f"RelationType.{name} の annotations が不正: {ann!r}")
    print(f"    {name!r}: RelationTraits(transitive={transitive}, inverse={inverse!r}),")
print("}")
PY

(cd "$ROOT" && uv run python - "$SCHEMA_REL" "$HEADER" > "$OUT_DIR/encoding_traits.py") <<'PY'
import sys
import yaml

schema_path, header = sys.argv[1], sys.argv[2]
schema = yaml.safe_load(open(schema_path, encoding="utf-8"))
values = schema["enums"]["SourceEncoding"]["permissible_values"]
value_types = schema["enums"]["ValueType"]["permissible_values"]
print(header)
print('"""物理的な表現（SourceEncoding）ごとの、変換先の意味の型（ValueType の値）。"""')
print()
print("ENCODING_TARGETS: dict[str, str] = {")
for name, body in values.items():
    target = (body.get("annotations") or {}).get("decodes_to")
    if target not in value_types:
        raise SystemExit(f"SourceEncoding.{name} の decodes_to が ValueType に無い: {target!r}")
    print(f"    {name!r}: {target!r},")
print("}")
PY
