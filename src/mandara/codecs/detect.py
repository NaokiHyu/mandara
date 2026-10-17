"""列の値から、物理的な表現（encoding）を機械的に判別する（T2.4）。

Domain 層の純粋な関数。値はソースの表現のまま（読むのは呼ぶ側）。
各コーデックについて、NULL でない値のうち matches が真の数と、さらに decode も通った数を数える。
- すべての値を読めたコーデックがちょうど1つなら、それに決める
- 2つ以上なら決めない（どれとも言えない）。部分的にしか読めないものも決めない（形式の混ざった列を1つに寄せない）
- どのコーデックにも当たらない（画像など、登録の無い形式か標準の表現）も、決まらなかった理由として返す
"""

from collections.abc import Sequence
from dataclasses import dataclass

from mandara.codecs.codec import Codec, CodecError
from mandara.codecs.registry import CODECS


@dataclass(frozen=True)
class Tally:
    encoding: str
    matched: int
    """matches が真だった値の数。"""
    decoded: int
    """matches が真で、decode も通った値の数。"""


@dataclass(frozen=True)
class Detection:
    values: int
    """NULL でない値の数。"""
    nulls: int
    tallies: tuple[Tally, ...]
    """どれかの値で matches が真だったコーデックだけ。"""
    encoding: str | None
    """決まった encoding。決まらなければ None で、理由は reason。"""
    reason: str | None
    """決まらなかった理由。決まったときだけ None（どのコーデックにも当たらない場合も理由を持つ）。"""

    def __post_init__(self) -> None:
        if (self.encoding is None) == (self.reason is None):
            raise ValueError(f"encoding と reason のちょうど一方を持つ: {self.encoding!r}, {self.reason!r}")


def detect(values: Sequence[object], codecs: Sequence[Codec] = CODECS) -> Detection:
    present = [v for v in values if v is not None]
    nulls = len(values) - len(present)
    tallies = tuple(t for t in (_tally(c, present) for c in codecs) if t.matched > 0)
    if not present:
        return Detection(0, nulls, tallies, None, "NULL でない値が無い")
    full = [t for t in tallies if t.decoded == len(present)]
    if len(full) == 1:
        return Detection(len(present), nulls, tallies, full[0].encoding, None)
    if full:
        return Detection(len(present), nulls, tallies, None,
                         f"すべての値を読めるコーデックが {len(full)} 個ある: {', '.join(t.encoding for t in full)}")
    if tallies:
        partial = ", ".join(f"{t.encoding} は {len(present)} 件中 形 {t.matched} 件・読めた {t.decoded} 件"
                            for t in tallies)
        return Detection(len(present), nulls, tallies, None, f"すべての値を読めるコーデックが無い（{partial}）")
    return Detection(len(present), nulls, tallies, None, "どのコーデックの形にも当たらない")


def _tally(codec: Codec, values: Sequence[object]) -> Tally:
    matched = [v for v in values if codec.matches(v)]
    return Tally(codec.encoding.value, len(matched), sum(1 for v in matched if _decodes(codec, v)))


def _decodes(codec: Codec, value: object) -> bool:
    try:
        codec.decode(value)
    except CodecError:
        return False  # 読めなかった数として数える（Tally.decoded）。黙って捨てない
    return True
