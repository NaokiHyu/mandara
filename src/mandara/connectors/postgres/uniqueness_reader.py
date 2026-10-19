"""PostgreSQL の列の組の一意を確かめる（UniquenessReader の実装）。

Adapter 層。識別子は psycopg.sql で引用する。読み取り専用のトランザクションで、数と見本を同じ読みで取る。
見本は、行の文字列の md5 の順に並べた先頭（決定的で、表の物理的な順や値の順に偏らない）。md5 が同じなら行の文字列の順。
"""

from collections.abc import Sequence

import psycopg
from psycopg import sql

from mandara.source.catalog import SourceError
from mandara.source.uniqueness import Distinct, RowSample


class PostgresUniquenessReader:
    def __init__(self, conninfo: str = "") -> None:
        self._conninfo = conninfo

    def rows(self, schema: str, table: str, columns: Sequence[str], limit: int) -> RowSample:
        if not columns or limit < 1:
            raise ValueError(f"列は 1 列以上、見本の数は 1 以上: {list(columns)}, {limit}")
        t, cs = sql.Identifier(schema, table), [sql.Identifier(c) for c in columns]
        counts = sql.SQL("select count(*), {nulls} from {t}").format(
            nulls=sql.SQL(", ").join(sql.SQL("count(*) - count({})").format(c) for c in cs), t=t)
        row_text = sql.SQL("row({})::text").format(sql.SQL(", ").join(cs))
        values = sql.SQL("select {cs} from {t} order by md5({r}), {r} limit {n}").format(
            cs=sql.SQL(", ").join(cs), t=t, r=row_text, n=sql.Literal(limit))
        where = f"{schema}.{table}"
        try:
            with psycopg.connect(self._conninfo) as conn:
                conn.read_only = True
                conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ  # 数と見本を同じスナップショットで
                with conn.transaction():
                    row = conn.execute(counts).fetchone()
                    found = conn.execute(values).fetchall()
        except psycopg.Error as e:
            raise SourceError(f"PostgreSQL から {where} の行の見本を読めなかった: {e}", e) from e
        if row is None:
            raise AssertionError(f"集計の問い合わせが行を返さない（{where}）")
        return RowSample(_count(row[0]), tuple(_count(n) for n in row[1:]), tuple(tuple(r) for r in found))

    def distinct(self, schema: str, table: str, columns: Sequence[str]) -> Distinct:
        if not columns:
            raise ValueError("列は 1 列以上")
        cs = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
        query = sql.SQL("select (select count(*) from {t}), count(*) from (select distinct {cs} from {t} where {nn}) d").format(
            cs=cs, t=sql.Identifier(schema, table),
            nn=sql.SQL(" and ").join(sql.SQL("{} is not null").format(sql.Identifier(c)) for c in columns))
        where = f"{schema}.{table}({', '.join(columns)})"
        try:
            with psycopg.connect(self._conninfo) as conn:
                conn.read_only = True
                row = conn.execute(query).fetchone()
        except psycopg.Error as e:
            raise SourceError(f"PostgreSQL から {where} の異なる値の組を数えられなかった: {e}", e) from e
        if row is None:
            raise AssertionError(f"集計の問い合わせが行を返さない（{where}）")
        return Distinct(_count(row[0]), _count(row[1]))


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssertionError(f"数が整数ではない（読み手の誤り）: {value!r}")
    return value
