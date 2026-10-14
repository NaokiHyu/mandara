"""OM の法則。

- 往復：読む → 書く → 読むで一致する（表と語彙）
- 冪等：書いたものを読んで書き直しても、バイト単位で同じ
- 健全：妥当な OM には違反が無い
- 完全：壊した OM は、対応する規則で必ず検出される（Rule のすべてに壊し方がある）
"""

from collections.abc import Callable
from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mandara.om.invariants import Rule, check
from mandara.om.model import (
    Column,
    ColumnDomain,
    ColumnPair,
    EnumValue,
    HasKind,
    Identity,
    IsARealization,
    Relation,
    RelationType,
    ReviewStatus,
    SourceEncoding,
    Table,
    TableKind,
    Unit,
    UnitSource,
    ValueType,
    Vocabulary,
)
from mandara.om.ontology import Ontology
from mandara.om.relation_traits import RELATION_TRAITS
from mandara.omstore.yaml_store import dump_table, dump_vocabulary, parse_table, parse_vocabulary

from tests.strategies import ontologies, vocabularies


@given(ontologies())
def test_table_roundtrip(ontology: Ontology) -> None:
    for table in ontology.tables:
        assert parse_table(dump_table(table)) == table


@given(ontologies())
def test_table_dump_is_idempotent(ontology: Ontology) -> None:
    for table in ontology.tables:
        once = dump_table(table)
        assert dump_table(parse_table(once)) == once


@given(vocabularies())
def test_vocabulary_roundtrip(vocabulary: Vocabulary) -> None:
    assert parse_vocabulary(dump_vocabulary(vocabulary)) == vocabulary


@given(vocabularies())
def test_vocabulary_dump_is_idempotent(vocabulary: Vocabulary) -> None:
    once = dump_vocabulary(vocabulary)
    assert dump_vocabulary(parse_vocabulary(once)) == once


@given(ontologies())
def test_valid_ontology_has_no_violations(ontology: Ontology) -> None:
    assert check(ontology) == []


# ---------------------------------------------------------------- 壊す
# どの壊し方も、生成された OM の中身に依存せず、決まった形の表を足すか書き換える。

Mutation = Callable[[Ontology], Ontology]
R = ReviewStatus.rejected  # 注釈の必須項目が最も少ない状態。壊したい規則以外を巻き込まない


def _col(name: str, domain: str = "key", unit: UnitSource | None = None) -> Column:
    return Column(name=name, domain=domain, unit=unit, status=R)


def _table(table_id: str, *, kind: TableKind = TableKind.domain, columns: list[Column] | None = None,
           relations: list[Relation] | None = None, grain: str | None = "1行 = 1件") -> Table:
    cols = columns or [_col("id")]
    return Table(
        id=table_id, name=f"s.{table_id}", source="src", context="ctx", kind=kind, grain=grain,
        identity=Identity(primary=["id"]), columns={c.name: c for c in cols}, relations=relations, status=R,
    )


def _rel(rel_id: str, rtype: RelationType, target: str, pairs: list[tuple[str, str]], **kw: object) -> Relation:
    via = [ColumnPair.model_validate({"from": a, "to": b}) for a, b in pairs]
    return Relation.model_validate({"id": rel_id, "type": rtype, "target": target, "via": via, "status": R, **kw})


def _add(o: Ontology, *tables: Table) -> Ontology:
    return replace(o, tables=(*o.tables, *tables))


def _first(o: Ontology) -> Table:
    return o.tables[0]


def _replace_first(o: Ontology, table: Table) -> Ontology:
    return replace(o, tables=(table, *o.tables[1:]))


MUTATIONS: dict[Rule, Mutation] = {
    Rule.UNKNOWN_DOMAIN: lambda o: _add(o, _table("tbl_m", columns=[_col("id", domain="no_such_domain")])),
    Rule.UNKNOWN_VALUE: lambda o: _replace_first(o, _first(o).model_copy(update={"status": "maybe"})),
    Rule.KEY_MISMATCH: lambda o: _add(
        o, _table("tbl_m").model_copy(update={"columns": {"id": _col("id"), "a": _col("b")}})
    ),
    Rule.APPROVED_WITHOUT_REVIEW: lambda o: _replace_first(
        o, _first(o).model_copy(update={"status": "approved", "reviewed_by": None, "reviewed_at": None})
    ),
    Rule.PROPOSED_WITHOUT_PROVENANCE: lambda o: _replace_first(
        o, _first(o).model_copy(update={"status": "proposed", "confidence": 0.5, "evidence": [], "inferred_by": "x"})
    ),
    Rule.MISSING_GRAIN: lambda o: _add(o, _table("tbl_m", kind=TableKind.event, grain="  ")),
    Rule.DUPLICATE_ID: lambda o: _add(o, _first(o)),
    Rule.UNKNOWN_TARGET: lambda o: _add(
        o,
        _table("tbl_m", columns=[_col("id"), _col("fk")],
               relations=[_rel("rel_m", RelationType.has, "tbl_missing", [("fk", "id")], has_kind=HasKind.reference)]),
    ),
    Rule.UNKNOWN_COLUMN: lambda o: _add(
        o, _table("tbl_m").model_copy(update={"identity": Identity(primary=["id", "no_such_column"])})
    ),
    Rule.CYCLE: lambda o: _add(
        o,
        _table("tbl_ma", relations=[_rel("rel_ma", RelationType.part_of, "tbl_mb", [("id", "id")])]),
        _table("tbl_mb", relations=[_rel("rel_mb", RelationType.part_of, "tbl_ma", [("id", "id")])]),
    ),
    Rule.DERIVED_AS_WHOLE: lambda o: _add(
        o,
        _table("tbl_md", kind=TableKind.derived),
        _table("tbl_mp", relations=[_rel("rel_mp", RelationType.part_of, "tbl_md", [("id", "id")])]),
    ),
    Rule.UNIT_MISSING: lambda o: _add(o, _table("tbl_m", columns=[_col("id"), _col("price", domain="amount")])),
    Rule.UNIT_UNEXPECTED: lambda o: _add(o, _table("tbl_m", columns=[_col("id", unit=UnitSource(fixed=Unit.USD))])),
    Rule.UNIT_AMBIGUOUS: lambda o: _add(
        o,
        _table("tbl_m", columns=[
            _col("id"), _col("u", domain="unit_code"),
            _col("price", domain="amount", unit=UnitSource(fixed=Unit.USD, column="u")),
        ]),
    ),
    Rule.RELATION_SHAPE: lambda o: _add(
        o, _table("tbl_m", relations=[_rel("rel_m", RelationType.part_of, "tbl_m", [("id", "id")])])
    ),
    Rule.SHARED_KEY_MISMATCH: lambda o: _add(
        o,
        _table("tbl_ms", columns=[_col("id"), _col("other")]),
        _table("tbl_mt", columns=[_col("id"), _col("x")], relations=[
            _rel("rel_mt", RelationType.is_a, "tbl_ms", [("x", "other")], realized_by=IsARealization.shared_key)
        ]),
    ),
    Rule.ENCODING_MISMATCH: lambda o: _add(o, _table("tbl_m", columns=[
        _col("id"), _col("place", domain="label").model_copy(update={"encoding": SourceEncoding.sqlserver_geography}),
    ])),
    Rule.ENUM_SHAPE: lambda o: replace(
        o,
        vocabulary=o.vocabulary.model_copy(update={"column_domains": {
            **(o.vocabulary.column_domains or {}),
            "dup": ColumnDomain(name="dup", type=ValueType.enum, values=[
                EnumValue(code="1", label="a", status=R), EnumValue(code="1", label="b", status=R),
            ]),
        }}),
    ),
}


def test_every_rule_has_a_mutation() -> None:
    assert set(MUTATIONS) == set(Rule)


@pytest.mark.parametrize("rule", list(Rule), ids=lambda r: r.name)
@given(data=st.data())
def test_broken_ontology_is_detected(rule: Rule, data: st.DataObject) -> None:
    ontology = data.draw(ontologies())
    rules = {v.rule for v in check(MUTATIONS[rule](ontology))}
    assert rule in rules



UNKNOWN_VALUE_PATHS: dict[str, Mutation] = {
    "type": lambda o: replace(o, vocabulary=o.vocabulary.model_copy(update={"column_domains": {
        **(o.vocabulary.column_domains or {}),
        "x": ColumnDomain(name="x", type=ValueType.text).model_copy(update={"type": "geo.point.binary"}),
    }})),
    "unit.fixed": lambda o: _add(o, _table("tbl_m", columns=[
        _col("id"), _col("price", domain="amount", unit=UnitSource.model_construct(fixed="kg")),
    ])),
    "encoding": lambda o: _add(o, _table("tbl_m", columns=[_col("id").model_copy(update={"encoding": "wkb"})])),
}


@pytest.mark.parametrize("path", list(UNKNOWN_VALUE_PATHS))
@given(data=st.data())
def test_closed_vocabulary_values_are_checked(path: str, data: st.DataObject) -> None:
    """UNKNOWN_VALUE の壊し方は status だけなので、値の型・単位・物理的な表現の経路は個別に見る。"""
    ontology = data.draw(ontologies())
    assert Rule.UNKNOWN_VALUE in {v.rule for v in check(UNKNOWN_VALUE_PATHS[path](ontology))}


def _cycle_edge(rel_id: str, rtype: str, target: str) -> Relation:
    if rtype == RelationType.derived_from.value:
        return Relation.model_validate(
            {"id": rel_id, "type": rtype, "target": target, "function": "fn_x", "columns": ["id"], "status": R}
        )
    extra = {"realized_by": IsARealization.shared_key} if rtype == RelationType.is_a.value else {}
    return _rel(rel_id, RelationType(rtype), target, [("id", "id")], **extra)


@pytest.mark.parametrize("rtype", [k for k, t in RELATION_TRAITS.items() if t.transitive])
@given(data=st.data())
def test_cycle_is_detected_for_every_transitive_type(rtype: str, data: st.DataObject) -> None:
    """循環の検出が、①で推移的とされたすべての関係型に効いていること（関係型を足したら自動で対象になる）。"""
    ontology = _add(
        data.draw(ontologies()),
        _table("tbl_ma", relations=[_cycle_edge("rel_ma", rtype, "tbl_mb")]),
        _table("tbl_mb", relations=[_cycle_edge("rel_mb", rtype, "tbl_ma")]),
    )
    cycles = [v for v in check(ontology) if v.rule == Rule.CYCLE]
    assert cycles and all(v.detail.startswith(f"{rtype}:") for v in cycles)
