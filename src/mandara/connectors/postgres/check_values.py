"""PostgreSQL の CHECK 制約の式から、1 列の値の一覧を読む（列 = ANY (ARRAY[...]) と 列 IN (...)）。

Adapter 層（PostgreSQL のコネクタの内側）。方言の式の読み方はここに閉じ込め、Catalog の Check には読めた値の一覧だけを渡す。
- 読むのは、式全体がちょうど「1 つの列が値の一覧のどれか」のときだけ。列は型変換（::text）と upper / lower に包まれていてよい
  が、そのときは「揃えた後の値が一覧のどれか」で、列の値そのものは一覧に限られない（'m' も通る）。normalized で区別する。
  値はすべて文字列か数の定数。「同じ列が NULL」との OR はよい（NULL を許す列の値の一覧。NULL は値ではない。IS NOT NULL は
  値を限らないので読まない）
- それ以外の式（範囲、複数の条件、関数、NULL を含む一覧）は値の一覧ではない（None）。式の文字列は Check にそのまま残る
- 式を読めない（sqlglot が字句・構文を解析できない）ときも None。値の一覧と認められないのと同じ扱い（式の文字列は
  checks の主張に残るので情報は失われない。値の一覧を主張にしないだけ）。読めない CHECK でカタログ全体を止めない
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError


def _unwrap(node: exp.Expr) -> tuple[exp.Expr, bool]:
    """括弧・型変換・upper / lower を剥がす。剥がした包みに型変換か upper / lower があれば normalized。"""
    normalized = False
    while isinstance(node, (exp.Paren, exp.Cast, exp.Upper, exp.Lower)):
        normalized = normalized or not isinstance(node, exp.Paren)
        node = node.this
    return node, normalized


def _literals(nodes: list[exp.Expr]) -> tuple[str, ...] | None:
    values: list[str] = []
    for node in nodes:
        node = node.this if isinstance(node, exp.Cast) else node
        if not isinstance(node, exp.Literal):
            return None
        values.append(str(node.this))
    return tuple(values) if values else None


@dataclass(frozen=True)
class Listed:
    column: str
    values: tuple[str, ...]
    normalized: bool
    """列を型変換か upper / lower で揃えてから比べている（列の値そのものは一覧に限られない）。"""


def allowed_values(expression: str) -> Listed | None:
    """値の一覧の CHECK なら、その列と値の一覧。そうでなければ（読めなければ）None。"""
    try:
        tree = sqlglot.parse_one(expression, read="postgres")
    except SqlglotError:
        return None
    node = _strip(tree)
    if isinstance(node, exp.Or):  # 値の一覧 OR 同じ列 IS NULL（順はどちらでも）
        for listed, other in ((node.this, node.expression), (node.expression, node.this)):
            found = _listed(_strip(listed))
            if found is not None and _is_null_of(_strip(other), found.column):
                return found
        return None
    return _listed(node)


def _strip(node: exp.Expr) -> exp.Expr:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _is_null_of(node: exp.Expr, column: str) -> bool:
    if isinstance(node, exp.Not) or not isinstance(node, exp.Is) or not isinstance(node.expression, exp.Null):
        return False  # IS NOT NULL は値を限らない
    if node.args.get("negate"):
        return False
    target, _ = _unwrap(node.this)
    return isinstance(target, exp.Column) and target.name == column


def _listed(node: exp.Expr) -> Listed | None:
    if isinstance(node, exp.In) and not node.args.get("query"):
        (column, normalized), values = _unwrap(node.this), _literals(list(node.expressions))
    elif isinstance(node, exp.EQ) and isinstance(node.expression, exp.Any):
        array, _ = _unwrap(node.expression.this)
        if not isinstance(array, exp.Array):
            return None
        (column, normalized), values = _unwrap(node.this), _literals(list(array.expressions))
    else:
        return None
    if not isinstance(column, exp.Column) or values is None:
        return None
    return Listed(column.name, values, normalized)
