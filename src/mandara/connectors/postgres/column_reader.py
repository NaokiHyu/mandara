"""PostgreSQL の列の値を読む（ColumnReader の実装）。

Adapter 層。識別子は psycopg.sql で引用する（名前を SQL に埋め込まない）。読み取り専用のトランザクションで読む。
上限を1件超えて読み、超えたら打ち切ったと伝える。
"""

from collections.abc import Sequence

import psycopg
from psycopg import sql

from mandara.source.catalog import SourceError
from mandara.source.rows import ColumnRow, ColumnSample


class PostgresColumnReader:
    def __init__(self, conninfo: str = "") -> None:
        self._conninfo = conninfo

    def read_column(
        self, schema: str, table: str, key_columns: Sequence[str], column: str, limit: int
    ) -> ColumnSample:
        if limit < 1:
            raise ValueError(f"上限は 1 以上: {limit}")
        if not key_columns:
            raise ValueError("行を特定する主キーの列が要る")
        keys = [sql.Identifier(k) for k in key_columns]
        query = sql.SQL("select {keys}, {column} from {table} order by {keys} limit {limit}").format(
            keys=sql.SQL(", ").join(keys),
            column=sql.Identifier(column),
            table=sql.Identifier(schema, table),
            limit=sql.Literal(limit + 1),
        )
        try:
            with psycopg.connect(self._conninfo) as conn:
                conn.read_only = True
                with conn.transaction():
                    rows = conn.execute(query).fetchall()
        except psycopg.Error as e:
            raise SourceError(f"PostgreSQL から {schema}.{table}.{column} を読めなかった: {e}", e) from e
        n = len(key_columns)
        sample = tuple(ColumnRow(tuple(row[:n]), row[n]) for row in rows[:limit])
        return ColumnSample(sample, truncated=len(rows) > limit)
