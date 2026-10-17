"""参照の被覆（is-a の手がかり）の法則。

- 読み手（CoverageReader、今は PostgreSQL）：covered は参照されている target の行の数（参照する側の NULL は数えない）、
  値ごとの数の和は全体と合う（Coverage が検査する）、値の種類が上限を超える列だけ None。AdventureWorks の実データ
- 主張（coverage_claims）：covers_all はすべての行が参照されているときだけ。discriminated_by は、値ごとに全部か無しかに
  きれいに分かれ、参照側に NULL が無く、値の種類が行に比べて十分少ない列だけ。そういう列には必ず出る
- 共有主キー（shared_keys）：主キーの各列が target の主キーの同じ位置の列に結ばれている参照だけ（並びは問わない）
"""

from dataclasses import replace

import psycopg
import pytest
from hypothesis import given
from hypothesis import strategies as st
from psycopg import sql

from mandara.connectors.postgres.catalog_reader import PostgresCatalogReader
from mandara.connectors.postgres.coverage_reader import PostgresCoverageReader
from mandara.extractors import values
from mandara.om.model import ObjectKind, ReferentialAction, TypeFamily
from mandara.source.catalog import Catalog, Column, ForeignKey, Key, Scope, Table
from mandara.source.coverage import Coverage, ValueCoverage, value_text

CONNINFO = "dbname=adventureworks"
SCOPE = Scope(("humanresources", "person", "production", "purchasing", "sales"))
CATALOG = PostgresCatalogReader(CONNINFO).read(SCOPE)
READER = PostgresCoverageReader(CONNINFO)
TABLES = {(t.schema, t.name): t for t in CATALOG.tables}
FKS = [(t, fk) for t in CATALOG.tables for fk in t.foreign_keys if (fk.target_schema, fk.target_table) in TABLES]


@pytest.mark.parametrize(("table", "fk"), FKS, ids=[f"{t.qualified_name}:{fk.name}" for t, fk in FKS])
def test_covered_counts_the_referenced_target_rows(table: Table, fk: ForeignKey) -> None:
    counted = READER.coverage(table.schema, table.name, fk.columns, fk.target_schema, fk.target_table,
                              fk.target_columns, [], values.MAX_DISCRIMINATOR_VALUES)
    with psycopg.connect(CONNINFO) as conn:
        rows, distinct = conn.execute(sql.SQL(
            "select (select count(*) from {t}), (select count(*) from (select distinct {cols} from {c} "
            "where {not_null}) d)").format(
            t=sql.Identifier(fk.target_schema, fk.target_table), c=sql.Identifier(table.schema, table.name),
            cols=sql.SQL(", ").join(map(sql.Identifier, fk.columns)),
            not_null=sql.SQL(" and ").join(sql.SQL("{} is not null").format(sql.Identifier(c)) for c in fk.columns),
        )).fetchone() or (None, None)
    assert (counted.rows, counted.covered) == (rows, distinct)  # 外部キーなので、参照する値はすべて target にある


@pytest.mark.parametrize("shared", values.shared_keys(CATALOG), ids=lambda s: s.table.qualified_name)
def test_split_columns_are_none_exactly_when_they_have_too_many_values(shared: values.SharedKey) -> None:
    fk, limit = shared.foreign_key, 5
    counted = READER.coverage(shared.table.schema, shared.table.name, fk.columns, shared.target.schema,
                              shared.target.name, fk.target_columns, shared.split_by, limit)
    assert set(counted.split) == set(shared.split_by)
    with psycopg.connect(CONNINFO) as conn:
        for column, split in counted.split.items():
            kinds = (conn.execute(sql.SQL("select count(*) from (select distinct {c} from {t}) d").format(
                c=sql.Identifier(column), t=sql.Identifier(shared.target.schema, shared.target.name))).fetchone()
                     or (0,))[0]
            assert (split is None) == (kinds > limit), column


# ---------------------------------------------------------------- 主張（純粋な関数）

def _table(name: str, key: tuple[str, ...], *extra: Column) -> Table:
    columns = tuple(Column(c, i + 1, "integer", TypeFamily.integer, False, None, None, None) for i, c in enumerate(key))
    return Table("s", name, ObjectKind.table, None, columns + extra, Key(f"{name}_pk", key), (), (), ())


def _shared() -> values.SharedKey:
    kind = Column("kind", 2, "text", TypeFamily.text, False, None, None, None)
    target = _table("parent", ("id",), kind)
    fk = ForeignKey("fk", ("id",), "s", "parent", ("id",), ReferentialAction.no_action, ReferentialAction.no_action)
    return values.SharedKey(_table("child", ("id",)), fk, target)


column_values = st.sampled_from([st.text(max_size=3), st.integers(-3, 3), st.booleans()])
"""1つの列の値は1つの型（真偽と整数は Python では等しくなりうるので、同じ列に混ぜない）。"""


@st.composite
def coverages(draw: st.DrawFn) -> Coverage:
    """区分の列の候補 1 つ（kind）と、値の種類が多すぎる列 1 つ（name）。"""
    picked = draw(st.lists(st.one_of(st.none(), draw(column_values)), max_size=6, unique=True))
    groups: list[ValueCoverage] = []
    for value in picked:
        rows = draw(st.integers(1, 6))
        covered = draw(st.sampled_from([0, rows, draw(st.integers(0, rows))]))
        groups.append(ValueCoverage(value, rows, covered))
    rows, covered = sum(g.rows for g in groups), sum(g.covered for g in groups)
    return Coverage(rows, covered, {"kind": tuple(groups), "name": None})


@given(coverages())
def test_coverage_claims_follow_the_rules(coverage: Coverage) -> None:
    claims = values.coverage_claims("src", _shared(), coverage)
    predicates = [c.predicate for c in claims]
    assert ("covers_all" in predicates) == (coverage.rows > 0 and coverage.covered == coverage.rows)
    groups = coverage.split["kind"] or ()
    full = [g for g in groups if g.covered == g.rows]
    none = [g for g in groups if g.covered == 0]
    clean = (len(full) + len(none) == len(groups) and full and none and all(g.value is not None for g in full)
             and len(groups) * values.MIN_ROWS_PER_VALUE <= coverage.rows)
    found = [c for c in claims if c.predicate == "discriminated_by"]
    assert bool(found) == bool(clean)
    for c in found:
        assert c.discriminator == "kind"
        assert c.discriminator_values == sorted(value_text(g.value) for g in full if g.value is not None)
    assert not ("covers_all" in predicates and found)


def _fk(name: str, columns: tuple[str, ...], to: tuple[str, ...], target: str = "parent") -> ForeignKey:
    return ForeignKey(name, columns, "s", target, to, ReferentialAction.no_action, ReferentialAction.no_action)


def test_shared_keys_pair_the_primary_keys_in_any_order() -> None:
    def child(name: str, fk: ForeignKey) -> Table:
        return replace(_table(name, ("a", "b")), foreign_keys=(fk,))

    tables = (
        replace(_table("parent", ("a", "b")), foreign_keys=(_fk("self_fk", ("a", "b"), ("a", "b")),)),  # 自分自身
        child("same", _fk("fk", ("a", "b"), ("a", "b"))),
        child("swapped", _fk("fk", ("b", "a"), ("b", "a"))),  # 並びが違っても、同じ位置の列どうし
        child("crossed", _fk("fk", ("a", "b"), ("b", "a"))),  # a → b、b → a：主キーどうしで結ばれていない
        child("partial", _fk("fk", ("a",), ("a",))),
        child("outside", _fk("fk", ("a", "b"), ("a", "b"), "elsewhere")),  # 参照先が範囲外
    )
    found = values.shared_keys(Catalog(Scope(("s",)), tables))
    assert sorted(s.table.name for s in found) == ["same", "swapped"]
