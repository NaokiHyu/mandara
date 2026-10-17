"""ドメインビューワのユースケース：承認済みの OM から、ドメイン（集合）とインスタンス（個）を見せる（仕様 7.6、T5.2 の手前）。

Application 層。どのソースかは知らない（RecordReader を外から渡す）。値はソースの表現から表示の文字列にする。
- ドメイン：承認済みの domain / event の表。relation の表はドメインにせず、リンクとしてたどる（n:m）。
  ドメインにしなかった表は理由とともに返す（黙って消さない）
- 集合：検索（文字の部分一致）の結果。表のイメージ
- 個：1行の値（技術列を除く）、参照先へのリンク（n:1）、近隣の集まり（参照してくる行 1:n、relation の表を挟んだ先 n:m）
- 列の等値で結べない関係（via_function）はたどらない
- 見出しの列が無い表は、is-a の上位（主キーを共有する）の同じキーの行の見出しを借りる（label_parent）
- n:m の集まりの行には、結んだ relation の表の行の値（期間、数量など。link_cells）を添える。relation の行ごとに1つ
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID

from mandara.om.model import (
    Additivity,
    BreakdownValue,
    BucketUnit,
    Cell,
    GroupView,
    LatestDay,
    MeasureOp,
    MeasureValue,
    SeriesPoint,
    SeriesSum,
    SkippedMeasure,
    IsARealization,
    LabelSource,
    DomainIndex,
    DomainRows,
    DomainSummary,
    HiddenTable,
    Instance,
    InstanceLink,
    InstanceSummary,
    LinkState,
    OmittedRelation,
    RelatedGroup,
    RelatedKind,
    Relation,
    RelationType,
    ReviewStatus,
    SemanticRole,
    SemanticType,
    Table,
    TableKind,
    ValueType,
)
from mandara.om.invariants import check
from mandara.om.names import split_qualified
from mandara.om.ontology import SELF, Ontology
from mandara.codecs.registry import BY_ENCODING, CodecError
from mandara.source.records import (
    Contains,
    Equals,
    InvalidKeyError,
    NotKeyableError,
    RecordReader,
    Aggregated,
    Group,
    Measure,
    Op,
    Records,
    TimeBucket,
    TimeUnit,
    key_text,
)
from mandara.source.values import GeoPoint

DOMAIN_KINDS = (TableKind.domain.value, TableKind.event.value)
SEARCH_LIMIT = 50
RELATED_LIMIT = 20
SUMMARY_CELLS = 6
"""一覧の行に添える列の数（キーと見出しを除く）。"""
ROW_TEXT = 80
"""一覧の行の値の長さの上限（それより長い文字は … で切る。個のビューでは切らない）。"""
_SEARCH_TYPES = {ValueType.text.value, ValueType.code.value, ValueType.identifier.value, ValueType.enum.value}


class BrowseError(Exception):
    """ドメインとして見せられない。"""


class InvalidOntologyError(BrowseError):
    """OM が検証（仕様 4.5）を通らない。ドメインビューワは検証済みの OM の性質（is-a は循環しない、共有主キーの via は
    主キーの列）に頼るので、通らない OM は見せない。"""


def _require_valid(ontology: Ontology) -> None:
    if violations := check(ontology):
        raise InvalidOntologyError(f"OM が検証を通らない（{len(violations)} 件。例 "
                                   f"{[f'{v.location}: {v.rule.name}' for v in violations[:3]]}）")


class NotFoundError(BrowseError):
    """表が無い、ドメインでない、行が無い。"""


class BadKeyError(BrowseError):
    """キーの数が主キーと合わない、キーを主キーの型として読めない。"""


class UnsupportedKeyError(BrowseError):
    """主キーの値を、たどれるキーの文字列にできない（バイト列の主キーなど）。"""


# ---------------------------------------------------------------- 表の見え方（純粋）


def hidden_reason(table: Table) -> str | None:
    """ドメインにしない理由。None ならドメイン。"""
    if table.status != ReviewStatus.approved.value:
        return f"承認されていない（{table.status}）"
    if table.kind == TableKind.relation.value:
        return "relation の表（リンクとしてたどる）"
    if table.kind not in DOMAIN_KINDS:
        return f"種類 {table.kind} はドメインにしない"
    if table.identity is None or not table.identity.primary:
        return "主キーが無い（行をたどれない）"
    return None


def _semantic(ontology: Ontology, table: Table, column: str) -> SemanticType | None:
    found = (table.columns or {}).get(column)
    if found is None or found.semantic_type is None:
        return None
    return (ontology.vocabulary.semantic_types or {}).get(found.semantic_type)


def visible_columns(ontology: Ontology, table: Table) -> list[str]:
    """見せる列（技術列を除く）。表の列の順。"""
    out: list[str] = []
    for name in table.columns or {}:
        d = _semantic(ontology, table, name)
        if d is None or d.role != SemanticRole.technical.value:
            out.append(name)
    return out


LABEL_COLUMNS = 3


def label_columns(ontology: Ontology, table: Table) -> list[str]:
    """見出しの列：name という列があればそれだけ。無ければ名前が name で終わる文字の列（3 つまで。名・ミドル・姓など）。
    空ならキーを見出しにする。"""
    columns = visible_columns(ontology, table)
    key: set[str] = set(table.identity.primary) if table.identity else set()
    if "name" in columns:
        return ["name"]
    return [c for c in columns if c not in key and c.endswith("name")
            and ((d := _semantic(ontology, table, c)) is None or d.type == ValueType.text.value)][:LABEL_COLUMNS]


def label_parent(ontology: Ontology, table: Table) -> tuple[Relation, Table] | None:
    """見出しを借りる is-a の上位：見出しの列が無い表の、主キーを共有する is-a の先のドメインで、見出しを持つか、
    さらに上から借りられるもの。上位が複数あれば関係の ID の順で最初のもの。
    OM は検証済み（is-a は循環しない、仕様 4.5）であること（_require_valid）。借りる連なりはそれで終わる。"""
    if label_columns(ontology, table):
        return None
    for rel in sorted(table.relations or [], key=lambda r: r.id):
        if rel.type != RelationType.is_a.value or rel.realized_by != IsARealization.shared_key.value or not rel.via:
            continue
        target = _target(ontology, table, rel)
        if (target is not None and target.id != table.id and hidden_reason(target) is None
                and (label_columns(ontology, target) or label_parent(ontology, target) is not None)):
            return rel, target
    return None


def searchable(ontology: Ontology, table: Table) -> list[str]:
    """検索（部分一致）に使う列：文字・コード・識別子・enum の見せる列。"""
    return [c for c in visible_columns(ontology, table)
            if (d := _semantic(ontology, table, c)) is not None and d.type in _SEARCH_TYPES]


def _summary_columns(ontology: Ontology, table: Table) -> list[str]:
    key = list(table.identity.primary) if table.identity else []
    labels = label_columns(ontology, table)
    rest = [c for c in visible_columns(ontology, table) if c not in key and c not in labels]
    return rest[:SUMMARY_CELLS]


def _target(ontology: Ontology, table: Table, relation: Relation) -> Table | None:
    return table if relation.target == SELF else ontology.table(relation.target)


def _schema_table(table: Table) -> tuple[str, str]:
    parts = split_qualified(table.name)
    if len(parts) != 2:
        raise ValueError(f"表の名前が「スキーマ.表」ではない（OM の不正）: {table.name}")
    return parts[0], parts[1]


# ---------------------------------------------------------------- 表示の文字列


def display(value: object, semantic_type: SemanticType | None, encoding: str | None = None) -> str | None:
    """ソースの表現を表示の文字列にする（表示だけ。行をたどるキーには key_text を使う）。NULL は None。
    列の encoding のコーデックがあれば、意味の値に変換してから表示する（geography → 緯度・経度）。変換できなければ理由を見せる。
    enum は語彙のラベルを添える。"""
    if value is None:
        return None
    if encoding is not None and (codec := BY_ENCODING.get(encoding)) is not None:
        try:
            value = codec.decode(value)
        except CodecError as e:
            return f"（{encoding} として読めない: {e}）"
    if isinstance(value, GeoPoint):
        return f"{value.lat:.6f}, {value.lon:.6f}"
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, Decimal):
        text = format(value.normalize(), "f") if value == value.to_integral() else str(value)
    elif isinstance(value, datetime):
        text = value.date().isoformat() if value.time() == time(0) and value.tzinfo is None else value.isoformat()
    elif isinstance(value, date | UUID):
        text = str(value)
    elif isinstance(value, bytes | bytearray):
        text = f"（{len(value):,} バイトのデータ）"
    else:
        text = str(value)
    if semantic_type is not None and semantic_type.values:
        label = next((v.label for v in semantic_type.values if v.code == text), None)
        if label is not None:
            return f"{label}（{text}）"
    return text


def _cut(text: str | None) -> str | None:
    return text if text is None or len(text) <= ROW_TEXT else text[:ROW_TEXT - 1] + "…"


# ---------------------------------------------------------------- ユースケース


def domain_index(ontology: Ontology, ontology_id: str, connected: set[str]) -> DomainIndex:
    """connected：行を読めるソースの名前。すべてのドメインのソースに接続があれば readable。"""
    _require_valid(ontology)
    domains: list[DomainSummary] = []
    hidden: list[HiddenTable] = []
    for t in sorted(ontology.tables, key=lambda t: (t.context, t.name)):
        reason = hidden_reason(t)
        if reason is not None:
            hidden.append(HiddenTable(table=t.id, name=t.name, reason=reason))
            continue
        parent = label_parent(ontology, t)
        alternates = [c for k in (t.identity.alternates or [] if t.identity else []) for c in k.columns]
        domains.append(DomainSummary(table=t.id, name=t.name, context=t.context, kind=t.kind,  # pyright: ignore[reportArgumentType]
                                     grain=t.grain, label_columns=label_columns(ontology, t),
                                     label_from=parent[1].id if parent else None,
                                     key_columns=list(t.identity.primary) if t.identity else [],
                                     natural_keys=list(dict.fromkeys(alternates)),
                                     searchable=searchable(ontology, t)))
    sources = {t.source for t in ontology.tables if hidden_reason(t) is None}
    return DomainIndex(ontology=ontology_id, contexts=sorted({t.context for t in ontology.tables}), domains=domains,
                       hidden=hidden, readable=sources <= connected)


def _domain(ontology: Ontology, table_id: str) -> Table:
    table = ontology.table(table_id)
    if table is None:
        raise NotFoundError(f"表が無い: {table_id}")
    if (reason := hidden_reason(table)) is not None:
        raise NotFoundError(f"{table.name} はドメインではない: {reason}")
    return table


@dataclass(frozen=True)
class _Reading:
    """1つのドメインの行を、見出しつきで読むための列。"""

    table: Table
    key: list[str]
    labels: list[str]
    cells: list[str]

    @property
    def columns(self) -> list[str]:
        return list(dict.fromkeys([*self.key, *self.labels, *self.cells]))


def _reading(ontology: Ontology, table: Table) -> _Reading:
    assert table.identity is not None
    return _Reading(table, list(table.identity.primary), label_columns(ontology, table), _summary_columns(ontology, table))


def _cell(ontology: Ontology, table: Table, column: str, value: object, cut: bool) -> Cell:
    d = _semantic(ontology, table, column)
    found = (table.columns or {}).get(column)
    text = display(value, d, found.encoding if found else None)
    return Cell(column=column, value=_cut(text) if cut else text, semantic_type=d.name if d else None)


def _key(r: _Reading, row: Mapping[str, object]) -> list[str]:
    try:
        return [key_text(row[c]) for c in r.key]
    except NotKeyableError as e:
        raise UnsupportedKeyError(f"{r.table.name} の主キー {r.key} をたどれる文字列にできない: {e}") from e


def _summary(ontology: Ontology, r: _Reading, row: Mapping[str, object], reader: RecordReader, cut: bool = True,
             skip: Sequence[str] = ()) -> InstanceSummary:
    """skip：添える列から外す列（近隣の集まりで、このインスタンスを指す列。どの行も同じ値になる）。
    key は往復できるキーの文字列（key_text）。表示の文字列（display）とは別。"""
    key = _key(r, row)
    cells = [_cell(ontology, r.table, c, row[c], cut) for c in r.cells if c not in skip]
    label, source = _label(ontology, r, row, reader, key)
    return InstanceSummary(table=r.table.id, key=key, label=label, label_source=source, cells=cells)


def _label(ontology: Ontology, r: _Reading, row: Mapping[str, object], reader: RecordReader,
           key: list[str]) -> tuple[str, LabelSource]:
    """見出しの列の値。無ければ is-a の上位の同じキーの行の見出し。上位の行が無ければ、壊れた参照としてキーを見出しにする。"""
    parts = [text for c in r.labels if (text := _cell(ontology, r.table, c, row[c], cut=False).value)]
    if r.labels:
        return (" ".join(parts), LabelSource.own) if parts else (" · ".join(key), LabelSource.key)
    parent = label_parent(ontology, r.table)
    if parent is None:
        return " · ".join(key), LabelSource.key
    rel, target = parent
    pr = _reading(ontology, target)
    schema, name = _schema_table(target)
    # 共有主キーの via の元の列は主キーの列（検証済み）なので、row にある
    found = reader.find(schema, name, pr.columns, pr.key, Equals({p.to: row[p.from_] for p in rel.via or []}), 1)
    if not found.rows:
        return " · ".join(key), LabelSource.borrowed_missing
    label, source = _label(ontology, pr, found.rows[0], reader, _key(pr, found.rows[0]))
    return label, (LabelSource.borrowed if source in (LabelSource.own, LabelSource.borrowed) else source)


class _Memo:
    """1つの要求の中で、同じ読みを1回にする（n:m の先や見出しの借用で、同じ行を何度も読まないため）。"""

    def __init__(self, inner: RecordReader) -> None:
        self._inner = inner
        self._seen: dict[tuple[object, ...], Records] = {}

    def find(self, schema: str, table: str, columns: Sequence[str], order_by: Sequence[str],
             where: Contains | Equals | None, limit: int, descending: bool = False) -> Records:
        condition = (None if where is None else ("contains", where.columns, where.text) if isinstance(where, Contains)
                     else ("equals", tuple(sorted((k, repr(v)) for k, v in where.values.items()))))
        key = (schema, table, tuple(columns), tuple(order_by), condition, limit, descending)
        if key not in self._seen:
            self._seen[key] = self._inner.find(schema, table, columns, order_by, where, limit, descending)
        return self._seen[key]

    def aggregate(self, schema: str, table: str, where: Equals, by: str | TimeBucket | None,
                  measures: Sequence[Measure], limit: int, latest_first: bool = False) -> Aggregated:
        return self._inner.aggregate(schema, table, where, by, measures, limit, latest_first)


def search(ontology: Ontology, table_id: str, reader: RecordReader, query: str | None,
           limit: int = SEARCH_LIMIT) -> DomainRows:
    """ドメインの集合：query があれば検索の列とキーの部分一致、無ければ先頭から。"""
    _require_valid(ontology)
    reader = _Memo(reader)
    table = _domain(ontology, table_id)
    r = _reading(ontology, table)
    text = (query or "").strip()
    columns = searchable(ontology, table)
    where = Contains(tuple(dict.fromkeys([*r.key, *columns])), text) if text else None
    schema, name = _schema_table(table)
    records = reader.find(schema, name, r.columns, r.key, where, limit)
    return DomainRows(table=table.id, name=table.name, query=text or None,
                      columns=[*r.labels, *r.cells],
                      rows=[_summary(ontology, r, row, reader) for row in records.rows], limit=limit,
                      truncated=records.truncated)


def instance(ontology: Ontology, table_id: str, key: Sequence[str], reader: RecordReader, today: date,
             related_limit: int = RELATED_LIMIT) -> Instance:
    """インスタンスの個のビュー：値、参照先へのリンク（n:1）、近隣の集まり（1:n の子、relation の表を挟んだ n:m）。
    key はキーの文字列（key_text）。たどらなかった関係は理由とともに omitted に返す。"""
    _require_valid(ontology)
    reader = _Memo(reader)
    table = _domain(ontology, table_id)
    r = _reading(ontology, table)
    if len(key) != len(r.key):
        raise BadKeyError(f"キーの数が主キー {r.key} と合わない: {list(key)}")
    shown = visible_columns(ontology, table)
    omitted: list[OmittedRelation] = []
    relations: list[Relation] = []
    for rel in table.relations or []:
        if not rel.via:
            omitted.append(OmittedRelation(relation=rel.id, table=table.name, reason="列の等値で結べない関係（via_function）"))
        else:
            relations.append(rel)
    referenced = [p.to for other in ontology.tables for rel in other.relations or []
                  if rel.via and _points_to(ontology, other, rel, table) for p in rel.via]
    own = list(dict.fromkeys([*r.key, *shown, *(p.from_ for rel in relations for p in rel.via or []), *referenced]))
    schema, name = _schema_table(table)
    try:
        found = reader.find(schema, name, own, r.key, Equals(dict(zip(r.key, key, strict=True))), 1)
    except InvalidKeyError as e:
        raise BadKeyError(f"{table.name} のキー {list(key)} を主キーの型として読めない") from e
    if not found.rows:
        raise NotFoundError(f"{table.name} に行が無い: {list(key)}")
    row = found.rows[0]
    me = _summary(ontology, r, row, reader, cut=False)
    fields = [_cell(ontology, table, c, row[c], cut=False) for c in shown]
    links: list[InstanceLink] = []
    for rel in relations:
        target = _target(ontology, table, rel)
        reason = "参照先の表が OM に無い" if target is None else hidden_reason(target)
        if target is None or reason is not None:
            omitted.append(OmittedRelation(relation=rel.id, table=table.name, reason=f"参照先がドメインでない: {reason}"))
            continue
        links.append(_link(ontology, table, target, rel, row, reader))
    related = [*_children(ontology, table, row, reader, related_limit, today, omitted),
               *_through_relations(ontology, table, row, reader, related_limit, today, omitted)]
    return Instance(table=table.id, name=table.name, key=me.key, label=me.label, fields=fields, links=links,
                    related=related, omitted=omitted)


def _points_to(ontology: Ontology, source: Table, rel: Relation, table: Table) -> bool:
    target = _target(ontology, source, rel)
    return target is not None and target.id == table.id


def _link(ontology: Ontology, table: Table, target: Table, rel: Relation, row: Mapping[str, object],
          reader: RecordReader) -> InstanceLink:
    """参照先（n:1）へのリンク。参照の列が NULL なら null、参照先の行が無ければ missing（壊れた参照）。"""
    pairs = rel.via or []
    values = {p.to: row[p.from_] for p in pairs}
    summary, state = None, LinkState.null
    if all(v is not None for v in values.values()):
        tr = _reading(ontology, target)
        schema, name = _schema_table(target)
        found = reader.find(schema, name, tr.columns, tr.key, Equals(values), 1)
        summary = _summary(ontology, tr, found.rows[0], reader) if found.rows else None
        state = LinkState.found if found.rows else LinkState.missing
    return InstanceLink(relation=rel.id, relation_type=rel.type, role=rel.role, columns=[p.from_ for p in pairs],
                        target_table=target.id, target_name=target.name, state=state, target=summary)


def _children(ontology: Ontology, table: Table, row: Mapping[str, object], reader: RecordReader,
              limit: int, today: date, omitted: list[OmittedRelation]) -> list[RelatedGroup]:
    """このインスタンスを参照してくる、他のドメインの行（1:n）。参照してくる表がドメインでなければ理由を omitted に足す
    （relation の表は _through_relations が n:m としてたどる）。見せ方は _profile が決める。"""
    groups: list[RelatedGroup] = []
    for other in sorted(ontology.tables, key=lambda t: t.name):
        incoming = [rel for rel in other.relations or [] if rel.via and _points_to(ontology, other, rel, table)]
        if not incoming:
            continue
        reason = hidden_reason(other)
        if reason is not None:
            if other.kind != TableKind.relation.value or other.status != ReviewStatus.approved.value:
                omitted += [OmittedRelation(relation=rel.id, table=other.name,
                                            reason=f"参照してくる表がドメインでない: {reason}") for rel in incoming]
            continue
        for rel in incoming:
            if any(row[p.to] is None for p in rel.via or []):
                continue
            where = Equals({p.from_: row[p.to] for p in rel.via or []})
            profile = _profile(ontology, other, where, reader, today)
            if profile.count == 0:
                continue
            columns = [p.from_ for p in rel.via or []]
            cr = _reading(ontology, other)
            cr = _Reading(cr.table, cr.key, cr.labels, _with_first(profile.time_columns, cr.cells, cr.labels))
            schema, name = _schema_table(other)
            found = reader.find(schema, name, cr.columns, profile.order or cr.key, where,
                                profile.row_limit(limit), profile.descending)
            groups.append(RelatedGroup.model_validate(dict(
                kind=RelatedKind.children, relation=rel.id, relation_type=rel.type, table=other.id, name=other.name,
                columns=columns, rows=[_summary(ontology, cr, x, reader, skip=columns) for x in found.rows],
                truncated=found.truncated, missing=0, **profile.fields)))
    return groups


def _through_relations(ontology: Ontology, table: Table, row: Mapping[str, object], reader: RecordReader,
                       limit: int, today: date, omitted: list[OmittedRelation]) -> list[RelatedGroup]:
    """relation の表を挟んだ先のインスタンス（n:m）。relation の表そのものは出さない。見せ方と集計は relation の表の行について
    （_profile）。先の行が無ければ missing に数える。先の表がドメインでなければ理由を omitted に足す。"""
    groups: list[RelatedGroup] = []
    for middle in sorted(ontology.tables, key=lambda t: t.name):
        if middle.status != ReviewStatus.approved.value or middle.kind != TableKind.relation.value:
            continue
        rels = [rel for rel in middle.relations or [] if rel.via]
        for mine in rels:
            if not _points_to(ontology, middle, mine, table) or any(row[p.to] is None for p in mine.via or []):
                continue
            others: list[tuple[Relation, Table]] = []
            for rel in rels:
                if rel is mine:
                    continue
                target = _target(ontology, middle, rel)
                reason = "参照先の表が OM に無い" if target is None else hidden_reason(target)
                if target is None or reason is not None:
                    omitted.append(OmittedRelation(relation=rel.id, table=middle.name,
                                                   reason=f"relation の表の先がドメインでない: {reason}"))
                else:
                    others.append((rel, target))
            if not others:
                continue
            where = Equals({p.from_: row[p.to] for p in mine.via or []})
            profile = _profile(ontology, middle, where, reader, today)
            if profile.count == 0:
                continue
            columns = list(dict.fromkeys(p.from_ for rel, _ in others for p in rel.via or []))
            referencing = {p.from_ for rel in rels for p in rel.via or []}
            surrogate = (set(middle.identity.primary) if middle.identity and len(middle.identity.primary) == 1
                         and not set(middle.identity.primary) & referencing else set[str]())
            # つながりの属性：参照の列と、relation の行の代理キー（1列で参照でない主キー。行の番号でしかない）を除く
            attributes = _with_first(profile.time_columns,
                                     [c for c in visible_columns(ontology, middle) if c not in referencing | surrogate], [])
            schema, name = _schema_table(middle)
            order = list(middle.identity.primary) if middle.identity else columns
            links = reader.find(schema, name, list(dict.fromkeys([*columns, *order, *attributes])),
                                profile.order or order, where, profile.row_limit(limit), profile.descending)
            for rel, target in others:
                tr = _reading(ontology, target)
                tschema, tname = _schema_table(target)
                rows: list[InstanceSummary] = []
                missing = 0
                for link in links.rows:  # relation の行ごとに1つ（同じ先が別の期間・属性で2回出ることがある）
                    values = {p.to: link[p.from_] for p in rel.via or []}
                    if any(v is None for v in values.values()):
                        continue
                    found = reader.find(tschema, tname, tr.columns, tr.key, Equals(values), 1)
                    if found.rows:
                        summary = _summary(ontology, tr, found.rows[0], reader)
                        rows.append(summary.model_copy(update={"link_cells": [
                            _cell(ontology, middle, c, link[c], cut=True) for c in attributes] or None}))
                    else:
                        missing += 1
                groups.append(RelatedGroup.model_validate(dict(
                    kind=RelatedKind.via, relation=rel.id, relation_type=rel.type, table=target.id, name=target.name,
                    columns=[p.from_ for p in rel.via or []], via_table=middle.id, via_name=middle.name, rows=rows,
                    truncated=links.truncated, missing=missing, **profile.fields)))
    return groups


def _with_first(first: Sequence[str], rest: Sequence[str], exclude: Sequence[str]) -> list[str]:
    """時刻・期間の列を先頭に（rows で必ず見えるように）。添える列の数は SUMMARY_CELLS まで。"""
    return list(dict.fromkeys([*first, *(c for c in rest if c not in exclude)]))[:max(SUMMARY_CELLS, len(first))]


# ---------------------------------------------------------------- 近隣の集まりの見せ方（仕様 9.4 の近隣への具体化）

LIST_MAX = 10
"""これ以下の件数なら個々のサマリを並べる。"""
SERIES_MIN = 30
"""出来事の時刻の列があり、これを超える件数なら時系列。"""
RECENT = 5
"""集計や時系列に添える、新しい（または先頭の）行の数。"""
BREAKDOWN_LIMIT = 6
MEASURES_MAX = 3
SERIES_MEASURES_MAX = 2
DAY_SPAN = 62
"""期間の幅がこれ以下の日数なら日ごと、MONTH_SPAN 以下なら月ごと、それより長ければ年ごとに区切る。"""
MONTH_SPAN = 1100


def group_view(count: int, has_time: bool, has_period: bool) -> GroupView:
    """近隣の集まりの見せ方。上から最初に当たったもの：出来事の時刻があって件数が多い → 時系列、期間の列がある → 期間の帯、
    件数が少ない → 一覧、それ以外 → 集計。"""
    if count < 0:
        raise ValueError(f"件数が負: {count}")
    if has_time and count > SERIES_MIN:
        return GroupView.time_series
    if has_period:
        return GroupView.timeline
    if count <= LIST_MAX:
        return GroupView.list
    return GroupView.summary


def bucket_unit(first: date, last: date) -> BucketUnit:
    """時系列の区切り：期間の幅から。"""
    days = (last - first).days
    if days < 0:
        raise ValueError(f"期間が逆: {first} > {last}")
    return BucketUnit.day if days <= DAY_SPAN else BucketUnit.month if days <= MONTH_SPAN else BucketUnit.year


def _role_column(ontology: Ontology, table: Table, role: str) -> str | None:
    return next((c for c in visible_columns(ontology, table)
                 if (d := _semantic(ontology, table, c)) is not None and d.role == role), None)


@dataclass(frozen=True)
class _Plan:
    """集まりの表（children なら子の表、via なら relation の表）の、OM から決まる集計の計画（件数に依らない部分）。"""

    time: str | None
    period: tuple[str, str] | None
    sums: list[tuple[str, MeasureOp]]
    """集まり全体で集計する尺度（加法は合計、非加法は平均）。"""
    snapshot: list[str]
    """最新の時点の行だけで合計する半加法の尺度。"""
    skipped: list[SkippedMeasure]
    breakdown: str | None


def measure_plan(ontology: Ontology, table: Table) -> _Plan:
    """尺度の集計を加法性から決める：additive は合計、non-additive は平均。semi-additive（残高・在庫など）は集まり全体では
    集計しない（時間の軸が無いと OM から言えない。役割の付け漏れもある）。出来事の時刻の列があれば、最新の時点の行だけで合計する。
    尺度が多ければ先頭の MEASURES_MAX 個（省いたものは理由つき）。"""
    time = _role_column(ontology, table, SemanticRole.event_time.value)
    start = _role_column(ontology, table, SemanticRole.valid_from.value)
    end = _role_column(ontology, table, SemanticRole.valid_to.value)
    key = set(table.identity.primary) if table.identity else set[str]()
    sums: list[tuple[str, MeasureOp]] = []
    snapshot: list[str] = []
    skipped: list[SkippedMeasure] = []
    breakdown: str | None = None
    for c in visible_columns(ontology, table):
        d = _semantic(ontology, table, c)
        if d is None or c in key:
            continue
        if d.type == ValueType.enum.value and d.values and breakdown is None:
            breakdown = c
        if d.additivity is None:
            continue
        if d.additivity == Additivity.semi_additive.value:
            if time is None:
                skipped.append(SkippedMeasure(column=c, reason="半加法（残高・在庫など）は、時間の軸が無いと言えないので合計しない"))
            else:
                snapshot.append(c)
        elif len(sums) >= MEASURES_MAX:
            skipped.append(SkippedMeasure(column=c, reason=f"尺度が多いので先頭の {MEASURES_MAX} 個だけ集計した"))
        else:
            sums.append((c, MeasureOp.sum if d.additivity == Additivity.additive.value else MeasureOp.avg))
    return _Plan(time, (start, end) if start and end else None, sums, snapshot, skipped, breakdown)


@dataclass(frozen=True)
class _Profile:
    count: int
    view: GroupView
    time_columns: list[str]
    order: list[str]
    descending: bool
    fields: dict[str, object]

    def row_limit(self, limit: int) -> int:
        return limit if self.view in (GroupView.list, GroupView.timeline) else min(RECENT, limit)


_OP: dict[MeasureOp, Op] = {MeasureOp.sum: "sum", MeasureOp.avg: "avg"}


def _profile(ontology: Ontology, table: Table, where: Equals, reader: RecordReader, today: date) -> _Profile:
    """集まりの件数と、見せ方に応じた集計（ソースに押し下げる）。件数で見せ方を決めてから、要る集計だけを問う。"""
    plan = measure_plan(ontology, table)
    schema, name = _schema_table(table)
    bounds = [Measure("min", plan.time), Measure("max", plan.time)] if plan.time else []
    [whole] = reader.aggregate(schema, name, where, None, [Measure("count", None), *bounds], 1).groups
    count = _int(whole.values[0])
    view = group_view(count, plan.time is not None, plan.period is not None)
    time_columns = [c for c in (plan.time, *(plan.period or ())) if c is not None]
    order = [plan.time] if plan.time else [plan.period[0]] if plan.period else []
    fields: dict[str, object] = {
        "view": view, "count": count, "time_column": plan.time,
        "period_from": plan.period[0] if plan.period else None, "period_to": plan.period[1] if plan.period else None,
    }
    profile = _Profile(count, view, time_columns, order, bool(order), fields)
    if count == 0 or view == GroupView.list:
        return profile  # 一覧では集計を見せない（個々の行がすべて見える）
    units, mixed = _units(table, [*(c for c, _ in plan.sums), *plan.snapshot], schema, name, where, reader)
    excluded = {m.column for m in mixed}
    measures = [(c, op) for c, op in plan.sums if c not in excluded]
    snapshot = [c for c in plan.snapshot if c not in excluded]
    skipped = list({m.column: m for m in [*plan.skipped, *mixed]}.values())
    if measures:
        [totals] = reader.aggregate(schema, name, where, None, [Measure(_OP[op], c) for c, op in measures], 1).groups
        fields["measures"] = [_measure_value(c, op, v, units.get(c)) for (c, op), v in zip(measures, totals.values, strict=True)]
    fields["skipped_measures"] = skipped or None
    if plan.breakdown is not None:
        found = reader.aggregate(schema, name, where, plan.breakdown, [Measure("count", None)], BREAKDOWN_LIMIT)
        d = _semantic(ontology, table, plan.breakdown)
        fields |= {"breakdown_column": plan.breakdown, "breakdown_truncated": found.truncated,
                   "breakdown": [BreakdownValue(value=display(g.key, d), count=_int(g.values[0])) for g in found.groups]}
    if plan.time is None:
        return profile
    first, last = whole.values[1], whole.values[2]
    if not isinstance(first, date) or not isinstance(last, date):
        raise BrowseError(f"{table.name} の出来事の時刻の列 {plan.time} の値が日付・日時でない"
                          f"（{type(first).__name__}）。OM の意味型か、ソースの型が食い違っている")
    additive = [c for c, op in measures if op == MeasureOp.sum]
    if view == GroupView.time_series:
        unit = bucket_unit(_day(first), _day(last))
        series_measures = additive[:SERIES_MEASURES_MAX]
        found = reader.aggregate(schema, name, where, TimeBucket(plan.time, _UNIT[unit]),
                                 [Measure("count", None), *(Measure("sum", c) for c in series_measures)], MONTH_SPAN)
        fields |= {"series_unit": unit, "series_measures": series_measures or None,
                   "series": _fill(found.groups, unit, len(series_measures))}
    found = reader.aggregate(schema, name, where, TimeBucket(plan.time, "day"),
                             [Measure("count", None), *(Measure("sum", c) for c in additive)], 1, latest_first=True)
    g = found.groups[0]  # 件数が 1 以上で、時刻の列の最小・最大が日付なので、少なくとも1つある
    day = _day(cast(date, g.key))
    latest: dict[str, object] = {
        "day": day, "is_today": day == today, "count": _int(g.values[0]),
        "measures": [_measure_value(c, MeasureOp.sum, v, units.get(c)) for c, v in zip(additive, g.values[1:], strict=True)]
        or None}
    if snapshot:  # 半加法は、最新の時点（時刻の最大の値）の行だけで合計する
        at = Equals({**where.values, plan.time: last})
        [shot] = reader.aggregate(schema, name, at, None, [Measure("sum", c) for c in snapshot], 1).groups
        latest |= {"snapshot_at": display(last, None),
                   "snapshot": [_measure_value(c, MeasureOp.sum, v, units.get(c))
                                for c, v in zip(snapshot, shot.values, strict=True)]}
    fields["latest"] = LatestDay.model_validate(latest)
    return profile


_UNIT: dict[BucketUnit, TimeUnit] = {BucketUnit.day: "day", BucketUnit.month: "month", BucketUnit.year: "year"}


def _fill(groups: Sequence[Group], unit: BucketUnit, sums: int) -> list[SeriesPoint]:
    """行の無い区切りを count 0（合計は値無し）で埋める（時間軸を詰めない）。区切りは古い順。"""
    found = {_day(cast(date, g.key)): g for g in groups}
    if not found:
        return []
    points: list[SeriesPoint] = []
    current, last = min(found), max(found)
    while current <= last:
        g = found.get(current)
        points.append(SeriesPoint(start=current, count=_int(g.values[0]) if g else 0,
                                  sums=[SeriesSum(value=float(cast(float, v)) if v is not None else None)
                                        for v in (g.values[1:] if g else [None] * sums)]))
        current = _next(current, unit)
    return points


def _next(day: date, unit: BucketUnit) -> date:
    if unit == BucketUnit.day:
        return day + timedelta(days=1)
    if unit == BucketUnit.month:
        return date(day.year + day.month // 12, day.month % 12 + 1, 1)
    return date(day.year + 1, 1, 1)


def _int(value: object) -> int:
    if not isinstance(value, int):
        raise AssertionError(f"件数が整数でない（読み手の誤り）: {value!r}")
    return value


def _units(table: Table, columns: Sequence[str], schema: str, name: str, where: Equals,
           reader: RecordReader) -> tuple[dict[str, str], list[SkippedMeasure]]:
    """尺度の単位：固定ならそれ。単位の列なら、集まりの中で値が1つのときだけ集計する（混ざっていれば合計しない。理由を返す）。
    同じ単位の列は1回だけ問う。"""
    units: dict[str, str] = {}
    mixed: list[SkippedMeasure] = []
    asked: dict[str, Aggregated] = {}
    for c in columns:
        unit = (table.columns or {})[c].unit
        if unit is not None and unit.fixed is not None:
            units[c] = str(unit.fixed)
        elif unit is not None and unit.column is not None:
            if unit.column not in asked:
                asked[unit.column] = reader.aggregate(schema, name, where, unit.column, [Measure("count", None)], 1)
            found = asked[unit.column]
            if found.truncated:
                mixed.append(SkippedMeasure(column=c, reason=f"単位が混ざる（列 {unit.column} の値が2つ以上）ので集計しない"))
            elif found.groups and found.groups[0].key is not None:
                units[c] = str(found.groups[0].key).strip()
    return units, mixed


def _measure_value(column: str, op: MeasureOp, value: object, unit: str | None) -> MeasureValue:
    """平均は小数 4 桁に丸め、末尾の 0 を落とす（Decimal も浮動小数も）。"""
    if op == MeasureOp.avg and isinstance(value, Decimal | float):
        value = Decimal(str(round(value, 4))).normalize()
    return MeasureValue(column=column, op=op, value=display(value, None), unit_text=unit)


def _day(value: date) -> date:
    return value.date() if isinstance(value, datetime) else value
