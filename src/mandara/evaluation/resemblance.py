"""値の似かたの評価（T2.6）：信号ごとに、「近い」と言った組が、正解 OM で同じ意味型の列の組（兄弟）だったかを測る。

Domain 層の純粋な関数。
- 正解の組：正解 OM の表の列で、同じ意味型を持つ 2 列（表をまたぐ組も、同じ表の組も）。範囲は正解 OM の表の列だけ
- 信号が「近い」と言った組（主張にした組のうち、信号の読み方で近いもの）を数える：
  適合率 = 近いと言った組のうち兄弟、再現率 = 兄弟のうち近いと言った組
- 「つながっていない兄弟」：宣言された外部キーと、値の包含から参照とみなした結合（mandara.modeler.joins）で、推移的にも
  つながらない兄弟。値の包含では拾えない兄弟（行の guid どうし、更新日時どうし、金額どうし）を、値の似かたが拾えるか
- 信号の一致：k 個以上の信号が近いと言った組も、同じ数で測る（組み合わせの手がかり。決めるのは判断モデル）
- 分母は 2 通り出す：正解の兄弟の組すべてと、測れる組（両方が表の列で、比べる型族の同じ群。ビューの列、真偽値・バイト列の列、
  群をまたぐ組は、作りの上で測らない）
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import combinations

from mandara.modeler import joins as joins_modeler
from mandara.modeler.meaning import Member
from mandara.extractors.resemblance import group_of
from mandara.om.model import Claim, ClaimPredicate, ObjectKind, TypeFamily
from mandara.om.names import qualify
from mandara.om.ontology import Ontology
from mandara.resemblance.signals import SIGNALS, Measurement, signal_of

Pair = tuple[Member, Member]


@dataclass(frozen=True)
class Part:
    name: str
    close: int
    """近いと言った組（正解の範囲の中）。"""
    siblings: int
    """そのうち兄弟の組。"""
    unlinked: int
    """そのうち、つながっていない兄弟の組。"""
    columns_hit: int
    """兄弟のある列のうち、近いと言った相手に兄弟が 1 つ以上ある列（分類の問いに効くのは、列ごとに 1 つの正しい手がかり）。"""

    def precision(self) -> float | None:
        return self.siblings / self.close if self.close else None


@dataclass(frozen=True)
class ResemblanceScore:
    columns: int
    """正解 OM の、意味型のある列。"""
    sibling_pairs: int
    unlinked_pairs: int
    kept_pairs: int
    """主張にした組（正解の範囲の中）。"""
    columns_with_siblings: int
    comparable_pairs: int
    """兄弟の組のうち、測れる組。"""
    parts: tuple[Part, ...]

    def comparable_recall(self, part: Part) -> float | None:
        return part.siblings / self.comparable_pairs if self.comparable_pairs else None

    def column_recall(self, part: Part) -> float | None:
        return part.columns_hit / self.columns_with_siblings if self.columns_with_siblings else None

    def recall(self, part: Part) -> float | None:
        return part.siblings / self.sibling_pairs if self.sibling_pairs else None

    def unlinked_recall(self, part: Part) -> float | None:
        return part.unlinked / self.unlinked_pairs if self.unlinked_pairs else None


def _pair(a: Member, b: Member) -> Pair:
    return (a, b) if a <= b else (b, a)


def _linked(claims: Sequence[Claim]) -> dict[Member, Member]:
    """宣言された外部キーと、参照とみなした値の包含でつながる列の組（union-find の根）。"""
    parent: dict[Member, Member] = {}

    def find(x: Member) -> Member:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    chosen = {c.claim.id for c in joins_modeler.choose(claims)}
    for c in claims:
        if c.target is None or not (c.predicate == ClaimPredicate.references.value or c.id in chosen):
            continue
        for a, b in zip(c.columns or [], c.target_columns or [], strict=True):
            parent[find((qualify(c.subject.schema_name, c.subject.table), a))] = find(
                (qualify(c.target.schema_name, c.target.table), b))
    return {x: find(x) for x in list(parent)}


def score(claims: Sequence[Claim], gold: Ontology) -> ResemblanceScore:
    types: dict[Member, str] = {(t.name, name): c.semantic_type for t in gold.tables
                                for name, c in (t.columns or {}).items() if c.semantic_type is not None}
    siblings = {_pair(a, b) for a, b in combinations(sorted(types), 2) if types[a] == types[b]}
    roots = _linked(claims)
    unlinked = {p for p in siblings if roots.get(p[0], p[0]) != roots.get(p[1], p[1])}
    readings: dict[Pair, dict[str, bool]] = {}
    for c in claims:
        if c.predicate != ClaimPredicate.resembles.value:
            continue
        if c.target is None or not c.columns or not c.target_columns or c.method is None or c.score is None \
                or c.baseline is None:
            raise AssertionError(f"検証済みの resembles に値が無い: {c.id}")
        a = (qualify(c.subject.schema_name, c.subject.table), c.columns[0])
        b = (qualify(c.target.schema_name, c.target.table), c.target_columns[0])
        if a not in types or b not in types:
            continue
        signal = signal_of(c.method)
        readings.setdefault(_pair(a, b), {})[signal.name] = signal.close(Measurement(c.score, c.baseline))

    def part(name: str, close: Iterable[Pair]) -> Part:
        found = set(close)
        hit = {m for p in found & siblings for m in p}
        return Part(name, len(found), len(found & siblings), len(found & unlinked), len(hit))

    parts = [part(s.name, (p for p, r in readings.items() if r.get(s.name))) for s in SIGNALS]
    parts += [part(f"{k}+ agree", (p for p, r in readings.items() if sum(r.values()) >= k))
              for k in range(1, len(SIGNALS) + 1)]
    with_siblings = {m for p in siblings for m in p}
    groups = _groups(claims)
    comparable = {p for p in siblings if groups.get(p[0]) is not None and groups.get(p[0]) == groups.get(p[1])}
    return ResemblanceScore(len(types), len(siblings), len(unlinked), len(readings), len(with_siblings),
                            len(comparable), tuple(parts))


def _groups(claims: Sequence[Claim]) -> dict[Member, str]:
    """測る列（表の列で、比べる型族のもの）の型族の群（mandara.extractors.resemblance.GROUPS）。"""
    tables = {qualify(c.subject.schema_name, c.subject.table) for c in claims
              if c.predicate == ClaimPredicate.declared_as.value and c.object_kind == ObjectKind.table.value}
    out: dict[Member, str] = {}
    for c in claims:
        if c.predicate != ClaimPredicate.typed_as.value or c.subject.column is None or c.family is None:
            continue
        table = qualify(c.subject.schema_name, c.subject.table)
        group = group_of(TypeFamily(c.family))
        if table in tables and group is not None:
            out[(table, c.subject.column)] = group
    return out
