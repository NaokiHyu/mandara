"""同じ意味の列：外部キーでつながった列の組から、同じ意味型になるべき列を決める。

Domain 層の純粋な関数。
- 外部キーの列と、参照先の列は同じ物を指す（値が参照先の値そのもの）ので、同じ意味（同じ意味型）。
  参照は推移的にたどる（customer.personid → person.businessentityid → businessentity.businessentityid）。
  役割の違い（請求先と配送先の住所など）は意味型ではなく関係の役割で表す
- 確定に使う根拠は、宣言された外部キー（references の主張）だけ。弱い根拠（列名、値の包含、DB のドメイン型など）で
  まとめると、同じ意味の推移性で誤りが連鎖するので、ここでは使わない
- 伝播（propagate）：組のどこかの列に意味型が付いたら、まだ付いていない組の列に同じ意味型を付ける。
  名前を付けるのは LLM（v1）で、組を決めるのはここ。組の中で食い違う意味型は作らない（v1 が捨てる）
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from mandara.om.invariants import check
from mandara.om.model import Claim, ClaimPredicate, Column, ReviewStatus
from mandara.om.names import qualify, split_qualified
from mandara.om.ontology import Ontology
from mandara.om.value_types import COMPATIBLE

PROPAGATOR = "modeler-meaning 0.1"

Member = tuple[str, str]
"""（表の名前、列名）。"""


@dataclass(frozen=True)
class SameMeaning:
    """外部キーでつながった列の組（2列以上）。"""

    members: frozenset[Member]
    evidence: tuple[str, ...]
    """組をつないだ外部キーの主張 ID（references）。"""


@dataclass(frozen=True)
class MeaningGroups:
    groups: tuple[SameMeaning, ...]

    def of(self, table: str, column: str) -> SameMeaning | None:
        return next((g for g in self.groups if (table, column) in g.members), None)


def same_meaning_groups(claims: Iterable[Claim]) -> MeaningGroups:
    """references の主張の列の対応を、推移的に閉じた組にする。"""
    parent: dict[Member, Member] = {}

    def find(x: Member) -> Member:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    links: list[tuple[Member, str]] = []
    for c in claims:
        if c.predicate != ClaimPredicate.references.value:
            continue
        if c.target is None or c.columns is None or c.target_columns is None:
            raise AssertionError(f"検証済みの references に参照先が無い: {c.id}")
        source = qualify(c.subject.schema_name, c.subject.table)
        target = qualify(c.target.schema_name, c.target.table)
        for a, b in zip(c.columns, c.target_columns, strict=True):
            parent[find((source, a))] = find((target, b))
            links.append(((source, a), c.id))

    members: dict[Member, set[Member]] = {}
    for x in list(parent):
        members.setdefault(find(x), set()).add(x)
    evidence: dict[Member, set[str]] = {}
    for x, claim_id in links:
        evidence.setdefault(find(x), set()).add(claim_id)
    groups = [SameMeaning(frozenset(m), tuple(sorted(evidence[root]))) for root, m in members.items()]
    return MeaningGroups(tuple(sorted(groups, key=lambda g: sorted(g.members))))


def column_families(claims: Iterable[Claim]) -> dict[Member, str]:
    """列の SQL の型族（typed_as の主張から）。"""
    return {(qualify(c.subject.schema_name, c.subject.table), c.subject.column): c.family for c in claims
            if c.predicate == ClaimPredicate.typed_as.value and c.subject.column is not None and c.family is not None}


class InconsistentGroupError(Exception):
    """外部キーで同じ意味の列に、別々の意味型が付いている。"""


def group_semantic_type(ontology: Ontology, group: SameMeaning) -> str | None:
    """組の列に付いている意味型。付いていなければ None。食い違っていればエラー（黙ってどれかを選ばない）。"""
    found = {column.semantic_type for _, column in _columns(ontology, group) if column.semantic_type is not None}
    if len(found) > 1:
        raise InconsistentGroupError(f"外部キーで同じ意味の列に、別々の意味型がある: {sorted(found)} "
                                     f"（{sorted(label(m) for m in group.members)[:5]} …）")
    return next(iter(found), None)


@dataclass(frozen=True)
class Fill:
    member: Member
    semantic_type: str


@dataclass(frozen=True)
class NotFilled:
    """伝播しなかった列。黙って捨てず、実行の記録に残す。"""

    member: Member
    semantic_type: str
    reason: str


@dataclass(frozen=True)
class Propagated:
    ontology: Ontology
    filled: tuple[Fill, ...]
    not_filled: tuple[NotFilled, ...]


class PropagationError(Exception):
    """伝播した結果が検証（4.5）を通らない。伝播の前の確かめ（型、単位、判断の状態）の漏れで、バグ。"""


def propagate(ontology: Ontology, groups: MeaningGroups, families: Mapping[Member, str]) -> Propagated:
    """組の意味型を、意味型がまだ無い組の列に付ける。冪等。families は列の SQL の型族（column_families）。

    付けないもの（理由を残す）：人が判断済みの列、型族と値の型が両立しない列、単位の要る意味型（単位を決められない）、
    組の意味型が人の判断や確信度の無い列から来ているもの（人の判断からの伝播は未対応。T6.1 で決める）。
    """
    semantic_types = ontology.vocabulary.semantic_types or {}
    updates: dict[str, dict[str, Column]] = {}
    filled: list[Fill] = []
    not_filled: list[NotFilled] = []
    for group in groups.groups:
        name = group_semantic_type(ontology, group)
        if name is None:
            continue
        semantic_type = semantic_types[name]
        origins = [c for _, c in _columns(ontology, group) if c.semantic_type == name]
        for table, column in _columns(ontology, group):
            member = (table, column.name)
            if column.semantic_type is not None:
                continue
            if column.status != ReviewStatus.proposed.value:
                not_filled.append(NotFilled(member, name, f"人が判断済み（{column.status}）"))
                continue
            if any(o.status != ReviewStatus.proposed.value or o.confidence is None for o in origins):
                not_filled.append(NotFilled(member, name, "組の意味型が人の判断か確信度の無い列から来ている"
                                                          "（人の判断からの伝播は未対応）"))
                continue
            family = families.get(member)
            if family is None or semantic_type.type not in COMPATIBLE[family]:
                not_filled.append(NotFilled(member, name, f"列の型族 {family} に、値の型 {semantic_type.type} は当てられない"))
                continue
            if semantic_type.requires_unit:
                not_filled.append(NotFilled(member, name, "単位の要る意味型は、単位を決められないので伝播しない"))
                continue
            updates.setdefault(table, {})[column.name] = column.model_copy(update={
                "semantic_type": name, "confidence": min(o.confidence for o in origins if o.confidence is not None),
                "inferred_by": PROPAGATOR, "evidence": sorted({*(column.evidence or []), *group.evidence})})
            filled.append(Fill(member, name))
    if not updates:
        return Propagated(ontology, (), tuple(not_filled))
    tables = tuple(
        t.model_copy(update={"columns": {**(t.columns or {}), **updates[t.name]}}) if t.name in updates else t
        for t in ontology.tables)
    result = Ontology(ontology.vocabulary, tables)
    before = set(check(ontology))
    if introduced := [v for v in check(result) if v not in before]:
        raise PropagationError(f"伝播した結果が検証を通らない: {introduced[:3]}")
    return Propagated(result, tuple(filled), tuple(not_filled))


def label(member: Member) -> str:
    """列を読める1語にする（"schema.table.column"）。"""
    table, column = member
    return qualify(*split_qualified(table), column)


def _columns(ontology: Ontology, group: SameMeaning) -> Sequence[tuple[str, Column]]:
    """組の列のうち、OM にあるもの（範囲外の参照先は OM に無い）。"""
    return [(t.name, column) for t in ontology.tables for name, column in (t.columns or {}).items()
            if (t.name, name) in group.members]

