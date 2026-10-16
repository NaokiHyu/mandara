"""データソースの行を読むポート（ゼロコピー：ソースにある行を、その場で読む。ドメインビューワのため）。

Domain 層。値はソースの表現のまま返す（表示の形にするのは呼ぶ側）。NULL は None。件数の上限を超えたかを truncated で伝える。
条件は2種だけ：文字の部分一致（どれかの列）と、列の値の一致（すべての列）。
- 一致（Equals）は列の型で比べる。値は、同じ読み手が読んだソースの値か、キーの文字列（key_text）のどちらか。
  キーの文字列を列の型として読めなければ InvalidKeyError（URL から来たキーなど）
- キーの文字列（key_text）は、行をたどるための往復できる表現（表示の文字列とは別）。読み手はこれを列の型として読めること
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Protocol
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


class RecordReader(Protocol):
    """表の行を読む。失敗は mandara.source.catalog.SourceError（とその下位）。"""

    def find(self, schema: str, table: str, columns: Sequence[str], order_by: Sequence[str],
             where: Contains | Equals | None, limit: int) -> Records: ...
