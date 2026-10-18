"""名前の手がかりの無い参照の推定（T2.2 の 3b）の法則。

- 偶然の確率：隙間の無い連番のキーでは 1（log10 で 0）、確率は 1 以下で、値が増えるほど下がる。小さい数では組合せの比と一致
- 問う列：関係の参照元でも、表の 1 列の主キーでもなく、包含が 100% の列だけ。選択肢は偶然の確率の低い順に上限まで
- 答えの読み：閾値以上で参照先を選んだ列だけが関係になり、確信度は上限を超えない。根拠は選んだ測定の主張
"""

import math

from hypothesis import event, given
from hypothesis import strategies as st

from mandara.extractors import catalog as catalog_extractor
from mandara.llm.decision import ChoiceAnswer, DecisionResponse
from mandara.llm.model import Usage
from mandara.modeler import classify, nameless_joins
from mandara.modeler.rules import propose
from mandara.om.claims import check_claims, new_claim
from mandara.om.invariants import check
from mandara.om.model import Claim, ClaimPredicate, ClaimSource, ClaimSourceKind, ObjectKind, SourceRef, TypeFamily
from mandara.om.ontology import Ontology
from mandara.source.catalog import Catalog, Column, Key, Scope, Table


@given(st.integers(0, 60), st.integers(0, 60), st.integers(0, 200))
def test_chance_is_a_probability_that_falls_with_more_values(d: int, extra_k: int, extra_s: int) -> None:
    k, s = d + extra_k, d + extra_k + extra_s
    chance = nameless_joins.log10_chance(d, k, s)
    assert chance is not None and chance <= 1e-9
    if extra_s == 0:
        assert abs(chance) < 1e-9  # 隙間の無い連番のキー：包含は何の証拠にもならない
    if d < k:
        more = nameless_joins.log10_chance(d + 1, k, s)
        assert more is not None and more <= chance + 1e-9
    if s <= 60:
        assert math.isclose(10 ** chance, math.comb(k, d) / math.comb(s, d), rel_tol=1e-6)


def test_chance_is_unknown_for_keys_that_are_not_integers() -> None:
    assert nameless_joins.log10_chance(3, 10, None) is None


def _column(name: str, position: int) -> Column:
    return Column(name, position, "integer", TypeFamily.integer, False, None, None, None)


def _table(name: str, *columns: str) -> Table:
    return Table("s", name, ObjectKind.table, None, tuple(_column(c, i) for i, c in enumerate(columns, 1)),
                 Key(f"pk_{name}", (columns[0],)), (), (), ())


def _ref(table: str) -> SourceRef:
    return SourceRef(source="src", schema_name="s", table=table)


def _included(table: str, column: str, target: str, key: str, distinct: int, matched: int, target_values: int,
              span: tuple[int, int] | None, repeats: int = 2) -> Claim:
    """span：（列の値の範囲の整数の数、その範囲にあるキーの値の数）。"""
    return new_claim(subject=_ref(table), predicate=ClaimPredicate.included_in,
                     source=ClaimSource(kind=ClaimSourceKind.data, locator=f"{table}.{column} in {target}.{key}"),
                     extracted_by="test", confidence=1.0, columns=[column], target=_ref(target), target_columns=[key],
                     distinct_values=distinct, matched_values=matched, target_values=target_values, max_repeats=repeats,
                     value_span=span[0] if span else None, target_values_in_span=span[1] if span else None)


def _world() -> tuple[Ontology, list[Claim]]:
    """部品表の componentid は隙間の多い product に全部含まれる（名前の手がかりは無い）。数量 qty は連番にたまたま含まれる。
    bom.productid は名前で規則が選ぶ。bom の主キー bomid は、自分の ID がほかの連番に含まれるだけ。"""
    catalog = Catalog(Scope(("s",)), (_table("product", "productid"), _table("bom", "bomid", "productid", "componentid", "qty"),
                                      _table("serial", "serialid")))
    claims = [*catalog_extractor.extract("src", catalog),
              _included("bom", "productid", "product", "productid", 200, 200, 500, (999, 500)),
              _included("bom", "componentid", "product", "productid", 300, 300, 500, (999, 500)),
              _included("bom", "componentid", "serial", "serialid", 300, 300, 5000, (999, 999)),
              _included("bom", "qty", "serial", "serialid", 5, 5, 5000, (5, 5)),
              _included("bom", "qty", "product", "productid", 5, 5, 500, (5, 5)),
              _included("bom", "qty", "bom", "bomid", 5, 4, 40, (5, 4)),  # 包含が 100% でない（選択肢にしない）
              _included("bom", "bomid", "serial", "serialid", 40, 40, 5000, (40, 40), 1)]
    assert check_claims(claims) == []
    return propose("src", ("s",), claims).ontology, claims


def test_questions_ask_only_unlinked_non_key_columns_ordered_by_chance() -> None:
    ontology, claims = _world()
    asked = nameless_joins.questions(ontology, claims)
    assert [q.table.name for q in asked] == ["s.bom"]
    by_column = {a.column: a for a in asked[0].asked}
    assert sorted(by_column) == ["componentid", "qty"]  # productid は規則が選んだ。bomid は自分の主キー
    assert [t.option for t in by_column["componentid"].targets] == ["s.product.productid", "s.serial.serialid"]
    # 範囲の中でキーに隙間が無ければ、偶然の確率は 1。同じなら最も狭い包含先が先
    assert [t.option for t in by_column["qty"].targets] == ["s.product.productid", "s.serial.serialid"]
    assert all(abs(t.log10_chance or 0) < 1e-9 for t in by_column["qty"].targets)
    options = list(asked[0].request.questions["column componentid"].options)
    assert options == ["s.product.productid", "s.serial.serialid", nameless_joins.NOT_A_REFERENCE, classify.UNCLEAR]


def _response(answers: dict[str, tuple[str, float]], asked: nameless_joins.TableQuestions) -> DecisionResponse:
    out: dict[str, ChoiceAnswer] = {}
    for key, (choice, p) in answers.items():
        options = list(asked.request.questions[key].options)
        rest = (1 - p) / (len(options) - 1)
        out[key] = ChoiceAnswer(choice, {o: (p if o == choice else rest) for o in options})
    return DecisionResponse(out, Usage(10, 10), "fake", None)


def test_only_confident_target_choices_become_relations_with_capped_confidence() -> None:
    ontology, claims = _world()
    [asked] = nameless_joins.questions(ontology, claims)
    read = nameless_joins.read(asked, _response({"column componentid": ("s.product.productid", 0.95),
                                                 "column qty": (nameless_joins.NOT_A_REFERENCE, 0.9)}, asked))
    assert [d.asked.column for d in read.decided] == ["componentid"] and read.not_references == ("s.bom.qty",)
    updated = nameless_joins.apply(ontology, read.decided, "test-decider")
    assert check(updated) == []
    bom = next(t for t in updated.tables if t.name == "s.bom")
    [added] = [r for r in bom.relations or [] if r.inferred_by == "test-decider"]
    assert [(p.from_, p.to) for p in added.via or []] == [("componentid", "productid")]
    assert added.confidence == nameless_joins.CONFIDENCE_CAP
    assert added.evidence == [read.decided[0].target.claim.id]
    # 一度足した列は、次に問うときには問わない
    assert [a.column for q in nameless_joins.questions(updated, claims) for a in q.asked] == ["qty"]


def test_unclear_and_low_probability_answers_are_recorded_not_applied() -> None:
    ontology, claims = _world()
    [asked] = nameless_joins.questions(ontology, claims)
    read = nameless_joins.read(asked, _response({"column componentid": ("s.product.productid", 0.5),
                                                 "column qty": (classify.UNCLEAR, 0.8)}, asked))
    assert read.decided == () and read.not_references == ()
    assert sorted(d.item for d in read.abstained) == ["s.bom.componentid", "s.bom.qty"]


# ---------------------------------------------------------------- 法則（任意の台帳で）


_TABLES = ["a", "b", "c", "d"]


@st.composite
def _ledgers(draw: st.DrawFn) -> list[Claim]:
    """表 a〜d（主キー <表>id、整数の列 x, y, <表>id 以外の他の表の名前の列 <他>no）。包含の測定と宣言された外部キーを任意に。"""
    tables = [_table(t, f"{t}id", "x", "y", *(f"{o}no" for o in _TABLES if o != t)) for t in _TABLES]
    claims = catalog_extractor.extract("src", Catalog(Scope(("s",)), tuple(tables)))
    seen: set[tuple[str, str, str]] = set()
    for _ in range(draw(st.integers(0, 14))):
        table, target = draw(st.sampled_from(_TABLES)), draw(st.sampled_from(_TABLES))
        column = draw(st.sampled_from([f"{table}id", "x", "y", *(f"{o}no" for o in _TABLES if o != table)]))
        if (table, column, target) in seen or (table == target and column == f"{table}id"):
            continue
        seen.add((table, column, target))
        key_values = draw(st.integers(1, 50))
        distinct = draw(st.integers(0, key_values))
        matched = draw(st.sampled_from([distinct, max(0, distinct - 1)]))
        span = distinct + draw(st.integers(0, 60)) if distinct else None
        in_span = draw(st.integers(matched, min(span, key_values))) if span is not None else None
        claims.append(_included(table, column, target, f"{target}id", distinct, matched, key_values,
                                (span, in_span) if span is not None and in_span is not None else None,
                                draw(st.integers(1, 3)) if distinct else 0))
    for _ in range(draw(st.integers(0, 2))):  # 宣言された外部キー（範囲外の表を指すものも。関係にはならない）
        table = draw(st.sampled_from(_TABLES))
        claims.append(new_claim(subject=_ref(table), predicate=ClaimPredicate.references,
                                source=ClaimSource(kind=ClaimSourceKind.schema, locator=f"fk {table} {len(claims)}"),
                                extracted_by="test", confidence=1.0, columns=["y"],
                                target=SourceRef(source="src", schema_name="other", table="outside"),
                                target_columns=["id"], on_delete="no_action", on_update="no_action"))
    return claims


@given(_ledgers(), st.data())
def test_questions_and_apply_laws(claims: list[Claim], data: st.DataObject) -> None:
    """問う列は、関係の参照元・宣言された外部キーの列・1 列のキーのどれでもない。選択肢は上限まで、確率の低い順。
    名前の事実は、すべての候補で名前の手がかりが無いときだけ。どう答えても、当てた OM は検証を通り、確信度は上限以下で、
    足した列は次に問わない。"""
    from mandara.modeler import joins
    ontology = propose("src", ("s",), claims).ontology
    asked = nameless_joins.questions(ontology, claims)
    event(f"問う列 {min(3, sum(len(q.asked) for q in asked))}+")
    declared = {(c.subject.table, n) for c in claims if c.predicate == ClaimPredicate.references.value
                for n in c.columns or []}
    decided: list[nameless_joins.Decided] = []
    for q in asked:
        short = q.table.name.removeprefix("s.")
        linked = {r.via[0].from_ for r in q.table.relations or [] if r.via and len(r.via) == 1}
        for a in q.asked:
            assert a.column not in linked and (short, a.column) not in declared and a.column != f"{short}id"
            options = list(q.request.questions[f"column {a.column}"].options)
            assert len(options) <= nameless_joins.MAX_TARGETS + 2 and options[-2:] == [nameless_joins.NOT_A_REFERENCE,
                                                                                         classify.UNCLEAR]
            chances = [t.log10_chance if t.log10_chance is not None else 0.0 for t in a.targets]
            assert chances == sorted(chances) and all(c <= 0 for c in chances)
            every = [c for c in claims if c.predicate == ClaimPredicate.included_in.value and c.subject.table == short
                     and (c.columns or [None])[0] == a.column and c.distinct_values
                     and c.matched_values == c.distinct_values]
            assert a.nameless == all(
                joins.name_strength(a.column, c.target.table if c.target else "", (c.target_columns or [""])[0]) == 0
                for c in every)
            assert len(a.targets) + a.omitted == len(every)
            pick = data.draw(st.sampled_from([None, *a.targets]))
            if pick is not None:
                decided.append(nameless_joins.Decided(a, pick, data.draw(st.floats(0.6, 1.0))))
    updated = nameless_joins.apply(ontology, decided, "test-decider")
    assert check(updated) == []
    added = [r for t in updated.tables for r in t.relations or [] if r.inferred_by == "test-decider"]
    assert len(added) == len(decided)
    assert all(r.confidence is not None and r.confidence <= nameless_joins.CONFIDENCE_CAP for r in added)
    again = {(q.table.name, a.column) for q in nameless_joins.questions(updated, claims) for a in q.asked}
    assert again.isdisjoint({(d.asked.table, d.asked.column) for d in decided})


def test_an_integer_column_measured_without_its_range_is_an_old_ledger_and_an_error() -> None:
    ontology, claims = _world()
    stale = [_included("bom", "componentid", "product", "productid", 300, 300, 500, None) if
             c.predicate == ClaimPredicate.included_in.value and c.columns == ["componentid"]
             and c.target is not None and c.target.table == "product" else c for c in claims]
    import pytest
    with pytest.raises(ValueError, match="範囲を測る前の台帳"):
        nameless_joins.questions(ontology, stale)
