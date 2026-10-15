"""評価 v0：提案した OM を正解 OM と突き合わせ、観点ごとの点数を出す（仕様 17.1、17.2）。

Domain 層の純粋な関数。
- 採点の前に、両方の OM を検証（4.5）に通す。壊れた OM（参照先が無い関係など）で、事実が黙って落ちたり
  None の混じった事実ができたりしないように
- どの観点も「事実の一覧」の突き合わせとして同じ形で測る。観点 = OM から事実を取り出す関数
  - 復元率 = 正解の事実のうち、提案に含まれた割合（正解が0件なら測れない＝None）
  - 誤提案率 = 提案した事実のうち、正解に無かったものの割合（提案が0件なら None）。重複した提案は、2つ目以降を誤りに数える
- 突き合わせは安定 ID ではなくソース上の名前で行う（表の name、列名、関係の結合）。ID の採番方式に点数を左右させない
- 未決（表の種類が None など）は事実を出さない。復元率は下がるが、誤提案には数えない
- 列ドメインは名前で比べない（名前は任意）。値の型（column_types）と、同じドメインに属する列の組
  （column_domain_pairs）を組で読む。型だけでは別の意味の同じ型を区別できない。組の数はドメインの大きさの2乗で
  増えるので、大きなドメインが点を支配し、1列だけのドメインは組を出さない（列ごとの平均の指標は今後の課題）
- 関係は2段で測る。端点（どの表のどの列を結ぶか）と、型（has / part-of / classified-as …）。
  型は、端点が正解と提案の両方にある関係だけで測る（端点の外れを型でも数えない）
- 正解 OM は一部の表しか持たない。正解の表の外へ出る提案（表、関係・辺の参照先）は採点せず、件数だけ数える
"""

from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import cast

from mandara.om.invariants import Violation, check
from mandara.om.model import Relation, RelationType, Table
from mandara.om.ontology import SELF, Ontology

Fact = tuple[object, ...]


@dataclass(frozen=True)
class AspectScore:
    aspect: str
    gold: int
    proposed: int
    correct: int
    missed: tuple[str, ...]
    """正解にあって、提案に無い事実。"""
    wrong: tuple[str, ...]
    """提案にあって、正解に無い事実（重複した提案を含む）。"""

    @property
    def recall(self) -> float | None:
        return self.correct / self.gold if self.gold else None

    @property
    def false_rate(self) -> float | None:
        return (self.proposed - self.correct) / self.proposed if self.proposed else None


@dataclass(frozen=True)
class Report:
    aspects: tuple[AspectScore, ...]
    unscored_tables: int
    """正解の範囲の外の表の提案（採点しない）。"""
    unscored_relations: int
    """正解の範囲の外の表を参照する関係・辺の提案（採点しない）。"""

    def aspect(self, name: str) -> AspectScore:
        for a in self.aspects:
            if a.aspect == name:
                return a
        raise KeyError(f"観点が無い: {name}（ある観点: {[a.aspect for a in self.aspects]}）")


class InvalidForScoringError(Exception):
    """採点する OM が検証（4.5）を通らない。"""

    def __init__(self, which: str, violations: Sequence[Violation]) -> None:
        lines = "\n".join(f"  - {v.location}: {v.rule.name} {v.detail}" for v in violations[:10])
        super().__init__(f"{which}の OM が検証を通らない（{len(violations)} 件）:\n{lines}")
        self.violations = tuple(violations)


# ---------------------------------------------------------------- 事実の取り出し


@dataclass(frozen=True)
class _View:
    """採点の範囲（正解の表の名前）に絞った OM。検証済みであること（参照先は必ず解決できる）。"""

    ontology: Ontology
    scope: frozenset[str]

    def tables(self) -> Iterator[Table]:
        return (t for t in self.ontology.tables if t.name in self.scope)

    def target_name(self, table: Table, relation: Relation) -> str:
        if relation.target == SELF:
            return table.name
        found = self.ontology.table(relation.target)
        if found is None:
            raise AssertionError(f"検証済みの OM で参照先が解決できない: {relation.id}")
        return found.name

    def endpoint(self, table: Table, relation: Relation) -> Fact | None:
        """関係の端点。参照先が範囲外なら None（採点しない）。"""
        target = self.target_name(table, relation)
        return _endpoint(table.name, target, relation) if target in self.scope else None

    def relations(self) -> Iterator[tuple[Relation, Fact]]:
        for table in self.tables():
            for relation in table.relations or []:
                if (endpoint := self.endpoint(table, relation)) is not None:
                    yield relation, endpoint


def _endpoint(source: str, target: str, relation: Relation) -> Fact:
    """関係が結ぶもの。形は関係型の検証（RELATION_SHAPE）で決まっている3通りのどれか。"""
    if relation.type == RelationType.derived_from.value:
        if relation.function is None or not relation.columns:
            raise AssertionError(f"検証済みの derived-from に function / columns が無い: {relation.id}")
        return (source, target, f"function {relation.function}", frozenset(relation.columns))
    if relation.via and relation.via_function is None:
        return (source, target, frozenset((p.from_, p.to) for p in relation.via))
    if relation.via_function is not None and not relation.via:
        return (source, target, f"via_function {relation.via_function}")
    raise AssertionError(f"検証済みの関係の形が想定外: {relation.id}")


def _tables(view: _View) -> Iterator[Fact]:
    return ((t.name,) for t in view.tables())


def _kind(view: _View) -> Iterator[Fact]:
    return ((t.name, t.kind) for t in view.tables() if t.kind is not None)


def _primary_key(view: _View) -> Iterator[Fact]:
    """主キーは列の集合として比べる（列の並びは識別の意味に関わらない）。"""
    return ((t.name, frozenset(t.identity.primary)) for t in view.tables() if t.identity is not None)


def _alternate_keys(view: _View) -> Iterator[Fact]:
    for t in view.tables():
        for key in (t.identity.alternates or []) if t.identity is not None else []:
            yield (t.name, frozenset(key.columns))


def _relation_endpoints(view: _View) -> Iterator[Fact]:
    return (endpoint for _, endpoint in view.relations())


def _relation_types(view: _View) -> Iterator[Fact]:
    for relation, endpoint in view.relations():
        if relation.type == RelationType.has.value:
            yield (endpoint, relation.type, relation.has_kind)
        else:
            yield (endpoint, relation.type)


def _edges(view: _View) -> Iterator[Fact]:
    """辺は、型と、始点・終点の参照の端点で表す（同じ表への2本の参照を取り違えない）。"""
    for t in view.tables():
        if t.edge is None:
            continue
        by_id = {r.id: r for r in t.relations or []}
        ends = [view.endpoint(t, by_id[t.edge.from_]), view.endpoint(t, by_id[t.edge.to])]
        if all(e is not None for e in ends):
            yield (t.name, t.edge.type, *ends)


def _column_types(view: _View) -> Iterator[Fact]:
    """列の値の型（列ドメインの型）。ドメインの名前の付け方には左右されない。"""
    domains = view.ontology.vocabulary.column_domains or {}
    for t in view.tables():
        for name, column in (t.columns or {}).items():
            domain = domains.get(column.domain or "")
            if domain is not None:
                yield (t.name, name, domain.type)


def _column_domain_pairs(view: _View) -> Iterator[Fact]:
    """同じドメイン（同じ意味）に属する列の組。名前の付け方には左右されず、「同じ意味をまとめられたか」を測る。"""
    groups: dict[str, list[tuple[str, str]]] = {}
    for t in view.tables():
        for name, column in (t.columns or {}).items():
            if column.domain is not None:
                groups.setdefault(column.domain, []).append((t.name, name))
    for members in groups.values():
        ordered = sorted(members)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                yield (a, b)


def _grain_present(view: _View) -> Iterator[Fact]:
    """grain は文で書くので中身は比べられない。書いてあるかだけを見る（観点の名前もそう読めるように）。"""
    return ((t.name,) for t in view.tables() if t.grain is not None and t.grain.strip())


ASPECTS: dict[str, Callable[[_View], Iterator[Fact]]] = {
    "tables": _tables,
    "kind": _kind,
    "primary_key": _primary_key,
    "alternate_keys": _alternate_keys,
    "relation_endpoints": _relation_endpoints,
    "relation_types": _relation_types,
    "edges": _edges,
    "column_types": _column_types,
    "column_domain_pairs": _column_domain_pairs,
    "grain_present": _grain_present,
}


# ---------------------------------------------------------------- 採点


def facts(ontology: Ontology, scope: frozenset[str], aspect: str) -> list[Fact]:
    """ある観点の事実の一覧（重複を含む）。テストと調査のために公開する。"""
    return list(ASPECTS[aspect](_View(ontology, scope)))


def score(proposed: Ontology, gold: Ontology) -> Report:
    for which, ontology in (("提案", proposed), ("正解", gold)):
        if violations := check(ontology):
            raise InvalidForScoringError(which, violations)
    scope = frozenset(t.name for t in gold.tables)
    gold_view, proposed_view = _View(gold, scope), _View(proposed, scope)
    matched_endpoints = set(_relation_endpoints(gold_view)) & set(_relation_endpoints(proposed_view))

    aspects: list[AspectScore] = []
    for name, extract in ASPECTS.items():
        expected, actual = list(extract(gold_view)), list(extract(proposed_view))
        if name == "relation_types":
            expected = [f for f in expected if f[0] in matched_endpoints]
            actual = [f for f in actual if f[0] in matched_endpoints]
        if duplicated := [f for f, n in Counter(expected).items() if n > 1]:
            raise AssertionError(f"正解の {name} に重複した事実がある: {list(map(_show, duplicated))}")
        expected_set, actual_counts = set(expected), Counter(actual)
        wrong = [f for f in actual_counts if f not in expected_set]
        duplicates = [f for f, n in actual_counts.items() if f in expected_set and n > 1 for _ in range(n - 1)]
        aspects.append(AspectScore(
            aspect=name,
            gold=len(expected),
            proposed=len(actual),
            correct=len(expected_set & set(actual_counts)),
            missed=tuple(sorted(_show(f) for f in expected_set - set(actual_counts))),
            wrong=tuple(sorted([*(_show(f) for f in wrong for _ in range(actual_counts[f])),
                                *(f"重複 {_show(f)}" for f in duplicates)])),
        ))

    unscored_relations = sum(
        1
        for t in proposed_view.tables()
        for r in t.relations or []
        if proposed_view.endpoint(t, r) is None
    )
    return Report(
        aspects=tuple(aspects),
        unscored_tables=sum(t.name not in scope for t in proposed.tables),
        unscored_relations=unscored_relations,
    )


def _show(fact: object) -> str:
    """事実を、順序の決まった読める文字列にする（集合は並べ替える）。"""
    if isinstance(fact, frozenset):
        return "{" + ", ".join(sorted(_show(x) for x in cast(frozenset[object], fact))) + "}"
    if isinstance(fact, tuple):
        return "(" + ", ".join(_show(x) for x in cast(tuple[object, ...], fact)) + ")"
    return str(fact)
