"""確信度の較正（mandara.evaluation.score.calibrate）：提案の確信度が、正解と突き合わせてどれだけ当たっているか。"""

from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from mandara.evaluation.score import CALIBRATION_BINS, calibrate
from mandara.om.model import TableKind
from mandara.om.ontology import Ontology
from mandara.omstore.yaml_store import load_ontology

GOLD = load_ontology(Path(__file__).parent.parent / "evals/datasets/adventureworks/gold/ontology")


def _with_confidence(ontology: Ontology, confidence: float) -> Ontology:
    tables = tuple(t.model_copy(update={
        "confidence": confidence,
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
