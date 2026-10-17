"""ドメインビューワのユースケース：承認済みの OM から、ドメイン（集合）とインスタンス（個）を見せる（仕様 7.6、T5.2 の手前）。

Application 層。どのソースかは知らない（RecordReader を外から渡す）。値はソースの表現から表示の文字列にする。
- ドメイン：承認済みの domain / event の表。relation の表はドメインにせず、リンクとしてたどる（n:m）。
  ドメインにしなかった表は理由とともに返す（黙って消さない）
- 集合：検索（文字の部分一致）の結果。表のイメージ
- 個：1行の値（技術列を除く）、参照先へのリンク（n:1）、近隣の集まり（参照してくる行 1:n、relation の表を挟んだ先 n:m）
- 列の等値で結べない関係（via_function）はたどらない
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from mandara.om.model import (
    Cell,
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
    ReviewStatus,
    SemanticRole,
    SemanticType,
    Table,
    TableKind,
    ValueType,
)
from mandara.om.names import split_qualified
from mandara.om.ontology import SELF, Ontology
from mandara.codecs.registry import BY_ENCODING, CodecError
from mandara.source.records import Contains, Equals, InvalidKeyError, NotKeyableError, RecordReader, key_text
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
    domains: list[DomainSummary] = []
    hidden: list[HiddenTable] = []
    for t in sorted(ontology.tables, key=lambda t: (t.context, t.name)):
        reason = hidden_reason(t)
        if reason is not None:
            hidden.append(HiddenTable(table=t.id, name=t.name, reason=reason))
            continue
        domains.append(DomainSummary(table=t.id, name=t.name, context=t.context, kind=t.kind,  # pyright: ignore[reportArgumentType]
                                     grain=t.grain, label_columns=label_columns(ontology, t),
                                     searchable=searchable(ontology, t)))
    sources = {t.source for t in ontology.tables if hidden_reason(t) is None}
    return DomainIndex(ontology=ontology_id, domains=domains, hidden=hidden, readable=sources <= connected)


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


def _summary(ontology: Ontology, r: _Reading, row: Mapping[str, object], cut: bool = True,
             skip: Sequence[str] = ()) -> InstanceSummary:
    """skip：添える列から外す列（近隣の集まりで、このインスタンスを指す列。どの行も同じ値になる）。
    key は往復できるキーの文字列（key_text）。表示の文字列（display）とは別。"""
    key = _key(r, row)
    parts = [text for c in r.labels if (text := _cell(ontology, r.table, c, row[c], cut=False).value)]
    cells = [_cell(ontology, r.table, c, row[c], cut) for c in r.cells if c not in skip]
    return InstanceSummary(table=r.table.id, key=key, label=" ".join(parts) if parts else " · ".join(key), cells=cells)


def search(ontology: Ontology, table_id: str, reader: RecordReader, query: str | None,
           limit: int = SEARCH_LIMIT) -> DomainRows:
    """ドメインの集合：query があれば検索の列とキーの部分一致、無ければ先頭から。"""
    table = _domain(ontology, table_id)
    r = _reading(ontology, table)
    text = (query or "").strip()
    columns = searchable(ontology, table)
    where = Contains(tuple(dict.fromkeys([*r.key, *columns])), text) if text else None
    schema, name = _schema_table(table)
    records = reader.find(schema, name, r.columns, r.key, where, limit)
    return DomainRows(table=table.id, name=table.name, query=text or None,
                      columns=[*r.labels, *r.cells],
                      rows=[_summary(ontology, r, row) for row in records.rows], limit=limit,
                      truncated=records.truncated)


def instance(ontology: Ontology, table_id: str, key: Sequence[str], reader: RecordReader,
             related_limit: int = RELATED_LIMIT) -> Instance:
    """インスタンスの個のビュー：値、参照先へのリンク（n:1）、近隣の集まり（1:n の子、relation の表を挟んだ n:m）。
    key はキーの文字列（key_text）。たどらなかった関係は理由とともに omitted に返す。"""
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
    me = _summary(ontology, r, row, cut=False)
    fields = [_cell(ontology, table, c, row[c], cut=False) for c in shown]
    links: list[InstanceLink] = []
    for rel in relations:
        target = _target(ontology, table, rel)
        reason = "参照先の表が OM に無い" if target is None else hidden_reason(target)
        if target is None or reason is not None:
            omitted.append(OmittedRelation(relation=rel.id, table=table.name, reason=f"参照先がドメインでない: {reason}"))
            continue
        links.append(_link(ontology, table, target, rel, row, reader))
    related = [*_children(ontology, table, row, reader, related_limit, omitted),
               *_through_relations(ontology, table, row, reader, related_limit, omitted)]
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
        summary = _summary(ontology, tr, found.rows[0]) if found.rows else None
        state = LinkState.found if found.rows else LinkState.missing
    return InstanceLink(relation=rel.id, relation_type=rel.type, role=rel.role, columns=[p.from_ for p in pairs],
                        target_table=target.id, target_name=target.name, state=state, target=summary)


def _children(ontology: Ontology, table: Table, row: Mapping[str, object], reader: RecordReader,
              limit: int, omitted: list[OmittedRelation]) -> list[RelatedGroup]:
    """このインスタンスを参照してくる、他のドメインの行（1:n）。参照してくる表がドメインでなければ理由を omitted に足す
    （relation の表は _through_relations が n:m としてたどる）。"""
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
            cr = _reading(ontology, other)
            schema, name = _schema_table(other)
            found = reader.find(schema, name, cr.columns, cr.key, Equals({p.from_: row[p.to] for p in rel.via or []}),
                                limit)
            if found.rows:
                columns = [p.from_ for p in rel.via or []]
                groups.append(RelatedGroup(kind=RelatedKind.children, relation=rel.id, relation_type=rel.type,
                                           table=other.id, name=other.name, columns=columns,
                                           rows=[_summary(ontology, cr, x, skip=columns) for x in found.rows],
                                           truncated=found.truncated, missing=0))
    return groups


def _through_relations(ontology: Ontology, table: Table, row: Mapping[str, object], reader: RecordReader,
                       limit: int, omitted: list[OmittedRelation]) -> list[RelatedGroup]:
    """relation の表を挟んだ先のインスタンス（n:m）。relation の表そのものは出さない。
    先の行が無ければ missing に数える。先の表がドメインでなければ理由を omitted に足す。"""
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
            columns = list(dict.fromkeys(p.from_ for rel, _ in others for p in rel.via or []))
            schema, name = _schema_table(middle)
            order = list(middle.identity.primary) if middle.identity else columns
            links = reader.find(schema, name, list(dict.fromkeys([*columns, *order])), order,
                                Equals({p.from_: row[p.to] for p in mine.via or []}), limit)
            for rel, target in others:
                tr = _reading(ontology, target)
                tschema, tname = _schema_table(target)
                rows: list[InstanceSummary] = []
                missing = 0
                seen: set[tuple[object, ...]] = set()
                for link in links.rows:
                    values = {p.to: link[p.from_] for p in rel.via or []}
                    if any(v is None for v in values.values()) or tuple(values.values()) in seen:
                        continue
                    seen.add(tuple(values.values()))
                    found = reader.find(tschema, tname, tr.columns, tr.key, Equals(values), 1)
                    if found.rows:
                        rows.append(_summary(ontology, tr, found.rows[0]))
                    else:
                        missing += 1
                if rows or missing:
                    groups.append(RelatedGroup(kind=RelatedKind.via, relation=rel.id, relation_type=rel.type,
                                               table=target.id, name=target.name,
                                               columns=[p.from_ for p in rel.via or []], via_table=middle.id,
                                               via_name=middle.name, rows=rows, truncated=links.truncated,
                                               missing=missing))
    return groups
