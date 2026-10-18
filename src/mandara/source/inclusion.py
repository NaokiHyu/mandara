"""値の包含を読むポート：列の値が、別の表のキーの値にどれだけ含まれるか（ゼロコピー。宣言の無い参照の候補を測るため）。

Domain 層。仕様 6.2（値の包含率）、6.7.2 B。
- 列の様子（profile）：NULL を除いた行の数、異なる値の数、最小と最大（範囲で候補を足切りするため。順序に意味のある型だけ）
- 包含（inclusion）：1 つの列について、複数のキー（参照先の候補）への包含を1回で数える。値は NULL を除き、異なる値で数える。
  キーの値の数も同じ読みの中で数える（包含の数とキーの数が、別々の時点のデータにならない）。整数の列なら、列の値の最小から
  最大までの整数の数（範囲の幅）と、その範囲にあるキーの値の数も同じ読みの中で数える（列の値の範囲でのキーの詰まり方。
  偶然の包含を見分けるため）
- 見本の重なり（probe）：列の異なる値を値の順に先頭から sample 個だけ取り、キーごとに含まれる数を数える。重なりの無いキーを
  全部数える前に落とすため（範囲で比べられない型、たとえば uuid のため）。結果は数えた値の数と、キーごとの数
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

Ordered = int | Decimal | float | date | datetime
"""最小と最大を比べてよい値（数と日時。文字列は照合順序が DB に依るので比べない）。"""


@dataclass(frozen=True)
class ColumnProfile:
    rows: int
    """NULL を除いた行の数。"""
    distinct: int
    """NULL を除いた異なる値の数。"""
    low: Ordered | None
    high: Ordered | None
    """最小と最大（順序に意味のある型で、値があるときだけ。ほかは None）。"""

    def __post_init__(self) -> None:
        if not 0 <= self.distinct <= self.rows or (self.rows > 0) != (self.distinct > 0):
            raise ValueError(f"列の数が不正: rows={self.rows}, distinct={self.distinct}")
        if (self.low is None) != (self.high is None):
            raise ValueError("最小と最大は両方あるか、両方無いか")


@dataclass(frozen=True)
class KeyRef:
    schema: str
    table: str
    column: str


@dataclass(frozen=True)
class Inclusion:
    distinct: int
    """列の、NULL を除いた異なる値の数。"""
    max_repeats: int
    """1 つの値を持つ行の最大の数（値が無ければ 0）。"""
    matched: tuple[int, ...]
    """keys の順に、列の異なる値のうち、そのキーの値にある数。"""
    key_values: tuple[int, ...]
    """keys の順に、キーの異なる値の数（matched と同じ読みの中で数える。数が互いに食い違わない）。"""
    span: int | None
    """列の値の最小から最大までにある整数の数（spans を求めて、列に値があるときだけ。ほかは None）。"""
    key_values_in_span: tuple[int, ...] | None
    """keys の順に、キーの値のうち列の値の最小から最大までにある数（span があるときだけ）。"""

    def __post_init__(self) -> None:
        if len(self.matched) != len(self.key_values):
            raise ValueError(f"キーごとの数の長さが違う: {len(self.matched)} と {len(self.key_values)}")
        if (self.span is None) != (self.key_values_in_span is None):
            raise ValueError("範囲の幅と、範囲にあるキーの値の数は、両方あるか両方無いか")
        if self.span is not None and self.key_values_in_span is not None:
            if self.span < self.distinct or self.distinct == 0:
                raise ValueError(f"範囲の幅 {self.span} が値の数 {self.distinct} より小さいか、値が無いのに幅がある")
            if len(self.key_values_in_span) != len(self.matched) or any(
                    not m <= n <= min(self.span, k)
                    for m, n, k in zip(self.matched, self.key_values_in_span, self.key_values, strict=True)):
                raise ValueError(f"範囲にあるキーの値の数が不正: {self.key_values_in_span}（含まれる {self.matched}、"
                                 f"キーの値 {self.key_values}、幅 {self.span}）")
        if any(not 0 <= m <= min(self.distinct, k) for m, k in zip(self.matched, self.key_values, strict=True)):
            raise ValueError(f"含まれる値の数が不正: {self.matched}（値の数 {self.distinct}、キーの値の数 {self.key_values}）")
        if (self.distinct == 0) != (self.max_repeats == 0):
            raise ValueError(f"値の数と最大の行数が食い違う: distinct={self.distinct}, max_repeats={self.max_repeats}")


class InclusionReader(Protocol):
    """失敗は mandara.source.catalog.SourceError（とその下位）。"""

    def profile(self, schema: str, table: str, column: str, ordered: bool) -> ColumnProfile:
        """ordered なら最小と最大も読む。"""
        ...

    def inclusion(self, schema: str, table: str, column: str, keys: Sequence[KeyRef], spans: bool) -> Inclusion:
        """keys は1つ以上。spans なら（列とキーが整数のときだけ求める）列の値の範囲の幅と、範囲にあるキーの値の数も数える。"""
        ...

    def probe(self, schema: str, table: str, column: str, keys: Sequence[KeyRef], sample: int) -> tuple[int, ...]:
        """keys の順に、見本の値のうちそのキーにある数。sample は 1 以上、keys は1つ以上。"""
        ...
