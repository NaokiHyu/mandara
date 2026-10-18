"""PostgreSQL の列の値の見本を読む（ValueSampler の実装）。

Adapter 層。識別子は psycopg.sql で引用する。読み取り専用のトランザクションで、数と見本を同じ読みで取る。
見本は、異なる値を値の文字列の md5 の順に並べた先頭（決定的で、値の順に偏らない）。md5 が同じなら文字列の順。
"""

import psycopg
from psycopg import sql

from mandara.source.catalog import SourceError
from mandara.source.samples import ValueSample


class PostgresSampleReader:
    def __init__(self, conninfo: str = "") -> None:
        self._conninfo = conninfo

    def sample(self, schema: str, table: str, column: str, limit: int) -> ValueSample:
        if limit < 1:
            raise ValueError(f"見本の数は 1 以上: {limit}")
        c, t = sql.Identifier(column), sql.Identifier(schema, table)
        counts = sql.SQL("select count({c}), count(distinct {c}) from {t}").format(c=c, t=t)
        values = sql.SQL("select value from (select distinct {c} as value from {t} where {c} is not null) d "
                         "order by md5(value::text), value::text limit {n}").format(c=c, t=t, n=sql.Literal(limit))
        where = f"{schema}.{table}.{column}"
        try:
            with psycopg.connect(self._conninfo) as conn:
                conn.read_only = True
                conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ  # 数と見本を同じスナップショットで
                with conn.transaction():
                    row = conn.execute(counts).fetchone()
                    rows = conn.execute(values).fetchall()
        except psycopg.Error as e:
            raise SourceError(f"PostgreSQL から {where} の値の見本を読めなかった: {e}", e) from e
        if row is None:
            raise AssertionError(f"集計の問い合わせが行を返さない（{where}）")
        return ValueSample(_count(row[0]), _count(row[1]), tuple(r[0] for r in rows))


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssertionError(f"数が整数ではない（読み手の誤り）: {value!r}")
    return value
