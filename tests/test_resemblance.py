"""値の似かた（T2.6）の法則。

- 見本の読み手（偽物と PostgreSQL）は、共通の契約（tests/sample_laws.py）に従う
- 値の文字列表現は型ごとに 1 通り。決められない型はエラー
- 構文パターン：段が上がるほど粗い（同じ段のパターンの値は、上の段でも同じパターン）。MDL の段は値の並びに依らない。
  見せる形の割合は多い順で和は 1 以下。パターンは文法どおりで、値の文字列を含まない
- 信号：パターンの揃った列は、それ自身と近い。同じ作り方の 2 つの見本はほとんど近く、作り方の違う見本は近くない（基準の法則）。
  パターンと NCD は対称。決定的
- 抽出：組は同じ群で名前の順、主張は検証を通る。近い順の選び方は入力の順に依らない。並列でも 1 プロセスでも同じ主張
- モデラー：事実を添えるのは、選択肢の意味型が付き、どれかの信号が近いと言う列だけ。古い版の主張は誤り
- 実データ：AdventureWorks の rowguid どうしは、パターンと圧縮で全部近く、2 標本検定でほぼ近い（有意水準 5% の偶然を除く）
"""

import hashlib
import random
import re
from collections.abc import Callable, Sequence
from typing import cast
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

import psycopg
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from psycopg import sql

from mandara.app.resemblance import measure_resemblance
from mandara.connectors.postgres.catalog_reader import PostgresCatalogReader
from mandara.connectors.postgres.sample_reader import PostgresSampleReader
from mandara.extractors import resemblance as extractor
from mandara.modeler import resemblance as modeler
from mandara.om.claims import check_claims, new_claim
from mandara.om.ontology import Ontology
from mandara.om.model import Claim, ClaimPredicate, ClaimSource, ClaimSourceKind, ObjectKind, SourceRef, TypeFamily
from mandara.resemblance import shape as shapes
from mandara.resemblance.signals import (
    SIGNALS,
    Measurement,
    StaleMethodError,
    closeness,
    method,
    prepare,
    signal_of,
)
from mandara.resemblance.values import render
from mandara.source.catalog import Catalog, Column, Scope, Table
from mandara.source.samples import ValueSample
from tests import sample_laws

CONNINFO = "dbname=adventureworks"


# ---------------------------------------------------------------- 見本の読み手


class SetSampler:
    """偽物：値の一覧から、値の表現のハッシュの順に選ぶ。"""

    def __init__(self, columns: dict[tuple[str, str, str], Sequence[object]]) -> None:
        self._columns = columns

    def sample(self, schema: str, table: str, column: str, limit: int) -> ValueSample:
        if limit < 1:
            raise ValueError(limit)
        rows = [v for v in self._columns[(schema, table, column)] if v is not None]
        distinct = sorted(set(rows), key=lambda v: (hashlib.md5(repr(v).encode()).hexdigest(), repr(v)))
        return ValueSample(len(rows), len(distinct), tuple(distinct[:limit]))


@settings(max_examples=60, deadline=None)
@given(values=st.lists(st.none() | st.integers(-5, 30) | st.text(max_size=4), max_size=40),
       limit=st.integers(1, 50))
def test_the_fake_sampler_keeps_the_contract(values: list[object], limit: int) -> None:
    reader = SetSampler({("s", "t", "c"): values})

    def oracle(schema: str, table: str, column: str) -> list[object]:
        return values
    assert sample_laws.sample_agrees_with_sets(reader, oracle, "s", "t", "c", limit) == []


POSTGRES_CASES = [("person", "person", "middlename"), ("person", "person", "persontype"),
                  ("person", "address", "rowguid"), ("production", "product", "listprice"),
                  ("humanresources", "employee", "birthdate"), ("sales", "salesorderheader", "modifieddate"),
                  ("production", "product", "productid"), ("production", "product", "color")]


def _oracle(schema: str, table: str, column: str) -> list[object]:
    with psycopg.connect(CONNINFO) as conn:
        query = sql.SQL("select {c} from {t}").format(c=sql.Identifier(column), t=sql.Identifier(schema, table))
        return [r[0] for r in conn.execute(query).fetchall()]


@pytest.mark.parametrize("case", POSTGRES_CASES)
@pytest.mark.parametrize("limit", [1, 30, 256])
def test_the_postgres_sampler_keeps_the_contract(case: tuple[str, str, str], limit: int) -> None:
    assert sample_laws.sample_agrees_with_sets(PostgresSampleReader(CONNINFO), _oracle, *case, limit) == []


# ---------------------------------------------------------------- 値の文字列表現


def test_render_is_one_text_per_value() -> None:
    assert [render(v) for v in (True, False, 12, "x", Decimal("12.50"), Decimal("1E+2"), 0.25)] == [
        "true", "false", "12", "x", "12.50", "100", "0.25"]
    assert render(date(2014, 5, 31)) == "2014-05-31"
    assert render(datetime(2014, 5, 31, 12, 0)) == "2014-05-31T12:00:00"
    assert render(time(9, 30)) == "09:30:00"
    assert render(UUID("12345678-1234-5678-1234-567812345678")) == "12345678-1234-5678-1234-567812345678"
    with pytest.raises(ValueError, match="決められない型"):
        render(b"bytes")


# ---------------------------------------------------------------- 構文パターン

values = st.lists(st.text(alphabet="AWaz09-. 漢", min_size=0, max_size=8), min_size=1, max_size=30)


@given(vs=values)
def test_levels_get_coarser(vs: list[str]) -> None:
    for level in (1, 2):
        coarser: dict[str, set[str]] = {}
        for v in vs:
            coarser.setdefault(shapes.pattern(v, level), set()).add(shapes.pattern(v, level + 1))
        assert all(len(up) == 1 for up in coarser.values())


@given(vs=values, seed=st.randoms())
def test_mdl_level_does_not_depend_on_the_order(vs: list[str], seed: object) -> None:
    import random
    assert isinstance(seed, random.Random)
    shuffled = list(vs)
    seed.shuffle(shuffled)
    assert shapes.mdl_level(vs) == shapes.mdl_level(shuffled)


PATTERN = re.compile(r"^(?:[9AaL_](?:\{\d+\}|\+)|\\.|\{(?:[9AaL_]|\\.)+\})+$")
"""パターンの文法：原子の種類と長さ、記号（\\ 付き）、原子の種類の集合だけ。値の文字列は入らない。"""


@given(vs=values.map(lambda xs: [x for x in dict.fromkeys(xs) if x]).filter(bool))
def test_shapes_are_summaries_without_the_text_of_values(vs: list[str]) -> None:
    found = shapes.shape(vs)
    shares = [s for _, s in found.patterns]
    assert shares == sorted(shares, reverse=True) and 0 < sum(shares) <= 1 + 1e-9
    assert all(PATTERN.match(p) for p, _ in found.patterns), found.patterns


def test_shapes_of_familiar_columns() -> None:
    accounts = [f"AW{n:08d}" for n in range(1000, 1300, 7)]
    assert shapes.shape(accounts).patterns == (("A{2}9{8}", 1.0),)
    dates = [f"2014-{m:02d}-{d:02d}" for m in range(1, 13) for d in (1, 15, 28)]
    assert shapes.shape(dates).patterns == (("9{4}\\-9{2}\\-9{2}", 1.0),)
    # 形のばらばらな文は、細かい段より粗い段が短く書ける
    prose = ["a tall order", "ok then", "Hello, World!", "x-y", "The end.", "Second thoughts", "no way, José",
             "Fine print here", "maybe", "Just 4 words here", "Q&A time", "It's late", "So be it", "Done; next."]
    assert shapes.mdl_level(prose) >= 2
    with pytest.raises(ValueError):
        shapes.mdl_level([])


@given(a=st.dictionaries(st.text(max_size=3), st.integers(1, 5)), b=st.dictionaries(st.text(max_size=3), st.integers(1, 5)))
def test_overlap_is_a_symmetric_share(a: dict[str, int], b: dict[str, int]) -> None:
    from collections import Counter
    x, y = Counter(a), Counter(b)
    assert shapes.overlap(x, y) == pytest.approx(shapes.overlap(y, x))
    assert 0 <= shapes.overlap(x, y) <= 1 + 1e-9
    if x:
        assert shapes.overlap(x, x) == pytest.approx(1)


# ---------------------------------------------------------------- 信号

column_values = st.lists(st.text(alphabet="AZaz0189-.", min_size=1, max_size=10), min_size=16, max_size=60, unique=True)


@settings(max_examples=40, deadline=None)
@given(vs=column_values)
def test_every_signal_finds_a_column_with_a_regular_shape_close_to_itself(vs: list[str]) -> None:
    """パターンの揃った列は、それ自身と近い（NCD は作りの上で値 = 基準なので、ここでは読み方だけを見る）。"""
    p = prepare(vs)
    for signal in SIGNALS:
        m = signal.measure(p, p)
        if signal.name == "pattern" and m.baseline <= 0.1:
            assert not signal.close(m)  # 列の中でもパターンが揃わない：基準が情報を持たず、近いとは言わない
            continue
        assert signal.close(m), (signal.name, m)
        assert signal.measure(p, p) == m  # 決定的


@settings(max_examples=30, deadline=None)
@given(a=column_values, b=column_values)
def test_pattern_and_ncd_are_symmetric(a: list[str], b: list[str]) -> None:
    """パターンと NCD は組の向きに依らない（2 標本検定は、同じ距離の近傍を見本の順で選ぶので向きで少し変わる。組は名前の順）。"""
    pa, pb = prepare(a), prepare(b)
    for signal in SIGNALS[:2]:
        assert signal.measure(pa, pb) == pytest.approx(signal.measure(pb, pa))


GENERATORS: dict[str, Callable[[random.Random], str]] = {
    "account": lambda r: f"AW{r.randrange(10 ** 8):08d}",
    "date": lambda r: f"20{r.randrange(10, 15)}-{r.randrange(1, 13):02d}-{r.randrange(1, 29):02d}",
    "price": lambda r: f"{r.uniform(1, 3000):.4f}",
    "word": lambda r: "".join(r.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(r.randrange(4, 11))).title(),
    "code": lambda r: "".join(r.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(3)),
}


def _draw(kind: str, seed: int, n: int = 120) -> list[str]:
    r = random.Random(f"{kind}-{seed}")
    out: dict[str, None] = {}
    while len(out) < n:
        out[GENERATORS[kind](r)] = None
    return list(out)


@pytest.mark.parametrize("kind", list(GENERATORS))
def test_two_samples_of_one_population_are_mostly_close(kind: str) -> None:
    """基準の法則：同じ作り方の値を独立に 2 回取った列は、ほとんど近い（2 標本検定は有意水準 5% で外れうる）。"""
    rates = {s.name: sum(s.close(s.measure(prepare(_draw(kind, i)), prepare(_draw(kind, i + 100))))
                         for i in range(10)) / 10 for s in SIGNALS}
    assert all(rate >= 0.8 for rate in rates.values()), rates


@pytest.mark.parametrize("a,b", [("account", "date"), ("price", "date"), ("word", "code"), ("account", "price")])
def test_samples_of_different_populations_are_not_close(a: str, b: str) -> None:
    """基準の法則：作り方の違う値の列は、どの信号でも近くない。"""
    for s in SIGNALS:
        close = sum(s.close(s.measure(prepare(_draw(a, i)), prepare(_draw(b, i)))) for i in range(5))
        assert close == 0, (s.name, a, b)


def test_signals_are_named_and_versioned_once() -> None:
    assert [s.name for s in SIGNALS] == ["pattern", "ncd", "c2st"]
    assert all(signal_of(method(s)) is s for s in SIGNALS)
    with pytest.raises(StaleMethodError):
        signal_of("ncd 0.0")
    with pytest.raises(ValueError, match="知らない信号"):
        signal_of("telepathy 1.0")


# ---------------------------------------------------------------- 抽出


def _column(name: str, family: TypeFamily, position: int) -> Column:
    return Column(name, position, str(family.value), family, True, None, None, None)


def _catalog() -> tuple[Catalog, dict[tuple[str, str, str], Sequence[object]]]:
    accounts = [f"AW{n:08d}" for n in range(100, 400, 3)]
    other_accounts = [f"AW{n:08d}" for n in range(50, 350, 3)]
    names = [f"{w} {n}" for w in ("Road", "Mountain", "Touring") for n in range(38, 60)]
    tables = (
        Table("s", "orders", ObjectKind.table, None, (_column("account", TypeFamily.text, 1),
                                                     _column("total", TypeFamily.decimal, 2),
                                                     _column("note", TypeFamily.text, 3)), None, (), (), ()),
        Table("s", "customers", ObjectKind.table, None, (_column("account", TypeFamily.text, 1),
                                                        _column("name", TypeFamily.text, 2),
                                                        _column("empty", TypeFamily.text, 3)), None, (), (), ()),
        Table("s", "v", ObjectKind.view, None, (_column("account", TypeFamily.text, 1),), None, (), (), ()),
    )
    columns: dict[tuple[str, str, str], Sequence[object]] = {
        ("s", "orders", "account"): accounts, ("s", "orders", "total"): [Decimal(f"{n}.25") for n in range(40)],
        ("s", "orders", "note"): names[::2], ("s", "customers", "account"): other_accounts,
        ("s", "customers", "name"): names, ("s", "customers", "empty"): [None, None],
    }
    return Catalog(Scope(("s",)), tables), columns


def test_pairs_stay_in_their_group_and_in_name_order() -> None:
    catalog, _ = _catalog()
    found = extractor.targets(catalog)
    assert [(t.table.name, t.column.name) for t in found] == [
        ("orders", "account"), ("orders", "total"), ("orders", "note"), ("customers", "account"), ("customers", "name"),
        ("customers", "empty")]  # ビューの列は比べない
    for i, j in extractor.pairs(found):
        assert found[i].group == found[j].group and found[i].key < found[j].key


@given(scores=st.dictionaries(st.tuples(st.integers(0, 6), st.integers(0, 6)).filter(lambda p: p[0] < p[1]),
                              st.floats(0, 1), min_size=1, max_size=15), k=st.integers(1, 4), seed=st.randoms())
def test_top_does_not_depend_on_the_order_of_its_input(scores: dict[tuple[int, int], float], k: int,
                                                       seed: object) -> None:
    import random
    assert isinstance(seed, random.Random)
    measured = {pair: {"pattern": Measurement(s, 0.5)} for pair, s in scores.items()}
    items = list(measured.items())
    seed.shuffle(items)
    assert extractor.top(dict(items), SIGNALS, k) == extractor.top(measured, SIGNALS, k)
    picked = extractor.top(measured, SIGNALS, k)
    for column in {c for pair in scores for c in pair}:  # 列ごとに高々 k 本は、その列が選んだ組
        mine = sorted(((-s, p) for p, s in scores.items() if column in p))[:k]
        assert {p for _, p in mine} <= picked


def test_measurement_makes_valid_claims_and_parallel_equals_serial() -> None:
    catalog, columns = _catalog()
    serial = measure_resemblance("src", catalog, SetSampler(columns), workers=1)
    parallel = measure_resemblance("src", catalog, SetSampler(columns), workers=2, chunk=1)  # プロセスプールを通す
    assert parallel.measured.workers == 2 and serial.measured.workers == 1
    assert serial.claims == parallel.claims and serial.claims
    assert check_claims(serial.claims) == []
    assert serial.measured.empty == 1 and serial.measured.columns == 6
    shaped = {c.subject.column: c for c in serial.claims if c.predicate == ClaimPredicate.shaped_as.value}
    assert shaped["account"].patterns == ["A{2}9{8}"]
    accounts = [c for c in serial.claims if c.predicate == ClaimPredicate.resembles.value
                and c.columns == ["account"] and c.target_columns == ["account"]]
    assert {c.method for c in accounts} == {method(s) for s in SIGNALS}
    assert all(signal_of(c.method or "").close(Measurement(c.score or 0, c.baseline or 0)) for c in accounts)


# ---------------------------------------------------------------- モデラー


def _resembles(a: tuple[str, str], b: tuple[str, str], signal_method: str, score: float, baseline: float) -> Claim:
    (ta, ca), (tb, cb) = sorted([a, b])
    return new_claim(
        subject=SourceRef(source="src", schema_name="s", table=ta), predicate=ClaimPredicate.resembles,
        source=ClaimSource(kind=ClaimSourceKind.data, locator=f"{ta}.{ca}~{tb}.{cb} {signal_method}"),
        extracted_by="t", confidence=1.0, columns=[ca], target=SourceRef(source="src", schema_name="s", table=tb),
        target_columns=[cb], method=signal_method, score=score, baseline=baseline, sampled_values=50,
        target_sampled_values=50)


def test_facts_name_close_columns_only_when_they_point_to_one_meaning() -> None:
    me = ("s.a", "x")
    claims = [
        _resembles(("a", "x"), ("b", "y"), "pattern 0.1", 0.95, 0.9),  # 近い、意味型 order_id
        _resembles(("a", "x"), ("b", "y"), "c2st 0.1", 0.5, 0.56),
        _resembles(("a", "x"), ("c", "z"), "pattern 0.1", 0.2, 0.9),  # 近くない（意味型が違っても数えない）
        _resembles(("a", "x"), ("d", "w"), "pattern 0.1", 0.99, 0.9),  # 近い。意味型を付けるかはケースごと
    ]
    found = modeler.index(claims)
    assert {n.member for n in found.of(me)} == {("s.b", "y"), ("s.c", "z"), ("s.d", "w")}
    assert {n.member for n in found.of(("s.b", "y"))} == {me}  # 両向きに引ける
    # 似た列が 1 つの意味型を指す：その列と信号ごとの文
    single = {("s.b", "y"): "order_id", ("s.c", "z"): "postal_code"}
    facts = modeler.option_facts(found, me, single, {"order_id"})
    assert facts.specific and len(facts.facts) == 1 and "s.b.y" in facts.facts[0]
    assert "2 of the 2 measures taken" in facts.facts[0] and "Not measured: ncd" in facts.facts[0]
    assert set(facts.evidence) == {claims[0].id, claims[1].id}
    assert modeler.option_facts(found, me, single, {"postal_code"}).facts == ()  # 指す意味型が選択肢に無い
    # 似た列が別々の意味型にまたがる：似かたは意味を絞らない、とだけ言う（どの列も挙げない）
    spread = {**single, ("s.d", "w"): "postal_code"}
    vague = modeler.option_facts(found, me, spread, {"order_id", "postal_code"})
    assert not vague.specific and len(vague.facts) == 1
    assert "2 different semantic types (order_id, postal_code)" in vague.facts[0] and "s.b.y" not in vague.facts[0]
    assert set(vague.evidence) == {claims[0].id, claims[1].id, claims[3].id}
    assert modeler.option_facts(found, me, {}, {"order_id"}).facts == ()  # 意味型の付いた似た列が無い


def test_an_old_signal_version_is_an_error() -> None:
    with pytest.raises(StaleMethodError):
        modeler.index([_resembles(("a", "x"), ("b", "y"), "pattern 0.0", 0.9, 0.9)])


def test_closeness_orders_by_direction() -> None:
    pattern, ncd = SIGNALS[0], SIGNALS[1]
    assert closeness(pattern, Measurement(0.9, 0)) > closeness(pattern, Measurement(0.1, 0))
    assert closeness(ncd, Measurement(0.1, 0)) > closeness(ncd, Measurement(0.9, 0))


# ---------------------------------------------------------------- 実データ


@pytest.fixture(scope="module")
def aw_production() -> list[Claim]:
    catalog = PostgresCatalogReader(CONNINFO).read(Scope(("production", "person")))
    return list(measure_resemblance("adventureworks", catalog, PostgresSampleReader(CONNINFO)).claims)


def test_row_guids_resemble_each_other_on_every_signal(aw_production: list[Claim]) -> None:
    claims = aw_production
    assert check_claims(claims) == []
    guids = [c for c in claims if c.predicate == ClaimPredicate.resembles.value
             and c.columns == ["rowguid"] and c.target_columns == ["rowguid"]]
    assert len({(c.subject.table, c.target.table if c.target else "") for c in guids}) >= 10
    close = [(c.method or "", signal_of(c.method or "").close(Measurement(c.score or 0, c.baseline or 0))) for c in guids]
    for name in ("pattern", "ncd"):
        assert all(ok for m, ok in close if m.startswith(name)), name
    # 2 標本検定は有意水準 5%：同じ母集団でも、およそ 5% の組は偶然に「見分けられる」と出る
    c2st = [ok for m, ok in close if m.startswith("c2st")]
    assert sum(c2st) >= 0.9 * len(c2st)


def test_the_evaluation_counts_are_consistent(aw_production: list[Claim]) -> None:
    """評価の数の勘定：兄弟 ≤ 近い組、つながらない兄弟 ≤ 兄弟、測れる組 ≤ 兄弟の組、当てた列 ≤ 兄弟のある列。"""
    from pathlib import Path

    from mandara.evaluation import resemblance as evaluation
    from mandara.extractors.catalog import extract
    from mandara.omstore.yaml_store import load_ontology

    catalog = extract("adventureworks", PostgresCatalogReader(CONNINFO).read(Scope(("production", "person"))))
    gold = load_ontology(Path(__file__).parent.parent / "evals/datasets/adventureworks/gold/ontology")
    found = evaluation.score([*catalog, *aw_production], gold)
    assert 0 < found.comparable_pairs <= found.sibling_pairs and found.unlinked_pairs <= found.sibling_pairs
    for part in found.parts:
        assert part.unlinked <= part.siblings <= part.close
        assert part.columns_hit <= found.columns_with_siblings
    by_name = {p.name: p for p in found.parts}
    assert by_name["1+ agree"].close >= by_name["2+ agree"].close >= by_name["3+ agree"].close
    assert by_name["3+ agree"].siblings > 0  # 行の guid どうしなど


def test_the_level_3_gold_renames_every_column_reference() -> None:
    """段 3 の正解 OM：列名を持つ場所（列、キー、関係の via、単位の列）をすべて同じ規則で変え、検証を通る。"""
    import importlib.util
    from pathlib import Path

    from mandara.om.invariants import check
    from mandara.omstore.yaml_store import load_ontology

    root = Path(__file__).parent.parent / "evals/datasets/adventureworks"
    spec = importlib.util.spec_from_file_location("rename_gold", root / "rename_gold.py")
    assert spec is not None and spec.loader is not None
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    gold = load_ontology(root / "gold/ontology")
    rename = cast(Callable[[Ontology], Ontology], tool.rename)
    opaque = cast(Callable[[str, str], str], tool.opaque)
    renamed = rename(gold)
    assert check(renamed) == []
    for before, after in zip(gold.tables, renamed.tables, strict=True):
        assert set(after.columns or {}) == {opaque(before.name, c) for c in before.columns or {}}
        assert all(n == c.name for n, c in (after.columns or {}).items())
        for r in after.relations or []:
            target = after if r.target == "self" else next(t for t in renamed.tables if t.id == r.target)
            assert all(p.from_ in (after.columns or {}) and p.to in (target.columns or {}) for p in r.via or [])
    assert opaque("sales.salesorderheader", "salesorderid") == "c_554c9d"  # make_bare.sh 3 の md5 と同じ
