"""知識パック（mandara.modeler.pack、pack_store）：業界の語彙を推論の前に用意する。"""

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mandara.modeler import pack
from mandara.modeler.llm import InvalidAnswerError
from mandara.om.invariants import check
from mandara.om.model import Column, PackManifest, ReviewStatus, SemanticType, Table, ValueType, Vocabulary
from mandara.om.ontology import Ontology
from mandara.omstore.pack_store import PackExistsError, PackLoadError, load_pack, write_pack

MANIFEST = PackManifest(name="bikes", version="0.1.0", description="d", written_by="w")
LABEL = "bikes 0.1.0"


def _t(name: str, type_: str = "date", role: str | None = None, additivity: str | None = None) -> pack.PackTypeAnswer:
    return pack.PackTypeAnswer(name=name, type=type_, description=f"about {name}", role=role, additivity=additivity)


def test_read_keeps_the_types_that_follow_the_rules() -> None:
    answer = pack.PackAnswer(semantic_types=[
        _t("order_date"), _t("unit_price", "money", additivity="non-additive"), _t("row_guid", "identifier", "technical"),
        _t("order_date"), _t("Bad Name"), _t("identifier", "identifier"), _t("status", "enum"),
        _t("hire_date", role="event_time"), _t("color_name", "text", additivity="additive"), _t("x_kind", "nonsense")])
    vocabulary, dropped = pack.read(answer, MANIFEST)
    types = vocabulary.semantic_types or {}
    assert sorted(types) == ["order_date", "row_guid", "unit_price"]
    assert types["unit_price"].requires_unit is True and types["order_date"].requires_unit is None
    assert all(t.pack == LABEL for t in types.values())
    assert [d.item for d in dropped] == ["semantic_type order_date", "semantic_type Bad Name", "semantic_type identifier",
                                         "semantic_type status", "semantic_type hire_date", "semantic_type color_name",
                                         "semantic_type x_kind"]
    assert pack.check(vocabulary, MANIFEST) == []


_NAMES = st.sampled_from(["order_date", "unit_price", "row_guid", "Bad", "identifier", "ship_date"])
_TYPES = st.sampled_from(["date", "money", "identifier", "enum", "text", "nope"])
_ROLES = st.sampled_from([None, "technical", "event_time", "nope"])
_ADDS = st.sampled_from([None, "additive", "nope"])


@given(st.lists(st.tuples(_NAMES, _TYPES, _ROLES, _ADDS), max_size=10))
def test_what_read_keeps_passes_the_check_and_everything_else_is_dropped(
        raw: list[tuple[str, str, str | None, str | None]]) -> None:
    """法則：答えの各意味型は、語彙に入るか理由つきで落ちる。語彙に入ったものは読むときの確かめを通る。"""
    answer = pack.PackAnswer(semantic_types=[_t(n, ty, r, a) for n, ty, r, a in raw])
    vocabulary, dropped = pack.read(answer, MANIFEST)
    assert len(vocabulary.semantic_types or {}) + len(dropped) == len(raw)
    assert pack.check(vocabulary, MANIFEST) == []


def test_check_catches_a_hand_edit_that_breaks_the_rules() -> None:
    vocabulary = Vocabulary(semantic_types={
        "order_date": SemanticType(name="order_date", type=ValueType.date, pack="other 9"),
        "unit_price": SemanticType(name="unit_price", type=ValueType.money, pack=LABEL)})
    problems = pack.check(vocabulary, MANIFEST)
    assert any(p.startswith("order_date: pack が目録と違う") for p in problems)
    assert any(p.startswith("unit_price: requires_unit") for p in problems)


def test_parse_rejects_a_malformed_answer() -> None:
    with pytest.raises(InvalidAnswerError):
        pack.parse({"semantic_types": [{"name": "x"}]})


def _ontology(*used: str) -> Ontology:
    columns = {f"c{i}": Column(name=f"c{i}", semantic_type=u, status=ReviewStatus.proposed, confidence=0.7,
                               evidence=["clm_x"], inferred_by="t") for i, u in enumerate(used)}
    table = Table(id="tbl_t", name="s.t", source="src", entity_group="s", columns=columns or None,
                  status=ReviewStatus.proposed, confidence=0.7, evidence=["clm_x"], inferred_by="t")
    mine = SemanticType(name="order_date", type=ValueType.date)
    return Ontology(vocabulary=Vocabulary(semantic_types={"order_date": mine}), tables=(table,))


def test_merge_keeps_the_ontology_types_and_prune_removes_unused_pack_types() -> None:
    vocabulary, _ = pack.read(pack.PackAnswer(semantic_types=[_t("order_date"), _t("ship_date"), _t("due_date")]),
                              MANIFEST)
    merged = pack.merge(_ontology(), vocabulary)
    assert merged.added == ("due_date", "ship_date") and [d.item for d in merged.kept] == ["semantic_type order_date"]
    types = merged.ontology.vocabulary.semantic_types or {}
    assert types["order_date"].pack is None and types["ship_date"].pack == LABEL
    used = Ontology(vocabulary=merged.ontology.vocabulary, tables=_ontology("ship_date").tables)
    pruned, removed = pack.prune(used, LABEL)
    assert removed == ("due_date",) and sorted(pruned.vocabulary.semantic_types or {}) == ["order_date", "ship_date"]
    assert check(pruned) == []
    assert pack.prune(pruned, LABEL) == (pruned, ())  # 冪等


def test_candidates_narrow_only_the_pack_types() -> None:
    vocabulary, _ = pack.read(pack.PackAnswer(semantic_types=[_t("ship_date"), _t("due_date")]), MANIFEST)
    merged = pack.merge(_ontology(), vocabulary).ontology.vocabulary.semantic_types or {}
    assert sorted(pack.candidates(merged, ("ship_date",))) == ["order_date", "ship_date"]
    assert sorted(pack.candidates(merged, None)) == ["due_date", "order_date", "ship_date"]
    assert sorted(pack.candidates(merged, ())) == ["order_date"]


def test_pack_round_trips_and_is_never_overwritten(tmp_path: Path) -> None:
    vocabulary, _ = pack.read(pack.PackAnswer(semantic_types=[_t("ship_date")]), MANIFEST)
    root = tmp_path / "bikes"
    write_pack(root, MANIFEST, vocabulary)
    loaded = load_pack(root)
    assert loaded.manifest == MANIFEST and loaded.vocabulary == vocabulary and len(loaded.sha256) == 64
    with pytest.raises(PackExistsError):
        write_pack(root, MANIFEST, vocabulary)
    with pytest.raises(PackLoadError):
        load_pack(tmp_path / "missing")


def test_check_catches_a_key_that_differs_from_the_name() -> None:
    t = SemanticType(name="ship_date", type=ValueType.date, pack=LABEL)
    problems = pack.check(Vocabulary(semantic_types={"due_date": t}), MANIFEST)
    assert any(p.startswith("due_date: キーと name") for p in problems)


@given(st.lists(st.sampled_from(["order_date", "ship_date", "due_date", "close_date"]), unique=True))
def test_merging_a_pack_and_pruning_what_no_column_uses_gives_back_the_ontology(names: list[str]) -> None:
    """法則：どの列にも使われないなら、prune(merge(o, パック)) は o に戻る。merge は OM にもとからある意味型を変えない。"""
    base = _ontology()
    vocabulary, _ = pack.read(pack.PackAnswer(semantic_types=[_t(n) for n in names]), MANIFEST)
    merged = pack.merge(base, vocabulary)
    assert (merged.ontology.vocabulary.semantic_types or {})["order_date"] == (base.vocabulary.semantic_types or {})["order_date"]
    assert pack.prune(merged.ontology, LABEL)[0] == base


def test_write_pack_writes_the_pack_and_records_the_answer_outside_it(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from mandara.app import pipeline
    from mandara.llm.model import StructuredRequest, StructuredResponse, Usage
    from mandara.modeler import writer
    from mandara.om.model import Primer, PrimerDocument
    from mandara.omstore.document_store import write_primer

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "README.md").write_text("A bicycle parts wholesaler.", encoding="utf-8")
    readme = writer.Document("README.md", "A bicycle parts wholesaler.")
    write_primer(docs, Primer(written_by="w", documents=[PrimerDocument(name=readme.name, sha256=readme.sha256)],
                              summary="s", entity_groups=[], entries=[]))

    class _Model:
        model = "fake"

        def count_input_tokens(self, request: StructuredRequest) -> int:
            return 1

        def ask(self, request: StructuredRequest) -> StructuredResponse:
            return StructuredResponse({"semantic_types": [{"name": "ship_date", "type": "date", "description": "d"},
                                                          {"name": "Bad", "type": "date", "description": "d"}]},
                                      Usage(1, 1), "fake", None)

    done = pipeline.write_pack_for(docs, tmp_path / "packs" / "bikes", "bikes", "0.1.0", "d", _Model(),
                                   lambda: datetime(2026, 10, 20, tzinfo=UTC), tmp_path / "work" / "packs")
    assert sorted(done.vocabulary.semantic_types or {}) == ["ship_date"] and len(done.dropped) == 1
    assert done.record.parent == tmp_path / "work" / "packs" and done.record.exists()
    assert sorted(p.name for p in (tmp_path / "packs" / "bikes").iterdir()) == ["pack.yaml", "vocabulary.yaml"]
    assert pipeline.read_pack(tmp_path / "packs" / "bikes").vocabulary == done.vocabulary
