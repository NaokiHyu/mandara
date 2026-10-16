"""評価 v0 の法則。

- 自分自身との突き合わせは満点（全観点で復元率 1.0、誤提案率 0 か測れない）
- 名前で突き合わせる：ID をすべて付け替えても点数は変わらない
- 未決は誤提案に数えない。関係の型は、端点が合った関係についてだけ測る
- 正解の範囲の外の提案は採点せず、件数だけ数える
"""

from dataclasses import replace
from pathlib import Path

from collections.abc import Iterable
from typing import cast

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from mandara.evaluation.score import ASPECTS, InvalidForScoringError, Report, facts, score
from mandara.om.invariants import check
from mandara.om.model import Relation, RelationType, Table, TableKind
from mandara.om.ontology import SELF, Ontology
from mandara.omstore.yaml_store import load_ontology

from tests.strategies import ontologies

GOLD = Path(__file__).parent.parent / "evals/datasets/adventureworks/gold/ontology"


def _assert_perfect(report: Report) -> None:
    for a in report.aspects:
        assert a.recall in (1.0, None), (a.aspect, a.missed)
        assert a.false_rate in (0.0, None), (a.aspect, a.wrong)
    assert (report.unscored_tables, report.unscored_relations) == (0, 0)


@given(ontologies())
def test_self_comparison_is_perfect(ontology: Ontology) -> None:
    _assert_perfect(score(ontology, ontology))


def test_gold_against_itself_is_perfect() -> None:
    gold = load_ontology(GOLD)
    _assert_perfect(score(gold, gold))


def _renamed(ontology: Ontology) -> Ontology:
    """表と関係の ID をすべて付け替える（参照も追従させる）。"""
    ids = {t.id: f"tbl_renamed{i}" for i, t in enumerate(ontology.tables)}

    def retarget(table: Table) -> Table:
        relations = [r.model_copy(update={"id": f"rel_renamed_{r.id}",
                                          "target": r.target if r.target == SELF else ids[r.target]})
                     for r in table.relations or []]
        renamed = {r_old.id: r_new.id for r_old, r_new in zip(table.relations or [], relations, strict=True)}
        edge = table.edge
        if edge is not None:
            edge = edge.model_copy(update={"from_": renamed[edge.from_], "to": renamed[edge.to]})
        return table.model_copy(update={"id": ids[table.id], "relations": relations or None, "edge": edge})

    return replace(ontology, tables=tuple(retarget(t) for t in ontology.tables))


@given(ontologies())
def test_score_does_not_depend_on_ids(ontology: Ontology) -> None:
    _assert_perfect(score(_renamed(ontology), ontology))


def test_gold_with_renamed_ids_is_perfect() -> None:
    gold = load_ontology(GOLD)
    _assert_perfect(score(_renamed(gold), gold))


def test_undecided_is_missed_not_wrong() -> None:
    gold = load_ontology(GOLD)
    # 辺を宣言する表は relation でなければならない（EDGE_SHAPE）ので、種類を残す
    undecided = replace(gold, tables=tuple(t if t.edge is not None else t.model_copy(update={"kind": None})
                                           for t in gold.tables))
    kind = score(undecided, gold).aspect("kind")
    assert (kind.correct, kind.proposed, kind.gold, kind.false_rate) == (1, 1, 14, 0.0)
    assert len(kind.missed) == 13 and kind.wrong == ()


def test_relation_type_is_measured_only_on_matched_endpoints() -> None:
    gold = load_ontology(GOLD)
    detail = next(t for t in gold.tables if t.name == "sales.salesorderdetail")
    relations = list(detail.relations or [])
    header = next(r for r in relations if r.type == RelationType.part_of.value)
    as_has = header.model_copy(update={"type": RelationType.has, "has_kind": "reference"})
    wrong_endpoint = header.model_copy(update={"id": "rel_x", "via": [
        header.via[0].model_copy(update={"from_": "carriertrackingnumber"})] if header.via else None})
    changed = detail.model_copy(update={"relations": [as_has if r is header else r for r in relations] + [wrong_endpoint]})
    report = score(replace(gold, tables=tuple(changed if t is detail else t for t in gold.tables)), gold)

    endpoints, types = report.aspect("relation_endpoints"), report.aspect("relation_types")
    assert endpoints.wrong == ("(sales.salesorderdetail, sales.salesorderheader, {(carriertrackingnumber, salesorderid)})",)
    assert len(types.wrong) == 1 and "has, reference" in types.wrong[0]  # 端点の外れた関係は型の採点に入らない
    # 型の分母は、端点が正解と提案の両方にある関係（外した1本は入らない）
    assert types.proposed == types.gold == len(set(facts(gold, _scope(gold), "relation_endpoints")))


def test_proposals_outside_gold_are_counted_not_scored() -> None:
    gold = load_ontology(GOLD)
    base = gold.tables[0]
    assert base.identity is not None
    extra = base.model_copy(update={"id": "tbl_extra", "name": "sales.not_in_gold", "relations": None, "edge": None})
    out = Relation.model_validate({
        "id": "rel_out", "type": "has", "has_kind": "reference", "target": "tbl_extra", "cardinality": "n:1",
        "via": [{"from": c, "to": c} for c in base.identity.primary], "status": "rejected"})
    pointing = base.model_copy(update={"relations": [*(base.relations or []), out]})
    report = score(replace(gold, tables=(pointing, *gold.tables[1:], extra)), gold)
    assert (report.unscored_tables, report.unscored_relations) == (1, 1)
    _assert_perfect(replace(report, unscored_tables=0, unscored_relations=0))


def test_adventureworks_stage1_score() -> None:
    """段 1（規則だけ）の AW の点数のスナップショット。変わったら（改善でも）、docs/backlog.md の点数表と同時に更新する。"""
    from mandara.connectors.postgres.catalog_reader import PostgresCatalogReader
    from mandara.extractors.catalog import extract
    from mandara.modeler.rules import propose
    from mandara.source.catalog import Scope

    scope = Scope(("humanresources", "person", "production", "purchasing", "sales"))
    claims = extract("adventureworks", PostgresCatalogReader("dbname=adventureworks").read(scope))
    report = score(propose("adventureworks", scope.schemas, claims).ontology, load_ontology(GOLD))
    got = {a.aspect: (a.correct, a.gold, a.proposed) for a in report.aspects}
    assert got == {
        "tables": (14, 14, 14),
        "kind": (0, 14, 0),
        "primary_key": (14, 14, 14),
        "alternate_keys": (0, 6, 0),  # 正解の代替キーは、制約の無いデータ上の一意（プロファイルで取る）
        "relation_endpoints": (15, 17, 15),
        "relation_types": (8, 15, 15),  # 型は、端点が両方にある関係（15本）だけで測る
        "edges": (0, 1, 0),
        "column_types": (0, 141, 0),
        "semantic_type_pairs": (0, 234, 0),
        "grain_present": (0, 5, 0),
    }



def _scope(ontology: Ontology) -> frozenset[str]:
    return frozenset(t.name for t in ontology.tables)


@given(ontologies())
def test_facts_never_contain_none(ontology: Ontology) -> None:
    def walk(x: object) -> None:
        assert x is not None
        if isinstance(x, (tuple, frozenset)):
            for y in tuple(cast(Iterable[object], x)):
                walk(y)

    for aspect in ASPECTS:
        for fact in facts(ontology, _scope(ontology), aspect):
            walk(fact)


def test_gold_exercises_every_aspect() -> None:
    gold = load_ontology(GOLD)
    for aspect in ASPECTS:
        assert facts(gold, _scope(gold), aspect), aspect


@given(ontologies(), st.data())
def test_removing_one_relation_is_local(ontology: Ontology, data: st.DataObject) -> None:
    """関係を1本消すと、関係の端点の取りこぼしがちょうど1つ増え、ほかの観点は満点のまま（局所性）。"""
    candidates = [(i, r) for i, t in enumerate(ontology.tables) for r in t.relations or []
                  if t.edge is None or r.id not in (t.edge.from_, t.edge.to)]
    assume(candidates)
    i, removed = data.draw(st.sampled_from(candidates))
    table = ontology.tables[i]
    rest = [r for r in table.relations or [] if r is not removed]
    proposal = replace(ontology, tables=tuple(
        t.model_copy(update={"relations": rest or None}) if j == i else t for j, t in enumerate(ontology.tables)))
    report = score(proposal, ontology)
    endpoints = report.aspect("relation_endpoints")
    lost = (set(facts(ontology, _scope(ontology), "relation_endpoints"))
            - set(facts(proposal, _scope(ontology), "relation_endpoints")))
    assert len(endpoints.missed) == len(lost) <= 1
    for a in report.aspects:
        if a.aspect != "relation_endpoints":
            assert a.recall in (1.0, None) and a.false_rate in (0.0, None), a.aspect


@given(ontologies(), st.data())
def test_changing_a_kind_is_detected(ontology: Ontology, data: st.DataObject) -> None:
    """表の種類を1つ別の値に変えると、種類の観点で誤りと取りこぼしがちょうど1つずつ出る（感度）。"""
    decided = [i for i, t in enumerate(ontology.tables) if t.kind is not None]
    assume(decided)
    i = data.draw(st.sampled_from(decided))
    table = ontology.tables[i]
    other = data.draw(st.sampled_from([k for k in TableKind if k.value != table.kind]))
    changed = table.model_copy(update={"kind": other})
    assume(check(replace(ontology, tables=(*ontology.tables[:i], changed, *ontology.tables[i + 1:]))) == [])
    report = score(replace(ontology, tables=(*ontology.tables[:i], changed, *ontology.tables[i + 1:])), ontology)
    kind = report.aspect("kind")
    assert (len(kind.wrong), len(kind.missed)) == (1, 1)


def test_duplicated_proposals_count_as_wrong() -> None:
    """同じ名前の表を2つ提案すると（ID は別）、2つ目は誤りに数える。"""
    gold = load_ontology(GOLD)
    copy = gold.tables[0].model_copy(update={"id": "tbl_dup", "relations": None, "edge": None})
    tables = score(replace(gold, tables=(*gold.tables, copy)), gold).aspect("tables")
    assert tables.wrong == (f"重複 ({copy.name})",)
    assert tables.false_rate == 1 / 15


def test_invalid_ontology_is_not_scored() -> None:
    gold = load_ontology(GOLD)
    broken = gold.tables[0].model_copy(update={"relations": [
        next(r for t in gold.tables for r in t.relations or [] if r.via).model_copy(
            update={"id": "rel_broken", "target": "tbl_nowhere"})]})
    with pytest.raises(InvalidForScoringError, match="提案"):
        score(replace(gold, tables=(broken, *gold.tables[1:])), gold)
