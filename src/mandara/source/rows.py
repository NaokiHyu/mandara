"""データソースの行の値を読むポート（ゼロコピー：ソースにある値を、その場で読む）。

Domain 層。値はソースの表現のまま（バイト列など）で返す。意味の型への変換はコーデック（mandara.codecs）の責務。
NULL は読み飛ばさず None として返す（数えるのは呼ぶ側）。件数の上限を超えたかを truncated で伝える。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ColumnRow:
    key: tuple[object, ...]
    """行の主キーの値（主キーの列の順）。"""
    value: object
    """列の値（ソースの表現）。NULL は None。"""


@dataclass(frozen=True)
class ColumnSample:
    rows: tuple[ColumnRow, ...]
    """主キーの順。"""
    truncated: bool
    """上限で打ち切った（表にはまだ行がある）。"""


class ColumnReader(Protocol):
    """1つの列の値を、行の主キーと組で読む。失敗は mandara.source.catalog.SourceError（とその下位）。"""

    def read_column(
        self, schema: str, table: str, key_columns: Sequence[str], column: str, limit: int
    ) -> ColumnSample: ...
