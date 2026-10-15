"""同じ意味の列（外部キーの組）と、ドメインの伝播。

- 組：外部キーの列の対応を推移的に閉じたもの。素朴な実装（連結成分の探索）と一致し、主張の順に依らない
- 伝播：組のドメインを、まだ付いていない列に付ける。冪等で、検証を通り、組の中でドメインが食い違わない
"""

import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mandara.connectors.postgres.catalog_reader import PostgresCatalogReader
from mandara.extractors.catalog import extract
from mandara.modeler.meaning import (
    PROPAGATOR,
    InconsistentGroupError,
    MeaningGroups,
    Member,
    column_families,
    group_domain,
    label,
    propagate,
    same_meaning_groups,
)
from mandara.modeler.rules import propose
from mandara.om.claims import new_claim
from mandara.om.invariants import check
from mandara.om.model import (
    Claim,
    ClaimPredicate,
    ClaimSource,
    ClaimSourceKind,
    Column,
    ColumnDomain,
    ReviewStatus,
    SourceRef,
    ValueType,
)
from mandara.om.names import qualify
from mandara.om.ontology import Ontology
from mandara.source.catalog import Scope

# ---------------------------------------------------------------- 組

TABLES = ["a", "b", "c", "d"]
COLUMNS = ["x", "y", "z"]


def _reference(table: str, columns: list[str], target: str, target_columns: list[str], locator: str) -> Claim:
    return new_claim(
        subject=SourceRef(source="src", schema_name="s", table=table), predicate=ClaimPredicate.references,
        source=ClaimSource(kind=ClaimSourceKind.schema, locator=locator), extracted_by="test", confidence=1.0,
        columns=columns, target=SourceRef(source="src", schema_name="s", table=target), target_columns=target_columns,
        on_delete="no_action", on_update="no_action")


@st.composite
def references(draw: st.DrawFn) -> list[Claim]:
    out: list[Claim] = []
    for i in range(draw(st.integers(0, 8))):
        n = draw(st.integers(1, 2))
        columns = draw(st.lists(st.sampled_from(COLUMNS), min_size=n, max_size=n, unique=True))
        target_columns = draw(st.lists(st.sampled_from(COLUMNS), min_size=n, max_size=n, unique=True))
        out.append(_reference(draw(st.sampled_from(TABLES)), columns, draw(st.sampled_from(TABLES)), target_columns,
                              f"fk{i}"))
    return out


def _pairs(claims: list[Claim]) -> list[tuple[Member, Member, str]]:
    return [((qualify("s", c.subject.table), a), (qualify("s", c.target.table), b), c.id)
            for c in claims if c.target is not None
            for a, b in zip(c.columns or [], c.target_columns or [], strict=True)]


def _components(claims: list[Claim]) -> set[frozenset[Member]]:
    """素朴な実装：外部キーの列の対応を辺とするグラフの連結成分（辺を持つ頂点だけ）。"""
    edges: dict[Member, set[Member]] = {}
    for a, b, _ in _pairs(claims):
        edges.setdefault(a, set()).add(b)
        edges.setdefault(b, set()).add(a)
    seen: set[Member] = set()
    out: set[frozenset[Member]] = set()
    for start in edges:
        if start in seen:
            continue
        component, stack = {start}, [start]
        while stack:
            for nxt in edges[stack.pop()] - component:
                component.add(nxt)
                stack.append(nxt)
        seen |= component
        out.add(frozenset(component))
    return out


@settings(max_examples=200)
@given(claims=references(), seed=st.integers())
def test_groups_are_the_connected_components_of_foreign_keys(claims: list[Claim], seed: int) -> None:
    groups = same_meaning_groups(claims)
    assert {g.members for g in groups.groups} == _components(claims)
    # 根拠：組の根拠は、その組の列の対応を持つ外部キーちょうど
    for g in groups.groups:
        assert set(g.evidence) == {cid for a, _, cid in _pairs(claims) if a in g.members}
    # 主張の順に依らない
    shuffled = list(claims)
    random.Random(seed).shuffle(shuffled)
    assert same_meaning_groups(shuffled) == groups


def test_non_reference_claims_do_not_make_groups() -> None:
    claim = new_claim(subject=SourceRef(source="src", schema_name="s", table="a", column="x"),
                      predicate=ClaimPredicate.not_null, source=ClaimSource(kind=ClaimSourceKind.schema, locator="l"),
                      extracted_by="test", confidence=1.0)
    assert same_meaning_groups([claim]) == MeaningGroups(())


# ---------------------------------------------------------------- AdventureWorks での組と伝播

SCOPE = Scope(("humanresources", "person", "production", "purchasing", "sales"))
CLAIMS = extract("adventureworks", PostgresCatalogReader("dbname=adventureworks").read(SCOPE))
V0 = propose("adventureworks", SCOPE.schemas, CLAIMS).ontology
GROUPS = same_meaning_groups(CLAIMS)
FAMILIES = column_families(CLAIMS)


def test_adventureworks_business_entity_ids_form_one_group() -> None:
    group = GROUPS.of("sales.customer", "personid")
    assert group is not None
    for member in [("person.person", "businessentityid"), ("sales.store", "businessentityid"),
                   ("humanresources.employee", "businessentityid"), ("sales.salesorderheader", "salespersonid")]:
        assert member in group.members
    assert GROUPS.of("production.product", "productid") != group


def _with_domain(ontology: Ontology, table: str, column: str, domain: ColumnDomain, confidence: float) -> Ontology:
    vocabulary = ontology.vocabulary.model_copy(update={
        "column_domains": {**(ontology.vocabulary.column_domains or {}), domain.name: domain}})
    target = next(t for t in ontology.tables if t.name == table)
    location = qualify(target.id, "columns", column)
    columns = target.columns or {}
    updated = columns[column].model_copy(update={"domain": domain.name, "confidence": confidence})
    return Ontology(vocabulary, ontology.tables).replace_annotation(location, updated)


ADDRESS_ID = ColumnDomain(name="address_id", type=ValueType.identifier, description="住所の ID")


def test_propagation_fills_the_group_and_is_idempotent() -> None:
    seeded = _with_domain(V0, "person.address", "addressid", ADDRESS_ID, 0.8)
    once = propagate(seeded, GROUPS, FAMILIES)
    group = GROUPS.of("person.address", "addressid")
    assert group is not None
    expected = {label(m) for m in group.members if m != ("person.address", "addressid")
                and any(t.name == m[0] for t in V0.tables)}
    assert {label(f.member) for f in once.filled} == expected and {f.domain for f in once.filled} == {"address_id"}
    assert check(once.ontology) == []
    for name in ("billtoaddressid", "shiptoaddressid"):
        header = next(t for t in once.ontology.tables if t.name == "sales.salesorderheader")
        column = (header.columns or {})[name]
        assert (column.domain, column.confidence, column.inferred_by) == ("address_id", 0.8, PROPAGATOR)
        assert set(group.evidence) <= set(column.evidence or [])
    twice = propagate(once.ontology, GROUPS, FAMILIES)
    assert twice.filled == () and twice.ontology == once.ontology


def test_units_are_not_guessed_when_propagating() -> None:
    amount = ColumnDomain(name="amount", type=ValueType.money, description="金額", requires_unit=True)
    seeded = _with_domain(V0, "person.address", "addressid", amount, 0.8)
    result = propagate(seeded, GROUPS, FAMILIES)
    assert result.filled == ()
    assert result.not_filled and all(n.reason == "単位の要るドメインは、単位を決められないので伝播しない"
                                     for n in result.not_filled)


def test_conflicting_domains_in_a_group_are_an_error() -> None:
    other = ColumnDomain(name="place_id", type=ValueType.identifier, description="別名")
    seeded = _with_domain(_with_domain(V0, "person.address", "addressid", ADDRESS_ID, 0.8),
                          "sales.salesorderheader", "billtoaddressid", other, 0.8)
    group = GROUPS.of("person.address", "addressid")
    assert group is not None
    with pytest.raises(InconsistentGroupError):
        group_domain(seeded, group)
    with pytest.raises(InconsistentGroupError):
        propagate(seeded, GROUPS, FAMILIES)


# ---------------------------------------------------------------- 伝播の法則

POOL = [ADDRESS_ID, ColumnDomain(name="thing_id", type=ValueType.identifier, description="例"),
        ColumnDomain(name="kind_code", type=ValueType.code, description="例"),
        ColumnDomain(name="amount", type=ValueType.money, description="例", requires_unit=True),
        ColumnDomain(name="day", type=ValueType.date, description="例")]
SEEDABLE = [g for g in GROUPS.groups if sum(any(t.name == m[0] for t in V0.tables) for m in g.members) >= 2]


@st.composite
def seeded_ontologies(draw: st.DrawFn) -> Ontology:
    """組ごとに高々1つのドメインを、組の列のいくつかに付けた OM（人の判断、確信度の無い列を含む）。"""
    vocabulary = V0.vocabulary.model_copy(update={"column_domains": {d.name: d for d in POOL}})
    ontology = Ontology(vocabulary, V0.tables)
    by_name = {t.name: t for t in V0.tables}
    for group in draw(st.lists(st.sampled_from(SEEDABLE), max_size=6, unique_by=lambda g: g.members)):
        domain = draw(st.sampled_from(POOL)).name
        present = sorted(m for m in group.members if m[0] in by_name)
        for table, column in draw(st.lists(st.sampled_from(present), min_size=1, max_size=3, unique=True)):
            location = qualify(by_name[table].id, "columns", column)
            current = dict(ontology.annotations())[location]
            status = draw(st.sampled_from([ReviewStatus.proposed, ReviewStatus.proposed, ReviewStatus.approved]))
            ontology = ontology.replace_annotation(location, current.model_copy(update={
                "domain": domain, "status": status.value,
                "confidence": draw(st.none() | st.floats(0, 1))}))
        # ドメインの無いまま人が判断した列（伝播で変えてはいけない）
        for table, column in draw(st.lists(st.sampled_from(present), max_size=1)):
            location = qualify(by_name[table].id, "columns", column)
            current = dict(ontology.annotations())[location]
            if isinstance(current, Column) and current.domain is None:
                ontology = ontology.replace_annotation(location, current.model_copy(update={
                    "status": ReviewStatus.approved.value}))
    return ontology


def _column_states(ontology: Ontology) -> dict[Member, tuple[object, ...]]:
    return {(t.name, n): (c.domain, c.status, c.confidence) for t in ontology.tables
            for n, c in (t.columns or {}).items()}


@settings(max_examples=60, deadline=None)
@given(seeded=seeded_ontologies())
def test_propagation_laws(seeded: Ontology) -> None:
    result = propagate(seeded, GROUPS, FAMILIES)
    before, after = _column_states(seeded), _column_states(result.ontology)
    filled = {f.member: f.domain for f in result.filled}
    # 単調：付けた列以外は変えない（ドメインのある列、人が判断済みの列はそのまま）
    assert {m for m in before if before[m] != after[m]} == set(filled)
    for member, domain in filled.items():
        group = GROUPS.of(*member)
        assert group is not None and before[member][0] is None and before[member][1] == ReviewStatus.proposed.value
        # 付けたドメインは組のドメイン
        assert domain == group_domain(seeded, group) == after[member][0]
    # 勘定：組のドメインがあって、ドメインの無い列は、付けたか理由つきで付けなかったかのどちらか1つ
    expected = [m for g in GROUPS.groups if group_domain(seeded, g) is not None
                for m in sorted(g.members) if m in before and before[m][0] is None]
    assert sorted([*filled, *(n.member for n in result.not_filled)]) == sorted(expected)
    assert all(n.reason for n in result.not_filled)
    # 冪等
    again = propagate(result.ontology, GROUPS, FAMILIES)
    assert again.filled == () and again.ontology == result.ontology
