"""宣言の無い参照の推定（T2.2）の法則。

- 足切り（narrow）は健全：値が本当にキーに含まれる組は、決して落とさない
- 包含の読み手（PostgreSQL）は、行を読んで集合で数えたのと同じ数を返す。見本の重なりは見本の数を超えない
- 選び方（choose）：1 列に参照先は 1 つ。選んだ組は包含が 100% で名前の手がかりがある。表の 1 列の主キーが別の表を指すのは
  名前が完全に一致するときだけ。値の集合が同じ表どうしの両向きは作らない。入力の順に依らない
- 実データ：AdventureWorks で、宣言された 1 列の外部キーのほとんどを、宣言を見ずに値の包含から選べる
"""

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mandara.connectors.postgres.catalog_reader import PostgresCatalogReader
from mandara.connectors.postgres.inclusion_reader import PostgresInclusionReader
from mandara.evaluation import joins as joins_evaluation
from mandara.extractors import joins as joins_extractor
from mandara.modeler import joins
from mandara.om.claims import new_claim
from mandara.om.model import Claim, ClaimPredicate, ClaimSource, ClaimSourceKind, ObjectKind, SourceRef, TypeFamily
from mandara.source.catalog import Catalog, Column, Key, Scope, Table
from mandara.source.inclusion import ColumnProfile, Inclusion, KeyRef
from tests import inclusion_laws

CONNINFO = "dbname=adventureworks"
SCHEMAS = ("person", "humanresources", "sales", "purchasing", "production")


# ---------------------------------------------------------------- 名前の手がかり


def test_name_strength_orders_the_kinds_of_hints() -> None:
    assert joins.name_strength("productid", "product", "productid") == 3
    assert joins.name_strength("ProductID", "product", "productid") == 3  # 大文字小文字と記号は見ない
    assert joins.name_strength("billtoaddressid", "address", "addressid") == 2
    assert joins.name_strength("personid", "person", "businessentityid") == 2
    assert joins.name_strength("productassemblyid", "product", "productid") == 1
    assert joins.name_strength("componentid", "product", "productid") == 0
    assert joins.name_strength("", "product", "productid") == 0
    # 日本語や全角の名前も、正規化して同じに比べる（ファイルのソースの列名など）
    assert joins.name_strength("顧客ID", "顧客", "顧客ｉｄ") == 3
    assert joins.name_strength("請求先顧客ID", "顧客", "顧客ID") == 2
    assert joins.name_strength("担当顧客番号", "顧客", "顧客ID") == 1


# ---------------------------------------------------------------- 足切りの健全さ


def _profile(values: set[int]) -> ColumnProfile:
    return ColumnProfile(len(values), len(values), min(values) if values else None, max(values) if values else None)


def _column(name: str, family: TypeFamily = TypeFamily.integer, source_type: str = "integer") -> Column:
    return Column(name, 1, source_type, family, False, None, None, None)


@given(st.sets(st.integers(-50, 50), max_size=30), st.sets(st.integers(-50, 50), min_size=1, max_size=30))
def test_narrow_never_drops_a_key_that_includes_the_values(values: set[int], key_values: set[int]) -> None:
    key_table = Table("s", "k", ObjectKind.table, None, (_column("id"),), Key("pk", ("id",)), (), (), ())
    key = joins_extractor.Key(key_table, key_table.columns[0])
    candidate = joins_extractor.Candidate(Table("s", "t", ObjectKind.table, None, (_column("ref"),), None, (), (), ()),
                                          _column("ref"), (key,))
    kept = joins_extractor.narrow(candidate, _profile(values), {key.ref: _profile(key_values)})
    if values and values <= key_values:
        assert kept == [key]
    if not values:
        assert kept == []


def test_candidates_match_by_type_family_and_skip_the_key_itself() -> None:
    small = Column("departmentid", 2, "smallint", TypeFamily.integer, False, None, None, None)
    text = Column("name", 3, "text", TypeFamily.text, False, None, None, None)
    key_table = Table("s", "department", ObjectKind.table, None, (_column("departmentid"),),
                      Key("pk", ("departmentid",)), (), (), ())
    table = Table("s", "history", ObjectKind.table, None, (_column("id"), small, text), Key("pk", ("id",)), (), (), ())
    view = Table("s", "v", ObjectKind.view, None, (small,), None, (), (), ())
    catalog = Catalog(Scope(("s",)), (key_table, table, view))
    found = joins_extractor.keys(catalog, [])
    assert sorted((k.table.name, k.column.name) for k in found) == [("department", "departmentid"), ("history", "id")]
    by = {(c.table.name, c.column.name): sorted(k.table.name for k in c.keys)
          for c in joins_extractor.candidates(catalog, found)}
    # smallint の列が integer のキーを指せる（型族で比べる）。キーの列自身と、ビューと、型族の違う列は候補にしない
    assert by == {("department", "departmentid"): ["history"], ("history", "id"): ["department"],
                  ("history", "departmentid"): ["department", "history"]}


# ---------------------------------------------------------------- 包含の読み手（共通の法則を、偽物と PostgreSQL に）


class SetReader:
    """集合で数える偽物（法則の基準）。tables は（スキーマ, 表）→ 列 → 値の一覧。"""

    def __init__(self, tables: dict[tuple[str, str], dict[str, list[object]]]) -> None:
        self.tables = tables

    def values(self, schema: str, table: str, column: str) -> list[object]:
        return self.tables[(schema, table)][column]

    def profile(self, schema: str, table: str, column: str, ordered: bool) -> ColumnProfile:
        rows = [v for v in self.values(schema, table, column) if v is not None]
        distinct = sorted({v for v in rows}, key=repr)
        ints = [v for v in rows if isinstance(v, int)]
        low = min(ints) if ordered and ints and len(ints) == len(rows) else None
        high = max(ints) if ordered and ints and len(ints) == len(rows) else None
        return ColumnProfile(len(rows), len(distinct), low, high)

    def inclusion(self, schema: str, table: str, column: str, keys: Sequence[KeyRef], spans: bool) -> Inclusion:
        rows = [v for v in self.values(schema, table, column) if v is not None]
        distinct = set(rows)
        key_sets = [{v for v in self.values(k.schema, k.table, k.column) if v is not None} for k in keys]
        ints = [v for v in distinct if isinstance(v, int)]
        if spans and len(ints) != len(distinct):
            raise AssertionError("整数でない列に範囲の幅を求めた")
        low, high = (min(ints), max(ints)) if spans and ints else (0, -1)
        ranged = spans and bool(ints)
        return Inclusion(len(distinct), max(Counter(rows).values(), default=0),
                         tuple(len(distinct & k) for k in key_sets), tuple(len(k) for k in key_sets),
                         high - low + 1 if ranged else None,
                         tuple(sum(1 for v in k if isinstance(v, int) and low <= v <= high) for k in key_sets)
                         if ranged else None)

    def probe(self, schema: str, table: str, column: str, keys: Sequence[KeyRef], sample: int) -> tuple[int, ...]:
        head = set(sorted({v for v in self.values(schema, table, column) if v is not None}, key=repr)[:sample])
        return tuple(len(head & {v for v in self.values(k.schema, k.table, k.column)}) for k in keys)


@given(st.lists(st.none() | st.integers(0, 30), max_size=40), st.lists(st.integers(0, 40), max_size=30, unique=True),
       st.lists(st.integers(10, 60), max_size=30, unique=True), st.integers(1, 8), st.booleans())
def test_the_fake_reader_obeys_the_laws(column: list[int | None], key1: list[int], key2: list[int], sample: int,
                                        spans: bool) -> None:
    reader = SetReader({("s", "t"): {"c": list(column)}, ("s", "k1"): {"id": list(key1)}, ("s", "k2"): {"id": list(key2)}})
    keys = [KeyRef("s", "k1", "id"), KeyRef("s", "k2", "id")]
    assert inclusion_laws.counts_agree_with_sets(reader, reader.values, "s", "t", "c", keys, sample, spans) == []


INCLUSION = [
    # 整数のキー（幅も数える）
    ("sales", "salesorderheader", "territoryid", [("sales", "salesterritory", "territoryid"),
                                                  ("sales", "customer", "customerid")], True),
    # 型をまたぐ等値：smallint の列と integer のキー
    ("humanresources", "employeedepartmenthistory", "departmentid", [("humanresources", "department", "departmentid")],
     True),
    # 文字列のキー（固定長 char と可変長 varchar。同じ型どうし）
    ("sales", "countryregioncurrency", "currencycode", [("sales", "currency", "currencycode")], False),
    ("person", "stateprovince", "countryregioncode", [("person", "countryregion", "countryregioncode")], False),
    # uuid（どれも重ならない）
    ("person", "addresstype", "rowguid", [("person", "person", "rowguid")], False),
]


def _values(schema: str, table: str, column: str) -> list[object]:
    """oracle：列の値を、数えずにそのまま読む。"""
    import psycopg
    from psycopg import sql
    with psycopg.connect(CONNINFO) as conn:
        query = sql.SQL("select {c} from {t}").format(c=sql.Identifier(column), t=sql.Identifier(schema, table))
        return [r[0] for r in conn.execute(query).fetchall()]


@pytest.mark.parametrize(("schema", "table", "column", "keys", "integer"), INCLUSION)
def test_postgres_reader_obeys_the_laws(schema: str, table: str, column: str, keys: list[tuple[str, str, str]],
                                        integer: bool) -> None:
    reader = PostgresInclusionReader(CONNINFO)
    refs = [KeyRef(*k) for k in keys]
    for spans in {False, integer}:
        assert inclusion_laws.counts_agree_with_sets(reader, _values, schema, table, column, refs,
                                                     joins_extractor.PROBE_SAMPLE, spans) == []


def test_profile_reads_ordered_bounds_of_dates_and_decimals() -> None:
    reader = PostgresInclusionReader(CONNINFO)
    assert isinstance(reader.profile("sales", "salesorderheader", "orderdate", ordered=True).low, date)
    assert isinstance(reader.profile("sales", "salesorderheader", "totaldue", ordered=True).high, Decimal)
    assert reader.profile("person", "person", "lastname", ordered=False).low is None


@given(st.sets(st.dates(), max_size=20), st.sets(st.dates(), min_size=1, max_size=20))
def test_narrow_is_sound_for_dates(values: set[date], key_values: set[date]) -> None:
    key_table = Table("s", "k", ObjectKind.table, None, (_column("d", TypeFamily.date, "date"),), Key("pk", ("d",)),
                      (), (), ())
    key = joins_extractor.Key(key_table, key_table.columns[0])
    candidate = joins_extractor.Candidate(key_table, _column("x", TypeFamily.date, "date"), (key,))
    profile = ColumnProfile(len(values), len(values), min(values) if values else None, max(values) if values else None)
    kept = joins_extractor.narrow(candidate, profile,
                                  {key.ref: ColumnProfile(len(key_values), len(key_values), min(key_values), max(key_values))})
    if values and values <= key_values:
        assert kept == [key]


def test_bounds_that_cannot_be_compared_do_not_narrow() -> None:
    """タイムゾーンの有無が違う日時や NaN は範囲で比べない（落とさない側に倒す。例外にもしない）。"""
    naive, aware = datetime(2020, 1, 1), datetime(2021, 1, 1, tzinfo=UTC)
    assert not joins_extractor.comparable(naive, aware)
    assert not joins_extractor.comparable(Decimal("NaN"), Decimal(1))
    assert joins_extractor.comparable(1, Decimal("2.5")) and joins_extractor.comparable(date(2020, 1, 1), date(2021, 1, 1))
    key_table = Table("s", "k", ObjectKind.table, None, (_column("t", TypeFamily.timestamp, "timestamptz"),),
                      Key("pk", ("t",)), (), (), ())
    key = joins_extractor.Key(key_table, key_table.columns[0])
    candidate = joins_extractor.Candidate(key_table, _column("x", TypeFamily.timestamp, "timestamp"), (key,))
    kept = joins_extractor.narrow(candidate, ColumnProfile(1, 1, naive, naive),
                                  {key.ref: ColumnProfile(1, 1, aware, aware)})
    assert kept == [key]


# ---------------------------------------------------------------- 選び方


def _ref(table: str) -> SourceRef:
    return SourceRef(source="src", schema_name="s", table=table)


def _included(table: str, column: str, target: str, key: str, distinct: int, matched: int, target_values: int,
              repeats: int = 2) -> Claim:
    return new_claim(subject=_ref(table), predicate=ClaimPredicate.included_in,
                     source=ClaimSource(kind=ClaimSourceKind.data, locator=f"{table}.{column} in {target}.{key}"),
                     extracted_by="test", confidence=1.0, columns=[column], target=_ref(target), target_columns=[key],
                     distinct_values=distinct, matched_values=matched, target_values=target_values, max_repeats=repeats)


def _key(table: str, column: str) -> Claim:
    return new_claim(subject=_ref(table), predicate=ClaimPredicate.identified_by,
                     source=ClaimSource(kind=ClaimSourceKind.schema, locator=f"pk {table}"), extracted_by="test",
                     confidence=1.0, columns=[column])


def test_choose_prefers_the_closest_superset_and_breaks_same_set_cycles_by_hub() -> None:
    # entity ⊃ person = password（同じ集合）⊃ employee。person は多くの表から名前で指される（ハブ）
    claims = [_key("entity", "entityid"), _key("person", "entityid"), _key("password", "entityid"),
              _key("employee", "entityid"),
              _included("person", "entityid", "entity", "entityid", 100, 100, 120, 1),
              _included("person", "entityid", "password", "entityid", 100, 100, 100, 1),
              _included("password", "entityid", "person", "entityid", 100, 100, 100, 1),
              _included("password", "entityid", "entity", "entityid", 100, 100, 120, 1),
              _included("employee", "entityid", "person", "entityid", 10, 10, 100, 1),
              _included("employee", "entityid", "password", "entityid", 10, 10, 100, 1),
              _included("employee", "entityid", "entity", "entityid", 10, 10, 120, 1),
              _included("order", "personid", "person", "entityid", 50, 50, 100),
              _included("contact", "personid", "person", "entityid", 5, 5, 100)]
    picked = {(c.claim.subject.table, c.claim.target.table if c.claim.target else "") for c in joins.choose(claims)}
    assert picked == {("person", "entity"), ("password", "person"), ("employee", "person"), ("order", "person"),
                      ("contact", "person")}


def test_choose_skips_partial_inclusion_nameless_columns_and_primary_keys_that_only_contain_a_name() -> None:
    claims = [_key("addresstype", "addresstypeid"),
              _included("addresstype", "addresstypeid", "address", "addressid", 6, 6, 1000, 1),  # 主キーが名前を含むだけ
              _included("order", "componentid", "product", "productid", 5, 5, 500),  # 名前の手がかりが無い
              _included("order", "productid", "product", "productid", 5, 4, 500)]  # 包含が 100% でない
    assert joins.choose(claims) == []


@st.composite
def _ledgers(draw: st.DrawFn) -> list[Claim]:
    tables = ["a", "b", "c", "d"]
    out: list[Claim] = [_key(t, f"{t}id") for t in tables]
    for t in tables:
        for target in tables:
            if t == target or not draw(st.booleans()):
                continue
            distinct = draw(st.integers(1, 20))
            target_values = draw(st.integers(distinct, 25))
            matched = draw(st.sampled_from([distinct, max(0, distinct - 1)]))
            column = draw(st.sampled_from([f"{target}id", f"x{target}id", f"{t}id", "other"]))
            out.append(_included(t, column, target, f"{target}id", distinct, matched, target_values,
                                 draw(st.integers(1, 3))))
    return out


@settings(max_examples=150)
@given(_ledgers(), st.randoms(use_true_random=False))
def test_choose_laws(claims: list[Claim], rnd: object) -> None:
    import random
    chosen = joins.choose(claims)
    columns = [(c.claim.subject.table, tuple(c.claim.columns or [])) for c in chosen]
    assert len(columns) == len(set(columns))  # 1 列に参照先は 1 つ
    keys = {c.subject.table: tuple(c.columns or []) for c in claims if c.predicate == ClaimPredicate.identified_by.value}
    for c in chosen:
        assert c.claim.matched_values == c.claim.distinct_values and c.strength >= 1
        assert c.confidence == joins.CONFIDENCE[c.strength] and c.one_to_one == (c.claim.max_repeats == 1)
        if keys.get(c.claim.subject.table) == tuple(c.claim.columns or []):
            assert c.strength == 3  # 表の 1 列の主キーが別の表を指すのは、名前が完全に一致するときだけ
    same = {(c.claim.subject.table, c.claim.target.table if c.claim.target else "") for c in chosen
            if c.claim.matched_values == c.claim.distinct_values == c.claim.target_values}
    assert not any((t, s) in same for s, t in same)  # 値の集合が同じ表どうしの両向きは作らない
    shuffled = list(claims)
    assert isinstance(rnd, random.Random)
    rnd.shuffle(shuffled)
    assert joins.choose(shuffled) == chosen  # 入力の順に依らない


# ---------------------------------------------------------------- 評価と、実データでの再現率


def test_score_joins_counts_single_and_composite_separately() -> None:
    a = ("s.t", (("x", "id"),), "s.u")
    b = ("s.t", (("p", "a"), ("q", "b")), "s.v")
    c = ("s.w", (("y", "id"),), "s.u")
    score = joins_evaluation.score_joins({a, c}, {a, b})
    assert (score.single.truth, score.single.proposed, score.single.correct) == (1, 2, 1)
    assert (score.composite.truth, score.composite.correct, score.composite.recall) == (1, 0, 0.0)
    assert score.missed == (b,) and score.wrong == (c,) and score.single.precision == 0.5


def test_adventureworks_declared_foreign_keys_are_recovered_from_values_alone() -> None:
    """宣言された外部キー（正解）を見ずに、値の包含と名前だけで、1 列の外部キーの 95% 以上を選べる。"""
    catalog = PostgresCatalogReader(CONNINFO).read(Scope(SCHEMAS))
    reader = PostgresInclusionReader(CONNINFO)
    from mandara.app.pipeline import measure_joins
    from mandara.extractors import catalog as catalog_extractor
    measured = measure_joins("adventureworks", catalog, reader, [])
    declared = [c for c in catalog_extractor.extract("adventureworks", catalog)
                if c.predicate == ClaimPredicate.identified_by.value]  # 主キーだけ（外部キーの主張は渡さない）
    chosen = joins.choose([*declared, *measured.claims])
    found = {(f"{c.claim.subject.schema_name}.{c.claim.subject.table}", ((c.claim.columns or [""])[0],
              (c.claim.target_columns or [""])[0]), f"{c.claim.target.schema_name}.{c.claim.target.table}")
             for c in chosen if c.claim.target is not None}
    truth = {(t, p[0], u) for t, p, u in joins_evaluation.truth(catalog) if len(p) == 1}
    assert len(truth & found) / len(truth) >= 0.95, sorted(truth - found)  # 再現率
    # 適合率：宣言の外の提案は、意味としては正しい結合（宣言されていないだけ）もあるので、下限は緩めに見る
    assert len(truth & found) / len(found) >= 0.9, sorted(found - truth)


def test_claims_for_keeps_only_the_measurements_that_back_a_relation() -> None:
    """推論に渡す主張は入力の部分集合で、included_in は関係の根拠になっているものだけが過不足なく残る。"""
    from mandara.om.model import ColumnPair, Relation, RelationType, ReviewStatus
    used = _included("order", "productid", "product", "productid", 5, 5, 500)
    unused = _included("order", "productid", "other", "otherid", 5, 5, 900)
    key = _key("order", "orderid")
    relation = Relation(id="rel_x", type=RelationType.has, target="tbl_product",
                        via=[ColumnPair.model_validate({"from": "productid", "to": "productid"})],
                        status=ReviewStatus.proposed, confidence=0.8, evidence=[used.id], inferred_by="test")
    from mandara.om.model import Table as OmTable
    table = OmTable(id="tbl_order", name="s.order", source="src", context="s", relations=[relation],
                    status=ReviewStatus.proposed, confidence=0.7, evidence=[key.id], inferred_by="test")
    kept = joins.claims_for(table, [key, used, unused])
    assert kept == (key, used)


def test_inferred_relations_never_duplicate_declared_joins() -> None:
    """宣言された外部キーと同じ結合は、推定の関係にしない（AdventureWorks：宣言 90 本と推定を合わせて、結合は重ならない）。"""
    from mandara.app.pipeline import measure_joins
    from mandara.extractors import catalog as catalog_extractor
    from mandara.modeler.rules import propose
    from mandara.om.ontology import SELF
    catalog = PostgresCatalogReader(CONNINFO).read(Scope(SCHEMAS))
    claims = [*catalog_extractor.extract("adventureworks", catalog),
              *measure_joins("adventureworks", catalog, PostgresInclusionReader(CONNINFO), []).claims]
    ontology = propose("adventureworks", SCHEMAS, claims).ontology
    seen: list[tuple[str, tuple[tuple[str, str], ...], str]] = []
    for t in ontology.tables:
        for r in t.relations or []:
            target = t if r.target == SELF else ontology.table(r.target)
            assert target is not None
            seen.append((t.name, tuple(sorted((p.from_, p.to) for p in r.via or [])), target.name))
    assert len(seen) == len(set(seen))
    inferred = [r for t in ontology.tables for r in t.relations or [] if r.inferred_by == joins.MODELER]
    assert inferred and all(r.confidence is not None and r.confidence < 0.9 for r in inferred)  # 推定は宣言より低い確信度で、人のレビューに回る


def test_claims_refuse_a_range_that_does_not_match_the_column_family() -> None:
    """値の範囲は整数の列で値があるときだけ。読み手が食い違えば、主張にせず誤りにする。"""
    key_table = Table("s", "k", ObjectKind.table, None, (_column("id"),), Key("pk", ("id",)), (), (), ())
    key = joins_extractor.Key(key_table, key_table.columns[0])
    text = _column("code", TypeFamily.text, "text")
    candidate = joins_extractor.Candidate(Table("s", "t", ObjectKind.table, None, (text,), None, (), (), ()), text, (key,))
    with pytest.raises(ValueError, match="値の範囲の幅"):
        joins_extractor.claims("src", candidate, [key], Inclusion(3, 1, (3,), (5,), 4, (3,)))
    integer = _column("ref")
    candidate = joins_extractor.Candidate(Table("s", "t", ObjectKind.table, None, (integer,), None, (), (), ()), integer,
                                          (key,))
    with pytest.raises(ValueError, match="値の範囲の幅"):
        joins_extractor.claims("src", candidate, [key], Inclusion(3, 1, (3,), (5,), None, None))
    [claim] = joins_extractor.claims("src", candidate, [key], Inclusion(3, 1, (3,), (5,), 4, (3,)))
    assert (claim.value_span, claim.target_values_in_span) == (4, 3)
