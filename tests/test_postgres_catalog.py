"""PostgreSQL のカタログコネクタ。

- 契約（tests/catalog_laws.py）を当てる
- 往復の法則：Hypothesis で作ったスキーマを DDL で作り、読み戻すと書いたとおりの構造になる
- 未対応の機能は、全部まとめて明示的なエラーになる
- AdventureWorks の実数と一致する

実 DB に対するテスト。テスト用のデータベースを作って消す。PostgreSQL に繋がらなければ失敗する（スキップしない）。
接続は libpq の環境変数（PGHOST など）に従う。
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import LiteralString

import psycopg
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from psycopg import sql

from mandara.connectors.postgres.catalog_reader import PostgresCatalogReader
from mandara.source.catalog import (
    Catalog,
    ObjectKind,
    ReferentialAction,
    Scope,
    SourceError,
    TypeFamily,
    UnknownSchemaError,
    UnsupportedFeatureError,
)

from tests import catalog_laws

TEST_DB = f"mandara_test_{os.getpid()}"


@pytest.fixture(scope="module")
def db() -> Iterator[str]:
    """テスト用のデータベース。conninfo を返す。"""
    with psycopg.connect("dbname=postgres", autocommit=True) as admin:
        admin.execute(sql.SQL("create database {}").format(sql.Identifier(TEST_DB)))
    try:
        yield f"dbname={TEST_DB}"
    finally:
        with psycopg.connect("dbname=postgres", autocommit=True) as admin:
            admin.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(TEST_DB)))


def _execute(conninfo: str, *statements: sql.Composed) -> None:
    with psycopg.connect(conninfo, autocommit=True) as conn:
        for statement in statements:
            conn.execute(statement)


# ---------------------------------------------------------------- 往復の法則


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    ddl_type: sql.Composable
    source_type: str
    family: TypeFamily
    declared_nullable: bool
    domain: str | None
    """この列が使うドメイン型の名前（スキーマ内）。ドメインは NOT NULL。"""
    comment: str | None


@dataclass(frozen=True)
class ForeignKeySpec:
    name: str
    columns: tuple[str, ...]
    target: str
    target_columns: tuple[str, ...]
    on_delete: ReferentialAction
    on_update: ReferentialAction


@dataclass(frozen=True)
class TableSpec:
    name: str
    comment: str | None
    columns: tuple[ColumnSpec, ...]
    primary_key: tuple[str, ...]
    unique: tuple[str, ...] | None
    foreign_keys: tuple[ForeignKeySpec, ...]
    check_column: str | None


DOMAIN = "d_label"
DOMAIN_BASE: LiteralString = "character varying(30)"
_ACTIONS = tuple(ReferentialAction)
_ACTION_SQL: dict[ReferentialAction, LiteralString] = {
    ReferentialAction.NO_ACTION: "no action",
    ReferentialAction.RESTRICT: "restrict",
    ReferentialAction.CASCADE: "cascade",
    ReferentialAction.SET_NULL: "set null",
    ReferentialAction.SET_DEFAULT: "set default",
}
VIEW = "V first"
"""生成する識別子（小文字始まり、または固定の一覧）と重ならない名前。制約・FK 列も大文字で始める。"""
_TYPES: list[tuple[LiteralString, TypeFamily]] = [
    ("integer", TypeFamily.INTEGER),
    ("bigint", TypeFamily.INTEGER),
    ("numeric(10,2)", TypeFamily.DECIMAL),
    ("double precision", TypeFamily.FLOAT),
    ("character varying(20)", TypeFamily.TEXT),
    ("text", TypeFamily.TEXT),
    ("boolean", TypeFamily.BOOLEAN),
    ("date", TypeFamily.DATE),
    ("timestamp without time zone", TypeFamily.TIMESTAMP),
    ("bytea", TypeFamily.BINARY),
    ("uuid", TypeFamily.UUID),
    ("xml", TypeFamily.XML),
    ("jsonb", TypeFamily.JSON),
    ("interval", TypeFamily.OTHER),
    ("integer[]", TypeFamily.OTHER),
]

# 引用符が要る名前（大文字、空白、予約語、日本語）を混ぜる。PostgreSQL の識別子は 63 バイトまで
identifiers = st.one_of(
    st.sampled_from(("select", "order", "user", "Mixed Case", "名前", "a\"b", "x y")),
    st.from_regex(r"[a-z][a-z0-9_]{0,10}", fullmatch=True),
)
comments = st.none() | st.text(st.characters(codec="utf-8", exclude_categories=("Cs", "Cc")), min_size=1, max_size=20)


@st.composite
def column_specs(draw: st.DrawFn, name: str) -> ColumnSpec:
    if draw(st.integers(0, len(_TYPES))) == len(_TYPES):
        return ColumnSpec(name, sql.Identifier(DOMAIN), DOMAIN, TypeFamily.TEXT, True, DOMAIN, draw(comments))
    source_type, family = draw(st.sampled_from(_TYPES))
    return ColumnSpec(name, sql.SQL(source_type), source_type, family, draw(st.booleans()), None, draw(comments))


def _keyable(column: ColumnSpec) -> bool:
    """btree の一意インデックスを張れる型（主キー・一意にできる）。"""
    return column.family not in (TypeFamily.XML, TypeFamily.JSON) and column.source_type != "integer[]"


@st.composite
def schemas(draw: st.DrawFn) -> tuple[TableSpec, ...]:
    table_names = draw(st.lists(identifiers, min_size=1, max_size=4, unique=True))
    tables: list[TableSpec] = []
    for i, table_name in enumerate(table_names):
        names = draw(st.lists(identifiers, min_size=1, max_size=5, unique=True))
        columns = [draw(column_specs(n)) for n in names]
        keyable = [c.name for c in columns if _keyable(c)]
        primary_key = tuple(keyable[: draw(st.integers(0, min(2, len(keyable))))])

        foreign_keys: list[ForeignKeySpec] = []
        targets = [t for t in tables if t.primary_key]
        for k, target in enumerate(draw(st.lists(st.sampled_from(targets), max_size=2)) if targets else []):
            fk_columns: list[str] = []
            for j, target_column in enumerate(target.primary_key):
                spec = next(c for c in target.columns if c.name == target_column)
                fk_name = f"FK{k}_{j}"
                columns.append(ColumnSpec(fk_name, spec.ddl_type, spec.source_type, spec.family, True, spec.domain, None))
                fk_columns.append(fk_name)
            on_delete, on_update = draw(st.sampled_from(_ACTIONS)), draw(st.sampled_from(_ACTIONS))
            foreign_keys.append(ForeignKeySpec(f"FK_{i}_{k}", tuple(fk_columns), target.name, target.primary_key,
                                               on_delete, on_update))

        unique_candidates = [c.name for c in columns if _keyable(c) and c.name not in primary_key]
        unique = (draw(st.sampled_from(unique_candidates)),) if unique_candidates and draw(st.booleans()) else None
        integers = [c.name for c in columns if c.source_type == "integer"]
        check_column = draw(st.sampled_from(integers)) if integers and draw(st.booleans()) else None
        tables.append(TableSpec(table_name, draw(comments), tuple(columns), primary_key, unique,
                                tuple(foreign_keys), check_column))
    return tuple(tables)


def _ddl(schema: str, tables: tuple[TableSpec, ...]) -> list[sql.Composed]:
    s = sql.Identifier(schema)
    out: list[sql.Composed] = [
        sql.SQL("create schema {}").format(s),
        sql.SQL("create domain {}.{} as {} not null").format(s, sql.Identifier(DOMAIN), sql.SQL(DOMAIN_BASE)),
    ]
    for i, t in enumerate(tables):
        q = sql.SQL("{}.{}").format(s, sql.Identifier(t.name))
        parts: list[sql.Composable] = [
            sql.SQL("{} {}{}").format(
                sql.Identifier(c.name),
                sql.SQL("{}.{}").format(s, c.ddl_type) if c.domain else c.ddl_type,
                sql.SQL("") if c.declared_nullable else sql.SQL(" not null"),
            )
            for c in t.columns
        ]
        if t.primary_key:
            parts.append(sql.SQL("constraint {} primary key ({})").format(
                sql.Identifier(f"PK_{i}"), sql.SQL(", ").join(map(sql.Identifier, t.primary_key))))
        if t.unique:
            parts.append(sql.SQL("constraint {} unique ({})").format(
                sql.Identifier(f"UQ_{i}"), sql.SQL(", ").join(map(sql.Identifier, t.unique))))
        for fk in t.foreign_keys:
            parts.append(sql.SQL("constraint {} foreign key ({}) references {}.{} ({}) on delete {} on update {}").format(
                sql.Identifier(fk.name), sql.SQL(", ").join(map(sql.Identifier, fk.columns)),
                s, sql.Identifier(fk.target), sql.SQL(", ").join(map(sql.Identifier, fk.target_columns)),
                sql.SQL(_ACTION_SQL[fk.on_delete]), sql.SQL(_ACTION_SQL[fk.on_update])))
        if t.check_column:
            parts.append(sql.SQL("constraint {} check ({} >= 0)").format(
                sql.Identifier(f"CK_{i}"), sql.Identifier(t.check_column)))
        out.append(sql.SQL("create table {} ({})").format(q, sql.SQL(", ").join(parts)))
        if t.comment is not None:
            out.append(sql.SQL("comment on table {} is {}").format(q, sql.Literal(t.comment)))
        for c in t.columns:
            if c.comment is not None:
                out.append(sql.SQL("comment on column {}.{} is {}").format(q, sql.Identifier(c.name), sql.Literal(c.comment)))
    out.append(sql.SQL("create view {}.{} as select * from {}.{}").format(
        s, sql.Identifier(VIEW), s, sql.Identifier(tables[0].name)))
    return out


def _quote_ident(conninfo: str, name: str) -> str:
    """PostgreSQL が式の中で識別子を書くときの形。"""
    with psycopg.connect(conninfo) as conn:
        row = conn.execute("select quote_ident(%s)", (name,)).fetchone()
    assert row is not None
    return row[0]


def _assert_matches(conninfo: str, catalog: Catalog, schema: str, tables: tuple[TableSpec, ...]) -> None:
    by_name = {t.name: t for t in catalog.tables}
    assert set(by_name) == {t.name for t in tables} | {VIEW}
    for spec in tables:
        table = by_name[spec.name]
        assert table.kind is ObjectKind.TABLE
        assert table.comment == spec.comment
        expected = [
            (c.name, c.source_type if c.domain is None else DOMAIN_BASE, c.family,
             # 主キーの列と NOT NULL のドメイン型の列は、宣言に関わらず NULL 不可
             c.declared_nullable and c.domain is None and c.name not in spec.primary_key, c.comment, f"{schema}.{c.domain}" if c.domain else None)
            for c in spec.columns
        ]
        actual = [(c.name, c.source_type, c.family, c.nullable, c.comment, c.type_alias) for c in table.columns]
        assert actual == expected
        assert (table.primary_key.columns if table.primary_key else ()) == spec.primary_key
        assert [k.columns for k in table.uniques] == ([spec.unique] if spec.unique else [])
        assert [(f.name, f.columns, f.target_schema, f.target_table, f.target_columns, f.on_delete, f.on_update)
                for f in table.foreign_keys] == [
            (f.name, f.columns, schema, f.target, f.target_columns, f.on_delete, f.on_update) for f in spec.foreign_keys
        ]
        expected_checks = (
            [(f"CK_{tables.index(spec)}", f"({_quote_ident(conninfo, spec.check_column)} >= 0)")]
            if spec.check_column else []
        )
        assert [(c.name, c.expression) for c in table.checks] == expected_checks
    view = by_name[VIEW]
    assert view.kind is ObjectKind.VIEW
    assert [c.nullable for c in view.columns] == [None] * len(tables[0].columns)
    first = by_name[tables[0].name]
    assert [(c.name, c.source_type, c.family, c.type_alias) for c in view.columns] == [
        (c.name, c.source_type, c.family, c.type_alias) for c in first.columns
    ]


_counter = iter(range(1_000_000))


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(tables=schemas())
def test_written_structure_is_read_back(db: str, tables: tuple[TableSpec, ...]) -> None:
    schema = f"s{next(_counter)}"
    _execute(db, *_ddl(schema, tables))
    try:
        reader = PostgresCatalogReader(db)
        catalog = reader.read(Scope((schema,)))
        _assert_matches(db, catalog, schema, tables)
        assert catalog_laws.all_laws(catalog) == []
        assert catalog_laws.deterministic(reader, Scope((schema,)))
    finally:
        _execute(db, sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))


# ---------------------------------------------------------------- 失敗の表し方


def test_unknown_schema_is_an_error(db: str) -> None:
    with pytest.raises(UnknownSchemaError) as e:
        PostgresCatalogReader(db).read(Scope(("public", "no_such_schema")))
    assert e.value.schemas == ("no_such_schema",)


def test_connection_failure_keeps_cause() -> None:
    with pytest.raises(SourceError) as e:
        PostgresCatalogReader("host=127.0.0.1 port=1 connect_timeout=2").read(Scope(("public",)))
    assert isinstance(e.value.cause, psycopg.Error)


UNSUPPORTED_DDL: LiteralString = """
create schema u;
create table u.parted (k int) partition by range (k);
create table u.parent (k int);
create table u.child () inherits (u.parent);
create table u.ident (k int generated always as identity, g int generated always as (k * 2) stored);
create domain u.positive as int check (value > 0);
create table u.checked (v u.positive);
create table u.idx (a text, b int);
create unique index idx_expr on u.idx (lower(a));
create unique index idx_partial on u.idx (b) where b > 0;
create table u.excl (r int4range, exclude using gist (r with &&));
create type u.mood as enum ('happy');
create type u.pair as (a int, b int);
create type u.span as range (subtype = int4);
create table u.typed_cols (m u.mood, p u.pair, s u.span, ms u.mood[], r int4range);
create table u.ruled (k int);
create rule ruled_ignore as on insert to u.ruled do instead nothing;
create table u.secured (k int);
alter table u.secured enable row level security;
create table u.of_type of u.pair;
create table u.collated (t text collate "C");
create table u.target (k int primary key);
create table u.cons (
  k int constraint cons_deferrable unique deferrable,
  n int constraint cons_nnd unique nulls not distinct,
  f int,
  constraint cons_match foreign key (f) references u.target (k) match full
);
alter table u.cons add constraint cons_not_valid check (k > 0) not valid;
"""


def test_unsupported_features_are_all_reported(db: str) -> None:
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(UNSUPPORTED_DDL)
    try:
        with pytest.raises(UnsupportedFeatureError) as e:
            PostgresCatalogReader(db).read(Scope(("u",)))
        found = {(u.location, u.feature.split(" (")[0]) for u in e.value.found}
        assert found == {
            ("u.parted", "partitioned table"),
            ("u.child", "inheritance"),
            ("u.ident.k", "identity column"),
            ("u.ident.g", "generated column"),
            ("u.checked.v", "domain with CHECK"),
            ("u.idx", "expression unique index"),
            ("u.idx", "partial unique index"),
            ("u.excl", "constraint type 'x'"),
            ("u.excl.r", "range type"),
            ("u.typed_cols.m", "enum type"),
            ("u.typed_cols.p", "composite type"),
            ("u.typed_cols.s", "range type"),
            ("u.typed_cols.ms", "array of enum type"),
            ("u.typed_cols.r", "range type"),
            ("u.ruled", "rule"),
            ("u.secured", "row level security"),
            ("u.of_type", "typed table"),
            ("u.collated.t", "collation"),
            ("u.cons", "deferrable constraint"),
            ("u.cons", "nulls not distinct"),
            ("u.cons", "MATCH FULL foreign key"),
            ("u.cons", "not valid constraint"),
        }
    finally:
        _execute(db, sql.SQL("drop schema {} cascade").format(sql.Identifier("u")))


REGRESSION_DDL: LiteralString = """
create schema r;
create type r.date as enum ('x');
create table r.b (x int, d r.date);
create unique index b_x on r.b (x);
create table r.a (x int references r.b (x));
"""


def test_review_regressions(db: str) -> None:
    """レビューで見つかった、黙って構造を失う経路。"""
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(REGRESSION_DDL)
    try:
        with pytest.raises(UnsupportedFeatureError) as e:
            PostgresCatalogReader(db).read(Scope(("r",)))
        # 名前が date でも、組み込みの date ではない
        assert [(u.location, u.feature) for u in e.value.found] == [("r.b.d", "enum type (r.date)")]
        _execute(db, sql.SQL("alter table {}.{} drop column d").format(sql.Identifier("r"), sql.Identifier("b")))
        catalog = PostgresCatalogReader(db).read(Scope(("r",)))
        b = catalog.table("r", "b")
        # 外部キーから参照されている、制約でない一意インデックスも一意として読む
        assert b is not None and [k.columns for k in b.uniques] == [("x",)]
    finally:
        _execute(db, sql.SQL("drop schema {} cascade").format(sql.Identifier("r")))


def test_scope_order_does_not_matter(db: str) -> None:
    reader = PostgresCatalogReader(db)
    assert reader.read(Scope(("public", "pg_toast"))) == reader.read(Scope(("pg_toast", "public", "public")))


# ---------------------------------------------------------------- AdventureWorks

AW_SCOPE = Scope(("humanresources", "person", "production", "purchasing", "sales"))


@pytest.fixture(scope="module")
def adventureworks() -> Catalog:
    return PostgresCatalogReader("dbname=adventureworks").read(AW_SCOPE)


def test_adventureworks_counts(adventureworks: Catalog) -> None:
    """仕様の調査（docs/tasks/T1.1）で数えた値と一致する。"""
    tables = [t for t in adventureworks.tables if t.kind is ObjectKind.TABLE]
    assert len(tables) == 68
    assert sum(t.kind is ObjectKind.VIEW for t in adventureworks.tables) == 19
    assert sum(t.kind is ObjectKind.MATERIALIZED_VIEW for t in adventureworks.tables) == 2
    assert sum(t.primary_key is not None for t in tables) == 68
    assert sum(len(t.foreign_keys) for t in tables) == 90
    assert sum(len(t.checks) for t in tables) == 88
    aliased = [t.kind for t in adventureworks.tables for c in t.columns if c.type_alias is not None]
    assert (aliased.count(ObjectKind.TABLE), aliased.count(ObjectKind.VIEW),
            aliased.count(ObjectKind.MATERIALIZED_VIEW)) == (43, 63, 5)
    cascades = [(t.qualified_name, f.name) for t in tables for f in t.foreign_keys
                if f.on_delete is ReferentialAction.CASCADE]
    assert len(cascades) == 2


def test_adventureworks_obeys_contract(adventureworks: Catalog) -> None:
    assert catalog_laws.all_laws(adventureworks) == []
    assert catalog_laws.deterministic(PostgresCatalogReader("dbname=adventureworks"), AW_SCOPE)


def test_adventureworks_details(adventureworks: Catalog) -> None:
    """正解 OM を書くときに確かめた事実が、カタログから読める。"""
    detail = adventureworks.table("sales", "salesorderdetail")
    assert detail is not None and detail.primary_key is not None
    assert detail.primary_key.columns == ("salesorderid", "salesorderdetailid")
    # 製品への直接の FK は無く、特売製品への複合 FK だけ
    assert {(f.columns, f.target_table) for f in detail.foreign_keys} >= {
        (("specialofferid", "productid"), "specialofferproduct")
    }
    assert not any(f.target_table == "product" for f in detail.foreign_keys)

    person = adventureworks.table("person", "person")
    assert person is not None
    firstname = person.column("firstname")
    assert firstname is not None and firstname.type_alias == "public.Name" and firstname.family is TypeFamily.TEXT
    assert firstname.source_type == "character varying(50)"

    address = adventureworks.table("person", "address")
    assert address is not None
    location = address.column("spatiallocation")
    assert location is not None and location.family is TypeFamily.BINARY

    matview = adventureworks.table("person", "vstateprovincecountryregion")
    assert matview is not None and matview.kind is ObjectKind.MATERIALIZED_VIEW
    assert [k.columns for k in matview.uniques] == [("stateprovinceid", "countryregioncode")]
