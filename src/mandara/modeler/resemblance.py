"""値の似かた（T2.6）の主張を、モデラーが読む形にする。

Domain 層の純粋な関数。
- 列ごとに、値の似た列と、信号ごとの読み（近いか、事実の文）。決めない。決めるのは判断モデル（mandara.modeler.same_meaning）
- 信号の読み方は信号のプラグイン（mandara.resemblance.signals）。台帳の method の版が今の信号と違えば誤り
  （StaleMethodError。古い台帳を黙って別の読み方で読まない）
- 列の値の形（shaped_as）は、判断モデルと LLM の材料にする文（value_shape）
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from mandara.modeler.meaning import Member
from mandara.om.model import Claim, ClaimPredicate
from mandara.om.names import qualify
from mandara.resemblance.signals import Measurement, signal_of

SHAPE_NOTATION = ("Value shapes are patterns of the distinct values, with the share of distinct values in a sample, "
                  "most common first: 9 = digits, A = upper-case letters, "
                  "a = lower-case letters, L = other letters, _ = spaces; {n} = exactly n of them, + = one or more; "
                  "\\x = the symbol x; {...} = only the set of kinds of characters is kept.")
"""value_shape の書き方の説明（材料に 1 回だけ置く）。"""
SHAPE_PATTERNS = 2
"""材料に書くパターンの数（材料は表の問いのたびに送るので短くする）。"""


@dataclass(frozen=True)
class Reading:
    method: str
    close: bool
    fact: str
    claim: str


@dataclass(frozen=True)
class Neighbor:
    member: Member
    readings: tuple[Reading, ...]

    @property
    def close(self) -> int:
        return sum(r.close for r in self.readings)


@dataclass(frozen=True)
class Resemblances:
    by_member: Mapping[Member, tuple[Neighbor, ...]]

    def of(self, member: Member) -> tuple[Neighbor, ...]:
        return self.by_member.get(member, ())


def index(claims: Iterable[Claim]) -> Resemblances:
    """resembles の主張を、列ごとの相手の一覧にする（組は両向きに引ける）。"""
    found: dict[tuple[Member, Member], list[Reading]] = {}
    for c in claims:
        if c.predicate != ClaimPredicate.resembles.value:
            continue
        if (c.target is None or not c.columns or not c.target_columns or c.method is None or c.score is None
                or c.baseline is None):
            raise AssertionError(f"検証済みの resembles に値が無い: {c.id}")
        signal = signal_of(c.method)
        m = Measurement(c.score, c.baseline)
        a = (qualify(c.subject.schema_name, c.subject.table), c.columns[0])
        b = (qualify(c.target.schema_name, c.target.table), c.target_columns[0])
        reading = Reading(c.method, signal.close(m), signal.fact(m), c.id)
        found.setdefault((a, b), []).append(reading)
        found.setdefault((b, a), []).append(reading)
    by_member: dict[Member, list[Neighbor]] = {}
    for (a, b), readings in sorted(found.items()):
        by_member.setdefault(a, []).append(Neighbor(b, tuple(sorted(readings, key=lambda r: r.method))))
    return Resemblances({m: tuple(n) for m, n in by_member.items()})


def tables_of(claim: Claim) -> tuple[str, ...]:
    """主張が材料になる表。ふつうは主語の表だけ。値の似かた（resembles）は組の両方の表：組は名前の順で片方の表に 1 つだけ
    置くが、どちらの表の問いでも事実と根拠になる（後ろの表の答えの根拠も、その表の主張として確かめられるように）。"""
    own = qualify(claim.subject.schema_name, claim.subject.table)
    if claim.predicate != ClaimPredicate.resembles.value:
        return (own,)
    if claim.target is None:
        raise AssertionError(f"検証済みの resembles に target が無い: {claim.id}")
    other = qualify(claim.target.schema_name, claim.target.table)
    return (own,) if other == own else (own, other)


def value_shape(claim: Claim) -> str:
    """列の値の形を 1 行に（パターンと割合、異なる値の数）。"""
    if claim.predicate != ClaimPredicate.shaped_as.value or claim.patterns is None or claim.pattern_shares is None:
        raise ValueError(f"shaped_as の主張ではない: {claim.id}")
    shown = ", ".join(f"{p} {s:.0%}" for p, s in list(zip(claim.patterns, claim.pattern_shares, strict=True))
                      [:SHAPE_PATTERNS])
    rest = 1 - sum(claim.pattern_shares[:SHAPE_PATTERNS])
    return (f"{shown}" + (f", other patterns {rest:.0%}" if rest > 0.005 else "")
            + f" ({claim.distinct_values} distinct values)")
