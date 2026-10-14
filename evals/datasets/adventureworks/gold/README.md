# AdventureWorks の正解 OM（下書き）

`ontology/` は仕様 5章の ontology リポジトリと同じ配置。AdventureWorks の 13 表と、その語彙。

- **AI が手で書いた下書き**なので、すべての注釈が `status: proposed`、`inferred_by: gold-draft`。人のレビューで approved にする（仕様 3.3：approved を書けるのは人だけ）
- `evidence` は主張台帳（6.6）ができるまで、人が読める文字列で書いている（仕様 4.4 の注記）
- ファイルは正規形（`mandara.omstore.yaml_store` の書き出しと一致する形）で置く。YAML コメントは使わず、メモは `note` に書く。手で直したら `dump(parse(file)) == file` を保つこと（tests/test_gold.py で検査する）
