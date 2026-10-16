"""モデラー v0（規則だけ）の法則。

- 健全：出力は検証（4.5）を通る。すべて proposed で、evidence の主張 ID は台帳に実在する
- 対応：カタログのオブジェクト → 表がちょうど1つ、範囲内の外部キー → 関係がちょうど1つ。範囲外は Skipped に残る
- 規則：表の種類は、確実な規則（外部キーだけの主キー → relation）にだけ付き、ほかは未決。
  ビューは計算で導出された表（derived。種類とは別の軸）で、種類は規則では決まらない
- 安定 ID：表・関係の ID は、その存在を示す主張だけで決まる（ほかの事実が変わっても変わらない）
- ストア：生成した OM は書いて読める。人の判断を含む OM は上書きしない
"""

from dataclasses import replace
from itertools import combinations
from datetime import date
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mandara.connectors.postgres.catalog_reader import PostgresCatalogReader
from mandara.extractors.catalog import extract
from mandara.modeler.rules import InvalidInputError, Proposal, propose
from mandara.om.names import qualify, split_qualified
from mandara.om.model import ObjectKind, ReferentialAction, ReviewStatus, TableKind, TypeFamily
from mandara.om.ontology import SELF
from mandara.omstore.yaml_store import OntologyWriteRefusedError, load_ontology, write_ontology
from mandara.source.catalog import Catalog, Column, ForeignKey, Key, Scope, Table

from tests.catalog_strategies import catalogs


def _propose(catalog: Catalog) -> Proposal:
    return propose("src", catalog.scope.schemas, extract("src", catalog))


@given(catalogs())
def test_evidence_points_to_existing_claims(catalog: Catalog) -> None:
    claims = {c.id for c in extract("src", catalog)}
    ontology = _propose(catalog).ontology
    annotations = [*ontology.tables, *(c for t in ontology.tables for c in (t.columns or {}).values()),
                   *(r for t in ontology.tables for r in t.relations or [])]
    for annotation in annotations:
        assert annotation.status == ReviewStatus.proposed.value  # AI は承認しない
        assert annotation.evidence and set(annotation.evidence) <= claims


@given(catalogs())
def test_one_table_per_object_and_one_relation_per_foreign_key(catalog: Catalog) -> None:
    ontology = _propose(catalog).ontology
    by_name = {t.name: t for t in ontology.tables}
    assert len(by_name) == len(ontology.tables) == len(catalog.tables)
    ids = {(t.schema, t.name): by_name[qualify(t.schema, t.name)].id for t in catalog.tables}
    for source_table in catalog.tables:
        table = by_name[qualify(source_table.schema, source_table.name)]
        assert table.context == source_table.schema
        assert list(table.columns or {}) == [c.name for c in source_table.columns]  # 位置の順
        relations = [(r.target, frozenset((p.from_, p.to) for p in r.via or [])) for r in table.relations or []]
        # 中身が同じ外部キー（名前だけ違う）は1つの関係にまとまる
        expected = {
            (SELF if (f.target_schema, f.target_table) == (source_table.schema, source_table.name)
             else ids[(f.target_schema, f.target_table)], frozenset(zip(f.columns, f.target_columns, strict=True)))
            for f in source_table.foreign_keys
        }
        assert len(relations) == len(expected) and set(relations) == expected


def _is_junction(table: Table) -> bool:
    """中間表の定義を総当たりで確かめる（実装とは別の方法）：列の組が互いに異なり、それぞれ主キーの真部分集合である
    2本以上の外部キーの組み合わせで、和が主キーに等しいものがある。"""
    if table.primary_key is None:
        return False
    key = set(table.primary_key.columns)
    candidates = {frozenset(f.columns) for f in table.foreign_keys if set(f.columns) < key}
    return any(set().union(*combo) == key
               for n in range(2, len(candidates) + 1) for combo in combinations(candidates, n))


@given(catalogs())
def test_kind_only_from_definitions(catalog: Catalog) -> None:
    by_name = {t.name: t for t in _propose(catalog).ontology.tables}
    for source_table in catalog.tables:
        table = by_name[qualify(source_table.schema, source_table.name)]
        is_view = source_table.kind in (ObjectKind.view, ObjectKind.materialized_view)
        assert (table.derived is True) == is_view and table.derived in (True, None)
        assert (table.kind == TableKind.relation.value) == (not is_view and _is_junction(source_table))
        assert table.kind in (None, TableKind.relation.value)


def _junction_candidate(*fks: tuple[str, ...], pk: tuple[str, ...] = ("a", "b"), extra: tuple[str, ...] = ()) -> Table:
    columns = tuple(Column(n, i + 1, "integer", TypeFamily.integer, False, None, None, None)
                    for i, n in enumerate((*pk, *extra)))
    foreign_keys = tuple(ForeignKey(f"fk{i}", cols, "s", "u", ("id",) * len(cols), ReferentialAction.no_action,
                                    ReferentialAction.no_action) for i, cols in enumerate(fks))
    return Table("s", "t", ObjectKind.table, None, columns, Key("pk", pk), (), foreign_keys, ())


def _kind_of(table: Table) -> str | None:
    target_columns = tuple(Column(f"id{i}", i + 1, "integer", TypeFamily.integer, False, None, None, None)
                           for i in range(3))
    target = Table("s", "u", ObjectKind.table, None, target_columns, Key("pk_u", ("id0",)), (), (), ())
    fixed = replace(table, foreign_keys=tuple(
        replace(f, target_columns=tuple(f"id{i}" for i in range(len(f.columns)))) for f in table.foreign_keys))
    tables = {t.name: t for t in _propose(Catalog(Scope(("s",)), (fixed, target))).ontology.tables}
    return tables["s.t"].kind


def test_junction_examples() -> None:
    assert _kind_of(_junction_candidate(("a",), ("b",))) == TableKind.relation.value
    assert _kind_of(_junction_candidate(("a",), ("b",), extra=("qty",))) == TableKind.relation.value  # 属性はあってよい
    assert _kind_of(_junction_candidate(("a",), ("a",))) is None  # 同じ列の外部キーが2本：中間表ではない
    assert _kind_of(_junction_candidate(("a", "b"), ("a",))) is None  # 主キー全体を覆う外部キー：1:1 の拡張
    assert _kind_of(_junction_candidate(("a",))) is None


@given(catalogs())
def test_alternates_are_uniques_without_nulls(catalog: Catalog) -> None:
    by_name = {t.name: t for t in _propose(catalog).ontology.tables}
    for source_table in catalog.tables:
        identity = by_name[qualify(source_table.schema, source_table.name)].identity
        if source_table.primary_key is None:
            assert identity is None
            continue
        assert identity is not None and identity.primary == list(source_table.primary_key.columns)
        not_null = {c.name for c in source_table.columns if c.nullable is False}
        alternates = [frozenset(k.columns) for k in identity.alternates or []]
        assert len(set(alternates)) == len(alternates)  # 同じ組を2度出さない
        assert frozenset(source_table.primary_key.columns) not in alternates
        expected = {frozenset(k.columns) for k in source_table.uniques if set(k.columns) <= not_null}
        assert set(alternates) == expected - {frozenset(source_table.primary_key.columns)}


def _strip(table: Table) -> Table:
    """存在の場所以外の事実（種類、コメント、一意、CHECK、列の注記、外部キーの動作）を変える。"""
    return replace(
        table, kind=ObjectKind.view if table.kind is ObjectKind.table else ObjectKind.table,
        comment="changed", uniques=(), checks=(),
        columns=tuple(replace(c, comment=None, default=None, nullable=None) for c in table.columns),
        foreign_keys=tuple(replace(f, on_delete=ReferentialAction.cascade, on_update=ReferentialAction.restrict)
                           for f in table.foreign_keys),
    )


@given(catalogs())
def test_ids_depend_only_on_existence(catalog: Catalog) -> None:
    before = _propose(catalog).ontology
    after = _propose(replace(catalog, tables=tuple(_strip(t) for t in catalog.tables))).ontology
    assert [t.id for t in before.tables] == [t.id for t in after.tables]
    assert [r.id for t in before.tables for r in t.relations or []] == [r.id for t in after.tables for r in t.relations or []]


def test_reference_outside_scope_is_skipped_not_dropped() -> None:
    column = Column("id", 1, "integer", TypeFamily.integer, False, None, None, None)
    table = Table("s", "t", ObjectKind.table, None, (column,), Key("pk", ("id",)), (),
                  (ForeignKey("fk_out", ("id",), "elsewhere", "u", ("id",), ReferentialAction.no_action,
                              ReferentialAction.no_action),), ())
    proposal = _propose(Catalog(Scope(("s",)), (table,)))
    assert proposal.ontology.tables[0].relations is None
    assert [s.reason for s in proposal.skipped] == ["参照先 elsewhere.u が範囲外"]
    # 参照先のスキーマが範囲内なのに表が無いのは、範囲外ではなく台帳の欠け
    with pytest.raises(InvalidInputError, match="台帳が不完全"):
        propose("src", ("s", "elsewhere"), extract("src", Catalog(Scope(("s", "elsewhere")), (table,))))


@given(catalogs())
def test_table_confidence_reflects_the_context_guess(catalog: Catalog) -> None:
    for table in _propose(catalog).ontology.tables:
        assert table.confidence is not None and table.confidence <= 0.7


def test_invalid_claims_are_rejected() -> None:
    column = Column("id", 1, "integer", TypeFamily.integer, False, None, None, None)
    claims = extract("src", Catalog(Scope(("s",)), (Table("s", "t", ObjectKind.table, None, (column,), None, (), (), ()),)))
    broken = [c.model_copy(update={"columns": []}) if c.predicate == "not_null" else c for c in claims]
    with pytest.raises(InvalidInputError, match="入力の主張が不正"):
        propose("src", ("s",), broken)


# ---------------------------------------------------------------- ストア


@given(catalogs())
def test_written_ontology_reads_back(tmp_path_factory: pytest.TempPathFactory, catalog: Catalog) -> None:
    root = tmp_path_factory.mktemp("om") / "ontology"
    ontology = _propose(catalog).ontology
    write_ontology(root, ontology)
    loaded = load_ontology(root)
    assert sorted(loaded.tables, key=lambda t: t.id) == sorted(ontology.tables, key=lambda t: t.id)
    write_ontology(root, ontology)  # 提案だけの OM は書き直せる


def test_human_decisions_are_not_overwritten(tmp_path: Path) -> None:
    column = Column("id", 1, "integer", TypeFamily.integer, False, None, None, None)
    catalog = Catalog(Scope(("s",)), (Table("s", "t", ObjectKind.view, None, (column,), None, (), (), ()),))
    ontology = _propose(catalog).ontology
    root = tmp_path / "ontology"
    write_ontology(root, ontology)
    approved = ontology.tables[0].model_copy(update={
        "status": "approved", "kind": "domain", "reviewed_by": "alice", "reviewed_at": date(2026, 10, 15)})
    write_ontology(root, replace(ontology, tables=(approved,)))
    with pytest.raises(OntologyWriteRefusedError, match="人の判断"):
        write_ontology(root, ontology)
    assert load_ontology(root).tables[0].status == "approved"


# ---------------------------------------------------------------- AdventureWorks


def test_adventureworks_proposal(tmp_path: Path) -> None:
    scope = Scope(("humanresources", "person", "production", "purchasing", "sales"))
    claims = extract("adventureworks", PostgresCatalogReader("dbname=adventureworks").read(scope))
    proposal = propose("adventureworks", scope.schemas, claims)
    tables = {t.name: t for t in proposal.ontology.tables}
    assert len(tables) == 68 + 19 + 2
    assert proposal.skipped == ()
    assert sum(len(t.relations or []) for t in tables.values()) == 90
    assert sum(t.derived is True for t in tables.values()) == 21
    assert all(t.kind is None for t in tables.values() if t.derived)
    assert tables["sales.specialofferproduct"].kind == TableKind.relation.value
    assert tables["sales.salesorderdetail"].kind is None  # 規則では決まらない
    detail = tables["sales.salesorderdetail"].identity
    assert detail is not None and detail.primary == ["salesorderid", "salesorderdetailid"]

    root = tmp_path / "ontology"
    write_ontology(root, proposal.ontology)
    assert len(load_ontology(root).tables) == 89


@given(st.lists(st.text(), min_size=1, max_size=3))
def test_qualified_name_roundtrip(parts: list[str]) -> None:
    assert split_qualified(qualify(*parts)) == parts


def test_plain_names_are_not_quoted() -> None:
    assert qualify("sales", "orders") == "sales.orders"
    assert qualify("s1", "..") == 's1.".."'
