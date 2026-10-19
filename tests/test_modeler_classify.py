"""分類と生成に分けた問い（T3.7）の法則。本物の API は呼ばない。

- 問い：意味型の選択肢は列の型族で当ててよいものだけ、選択肢は上限以下。根拠は表の主張だけ
- 読み：どんな確率の答えでも、閾値以上の答えだけが提案になり、残りの問いはすべて理由つきで記録される（黙って消えない）
- 命名：問うた列は、ちょうど1つの意味型に当たるか、理由つきで落ちるかのどちらか
- 組み立てた答えは、従来の検証（apply_answer）を通しても OM を壊さない
"""

from collections.abc import Collection, Mapping, Sequence
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
from mandara.modeler.joins import InferenceClaims
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


def _classify(table: Table, table_claims: InferenceClaims, vocabulary: Mapping[str, SemanticType],
              hints: Mapping[str, v1.MeaningHint], shared: Collection[str], max_options: int, purpose: str,
              elsewhere_only: Collection[str] = ()) -> c.Classify:
    return c.classify(table, table_claims, vocabulary, hints, shared, max_options, purpose, elsewhere_only=elsewhere_only)


def _claims(name: str) -> list[Claim]:
    return [x for x in CLAIMS if qualify(x.subject.schema_name, x.subject.table) == name]


def _for(claims: Sequence[Claim]) -> InferenceClaims:
    """推論の材料にする表の主張。この台帳はカタログの主張だけ（値の包含の測定が無い）なので、絞らずにそのまま使える。"""
    assert not any(x.predicate == "included_in" for x in claims)
    return InferenceClaims(tuple(claims))


def _table(name: str) -> Table:
    return next(t for t in SEEDED.tables if t.name == name)


def _asked(name: str) -> c.Classify:
    table = _table(name)
    return _classify(table, _for(_claims(name)), VOCABULARY, v1.meaning_hints(SEEDED, table, GROUPS),
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
    routed = {d.item.removeprefix("classify ") for d in classified.routed}
    picked = {**({"kind": classified.kind} if classified.kind else {}),
              **{f"relation {k}": v for k, v in classified.relations.items()},
              **{f"column {k}": v for k, v in classified.columns.items()
                 if k not in asked.new_only and f"column {k}" not in routed}}
    edge = {"edge"} if classified.edge is not None else set[str]()
    recorded = {d.item for d in classified.abstained}
    assert set(picked) | edge | recorded | routed == set(response.answers) | {d.item for d in asked.not_asked}
    assert not (set(picked) | edge) & (recorded | routed) and not recorded & routed
    # 既存の意味型が閾値に届かない列は、落とさずに新しい意味型として命名に回る（理由つき）
    assert all(classified.columns[k.removeprefix("column ")].value == c.NEW for k in routed)
    assert all(d.reason.endswith("新しい意味型として命名に回した") for d in classified.routed)
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
    column = next(k for k in asked.request.questions if k.startswith("column "))
    options = list(asked.request.questions[column].options)
    answers[column] = ChoiceAnswer(options[0], {o: 0.5 if o == options[0] else 0.5 / (len(options) - 1) for o in options})
    classified = c.read_classified(asked, DecisionResponse(answers, Usage(1, 0), "m", None))
    assert classified.kind is None
    assert any(d.item == "kind" and "決められない" in d.reason for d in classified.abstained)
    # 列：既存の意味型が閾値未満なら、提案はせず、新しい意味型として命名に回す
    assert classified.columns[column.removeprefix("column ")].value == c.NEW
    assert [d.item for d in classified.routed] == [f"classify {column}"]


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
    asked = c.properties(_table(name), _for(claims), c.Named((with_values, without), ()), "properties")
    assert asked.candidates["gender_code"] == ("enum",)
    assert "enum" not in asked.candidates["job_title"]


def test_unit_column_evidence_is_included() -> None:
    name = "sales.salesorderdetail"
    asked = _asked(name)
    classified = c.Classified(columns={"unitprice": c.Picked("unit_price", 0.9)})
    units = c.units(_for(_claims(name)), ["unitprice"], ["unitprice"], ["carriertrackingnumber"], "units")
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
    text = c.naming_prompt(_for(_claims(name)), VOCABULARY, None, {}, ["unitprice"])
    assert "product_id" in text and "製品" not in text


def test_named_types_are_capped_below_measured_confidence() -> None:
    name = "humanresources.employee"
    t = c.NamedType(name="job_title", description="職名", columns=["jobtitle"], values_evidence=[])
    named = c.Named((t,), ())
    asked = c.properties(_table(name), _for(_claims(name)), named, "properties")
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
    asked = _classify(plain, _for(_claims(name)), vocabulary, hints, (), MAX_OPTIONS, "classify")
    assert asked.request is not None
    assert {"location", "photo"} <= set(asked.request.questions["column spatiallocation"].options)
    columns = dict(plain.columns or {})
    columns["spatiallocation"] = columns["spatiallocation"].model_copy(update={"encoding": "sqlserver_geography"})
    encoded = plain.model_copy(update={"columns": columns})
    asked = _classify(encoded, _for(_claims(name)), vocabulary, hints, (), MAX_OPTIONS, "classify")
    assert asked.request is not None
    assert set(asked.request.questions["column spatiallocation"].options) - {c.NEW, c.UNCLEAR} == {"location"}
    named = c.Named((c.NamedType(name="spot", description="位置", columns=["spatiallocation"], values_evidence=[]),), ())
    assert c.properties(encoded, _for(_claims(name)), named, "properties").candidates["spot"] == ("geo.point",)
    assert "media.image" in c.properties(plain, _for(_claims(name)), named, "properties").candidates["spot"]


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
    asked = _classify(table, _for(claims), VOCABULARY, v1.meaning_hints(SEEDED, table, GROUPS),
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
    asked = _classify(_table(name), _for(undeclared), VOCABULARY, {}, (), MAX_OPTIONS, "classify")
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
        asked = _classify(_table(name).model_copy(update={"kind": None}), _for(_claims(name)), VOCABULARY, {}, (),
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
    asked = c.units(_for([*_claims(name), *declared]), ["unitprice", "orderqty"], ["unitprice"], [], "units")
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
    plain = _classify(table, _for(_claims(name)), vocabulary, hints, (), MAX_OPTIONS, "classify")
    guarded = _classify(table, _for(_claims(name)), vocabulary, hints, (), MAX_OPTIONS, "classify",
                         elsewhere_only={"hire_date", "birth_date"})
    assert plain.request is not None and guarded.request is not None
    assert "hire_date" in plain.request.questions["column startdate"].options
    options = guarded.request.questions["column startdate"].options
    assert "hire_date" not in options and "birth_date" in options  # 役割の無い意味型はそのまま出す


def test_decided_facts_go_to_the_llm_only_when_decided() -> None:
    """判断モデルが閾値を超えて決めたこと（既存の意味型、関係型）だけを、LLM の材料に事実として入れる。
    新しい意味型（new）や決められなかったものは入れない。"""
    name = "sales.salesorderdetail"
    claims = _claims(name)
    fk = next(x for x in claims if x.predicate == "references" and x.columns == ["salesorderid"])
    classified = c.Classified(columns={"productid": c.Picked("product_id", 0.9), "unitprice": c.Picked(c.NEW, 1.0)},
                              relations={fk.id: c.Picked("part-of", 0.88)})
    lines = c.decided_lines(classified, _for(claims), VOCABULARY)
    assert lines == ['column "productid": semantic type product_id (identifier), probability 0.90',
                     "foreign key (salesorderid) -> sales.salesorderheader: relation part-of, probability 0.88"]
    prompt = c.naming_prompt(_for(claims), VOCABULARY, "event", {}, ["unitprice"], lines)
    body = prompt.split("<data>")[1].split("</data>")[0]
    assert "decided by the classification" in body and "relation part-of" in body  # データの区画の中
    assert "decided by the classification" not in c.naming_prompt(_for(claims), VOCABULARY, "event", {}, ["unitprice"])
    final = {"unitprice": SemanticType(name="unit_price", type=ValueType.money, description="単価", requires_unit=True)}
    assert c.decided_lines(classified, _for(claims), VOCABULARY, final)[0] == 'column "unitprice": semantic type unit_price (money)'


def test_value_shapes_are_in_the_state_and_resemblance_is_not_in_the_questions() -> None:
    """値の形（shaped_as）は材料の列に書き、書き方の説明を 1 回だけ添える。値の似た列の事実は、分類の問いに入れない
    （T2.6。名前で決まる判断が引かれる。似かたは mandara.modeler.same_meaning の別の問い）。"""
    from mandara.modeler import resemblance as rm
    from mandara.om.claims import new_claim
    from mandara.om.model import ClaimPredicate, ClaimSource, ClaimSourceKind, SourceRef

    name = "sales.salesorderdetail"
    shaped = new_claim(
        subject=SourceRef(source="adventureworks", schema_name="sales", table="salesorderdetail", column="unitprice"),
        predicate=ClaimPredicate.shaped_as, source=ClaimSource(kind=ClaimSourceKind.data, locator="values"),
        extracted_by="t", confidence=1.0, patterns=["9{2}\\.9{4}", "9{3}\\.9{4}"], pattern_shares=[0.6, 0.4],
        shape_level=1, distinct_values=287, sampled_values=256)
    table = _table(name)
    asked = _classify(table, _for([*_claims(name), shaped]), VOCABULARY, v1.meaning_hints(SEEDED, table, GROUPS),
                      v1.shared_key_foreign_keys(table, _claims(name), SEEDED), MAX_OPTIONS, f"classify {name}")
    assert asked.request is not None
    columns = cast(list[dict[str, object]], asked.request.state["columns"])
    assert next(x for x in columns if x["name"] == "unitprice")["value_shape"] == (
        "9{2}\\.9{4} 60%, 9{3}\\.9{4} 40% (287 distinct values)")
    assert asked.request.state["value_shape_notation"] == rm.SHAPE_NOTATION
    assert shaped.id in asked.evidence["column unitprice"]  # 列の根拠に、値の形の主張も入る
    assert "facts" not in cast(dict[str, object], asked.request.questions["column unitprice"].instructions)
    others = _classify(table, _for(_claims(name)), VOCABULARY, v1.meaning_hints(SEEDED, table, GROUPS),
                       v1.shared_key_foreign_keys(table, _claims(name), SEEDED), MAX_OPTIONS, f"classify {name}")
    assert others.request is not None and "value_shape_notation" not in others.request.state


GOOD_TYPE: st.SearchStrategy[dict[str, object]] = st.fixed_dictionaries({
    "name": st.from_regex(r"[a-z][a-z_]{2,8}", fullmatch=True), "description": st.just("説明"),
    "columns": st.lists(st.sampled_from(["a", "b", "c", "d"]), min_size=1, max_size=2, unique=True),
    "values": st.none(), "values_evidence": st.just(cast(list[str], []))})
BROKEN_TYPE: st.SearchStrategy[object] = st.sampled_from([
    {"name": "x_flag", "description": "d", "columns": ["a"], "values": [{"name": "false", "label": "いいえ"}],
     "values_evidence": []},  # enum の値のキーの間違い（実際に起きた崩れ）
    {"name": "x_id", "description": "d", "columns": ["b"], "values": None, "values_evidence": [], "extra": 1},
    {"name": "Bad Name", "description": "d", "columns": ["c"], "values": None, "values_evidence": []},
    "not an object"])


@given(items=st.lists(st.tuples(st.booleans(), GOOD_TYPE, BROKEN_TYPE), max_size=6))
def test_naming_is_validated_per_semantic_type(items: list[tuple[bool, dict[str, object], object]]) -> None:
    """命名の答えは意味型ごとに検証する：形の正しい意味型は順に残り、崩れた意味型は位置と理由つきで落ちる（1 つの崩れで
    表の命名をまるごと捨てない）。"""
    data = {"semantic_types": [good if ok else bad for ok, good, bad in items]}
    parsed = c.parse_naming(data)
    assert [t.name for t in parsed.answer.semantic_types] == [good["name"] for ok, good, _ in items if ok]
    assert [d.item for d in parsed.broken] == [f"semantic_type #{i}" for i, (ok, _, _) in enumerate(items) if not ok]
    assert all("命名の答えの形が違う" in d.reason for d in parsed.broken)


@pytest.mark.parametrize("data", [None, [], {"semantic_types": "x"}, {"semantic_types": [], "other": 1}])
def test_a_naming_answer_without_the_list_of_semantic_types_is_an_error(data: object) -> None:
    with pytest.raises(v1.InvalidAnswerError, match="semantic_types の一覧"):
        c.parse_naming(data)


def test_a_retry_names_only_the_missing_columns_and_clears_their_reasons() -> None:
    first = c.read_naming(c.NamingAnswer(semantic_types=[
        c.NamedType(name="order_id", description="受注", columns=["a"], values_evidence=[])]), ["a", "b", "c"], {})
    assert c.uncovered(first, ["a", "b", "c"]) == ["b", "c"]
    retry = c.read_naming(c.NamingAnswer(semantic_types=[
        c.NamedType(name="ship_date", description="出荷日", columns=["b"], values_evidence=[]),
        c.NamedType(name="order_id", description="重なる名前", columns=["c"], values_evidence=[])]),
        ["b", "c"], {"order_id"})  # 最初の答えの名前は使えない
    merged = c.merge_named(first, retry, ["b", "c"])
    assert [t.name for t in merged.types] == ["order_id", "ship_date"]
    assert c.uncovered(merged, ["a", "b", "c"]) == ["c"]
    reasons = [(d.item, d.reason) for d in merged.dropped]
    assert ("column b", "命名の答えに無い") not in reasons  # 問い直しで名前が付いた
    assert ("column c", "命名の答えに無い") in reasons and any("既存の語彙の名前" in r for i, r in reasons if i == "column c")


@given(st.lists(st.tuples(st.sampled_from(["a_id", "b_id", "product_id", "order_date"]),
                          st.lists(st.sampled_from(["x", "y", "z", "w"]), max_size=3)), max_size=4),
       st.dictionaries(st.sampled_from(["x", "y", "z"]), st.sets(st.sampled_from(["product_id", "order_date"]))))
def test_an_existing_name_is_reused_only_where_it_is_allowed(types: list[tuple[str, list[str]]],
                                                               reusable: dict[str, set[str]]) -> None:
    """法則：問うた列は、新しい名前か、使ってよい既存の名前のどちらか 1 つを得るか、理由つきで落ちる。既存の名前を得るのは、
    その列に使ってよい（reusable）名前だけ。"""
    asked = ["x", "y", "z"]
    answer = c.NamingAnswer(semantic_types=[c.NamedType(name=n, description="説明", columns=cols, values_evidence=[])
                                            for n, cols in types])
    named = c.read_naming(answer, asked, VOCABULARY, reusable)
    assigned = [col for t in named.types for col in t.columns] + [col for col, _ in named.reused]
    assert len(assigned) == len(set(assigned)) and set(assigned) <= set(asked)
    assert all(name in reusable.get(col, set()) for col, name in named.reused)
    assert all(t.name not in VOCABULARY for t in named.types)
    dropped = {d.item for d in named.dropped}
    for col in asked:
        assert (col in assigned) != (f"column {col}" in dropped)
    assert c.uncovered(named, asked) == [col for col in asked if col not in assigned]


def test_pack_time_types_fit_only_the_precision_of_the_column() -> None:
    day = SemanticType(name="sell_start_date", type=ValueType.date, pack="p 1")
    moment = SemanticType(name="modified_date", type=ValueType.datetime, pack="p 1")
    mine = SemanticType(name="order_date", type=ValueType.date)
    assert not c.pack_fits(day, "timestamp") and c.pack_fits(moment, "timestamp")
    assert c.pack_fits(day, "date") and not c.pack_fits(moment, "date")
    assert c.pack_fits(mine, "timestamp")  # 推論の中で名付けた意味型は今のまま
    assert c.pack_fits(SemanticType(name="unit_price", type=ValueType.money, pack="p 1"), "decimal")


def test_offered_types_are_reusable_only_for_columns_the_decider_could_not_decide() -> None:
    name = "sales.salesorderdetail"
    asked = _asked(name)
    assert asked.request is not None
    column = next(k.removeprefix("column ") for k, q in asked.request.questions.items()
                  if k.startswith("column ") and len(q.options) > 2)
    offered = set(asked.request.questions[f"column {column}"].options) - {c.NEW, c.UNCLEAR}
    firm = c.reusable_types(_table(name), _for(_claims(name)), VOCABULARY, asked, [column])
    unsure = c.reusable_types(_table(name), _for(_claims(name)), VOCABULARY, asked, [column], routed=[column])
    assert not firm[column] & offered and offered <= unsure[column]
    classified = c.Classified(routed=[v1.Dropped(f"classify column {column}", "迷った"), v1.Dropped("classify kind", "x")])
    assert c.routed_columns(classified) == {column}


@pytest.mark.parametrize("name", NAMES)
def test_reuse_never_brings_back_what_the_decider_was_offered_or_excluded(name: str) -> None:
    """法則：迷っていない列で命名が使ってよい既存の意味型は、分類の選択肢と交わらず、選択肢から外した意味型（時刻の役割を
    持ち込むもの）も含まない。"""
    asked = _asked(name)
    if asked.request is None:
        return
    columns = [k.removeprefix("column ") for k in asked.request.questions if k.startswith("column ")]
    stamped = SemanticType(name="hire_date", type=ValueType.datetime, role="event_time")  # pyright: ignore[reportArgumentType]
    vocabulary = {**VOCABULARY, "hire_date": stamped}
    reusable = c.reusable_types(_table(name), _for(_claims(name)), vocabulary, asked, columns,
                                elsewhere_only=["hire_date"])
    for column in columns:
        offered = set(asked.request.questions[f"column {column}"].options)
        assert not reusable[column] & offered and "hire_date" not in reusable[column]


def _named_answer(name: str, column: str) -> tuple[c.Named, list[v1.SemanticTypeAnswer]]:
    named = c.Named((c.NamedType(name=name, description="説明", columns=[column], values_evidence=[]),), ())
    answer = v1.SemanticTypeAnswer(name=name, type=ValueType.identifier, description="説明", confidence=0.7, evidence=[])
    return named, [answer]


@given(st.sampled_from(["named", "rival", "unclear"]), st.floats(min_value=0.0, max_value=1.0))
def test_confirmation_measures_the_named_type_against_the_offered_ones(choice: str, probability: float) -> None:
    """法則：確かめの選択肢は、名付けた意味型と、分類でその列に出した既存の意味型と「決められない」。名付けた意味型を閾値以上で
    選べばその確率（上限 MAX_CONFIDENCE）、既存の意味型を閾値以上で選べば列を落とし、それ以外は何もしない。"""
    name = "sales.salesorderdetail"
    asked = _asked(name)
    assert asked.request is not None
    column = next(k.removeprefix("column ") for k, q in asked.request.questions.items()
                  if k.startswith("column ") and len(q.options) > 2)
    named, types = _named_answer("brand_new_id", column)
    checked = c.confirm(_for(_claims(name)), asked, types, named, VOCABULARY, f"confirm {name}")
    assert checked.request is not None
    question = checked.request.questions[f"confirm {column}"]
    offered = set(asked.request.questions[f"column {column}"].options) - {c.NEW, c.UNCLEAR}
    assert set(question.options) == {"brand_new_id", c.UNCLEAR} | offered
    rival = sorted(offered)[0]
    picked = {"named": "brand_new_id", "rival": rival, "unclear": c.UNCLEAR}[choice]
    rest = (1 - probability) / (len(question.options) - 1)
    answer = ChoiceAnswer(picked, {o: probability if o == picked else rest for o in question.options})
    confirmed, dropped = c.read_confirm(checked, DecisionResponse({f"confirm {column}": answer}, Usage(1, 0), "m", None))
    sure = probability >= c.THRESHOLD and choice != "unclear"
    assert confirmed == ({column: min(probability, v1.MAX_CONFIDENCE)} if sure and choice == "named" else {})
    assert [d.item for d in dropped] == ([f"column {column}"] if sure and choice == "rival" else [])


@pytest.mark.parametrize("name", NAMES)
def test_neighbors_list_the_tables_that_reference_the_table(name: str) -> None:
    """表の周り：この表を参照している表は、OM の関係の参照先がこの表のもの（自分自身を除く）で、数は全部、並びは上限まで。"""
    table = _table(name)
    around = c.neighbors(SEEDED, table, _for(_claims(name)))
    expected = sorted({t.name for t in SEEDED.tables for r in t.relations or []
                       if r.target == table.id and t.id != table.id and r.via})
    shown = cast(list[str], around.get("referenced_by", []))
    assert cast(int, around["referenced_by_count"]) >= len(expected)
    assert {s.split(" (")[0] for s in shown} <= set(expected) and len(shown) <= c.NEIGHBORS_SHOWN
    assert "rows" not in around  # 宣言の主キーの表では行の数を測っていない
