"""構文パターン：値を「原子」の列に一般化し、列の値の形を少数のパターンで要約する（FlashProfile、Potter's Wheel）。

Domain 層の純粋な関数。
- 原子：数字の並び（9）、ASCII の大文字の並び（A）、小文字の並び（a）、そのほかの文字の並び（L。かな・漢字など）、
  空白の並び（_）、記号（1 文字ずつ、そのまま）
- 一般化の段（見本は異なる値なので、値そのものを書く段は持たない。繰り返しの無い値を値のまま書いても短くならない）：
  1 = 原子の種類と長さ（AW00000123 → A{2}9{8}）
  2 = 原子の種類だけ（A+9+）
  3 = 使っている原子の種類の集合だけ（{9A}）
- 段は MDL（最小記述長）で選ぶ：パターンの一覧を書く長さ ＋ パターンを使って値を書く長さ、が最も短い段。
  値がばらばらなら一般的な段、揃っていれば細かい段になる。同じ長さなら細かい段
- 見せる形（Shape）は、上位のパターンと、それに当たる値の割合。値の文字列は書かない（共通の接頭辞のような定数の原子も
  書かない。複数の値に共通でも、姓や秘密の接頭辞のようなデータそのものでありうるため。書くかどうかは仕様で決める）
"""

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

DIGIT, UPPER, LOWER, LETTER, SPACE = "9", "A", "a", "L", "_"
CLASSES = (DIGIT, UPPER, LOWER, LETTER, SPACE)
_SIZE = {DIGIT: 10, UPPER: 26, LOWER: 26, LETTER: 3000, SPACE: 4}
"""原子の種類ごとの文字の数（値を書く長さの見積もり。そのほかの文字は常用の漢字とかなの程度）。"""
_KINDS = len(CLASSES) + 2
"""パターンの 1 原子を書くときの種類の数（原子の種類 ＋ 記号 ＋ 終わり）。"""
LEVELS = (1, 2, 3)
TOP = 5
"""見せるパターンの数。"""

Token = tuple[str, str]
"""（原子の種類、文字列）。記号は種類が "" で、文字列が記号そのもの。"""


def _class(ch: str) -> str:
    if ch.isdigit() and ch.isascii():
        return DIGIT
    if ch.isascii() and ch.isupper():
        return UPPER
    if ch.isascii() and ch.islower():
        return LOWER
    if ch.isspace():
        return SPACE
    if ch.isalpha() or ch.isdigit():
        return LETTER
    return ""


def tokens(value: str) -> list[Token]:
    out: list[Token] = []
    for ch in value:
        kind = _class(ch)
        if kind and out and out[-1][0] == kind:
            out[-1] = (kind, out[-1][1] + ch)
        else:
            out.append((kind, ch))
    return out


def _escape(ch: str) -> str:
    """記号は \\ を付けて書く（原子の記号や {} + と取り違えない）。"""
    return "\\" + ch


def pattern(value: str, level: int) -> str:
    """値の、段 level のパターン。"""
    toks = tokens(value)
    if level == 1:
        return "".join(f"{k}{{{len(t)}}}" if k else _escape(t) for k, t in toks)
    if level == 2:
        return "".join(f"{k}+" if k else _escape(t) for k, t in toks)
    if level == 3:
        return "{" + "".join(sorted({k if k else _escape(t) for k, t in toks})) + "}"
    raise ValueError(f"一般化の段は {LEVELS} のどれか: {level}")


def distribution(values: Sequence[str], level: int) -> Counter[str]:
    return Counter(pattern(v, level) for v in values)


# ---------------------------------------------------------------- MDL


def _gamma(n: int) -> float:
    """正の整数を書く長さ（Elias のガンマ符号）。"""
    return 2 * math.floor(math.log2(n)) + 1


def _model_bits(toks: Sequence[Token], level: int, raw: str) -> float:
    """パターンを 1 つ書く長さ。"""
    if level == 3:
        symbols = {k if k else t for k, t in toks}
        return (len(symbols) + 1) * math.log2(_KINDS) + 8 * sum(1 for s in symbols if s not in _SIZE)
    bits = (len(toks) + 1) * math.log2(_KINDS)
    for k, t in toks:
        if not k:
            bits += 8
        elif level == 1:
            bits += _gamma(len(t))
    return bits


def _fill_bits(toks: Sequence[Token], level: int, raw: str) -> float:
    """パターンを知っているとき、値を書く長さ。"""
    if level == 3:
        alphabet = sum(_SIZE[s] if s in _SIZE else 1 for s in {k if k else t for k, t in toks})
        return _gamma(len(raw) + 1) + len(raw) * math.log2(max(alphabet, 2))
    bits = 0.0
    for k, t in toks:
        if k:
            bits += len(t) * math.log2(_SIZE[k]) + (_gamma(len(t)) if level == 2 else 0)
    return bits


def description_length(values: Sequence[str], level: int) -> float:
    """段 level で値の一覧を書く長さ（ビット）：パターンの一覧 ＋ 値ごとに（どのパターンか ＋ パターンを埋める文字）。"""
    by_pattern: dict[str, list[str]] = {}
    for v in values:
        by_pattern.setdefault(pattern(v, level), []).append(v)
    n = len(values)
    terms = [_gamma(len(by_pattern) + 1)]
    for members in by_pattern.values():
        terms.append(_model_bits(tokens(members[0]), level, members[0]))
        terms.append(len(members) * -math.log2(len(members) / n))
        terms += [_fill_bits(tokens(v), level, v) for v in members]
    return math.fsum(terms)  # 正確に丸めた和（足す順に依らない。同じ長さの段を、値の並びで取り違えない）


def mdl_level(values: Sequence[str]) -> int:
    """記述長が最も短い段（同じなら細かい段）。値が無ければ誤り（形の無い列は測らない）。"""
    if not values:
        raise ValueError("値の無い列の段は決められない")
    return min(LEVELS, key=lambda level: (description_length(values, level), level))


# ---------------------------------------------------------------- 見せる形


@dataclass(frozen=True)
class Shape:
    level: int
    """MDL で選んだ段。"""
    patterns: tuple[tuple[str, float], ...]
    """その段の上位のパターンと、それに当たる値の割合（多い順。同じならパターンの順）。"""


def shape(values: Sequence[str]) -> Shape:
    level = mdl_level(values)
    found = distribution(values, level)
    n = len(values)
    ranked = sorted(found.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP]
    return Shape(level, tuple((p, count / n) for p, count in ranked))


def overlap(a: Counter[str], b: Counter[str]) -> float:
    """2 つのパターンの分布の重なり（1 − 全変動距離）。0〜1。どちらかが空なら 0。"""
    na, nb = sum(a.values()), sum(b.values())
    if na == 0 or nb == 0:
        return 0.0
    return sum(min(a[p] / na, b[p] / nb) for p in a.keys() & b.keys())
