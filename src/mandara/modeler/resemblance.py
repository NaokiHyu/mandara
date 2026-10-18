"""値の似かた（T2.6）を、判断モデルの事実にする：列の意味型の問いに、値の似た列とその意味型、信号ごとの文を添える。

Domain 層の純粋な関数。
- 決めない。似ていることは根拠の 1 つで、決め手ではない（同じ形の別の意味がある：注文番号と請求書番号）。決めるのは判断モデル
- 信号の読み方（近いか、事実の文）は信号のプラグイン（mandara.resemblance.signals）。台帳の method の版が今の信号と違えば
  誤り（StaleMethodError。古い台帳を黙って別の読み方で読まない）
- 添えるのは、どれかの信号が近いと言う列のうち意味型の付いた列が、1 つの意味型に決まり、それが問いの選択肢にあるときだけ。
  近いと言う信号の数の多い順に MAX_NEIGHBORS 個まで（同じなら名前の順）。別々の意味型にまたがれば「似かたは意味を絞らない」
  とだけ添える（特定性）
- 列の値の形（shaped_as）は、判断モデルと LLM の材料にする文（value_shape）
"""

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass

from mandara.modeler.meaning import Member, label
from mandara.om.model import Claim, ClaimPredicate
from mandara.om.names import qualify
from mandara.resemblance.signals import SIGNALS, Measurement, signal_of

MAX_NEIGHBORS = 3
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


@dataclass(frozen=True)
class OptionFacts:
    facts: tuple[str, ...]
    evidence: tuple[str, ...]
    specific: bool
    """似た列が 1 つの意味型を指す（False は、似かたが意味を絞らないという事実、または事実が無い）。"""


def option_facts(found: Resemblances, member: Member, assigned: Mapping[Member, str],
                 options: Collection[str]) -> OptionFacts:
    """列の意味型の問いに添える事実。値の似た列（近いと言う信号が 1 つ以上）のうち、意味型の付いた列を見る：
    - 付いた意味型が 1 つに決まり、それが選択肢にあれば、その列（MAX_NEIGHBORS 個まで）と信号ごとの文
    - 別々の意味型にまたがれば、似かたは意味を絞らない、という事実だけ（特定性。日付どうし、整数の番号どうしは、意味が違っても
      値が似る。似た列を 1 つずつ挙げると、判断モデルが別の意味型に引かれる。段 2 の実験で、受注日が更新日時に引かれた）
    - 意味型の付いた似た列が無いか、その意味型が選択肢に無ければ、何も添えない"""
    typed = sorted((n for n in found.of(member) if n.close > 0 and n.member in assigned),
                   key=lambda n: (-n.close, n.member))
    kinds = sorted({assigned[n.member] for n in typed})
    if len(kinds) > 1:
        return OptionFacts(
            (f"The values of `column` resemble columns of {len(kinds)} different semantic types "
             f"({', '.join(kinds)}): values of this shape occur with several meanings, so the resemblance does not "
             "single out a meaning.",),
            tuple(sorted({r.claim for n in typed for r in n.readings})), False)
    neighbors = [n for n in typed if assigned[n.member] in options]
    facts: list[str] = []
    evidence: list[str] = []
    for n in neighbors[:MAX_NEIGHBORS]:
        measured = {r.method.partition(" ")[0] for r in n.readings}
        missing = [s.name for s in SIGNALS if s.name not in measured]
        facts.append(f"The values of `column` resemble those of the column {label(n.member)}, whose semantic type is "
                     f"{assigned[n.member]}: {n.close} of the {len(n.readings)} measures taken find them as similar as "
                     "values within one column. " + " ".join(r.fact for r in n.readings)
                     + (f" Not measured: {', '.join(missing)}." if missing else ""))
        evidence += [r.claim for r in n.readings]
    return OptionFacts(tuple(facts), tuple(sorted(set(evidence))), bool(facts))


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
