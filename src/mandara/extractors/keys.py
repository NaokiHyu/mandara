"""キーの抽出器（T2.7）：主キーの宣言が無い表の、実データで一意な極小の列の組 → 主張（unique_in_data、source.kind: data）。

Domain 層の純粋な関数。値を読むのは呼ぶ側（UniquenessReader）。どの DB でも同じに働く。
- 見るのは、主キーの宣言が無い表（ビューは見ない）の、キーになりうる型族で、データに NULL の無い列
- 1 列から MAX_COLUMNS 列まで、列の少ない順に極小な組を探す（一意な組を含む組は見ない）
- 見本の列ごとの異なる値の数の積が見本の行の数に届かない組は、見本の中に必ず重複がある（数えずに落とす）
- 行の見本の中に重複があれば、その組は一意でない（見本の重複は全体の重複。足切りとして健全）。見本で一意だった組は、
  見本が表の全部でも、必ず DB で数えて確かめる（verify）。Python の等値は DB の等値と違うことがある（numeric の NaN、
  大文字小文字を区別しない照合など）ので、「見本で異なる ⇒ DB でも異なる」とは言えない。逆向き（見本で等しい ⇒ DB でも等しい）
  は成り立つので、足切りには使える
- 行の無い表は見ない（どの組も一意になり、何の証拠にもならない）
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import combinations
from math import prod

from mandara.extractors.catalog import quote
from mandara.om.claims import new_claim
from mandara.om.model import Claim, ClaimPredicate, ClaimSource, ClaimSourceKind, ObjectKind, SourceRef, TypeFamily
from mandara.source.catalog import Catalog, Table
from mandara.source.uniqueness import RowSample

EXTRACTOR = "keys 0.1"
SAMPLE_ROWS = 20_000
"""足切りに読む行の見本の数。"""
MAX_COLUMNS = 4
"""探す組の列の数の上限。"""
CLAIM_CONFIDENCE = 1.0
"""一意は全体を数えた事実（読んだ時点のデータについて）。"""
KEY_FAMILIES = frozenset({TypeFamily.integer, TypeFamily.decimal, TypeFamily.text, TypeFamily.uuid, TypeFamily.date,
                          TypeFamily.timestamp})
"""キーになりうる型族（浮動小数・真偽・バイト列・XML などはキーにしない）。"""


def targets(catalog: Catalog) -> list[Table]:
    """キーを探す表：主キーの宣言の無い表。"""
    return [t for t in catalog.tables if t.kind == ObjectKind.table.value and t.primary_key is None]


def columns(table: Table) -> list[str]:
    """キーになりうる型族の列（位置の順）。"""
    return [c.name for c in table.columns if c.family in KEY_FAMILIES]


def minimal_uniques(names: Sequence[str], sample: RowSample,
                    verify: Callable[[tuple[str, ...]], bool]) -> list[tuple[str, ...]]:
    """一意な極小の列の組（列の少ない順、同じ数なら names の位置の順）。sample は names の列を読んだもの。
    verify は、見本で一意だった組を DB で確かめる。"""
    if len(sample.nulls) != len(names):
        raise ValueError(f"見本の列の数 {len(sample.nulls)} が列の数 {len(names)} と違う")
    if sample.rows == 0:
        return []
    usable = [i for i, n in enumerate(sample.nulls) if n == 0]
    kinds = {i: len({row[i] for row in sample.values}) for i in usable}
    found: list[tuple[int, ...]] = []
    for size in range(1, MAX_COLUMNS + 1):
        for combo in combinations(usable, size):
            if any(set(f) <= set(combo) for f in found) or prod(kinds[i] for i in combo) < len(sample.values):
                continue
            if len({tuple(row[i] for i in combo) for row in sample.values}) < len(sample.values):
                continue
            if verify(tuple(names[i] for i in combo)):
                found.append(combo)
    return [tuple(names[i] for i in combo) for combo in found]


@dataclass(frozen=True)
class Found:
    table: Table
    rows: int
    uniques: tuple[tuple[str, ...], ...]


def claims(source: str, found: Found) -> list[Claim]:
    table = found.table
    return [new_claim(
        subject=SourceRef(source=source, schema_name=table.schema, table=table.name),
        predicate=ClaimPredicate.unique_in_data,
        source=ClaimSource(kind=ClaimSourceKind.data, locator=f"unique {quote(table.schema, table.name)} ({', '.join(quote(c) for c in u)})"),
        extracted_by=EXTRACTOR,
        confidence=CLAIM_CONFIDENCE,
        columns=list(u),
        distinct_values=found.rows,
    ) for u in found.uniques]
