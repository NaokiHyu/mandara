"""キーの推定（T2.7）の法則。

- UniquenessReader の契約（偽物と PostgreSQL）
- 極小な一意の組：見本がどれだけでも、総当たりで求めた極小な一意の組と同じ（見本での足切りは健全）。一意とした組は
  どれも DB で確かめる
- AdventureWorks の主キーと一意を外しても、データで極小な主キーは全部見つかる
- 規則：候補が 1 組なら主キーにする。2 組以上なら選ばない
- 判断モデルに問う：主キーの無い実体の表で候補が 2 組以上のものだけ。参照されている組が先。閾値以上で選んだ組だけ当てる
- 評価：当たり・部分・外れ・未決に分ける
- refine の段：問う表を全部問い、答えを得られなかった表は記録して次へ。予算で止めたら残りは問わない
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from itertools import combinations

import psycopg
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from psycopg import sql

from mandara.app import pipeline
from mandara.app.pipeline import measure_keys
from mandara.connectors.postgres.catalog_reader import PostgresCatalogReader
from mandara.connectors.postgres.uniqueness_reader import PostgresUniquenessReader
from mandara.evaluation import keys as keys_evaluation
from mandara.extractors import catalog as catalog_extractor
from mandara.extractors import keys as keys_extractor
from mandara.llm.decision import ChoiceAnswer, DecisionRequest, DecisionResponse
from mandara.llm.model import BudgetExceededError, LlmError, Usage
from mandara.modeler import classify, joins, keys
from mandara.modeler.llm import Dropped
from mandara.modeler.rules import propose
from mandara.om.claims import check_claims, new_claim
from mandara.om.invariants import check
from mandara.om.model import Claim, ClaimPredicate, ClaimSource, ClaimSourceKind, ObjectKind, SourceRef, TypeFamily
from mandara.om.ontology import Ontology
from mandara.source.catalog import Catalog, Column, Key, Scope, Table
from mandara.source.uniqueness import Distinct, RowSample
from tests import uniqueness_laws

CONNINFO = "dbname=adventureworks"


class ListUniqueness:
    """偽物：行の一覧から、行の表現のハッシュの順に見本を選ぶ。"""

    def __init__(self, tables: dict[tuple[str, str], tuple[Sequence[str], Sequence[tuple[object, ...]]]]) -> None:
        self._tables = tables

    def _project(self, schema: str, table: str, columns: Sequence[str]) -> list[tuple[object, ...]]:
        names, rows = self._tables[(schema, table)]
        index = [list(names).index(c) for c in columns]
        return [tuple(r[i] for i in index) for r in rows]

    def rows(self, schema: str, table: str, columns: Sequence[str], limit: int) -> RowSample:
        if not columns or limit < 1:
            raise ValueError((columns, limit))
        rows = self._project(schema, table, columns)
        ordered = sorted(rows, key=lambda r: (hashlib.md5(repr(r).encode()).hexdigest(), repr(r)))
        nulls = tuple(sum(1 for r in rows if r[i] is None) for i in range(len(columns)))
        return RowSample(len(rows), nulls, tuple(ordered[:limit]))

    def distinct(self, schema: str, table: str, columns: Sequence[str]) -> Distinct:
        rows = self._project(schema, table, columns)
        return Distinct(len(rows), len({r for r in rows if None not in r}))


_cell = st.none() | st.integers(0, 3)


@st.composite
def _tables(draw: st.DrawFn) -> tuple[list[str], list[tuple[object, ...]]]:
    width = draw(st.integers(1, 6))
    rows = draw(st.lists(st.tuples(*[_cell] * width), max_size=30))
    return [f"c{i}" for i in range(width)], rows


@settings(max_examples=60, deadline=None)
@given(table=_tables(), limit=st.integers(1, 40))
def test_the_fake_reader_keeps_the_contract(table: tuple[list[str], list[tuple[object, ...]]], limit: int) -> None:
    names, rows = table
    reader = ListUniqueness({("s", "t"): (names, rows)})

    def oracle(schema: str, t: str, columns: Sequence[str]) -> list[tuple[object, ...]]:
        index = [names.index(c) for c in columns]
        return [tuple(r[i] for i in index) for r in rows]
    for size in range(1, len(names) + 1):
        assert uniqueness_laws.rows_agree_with_lists(reader, oracle, "s", "t", names[:size], limit) == []


POSTGRES_CASES = [("person", "personphone", ("businessentityid", "phonenumber", "phonenumbertypeid")),
                  ("person", "address", ("addressline2", "city")),
                  ("production", "productproductphoto", ("productid", "productphotoid", "primary")),
                  ("humanresources", "employee", ("businessentityid", "birthdate", "jobtitle"))]


def _oracle(schema: str, table: str, columns: Sequence[str]) -> list[tuple[object, ...]]:
    with psycopg.connect(CONNINFO) as conn:
        query = sql.SQL("select {cs} from {t}").format(cs=sql.SQL(", ").join(map(sql.Identifier, columns)),
                                                      t=sql.Identifier(schema, table))
        return [tuple(r) for r in conn.execute(query).fetchall()]


@pytest.mark.parametrize("case", POSTGRES_CASES)
@pytest.mark.parametrize("limit", [1, 37, 100_000])
def test_the_postgres_reader_keeps_the_contract(case: tuple[str, str, tuple[str, ...]], limit: int) -> None:
    assert uniqueness_laws.rows_agree_with_lists(PostgresUniquenessReader(CONNINFO), _oracle, *case, limit) == []


# ---------------------------------------------------------------- 極小な一意の組


def _brute(names: list[str], rows: list[tuple[object, ...]]) -> list[tuple[str, ...]]:
    found: list[tuple[int, ...]] = []
    usable = [i for i in range(len(names)) if all(r[i] is not None for r in rows)]
    for size in range(1, keys_extractor.MAX_COLUMNS + 1):
        for combo in combinations(usable, size):
            unique = len({tuple(r[i] for i in combo) for r in rows}) == len(rows)
            if unique and not any(set(f) < set(combo) for f in found):
                found.append(combo)
    return [tuple(names[i] for i in c) for c in found]


@settings(max_examples=200, deadline=None)
@given(table=_tables(), limit=st.integers(1, 40))
def test_minimal_uniques_are_the_brute_force_ones_whatever_the_sample(
        table: tuple[list[str], list[tuple[object, ...]]], limit: int) -> None:
    names, rows = table
    reader = ListUniqueness({("s", "t"): (names, rows)})
    sample = reader.rows("s", "t", names, limit)
    verified: list[tuple[str, ...]] = []

    def verify(columns: tuple[str, ...]) -> bool:
        verified.append(columns)
        counted = reader.distinct("s", "t", columns)
        return counted.distinct == counted.rows

    found = keys_extractor.minimal_uniques(names, sample, verify)
    assert found == ([] if not rows else _brute(names, rows))
    assert set(found) <= set(verified)  # 一意とした組は、どれも DB で確かめた
    assert all(not set(a) < set(b) for a in found for b in found)  # 極小


def test_adventureworks_minimal_primary_keys_are_found_without_declarations() -> None:
    """主キーと一意の宣言を外した AdventureWorks（2 スキーマ）で、データで極小な宣言の主キーは全部見つかる。"""
    declared = PostgresCatalogReader(CONNINFO).read(Scope(("humanresources", "purchasing")))
    bare = replace(declared, tables=tuple(replace(t, primary_key=None, uniques=()) for t in declared.tables))
    measured = measure_keys("adventureworks", bare, PostgresUniquenessReader(CONNINFO))
    assert check_claims(measured.claims) == []
    found = {(c.subject.table, frozenset(c.columns or [])) for c in measured.claims}
    tables = [t for t in declared.tables if t.kind == ObjectKind.table.value and t.primary_key is not None]
    hits = [t.name for t in tables if t.primary_key and (t.name, frozenset(t.primary_key.columns)) in found]
    # 極小でない主キー（データでは真部分集合が一意）：employeedepartmenthistory、purchaseorderdetail
    assert sorted(set(t.name for t in tables) - set(hits)) == ["employeedepartmenthistory", "purchaseorderdetail"]
    assert measured.measured.tables == len([t for t in bare.tables if t.kind == ObjectKind.table.value])


# ---------------------------------------------------------------- 規則と判断モデル


def _column(name: str, position: int, family: TypeFamily = TypeFamily.integer) -> Column:
    return Column(name, position, family.value, family, False, None, None, None)


def _world() -> tuple[Ontology, list[Claim]]:
    """主キーの宣言の無い 3 表：product は id と guid が一意（id はほかの表から参照される）、order は no だけ、line は無い。"""
    product = Table("s", "product", ObjectKind.table, None,
                    (_column("guid", 1, TypeFamily.uuid), _column("id", 2), _column("name", 3, TypeFamily.text)),
                    None, (), (), ())
    order = Table("s", "order", ObjectKind.table, None, (_column("no", 1), _column("productid", 2)), None, (), (), ())
    line = Table("s", "line", ObjectKind.table, None, (_column("qty", 1),), None, (), (), ())
    catalog = Catalog(Scope(("s",)), (product, order, line))
    claims = [*catalog_extractor.extract("src", catalog),
              *keys_extractor.claims("src", keys_extractor.Found(product, 10, (("guid",), ("id",), ("name",)))),
              *keys_extractor.claims("src", keys_extractor.Found(order, 7, (("no",),))),
              new_claim(subject=SourceRef(source="src", schema_name="s", table="order"),
                        predicate=ClaimPredicate.included_in,
                        source=ClaimSource(kind=ClaimSourceKind.data, locator="order.productid in product.id"),
                        extracted_by="test", confidence=1.0, columns=["productid"],
                        target=SourceRef(source="src", schema_name="s", table="product"), target_columns=["id"],
                        distinct_values=5, matched_values=5, target_values=10, max_repeats=2)]
    assert check_claims(claims) == []
    return propose("src", ("s",), claims).ontology, claims


def _identity(ontology: Ontology, name: str) -> list[str] | None:
    table = next(t for t in ontology.tables if t.name == name)
    return list(table.identity.primary) if table.identity is not None else None


def test_rules_take_the_only_unique_column_set_and_leave_the_rest() -> None:
    ontology, claims = _world()
    assert check(ontology) == []
    assert (_identity(ontology, "s.order"), _identity(ontology, "s.product"), _identity(ontology, "s.line")) == (
        ["no"], None, None)
    order = next(t for t in ontology.tables if t.name == "s.order")
    [unique] = [c for c in claims if c.predicate == ClaimPredicate.unique_in_data.value and c.subject.table == "order"]
    assert unique.id in (order.evidence or [])


def _response(question: keys.TableQuestion, choice: str, p: float) -> DecisionResponse:
    options = list(question.request.questions["primary key"].options)
    rest = (1 - p) / (len(options) - 1)
    return DecisionResponse({"primary key": ChoiceAnswer(choice, {o: (p if o == choice else rest) for o in options})},
                            Usage(10, 10), "fake", None)


def test_the_decider_is_asked_only_where_several_sets_are_unique_and_referenced_sets_come_first() -> None:
    ontology, claims = _world()
    [question] = keys.questions(ontology, claims)
    assert question.table.name == "s.product"
    assert list(question.request.questions["primary key"].options) == [
        "key id", "key guid", "key name", keys.NONE_OF_THESE, classify.UNCLEAR]
    decided = keys.read(question, _response(question, "key id", 0.9))
    assert isinstance(decided, keys.Decided)
    updated = keys.apply(ontology, [decided])
    assert check(updated) == [] and _identity(updated, "s.product") == ["id"]
    product = next(t for t in updated.tables if t.name == "s.product")
    assert decided.candidate.claim.id in (product.evidence or [])
    assert product.confidence is not None and product.confidence <= keys.CONFIDENCE_CAP
    assert keys.questions(updated, claims) == []  # 主キーのある表は問わない
    # 後段には、主キーに選んだ組だけが渡る
    table_claims = joins.claims_for(product, [c for c in claims if c.subject.table == "product"])
    assert [c.columns for c in table_claims if c.predicate == ClaimPredicate.unique_in_data.value] == [["id"]]
    assert classify.state(table_claims)["primary_key"] == ["id"]


@pytest.mark.parametrize(("choice", "p"), [(classify.UNCLEAR, 0.9), ("key id", 0.4), (keys.NONE_OF_THESE, 0.9)])
def test_unclear_low_and_none_answers_are_recorded_not_applied(choice: str, p: float) -> None:
    ontology, claims = _world()
    [question] = keys.questions(ontology, claims)
    assert isinstance(keys.read(question, _response(question, choice, p)), Dropped)


# ---------------------------------------------------------------- 評価


def test_key_score_splits_exact_subset_wrong_and_undecided() -> None:
    def table(name: str, key: tuple[str, ...]) -> Table:
        return Table("s", name, ObjectKind.table, None, tuple(_column(c, i) for i, c in enumerate(("a", "b", "c"), 1)),
                     Key(f"pk_{name}", key), (), (), ())
    truth = Catalog(Scope(("s",)), (table("t1", ("a",)), table("t2", ("a", "b")), table("t3", ("a",)),
                                    table("t4", ("b",))))
    bare = replace(truth, tables=tuple(replace(t, primary_key=None) for t in truth.tables))
    claims = [*catalog_extractor.extract("src", bare)]
    for t, key in (("t1", ("a",)), ("t2", ("b",)), ("t3", ("b",))):
        found = next(x for x in bare.tables if x.name == t)
        claims += keys_extractor.claims("src", keys_extractor.Found(found, 3, (key,)))
    ontology = propose("src", ("s",), claims).ontology
    score = keys_evaluation.score(ontology, truth)
    assert (score.tables, score.exact) == (4, 1)
    assert [m.table for m in score.subset] == ["s.t2"] and [m.table for m in score.wrong] == ["s.t3"]
    assert [m.table for m in score.undecided] == ["s.t4"]


# ---------------------------------------------------------------- refine の段（判断モデルを呼ぶところ）


@dataclass
class _Decider:
    """判断モデルの偽物：問いの目的ごとに、選ぶ選択肢か例外。"""

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
        options = list(request.questions["primary key"].options)
        rest = 0.1 / (len(options) - 1)
        return DecisionResponse({"primary key": ChoiceAnswer(reply, {o: 0.9 if o == reply else rest for o in options})},
                                Usage(10, 10), self.model, None)


def _two_tables() -> tuple[Ontology, list[Claim]]:
    """主キーの宣言が無く、一意な組が 2 つずつある表 a と b。"""
    tables = [Table("s", n, ObjectKind.table, None, (_column("x", 1), _column("y", 2)), None, (), (), ()) for n in "ab"]
    claims = [*catalog_extractor.extract("src", Catalog(Scope(("s",)), tuple(tables)))]
    for t in tables:
        claims += keys_extractor.claims("src", keys_extractor.Found(t, 5, (("x",), ("y",))))
    return propose("src", ("s",), claims).ontology, claims


def test_refine_asks_every_open_table_records_failures_and_stops_on_the_budget() -> None:
    ontology, claims = _two_tables()
    decider = _Decider({"primary key s.a": "key y", "primary key s.b": LlmError("HTTP 529")})
    decided, outcomes, stopped = pipeline._decide_keys(ontology, claims, decider)  # pyright: ignore[reportPrivateUsage]
    assert stopped is None and decider.asked == ["primary key s.a", "primary key s.b"]
    assert [(o.table, o.status, o.chosen) for o in outcomes] == [("s.a", "answered", "key y"), ("s.b", "failed", None)]
    assert (_identity(decided, "s.a"), _identity(decided, "s.b")) == (["y"], None)
    assert outcomes[0].answer is not None  # 課金済みの答えは記録に残す

    budget = BudgetExceededError("primary key s.a", 0.0, 1.0, 0.5)
    decider = _Decider({"primary key s.a": budget, "primary key s.b": "key x"})
    decided, outcomes, stopped = pipeline._decide_keys(ontology, claims, decider)  # pyright: ignore[reportPrivateUsage]
    assert stopped is not None and "予算" in stopped and decider.asked == ["primary key s.a"]  # 止めた後は問わない
    assert [o.status for o in outcomes] == ["not_reached", "not_reached"] and decided == ontology

    with pytest.raises(ValueError, match="判断モデルの上限"):
        pipeline._decide_keys(ontology, claims, _Decider({}, max_options=keys.MAX_CANDIDATES + 1))  # pyright: ignore[reportPrivateUsage]


def test_join_choice_treats_the_rule_key_as_the_table_own_key() -> None:
    """結合の選び方の「表の主キー」は規則と同じ：宣言、無ければ一意な組が 1 つだけのときのその組。"""
    ontology, claims = _world()
    rule_keys = joins.own_keys(claims)
    assert rule_keys == {"s.order": ("no",)}  # product は候補が 3 組（判断モデルが選ぶ）、line は候補なし
    assert all((_identity(ontology, n) or []) == list(k) for n, k in rule_keys.items())
