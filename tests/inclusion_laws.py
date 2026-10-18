"""InclusionReader の契約（全コネクタ共通の法則）。

各コネクタのテストは、自分の reader と、同じ値を別の道で読む oracle（列の値の一覧を返す関数）を渡してこれらを当てる。
法則は「違反の一覧」を返す。空なら成立。値の比べ方（照合順序、型をまたぐ等値）は読み手に任せ、oracle はその読み手の値で
集合を作る。見本の取り方（値の順の先頭）は照合順序に依るので、見本の重なりは不等式だけで見る。
値の範囲は、spans で求めたときだけ（列とキーの値が整数のとき）：幅は列の値の最大 − 最小 + 1、範囲にあるキーの値の数。
求めなければ（または列に値が無ければ）None。
"""

from collections import Counter
from collections.abc import Callable, Sequence

from mandara.source.inclusion import InclusionReader, KeyRef

Oracle = Callable[[str, str, str], Sequence[object]]
"""（スキーマ、表、列）→ 列の値（NULL は None）。"""


def counts_agree_with_sets(reader: InclusionReader, oracle: Oracle, schema: str, table: str, column: str,
                           keys: Sequence[KeyRef], sample: int, spans: bool) -> list[str]:
    where = f"{schema}.{table}.{column}"
    rows = [v for v in oracle(schema, table, column) if v is not None]
    distinct = set(rows)
    problems: list[str] = []
    counted = reader.inclusion(schema, table, column, keys, spans)
    if counted.distinct != len(distinct):
        problems.append(f"{where}: 異なる値の数 {counted.distinct} ≠ 集合 {len(distinct)}")
    expected_repeats = max(Counter(rows).values(), default=0)
    if counted.max_repeats != expected_repeats:
        problems.append(f"{where}: 1 値の最大の行数 {counted.max_repeats} ≠ {expected_repeats}")
    if spans and not all(isinstance(v, int) and not isinstance(v, bool) for v in distinct):
        raise ValueError(f"{where}: 整数でない列に範囲の幅を求めた（法則の使い方の誤り）")
    ints = [v for v in distinct if isinstance(v, int)]
    low, high = (min(ints), max(ints)) if spans and ints else (None, None)
    expected_span = high - low + 1 if low is not None and high is not None else None
    if counted.span != expected_span:
        problems.append(f"{where}: 範囲の幅 {counted.span} ≠ {expected_span}")
    in_span = (None,) * len(keys) if counted.key_values_in_span is None else counted.key_values_in_span
    for key, matched, key_values, in_range in zip(keys, counted.matched, counted.key_values, in_span, strict=True):
        key_set = {v for v in oracle(key.schema, key.table, key.column) if v is not None}
        expected_in = (sum(1 for k in key_set if isinstance(k, int) and low <= k <= high)
                       if low is not None and high is not None else None)
        if in_range != expected_in:
            problems.append(f"{where} in {key}: 範囲にあるキーの値の数 {in_range} ≠ {expected_in}")
        if matched != len(distinct & key_set):
            problems.append(f"{where} in {key}: 含まれる数 {matched} ≠ 集合 {len(distinct & key_set)}")
        if key_values != len(key_set):
            problems.append(f"{where} in {key}: キーの値の数 {key_values} ≠ 集合 {len(key_set)}")
    profile = reader.profile(schema, table, column, ordered=False)
    if (profile.rows, profile.distinct) != (len(rows), len(distinct)):
        problems.append(f"{where}: 様子 {profile.rows}/{profile.distinct} ≠ 集合 {len(rows)}/{len(distinct)}")
    probed = reader.probe(schema, table, column, keys, sample)
    for key, n, matched in zip(keys, probed, counted.matched, strict=True):
        if n > min(sample, len(distinct)) or n > matched:
            problems.append(f"{where} in {key}: 見本の重なり {n} が見本の数か含まれる数を超える")
        if matched == len(distinct) and n != min(sample, len(distinct)):
            problems.append(f"{where} in {key}: すべて含まれるのに見本の重なり {n} が見本の数と違う")
    return problems
