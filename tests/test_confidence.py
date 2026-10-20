"""表の確信度の内訳（mandara.om.confidence）と、その不変条件（CONFIDENCE_PARTS）。"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mandara.om.confidence import PARTS, overall, present, with_part
from mandara.om.invariants import Rule, check
from mandara.om.model import (Column, Identity, ReviewStatus, SemanticType, Table, TableConfidence, TableKind,
                              ValueType, Vocabulary)
from mandara.om.ontology import Ontology

_CONF = st.floats(min_value=0.0, max_value=1.0)
_MAYBE = st.none() | _CONF


def _table(parts: TableConfidence, confidence: float | None = None) -> Table:
    return Table(id="tbl_t", name="s.t", source="src", entity_group="s", kind=TableKind.domain,
                 identity=Identity(primary=["id"]),
                 columns={"id": Column(name="id", semantic_type="t_id", status=ReviewStatus.proposed, confidence=0.8,
                                       evidence=["clm_x"], inferred_by="t")},
                 status=ReviewStatus.proposed, confidence=overall(parts) if confidence is None else confidence,
                 evidence=["clm_x"], inferred_by="t", confidence_parts=parts)


def _ontology(table: Table) -> Ontology:
    return Ontology(vocabulary=Vocabulary(semantic_types={"t_id": SemanticType(name="t_id", type=ValueType.identifier)}),
                    tables=(table,))


def _rules(table: Table) -> list[Rule]:
    return [v.rule for v in check(_ontology(table))]


@given(_CONF, _MAYBE, _MAYBE, st.sampled_from(PARTS), _MAYBE)
def test_replacing_a_part_keeps_the_confidence_the_minimum_of_the_parts(
        group: float, kind: float | None, identity: float | None, name: str, value: float | None) -> None:
    """法則：with_part の後も、表の confidence は書かれた部品の最小値で、不変条件を通る（部品が残る限り）。"""
    table = _table(TableConfidence(entity_group=group, kind=kind, identity=identity))
    parts = table.confidence_parts
    assert parts is not None
    if not {n: v for n, v in present(parts).items() if n != name} and value is None:
        with pytest.raises(ValueError):
            with_part(table, name, value)
        return
    changed = with_part(table, name, value)
    assert changed.confidence_parts is not None
    assert changed.confidence == min(present(changed.confidence_parts).values())
    assert getattr(changed.confidence_parts, name) == value
    assert _rules(changed) == []


def test_check_catches_a_confidence_that_is_not_the_minimum() -> None:
    assert _rules(_table(TableConfidence(entity_group=0.7, kind=0.9), confidence=0.9)) == [Rule.CONFIDENCE_PARTS]


def test_check_catches_parts_for_judgments_that_are_not_there() -> None:
    table = _table(TableConfidence(entity_group=0.7, kind=0.9, identity=0.8))
    assert _rules(table.model_copy(update={"kind": None, "identity": None})) == [Rule.CONFIDENCE_PARTS] * 2
    assert _rules(_table(TableConfidence(entity_group=0.7)).model_copy(
        update={"confidence_parts": TableConfidence()})) == [Rule.CONFIDENCE_PARTS]


def test_with_part_refuses_a_table_without_parts_and_an_unknown_part() -> None:
    table = _table(TableConfidence(entity_group=0.7))
    with pytest.raises(ValueError):
        with_part(table.model_copy(update={"confidence_parts": None}), "kind", 0.9)
    with pytest.raises(ValueError):
        with_part(table, "grain", 0.9)
