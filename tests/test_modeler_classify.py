"""分類と生成に分けた問い（T3.7）の法則。本物の API は呼ばない。

- 問い：意味型の選択肢は列の型族で当ててよいものだけ、選択肢は上限以下。根拠は表の主張だけ
- 読み：どんな確率の答えでも、閾値以上の答えだけが提案になり、残りの問いはすべて理由つきで記録される（黙って消えない）
- 命名：問うた列は、ちょうど1つの意味型に当たるか、理由つきで落ちるかのどちらか
- 組み立てた答えは、従来の検証（apply_answer）を通しても OM を壊さない
"""

from dataclasses import replace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mandara.connectors.postgres.catalog_reader import PostgresCatalogReader
from mandara.extractors.catalog import extract
from mandara.llm.decision import ChoiceAnswer, DecisionResponse
from mandara.llm.model import Usage
from mandara.modeler import classify as c
from mandara.modeler import llm as v1
from mandara.modeler.meaning import same_meaning_groups
from mandara.modeler.rules import propose
from mandara.om.invariants import check
from mandara.om.model import Additivity, Claim, SemanticType, Table, ValueType
from mandara.om.names import qualify
from mandara.om.value_types import COMPATIBLE
from mandara.source.catalog import Scope

SCOPE = Scope(("humanresources", "person", "production", "purchasing", "sales"))
CLAIMS = extract("adventureworks", PostgresCatalogReader("dbname=adventureworks").read(SCOPE))
V0 = propose("adventureworks", SCOPE.schemas, CLAIMS).ontology
GROUPS = same_meaning_groups(CLAIMS)
VOCABULARY = {
    "product_id": SemanticType(name="product_id", type=ValueType.identifier, description="製品"),
    "order_date": SemanticType(name="order_date", type=ValueType.datetime, description="受注日"),
    "unit_price": SemanticType(name="unit_price", type=ValueType.money, description="単価", requires_unit=True),
    "name_text": SemanticType(name="name_text", type=ValueType.text, description="名前"),
}
MAX_OPTIONS = 255
SEEDED = replace(V0, vocabulary=V0.vocabulary.model_copy(update={"semantic_types": VOCABULARY}))
NAMES = ["sales.salesorderdetail", "sales.specialofferproduct", "production.product", "person.address",
         "production.billofmaterials"]


def _claims(name: str) -> list[Claim]:
    return [x for x in CLAIMS if qualify(x.subject.schema_name, x.subject.table) == name]


def _table(name: str) -> Table:
    return next(t for t in SEEDED.tables if t.name == name)


def _asked(name: str) -> c.Classify:
    table = _table(name)
    return c.classify(table, _claims(name), VOCABULARY, v1.meaning_hints(SEEDED, table, GROUPS), MAX_OPTIONS,
                      f"classify {name}")


@pytest.mark.parametrize("name", NAMES)
def test_questions_offer_only_compatible_types_within_the_limit(name: str) -> None:
    asked = _asked(name)
    assert asked.request is not None
    families = {x.subject.column: x.family for x in _claims(name) if x.predicate == "typed_as"}
    ids = {x.id for x in _claims(name)}
    for key, question in asked.request.questions.items():
        assert 2 <= len(question.options) <= MAX_OPTIONS and c.UNCLEAR in question.options
        assert set(asked.evidence[key]) <= ids
        if key.startswith("column "):
            family = families[key.removeprefix("column ")]
            assert all(VOCABULARY[o].type in COMPATIBLE[str(family)] for o in question.options
                       if o not in (c.NEW, c.UNCLEAR))
    for name_ in asked.new_only:
        assert not any(d.type in COMPATIBLE[str(families[name_])] for d in VOCABULARY.values())


def _answers(asked: c.Classify, data: st.DataObject) -> DecisionResponse:
    assert asked.request is not None
    answers: dict[str, ChoiceAnswer] = {}
    for key, question in asked.request.questions.items():
        options = list(question.options)
        weights = data.draw(st.lists(st.integers(0, 20), min_size=len(options), max_size=len(options)).filter(any))
        probabilities = {o: w / sum(weights) for o, w in zip(options, weights, strict=True)}
        answers[key] = ChoiceAnswer(max(options, key=lambda o: probabilities[o]), probabilities)
    return DecisionResponse(answers, Usage(100, 0), "fakejev", None)


@pytest.mark.parametrize("name", NAMES)
@settings(max_examples=30, deadline=None)
@given(data=st.data())
def test_every_question_is_either_proposed_above_the_threshold_or_recorded(name: str, data: st.DataObject) -> None:
    asked = _asked(name)
    assert asked.request is not None
    response = _answers(asked, data)
    classified = c.read_classified(asked, response)
    picked = {**({"kind": classified.kind} if classified.kind else {}),
              **{f"relation {k}": v for k, v in classified.relations.items()},
              **{f"column {k}": v for k, v in classified.columns.items() if k not in asked.new_only}}
    edge = {"edge"} if classified.edge is not None else set[str]()
    recorded = {d.item for d in classified.abstained}
    assert set(picked) | edge | recorded == set(response.answers) | {d.item for d in asked.not_asked}
    assert not (set(picked) | edge) & recorded
    for key, p in picked.items():
        assert p.probability >= c.THRESHOLD and p.value != c.UNCLEAR
        assert response.answers[key].choice == p.value
    # 組み立てて当てても、OM は新しい違反を生まない（名前の要る列は名付けずに落とす）
    named = c.read_naming(c.NamingAnswer(semantic_types=[]), sorted(
        k for k, v in classified.columns.items() if v.value == c.NEW), VOCABULARY)
    answer, dropped = c.assemble(_table(name), _claims(name), asked, classified, [], named, {}, {}, None)
    if classified.edge is not None and answer.edge is None:  # 使わなかった辺は理由つきで返る
        assert [d.item for d in dropped] == ["edge"]
    applied = v1.apply_answer(SEEDED, _table(name).id, answer, _claims(name), GROUPS, "test")
    assert set(check(applied.ontology)) <= set(check(SEEDED))


@given(st.lists(st.tuples(st.sampled_from(["a_id", "b_id", "c_id", "product_id"]),
                          st.lists(st.sampled_from(["x", "y", "z", "w"]), max_size=3)), max_size=4))
def test_each_asked_column_is_named_once_or_dropped(types: list[tuple[str, list[str]]]) -> None:
    asked = ["x", "y", "z"]
    answer = c.NamingAnswer(semantic_types=[c.NamedType(name=n, description="説明", columns=cols, values_evidence=[])
                                            for n, cols in types])
    named = c.read_naming(answer, asked, VOCABULARY)
    assigned = [col for t in named.types for col in t.columns]
    assert len(assigned) == len(set(assigned)) and set(assigned) <= set(asked)
    assert all(t.name not in VOCABULARY for t in named.types)  # 既存の語彙の名前は使わない
    dropped = {d.item for d in named.dropped}
    for col in asked:
        assert (col in assigned) != (f"column {col}" in dropped)


def test_unclear_and_low_probability_are_not_proposed() -> None:
    asked = _asked("sales.salesorderdetail")
    assert asked.request is not None and "kind" in asked.request.questions
    answers = {k: ChoiceAnswer(list(q.options)[0], {o: 1.0 if i == 0 else 0.0 for i, o in enumerate(q.options)})
               for k, q in asked.request.questions.items()}
    answers["kind"] = ChoiceAnswer(c.UNCLEAR, {"domain": 0.0, "relation": 0.0, "event": 0.0, c.UNCLEAR: 1.0})
    classified = c.read_classified(asked, DecisionResponse(answers, Usage(1, 0), "m", None))
    assert classified.kind is None
    assert any(d.item == "kind" and "決められない" in d.reason for d in classified.abstained)


def test_needs_grain_matches_the_invariant() -> None:
    measure = SemanticType(name="amount", type=ValueType.money, additivity=Additivity.additive,
                           requires_unit=True)
    plain = SemanticType(name="label", type=ValueType.text)
    assert c.needs_grain("event", {})
    assert c.needs_grain("domain", {"a": measure}) and not c.needs_grain("domain", {"a": plain})
    assert not c.needs_grain("relation", {"a": measure}) and not c.needs_grain(None, {"a": measure})


def test_enum_is_a_candidate_only_when_values_are_named() -> None:
    name = "humanresources.employee"
    claims = _claims(name)
    with_values = c.NamedType(name="gender_code", description="性別", columns=["gender"],
                              values=[v1.EnumValueAnswer(code="M", label="男性")], values_evidence=[])
    without = c.NamedType(name="job_title", description="職名", columns=["jobtitle"], values_evidence=[])
    asked = c.properties(claims, c.Named((with_values, without), ()), "properties")
    assert asked.candidates["gender_code"] == ("enum",)
    assert "enum" not in asked.candidates["job_title"]


def test_unit_column_evidence_is_included() -> None:
    name = "sales.salesorderdetail"
    asked = _asked(name)
    classified = c.Classified(columns={"unitprice": c.Picked("unit_price", 0.9)})
    units = c.units(_claims(name), ["unitprice"], ["carriertrackingnumber"], "units")
    answer, _ = c.assemble(_table(name), _claims(name), asked, classified, [], None,
                           {"unitprice": (None, "carriertrackingnumber", 0.8)}, units.evidence, None)
    unit_column = {x.id for x in _claims(name) if x.subject.column == "carriertrackingnumber"}
    assert unit_column <= set(answer.columns[0].evidence)


def test_vocabulary_is_described_once_in_the_state() -> None:
    """選択肢には意味型の名前と値の型だけを並べ、説明の文は材料に1回だけ置く（列ごとに繰り返さない）。"""
    asked = _asked("sales.salesorderdetail")
    assert asked.request is not None
    described = asked.request.state["semantic_types"]
    assert isinstance(described, dict)
    for key, question in asked.request.questions.items():
        if key.startswith("column "):
            names = [o for o in question.options if o not in (c.NEW, c.UNCLEAR)]
            assert all(o in described and "value type" in (question.options[o] or "") for o in names)
            assert all((VOCABULARY[o].description or "-") not in (question.options[o] or "") for o in names)


def test_naming_prompt_lists_vocabulary_names_without_descriptions() -> None:
    name = "sales.salesorderdetail"
    text = c.naming_prompt(_claims(name), VOCABULARY, None, {}, ["unitprice"])
    assert "product_id" in text and "製品" not in text


def test_named_types_are_capped_below_measured_confidence() -> None:
    name = "humanresources.employee"
    t = c.NamedType(name="job_title", description="職名", columns=["jobtitle"], values_evidence=[])
    named = c.Named((t,), ())
    asked = c.properties(_claims(name), named, "properties")
    assert asked.request is not None
    answers = {k: ChoiceAnswer(list(q.options)[0], {o: 1.0 if i == 0 else 0.0 for i, o in enumerate(q.options)})
               for k, q in asked.request.questions.items()}
    types, _ = c.read_properties(_claims(name), named, asked, DecisionResponse(answers, Usage(1, 0), "m", None))
    assert types and all(x.confidence <= c.NAMED_CONFIDENCE for x in types)
