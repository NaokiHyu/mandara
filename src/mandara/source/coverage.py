"""参照の被覆を読むポート：参照される側（target）の行のうち、参照する側の表から参照されている行の数（ゼロコピー）。

Domain 層。is-a の手がかり（上位の行の一部だけに下位の行があり、その一部が区分の列の値と一致する）のため。
- covered は「参照する行が1つ以上ある target の行」の数（参照する行の数ではない）
- split_by の列ごとに、値ごとの行数と被覆の数を返す。値の種類が max_values を超える列は None（区分の列ではない）
- 区分の列の候補は文字列・整数・真偽の列だけなので、値は str / int / bool（NULL は None）に閉じる。それ以外の型を返すのは
  読み手の誤り（ValueError）。値の文字列表現は value_text で1通りに決める（主張の内容と ID がコネクタに依らない）
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol


SplitValue = str | int | bool | None


@dataclass(frozen=True)
class ValueCoverage:
    value: SplitValue
    rows: int
    covered: int

    def __post_init__(self) -> None:
        if not 0 <= self.covered <= self.rows or self.rows < 1:
            raise ValueError(f"値ごとの数が不正: rows={self.rows}, covered={self.covered}")


def value_text(value: str | int | bool) -> str:
    """区分の列の値の文字列表現（真偽は true / false）。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


@dataclass(frozen=True)
class Coverage:
    rows: int
    """target の行の数。"""
    covered: int
    """参照されている target の行の数。"""
    split: Mapping[str, tuple[ValueCoverage, ...] | None]
    """split_by の列 → 値ごとの被覆（値の種類が多すぎれば None）。値ごとの数を足すと rows / covered になる。"""

    def __post_init__(self) -> None:
        if not 0 <= self.covered <= self.rows:
            raise ValueError(f"被覆の数が不正: rows={self.rows}, covered={self.covered}")
        for column, values in self.split.items():
            if values is None:
                continue
            if (sum(v.rows for v in values), sum(v.covered for v in values)) != (self.rows, self.covered):
                raise ValueError(f"列 {column} の値ごとの数の和が、全体の数と合わない")
            if len({v.value for v in values}) != len(values):
                raise ValueError(f"列 {column} に同じ値が2回ある")


class CoverageReader(Protocol):
    """失敗は mandara.source.catalog.SourceError（とその下位）。"""

    def coverage(self, schema: str, table: str, columns: Sequence[str], target_schema: str, target_table: str,
                 target_columns: Sequence[str], split_by: Sequence[str], max_values: int) -> Coverage: ...
