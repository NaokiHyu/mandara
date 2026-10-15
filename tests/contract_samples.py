"""言語をまたぐ契約テスト（ui/src/api.test.ts）のための見本を、JSON で標準出力に書く。

UI 側は、これらを実際に受け取る側の検証器（Ajv + ①から生成した JSON Schema）に通す。
- documents：Hypothesis で生成した OM の文書（乱数は固定。毎回同じ）
- gold：正解 OM の文書（サーバーと同じ経路 pipeline.read_ontology → to_document）と、その表の数
- index：見られる OM の一覧の文書
- reviews：Hypothesis で生成した OM のレビューの問いの列（書ける・書けない の両方）
"""

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from hypothesis import given, settings

from mandara.api.server import OntologySource, Reviewing, to_document, to_index, to_review_queue
from mandara.app import pipeline
from mandara.om.model import OntologySourceKind
from mandara.om.ontology import Ontology
from mandara.review.decisions import questions

from tests.strategies import ontologies

GOLD = Path(__file__).parent.parent / "evals/datasets/adventureworks/gold/ontology"


def main() -> None:
    documents: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    source = OntologySource("work:sample", "sample", OntologySourceKind.work, Path("work/sample/ontology"))

    @settings(max_examples=40, database=None, derandomize=True)
    @given(ontologies())
    def collect(ontology: Ontology) -> None:
        documents.append(to_document(ontology))
        asked = tuple(questions(ontology))
        queue = pipeline.ReviewQueue(asked, {e: f"根拠 {e}" for q in asked for e in q.evidence})
        reviewing = Reviewing("alice", lambda: date(2026, 10, 15)) if len(reviews) % 2 else None
        reviews.append(to_review_queue(source, queue, reviewing))

    collect()
    gold = pipeline.read_ontology(GOLD)
    index = to_index([OntologySource("gold:adventureworks", "adventureworks", OntologySourceKind.gold, GOLD)])
    json.dump({"documents": documents, "gold": to_document(gold), "gold_tables": len(gold.tables), "index": index,
               "reviews": reviews}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
