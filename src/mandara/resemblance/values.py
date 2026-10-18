"""値の文字列表現：どの DB の値も、同じ値なら同じ文字列にする（形と分布の測定が、コネクタに依らないように）。

Domain 層。表現を決められない型はエラー（黙って str() にしない。形を測る対象の型族は限っている）。
- 真偽は true / false
- 10 進数は指数表記にせず、ソースの桁（スケール）を保つ（12.50 は 12.50。金額の小数の桁は形の手がかり）。浮動小数は最短の桁で、
  指数表記にしない（無限・NaN は inf / nan）
- 日付・日時・時刻は ISO 8601。uuid は小文字のハイフン区切り
"""

import math
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID


def render(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):  # 最短の桁で、指数表記にしない（1e+20 を 100000000000000000000 と書く）
        return format(Decimal(repr(value)), "f") if math.isfinite(value) else repr(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise ValueError(f"値の文字列表現を決められない型: {type(value).__name__}")
