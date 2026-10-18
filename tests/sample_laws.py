"""ValueSampler の契約（全コネクタ共通の法則）。

各コネクタのテストは、自分の読み手と、同じ値を別の道で読む oracle（列の値の一覧を返す関数）を渡してこれらを当てる。
法則は「違反の一覧」を返す。空なら成立。
- 数：NULL を除いた行の数と異なる値の数が、集合で数えたのと同じ
- 見本：NULL を含まず、重ならず、列の値の中にあり、数は min(limit, 異なる値の数)
- 決定的：同じ読みを 2 回しても同じ見本
- 伸ばしても先頭は変わらない：limit を大きくした見本の先頭は、小さい limit の見本（選ぶ順は値に依らず決まっている）
"""

from collections.abc import Callable, Sequence

from mandara.source.samples import ValueSampler

Oracle = Callable[[str, str, str], Sequence[object]]
"""（スキーマ、表、列）→ 列の値（NULL は None）。"""


def sample_agrees_with_sets(reader: ValueSampler, oracle: Oracle, schema: str, table: str, column: str,
                            limit: int) -> list[str]:
    where = f"{schema}.{table}.{column}"
    rows = [v for v in oracle(schema, table, column) if v is not None]
    distinct = set(rows)
    problems: list[str] = []
    found = reader.sample(schema, table, column, limit)
    if (found.rows, found.distinct) != (len(rows), len(distinct)):
        problems.append(f"{where}: 数 {found.rows}/{found.distinct} ≠ 集合 {len(rows)}/{len(distinct)}")
    if len(found.values) != min(limit, len(distinct)):
        problems.append(f"{where}: 見本の数 {len(found.values)} ≠ min({limit}, {len(distinct)})")
    if len(set(found.values)) != len(found.values) or not set(found.values) <= distinct:
        problems.append(f"{where}: 見本が重なるか、列に無い値を含む")
    if reader.sample(schema, table, column, limit) != found:
        problems.append(f"{where}: 同じ読みで見本が変わる")
    longer = reader.sample(schema, table, column, limit + 7)
    if longer.values[:len(found.values)] != found.values:
        problems.append(f"{where}: limit を伸ばすと見本の先頭が変わる")
    return problems
