"""確信度の較正（mandara.evaluation.score.calibrate）：提案の確信度が、正解と突き合わせてどれだけ当たっているか。"""

from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from mandara.evaluation.score import CALIBRATION_BINS, calibrate
from mandara.om.model import Identity, TableConfidence, TableKind
from mandara.om.ontology import Ontology
from mandara.omstore.yaml_store import load_ontology

GOLD = load_ontology(Path(__file__).parent.parent / "evals/datasets/adventureworks/gold/ontology")


def _with_confidence(ontology: Ontology, confidence: float) -> Ontology:
    tables = tuple(t.model_copy(update={
        "confidence": confidence,
        "confidence_parts": TableConfidence(entity_group=confidence, kind=confidence,
                                            identity=confidence if t.identity is not None else None),
        "relations": [r.model_copy(update={"confidence": confidence}) for r in t.relations or []] or None,
        "columns": {n: c.model_copy(update={"confidence": confidence}) for n, c in (t.columns or {}).items()} or None,
    }) for t in ontology.tables)
    return Ontology(vocabulary=ontology.vocabulary, tables=tables)


@settings(max_examples=20, deadline=None)
@given(st.floats(min_value=0.0, max_value=1.0))
def test_the_gold_itself_is_always_right_and_lands_in_one_band(confidence: float) -> None:
    """法則：正解そのものを提案にすると、数えた注釈はすべて当たり。閾値未満は数えない。どの注釈も 1 つの帯に入る。"""
    calibration = calibrate(_with_confidence(GOLD, confidence), GOLD)
    for bins in calibration.aspects.values():
        assert all(b.correct == b.proposed for b in bins)
        counted = [b for b in bins if b.proposed]
        if confidence < CALIBRATION_BINS[0]:
            assert counted == []
        else:
            assert len(counted) <= 1 and all(b.low <= confidence <= b.high for b in counted)


def test_a_wrong_kind_counts_as_wrong_in_its_band() -> None:
    proposed = _with_confidence(GOLD, 0.75)
    first = proposed.tables[0]
    other = TableKind.event if first.kind != TableKind.event.value else TableKind.domain
    proposed = Ontology(vocabulary=proposed.vocabulary,
                        tables=(first.model_copy(update={"kind": other}), *proposed.tables[1:]))
    [band] = [b for b in calibrate(proposed, GOLD).aspects["kind"] if b.proposed]
    assert (band.low, band.proposed - band.correct) == (0.7, 1)


def _changed(index: int, **update: object) -> Ontology:
    proposed = _with_confidence(GOLD, 0.75)
    tables = list(proposed.tables)
    tables[index] = tables[index].model_copy(update=update)
    return Ontology(vocabulary=proposed.vocabulary, tables=tuple(tables))


def test_each_part_of_the_table_confidence_is_scored_on_its_own() -> None:
    # 主キーを変えても検証を通る表（共有主キーの is-a に関わらない）
    shared = {t.id for t in GOLD.tables for r in t.relations or [] if r.realized_by is not None} | {
        r.target for t in GOLD.tables for r in t.relations or [] if r.realized_by is not None}
    index, table = next((i, t) for i, t in enumerate(GOLD.tables) if t.id not in shared and t.identity is not None)
    assert table.identity is not None
    wrong_key = [c for c in sorted(table.columns or {}) if c not in table.identity.primary][:1]
    proposed = _changed(index, entity_group=f"not_{table.entity_group}", identity=Identity(primary=wrong_key))
    aspects = calibrate(proposed, GOLD).aspects
    for aspect, wrong in (("entity_group", 1), ("identity", 1), ("kind", 0)):
        [band] = [b for b in aspects[aspect] if b.proposed]
        assert (aspect, band.proposed - band.correct) == (aspect, wrong)


def test_tables_without_parts_are_counted_apart() -> None:
    calibration = calibrate(_changed(0, confidence_parts=None), GOLD)
    assert calibration.without_parts == 1
    assert sum(b.proposed for b in calibration.aspects["kind"]) == len(GOLD.tables) - 1
