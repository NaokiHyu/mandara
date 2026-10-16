"""PostgreSQL の表の行を読む（RecordReader の実装）。

Adapter 層。識別子は psycopg.sql で引用し、値はパラメータで渡す（名前も値も SQL に埋め込まない）。読み取り専用のトランザクションで読む。
Equals は列の型で比べる（キャストしないので主キーの索引が効く）。値が文字列（キーの文字列）なら、psycopg は型の決まらない
リテラルとして渡し、PostgreSQL が列の型として読む。読めなければ InvalidKeyError。
Contains は列を text にキャストした ILIKE の部分一致（% と _ は逃がす。索引は効かない。検索の仕様として許容する）。
接続は find ごとに開く（プールしていない。1 インスタンスを見るのに数十回開くことがある）。
上限を1件超えて読み、超えたら打ち切ったと伝える。
"""

from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg import sql

from mandara.source.catalog import SourceError
from mandara.source.records import Contains, Equals, InvalidKeyError, Records


class PostgresRecordReader:
    def __init__(self, conninfo: str = "") -> None:
        self._conninfo = conninfo

    def find(self, schema: str, table: str, columns: Sequence[str], order_by: Sequence[str],
             where: Contains | Equals | None, limit: int) -> Records:
        if limit < 1:
            raise ValueError(f"上限は 1 以上: {limit}")
        if not columns:
            raise ValueError("読む列が要る")
        condition, params = _condition(where)
        query = sql.SQL("select {columns} from {table}{where} order by {order} limit {limit}").format(
            columns=sql.SQL(", ").join(sql.Identifier(c) for c in columns),
            table=sql.Identifier(schema, table),
            where=condition,
            order=sql.SQL(", ").join(sql.Identifier(c) for c in order_by) if order_by else sql.SQL("1"),
            limit=sql.Literal(limit + 1),
        )
        try:
            with psycopg.connect(self._conninfo) as conn:
                conn.read_only = True
                with conn.transaction():
                    rows = conn.execute(query, params).fetchall()
        except psycopg.DataError as e:
            raise InvalidKeyError(f"{schema}.{table} の一致の値を列の型として読めない: {e}", e) from e
        except psycopg.Error as e:
            raise SourceError(f"PostgreSQL から {schema}.{table} の行を読めなかった: {e}", e) from e
        return Records(tuple(dict(zip(columns, row, strict=True)) for row in rows[:limit]), truncated=len(rows) > limit)


def _condition(where: Contains | Equals | None) -> tuple[sql.Composable, list[Any]]:
    match where:
        case None:
            return sql.SQL(""), []
        case Contains(columns=columns, text=text):
            if not columns:
                raise ValueError("部分一致を探す列が要る")
            pattern = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            parts = [sql.SQL("{}::text ilike %s").format(sql.Identifier(c)) for c in columns]
            return sql.SQL(" where ") + sql.SQL(" or ").join(parts), [pattern] * len(columns)
        case Equals(values=values):
            if not values:
                raise ValueError("一致させる列が要る")
            parts = [sql.SQL("{} = %s").format(sql.Identifier(c)) for c in values]
            return sql.SQL(" where ") + sql.SQL(" and ").join(parts), list(values.values())
