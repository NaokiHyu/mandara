"""列の組の信号（プラグイン）：2 つの列の値の見本から、同じ母集団の値らしさを測る。

Domain 層の純粋な関数。1 つのプラグインが、測り方（measure）と読み方（近いか、事実の文）をまとめて持つ。
- どの信号も、測った値（score）と「同じ母集団ならこのくらい」の基準（baseline）を返す。数だけでは近いと言えないので、
  基準と組で主張にし、組で読む
- 測り方は決定的（乱数は固定）。同じ見本・同じ版なら同じ値
- 主張の method は「名前 版」。読むときは名前で引き、版が違えば誤り（古い台帳を黙って別の読み方で読まない）
- 足すときは SIGNALS に並べる。組み合わせ（どの信号をどれだけ信じるか）は判断モデルが決める（mandara.modeler.resemblance）
"""

import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from mandara.resemblance.features import features
from mandara.resemblance.shape import distribution, mdl_level, overlap
from mandara.resemblance import classifier


@dataclass(frozen=True)
class Prepared:
    """列の見本を、信号が使う形にしたもの（列ごとに 1 回。プロセスをまたいで渡す）。"""

    values: tuple[str, ...]
    """値の文字列表現（見本の順）。"""
    level: int
    """構文パターンの、MDL で選んだ段。"""
    vectors: tuple[tuple[float, ...], ...]
    """値ごとの特徴量（values の順）。"""


def prepare(values: Sequence[str]) -> Prepared:
    return Prepared(tuple(values), mdl_level(values), tuple(features(v) for v in values))


@dataclass(frozen=True)
class Measurement:
    score: float
    baseline: float


class Signal(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def costly(self) -> bool:
        """重い信号（安い信号で絞った組だけで測る）。"""
        ...

    @property
    def min_values(self) -> int:
        """両方の列に要る値の数（足りない組は測らない）。"""
        ...

    @property
    def higher_is_closer(self) -> bool: ...

    def measure(self, a: Prepared, b: Prepared) -> Measurement: ...

    def close(self, m: Measurement) -> bool:
        """同じ母集団と見てよいほど近いか（基準と比べて）。"""
        ...

    def fact(self, m: Measurement) -> str:
        """判断モデルに渡す事実の文（英語。値は埋め込まない）。"""
        ...


def method(signal: Signal) -> str:
    return f"{signal.name} {signal.version}"


def closeness(signal: Signal, m: Measurement) -> float:
    """大きいほど近い（並べるため）。"""
    return m.score if signal.higher_is_closer else -m.score


def _halves(values: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """見本を 1 つおきに 2 つに分ける（見本の順は値に依らず散らばっているので、2 つは同じ母集団の見本）。"""
    return tuple(values[0::2]), tuple(values[1::2])


# ---------------------------------------------------------------- 構文パターン


@dataclass(frozen=True)
class PatternSignal:
    """2 つの列の値のパターンの分布の重なり。段は両方の列の MDL の段の粗い方（1 以上）。基準は、各列の半分どうしの重なりの小さい方。
    基準が tolerance 以下（列の中でもパターンが揃わない）なら、近いとは言わない（基準が情報を持たないので、相手が何でも
    「基準並み」になってしまう）。半分が 4 値以上になる数から測る。"""

    name: str = "pattern"
    version: str = "0.1"
    costly: bool = False
    min_values: int = 8
    higher_is_closer: bool = True
    tolerance: float = 0.1

    def measure(self, a: Prepared, b: Prepared) -> Measurement:
        level = max(a.level, b.level, 1)
        score = overlap(distribution(a.values, level), distribution(b.values, level))
        baseline = min(overlap(*(distribution(h, level) for h in _halves(x.values))) for x in (a, b))
        return Measurement(score, baseline)

    def close(self, m: Measurement) -> bool:
        return m.baseline > self.tolerance and m.score >= m.baseline - self.tolerance

    def fact(self, m: Measurement) -> str:
        return (f"Patterns of the values (character classes, and lengths where they are regular) overlap {m.score:.0%}; "
                f"two halves of the same column overlap {m.baseline:.0%}.")


# ---------------------------------------------------------------- 圧縮


def compressed_size(values: Sequence[str]) -> int:
    """値を改行でつないで、ヘッダの無い deflate で圧縮した大きさ（固定のヘッダが少量の値の距離をぶらさないように）。"""
    packer = zlib.compressobj(9, zlib.DEFLATED, -15)
    data = "\n".join(values).encode("utf-8")
    return len(packer.compress(data) + packer.flush())


def ncd(a: Sequence[str], b: Sequence[str]) -> float:
    """正規化圧縮距離 (C(ab) − min(C(a), C(b))) / max(C(a), C(b))。つなぐ順で C(ab) が変わるので、両方の順の平均（対称）。"""
    ca, cb = compressed_size(a), compressed_size(b)
    joined = (compressed_size([*a, *b]) + compressed_size([*b, *a])) / 2
    return (joined - min(ca, cb)) / max(ca, cb)


@dataclass(frozen=True)
class NcdSignal:
    """NCD。大きさの偏りを避けるため、両方の列の見本を同じ数にそろえ、半分ずつで測る：
    値 = (NCD(A₁, B₂) + NCD(A₂, B₁)) / 2、基準 = 各列の半分どうしの NCD の平均。
    基準を大きい方にすると、圧縮の効く列（値の種類の少ない数量など）と効かない列の組が、どちらの列とも近く見える。
    値が乱数に近い列（番号など）では、どの組も 1 に近く、差が出にくい（基準との差を小さく見る）。"""

    name: str = "ncd"
    version: str = "0.1"
    costly: bool = False
    min_values: int = 16
    """少ない値では、圧縮の形式の固定の部分が距離をぶらす。"""
    higher_is_closer: bool = False
    tolerance: float = 0.02

    def measure(self, a: Prepared, b: Prepared) -> Measurement:
        n = min(len(a.values), len(b.values))
        a1, a2 = _halves(a.values[:n])
        b1, b2 = _halves(b.values[:n])
        return Measurement((ncd(a1, b2) + ncd(a2, b1)) / 2, (ncd(a1, a2) + ncd(b1, b2)) / 2)

    def close(self, m: Measurement) -> bool:
        return m.score <= m.baseline + self.tolerance

    def fact(self, m: Measurement) -> str:
        return (f"Compression distance between the values is {m.score:.2f}; between two halves of the same column it is "
                f"{m.baseline:.2f} on average (0 = the same structure, 1 = nothing shared).")


# ---------------------------------------------------------------- 分類器 2 標本検定


@dataclass(frozen=True)
class C2stSignal:
    """分類器 2 標本検定：値ごとの特徴量で、値がどちらの列のものかを当てる k 近傍（1 つ抜き）の正答率（両方の見本を同じ数に
    そろえる）。基準は、ラベルを並べ替えた正答率の 95% 分位点（同じ母集団なら、偶然に超える確率が 5%）。
    基準より低ければ、見分けられない（同じ母集団と矛盾しない）。"""

    name: str = "c2st"
    version: str = "0.1"
    costly: bool = True
    min_values: int = classifier.K // 2 + 2
    higher_is_closer: bool = False

    def measure(self, a: Prepared, b: Prepared) -> Measurement:
        n = min(len(a.vectors), len(b.vectors))
        found = classifier.two_sample(a.vectors[:n], b.vectors[:n])
        return Measurement(found.accuracy, found.critical)

    def close(self, m: Measurement) -> bool:
        return m.score <= m.baseline

    def fact(self, m: Measurement) -> str:
        return (f"A nearest-neighbour classifier on the shape of each value tells the two columns apart {m.score:.0%} of "
                f"the time (chance is about 50%; above {m.baseline:.0%} would show different kinds of values).")


SIGNALS: tuple[Signal, ...] = (PatternSignal(), NcdSignal(), C2stSignal())
BY_NAME: dict[str, Signal] = {s.name: s for s in SIGNALS}
assert len(BY_NAME) == len(SIGNALS), "信号の名前は重ならない"


class StaleMethodError(ValueError):
    """主張の method の版が、今の信号の版と違う（台帳を作り直す）。"""


def signal_of(method_text: str) -> Signal:
    name, _, version = method_text.partition(" ")
    signal = BY_NAME.get(name)
    if signal is None:
        raise ValueError(f"知らない信号: {method_text!r}")
    if signal.version != version:
        raise StaleMethodError(f"信号 {name} の版が違う: 主張は {version}、今は {signal.version}（台帳を作り直す）")
    return signal
