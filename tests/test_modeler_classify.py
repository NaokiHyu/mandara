"""分類と生成に分けた問い（T3.7）の法則。本物の API は呼ばない。

- 問い：意味型の選択肢は列の型族で当ててよいものだけ、選択肢は上限以下。根拠は表の主張だけ
- 読み：どんな確率の答えでも、閾値以上の答えだけが提案になり、残りの問いはすべて理由つきで記録される（黙って消えない）
- 命名：問うた列は、ちょうど1つの意味型に当たるか、理由つきで落ちるかのどちらか
- 組み立てた答えは、従来の検証（apply_answer）を通しても OM を壊さない
"""

from collections.abc import Sequence
from dataclasses import replace
from typing import cast

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
    return c.classify(table, _claims(name), VOCABULARY, v1.meaning_hints(SEEDED, table, GROUPS),
                      v1.shared_key_foreign_keys(table, _claims(name), SEEDED), MAX_OPTIONS,
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
    asked = c.properties(_table(name), claims, c.Named((with_values, without), ()), "properties")
    assert asked.candidates["gender_code"] == ("enum",)
    assert "enum" not in asked.candidates["job_title"]


def test_unit_column_evidence_is_included() -> None:
    name = "sales.salesorderdetail"
    asked = _asked(name)
    classified = c.Classified(columns={"unitprice": c.Picked("unit_price", 0.9)})
    units = c.units(_claims(name), ["unitprice"], ["unitprice"], ["carriertrackingnumber"], "units")
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
    asked = c.properties(_table(name), _claims(name), named, "properties")
    assert asked.request is not None
    answers = {k: ChoiceAnswer(list(q.options)[0], {o: 1.0 if i == 0 else 0.0 for i, o in enumerate(q.options)})
               for k, q in asked.request.questions.items()}
    types, _ = c.read_properties(_claims(name), named, asked, DecisionResponse(answers, Usage(1, 0), "m", None))
    assert types and all(x.confidence <= c.NAMED_CONFIDENCE for x in types)


def test_encoded_columns_are_offered_only_the_types_the_encoding_decodes_to() -> None:
    """OM の spatiallocation（バイト列）に geography の encoding があれば、画像の意味型は問わない・当てない（ENCODING_MISMATCH）。"""
    vocabulary = {**VOCABULARY,
                  "location": SemanticType(name="location", type=ValueType("geo.point"), description="位置"),
                  "photo": SemanticType(name="photo", type=ValueType("media.image"), description="写真")}
    name = "person.address"
    plain = _table(name)
    hints = v1.meaning_hints(SEEDED, plain, GROUPS)
    asked = c.classify(plain, _claims(name), vocabulary, hints, (), MAX_OPTIONS, "classify")
    assert asked.request is not None
    assert {"location", "photo"} <= set(asked.request.questions["column spatiallocation"].options)
    columns = dict(plain.columns or {})
    columns["spatiallocation"] = columns["spatiallocation"].model_copy(update={"encoding": "sqlserver_geography"})
    encoded = plain.model_copy(update={"columns": columns})
    asked = c.classify(encoded, _claims(name), vocabulary, hints, (), MAX_OPTIONS, "classify")
    assert asked.request is not None
    assert set(asked.request.questions["column spatiallocation"].options) - {c.NEW, c.UNCLEAR} == {"location"}
    named = c.Named((c.NamedType(name="spot", description="位置", columns=["spatiallocation"], values_evidence=[]),), ())
    assert c.properties(encoded, _claims(name), named, "properties").candidates["spot"] == ("geo.point",)
    assert "media.image" in c.properties(plain, _claims(name), named, "properties").candidates["spot"]


def test_an_encoding_that_contradicts_the_column_family_is_an_error() -> None:
    from mandara.om.value_types import IncompatibleEncodingError, assignable

    assert assignable("binary", "sqlserver_geography") == {"geo.point"}
    with pytest.raises(IncompatibleEncodingError, match="型族 integer"):
        assignable("integer", "sqlserver_geography")


# ---------------------------------------------------------------- 関係型の手がかり（構造とデータの事実）


def _relation_question(name: str, target: str, extra: Sequence[Claim] = ()) -> tuple[dict[str, object], set[str]]:
    table = _table(name)
    claims = [*_claims(name), *extra]
    fk = next(x for x in claims if x.predicate == "references" and x.target is not None
              and qualify(x.target.schema_name, x.target.table) == target)
    asked = c.classify(table, claims, VOCABULARY, v1.meaning_hints(SEEDED, table, GROUPS),
                       v1.shared_key_foreign_keys(table, claims, SEEDED), MAX_OPTIONS, "classify")
    assert asked.request is not None
    question = asked.request.questions[f"relation {fk.id}"]
    assert isinstance(question.instructions, dict)
    assert {fk.id} <= set(asked.evidence[f"relation {fk.id}"]) <= {x.id for x in claims}
    return question.instructions, set(question.options)


def _facts(instructions: dict[str, object]) -> str:
    facts = instructions["facts"]
    assert isinstance(facts, list)
    return " ".join(str(f) for f in cast(list[object], facts))


def test_is_a_is_offered_only_for_foreign_keys_that_share_the_primary_key() -> None:
    shared, options = _relation_question("humanresources.employee", "person.person")
    assert "is-a" in options and "shares its identity" in _facts(shared)
    detail, options = _relation_question("sales.salesorderdetail", "sales.salesorderheader")
    assert "is-a" not in options and {"has", "part-of", "classified-as", c.UNCLEAR} <= options
    facts = _facts(detail)
    assert "part of the primary key" in facts and "ON DELETE CASCADE" in facts and "NOT NULL" in facts
    header, _ = _relation_question("sales.salesorderheader", "sales.salesperson")
    assert "may be NULL" in _facts(header) and "CASCADE" not in _facts(header)

    # NULL 可否を宣言していない列（not_null も nullable も無い）には、NULL について何も言わない
    name = "sales.salesorderheader"
    undeclared = [x for x in _claims(name)
                  if not (x.predicate in ("not_null", "nullable") and x.subject.column == "salespersonid")]
    fk = next(x for x in undeclared if x.predicate == "references" and x.columns == ["salespersonid"])
    asked = c.classify(_table(name), undeclared, VOCABULARY, {}, (), MAX_OPTIONS, "classify")
    assert asked.request is not None
    instructions = asked.request.questions[f"relation {fk.id}"].instructions
    assert isinstance(instructions, dict) and "NULL" not in _facts(instructions)


def test_coverage_in_the_data_is_told_to_the_decision_model() -> None:
    """employee は persontype が EM / SP の人にだけあり、password はすべての人にある（AdventureWorks の実データ）。"""
    from mandara.connectors.postgres.coverage_reader import PostgresCoverageReader
    from mandara.extractors import values

    catalog = PostgresCatalogReader("dbname=adventureworks").read(SCOPE)
    reader = PostgresCoverageReader("dbname=adventureworks")
    found: dict[str, list[Claim]] = {}
    for shared in values.shared_keys(catalog):
        fk = shared.foreign_key
        coverage = reader.coverage(shared.table.schema, shared.table.name, fk.columns, shared.target.schema,
                                   shared.target.name, fk.target_columns, shared.split_by, values.MAX_DISCRIMINATOR_VALUES)
        found[shared.table.qualified_name] = values.coverage_claims("adventureworks", shared, coverage)
    employee = found["humanresources.employee"]
    assert [(x.predicate, x.discriminator, x.discriminator_values) for x in employee] == [
        ("discriminated_by", "persontype", ["EM", "SP"])]
    assert [x.predicate for x in found["person.password"]] == ["covers_all"]
    instructions, _ = _relation_question("humanresources.employee", "person.person", employee)
    assert "`persontype`" in _facts(instructions) and "EM" not in _facts(instructions)  # 値は文に埋め込まない
    assert instructions["discriminators"] == {"persontype": ["EM", "SP"]}
    instructions, options = _relation_question("person.password", "person.person", found["person.password"])
    assert "every target row" in _facts(instructions)
    assert "is-a" not in options  # すべての人にあるのは下位の型ではなく拡張
    assert "is-a" in _relation_question("person.password", "person.person")[1]  # 被覆を知らなければ出す


def test_kind_questions_tell_what_the_primary_key_is_made_of() -> None:
    def facts(name: str) -> str:
        asked = c.classify(_table(name).model_copy(update={"kind": None}), _claims(name), VOCABULARY, {}, (),
                           MAX_OPTIONS, "classify")
        assert asked.request is not None
        instructions = asked.request.questions["kind"].instructions
        assert isinstance(instructions, dict)
        return _facts(instructions)

    history = facts("humanresources.employeedepartmenthistory")
    assert "foreign keys" in history and "humanresources.department" in history and "the columns startdate" in history
    assert "the foreign key (salesorderid) -> sales.salesorderheader and the columns salesorderdetailid" in facts(
        "sales.salesorderdetail")
    assert facts("production.product") == "No foreign key is a part of the primary key."


def test_declared_currency_is_told_for_money_columns_only() -> None:
    """ソースの宣言（既定の通貨）は、金額の列の単位の問いにだけ事実として添い、根拠に宣言の主張が入る。"""
    from mandara.extractors import declarations
    from mandara.om.model import SourceDeclarations, Unit

    name = "sales.salesorderdetail"
    catalog = PostgresCatalogReader("dbname=adventureworks").read(SCOPE)
    declared = [x for x in declarations.extract("adventureworks", catalog, SourceDeclarations(default_currency=Unit.USD))
                if qualify(x.subject.schema_name, x.subject.table) == name]
    assert len(declared) == 1 and declared[0].source.kind == "declaration"
    asked = c.units([*_claims(name), *declared], ["unitprice", "orderqty"], ["unitprice"], [], "units")
    assert asked.request is not None
    money, quantity = asked.request.questions["unit unitprice"], asked.request.questions["unit orderqty"]
    assert isinstance(money.instructions, dict) and isinstance(quantity.instructions, dict)
    assert "USD" in _facts(money.instructions) and "facts" not in quantity.instructions
    assert declared[0].id in asked.evidence["unit unitprice"] and declared[0].id not in asked.evidence["unit orderqty"]
    assert declarations.extract("adventureworks", catalog, SourceDeclarations()) == []  # 宣言が無ければ主張も無い


def test_time_role_types_of_other_tables_are_not_offered() -> None:
    """別の表でしか使われていない、時刻の役割の意味型（入社日の event_time など）は選択肢に出さない。役割を持ち込まないため。"""
    from mandara.om.model import SemanticRole

    vocabulary = {**VOCABULARY,
                  "hire_date": SemanticType(name="hire_date", type=ValueType.date, description="入社日",
                                            role=SemanticRole.event_time),
                  "birth_date": SemanticType(name="birth_date", type=ValueType.date, description="誕生日")}
    name = "humanresources.employeedepartmenthistory"
    table = _table(name)
    hints = v1.meaning_hints(SEEDED, table, GROUPS)
    plain = c.classify(table, _claims(name), vocabulary, hints, (), MAX_OPTIONS, "classify")
    guarded = c.classify(table, _claims(name), vocabulary, hints, (), MAX_OPTIONS, "classify",
                         elsewhere_only={"hire_date", "birth_date"})
    assert plain.request is not None and guarded.request is not None
    assert "hire_date" in plain.request.questions["column startdate"].options
    options = guarded.request.questions["column startdate"].options
    assert "hire_date" not in options and "birth_date" in options  # 役割の無い意味型はそのまま出す
