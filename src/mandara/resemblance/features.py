"""値ごとの特徴量：分類器 2 標本検定（C2ST）の入力。Sherlock の「文字の分布」と「全体の統計」の考え方を、値 1 つの単位で。

Domain 層の純粋な関数。値の意味（単語の埋め込み）は見ない。形と大きさだけ。
"""

import math
from collections import Counter
from decimal import Decimal, InvalidOperation

from mandara.resemblance.shape import CLASSES, tokens

_EDGE = (*CLASSES, "")
"""先頭・末尾の文字の種類（"" は記号）。"""
NAMES = (
    "log_length", *(f"share_{k or 'symbol'}" for k in _EDGE), "tokens", "symbols_distinct",
    "numeric", "negative", "log_magnitude", "decimals", "leading_zero", "entropy",
    *(f"first_{k or 'symbol'}" for k in _EDGE), *(f"last_{k or 'symbol'}" for k in _EDGE),
)
"""特徴量の名前（並びの順）。"""


def features(value: str) -> tuple[float, ...]:
    toks = tokens(value)
    n = len(value)
    by_class: Counter[str] = Counter()
    for k, t in toks:
        by_class[k] += len(t)
    number = _number(value)
    decimals = len(value.split(".", 1)[1]) if number is not None and "." in value else 0
    out = [math.log1p(n), *(by_class[k] / n if n else 0.0 for k in _EDGE), float(len(toks)),
           float(len({t for k, t in toks if not k})),
           1.0 if number is not None else 0.0,
           1.0 if number is not None and number < 0 else 0.0,
           math.log10(float(abs(number)) + 1) if number is not None else 0.0,
           float(decimals),
           1.0 if n > 1 and value[0] == "0" and value[1:2].isdigit() else 0.0,
           _entropy(value)]
    first = toks[0][0] if toks else None
    last = toks[-1][0] if toks else None
    out += [1.0 if first == k else 0.0 for k in _EDGE]
    out += [1.0 if last == k else 0.0 for k in _EDGE]
    return tuple(out)


def _number(value: str) -> Decimal | None:
    """10 進の数として読めれば、その数（指数表記・無限・NaN は数として扱わない）。"""
    text = value.strip()
    if not text or any(c not in "+-.0123456789" for c in text):
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _entropy(value: str) -> float:
    """文字ごとのエントロピー（ビット）。"""
    if not value:
        return 0.0
    counts = Counter(value)
    n = len(value)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


assert len(features("AW-0012")) == len(NAMES), "特徴量の名前と並びを合わせる"
