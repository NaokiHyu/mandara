"""PostgreSQL の表の行を読む（RecordReader の実装）。

Adapter 層。識別子は psycopg.sql で引用し、値はパラメータで渡す（名前も値も SQL に埋め込まない）。読み取り専用のトランザクションで読む。
Equals は列の型で比べる（キャストしないので主キーの索引が効く）。値が文字列（キーの文字列）なら、psycopg は型の決まらない
リテラルとして渡し、PostgreSQL が列の型として読む。読めなければ InvalidKeyError。
Contains は列を text にキャストした ILIKE の部分一致（% と _ は逃がす。索引は効かない。検索の仕様として許容する）。
接続は find ごとに開く（プールしていない。1 インスタンスを見るのに数十回開くことがある）。
集計は SQL の集計関数に押し下げる。時間の区切りは date_trunc（区切りの始まりの日付）。
上限を1件超えて読み、超えたら打ち切ったと伝える。
"""

from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg import sql

from mandara.source.catalog import SourceError
from mandara.source.records import (
    Aggregated,
    All,
    Contains,
    Equals,
    Group,
    InvalidKeyError,
    Measure,
    Records,
    TimeBucket,
    Where,
)


class PostgresRecordReader:
    def __init__(self, conninfo: str = "") -> None:
        self._conninfo = conninfo

    def find(self, schema: str, table: str, columns: Sequence[str], order_by: Sequence[str],
             where: Where | None, limit: int, descending: bool = False) -> Records:
        if limit < 1:
            raise ValueError(f"上限は 1 以上: {limit}")
        if not columns:
            raise ValueError("読む列が要る")
        condition, params = _condition(where)
        query = sql.SQL("select {columns} from {table}{where} order by {order} limit {limit}").format(
            columns=sql.SQL(", ").join(sql.Identifier(c) for c in columns),
            table=sql.Identifier(schema, table),
            where=condition,
            order=sql.SQL(", ").join(sql.SQL("{} desc nulls last" if descending else "{}").format(sql.Identifier(c))
                                     for c in order_by) if order_by else sql.SQL("1"),
            limit=sql.Literal(limit + 1),
        )
        rows = self._run(schema, table, query, params)
        return Records(tuple(dict(zip(columns, row, strict=True)) for row in rows[:limit]), truncated=len(rows) > limit)

    def aggregate(self, schema: str, table: str, where: Equals | None, by: str | TimeBucket | None,
                  measures: Sequence[Measure], limit: int, latest_first: bool = False) -> Aggregated:
        if limit < 1:
            raise ValueError(f"上限は 1 以上: {limit}")
        if not measures:
            raise ValueError("集計が要る")
        values = sql.SQL(", ").join(_measure(m) for m in measures)
        if by is None:
            condition, params = _condition(where)
            query = sql.SQL("select null, {values} from {table}{where}").format(
                values=values, table=sql.Identifier(schema, table), where=condition)
        else:
            if isinstance(by, TimeBucket):
                key = sql.SQL("date_trunc({unit}, {c})::date").format(unit=_UNITS[by.unit], c=sql.Identifier(by.column))
                condition, params = _condition(where, [sql.SQL("{} is not null").format(sql.Identifier(by.column))])
                order = sql.SQL("1 desc" if latest_first else "1")
            else:
                key, order = sql.Identifier(by), sql.SQL("count(*) desc, 1")
                condition, params = _condition(where)
            query = sql.SQL("select {key}, {values} from {table}{where} group by 1 order by {order} "
                            "limit {limit}").format(key=key, values=values, table=sql.Identifier(schema, table),
                                                    where=condition, order=order,
                                                    limit=sql.Literal(limit + 1))
        rows = self._run(schema, table, query, params)
        return Aggregated(tuple(Group(r[0], tuple(r[1:])) for r in rows[:limit]), truncated=len(rows) > limit)

    def _run(self, schema: str, table: str, query: sql.Composed, params: list[Any]) -> list[tuple[Any, ...]]:
        try:
            with psycopg.connect(self._conninfo) as conn:
                conn.read_only = True
                with conn.transaction():
                    return conn.execute(query, params).fetchall()
        except psycopg.DataError as e:
            raise InvalidKeyError(f"{schema}.{table} の一致の値を列の型として読めない: {e}", e) from e
        except psycopg.Error as e:
            raise SourceError(f"PostgreSQL から {schema}.{table} の行を読めなかった: {e}", e) from e


def _condition(where: Where | None, extra: Sequence[sql.Composable] = ()) -> tuple[sql.Composable, list[Any]]:
    """where（と、値を持たない追加の節 extra）を1つの where 句にする。節が無ければ空。"""
    parts = () if where is None else where.parts if isinstance(where, All) else (where,)
    clauses: list[sql.Composable] = []
    params: list[Any] = []
    for part in parts:
        clause, values = _part(part)
        clauses.append(sql.SQL("(") + clause + sql.SQL(")"))
        params += values
    clauses += extra
    if not clauses:
        return sql.SQL(""), params
    return sql.SQL(" where ") + sql.SQL(" and ").join(clauses), params


def _part(where: Contains | Equals) -> tuple[sql.Composable, list[Any]]:
    match where:
        case Contains(columns=columns, text=text):
            if not columns:
                raise ValueError("部分一致を探す列が要る")
            pattern = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            parts = [sql.SQL("{}::text ilike %s").format(sql.Identifier(c)) for c in columns]
            return sql.SQL(" or ").join(parts), [pattern] * len(columns)
        case Equals(values=values):
            parts = [sql.SQL("{} = %s").format(sql.Identifier(c)) for c in values]
            return sql.SQL(" and ").join(parts), list(values.values())


_FUNCTIONS = {"count": sql.SQL("count"), "sum": sql.SQL("sum"), "avg": sql.SQL("avg"), "min": sql.SQL("min"),
              "max": sql.SQL("max")}
_UNITS = {"day": sql.Literal("day"), "month": sql.Literal("month"), "year": sql.Literal("year")}


def _measure(m: Measure) -> sql.Composable:
    if m.column is None:
        return sql.SQL("count(*)")
    return sql.SQL("{f}({c})").format(f=_FUNCTIONS[m.op], c=sql.Identifier(m.column))
