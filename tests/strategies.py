"""妥当な OM を生成する Hypothesis の戦略。

生成するのは不変条件（4.5）を満たす OM だけ。壊すのはテスト側（mutation）の責務。
- 推移的な関係（is-a / part-of / classified-as / derived-from）で他の表を指すときは、添字の小さい表にしか向けないので循環は生まれない
- 列名には YAML 1.1 で真偽値や null に化ける名前（on / no / null など）や日本語も混ぜる
- 複合主キー、代替キー、役割、単位の列参照、物理的な表現、self への関係（via_function / derived-from）も生成する
- 中間表（relation）には辺（edge）も生成する
- 生成する列名（c… と TRICKY_NAMES）は、外部キー列（ref… / edge…）と単位列（…__unit）と重ならない
"""

from datetime import date

from hypothesis import strategies as st

from mandara.om.claims import SHAPES, new_claim
from mandara.om.model import (
    Additivity,
    Claim,
    ClaimPredicate,
    ClaimSource,
    ClaimSourceKind,
    Cardinality,
    Column,
    SemanticType,
    ColumnPair,
    SemanticRole,
    Edge,
    EnumValue,
    HasKind,
    Identity,
    IsARealization,
    KeyColumns,
    ObjectKind,
    ReferentialAction,
    Relation,
    RelationType,
    ReviewStatus,
    SourceEncoding,
    SourceRef,
    Table,
    TableKind,
    TypeFamily,
    Unit,
    UnitSource,
    ValueType,
    Vocabulary,
)
from mandara.om.ontology import SELF, Ontology

VOCABULARY = Vocabulary(
    semantic_types={
        "key": SemanticType(name="key", type=ValueType.identifier),
        "label": SemanticType(name="label", type=ValueType.text),
        "unit_code": SemanticType(name="unit_code", type=ValueType.code),
        "amount": SemanticType(name="amount", type=ValueType.money, additivity=Additivity.additive, requires_unit=True),
        "at": SemanticType(name="at", type=ValueType.datetime, role=SemanticRole.event_time),
        "point": SemanticType(name="point", type=ValueType("geo.point")),
        "state": SemanticType(
            name="state",
            type=ValueType.enum,
            values=[
                EnumValue(
                    code="1", label="open", status=ReviewStatus.approved, reviewed_by="alice", reviewed_at=date(2026, 10, 1)
                ),
                EnumValue(
                    code="2", label="closed", status=ReviewStatus.proposed, confidence=0.7, evidence=["clm_x"], inferred_by="t"
                ),
            ],
        ),
    }
)

PLAIN_DOMAINS = ("label", "at", "state", "point")
TRICKY_NAMES = ("on", "no", "yes", "null", "true", "False", "~", "名前", "a b", "1")

texts = st.text(alphabet=st.characters(codec="utf-8", exclude_categories=("Cs", "Cc")), min_size=1, max_size=12)
column_names = st.one_of(st.sampled_from(TRICKY_NAMES), st.from_regex(r"c[a-z0-9]{0,8}", fullmatch=True))

_PLACEHOLDER = ReviewStatus.rejected
"""生成時の仮の状態。直後に provenance() で上書きする。"""


@st.composite
def provenance(draw: st.DrawFn) -> dict[str, object]:
    """注釈の共通フィールド。状態ごとに 4.4 の必須項目を満たす。model_copy(update=...) で当てる。"""
    status = draw(st.sampled_from(tuple(ReviewStatus)))
    fields: dict[str, object] = {"status": status.value}
    if status == ReviewStatus.proposed:
        fields |= {
            "confidence": draw(st.floats(min_value=0.0, max_value=1.0)),
            "evidence": draw(st.lists(st.from_regex(r"clm_[0-9a-z]{1,6}", fullmatch=True), min_size=1, max_size=3)),
            "inferred_by": draw(texts),
        }
    if status == ReviewStatus.approved:
        fields |= {"reviewed_by": draw(texts), "reviewed_at": draw(st.dates())}
    if draw(st.booleans()):
        fields["note"] = draw(texts)
    return fields


def _pairs(pairs: list[tuple[str, str]]) -> list[ColumnPair]:
    return [ColumnPair.model_validate({"from": a, "to": b}) for a, b in pairs]


@st.composite
def ontologies(draw: st.DrawFn, max_tables: int = 5) -> Ontology:
    def annotate[M: (Column, Relation, Edge, Table)](model: M) -> M:
        return model.model_copy(update=draw(provenance()))

    n = draw(st.integers(min_value=1, max_value=max_tables))
    tables: list[Table] = []
    for i in range(n):
        kind = draw(st.none() | st.sampled_from(tuple(TableKind)))  # None は未決（提案の段階）
        derived = draw(st.booleans())  # 計算で導出された表（kind とは別の軸。どの kind とも組み合わさる）
        names = draw(st.lists(column_names, min_size=1, max_size=6, unique=True))
        pk_size = draw(st.integers(min_value=1, max_value=min(2, len(names))))
        primary, rest = names[:pk_size], names[pk_size:]

        columns: dict[str, Column] = {c: annotate(Column(name=c, semantic_type="key", status=_PLACEHOLDER)) for c in primary}
        for c in rest:
            semantic_type = draw(st.sampled_from((*PLAIN_DOMAINS, "amount")))
            unit = None
            if semantic_type == "amount":
                if draw(st.booleans()):
                    unit = UnitSource(fixed=draw(st.sampled_from(tuple(Unit))))
                else:
                    unit_col = f"{c}__unit"
                    columns[unit_col] = annotate(Column(name=unit_col, semantic_type="unit_code", status=_PLACEHOLDER))
                    unit = UnitSource(column=unit_col)
            encoding = draw(st.none() | st.sampled_from(tuple(SourceEncoding))) if semantic_type == "point" else None
            columns[c] = annotate(Column(name=c, semantic_type=semantic_type, unit=unit, encoding=encoding, status=_PLACEHOLDER))

        # 代替キーは、主キーとも互いとも違う列の組
        alternates = draw(
            st.lists(st.lists(st.sampled_from(sorted(columns)), min_size=1, max_size=2, unique=True), max_size=2,
                     unique_by=lambda k: frozenset(k)).filter(lambda ks: frozenset(primary) not in map(frozenset, ks))
        )

        relations: list[Relation] = []
        # 同じ表を何度参照してもよいが、is-a（主キー同士の結合）は同じ表へ1本だけ（同じものを結ぶ関係を2度作らない）
        targets = draw(st.lists(st.integers(min_value=0, max_value=i - 1), max_size=3)) if i else []
        is_a_done: set[int] = set()
        for k, t in enumerate(targets):
            target = tables[t]
            target_pk = target.identity.primary if target.identity is not None else []
            choices = [RelationType.has, RelationType.part_of, RelationType.classified_as]
            if len(target_pk) == len(primary) and t not in is_a_done:
                choices.append(RelationType.is_a)
            rtype = draw(st.sampled_from(choices))
            if rtype == RelationType.part_of and target.derived:
                rtype = RelationType.has
            rel_id = f"rel_t{i}_{k}"
            if rtype == RelationType.is_a:
                is_a_done.add(t)
                relation = Relation(
                    id=rel_id,
                    type=rtype,
                    target=target.id,
                    realized_by=IsARealization.shared_key,
                    via=_pairs(list(zip(primary, target_pk, strict=True))),
                    status=_PLACEHOLDER,
                )
            else:
                fks = [f"ref{k}_{j}" for j in range(len(target_pk))]
                for fk in fks:
                    columns[fk] = annotate(Column(name=fk, semantic_type="key", status=_PLACEHOLDER))
                relation = Relation(
                    id=rel_id,
                    type=rtype,
                    target=target.id,
                    has_kind=HasKind.reference if rtype == RelationType.has else None,
                    role=draw(st.none() | st.from_regex(r"[a-z]{1,8}", fullmatch=True)),
                    via=_pairs(list(zip(fks, target_pk, strict=True))),
                    cardinality=Cardinality("n:1"),
                    status=_PLACEHOLDER,
                )
            relations.append(annotate(relation))

        if draw(st.booleans()):
            # 自己参照の木（部品表のような part-of、分類の木、上司のような参照）。derived は part-of の全体側になれない
            tree_type = draw(st.sampled_from((RelationType.has, RelationType.part_of, RelationType.classified_as)))
            if derived and tree_type == RelationType.part_of:
                tree_type = RelationType.has
            tree = Relation(
                id=f"rel_t{i}_tree",
                type=tree_type,
                has_kind=HasKind.reference if tree_type == RelationType.has else None,
                role=draw(st.none() | st.from_regex(r"[a-z]{1,8}", fullmatch=True)),
                target=SELF,
                via_function="fn_path_parent",
                status=_PLACEHOLDER,
            )
            relations.append(annotate(tree))
        if draw(st.booleans()):
            derivation = Relation(
                id=f"rel_t{i}_derived",
                type=RelationType.derived_from,
                target=SELF,
                function="fn_x",
                columns=draw(st.lists(st.sampled_from(sorted(columns)), min_size=1, max_size=2, unique=True)),
                status=_PLACEHOLDER,
            )
            relations.append(annotate(derivation))

        edge = None
        if kind == TableKind.relation and i and draw(st.booleans()):
            # 中間表の辺。始点の表は終点の表と同じか後ろ（添字が大きい）にして、循環を作らない
            end = draw(st.integers(min_value=0, max_value=i - 1))
            start = draw(st.integers(min_value=end, max_value=i - 1))
            ends: dict[str, str] = {}
            for side, t in (("from", start), ("to", end)):
                target = tables[t]
                target_pk = target.identity.primary if target.identity is not None else []
                fks = [f"edge{side}_{j}" for j in range(len(target_pk))]
                for fk in fks:
                    columns[fk] = annotate(Column(name=fk, semantic_type="key", status=_PLACEHOLDER))
                ends[side] = f"rel_t{i}_edge{side}"
                relations.append(annotate(Relation(
                    id=ends[side], type=RelationType.has, has_kind=HasKind.reference, target=target.id,
                    via=_pairs(list(zip(fks, target_pk, strict=True))), cardinality=Cardinality("n:1"),
                    status=_PLACEHOLDER,
                )))
            edge_types = [RelationType.has, RelationType.classified_as]
            if not tables[end].derived:
                edge_types.append(RelationType.part_of)
            edge = annotate(Edge.model_validate(
                {"type": draw(st.sampled_from(edge_types)), "from": ends["from"], "to": ends["to"], "status": _PLACEHOLDER}
            ))

        has_amount = any(c.semantic_type == "amount" for c in columns.values())
        needs_grain = kind == TableKind.event or (kind == TableKind.domain and has_amount)
        table = Table(
            id=f"tbl_t{i}",
            name=f"s.t{i}",
            source="src",
            context="ctx",
            kind=kind,
            derived=derived or None,
            grain=draw(texts.filter(str.strip)) if needs_grain or draw(st.booleans()) else None,
            identity=Identity(primary=primary, alternates=[KeyColumns(columns=a) for a in alternates] or None),
            columns=columns,
            relations=relations or None,
            edge=edge,
            status=_PLACEHOLDER,
        )
        if kind is None:  # 未決の表は approved にならない
            table = table.model_copy(update=draw(provenance().filter(lambda f: f["status"] != "approved")))
        else:
            table = annotate(table)
        tables.append(table)
    return Ontology(vocabulary=VOCABULARY, tables=tuple(tables))


@st.composite
def vocabularies(draw: st.DrawFn) -> Vocabulary:
    names = draw(st.lists(column_names, min_size=1, max_size=6, unique=True))
    semantic_types: dict[str, SemanticType] = {}
    for name in names:
        if draw(st.booleans()):
            codes = draw(st.lists(texts, min_size=1, max_size=4, unique=True))
            values = [
                EnumValue(code=c, label=draw(texts), status=_PLACEHOLDER).model_copy(update=draw(provenance()))
                for c in codes
            ]
            semantic_types[name] = SemanticType(name=name, type=ValueType.enum, description=draw(st.none() | texts), values=values)
        else:
            semantic_types[name] = SemanticType(
                name=name,
                type=draw(st.sampled_from(tuple(t for t in ValueType if t != ValueType.enum))),
                additivity=draw(st.none() | st.sampled_from(tuple(Additivity))),
                requires_unit=draw(st.none() | st.booleans()),
            )
    return Vocabulary(semantic_types=semantic_types)


# ---------------------------------------------------------------- 主張台帳


@st.composite
def claims(
    draw: st.DrawFn, source: str = "src", schema: str = "s", table: str | None = None,
    kinds: tuple[ClaimSourceKind, ...] = tuple(ClaimSourceKind),
) -> Claim:
    """妥当な主張を1件。述語ごとの形（mandara.om.claims.SHAPES）に従う。"""
    table = table if table is not None else draw(column_names)
    predicate = draw(st.sampled_from(tuple(ClaimPredicate)))
    shape = SHAPES[predicate.value]
    on_column = shape.subject == "column" or (shape.subject == "either" and draw(st.booleans()))
    subject = SourceRef(source=source, schema_name=schema, table=table, column=draw(column_names) if on_column else None)
    keys = st.lists(column_names, min_size=1, max_size=3, unique=True)
    payload: dict[str, object] = {}
    columns = draw(keys)
    if "columns" in shape.fields:
        payload["columns"] = columns
    if "target" in shape.fields:
        payload["target"] = SourceRef(source=source, schema_name=draw(column_names), table=draw(column_names))
        payload["target_columns"] = draw(st.lists(column_names, min_size=len(columns), max_size=len(columns)))
    if "on_delete" in shape.fields:
        payload["on_delete"] = draw(st.sampled_from(tuple(ReferentialAction)))
        payload["on_update"] = draw(st.sampled_from(tuple(ReferentialAction)))
    if "text" in shape.fields:
        payload["text"] = draw(texts)
    if "type_name" in shape.fields:
        payload["type_name"] = draw(texts)
    if "family" in shape.fields:
        payload["family"] = draw(st.sampled_from(tuple(TypeFamily)))
    if "position" in shape.fields:
        payload["position"] = draw(st.integers(min_value=1, max_value=1000))
    if "object_kind" in shape.fields:
        payload["object_kind"] = draw(st.sampled_from(tuple(ObjectKind)))
    if "discriminator" in shape.fields:
        payload["discriminator"] = draw(column_names)
        payload["discriminator_values"] = draw(st.lists(texts, min_size=1, max_size=3))
    if "fixed_unit" in shape.fields:
        payload["fixed_unit"] = draw(st.sampled_from(tuple(Unit)))
    if "encoding" in shape.fields:
        payload["encoding"] = draw(st.sampled_from(tuple(SourceEncoding)))
    if "distinct_values" in shape.fields:  # 包含の数：含まれる数は値の数とキーの値の数以下、値が無ければ最大の行数も 0
        distinct = draw(st.integers(0, 50))
        target_values = draw(st.integers(0, 60))
        payload["distinct_values"] = distinct
        payload["matched_values"] = draw(st.integers(0, min(distinct, target_values)))
        payload["target_values"] = target_values
        payload["max_repeats"] = 0 if distinct == 0 else draw(st.integers(1, 9))
    return new_claim(
        subject=subject,
        predicate=predicate,
        source=ClaimSource(kind=draw(st.sampled_from(kinds)), locator=draw(texts)),
        extracted_by=draw(texts),
        confidence=draw(st.floats(min_value=0.0, max_value=1.0)),
        **payload,
    )


@st.composite
def claim_ledgers(
    draw: st.DrawFn, source: str = "src", kinds: tuple[ClaimSourceKind, ...] = tuple(ClaimSourceKind)
) -> tuple[Claim, ...]:
    """複数の表にまたがる、ID の重複しない主張の集まり。"""
    tables = draw(st.lists(st.tuples(st.sampled_from(("s1", "スキーマ")), column_names), min_size=1, max_size=3, unique=True))
    drawn = [draw(claims(source=source, schema=schema, table=table, kinds=kinds)) for schema, table in tables for _ in range(draw(st.integers(1, 4)))]
    return tuple({c.id: c for c in drawn}.values())
