"""データソースの行を読むポート（ゼロコピー：ソースにある行を、その場で読む。ドメインビューワのため）。

Domain 層。値はソースの表現のまま返す（表示の形にするのは呼ぶ側）。NULL は None。件数の上限を超えたかを truncated で伝える。
条件は2種だけ：文字の部分一致（どれかの列）と、列の値の一致（すべての列）。
- 一致（Equals）は列の型で比べる。値は、同じ読み手が読んだソースの値か、キーの文字列（key_text）のどちらか。
  キーの文字列を列の型として読めなければ InvalidKeyError（URL から来たキーなど）
- キーの文字列（key_text）は、行をたどるための往復できる表現（表示の文字列とは別）。読み手はこれを列の型として読めること
- 集計（aggregate）はソースに押し下げる（行を持ってきて数えない）。一致（Equals）で絞った行を、まとめずに・列の値ごとに・
  時間の区切りごとに、件数・合計・平均・最小・最大にする。NULL は SQL の集計と同じに扱う（count(列) と sum は NULL を数えない）
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID

from mandara.source.catalog import SourceError


@dataclass(frozen=True)
class Contains:
    """columns のどれかの値（文字列の表現）が text を含む（大文字小文字を区別しない）。"""

    columns: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class Equals:
    """すべての列の値が、与えた値と一致する（列の型で比べる）。"""

    values: Mapping[str, object]


@dataclass(frozen=True)
class Records:
    rows: tuple[Mapping[str, object], ...]
    """列名 → 値（ソースの表現）。order_by の順。"""
    truncated: bool
    """上限で打ち切った（条件に合う行がまだある）。"""


class InvalidKeyError(SourceError):
    """一致の値（キーの文字列）を、列の型として読めなかった。"""


class NotKeyableError(ValueError):
    """キーの文字列にできない値（バイト列など）。"""


def key_text(value: object) -> str:
    """行をたどるためのキーの文字列（往復できる表現）。文字列、整数、10進数、日付、日時、時刻、UUID、真偽だけ。"""
    if isinstance(value, bool | str | int | Decimal | date | datetime | time | UUID):
        return str(value)
    raise NotKeyableError(f"キーの文字列にできない値: {type(value).__name__}")


Op = Literal["count", "sum", "avg", "min", "max"]
OPS: frozenset[str] = frozenset({"count", "sum", "avg", "min", "max"})
TimeUnit = Literal["day", "month", "year"]
TIME_UNITS: frozenset[str] = frozenset({"day", "month", "year"})


@dataclass(frozen=True)
class Measure:
    """集計の1つ。count で column が None なら行の数、それ以外は列の値（NULL を除く）について。"""

    op: Op
    column: str | None

    def __post_init__(self) -> None:
        if self.op not in OPS:  # 読み手は op を SQL の関数などに写すので、閉じた集合の外は入口で拒む
            raise ValueError(f"集計の種類が不正: {self.op!r}")
        if (self.column is None) != (self.op == "count"):
            raise ValueError(f"列が要るのは count 以外（count は列なしで行の数）: {self.op} {self.column}")


@dataclass(frozen=True)
class TimeBucket:
    """時間の区切り：column の値を unit の始まりの日付に切り詰めてまとめる。column が NULL の行は入れない。"""

    column: str
    unit: TimeUnit

    def __post_init__(self) -> None:
        if self.unit not in TIME_UNITS:
            raise ValueError(f"時間の区切りが不正: {self.unit!r}")


@dataclass(frozen=True)
class Group:
    key: object
    """まとめた値（by が列ならその値、TimeBucket なら区切りの始まりの date、by が無ければ None）。"""
    values: tuple[object, ...]
    """measures の順。合計などはソースの表現（Decimal など）。対象の行が無ければ None。"""


@dataclass(frozen=True)
class Aggregated:
    groups: tuple[Group, ...]
    truncated: bool
    """上限で打ち切った（まだまとまりがある）。"""


class RecordReader(Protocol):
    """表の行を読む。失敗は mandara.source.catalog.SourceError（とその下位）。"""

    def find(self, schema: str, table: str, columns: Sequence[str], order_by: Sequence[str],
             where: Contains | Equals | None, limit: int, descending: bool = False) -> Records:
        """descending なら order_by の列の降順（新しいものから、など）。"""
        ...

    def aggregate(self, schema: str, table: str, where: Equals, by: str | TimeBucket | None,
                  measures: Sequence[Measure], limit: int, latest_first: bool = False) -> Aggregated:
        """by が None なら1つのまとまり（行が無くても1つ。件数 0）。列なら値ごとに件数の多い順。
        TimeBucket なら区切りの古い順（latest_first なら新しい順）。"""
        ...
