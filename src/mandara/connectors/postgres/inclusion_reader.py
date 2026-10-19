"""PostgreSQL の値の包含を読む（InclusionReader の実装）。

Adapter 層。識別子は psycopg.sql で引用する。読み取り専用のトランザクションで読む。
包含は、列の異なる値を1回まとめ（group by）、キーごとに、キーの異なる値（select distinct）へ左外部結合して数える。
キーの値は異なるので、結合で行は増えない。結合はハッシュ結合などを選べ、メモリに収まらなくても動くので、キーに索引が
無くても（主キーの宣言の無い表、T2.7）値ごとにキーをなめない（相関する EXISTS は、索引が無いと値ごとに表をなめる）。
キーの NULL はどの値とも等しくならない（EXISTS と同じ数）。
1つの列について、複数のキーを1回の問い合わせで数える（列を1回なめる）。
"""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

import psycopg
from psycopg import sql

from mandara.source.catalog import SourceError
from mandara.source.inclusion import ColumnProfile, Inclusion, KeyRef, Ordered


class PostgresInclusionReader:
    def __init__(self, conninfo: str = "") -> None:
        self._conninfo = conninfo

    def profile(self, schema: str, table: str, column: str, ordered: bool) -> ColumnProfile:
        c = sql.Identifier(column)
        bounds = sql.SQL(", min({c}), max({c})").format(c=c) if ordered else sql.SQL(", null, null")
        query = sql.SQL("select count({c}), count(distinct {c}){bounds} from {t}").format(
            c=c, bounds=bounds, t=sql.Identifier(schema, table))
        row = self._one(query, f"{schema}.{table}.{column} の様子")
        return ColumnProfile(_count(row[0]), _count(row[1]), _ordered(row[2], column), _ordered(row[3], column))

    def inclusion(self, schema: str, table: str, column: str, keys: Sequence[KeyRef], spans: bool) -> Inclusion:
        if not keys:
            raise ValueError("包含を数えるキーが要る")
        matched, joined = _matched(keys)
        key_values = sql.SQL(", ").join(
            sql.SQL("(select count(distinct k.{kc}) from {t} k)").format(
                t=sql.Identifier(k.schema, k.table), kc=sql.Identifier(k.column))
            for k in keys)
        # 範囲の幅は numeric で引く（bigint の端どうしの差があふれないように）。値が無ければ null
        in_span = sql.SQL("").join(
            sql.SQL(", (select count(distinct k.{kc}) from {t} k where k.{kc} between (select min(value) from v) "
                    "and (select max(value) from v))").format(t=sql.Identifier(k.schema, k.table),
                                                             kc=sql.Identifier(k.column))
            for k in keys) if spans else sql.SQL("")
        span = sql.SQL("max(value)::numeric - min(value)::numeric + 1") if spans else sql.SQL("null")
        query = sql.SQL("with v as (select {c} as value, count(*) as n from {t} where {c} is not null group by {c}) "
                        "select count(*), coalesce(max(n), 0), {span}, {matched}, {key_values}{in_span} from v{joined}"
                        ).format(c=sql.Identifier(column), t=sql.Identifier(schema, table), matched=matched,
                                 key_values=key_values, span=span, in_span=in_span, joined=joined)
        row = self._one(query, f"{schema}.{table}.{column} の包含")
        n = len(keys)
        counts = [_count(x) for x in row[3:]]
        width = _span(row[2])
        return Inclusion(_count(row[0]), _count(row[1]), tuple(counts[:n]), tuple(counts[n:2 * n]), width,
                         tuple(counts[2 * n:]) if width is not None else None)

    def probe(self, schema: str, table: str, column: str, keys: Sequence[KeyRef], sample: int) -> tuple[int, ...]:
        if not keys or sample < 1:
            raise ValueError(f"見本の重なりにはキーと 1 以上の見本の数が要る: keys={len(keys)}, sample={sample}")
        matched, joined = _matched(keys)
        query = sql.SQL("with v as (select distinct {c} as value from {t} where {c} is not null order by 1 limit {n}) "
                        "select {matched} from v{joined}").format(
            c=sql.Identifier(column), t=sql.Identifier(schema, table), n=sql.Literal(sample), matched=matched,
            joined=joined)
        return tuple(_count(x) for x in self._one(query, f"{schema}.{table}.{column} の見本の重なり"))

    def _one(self, query: sql.Composed, what: str) -> tuple[object, ...]:
        try:
            with psycopg.connect(self._conninfo) as conn:
                conn.read_only = True
                with conn.transaction():
                    row = conn.execute(query).fetchone()
        except psycopg.Error as e:
            raise SourceError(f"PostgreSQL から{what}を読めなかった: {e}", e) from e
        if row is None:
            raise AssertionError(f"集計の問い合わせが行を返さない（{what}）")
        return tuple(row)


def _matched(keys: Sequence[KeyRef]) -> tuple[sql.Composed, sql.Composed]:
    """キーごとの、v の値のうちキーにあるものの数（select の式）と、キーの異なる値への左外部結合（from に続ける句）。"""
    aliases = [sql.Identifier(f"k{i}") for i in range(len(keys))]
    matched = sql.SQL(", ").join(sql.SQL("count({a}.key)").format(a=a) for a in aliases)
    joined = sql.SQL("").join(
        sql.SQL(" left join (select distinct {kc} as key from {t}) {a} on {a}.key = v.value").format(
            kc=sql.Identifier(k.column), t=sql.Identifier(k.schema, k.table), a=a)
        for k, a in zip(keys, aliases, strict=True))
    return matched, joined


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssertionError(f"数が整数ではない（読み手の誤り）: {value!r}")
    return value


def _span(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, Decimal) or value != value.to_integral_value():
        raise AssertionError(f"範囲の幅が整数の numeric ではない（読み手の誤り。整数でない列に幅を求めた？）: {value!r}")
    return int(value)


def _ordered(value: object, column: str) -> Ordered | None:
    if value is None or isinstance(value, int | Decimal | float | date | datetime):
        return value
    raise AssertionError(f"列 {column} の最小・最大が、順序に意味のある型ではない（読み手の誤り）: {type(value).__name__}")
