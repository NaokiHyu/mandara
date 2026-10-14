"""妥当な OM を生成する Hypothesis の戦略。

生成するのは不変条件（4.5）を満たす OM だけ。壊すのはテスト側（mutation）の責務。
- 推移的な関係（is-a / part-of / derived-from）で他の表を指すときは、添字の小さい表にしか向けないので循環は生まれない
- 列名には YAML 1.1 で真偽値や null に化ける名前（on / no / null など）や日本語も混ぜる
- 複合主キー、代替キー、役割、単位の列参照、self への関係（via_function / derived-from）も生成する
- 生成する列名（c… と TRICKY_NAMES）は、外部キー列（ref…）と単位列（…__unit）と重ならない
"""

from datetime import date

from hypothesis import strategies as st

from mandara.om.model import (
    Additivity,
    Cardinality,
    Column,
    ColumnDomain,
    ColumnPair,
    DomainRole,
    EnumValue,
    HasKind,
    Identity,
    IsARealization,
    KeyColumns,
    Relation,
    RelationType,
    ReviewStatus,
    Table,
    TableKind,
    UnitSource,
    Vocabulary,
)
from mandara.om.ontology import SELF, Ontology

VOCABULARY = Vocabulary(
    column_domains={
        "key": ColumnDomain(name="key", type="identifier"),
        "label": ColumnDomain(name="label", type="text"),
        "unit_code": ColumnDomain(name="unit_code", type="code"),
        "amount": ColumnDomain(name="amount", type="money", additivity=Additivity.additive, requires_unit=True),
        "at": ColumnDomain(name="at", type="datetime", role=DomainRole.event_time),
        "state": ColumnDomain(
            name="state",
            type="enum",
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

PLAIN_DOMAINS = ("label", "at", "state")
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
    def annotate[M: (Column, Relation, Table)](model: M) -> M:
        return model.model_copy(update=draw(provenance()))

    n = draw(st.integers(min_value=1, max_value=max_tables))
    tables: list[Table] = []
    for i in range(n):
        kind = draw(st.sampled_from(tuple(TableKind)))
        names = draw(st.lists(column_names, min_size=1, max_size=6, unique=True))
        pk_size = draw(st.integers(min_value=1, max_value=min(2, len(names))))
        primary, rest = names[:pk_size], names[pk_size:]

        columns: dict[str, Column] = {c: annotate(Column(name=c, domain="key", status=_PLACEHOLDER)) for c in primary}
        for c in rest:
            domain = draw(st.sampled_from((*PLAIN_DOMAINS, "amount")))
            unit = None
            if domain == "amount":
                if draw(st.booleans()):
                    unit = UnitSource(fixed=draw(st.sampled_from(("USD", "JPY", "each"))))
                else:
                    unit_col = f"{c}__unit"
                    columns[unit_col] = annotate(Column(name=unit_col, domain="unit_code", status=_PLACEHOLDER))
                    unit = UnitSource(column=unit_col)
            columns[c] = annotate(Column(name=c, domain=domain, unit=unit, status=_PLACEHOLDER))

        alternates = draw(
            st.lists(st.lists(st.sampled_from(sorted(columns)), min_size=1, max_size=2, unique=True), max_size=2)
        )

        relations: list[Relation] = []
        targets = draw(st.lists(st.integers(min_value=0, max_value=i - 1), max_size=3)) if i else []
        for k, t in enumerate(targets):
            target = tables[t]
            target_pk = target.identity.primary if target.identity is not None else []
            choices = [RelationType.has, RelationType.part_of]
            if len(target_pk) == len(primary):
                choices.append(RelationType.is_a)
            rtype = draw(st.sampled_from(choices))
            if rtype == RelationType.part_of and target.kind == TableKind.derived.value:
                rtype = RelationType.has
            rel_id = f"rel_t{i}_{k}"
            if rtype == RelationType.is_a:
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
                    columns[fk] = annotate(Column(name=fk, domain="key", status=_PLACEHOLDER))
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

        if kind != TableKind.derived and draw(st.booleans()):  # derived は part-of の全体側になれない
            tree = Relation(
                id=f"rel_t{i}_tree",
                type=RelationType.part_of,
                target=SELF,
                via_function="fn_path_parent",
                status=_PLACEHOLDER,
            )
            relations.append(annotate(tree))
        if draw(st.booleans()):
            derived = Relation(
                id=f"rel_t{i}_derived",
                type=RelationType.derived_from,
                target=SELF,
                function="fn_x",
                columns=draw(st.lists(st.sampled_from(sorted(columns)), min_size=1, max_size=2, unique=True)),
                status=_PLACEHOLDER,
            )
            relations.append(annotate(derived))

        has_amount = any(c.domain == "amount" for c in columns.values())
        needs_grain = kind == TableKind.event or (kind == TableKind.domain and has_amount)
        table = Table(
            id=f"tbl_t{i}",
            name=f"s.t{i}",
            source="src",
            context="ctx",
            kind=kind,
            grain=draw(texts.filter(str.strip)) if needs_grain or draw(st.booleans()) else None,
            identity=Identity(primary=primary, alternates=[KeyColumns(columns=a) for a in alternates] or None),
            columns=columns,
            relations=relations or None,
            status=_PLACEHOLDER,
        )
        tables.append(annotate(table))
    return Ontology(vocabulary=VOCABULARY, tables=tuple(tables))


@st.composite
def vocabularies(draw: st.DrawFn) -> Vocabulary:
    names = draw(st.lists(column_names, min_size=1, max_size=6, unique=True))
    domains: dict[str, ColumnDomain] = {}
    for name in names:
        if draw(st.booleans()):
            codes = draw(st.lists(texts, min_size=1, max_size=4, unique=True))
            values = [
                EnumValue(code=c, label=draw(texts), status=_PLACEHOLDER).model_copy(update=draw(provenance()))
                for c in codes
            ]
            domains[name] = ColumnDomain(name=name, type="enum", description=draw(st.none() | texts), values=values)
        else:
            domains[name] = ColumnDomain(
                name=name,
                type=draw(st.sampled_from(("identifier", "text", "money", "quantity"))),
                additivity=draw(st.none() | st.sampled_from(tuple(Additivity))),
                requires_unit=draw(st.none() | st.booleans()),
            )
    return Vocabulary(column_domains=domains)
