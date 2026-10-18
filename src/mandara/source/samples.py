"""列の値の見本を読むポート：NULL を除いた異なる値を、決定的に選んだ一部だけ（ゼロコピー。値の似かたを測るため。T2.6）。

Domain 層。
- 見本は異なる値から選ぶ（行の多い値に引きずられない。同じ意味の列は、値の集合の形が似る）
- 選び方は読み手が決めるが、決定的であること（同じデータなら同じ見本）。値の順に先頭から取るのではなく、値に依らず散らばる
  順（PostgreSQL では値の文字列のハッシュの順）で取る。先頭から取ると、値の順に偏った見本になる
- 値はソースの表現のまま返す。文字列表現は mandara.resemblance.values.render が 1 通りに決める
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ValueSample:
    rows: int
    """NULL を除いた行の数。"""
    distinct: int
    """NULL を除いた異なる値の数。"""
    values: tuple[object, ...]
    """異なる値の見本（重ならない。NULL を含まない）。数は min(limit, distinct)。"""

    def __post_init__(self) -> None:
        if not 0 <= self.distinct <= self.rows or (self.rows > 0) != (self.distinct > 0):
            raise ValueError(f"列の数が不正: rows={self.rows}, distinct={self.distinct}")
        if len(self.values) > self.distinct or any(v is None for v in self.values):
            raise ValueError(f"見本が異なる値の数を超えるか、NULL を含む: {len(self.values)} > {self.distinct}")


class ValueSampler(Protocol):
    """失敗は mandara.source.catalog.SourceError（とその下位）。"""

    def sample(self, schema: str, table: str, column: str, limit: int) -> ValueSample:
        """limit は 1 以上。"""
        ...
