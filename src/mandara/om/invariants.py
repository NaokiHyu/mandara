"""OM の不変条件（仕様 4.5）。

Domain 層。違反は例外ではなく値として、すべてまとめて返す。
ここに無い 4.5 の規則（コマンド、backend、read_from、watch、メトリクス）は、
それらがメタモデル①に入った時点で足す。関数（derived-from / via_function）の存在確認は、
関数の登録簿がまだ無いので行っていない。
"""

from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from mandara.om.encoding_traits import ENCODING_TARGETS
from mandara.om.model import (
    AliasEntry,
    Cardinality,
    Column,
    SemanticRole,
    SemanticType,
    HasKind,
    IsARealization,
    Relation,
    RelationType,
    ReviewStatus,
    Table,
    TableKind,
    Unit,
    ValueType,
    Vocabulary,
)
from mandara.om.ontology import SELF, Ontology
from mandara.om.relation_traits import RELATION_TRAITS
from mandara.om.value_types import UNIT_BEARING


class Rule(Enum):
    UNKNOWN_SEMANTIC_TYPE = "意味型が語彙に無い"
    UNKNOWN_VALUE = "閉じた集合に無い値（レビュー状態、表種別、関係型、値の型、単位、物理的な表現）"
    KEY_MISMATCH = "辞書のキーと要素の name が食い違っている"
    APPROVED_WITHOUT_REVIEW = "approved なのに reviewed_by / reviewed_at が無い"
    PROPOSED_WITHOUT_PROVENANCE = "proposed なのに confidence / evidence / inferred_by が無い"
    MISSING_GRAIN = "event 表、または尺度を持つ domain 表に grain が無い"
    UNDECIDED_KIND = "approved の表に kind が無い"
    DUPLICATE_RELATION = "同じものを結ぶ関係が2度ある（参照先と結合が同じ）"
    DUPLICATE_KEY = "同じ列の組のキーが2度ある（代替キーの重複、主キーと同じ代替キー）"
    DUPLICATE_ID = "安定ID が重複している"
    UNKNOWN_TARGET = "関係の参照先の表が存在しない"
    UNKNOWN_COLUMN = "存在しない列を指している"
    CYCLE = "推移的な関係（is-a / part-of / classified-as / derived-from。辺を含む）が表をまたいで循環している"
    DERIVED_AS_WHOLE = "derived の表が part-of の全体側になっている（辺を含む）"
    UNIT_MISSING = "単位が要る意味型なのに unit が無い"
    UNIT_UNEXPECTED = "単位の要らない意味型に unit がある"
    UNIT_AMBIGUOUS = "unit は fixed / column のちょうど1つでなければならない"
    RELATION_SHAPE = "関係の型と、その関係が持つフィールドが合っていない"
    SHARED_KEY_MISMATCH = "shared_key の is-a が、両側の主キー同士で結ばれていない"
    ENUM_SHAPE = "enum の値の定義が不正"
    SEMANTIC_TYPE_SHAPE = "意味型の値の型と、役割・加法性・単位の要否が合っていない"
    UNIT_COLUMN = "単位を持つ列の指定が不正（自分自身、単位のコードでない列）"
    ENCODING_MISMATCH = "列の encoding の変換先の型が、意味型の値の型と違う"
    EDGE_SHAPE = "辺（edge）の宣言が不正"


@dataclass(frozen=True)
class Violation:
    rule: Rule
    location: str
    detail: str


def check(ontology: Ontology) -> list[Violation]:
    return [
        *_check_vocabulary(ontology.vocabulary),
        *_check_ids(ontology.tables),
        *(v for t in ontology.tables for v in _check_table(t, ontology)),
        *_check_transitive_cycles(ontology),
    ]


# ---------------------------------------------------------------- 語彙


def _check_vocabulary(vocabulary: Vocabulary) -> Iterator[Violation]:
    semantic_types = vocabulary.semantic_types or {}
    yield from _check_keys(semantic_types, "vocabulary.semantic_types")
    aliases: dict[str, AliasEntry] = {}
    for key, entry in (vocabulary.aliases or {}).items():
        # 生成型は LinkML の簡略形（値が文字列だけ）も許すが、OM では使わない
        if isinstance(entry, AliasEntry):
            aliases[key] = entry
        else:
            yield Violation(Rule.UNKNOWN_VALUE, f"vocabulary.aliases.{key}", "簡略形（文字列だけ）は使わない")
    yield from _check_keys(aliases, "vocabulary.aliases")
    for name, semantic_type in semantic_types.items():
        loc = f"vocabulary.semantic_types.{name}"
        if semantic_type.type not in _VALUE_TYPES:
            yield Violation(Rule.UNKNOWN_VALUE, loc, f"type={semantic_type.type!r}")
        yield from _check_semantic_type_shape(semantic_type, loc)
        values = semantic_type.values or []
        if semantic_type.type == "enum":
            if not values:
                yield Violation(Rule.ENUM_SHAPE, loc, "enum なのに values が無い")
            for code in _duplicates(v.code for v in values):
                yield Violation(Rule.ENUM_SHAPE, loc, f"コード {code!r} が重複している")
            for i, value in enumerate(values):
                yield from _check_annotation(value, f"{loc}.values[{i}]")
        elif values:
            yield Violation(Rule.ENUM_SHAPE, loc, f"type が {semantic_type.type!r} なのに values がある")


_VALUE_TYPES = {t.value for t in ValueType}
_MEASURES = {ValueType.integer.value, ValueType.ratio.value, ValueType.quantity.value, ValueType.money.value}
"""加法性を持てる型（尺度）。"""
_TIME_ROLES = {SemanticRole.event_time.value, SemanticRole.valid_from.value, SemanticRole.valid_to.value}
_TIMES = {ValueType.date.value, ValueType.datetime.value}


def _check_semantic_type_shape(semantic_type: SemanticType, loc: str) -> Iterator[Violation]:
    if semantic_type.additivity is not None and semantic_type.type not in _MEASURES:
        yield Violation(Rule.SEMANTIC_TYPE_SHAPE, loc, f"{semantic_type.type} は尺度ではないので加法性を持たない")
    if semantic_type.type in UNIT_BEARING and semantic_type.requires_unit is not True:
        yield Violation(Rule.SEMANTIC_TYPE_SHAPE, loc, f"{semantic_type.type} は単位が要る（requires_unit: true）")
    if semantic_type.requires_unit and semantic_type.type not in UNIT_BEARING:
        yield Violation(Rule.SEMANTIC_TYPE_SHAPE, loc, f"{semantic_type.type} は単位を持たない")
    if semantic_type.role in _TIME_ROLES and semantic_type.type not in _TIMES:
        yield Violation(Rule.SEMANTIC_TYPE_SHAPE, loc, f"役割 {semantic_type.role} は日付・日時の型だけ")


class _Named(Protocol):
    @property
    def name(self) -> str: ...


def _check_keys(entries: Mapping[str, _Named], loc: str) -> Iterator[Violation]:
    for key, entry in entries.items():
        if key != entry.name:
            yield Violation(Rule.KEY_MISMATCH, f"{loc}.{key}", f"name={entry.name!r}")


# ---------------------------------------------------------------- ID


def _check_ids(tables: tuple[Table, ...]) -> Iterator[Violation]:
    for table_id in _duplicates(t.id for t in tables):
        yield Violation(Rule.DUPLICATE_ID, table_id, "表の ID が重複している")
    relation_ids = (r.id for t in tables for r in t.relations or [])
    for relation_id in _duplicates(relation_ids):
        yield Violation(Rule.DUPLICATE_ID, relation_id, "関係の ID が重複している")


# ---------------------------------------------------------------- 表

_TABLE_KINDS = {k.value for k in TableKind}
_UNITS = {u.value for u in Unit}


def _check_table(table: Table, ontology: Ontology) -> Iterator[Violation]:
    loc = table.id
    columns = table.columns or {}
    semantic_types = ontology.vocabulary.semantic_types or {}

    yield from _check_annotation(table, loc)
    yield from _check_keys(columns, f"{loc}.columns")
    if table.kind is None:
        if table.status == ReviewStatus.approved.value:
            yield Violation(Rule.UNDECIDED_KIND, loc, "")
    elif table.kind not in _TABLE_KINDS:
        yield Violation(Rule.UNKNOWN_VALUE, loc, f"kind={table.kind!r}")
    if table.derived is False:  # 導出された表なら true、実体の表なら書かない。false という3つ目の書き方を作らない
        yield Violation(Rule.UNKNOWN_VALUE, loc, "derived=False（実体の表には derived を書かない）")

    if table.identity is not None:
        keys = [table.identity.primary, *(k.columns for k in table.identity.alternates or [])]
        for name in (c for key in keys for c in key):
            if name not in columns:
                yield Violation(Rule.UNKNOWN_COLUMN, f"{loc}.identity", f"列 {name!r} が無い")
        for key in _duplicates(",".join(sorted(k)) for k in keys):
            yield Violation(Rule.DUPLICATE_KEY, f"{loc}.identity", f"列の組 {{{key}}}")

    if _is_blank(table.grain) and _needs_grain(table, semantic_types):
        yield Violation(Rule.MISSING_GRAIN, loc, f"kind={table.kind}")

    for name, column in columns.items():
        yield from _check_column(column, f"{loc}.columns.{name}", columns, semantic_types)

    for relation in table.relations or []:
        yield from _check_relation(relation, table, ontology)
    for joins in _duplicates(_joins(r) for r in table.relations or []):
        yield Violation(Rule.DUPLICATE_RELATION, f"{loc}.relations", joins)

    yield from _check_edge(table, ontology)


def _needs_grain(table: Table, semantic_types: dict[str, SemanticType]) -> bool:
    """event 表、または尺度（additivity を持つ意味型の列）を持つ domain 表。

    語彙に無い意味型の列は尺度かどうか判定できない。その列は UNKNOWN_SEMANTIC_TYPE として別に出る。
    """
    if table.kind == TableKind.event.value:
        return True
    if table.kind != TableKind.domain.value:
        return False
    return any(
        (d := semantic_types.get(c.semantic_type or "")) is not None and d.additivity is not None
        for c in (table.columns or {}).values()
    )


def _check_column(
    column: Column,
    loc: str,
    columns: dict[str, Column],
    semantic_types: dict[str, SemanticType],
) -> Iterator[Violation]:
    yield from _check_annotation(column, loc)

    semantic_type = semantic_types.get(column.semantic_type) if column.semantic_type is not None else None
    if column.semantic_type is not None and semantic_type is None:
        yield Violation(Rule.UNKNOWN_SEMANTIC_TYPE, loc, f"semantic_type={column.semantic_type!r}")
    if column.encoding is not None:
        target = ENCODING_TARGETS.get(column.encoding)
        if target is None:
            yield Violation(Rule.UNKNOWN_VALUE, loc, f"encoding={column.encoding!r}")
        elif semantic_type is not None and semantic_type.type != target:
            yield Violation(Rule.ENCODING_MISMATCH, loc, f"encoding={column.encoding} は {target} に変換する。semantic_type.type={semantic_type.type}")

    requires_unit = semantic_type is not None and bool(semantic_type.requires_unit)
    unit = column.unit
    if unit is None:
        if requires_unit:
            yield Violation(Rule.UNIT_MISSING, loc, f"semantic_type={column.semantic_type!r}")
        return
    if semantic_type is not None and not requires_unit:
        yield Violation(Rule.UNIT_UNEXPECTED, loc, f"semantic_type={column.semantic_type!r}")
    if unit.fixed is not None and unit.fixed not in _UNITS:
        yield Violation(Rule.UNKNOWN_VALUE, f"{loc}.unit", f"fixed={unit.fixed!r}")
    sources = [s for s in (unit.fixed, unit.column) if s is not None]
    if len(sources) != 1:
        yield Violation(Rule.UNIT_AMBIGUOUS, f"{loc}.unit", f"{len(sources)} 個指定されている")
    if unit.column is not None and unit.column not in columns:
        yield Violation(Rule.UNKNOWN_COLUMN, f"{loc}.unit", f"列 {unit.column!r} が無い")
    elif unit.column is not None:
        if unit.column == column.name:
            yield Violation(Rule.UNIT_COLUMN, f"{loc}.unit", "自分自身を単位の列にしている")
        unit_semantic_type = semantic_types.get(columns[unit.column].semantic_type or "")
        if unit_semantic_type is not None and unit_semantic_type.type != ValueType.code.value:
            yield Violation(Rule.UNIT_COLUMN, f"{loc}.unit", f"単位の列 {unit.column} の型が code ではない（{unit_semantic_type.type}）")


# ---------------------------------------------------------------- 関係


def _check_relation(relation: Relation, table: Table, ontology: Ontology) -> Iterator[Violation]:
    loc = f"{table.id}.relations.{relation.id}"
    yield from _check_annotation(relation, loc)
    if relation.type not in RELATION_TRAITS:
        yield Violation(Rule.UNKNOWN_VALUE, loc, f"type={relation.type!r}")
        return
    yield from _check_relation_shape(relation, table, loc)

    target = table if relation.target == SELF else ontology.table(relation.target)
    if target is None:
        yield Violation(Rule.UNKNOWN_TARGET, loc, f"target={relation.target!r}")
        return

    if relation.type == RelationType.part_of.value and target.derived:
        yield Violation(Rule.DERIVED_AS_WHOLE, loc, f"target={target.id}")

    own = table.columns or {}
    theirs = target.columns or {}
    for pair in relation.via or []:
        if pair.from_ not in own:
            yield Violation(Rule.UNKNOWN_COLUMN, f"{loc}.via", f"列 {pair.from_!r} が {table.id} に無い")
        if pair.to not in theirs:
            yield Violation(Rule.UNKNOWN_COLUMN, f"{loc}.via", f"列 {pair.to!r} が {target.id} に無い")
    for name in relation.columns or []:
        if name not in own:
            yield Violation(Rule.UNKNOWN_COLUMN, f"{loc}.columns", f"列 {name!r} が無い")

    if relation.realized_by == IsARealization.shared_key.value:
        yield from _check_shared_key(relation, table, target, loc)


def _joins(relation: Relation) -> str:
    """関係が結ぶもの（参照先と、列の対応・関数）を、比べられる文字列にする。"""
    pairs = sorted((p.from_, p.to) for p in relation.via or [])
    return repr((relation.target, pairs, relation.via_function, relation.function, sorted(relation.columns or [])))


def _check_relation_shape(relation: Relation, table: Table, loc: str) -> Iterator[Violation]:
    kind = relation.type

    def wrong(detail: str) -> Violation:
        return Violation(Rule.RELATION_SHAPE, loc, f"{kind}: {detail}")

    if relation.target == table.id:
        yield wrong("自分自身は self で書く")
    if (kind == RelationType.has.value) != (relation.has_kind is not None):
        yield wrong("has_kind は has にだけ、必ず付ける")
    if relation.realized_by is not None and kind != RelationType.is_a.value:
        yield wrong("realized_by は is-a にだけ付ける")
    if kind == RelationType.is_a.value and relation.target in (SELF, table.id):
        yield wrong("自分自身への is-a")

    if kind == RelationType.derived_from.value:
        if relation.function is None or not relation.columns:
            yield wrong("function と columns が要る")
        if relation.via or relation.via_function is not None:
            yield wrong("via / via_function は持たない")
    else:
        if relation.function is not None or relation.columns:
            yield wrong("function / columns は derived-from にだけ付ける")
        if bool(relation.via) == (relation.via_function is not None):
            yield wrong("via と via_function のちょうど一方が要る")


_EDGE_TYPES = {RelationType.has.value, RelationType.part_of.value, RelationType.classified_as.value}


def _check_edge(table: Table, ontology: Ontology) -> Iterator[Violation]:
    edge = table.edge
    if edge is None:
        return
    loc = f"{table.id}.edge"
    yield from _check_annotation(edge, loc)
    if table.kind != TableKind.relation.value:
        yield Violation(Rule.EDGE_SHAPE, loc, f"edge は relation の表にだけ書く。kind={table.kind}")
    if edge.type not in RELATION_TRAITS:
        yield Violation(Rule.UNKNOWN_VALUE, loc, f"type={edge.type!r}")
    elif edge.type not in _EDGE_TYPES:
        yield Violation(Rule.EDGE_SHAPE, loc, f"{edge.type} は辺にならない")
    if edge.from_ == edge.to:
        yield Violation(Rule.EDGE_SHAPE, loc, "from と to が同じ参照")
    relations = {r.id: r for r in table.relations or []}
    for side, rel_id in (("from", edge.from_), ("to", edge.to)):
        relation = relations.get(rel_id)
        if relation is None:
            yield Violation(Rule.EDGE_SHAPE, loc, f"{side}={rel_id!r} がこの表の関係に無い")
        elif (
            relation.type != RelationType.has.value
            or relation.has_kind != HasKind.reference.value
            or relation.cardinality != Cardinality("n:1").value
        ):
            yield Violation(Rule.EDGE_SHAPE, loc, f"{side}={rel_id} は n:1 の has（reference）でなければならない")
    whole = _edge_end(edge.to, table, ontology)
    if edge.type == RelationType.part_of.value and whole is not None and whole.derived:
        yield Violation(Rule.DERIVED_AS_WHOLE, loc, f"to の参照先={whole.id}")


def _is_edge_table(table: Table) -> bool:
    return table.kind == TableKind.relation.value and table.edge is not None and table.edge.type in _EDGE_TYPES


def _edge_end(rel_id: str, table: Table, ontology: Ontology) -> Table | None:
    """辺の端点（この表の参照 rel_id の参照先の表）。参照や参照先が無いときは None（別の規則で違反になる）。"""
    relation = next((r for r in table.relations or [] if r.id == rel_id), None)
    if relation is None:
        return None
    return table if relation.target == SELF else ontology.table(relation.target)


def _check_shared_key(relation: Relation, table: Table, target: Table, loc: str) -> Iterator[Violation]:
    pairs = relation.via or []
    own_key = table.identity.primary if table.identity is not None else None
    target_key = target.identity.primary if target.identity is not None else None
    if own_key is None or target_key is None:
        yield Violation(Rule.SHARED_KEY_MISMATCH, loc, "どちらかの表に主キーが無い")
        return
    if [p.from_ for p in pairs] != own_key or [p.to for p in pairs] != target_key:
        yield Violation(
            Rule.SHARED_KEY_MISMATCH,
            loc,
            f"via={[(p.from_, p.to) for p in pairs]}, 主キー={own_key} → {target_key}",
        )


def _check_transitive_cycles(ontology: Ontology) -> Iterator[Violation]:
    """推移的な関係型ごとに、表をまたいだ循環を検出する。

    自己参照（self。組織の木など）や、同じ表の行同士を結ぶ辺（部品表）は、インスタンスの木を表すので循環として扱わない。
    自分の ID を target に書いたものは _check_relation_shape で落とす。
    """
    for kind in (k for k, traits in RELATION_TRAITS.items() if traits.transitive):
        edges: dict[str, set[str]] = {}
        for table in ontology.tables:
            for relation in table.relations or []:
                if relation.type == kind and relation.target not in (SELF, table.id):
                    edges.setdefault(table.id, set()).add(relation.target)
            # 形の不正な辺（relation 以外の表、辺にならない型）は EDGE_SHAPE で出すので、ここでは数えない
            if table.edge is not None and table.edge.type == kind and _is_edge_table(table):
                start = _edge_end(table.edge.from_, table, ontology)
                end = _edge_end(table.edge.to, table, ontology)
                if start is not None and end is not None and start.id != end.id:
                    edges.setdefault(start.id, set()).add(end.id)
        for cycle in _find_cycles(edges):
            yield Violation(Rule.CYCLE, cycle[0], f"{kind}: {' → '.join(cycle)}")


def _find_cycles(edges: dict[str, set[str]]) -> Iterator[list[str]]:
    """各循環を、辞書順で最小のノードから始まる形で1回ずつ返す。"""
    seen: set[tuple[str, ...]] = set()

    def visit(node: str, path: list[str]) -> Iterator[list[str]]:
        for nxt in sorted(edges.get(node, ())):
            if nxt in path:
                cycle = path[path.index(nxt):]
                start = cycle.index(min(cycle))
                canonical = tuple(cycle[start:] + cycle[:start])
                if canonical not in seen:
                    seen.add(canonical)
                    yield [*canonical, canonical[0]]
            else:
                yield from visit(nxt, [*path, nxt])

    for root in sorted(edges):
        yield from visit(root, [root])


# ---------------------------------------------------------------- 注釈（4.4）


class _Annotated(Protocol):
    """メタモデル①の Annotation mixin を持つクラス。

    生成された型は Annotation を基底に持つが、フィールドは各クラスに展開され、基底には無い。
    そのため基底の型ではなく、構造で受ける。
    """

    @property
    def status(self) -> str: ...
    @property
    def confidence(self) -> float | None: ...
    @property
    def evidence(self) -> list[str] | None: ...
    @property
    def inferred_by(self) -> str | None: ...
    @property
    def reviewed_by(self) -> str | None: ...
    @property
    def reviewed_at(self) -> object | None: ...


_STATUSES = {s.value for s in ReviewStatus}


def _check_annotation(annotation: _Annotated, loc: str) -> Iterator[Violation]:
    status = annotation.status
    if status not in _STATUSES:
        yield Violation(Rule.UNKNOWN_VALUE, loc, f"status={status!r}")
    if status == ReviewStatus.approved.value and (annotation.reviewed_by is None or annotation.reviewed_at is None):
        yield Violation(Rule.APPROVED_WITHOUT_REVIEW, loc, "")
    if status == ReviewStatus.proposed.value:
        missing = [
            name
            for name, value in (
                ("confidence", annotation.confidence),
                ("evidence", annotation.evidence or None),
                ("inferred_by", annotation.inferred_by),
            )
            if value is None
        ]
        if missing:
            yield Violation(Rule.PROPOSED_WITHOUT_PROVENANCE, loc, f"無い: {', '.join(missing)}")


def _is_blank(text: str | None) -> bool:
    return text is None or not text.strip()


def _duplicates(items: Iterable[str]) -> list[str]:
    return sorted(k for k, n in Counter(items).items() if n > 1)
