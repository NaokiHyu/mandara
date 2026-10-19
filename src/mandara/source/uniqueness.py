"""表の列の組の一意を確かめるポート（キーの推定のため。T2.7）。

Domain 層。
- rows：表の行の数、列ごとの NULL の数（全体を数えた事実）と、行の見本（一部の行の、指定した列の値の組）。
  見本は決定的に選ぶ（同じデータなら同じ見本）。見本の中の重複は全体の重複なので、一意でない組の足切りに使える
- distinct：表の行の数と、列の組の異なる値の組の数（NULL を含む行は数えない）。同じ読みで全体を数える。
  行の数と同じなら、その組は一意で NULL も無い
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RowSample:
    rows: int
    """表の行の数。"""
    nulls: tuple[int, ...]
    """列ごとの NULL の行の数（指定した列の順）。"""
    values: tuple[tuple[object, ...], ...]
    """行の見本（指定した列の順の値の組）。数は min(limit, rows)。"""

    def __post_init__(self) -> None:
        if self.rows < 0 or any(not 0 <= n <= self.rows for n in self.nulls):
            raise ValueError(f"行の数が不正: rows={self.rows}, nulls={self.nulls}")
        if len(self.values) > self.rows or any(len(v) != len(self.nulls) for v in self.values):
            raise ValueError(f"見本が行の数を超えるか、列の数が合わない: {len(self.values)} 行、列 {len(self.nulls)}")


@dataclass(frozen=True)
class Distinct:
    rows: int
    """表の行の数。"""
    distinct: int
    """NULL を含まない行の、異なる値の組の数。"""

    def __post_init__(self) -> None:
        if not 0 <= self.distinct <= self.rows:
            raise ValueError(f"数が不正: rows={self.rows}, distinct={self.distinct}")


class UniquenessReader(Protocol):
    """失敗は mandara.source.catalog.SourceError（とその下位）。"""

    def rows(self, schema: str, table: str, columns: Sequence[str], limit: int) -> RowSample:
        """columns は 1 列以上、limit は 1 以上。"""
        ...

    def distinct(self, schema: str, table: str, columns: Sequence[str]) -> Distinct:
        """columns は 1 列以上。"""
        ...
