"""モデラー v1：規則では決まらないものを推論する（仕様 6.3、6.7）。答えの形と、答えを当てる検証。

Domain 層の純粋な関数（モデルの呼び出しは Application がポート経由で行う）。
問いは分類（判断モデル）と生成（LLM）に分けてある（mandara.modeler.classify、T3.7）。読んだ答えは TableAnswer に組み立てて、ここで当てる。
- brief：表の主張を、根拠の主張 ID つきで要約する（LLM への問いの材料）。自由文（名前、コメント、式、前の答えの説明）はすべて
  JSON の文字列にして、データの区画に入れる（DB の中身や前の答えに埋め込まれた指示を、指示として読ませない）
- apply_answer：答えを検証して v0 の表に当てる。答えは信頼できない入力。答えの各項目は、当たる（applied）か、
  理由つきで捨てる（dropped）かのどちらか。黙って消える項目は無い
  - 根拠の ID は渡した主張に含まれるものだけ。enum の値はコメントか CHECK を根拠に、grain も根拠が要る
  - 列の SQL の型族と、意味型の値の型が両立すること。列・外部キー・意味型は実在するもの。重複は捨てる
  - is-a は共有主キーのときだけ。辺は中間表で、端は別々の n:1 の has 参照
  - 意味型の名前に型の名前（identifier など。GENERIC_NAMES）は使えない
  - 人が判断済みの注釈は変えない。v0 が決めた表の種類と食い違う答えは捨てる
  - 外部キーで同じ意味の列（mandara.modeler.meaning）には、組で1つの意味型だけ。組の意味型が決まっていれば
    それと違う答えを、決まっていなければ同じ答えの中で食い違う答えを捨てる。組の他の列への伝播は meaning が行う
  - 当てた結果が検証（4.5）で新しい違反を生むなら、違反の起きた項目だけを元に戻す。それでも通らなければ表ごと v0
- 出すものはすべて status: proposed（AI は承認しない）。確信度は上限 MAX_CONFIDENCE
"""

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from mandara.modeler.meaning import MeaningGroups, SameMeaning, group_semantic_type
from mandara.om.invariants import Violation, check
from mandara.om.model import (
    Additivity,
    Cardinality,
    Claim,
    ClaimPredicate,
    Column,
    SemanticType,
    SemanticRole,
    Edge,
    EnumValue,
    IsARealization,
    Relation,
    RelationType,
    ReviewStatus,
    Table,
    TableKind,
    TypeFamily,
    Unit,
    UnitSource,
    ValueType,
)
from mandara.om.names import qualify
from mandara.om.value_types import UNIT_BEARING, assignable
from mandara.om.ontology import SELF, Ontology, is_shared_key

MODELER = "modeler-llm 0.2"
NAME = r"^[a-z][a-z0-9_]{0,62}$"
AUTHOR_SEPARATOR = " + "
"""表の inferred_by で、作者（規則、モデラーなど）を並べる区切り。作者の名前には含めない。"""
MAX_CONFIDENCE = 0.9
"""LLM の確信度の上限。一括承認（0.95）には乗せず、人が1件ずつ見るようにする。"""

GENERIC_NAMES = frozenset({*(v.value for v in ValueType if v.value not in UNIT_BEARING), *(f.value for f in TypeFamily)})
"""新しい意味型の名前に使わせない、型の名前（LLM の答えの採否の方針。OM の不変条件ではない）。
意味型は意味で名付ける（identifier ではなく product_id）。型の名前の意味型は別の意味の列をひとまとめにし、
外部キーの組で伝播すると誤りが組ごと広がる。
単位の要る型（money / quantity）は除く：単位の要る意味型は伝播しない（mandara.modeler.meaning）ので組ごとには広がらない。
金額をひとまとめにするか分けるか（粒度）は未決（相談事項）で、それまでは型の名前のままの意味型も許す。"""

# ---------------------------------------------------------------- 答えの形


class _Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnumValueAnswer(_Answer):
    code: str = Field(min_length=1)
    label: str = Field(min_length=1)


class SemanticTypeAnswer(_Answer):
    name: str = Field(pattern=NAME)
    type: ValueType
    description: str
    role: SemanticRole | None = None
    additivity: Additivity | None = None
    requires_unit: bool | None = None
    values: list[EnumValueAnswer] | None = None
    confidence: float = Field(ge=0, le=1)
    evidence: list[str]
    """意味型の意味と、enum の値の根拠（enum の値はコメントか CHECK）。"""


class ColumnAnswer(_Answer):
    name: str
    semantic_type: str
    unit_fixed: Unit | None = None
    unit_column: str | None = None
    confidence: float = Field(ge=0, le=1)
    evidence: list[str]


class RelationAnswer(_Answer):
    foreign_key: str
    """外部キーの主張 ID（references）。"""
    type: Literal["has", "part-of", "is-a", "classified-as"]
    role: str | None = None
    confidence: float = Field(ge=0, le=1)
    evidence: list[str]


class EdgeAnswer(_Answer):
    type: Literal["has", "part-of", "classified-as"]
    from_foreign_key: str
    to_foreign_key: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[str]


class TableAnswer(_Answer):
    kind: TableKind | None
    kind_confidence: float = Field(ge=0, le=1)
    kind_evidence: list[str]
    grain: str | None
    grain_evidence: list[str]
    new_semantic_types: list[SemanticTypeAnswer]
    columns: list[ColumnAnswer]
    relations: list[RelationAnswer]
    edge: EdgeAnswer | None = None


# ---------------------------------------------------------------- 問いの材料


def _q(text: object) -> str:
    """自由文を JSON の文字列にする（改行や偽の行を作らせない）。< と > も逃がし、データの区画の終わりを偽造させない。"""
    return json.dumps(str(text), ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")


@dataclass(frozen=True)
class MeaningHint:
    """列が外部キーで同じ意味の組に入っていること（問いに書く）。"""

    others: int
    """組の他の列の数（範囲外の表の列も含む）。"""
    semantic_type: str | None
    """組に付いている意味型。None はまだ無い。"""


def meaning_hints(ontology: Ontology, table: Table, groups: MeaningGroups) -> dict[str, MeaningHint]:
    hints: dict[str, MeaningHint] = {}
    for name in table.columns or {}:
        group = groups.of(table.name, name)
        if group is not None:
            hints[name] = MeaningHint(len(group.members) - 1, group_semantic_type(ontology, group))
    return hints


def brief(table_claims: Sequence[Claim], vocabulary: Mapping[str, SemanticType], decided_kind: str | None,
          meaning: Mapping[str, MeaningHint], decided: Sequence[str] = ()) -> str:
    """表の主張を、根拠の ID つきで読める形にする。自由文はすべて JSON の文字列で、データの区画の中。
    decided は前の段（判断モデルの分類）で決まったこと（classify.decided_lines。閾値を超えたものだけ）。"""
    by = {p.value: [c for c in table_claims if c.predicate == p.value] for p in ClaimPredicate}
    declared = by[ClaimPredicate.declared_as.value]
    if not declared:
        raise ValueError("declared_as の主張が無い表は要約できない")
    subject = declared[0].subject
    lines = ["<data>", f"table {_q(qualify(subject.schema_name, subject.table))} — {declared[0].object_kind} "
                       f"({declared[0].id})",
             f"decided kind: {decided_kind or 'none'}"]
    for c in by[ClaimPredicate.described_as.value]:
        if c.subject.column is None:
            lines.append(f"comment: {_q(c.text)} ({c.id})")
    for c in by[ClaimPredicate.identified_by.value]:
        lines.append(f"primary key: {', '.join(map(_q, c.columns or []))} ({c.id})")
    for c in by[ClaimPredicate.unique.value]:
        lines.append(f"unique: {', '.join(map(_q, c.columns or []))} ({c.id})")
    lines.append("columns:")
    for t in sorted(by[ClaimPredicate.typed_as.value], key=lambda c: c.position or 0):
        name = t.subject.column
        facts = [f"type {_q(t.type_name)} family {t.family} ({t.id})"]
        for p, label in ((ClaimPredicate.not_null, "not null"), (ClaimPredicate.nullable, "nullable")):
            facts += [f"{label} ({c.id})" for c in by[p.value] if c.subject.column == name]
        facts += [f"alias type {_q(c.type_name)} ({c.id})" for c in by[ClaimPredicate.aliased_as.value]
                  if c.subject.column == name]
        facts += [f"default {_q(c.text)} ({c.id})" for c in by[ClaimPredicate.defaults_to.value]
                  if c.subject.column == name]
        facts += [f"comment {_q(c.text)} ({c.id})" for c in by[ClaimPredicate.described_as.value]
                  if c.subject.column == name]
        if (hint := meaning.get(name or "")) is not None:
            facts.append(f"same meaning as {hint.others} other columns (foreign keys); "
                         + (f"semantic type fixed: {_q(hint.semantic_type)}" if hint.semantic_type else "semantic type not named yet"))
        lines.append(f"- {_q(name)}: " + "; ".join(facts))
    for c in by[ClaimPredicate.references.value]:
        target = c.target
        assert target is not None
        lines.append(f"foreign key {c.id}: ({', '.join(map(_q, c.columns or []))}) -> "
                     f"{_q(qualify(target.schema_name, target.table))}({', '.join(map(_q, c.target_columns or []))})"
                     f" on delete {c.on_delete}")
    for c in by[ClaimPredicate.checks.value]:
        lines.append(f"check {c.id}: {_q(c.text)}")
    if decided:
        lines.append("decided by the classification (facts; do not contradict them):")
        lines += [f"- {d}" for d in decided]
    if vocabulary:
        lines.append("existing vocabulary:")
        lines += [f"- {d.name}: {d.type}" + (f" {_q(d.description)}" if d.description else "")
                  for d in vocabulary.values()]
    lines.append("</data>")
    return "\n".join(lines)


# ---------------------------------------------------------------- 答えを当てる


@dataclass(frozen=True)
class Dropped:
    item: str
    reason: str


@dataclass(frozen=True)
class Applied:
    ontology: Ontology
    applied: tuple[str, ...]
    """当たった項目（"kind"、"grain"、"edge"、"semantic_type <名前>"、"column <名前>"、"relation <外部キー>"）。"""
    dropped: tuple[Dropped, ...]
    table_kept_as_is: bool
    """答えを当てると検証を通らないので、表を v0 のまま残した（applied は空）。"""


class InvalidAnswerError(Exception):
    """答えが決めた形をしていない（構造の検証に失敗）。"""


class _Proposed(TypedDict):
    status: str
    confidence: float
    evidence: list[str]
    inferred_by: str


def answer_items(answer: TableAnswer) -> list[str]:
    """答えの項目の一覧（当たったか捨てたかの勘定に使う）。"""
    items = [*(["kind"] if answer.kind is not None else []),
             *(["grain"] if answer.grain and answer.grain.strip() else []),
             *(["edge"] if answer.edge is not None else [])]
    items += [f"semantic_type {d.name}" for d in answer.new_semantic_types]
    items += [f"column {c.name}" for c in answer.columns]
    items += [f"relation {r.foreign_key}" for r in answer.relations]
    return items


class _Work:
    """1つの答えを当てる作業の状態（当たった項目、捨てた項目）。"""

    def __init__(self, known: Mapping[str, Claim], inferred_by: str) -> None:
        self.known = known
        self.inferred_by = inferred_by
        self.applied: list[str] = []
        self.dropped: list[Dropped] = []

    def drop(self, item: str, reason: str) -> None:
        self.dropped.append(Dropped(item, reason))

    def cited(self, item: str, evidence: Sequence[str]) -> list[str] | None:
        bad = [e for e in evidence if e not in self.known]
        if bad or not evidence:
            self.drop(item, f"根拠の ID が渡した主張に無い: {bad}" if bad else "根拠が無い")
            return None
        return sorted(set(evidence))

    def annotation(self, confidence: float, evidence: list[str]) -> _Proposed:
        # model_copy は検証しないので、保持の形（値の文字列）で入れる
        return {"status": ReviewStatus.proposed.value, "confidence": min(confidence, MAX_CONFIDENCE),
                "evidence": evidence, "inferred_by": self.inferred_by}


def apply_answer(ontology: Ontology, table_id: str, answer: TableAnswer, table_claims: Sequence[Claim],
                 groups: MeaningGroups, inferred_by: str) -> Applied:
    table = ontology.table(table_id)
    if table is None:
        raise ValueError(f"表が無い: {table_id}")
    work = _Work({c.id: c for c in table_claims}, inferred_by)

    items = answer_items(answer)
    counts = Counter(items)
    twice = {k for k, n in counts.items() if n > 1}
    for item in sorted(twice):
        for _ in range(counts[item]):
            work.drop(item, "同じ項目が答えに2度ある（後勝ちで黙って上書きしない）")

    semantic_types = _apply_semantic_types(answer, ontology.vocabulary.semantic_types or {}, twice, work)
    columns = _apply_columns(answer, ontology, table, semantic_types, table_claims, groups, twice, work)
    relations = list(table.relations or [])
    fk_relation = _foreign_key_relations(table, relations, table_claims, ontology)
    _apply_relations(answer, table, relations, fk_relation, ontology, twice, work)
    table_update = _apply_table(answer, table, relations, fk_relation, set(items) - twice, twice, work)

    vocabulary = ontology.vocabulary.model_copy(update={"semantic_types": semantic_types or None})
    before = set(check(ontology))

    def build() -> Ontology:
        new_table = table.model_copy(update={**table_update, "columns": columns or None,
                                             "relations": relations or None})
        return _with_table(replace(ontology, vocabulary=vocabulary), new_table)

    for _ in range(4):  # 戻すと別の違反が消えたり現れたりするので、数回まで繰り返す
        introduced = [v for v in check(build()) if v not in before]
        if not introduced:
            return Applied(build(), tuple(work.applied), tuple(work.dropped), table_kept_as_is=False)
        for v in introduced:
            _revert(v, table, columns, relations, table_update, work)
    kept = tuple(Dropped(i, "表ごと v0 のまま（答えを当てると検証を通らない）") for i in work.applied)
    return Applied(ontology, (), tuple(work.dropped) + kept, table_kept_as_is=True)


def _apply_semantic_types(answer: TableAnswer, existing: Mapping[str, SemanticType], twice: set[str],
                   work: _Work) -> dict[str, SemanticType]:
    semantic_types = dict(existing)
    for d in answer.new_semantic_types:
        item = f"semantic_type {d.name}"
        if item in twice:
            continue
        current = semantic_types.get(d.name)
        if current is not None:
            same = (current.type, current.role, current.additivity, current.requires_unit) == (
                d.type.value, d.role.value if d.role else None, d.additivity.value if d.additivity else None,
                d.requires_unit)
            work.drop(item, "既にある（既存の定義を使う）" if same
                      else "既にある同じ名前の意味型と定義が違う（既存の定義を使う）")
            continue
        if d.name in GENERIC_NAMES:
            work.drop(item, f"型の名前 {d.name} は意味型の名前に使えない（何を指す値かで名付ける）")
            continue
        if (d.type == ValueType.enum) != bool(d.values):
            work.drop(item, "enum の意味型は値の一覧を持ち、enum 以外は持たない")
            continue
        evidence = work.cited(item, d.evidence)
        if evidence is None:
            continue
        values = d.values or []
        if values and not any(work.known[e].predicate in (ClaimPredicate.described_as.value,
                                                           ClaimPredicate.checks.value) for e in evidence):
            work.drop(item, "enum の値の根拠には、コメントか CHECK の主張が要る")
            continue
        if len({v.code for v in values}) != len(values):
            work.drop(item, "enum の値のコードが重複している")
            continue
        enum_values = [EnumValue.model_validate({"code": v.code, "label": v.label,
                                                 **work.annotation(d.confidence, evidence)}) for v in values] or None
        semantic_types[d.name] = SemanticType(name=d.name, type=d.type, description=d.description, role=d.role,
                                       additivity=d.additivity, requires_unit=d.requires_unit, values=enum_values)
        work.applied.append(item)
    return semantic_types


def _apply_columns(answer: TableAnswer, ontology: Ontology, table: Table, semantic_types: Mapping[str, SemanticType],
                   table_claims: Sequence[Claim], groups: MeaningGroups, twice: set[str],
                   work: _Work) -> dict[str, Column]:
    families = {c.subject.column: str(c.family) for c in table_claims
                if c.predicate == ClaimPredicate.typed_as.value and c.family is not None}
    columns = dict(table.columns or {})
    # 1巡目：組に依らない検査。通ったものだけで、組の中の食い違いを判定する（他の理由で落ちる答えを数えない）
    passed: list[tuple[ColumnAnswer, Column, list[str]]] = []
    for c in answer.columns:
        item = f"column {c.name}"
        if item in twice:
            continue
        column = columns.get(c.name)
        if column is None:
            work.drop(item, "表に無い列")
            continue
        if column.status != ReviewStatus.proposed.value:
            work.drop(item, f"人が判断済み（{column.status}）")
            continue
        semantic_type = semantic_types.get(c.semantic_type)
        if semantic_type is None:
            work.drop(item, f"語彙に無い意味型 {c.semantic_type}")
            continue
        family = families.get(c.name)
        if family is None:
            work.drop(item, "型の主張が無い")
            continue
        if semantic_type.type not in assignable(family, column.encoding):
            work.drop(item, f"列（型族 {family}、encoding {column.encoding}）に、値の型 {semantic_type.type} は当てられない")
            continue
        evidence = work.cited(item, c.evidence)
        if evidence is None:
            continue
        passed.append((c, column, evidence))
    answered: dict[SameMeaning, set[str]] = {}
    for c, _, _ in passed:
        if (group := groups.of(table.name, c.name)) is not None:
            answered.setdefault(group, set()).add(c.semantic_type)
    # 2巡目：組の検査（組の意味型と違う、同じ答えの中で組に別々の意味型）
    for c, column, evidence in passed:
        item = f"column {c.name}"
        if (group := groups.of(table.name, c.name)) is not None:
            fixed = group_semantic_type(ontology, group)
            if fixed is not None and fixed != c.semantic_type:
                work.drop(item, f"外部キーで同じ意味の列の意味型は {fixed}（{len(group.members)} 列の組）")
                continue
            if fixed is None and len(answered[group]) > 1:
                work.drop(item, f"外部キーで同じ意味の列に、同じ答えの中で別々の意味型 {sorted(answered[group])}")
                continue
        unit = UnitSource(fixed=c.unit_fixed, column=c.unit_column) if (c.unit_fixed or c.unit_column) else None
        columns[c.name] = column.model_copy(update={
            "semantic_type": c.semantic_type, "unit": unit,
            **work.annotation(c.confidence, sorted({*(column.evidence or []), *evidence}))})
        work.applied.append(item)
    return columns


def _apply_relations(answer: TableAnswer, table: Table, relations: list[Relation], fk_relation: Mapping[str, int],
                     ontology: Ontology, twice: set[str], work: _Work) -> None:
    for r in answer.relations:
        item = f"relation {r.foreign_key}"
        if item in twice:
            continue
        index = fk_relation.get(r.foreign_key)
        if index is None:
            work.drop(item, "この表の外部キーではない")
            continue
        current = relations[index]
        if current.status != ReviewStatus.proposed.value:
            work.drop(item, f"人が判断済み（{current.status}）")
            continue
        evidence = work.cited(item, r.evidence)
        if evidence is None:
            continue
        update: dict[str, object] = {"type": r.type, "role": r.role,
                                     **work.annotation(r.confidence, sorted({r.foreign_key, *evidence}))}
        if r.type == RelationType.is_a.value:
            if not _shares_key(table, current, ontology):
                work.drop(item, "is-a は主キー同士の結合（共有主キー）のときだけ")
                continue
            update |= {"has_kind": None, "realized_by": IsARealization.shared_key.value, "cardinality": None}
        elif r.type == RelationType.has.value:
            update |= {"has_kind": "reference", "realized_by": None, "cardinality": Cardinality("n:1").value}
        else:
            update |= {"has_kind": None, "realized_by": None, "cardinality": Cardinality("n:1").value}
        relations[index] = current.model_copy(update=update)
        work.applied.append(item)


def _apply_table(answer: TableAnswer, table: Table, relations: list[Relation], fk_relation: Mapping[str, int],
                 asked: set[str], twice: set[str], work: _Work) -> dict[str, object]:
    """表の種類、grain、辺。表の注釈（誰が出したか、根拠、確信度）も更新する。"""
    if table.status != ReviewStatus.proposed.value:
        for item in ("kind", "grain", "edge"):
            if item in asked:
                work.drop(item, f"表は人が判断済み（{table.status}）")
        return {}
    update: dict[str, object] = {}
    evidence_all: set[str] = set(table.evidence or [])
    confidences = [table.confidence if table.confidence is not None else 1.0]
    kind = table.kind
    if "kind" in asked and answer.kind is not None:
        if table.kind is not None and answer.kind.value != table.kind:
            work.drop("kind", f"v0 が決めた種類 {table.kind} と食い違う")
        elif table.kind is not None:
            work.drop("kind", f"v0 が決めた種類と同じ（{table.kind}）")
        elif (evidence := work.cited("kind", answer.kind_evidence)) is not None:
            kind = answer.kind.value
            update["kind"] = kind
            evidence_all |= set(evidence)
            confidences.append(answer.kind_confidence)
            work.applied.append("kind")
    if "grain" in asked and answer.grain is not None:
        if table.grain == answer.grain.strip():
            work.drop("grain", "既に同じ grain")
        elif kind not in (TableKind.event.value, TableKind.domain.value):
            work.drop("grain", f"grain は event / domain の表だけ（種類 {kind}）")
        elif (evidence := work.cited("grain", answer.grain_evidence)) is not None:
            update["grain"] = answer.grain.strip()
            evidence_all |= set(evidence)
            work.applied.append("grain")
    if "edge" in asked and answer.edge is not None:
        e = answer.edge
        ends = (fk_relation.get(e.from_foreign_key), fk_relation.get(e.to_foreign_key))
        if kind != TableKind.relation.value:
            work.drop("edge", f"辺は relation の表だけ（種類 {kind}）")
        elif ends[0] is None or ends[1] is None or ends[0] == ends[1]:
            work.drop("edge", "辺の端は、この表の別々の外部キー")
        elif relations[ends[0]].type != RelationType.has.value or relations[ends[1]].type != RelationType.has.value:
            work.drop("edge", "辺の端は has の参照のまま（同じ答えで関係型を変えている）")
        elif (evidence := work.cited("edge", e.evidence)) is not None:
            update["edge"] = Edge.model_validate({
                "type": e.type, "from": relations[ends[0]].id, "to": relations[ends[1]].id,
                **work.annotation(e.confidence, evidence)})
            work.applied.append("edge")
    if update:
        if AUTHOR_SEPARATOR in work.inferred_by:
            raise ValueError(f"作者の名前に区切り {AUTHOR_SEPARATOR!r} がある: {work.inferred_by!r}")
        authors = table.inferred_by.split(AUTHOR_SEPARATOR) if table.inferred_by else []
        authors += [] if work.inferred_by in authors else [work.inferred_by]
        update |= work.annotation(min(confidences), sorted(evidence_all)) | {"inferred_by": AUTHOR_SEPARATOR.join(authors)}
    return update


def _revert(v: Violation, table: Table, columns: dict[str, Column], relations: list[Relation],
            table_update: dict[str, object], work: _Work) -> None:
    """違反の場所から項目を特定し、v0 に戻す。"""
    reason = f"検証を通らない: {v.rule.name} {v.detail}".strip()
    original_columns = table.columns or {}
    # 列：場所は "<表>.columns.<列名>[.…]"。列名に "." がありうるので、名前の一覧と突き合わせる（長い名前を優先）
    for name in sorted(columns, key=len, reverse=True):
        base = f"{table.id}.columns.{name}"
        if v.location == base or v.location.startswith(base + "."):
            if f"column {name}" in work.applied and name in original_columns:
                columns[name] = original_columns[name]
                work.applied.remove(f"column {name}")
                work.drop(f"column {name}", reason)
            return
    for i, r in enumerate(relations):
        if v.location.startswith(f"{table.id}.relations.{r.id}"):
            original = next((o for o in table.relations or [] if o.id == r.id), None)
            item = next((a for a in work.applied if a.startswith("relation ") and a[len("relation "):] in
                         (r.evidence or [])), None)
            if original is not None and item is not None:
                relations[i] = original
                work.applied.remove(item)
                work.drop(item, reason)
            return
    if v.location.startswith(f"{table.id}.edge") and "edge" in table_update:
        del table_update["edge"]
        work.applied.remove("edge")
        work.drop("edge", reason)
        return
    if v.location == table.id and v.rule.name == "MISSING_GRAIN" and "kind" in table_update:
        del table_update["kind"]
        work.applied.remove("kind")
        work.drop("kind", reason + "（grain が無い）")


def _foreign_key_relations(table: Table, relations: list[Relation], table_claims: Sequence[Claim],
                           ontology: Ontology) -> dict[str, int]:
    """外部キーの主張 ID → それを実現している関係の位置。結合（参照先の表と列の対応）で引く（根拠の並びに頼らない）。"""
    out: dict[str, int] = {}
    for c in table_claims:
        if c.predicate != ClaimPredicate.references.value or c.target is None:
            continue
        target_name = qualify(c.target.schema_name, c.target.table)
        pairs = sorted(zip(c.columns or [], c.target_columns or [], strict=True))
        for i, r in enumerate(relations):
            target = table if r.target == SELF else ontology.table(r.target)
            if target is not None and target.name == target_name and sorted(
                    (p.from_, p.to) for p in r.via or []) == pairs:
                out[c.id] = i
                break
    return out


def shared_key_foreign_keys(table: Table, table_claims: Sequence[Claim], ontology: Ontology) -> frozenset[str]:
    """主キーを共有する（is-a になりうる）外部キーの主張 ID。"""
    relations = list(table.relations or [])
    return frozenset(fk for fk, i in _foreign_key_relations(table, relations, table_claims, ontology).items()
                     if _shares_key(table, relations[i], ontology))


def _shares_key(table: Table, relation: Relation, ontology: Ontology) -> bool:
    """自分自身への参照は共有主キーにしない（行が自分を指すだけ）。"""
    target = None if relation.target == SELF else ontology.table(relation.target)
    if target is None or table.identity is None or target.identity is None:
        return False
    return is_shared_key([(p.from_, p.to) for p in relation.via or []], table.identity.primary, target.identity.primary)


def _with_table(ontology: Ontology, table: Table) -> Ontology:
    return replace(ontology, tables=tuple(table if t.id == table.id else t for t in ontology.tables))
