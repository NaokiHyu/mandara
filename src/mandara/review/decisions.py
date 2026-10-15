"""レビュー：提案を問いにし、人の答えを OM に当てる（仕様 6.4、9.3）。

Domain 層の純粋な関数。
- 問いは、まだ判断されていない（proposed の）注釈ごとに1つ。並びは影響の大きい順（v0 では、表 → 関係・辺 → 列 → enum の値。
  表は関係の多い順）。仕様 9.3 の「下流に生まれるものの数」による並べ替えは、下流（UI、メトリクス）ができてから
- 答えは「承認」「却下」。表の種類が未決の表は、承認するときに人が種類を選ぶ（approved の表には種類が要る）
- 当てた結果は、判断の項目（status、reviewed_by、reviewed_at と、未決だった表の種類）以外を変えない。
  これを review_changes で検査できる（書き込む側の境界で使う）
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

from mandara.om.invariants import check
from mandara.om.model import Column, Edge, EnumValue, Relation, ReviewStatus, Table, TableKind
from mandara.om.names import split_qualified
from mandara.om.ontology import SELF, Annotated, Ontology

KIND_LABELS = {
    TableKind.domain.value: "domain（もの）",
    TableKind.relation.value: "relation（もの同士の関係）",
    TableKind.event.value: "event（出来事）",
    TableKind.derived.value: "derived（導出された表）",
}


@dataclass(frozen=True)
class Question:
    location: str
    """Ontology.annotations() の場所。答えを当てる先。"""
    text: str
    confidence: float | None
    evidence: tuple[str, ...]
    needs_kind: bool
    """承認するには表の種類を選ぶ必要がある。"""


@dataclass(frozen=True)
class Approve:
    kind: TableKind | None = None
    """未決の表を承認するときに選んだ種類。"""


@dataclass(frozen=True)
class Reject:
    pass


Decision = Approve | Reject


class ReviewError(Exception):
    """答えを当てられない（場所が無い、判断済み、種類が要るのに無い、など）。"""


# ---------------------------------------------------------------- 問い


def questions(ontology: Ontology) -> list[Question]:
    names = {t.id: t.name for t in ontology.tables}
    pending = [(where, a) for where, a in ontology.annotations() if a.status == ReviewStatus.proposed.value]
    relation_count = {t.id: len(t.relations or []) for t in ontology.tables}

    def order(item: tuple[str, Annotated]) -> tuple[int, int, str]:
        where, annotation = item
        rank = {Table: 0, Relation: 1, Edge: 1, Column: 2, EnumValue: 3}[type(annotation)]
        return rank, -relation_count.get(_table_id(where), 0), where

    return [_question(where, a, ontology, names) for where, a in sorted(pending, key=order)]


def _question(where: str, annotation: Annotated, ontology: Ontology, names: dict[str, str]) -> Question:
    needs_kind = False
    match annotation:
        case Table(kind=None):
            text = f"表 {annotation.name} の種類はどれですか？"
            needs_kind = True
        case Table(kind=str() as kind):
            text = f"表 {annotation.name} は {KIND_LABELS.get(kind, kind)} ですか？"
        case Relation():
            table = _owner(where, ontology)
            target = table.name if annotation.target == SELF else names.get(annotation.target, annotation.target)
            pairs = ", ".join(f"{p.from_} → {p.to}" for p in annotation.via or [])
            via = f"（{pairs}）" if pairs else (f"（{annotation.via_function}）" if annotation.via_function else "")
            verb = {"has": "参照しますか", "part-of": "の部分ですか", "is-a": "の一種ですか",
                    "classified-as": "に分類されますか", "derived-from": "から導出されますか"}.get(annotation.type, "")
            text = f"{_short(table.name)} は {_short(target)} {verb}？{via}"
        case Edge():
            text = f"表 {_owner(where, ontology).name} の行は、{annotation.type} の辺ですか？"
        case Column():
            text = f"列 {_owner(where, ontology).name}.{annotation.name} を OM に含めますか？"
        case EnumValue():
            domain = split_qualified(where)[1]
            text = f"列ドメイン {domain} の値 {annotation.code} は「{annotation.label}」ですか？"
        case _:
            raise AssertionError(f"想定外の注釈: {type(annotation).__name__}")
    return Question(where, text, annotation.confidence, tuple(annotation.evidence or []), needs_kind)


def _short(name: str) -> str:
    return name if "." not in name else ".".join(split_qualified(name)[-2:])


def _table_id(where: str) -> str:
    return split_qualified(where)[0]


def _owner(where: str, ontology: Ontology) -> Table:
    table = ontology.table(_table_id(where))
    if table is None:
        raise AssertionError(f"注釈の場所に表が無い: {where}")
    return table


# ---------------------------------------------------------------- 答えを当てる


def apply(ontology: Ontology, location: str, decision: Decision, reviewer: str, on: date) -> Ontology:
    """location の注釈に判断を当てた OM を返す。"""
    if not reviewer.strip():
        raise ReviewError("レビューした人の名前が要る")
    found = dict(ontology.annotations()).get(location)
    if found is None:
        raise ReviewError(f"注釈が無い: {location}")
    if found.status != ReviewStatus.proposed.value:
        raise ReviewError(f"判断済み（{found.status}）: {location}")

    update: dict[str, object] = {"reviewed_by": reviewer, "reviewed_at": on}
    match decision:
        case Approve(kind=kind):
            update["status"] = ReviewStatus.approved.value  # model_copy は検証しないので、保持の形（値の文字列）で入れる
            if isinstance(found, Table) and found.kind is None:
                if kind is None:
                    raise ReviewError(f"種類が未決の表を承認するには、種類を選ぶ: {location}")
                update["kind"] = kind.value
            elif kind is not None:
                raise ReviewError(f"種類を選べるのは、種類が未決の表だけ: {location}")
        case Reject():
            update["status"] = ReviewStatus.rejected.value
    after = ontology.replace_annotation(location, found.model_copy(update=update))
    # 答えで検証が通らなくなるなら、その答えは受け付けない（例：grain の無い表を event にする）。人は選び直せる
    if introduced := [v for v in check(after) if v not in set(check(ontology))]:
        raise ReviewError("この答えでは OM が検証を通らない: " + "; ".join(
            f"{v.rule.name} {v.location} {v.detail}".strip() for v in introduced[:3]))
    return after


# ---------------------------------------------------------------- 変えてよいものの検査

_REVIEW_FIELDS = {"status", "reviewed_by", "reviewed_at"}


def review_changes(before: Ontology, after: Ontology) -> list[str]:
    """after が before に対して、判断以外を変えていないかを調べる。問題の一覧を返す（空なら判断だけ）。

    判断として許すのは、proposed の注釈を approved / rejected にし、そのときに reviewed_by / reviewed_at を書くこと。
    種類が未決の表は、承認するときに限り種類を入れてよい。並び順（表、列、関係、enum の値）も変えてはならない。
    """
    problems: list[str] = []
    old, new = list(before.annotations()), list(after.annotations())
    if [w for w, _ in old] != [w for w, _ in new]:
        problems.append("注釈の集まり、または並び順が変わった")
    new_by_place = dict(new)
    for where, a in old:
        b = new_by_place.get(where)
        if b is None:
            continue
        changed = {f for f in type(a).model_fields if f not in _REVIEW_FIELDS and _own(a, f) != _own(b, f)}
        decided_now = a.status == ReviewStatus.proposed.value and b.status != ReviewStatus.proposed.value
        if isinstance(a, Table) and a.kind is None and changed == {"kind"} and b.status == ReviewStatus.approved.value:
            changed = set[str]()  # 未決だった種類を、承認するときに人が決める
        if changed:
            problems.append(f"{where}: 判断以外の項目が変わった {sorted(changed)}")
        if a.status != ReviewStatus.proposed.value:
            if (a.status, a.reviewed_by, a.reviewed_at) != (b.status, b.reviewed_by, b.reviewed_at):
                problems.append(f"{where}: 判断済みの判断が変わった")
        elif decided_now:
            if not (b.reviewed_by and b.reviewed_by.strip()) or b.reviewed_at is None:
                problems.append(f"{where}: 判断したのに reviewed_by / reviewed_at が無い")
        elif (a.reviewed_by, a.reviewed_at) != (b.reviewed_by, b.reviewed_at):
            problems.append(f"{where}: 判断していないのに reviewed_by / reviewed_at が変わった")
    if _without_annotations(before) != _without_annotations(after):
        problems.append("注釈の外（語彙、表の並びなど）が変わった")
    return problems


def _own(annotation: Annotated, field: str) -> object:
    """入れ子の注釈（表の中の列・関係・辺）は別に比べるので、表の項目としては比べない。"""
    if isinstance(annotation, Table) and field in ("columns", "relations", "edge"):
        return None
    return getattr(annotation, field)


def _without_annotations(ontology: Ontology) -> object:
    domains = ontology.vocabulary.column_domains or {}
    return (
        [(name, d.model_copy(update={"values": None})) for name, d in domains.items()],
        list((ontology.vocabulary.aliases or {}).items()),
        [t.id for t in ontology.tables],
    )


def pending_count(ontology: Ontology) -> int:
    return sum(1 for _ in _pending(ontology))


def _pending(ontology: Ontology) -> Iterator[str]:
    return (where for where, a in ontology.annotations() if a.status == ReviewStatus.proposed.value)
