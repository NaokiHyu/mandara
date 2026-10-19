"""双子のキー（値の集合が同じキーの組）の元の表を問う段（mandara.modeler.twins。T2.7）。"""

from dataclasses import dataclass, field
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mandara.app import pipeline
from mandara.extractors import catalog as catalog_extractor
from mandara.llm.decision import ChoiceAnswer, DecisionRequest, DecisionResponse
from mandara.llm.model import BudgetExceededError, LlmError, Usage
from mandara.modeler import classify, nameless_joins, twins
from mandara.modeler.rules import propose
from mandara.om.claims import check_claims, new_claim
from mandara.om.invariants import check
from mandara.om.model import Claim, ClaimPredicate, ClaimSource, ClaimSourceKind, ObjectKind, SourceRef, TypeFamily
from mandara.om.ontology import Ontology
from mandara.source.catalog import Catalog, Column, Key, Scope, Table


def _column(name: str, position: int) -> Column:
    return Column(name, position, "integer", TypeFamily.integer, False, None, None, None)


def _table(name: str, *columns: str) -> Table:
    return Table("s", name, ObjectKind.table, None, tuple(_column(c, i) for i, c in enumerate(columns, 1)),
                 Key(f"pk_{name}", (columns[0],)), (), (), ())


def _ref(table: str) -> SourceRef:
    return SourceRef(source="src", schema_name="s", table=table)


def _included(table: str, column: str, target: str, key: str, distinct: int, matched: int, target_values: int,
              span: tuple[int, int], repeats: int = 1) -> Claim:
    return new_claim(subject=_ref(table), predicate=ClaimPredicate.included_in,
                     source=ClaimSource(kind=ClaimSourceKind.data, locator=f"{table}.{column} in {target}.{key}"),
                     extracted_by="test", confidence=1.0, columns=[column], target=_ref(target), target_columns=[key],
                     distinct_values=distinct, matched_values=matched, target_values=target_values, max_repeats=repeats,
                     value_span=span[0], target_values_in_span=span[1])


def _both(a: str, ka: str, b: str, kb: str, n: int, span: tuple[int, int]) -> list[Claim]:
    return [_included(a, ka, b, kb, n, n, n, span), _included(b, kb, a, ka, n, n, n, span)]


def _world() -> tuple[Ontology, list[Claim]]:
    """product と photo は同じ製品 ID（隙間の多い 500 値）。shift と reason は 1..5 の連番（偶然）。
    work.c3 は product（＝photo）を指す。列の名前は段 3 のように手がかりにならない（規則の結合が選ばない）。"""
    catalog = Catalog(Scope(("s",)), (_table("product", "productid"), _table("photo", "c1", "c2"),
                                      _table("shift", "shiftid"), _table("reason", "reasonid"),
                                      _table("work", "workid", "c3")))
    claims = [*catalog_extractor.extract("src", catalog),
              *_both("product", "productid", "photo", "c1", 500, (999, 500)),
              *_both("shift", "shiftid", "reason", "reasonid", 5, (5, 5)),
              _included("work", "c3", "product", "productid", 200, 200, 500, (999, 500), 3),
              _included("work", "c3", "photo", "c1", 200, 200, 500, (999, 500), 3),
              _included("photo", "c2", "shift", "shiftid", 3, 2, 5, (5, 4))]  # 包含が 100% でない
    assert check_claims(claims) == []
    return propose("src", ("s",), claims).ontology, claims


def _answer(group: twins.Group, choice: str, p: float = 0.9) -> DecisionResponse:
    options = list(group.request.questions["owner"].options)
    rest = (1 - p) / (len(options) - 1)
    return DecisionResponse({"owner": ChoiceAnswer(choice, {o: p if o == choice else rest for o in options})},
                            Usage(10, 10), "fakejev", None)


def test_groups_are_keys_holding_the_same_values_asked_with_the_tables_and_the_chance() -> None:
    ontology, claims = _world()
    groups, skipped = twins.questions(ontology, claims)
    assert skipped == []
    assert [g.name for g in groups] == ["s.photo.c1 = s.product.productid", "s.reason.reasonid = s.shift.shiftid"]
    real, lookups = groups
    assert list(real.request.questions["owner"].options) == ["s.photo", "s.product", twins.COINCIDENCE, classify.UNCLEAR]
    assert "same 500 values" in _facts(real) and "10^-" in _facts(real) and "is 1.00" in _facts(lookups)
    assert [s["table"] for s in cast(list[dict[str, object]], real.request.state["tables"])] == ["s.photo", "s.product"]


def _facts(group: twins.Group) -> str:
    instructions = group.request.questions["owner"].instructions
    assert not isinstance(instructions, str)
    return " ".join(cast(list[str], instructions["facts"]))


def test_the_owner_gets_one_to_one_references_and_the_others_stop_being_targets() -> None:
    ontology, claims = _world()
    [real, _] = twins.questions(ontology, claims)[0]
    [work] = [q for q in nameless_joins.questions(ontology, claims) if q.table.name == "s.work"]
    assert [t.option for a in work.asked for t in a.targets] == ["s.photo.c1", "s.product.productid"]

    decided = twins.read(real, _answer(real, "s.product"))
    assert isinstance(decided, twins.Decided)
    updated = twins.apply(ontology, [decided], "test-twins")
    assert check(updated) == []
    [photo] = [t for t in updated.tables if t.name == "s.photo"]
    [relation] = photo.relations or []
    assert relation.via is not None and (relation.via[0].from_, relation.via[0].to) == ("c1", "productid")
    assert relation.cardinality == "1:1" and relation.confidence == twins.CONFIDENCE_CAP
    # photo の製品 ID は product を指すので、ほかの列の参照先は product だけ。twins も、もう問わない
    [work] = [q for q in nameless_joins.questions(updated, claims) if q.table.name == "s.work"]
    assert [t.option for a in work.asked for t in a.targets] == ["s.product.productid"]
    groups, skipped = twins.questions(updated, claims)
    assert [g.name for g in groups] == ["s.reason.reasonid = s.shift.shiftid"] and skipped == []


@pytest.mark.parametrize(("choice", "p", "reason"), [(twins.COINCIDENCE, 0.9, "偶然"), (classify.UNCLEAR, 0.9, "決められない"),
                                                     ("s.product", 0.5, "閾値")])
def test_coincidence_unclear_and_low_answers_change_nothing(choice: str, p: float, reason: str) -> None:
    ontology, claims = _world()
    [real, _] = twins.questions(ontology, claims)[0]
    read = twins.read(real, _answer(real, choice, p))
    assert not isinstance(read, twins.Decided) and reason in read.reason


def test_groups_with_a_key_that_already_references_are_not_asked() -> None:
    """先に推定した関係（ここでは規則が名前で選んだ photo.productid → product）で、組の形はもう決まっている。"""
    catalog = Catalog(Scope(("s",)), (_table("product", "productid"), _table("photo", "productid", "c2"),
                                      _table("shift", "shiftid"), _table("reason", "reasonid")))
    claims = [*catalog_extractor.extract("src", catalog),
              *_both("product", "productid", "photo", "productid", 500, (999, 500)),
              *_both("shift", "shiftid", "reason", "reasonid", 5, (5, 5))]
    ontology = propose("src", ("s",), claims).ontology
    groups, skipped = twins.questions(ontology, claims)
    assert [g.name for g in groups] == ["s.reason.reasonid = s.shift.shiftid"]
    assert skipped == []  # photo のキーは product を指すので、もう参照先にも組にもならない


def test_sources_that_declare_foreign_keys_are_not_asked() -> None:
    """外部キーを 1 本でも宣言しているソースでは問わない（本物の双子は外部キーで結ばれ、残るのは偶然の双子だけ）。"""
    ontology, claims = _world()
    assert not twins.declared_references(claims)
    declared = new_claim(subject=_ref("work"), predicate=ClaimPredicate.references,
                         source=ClaimSource(kind=ClaimSourceKind.schema, locator="fk work product"), extracted_by="test",
                         confidence=1.0, columns=["c3"], target=_ref("product"), target_columns=["productid"],
                         on_delete="no_action", on_update="no_action")
    assert twins.declared_references([*claims, declared])
    with pytest.raises(ValueError, match="外部キーを宣言しているソース"):
        twins.questions(ontology, [*claims, declared])


@given(st.lists(st.sampled_from(["product", "photo", "shift", "reason", twins.COINCIDENCE, classify.UNCLEAR]),
                min_size=2, max_size=2), st.floats(0.0, 1.0))
def test_any_answers_give_a_valid_ontology_and_shared_keys_are_never_targets(choices: list[str], p: float) -> None:
    """どう答えても、当てた OM は検証を通り、足した関係はどれも同じ値の集合の 1 対 1 で、元でない側のキーは 3b の参照先に
    ならない。"""
    ontology, claims = _world()
    groups, _ = twins.questions(ontology, claims)
    decided: list[twins.Decided] = []
    for group, choice in zip(groups, choices, strict=True):
        owner = f"s.{choice}" if f"s.{choice}" in group.request.questions["owner"].options else choice
        if owner not in group.request.questions["owner"].options:
            continue
        if isinstance(read := twins.read(group, _answer(group, owner, p)), twins.Decided):
            decided.append(read)
    updated = twins.apply(ontology, decided, "test-twins")
    assert check(updated) == []
    added = [(t.name, r) for t in updated.tables for r in t.relations or [] if r.inferred_by == "test-twins"]
    assert len(added) == len(decided)
    by_id = {c.id: c for c in claims}
    assert all(nameless_joins.same_set(by_id[(r.evidence or [""])[0]]) and r.cardinality == "1:1" for _, r in added)
    targets = nameless_joins.target_keys(updated, claims)
    for name, r in added:
        assert r.via is not None and not targets.allows(name, r.via[0].from_)


# ---------------------------------------------------------------- refine の段（判断モデルを呼ぶところ）


@dataclass
class _Decider:
    script: dict[str, str | Exception]
    max_options: int = 255
    model: str = "fakejev"
    asked: list[str] = field(default_factory=lambda: list[str]())

    def max_input_tokens(self, request: DecisionRequest) -> int:
        return 1000

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        self.asked.append(request.purpose)
        reply = self.script[request.purpose]
        if isinstance(reply, Exception):
            raise reply
        options = list(request.questions["owner"].options)
        rest = 0.1 / (len(options) - 1)
        return DecisionResponse({"owner": ChoiceAnswer(reply, {o: 0.9 if o == reply else rest for o in options})},
                                Usage(10, 10), self.model, None)


REAL = "twins s.photo.c1 = s.product.productid"
LOOKUPS = "twins s.reason.reasonid = s.shift.shiftid"


def test_refine_applies_owners_records_failures_and_stops_on_the_budget() -> None:
    ontology, claims = _world()
    decider = _Decider({REAL: "s.product", LOOKUPS: LlmError("HTTP 529")})
    decided, outcomes, stopped = pipeline._decide_twins(ontology, claims, decider, "t")  # pyright: ignore[reportPrivateUsage]
    assert stopped is None and decider.asked == [REAL, LOOKUPS]
    assert [(o.status, o.owner, o.added) for o in outcomes] == [
        ("answered", "s.product", ("s.photo.c1 → s.product.productid",)), ("failed", None, ())]
    assert check(decided) == [] and outcomes[0].answer is not None

    budget = BudgetExceededError(REAL, 0.0, 1.0, 0.5)
    decider = _Decider({REAL: budget, LOOKUPS: twins.COINCIDENCE})
    decided, outcomes, stopped = pipeline._decide_twins(ontology, claims, decider, "t")  # pyright: ignore[reportPrivateUsage]
    assert stopped is not None and decider.asked == [REAL] and decided == ontology
    assert [o.status for o in outcomes] == ["not_reached", "not_reached"]

    with pytest.raises(ValueError, match="判断モデルの上限"):
        pipeline._decide_twins(ontology, claims, _Decider({}, max_options=twins.MAX_MEMBERS + 1), "t")  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------- 組の作り方の法則


@st.composite
def _classes(draw: st.DrawFn) -> tuple[list[int], dict[tuple[int, int], str]]:
    """表 t0..tn の値の集合の類（同じ番号は同じ集合）と、同じ類の対ごとの測定（"both" / "one" / "none"）。"""
    n = draw(st.integers(2, 8))
    cls = draw(st.lists(st.integers(0, 2), min_size=n, max_size=n))
    measured = {(i, j): draw(st.sampled_from(["both", "both", "one", "none"]))
                for i in range(n) for j in range(i + 1, n) if cls[i] == cls[j]}
    return cls, measured


def _class_world(cls: list[int], measured: dict[tuple[int, int], str]) -> tuple[Ontology, list[Claim]]:
    tables = tuple(_table(f"t{i}", f"c{i}") for i in range(len(cls)))
    claims = list(catalog_extractor.extract("src", Catalog(Scope(("s",)), tables)))
    for (i, j), how in measured.items():
        n = 100 + cls[i]
        if how in ("both", "one"):
            claims.append(_included(f"t{i}", f"c{i}", f"t{j}", f"c{j}", n, n, n, (999, n)))
        if how == "both":
            claims.append(_included(f"t{j}", f"c{j}", f"t{i}", f"c{i}", n, n, n, (999, n)))
    assert check_claims(claims) == []
    return propose("src", ("s",), claims).ontology, claims


def _components(n: int, measured: dict[tuple[int, int], str]) -> set[frozenset[str]]:
    """両向きに測った対のつながり（2 つ以上の表のもの）。"""
    seen: set[int] = set()
    out: set[frozenset[str]] = set()
    for start in range(n):
        if start in seen:
            continue
        stack, part = [start], set[int]()
        while stack:
            i = stack.pop()
            if i in part:
                continue
            part.add(i)
            stack += [j for (a, b), how in measured.items() if how == "both" for i2, j in ((a, b), (b, a)) if i2 == i]
        seen |= part
        if len(part) > 1:
            out.add(frozenset(f"s.t{i}" for i in part))
    return out


@given(_classes(), st.data())
def test_groups_are_the_components_of_keys_measured_both_ways_and_any_owner_links_or_records_every_member(
        world: tuple[list[int], dict[tuple[int, int], str]], data: st.DataObject) -> None:
    """組は、両向きに測った対のつながりと一致する（上限を超える組は問わずに記録）。どの表を元と答えても、当てた OM は
    検証を通り、元でない表はどれも、関係を足したか、直接の測定が無いと記録したかのどちらか。足した側は 3b の参照先にならず、
    もう一度問う組は記録した側だけでできている。"""
    cls, measured = world
    ontology, claims = _class_world(cls, measured)
    groups, skipped = twins.questions(ontology, claims)
    asked = {frozenset(t for t, _ in g.members) for g in groups}
    too_large = {frozenset(part.split(".")[0] + "." + part.split(".")[1] for part in d.item.split(" = ")) for d in skipped}
    assert asked | too_large == _components(len(cls), measured)
    assert all(len(p) > twins.MAX_MEMBERS for p in too_large) and all(len(p) <= twins.MAX_MEMBERS for p in asked)
    decided = [twins.Decided(g, data.draw(st.sampled_from([t for t, _ in g.members])), 0.9) for g in groups]
    updated = twins.apply(ontology, decided, "test-twins")
    assert check(updated) == []
    targets = nameless_joins.target_keys(updated, claims)
    for d in decided:
        links = twins.links(d)
        assert {a for a, _, _ in links.added} | set(links.missing) == {k for k in d.group.members if k[0] != d.owner}
        assert all(not targets.allows(*a) for a, _, _ in links.added)
    # もう一度問う組は、元へ直接の測定が無く関係を足さなかった表どうしだけ
    missing = {k for d in decided for k in twins.links(d).missing}
    assert all(set(g.members) <= missing for g in twins.questions(updated, claims)[0])


def test_a_member_without_a_direct_measurement_to_the_owner_is_recorded_not_linked() -> None:
    """t0 = t1、t1 = t2 だけを測った。t0 を元にすると、t2 には根拠の測定が無いので関係を足さず、記録に出す。"""
    ontology, claims = _class_world([0, 0, 0], {(0, 1): "both", (1, 2): "both", (0, 2): "none"})
    [group] = twins.questions(ontology, claims)[0]
    decider = _Decider({f"twins {group.name}": "s.t0"})
    decided, [outcome], _ = pipeline._decide_twins(ontology, claims, decider, "t")  # pyright: ignore[reportPrivateUsage]
    assert outcome.added == ("s.t1.c1 → s.t0.c0",)
    assert [(d.item, "直接の包含の測定が無い" in d.reason) for d in outcome.dropped] == [("s.t2.c2", True)]
    assert check(decided) == []


def test_the_chance_of_integer_keys_needs_the_range_and_other_keys_have_no_chance() -> None:
    ontology, claims = _class_world([0, 0], {(0, 1): "both"})
    stale = [c.model_copy(update={"value_span": None, "target_values_in_span": None})
             if c.predicate == ClaimPredicate.included_in.value else c for c in claims]
    with pytest.raises(ValueError, match="範囲を測る前の台帳"):
        twins.questions(ontology, stale)
    text = [c.model_copy(update={"family": TypeFamily.text.value}) if c.predicate == ClaimPredicate.typed_as.value else c
            for c in stale]
    [group] = twins.questions(ontology, text)[0]
    assert "not known" in _facts(group)
