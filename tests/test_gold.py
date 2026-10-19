"""AdventureWorks の正解 OM（下書き）が、メタモデル①と不変条件（4.5）を満たし、正規形で置かれていること。"""

from pathlib import Path

import pytest

from mandara.om.invariants import check
from mandara.omstore.yaml_store import dump_table, dump_vocabulary, load_ontology, parse_table, parse_vocabulary

GOLD = Path(__file__).parent.parent / "evals/datasets/adventureworks/gold/ontology"
TABLE_FILES = sorted(GOLD.glob("entity_groups/*/tables/*/*.yaml"))


def test_gold_has_no_violations() -> None:
    ontology = load_ontology(GOLD)
    assert len(ontology.tables) == 14
    assert check(ontology) == []


@pytest.mark.parametrize("path", TABLE_FILES, ids=lambda p: p.stem)
def test_gold_table_is_canonical(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert dump_table(parse_table(text)) == text


def test_gold_vocabulary_is_canonical() -> None:
    text = (GOLD / "vocabulary.yaml").read_text(encoding="utf-8")
    assert dump_vocabulary(parse_vocabulary(text)) == text
