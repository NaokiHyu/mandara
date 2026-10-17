"""PostgreSQL の参照の被覆を読む（CoverageReader の実装）。

Adapter 層。識別子は psycopg.sql で引用する。読み取り専用のトランザクションで読む。
被覆は EXISTS（参照する行が1つ以上ある target の行）。区分の列ごとに target を1回なめる（索引は使わない。表が大きいと重い）。
全体と区分の列ごとの数は、1つのスナップショット（REPEATABLE READ）で数える（数が互いに食い違わない）。
値の種類は上限を1つ超えて読み、超えたら None。
"""

from collections.abc import Sequence

import psycopg
from psycopg import sql

from mandara.source.catalog import SourceError
from mandara.source.coverage import Coverage, SplitValue, ValueCoverage


class PostgresCoverageReader:
    def __init__(self, conninfo: str = "") -> None:
        self._conninfo = conninfo

    def coverage(self, schema: str, table: str, columns: Sequence[str], target_schema: str, target_table: str,
                 target_columns: Sequence[str], split_by: Sequence[str], max_values: int) -> Coverage:
        if not columns or len(columns) != len(target_columns):
            raise ValueError(f"参照の列の組が不正: {list(columns)} → {list(target_columns)}")
        if max_values < 1:
            raise ValueError(f"値の種類の上限は 1 以上: {max_values}")
        referenced = sql.SQL("exists (select 1 from {table} c where {join})").format(
            table=sql.Identifier(schema, table),
            join=sql.SQL(" and ").join(
                sql.SQL("c.{} = t.{}").format(sql.Identifier(a), sql.Identifier(b))
                for a, b in zip(columns, target_columns, strict=True)),
        )
        target = sql.Identifier(target_schema, target_table)
        where = f"{schema}.{table} → {target_schema}.{target_table}"
        try:
            with psycopg.connect(self._conninfo) as conn:
                conn.read_only = True
                conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
                with conn.transaction():
                    total = conn.execute(sql.SQL("select count(*), count(*) filter (where {r}) from {t} t").format(
                        r=referenced, t=target)).fetchone()
                    if total is None:
                        raise AssertionError("count の問い合わせが行を返さない")
                    split: dict[str, tuple[ValueCoverage, ...] | None] = {}
                    for column in split_by:
                        rows = conn.execute(sql.SQL(
                            "select t.{c}, count(*), count(*) filter (where {r}) from {t} t group by t.{c} "
                            "order by count(*) desc limit {n}").format(
                            c=sql.Identifier(column), r=referenced, t=target, n=sql.Literal(max_values + 1))).fetchall()
                        split[column] = (None if len(rows) > max_values
                                         else tuple(ValueCoverage(_split_value(v, column), n, k) for v, n, k in rows))
        except psycopg.Error as e:
            raise SourceError(f"PostgreSQL から参照の被覆（{where}）を読めなかった: {e}", e) from e
        return Coverage(total[0], total[1], split)


def _split_value(value: object, column: str) -> SplitValue:
    """文字列（char、varchar、text）・整数・真偽の列の値。ほかの型は区分の列の候補にならないはずなので誤り。"""
    if value is None or isinstance(value, str | int | bool):
        return value
    raise ValueError(f"区分の列 {column} の値が str / int / bool ではない: {type(value).__name__}")
