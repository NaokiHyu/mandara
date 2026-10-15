"""PostgreSQL のカタログを読む（CatalogReader の実装）。

Adapter 層。pg_catalog の読み方、PostgreSQL の型、psycopg の例外はこのファイルの外に出さない。
- 1つの REPEATABLE READ READ ONLY トランザクションで読む（すべての問い合わせが同じスナップショットを見る）
- search_path を空にして読む。型名は常にスキーマで修飾され、接続ごとの設定に左右されない
- 範囲内に未対応の機能があれば、全部集めてから UnsupportedFeatureError にする。読み飛ばさない。
  未対応：パーティション、外部表、継承、型付き表、ルール、行レベルセキュリティ、identity 列、生成列、
  既定でない照合順序、CHECK を持つドメイン型、組み込みでない型（enum、複合、範囲、拡張の型）、
  排他制約、式・部分・NULLS NOT DISTINCT の一意、遅延可能・未検証の制約、MATCH FULL / PARTIAL の外部キー
- 接続情報は libpq の規約（conninfo 文字列、PGHOST などの環境変数）に従う
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, LiteralString

import psycopg
from psycopg import IsolationLevel

from mandara.om.model import ReferentialAction, TypeFamily
from mandara.source.catalog import (
    Catalog,
    Check,
    Column,
    ForeignKey,
    Key,
    ObjectKind,
    Scope,
    SourceError,
    Table,
    UnknownSchemaError,
    Unsupported,
    UnsupportedFeatureError,
)

_KINDS = {"r": ObjectKind.TABLE, "v": ObjectKind.VIEW, "m": ObjectKind.MATERIALIZED_VIEW}
_UNSUPPORTED_KINDS = {"p": "partitioned table", "f": "foreign table"}

# pg_catalog の組み込み型だけを引く。名前が同じでも別スキーマの型（ユーザーの enum など）は当てない
_FAMILIES: dict[str, TypeFamily] = {
    **dict.fromkeys(("int2", "int4", "int8"), TypeFamily.integer),
    **dict.fromkeys(("numeric", "money"), TypeFamily.decimal),
    **dict.fromkeys(("float4", "float8"), TypeFamily.float),
    **dict.fromkeys(("text", "varchar", "bpchar", "char", "name"), TypeFamily.text),
    "bool": TypeFamily.boolean,
    "date": TypeFamily.date,
    **dict.fromkeys(("time", "timetz"), TypeFamily.time),
    **dict.fromkeys(("timestamp", "timestamptz"), TypeFamily.timestamp),
    "bytea": TypeFamily.binary,
    "uuid": TypeFamily.uuid,
    "xml": TypeFamily.xml,
    **dict.fromkeys(("json", "jsonb"), TypeFamily.json),
}
_TYPE_KINDS = {"e": "enum type", "c": "composite type", "r": "range type", "m": "multirange type", "p": "pseudo type"}

_ACTIONS = {
    "a": ReferentialAction.no_action,
    "r": ReferentialAction.restrict,
    "c": ReferentialAction.cascade,
    "n": ReferentialAction.set_null,
    "d": ReferentialAction.set_default,
}
_MATCH = {"f": "MATCH FULL", "p": "MATCH PARTIAL"}


class PostgresCatalogReader:
    def __init__(self, conninfo: str = "") -> None:
        """conninfo が空なら、libpq の環境変数（PGHOST、PGDATABASE など）から接続する。"""
        self._conninfo = conninfo

    def read(self, scope: Scope) -> Catalog:
        try:
            with psycopg.connect(self._conninfo) as conn:
                conn.read_only = True
                conn.isolation_level = IsolationLevel.REPEATABLE_READ
                with conn.transaction():
                    # format_type などの出力が search_path で変わらないよう、型名を常に修飾させる
                    conn.execute("set local search_path = ''")
                    return _read(conn, scope)
        except psycopg.Error as e:
            raise SourceError(f"PostgreSQL のカタログを読めなかった: {e}", e) from e


# ---------------------------------------------------------------- 読み


def _read(conn: psycopg.Connection[Any], scope: Scope) -> Catalog:
    schemas = sorted(set(scope.schemas))
    found = {r[0] for r in _rows(conn, "select nspname from pg_namespace where nspname = any(%s)", schemas)}
    if missing := [s for s in schemas if s not in found]:
        raise UnknownSchemaError(missing)

    unsupported: list[Unsupported] = []
    relations: dict[int, tuple[str, str, ObjectKind, str | None]] = {}
    for oid, schema, name, relkind, comment, row_security, typed, rules in _rows(conn, _RELATIONS, schemas):
        loc = f"{schema}.{name}"
        if relkind in _UNSUPPORTED_KINDS:
            unsupported.append(Unsupported(_UNSUPPORTED_KINDS[relkind], loc))
            continue
        relations[oid] = (schema, name, _KINDS[relkind], comment)
        if row_security:
            unsupported.append(Unsupported("row level security", loc))
        if typed:
            unsupported.append(Unsupported("typed table", loc))
        unsupported.extend(Unsupported(f"rule ({rule})", loc) for rule in rules)
    oids = list(relations)

    def where(oid: int) -> str:
        schema, name, _, _ = relations[oid]
        return f"{schema}.{name}"

    for child, parent in _rows(conn, _INHERITANCE, oids):
        unsupported.append(Unsupported(f"inheritance (parent {parent})", child))

    types = _TypeResolver(_rows(conn, _TYPES, oids))
    columns: dict[int, list[Column]] = defaultdict(list)
    for oid, name, position, source_type, type_oid, notnull, default, comment, identity, generated, collated in _rows(
        conn, _COLUMNS, oids
    ):
        loc = f"{where(oid)}.{name}"
        if identity:
            unsupported.append(Unsupported("identity column", loc))
        if generated:
            unsupported.append(Unsupported("generated column", loc))
        if collated:
            unsupported.append(Unsupported(f"collation ({collated})", loc))
        resolved = types.resolve(type_oid)
        unsupported.extend(Unsupported(problem, loc) for problem in resolved.unsupported)
        is_table = relations[oid][2] is ObjectKind.TABLE
        columns[oid].append(
            Column(
                name=name,
                position=position,
                source_type=resolved.base_source_type or source_type,
                family=resolved.family,
                # ビュー・マテリアライズドビューの列は NULL 可否を宣言しない（attnotnull は常に false）
                nullable=not (notnull or resolved.notnull) if is_table else None,
                default=default,
                comment=comment,
                type_alias=resolved.alias,
            )
        )

    primary: dict[int, Key] = {}
    uniques: dict[int, list[Key]] = defaultdict(list)
    foreign: dict[int, list[ForeignKey]] = defaultdict(list)
    checks: dict[int, list[Check]] = defaultdict(list)
    for row in _rows(conn, _CONSTRAINTS, oids):
        (oid, name, contype, cols, target_schema, target_table, target_cols, expression,
         on_update, on_delete, match, deferrable, validated, nulls_not_distinct) = row
        loc = where(oid)
        if deferrable:
            unsupported.append(Unsupported(f"deferrable constraint ({name})", loc))
        if not validated:
            unsupported.append(Unsupported(f"not valid constraint ({name})", loc))
        if nulls_not_distinct:
            unsupported.append(Unsupported(f"nulls not distinct ({name})", loc))
        match contype:
            case "p":
                primary[oid] = Key(name, tuple(cols))
            case "u":
                uniques[oid].append(Key(name, tuple(cols)))
            case "f":
                if match in _MATCH:
                    unsupported.append(Unsupported(f"{_MATCH[match]} foreign key ({name})", loc))
                foreign[oid].append(ForeignKey(
                    name, tuple(cols), target_schema, target_table, tuple(target_cols),
                    on_delete=_ACTIONS[on_delete], on_update=_ACTIONS[on_update],
                ))
            case "c":
                checks[oid].append(Check(name, expression))
            case _:
                unsupported.append(Unsupported(f"constraint type {contype!r} ({name})", loc))

    for oid, name, cols, has_expressions, has_predicate, nulls_not_distinct in _rows(conn, _UNIQUE_INDEXES, oids):
        loc = where(oid)
        if has_expressions:
            unsupported.append(Unsupported(f"expression unique index ({name})", loc))
        if has_predicate:
            unsupported.append(Unsupported(f"partial unique index ({name})", loc))
        if nulls_not_distinct:
            unsupported.append(Unsupported(f"nulls not distinct ({name})", loc))
        if not (has_expressions or has_predicate):
            uniques[oid].append(Key(name, tuple(cols)))

    if unsupported:
        raise UnsupportedFeatureError(sorted(unsupported, key=lambda u: (u.location, u.feature)))

    tables = [
        Table(
            schema=schema,
            name=name,
            kind=kind,
            comment=comment,
            columns=tuple(sorted(columns[oid], key=lambda c: c.position)),
            primary_key=primary.get(oid),
            uniques=tuple(sorted(uniques[oid], key=lambda k: k.name)),
            foreign_keys=tuple(sorted(foreign[oid], key=lambda f: f.name)),
            checks=tuple(sorted(checks[oid], key=lambda c: c.name)),
        )
        for oid, (schema, name, kind, comment) in relations.items()
    ]
    return Catalog(scope=Scope(tuple(schemas)), tables=tuple(sorted(tables, key=lambda t: (t.schema, t.name))))


def _rows(conn: psycopg.Connection[Any], query: LiteralString, param: object) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(query, (param,))
        return cur.fetchall()


# ---------------------------------------------------------------- 型


@dataclass(frozen=True)
class _ResolvedType:
    family: TypeFamily
    alias: str | None
    """最も外側のドメイン型の修飾名。"""
    base_source_type: str | None
    """ドメイン型なら、連なりの最も内側の基底の型（長さなどの修飾つき）。"""
    notnull: bool
    """ドメイン型の連なりのどこかが NOT NULL。"""
    unsupported: tuple[str, ...]


@dataclass(frozen=True)
class _TypeRow:
    qualified: str
    typtype: str
    base: int
    base_source_type: str | None
    name: str
    category: str
    element: int
    builtin: bool
    notnull: bool
    checks: int


class _TypeResolver:
    """列の型を、ドメイン型（入れ子もある）と配列の要素までたどって判定する。"""

    def __init__(self, rows: Iterable[tuple[Any, ...]]) -> None:
        self._types = {row[0]: _TypeRow(*row[1:]) for row in rows}

    def resolve(self, type_oid: int) -> _ResolvedType:
        alias: str | None = None
        base_source_type: str | None = None
        notnull = False
        unsupported: list[str] = []
        t = self._types[type_oid]
        while t.typtype == "d":
            alias = alias or t.qualified
            base_source_type = t.base_source_type
            notnull = notnull or t.notnull
            if t.checks:
                unsupported.append(f"domain with CHECK ({t.qualified})")
            t = self._types[t.base]
        family, problems = self._base(t)
        return _ResolvedType(family, alias, base_source_type, notnull, (*unsupported, *problems))

    def _base(self, t: _TypeRow) -> tuple[TypeFamily, list[str]]:
        if t.category == "A" and t.element:
            _, problems = self._base(self._types[t.element])
            return TypeFamily.other, [f"array of {p}" for p in problems]
        if t.typtype in _TYPE_KINDS:
            return TypeFamily.other, [f"{_TYPE_KINDS[t.typtype]} ({t.qualified})"]
        if not t.builtin:
            return TypeFamily.other, [f"non-builtin type ({t.qualified})"]
        # 組み込みで型族に当てはまらないもの（interval、inet など）は OTHER。source_type で区別できる
        return _FAMILIES.get(t.name, TypeFamily.other), []


# ---------------------------------------------------------------- 問い合わせ

_RELATIONS: LiteralString = """
select c.oid, n.nspname, c.relname, c.relkind, obj_description(c.oid, 'pg_class'),
       c.relrowsecurity, c.reloftype <> 0,
       array(select r.rulename from pg_rewrite r where r.ev_class = c.oid and r.rulename <> '_RETURN'
             order by r.rulename)
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = any(%s) and c.relkind in ('r', 'v', 'm', 'p', 'f')
"""

# 範囲内の表が、継承の親か子になっているもの（パーティションも含む）
_INHERITANCE: LiteralString = """
with scope(oid) as (select unnest(%s::oid[]))
select i.inhrelid::regclass::text, i.inhparent::regclass::text from pg_inherits i
where i.inhrelid in (select oid from scope) or i.inhparent in (select oid from scope)
"""

# 照合順序は、型の既定と違うものだけを返す
_COLUMNS: LiteralString = """
select a.attrelid, a.attname, a.attnum, format_type(a.atttypid, a.atttypmod), a.atttypid, a.attnotnull,
       pg_get_expr(d.adbin, d.adrelid), col_description(a.attrelid, a.attnum),
       a.attidentity <> '', a.attgenerated <> '',
       case when a.attcollation <> t.typcollation then a.attcollation::regcollation::text end
from pg_attribute a
join pg_type t on t.oid = a.atttypid
left join pg_attrdef d on d.adrelid = a.attrelid and d.adnum = a.attnum
where a.attrelid = any(%s::oid[]) and a.attnum > 0 and not a.attisdropped
"""

# 列が使う型と、ドメイン型の基底・配列の要素をたどった先の型をすべて
_TYPES: LiteralString = """
with recursive used(oid) as (
    select distinct a.atttypid from pg_attribute a
    where a.attrelid = any(%s::oid[]) and a.attnum > 0 and not a.attisdropped
  union
    select case when t.typtype = 'd' then t.typbasetype else t.typelem end
    from pg_type t join used u on t.oid = u.oid
    where t.typtype = 'd' or (t.typcategory = 'A' and t.typelem <> 0)
)
select t.oid, n.nspname || '.' || t.typname, t.typtype, t.typbasetype,
       case when t.typtype = 'd' then format_type(t.typbasetype, t.typtypmod) end,
       t.typname, t.typcategory, t.typelem, n.nspname = 'pg_catalog', t.typnotnull,
       (select count(*) from pg_constraint c where c.contypid = t.oid and c.contype = 'c')
from pg_type t join used u on u.oid = t.oid join pg_namespace n on n.oid = t.typnamespace
"""

_CONSTRAINTS: LiteralString = """
select c.conrelid, c.conname, c.contype,
       array(select a.attname from unnest(c.conkey) with ordinality k(num, ord)
             join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.num order by k.ord),
       tn.nspname, tc.relname,
       array(select a.attname from unnest(c.confkey) with ordinality k(num, ord)
             join pg_attribute a on a.attrelid = c.confrelid and a.attnum = k.num order by k.ord),
       pg_get_expr(c.conbin, c.conrelid),
       c.confupdtype, c.confdeltype, c.confmatchtype, c.condeferrable, c.convalidated,
       coalesce(i.indnullsnotdistinct, false)
from pg_constraint c
left join pg_class tc on tc.oid = c.confrelid
left join pg_namespace tn on tn.oid = tc.relnamespace
left join pg_index i on i.indexrelid = c.conindid and c.contype in ('p', 'u')
where c.conrelid = any(%s::oid[]) and c.contype <> 'n'
"""

# 制約（主キー・一意・排他）の裏付けではない一意インデックス。
# 外部キーの conindid は参照先のインデックスを指すので、自分の表の主キー・一意・排他だけを除く
_UNIQUE_INDEXES: LiteralString = """
select i.indrelid, ic.relname,
       array(select a.attname from unnest(i.indkey::int2[]) with ordinality k(num, ord)
             join pg_attribute a on a.attrelid = i.indrelid and a.attnum = k.num order by k.ord),
       i.indexprs is not null, i.indpred is not null, i.indnullsnotdistinct
from pg_index i join pg_class ic on ic.oid = i.indexrelid
where i.indrelid = any(%s::oid[]) and i.indisunique
  and not exists (select 1 from pg_constraint c
                  where c.conindid = i.indexrelid and c.conrelid = i.indrelid and c.contype in ('p', 'u', 'x'))
"""
