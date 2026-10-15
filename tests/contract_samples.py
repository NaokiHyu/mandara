"""言語をまたぐ契約テスト（ui/src/api.test.ts）のための見本を、JSON で標準出力に書く。

UI 側は、これらを実際に受け取る側の検証器（Ajv + ①から生成した JSON Schema）に通す。
- documents：Hypothesis で生成した OM の文書（乱数は固定。毎回同じ）
- gold：正解 OM の文書（サーバーと同じ経路 pipeline.read_ontology → to_document）と、その表の数
- index：見られる OM の一覧の文書
"""

import json
import sys
from pathlib import Path
from typing import Any

from hypothesis import given, settings

from mandara.api.server import OntologySource, to_document, to_index
from mandara.app import pipeline
from mandara.om.model import OntologySourceKind
from mandara.om.ontology import Ontology

from tests.strategies import ontologies

GOLD = Path(__file__).parent.parent / "evals/datasets/adventureworks/gold/ontology"


def main() -> None:
    documents: list[dict[str, Any]] = []

    @settings(max_examples=40, database=None, derandomize=True)
    @given(ontologies())
    def collect(ontology: Ontology) -> None:
        documents.append(to_document(ontology))

    collect()
    gold = pipeline.read_ontology(GOLD)
    index = to_index([OntologySource("gold:adventureworks", "adventureworks", OntologySourceKind.gold, GOLD)])
    json.dump({"documents": documents, "gold": to_document(gold), "gold_tables": len(gold.tables), "index": index},
              sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
