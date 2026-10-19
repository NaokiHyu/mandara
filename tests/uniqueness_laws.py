"""UniquenessReader の契約（全コネクタ共通の法則）。

各コネクタのテストは、自分の読み手と、同じ行を別の道で読む oracle（表の指定した列の値の組の一覧を返す関数）を渡して当てる。
法則は「違反の一覧」を返す。空なら成立。
- 数：行の数と、列ごとの NULL の数が、行の一覧で数えたのと同じ
- 見本：数は min(limit, 行の数)。見本の値の組は表の行にある（重複も含めて、行の一覧の多重集合の部分）
- 決定的：同じ読みを 2 回しても同じ見本
- 異なる値の組：NULL を含まない行の異なる組の数と、行の数が、行の一覧で数えたのと同じ
"""

from collections import Counter
from collections.abc import Callable, Sequence

from mandara.source.uniqueness import UniquenessReader

Oracle = Callable[[str, str, Sequence[str]], Sequence[tuple[object, ...]]]
"""（スキーマ、表、列）→ 表のすべての行の、列の値の組（NULL は None）。"""


def rows_agree_with_lists(reader: UniquenessReader, oracle: Oracle, schema: str, table: str, columns: Sequence[str],
                          limit: int) -> list[str]:
    where = f"{schema}.{table}({', '.join(columns)})"
    rows = list(oracle(schema, table, columns))
    problems: list[str] = []
    found = reader.rows(schema, table, columns, limit)
    nulls = tuple(sum(1 for r in rows if r[i] is None) for i in range(len(columns)))
    if (found.rows, found.nulls) != (len(rows), nulls):
        problems.append(f"{where}: 数 {found.rows}/{found.nulls} ≠ 一覧 {len(rows)}/{nulls}")
    if len(found.values) != min(limit, len(rows)):
        problems.append(f"{where}: 見本の数 {len(found.values)} ≠ min({limit}, {len(rows)})")
    if Counter(found.values) - Counter(rows):
        problems.append(f"{where}: 見本に表に無い行（か、表より多い重複）がある")
    if reader.rows(schema, table, columns, limit) != found:
        problems.append(f"{where}: 同じ読みで見本が変わる")
    counted = reader.distinct(schema, table, columns)
    distinct = len({r for r in rows if None not in r})
    if (counted.rows, counted.distinct) != (len(rows), distinct):
        problems.append(f"{where}: 異なる組 {counted.rows}/{counted.distinct} ≠ 一覧 {len(rows)}/{distinct}")
    return problems
