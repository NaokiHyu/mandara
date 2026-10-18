"""ドメインビューワ（mandara.app.browse）の法則。ローカルの AdventureWorks を読む（正解 OM の表を承認済みにして使う）。

- ドメインは承認済みの domain / event の表だけ。ほかの表は理由つきで hidden に出る（どの表も、どちらかにちょうど1回）
- 検索の結果の行は、そのキーでインスタンスとして開ける。部分一致の % と _ は文字として扱う
- 参照先へのリンクを開くと、その参照先のインスタンスになる。子の集まり（1:n）の行は、開くとこのインスタンスを指し返す
- relation の表は、ドメインにも近隣の集まりにも出ない（n:m の向こう側だけが出る。つながりの集計は links に控えめに添う）
- 近隣の集まり（1:n も n:m も）は、そのインスタンスで絞ったドメインの集合と同じ（八百屋 X の野菜 ⊆ 野菜）
- 行を読むアダプタ：値はパラメータで渡す（識別子も値も SQL に埋め込まない）
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import cast
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mandara.app import browse
from mandara.connectors.postgres.record_reader import PostgresRecordReader
from mandara.om.model import Additivity, GroupProfile, GroupView, LabelSource, RelatedKind, SemanticType, Table, UnitSource, ValueType
from mandara.om.ontology import Ontology
from mandara.omstore.yaml_store import load_ontology
from mandara.source.records import (Aggregated, All, Contains, Equals, Exists, Group, Measure, Op, Records, TimeBucket,
                                    TimeUnit, Where)

GOLD = Path(__file__).parent.parent / "evals/datasets/adventureworks/gold/ontology"
READER = PostgresRecordReader("dbname=adventureworks")
TODAY = date(2026, 10, 18)


def _approved(ontology: Ontology) -> Ontology:
    approved = {"status": "approved", "reviewed_by": "test", "reviewed_at": date(2026, 10, 18)}
    return replace(ontology, tables=tuple(t.model_copy(update=approved) for t in ontology.tables))


GOLD_OM = load_ontology(GOLD)
OM = _approved(GOLD_OM)


def _table(name: str) -> Table:
    return next(t for t in OM.tables if t.name == name)


def test_every_table_is_a_domain_or_hidden_with_a_reason() -> None:
    index = browse.domain_index(OM, "gold", {"adventureworks"})
    shown = [d.table for d in index.domains]
    hidden = [h.table for h in index.hidden]
    assert sorted(shown + hidden) == sorted(t.id for t in OM.tables) and not set(shown) & set(hidden)
    for d in index.domains:
        t = OM.table(d.table)
        assert t is not None and t.status == "approved" and t.kind in browse.DOMAIN_KINDS
    assert all(h.reason for h in index.hidden)
    assert index.readable and not browse.domain_index(OM, "gold", set()).readable


def test_unapproved_tables_are_not_domains() -> None:
    index = browse.domain_index(GOLD_OM, "gold", {"adventureworks"})
    assert index.domains == [] and all("承認されていない" in h.reason for h in index.hidden)
    with pytest.raises(browse.BrowseError, match="ドメインではない"):
        browse.collection(GOLD_OM, GOLD_OM.tables[0].id, READER, TODAY)


@pytest.mark.parametrize("name", [d.name for d in browse.domain_index(OM, "gold", set()).domains])
def test_search_rows_open_as_instances_and_neighbors_lead_back(name: str) -> None:
    table = _table(name)
    rows = browse.collection(OM, table.id, READER, TODAY, None, limit=3)
    assert rows.rows, f"{name} に行が無い"
    first = rows.rows[0]
    it = browse.instance(OM, table.id, first.key, READER, TODAY)
    assert it.key == first.key and it.label == first.label
    for link in it.links:
        target = OM.table(link.target_table)
        assert target is not None and browse.hidden_reason(target) is None
        if link.target is not None:
            back = browse.instance(OM, link.target_table, link.target.key, READER, TODAY)
            assert back.key == link.target.key
    for group in it.related:
        group_table = OM.table(group.table)
        assert group_table is not None and group_table.kind != "relation"
        if group.kind == RelatedKind.children:
            child = browse.instance(OM, group.table, group.rows[0].key, READER, TODAY)
            assert any(l.relation == group.relation and l.target is not None and l.target.key == it.key
                       for l in child.links), f"{group.name} の子が {name} を指し返さない"
        else:
            middle = OM.table(group.via_table or "")
            assert middle is not None and middle.kind == "relation"


def test_search_finds_by_text_and_by_key() -> None:
    product = _table("production.product")
    found = browse.collection(OM, product.id, READER, TODAY, "mountain", limit=50)
    assert found.rows and all("mountain" in r.label.lower() or any("mountain" in (c.value or "").lower()
                                                                     for c in r.cells) for r in found.rows)
    by_key = browse.collection(OM, product.id, READER, TODAY, "707", limit=50)
    assert ["707"] in [r.key for r in by_key.rows]


def test_instance_shows_children_and_hides_technical_columns() -> None:
    header = _table("sales.salesorderheader")
    it = browse.instance(OM, header.id, ["43659"], READER, TODAY)
    assert "rowguid" not in [f.column for f in it.fields] and "modifieddate" not in [f.column for f in it.fields]
    children = [g for g in it.related if g.kind == RelatedKind.children and g.name == "sales.salesorderdetail"]
    # 受注 43659 の明細は 12 行：一覧の上限（10）を超えるので集計を頭出しし、先頭の数行を添える
    assert children and children[0].count == 12 and children[0].view == GroupView.summary
    assert len(children[0].rows) == browse.RECENT and children[0].truncated
    measures = {(m.column, str(m.op)): (m.value, m.unit_text) for m in children[0].measures or []}
    assert measures[("orderqty", "sum")] == ("26", "each")  # 加法の尺度は合計、単価（非加法）は平均
    assert ("unitprice", "avg") in measures and ("unitprice", "sum") not in measures
    assert all("salesorderid" not in [c.column for c in r.cells] for r in children[0].rows)  # 自分を指す列は添えない


def test_unknown_rows_and_wrong_keys_are_errors() -> None:
    product = _table("production.product")
    with pytest.raises(browse.BrowseError, match="行が無い"):
        browse.instance(OM, product.id, ["999999"], READER, TODAY)
    with pytest.raises(browse.BrowseError, match="キーの数"):
        browse.instance(OM, product.id, ["1", "2"], READER, TODAY)


def test_record_reader_treats_wildcards_as_text() -> None:
    found = READER.find("production", "product", ["productid", "name"], ["productid"], Contains(("name",), "%"), 5)
    assert found.rows == ()  # 名前に % を含む製品は無い（% をワイルドカードにしない）
    found = READER.find("production", "product", ["productid"], ["productid"], Equals({"productid": "707"}), 5)
    assert [r["productid"] for r in found.rows] == [707] and not found.truncated
    page = READER.find("production", "product", ["productid"], ["productid"], None, 2)
    assert len(page.rows) == 2 and page.truncated


def test_display_formats_source_values() -> None:
    from datetime import datetime
    from decimal import Decimal

    assert browse.display(None, None) is None
    assert browse.display(Decimal("34.9900"), None) == "34.9900"
    assert browse.display(Decimal("4.0000"), None) == "4"
    assert browse.display(datetime(2022, 5, 30), None) == "2022-05-30"
    assert browse.display(b"\x00\x01", None) == "（2 バイトのデータ）"
    assert browse.display(True, None) == "true"


# ---------------------------------------------------------------- キーの往復（型ごと）

KEYED = [  # 主キーの型がいろいろな表（整数、日時を含む複合、文字列、10進数の値を含む表）
    ("production", "productcosthistory", ["productid", "startdate"]),
    ("humanresources", "employeepayhistory", ["businessentityid", "ratechangedate"]),
    ("sales", "currency", ["currencycode"]),
    ("sales", "salesorderdetail", ["salesorderid", "salesorderdetailid"]),
    ("production", "productinventory", ["productid", "locationid"]),
]


@pytest.mark.parametrize(("schema", "table", "key"), KEYED)
def test_key_text_round_trips_through_equals(schema: str, table: str, key: list[str]) -> None:
    """読んだ行のキーをキーの文字列にして一致で引くと、同じ行が1つだけ返る（表示の文字列とは別の表現）。"""
    from mandara.source.records import key_text

    rows = READER.find(schema, table, key, key, None, 20).rows
    assert rows
    for row in rows:
        text = {c: key_text(row[c]) for c in key}
        again = READER.find(schema, table, key, key, Equals(text), 2)
        assert [dict(r) for r in again.rows] == [dict(row)]


def test_keys_that_do_not_fit_the_column_type_are_errors() -> None:
    from mandara.source.records import InvalidKeyError

    product = _table("production.product")
    with pytest.raises(InvalidKeyError):
        READER.find("production", "product", ["productid"], ["productid"], Equals({"productid": "abc"}), 1)
    with pytest.raises(browse.BadKeyError, match="型として読めない"):
        browse.instance(OM, product.id, ["abc"], READER, TODAY)


# ---------------------------------------------------------------- リンクの状態（偽の読み手で、壊れた参照を作る）


class DictReader:
    """表ごとの行のリストから読む偽物。Equals は値の文字列で比べる（偽物なので型は持たない）。無い表は空。"""

    def __init__(self, tables: Mapping[tuple[str, str], Sequence[Mapping[str, object]]]) -> None:
        self.tables = tables

    def _rows(self, schema: str, table: str, where: Where | None) -> list[Mapping[str, object]]:
        rows = list(self.tables.get((schema, table), []))
        for part in (where.parts if isinstance(where, All) else (where,) if where is not None else ()):
            if isinstance(part, Equals):
                rows = [r for r in rows if all(str(r[c]) == str(v) for c, v in part.values.items())]
            elif isinstance(part, Exists):
                links = [x for x in self.tables.get((part.schema, part.table), [])
                         if all(str(x[c]) == str(v) for c, v in part.values.items())]
                rows = [r for r in rows if any(all(r[mine] is not None and str(x[theirs]) == str(r[mine])
                                                   for mine, theirs in part.on) for x in links)]
            else:
                rows = [r for r in rows if any(part.text.lower() in str(r[c]).lower() for c in part.columns
                                               if r[c] is not None)]
        return rows

    def find(self, schema: str, table: str, columns: Sequence[str], order_by: Sequence[str],
             where: Where | None, limit: int, descending: bool = False) -> Records:
        rows = self._rows(schema, table, where)
        for c in reversed(order_by):  # PostgreSQL と同じ：値の順（降順なら逆）、NULL は最後
            present = sorted((r for r in rows if r[c] is not None), key=lambda r: cast(Decimal, r[c]), reverse=descending)
            rows = present + [r for r in rows if r[c] is None]
        picked = tuple({c: r[c] for c in columns} for r in rows)
        return Records(picked[:limit], truncated=len(picked) > limit)

    def aggregate(self, schema: str, table: str, where: Where | None, by: str | TimeBucket | None,
                  measures: Sequence[Measure], limit: int, latest_first: bool = False) -> Aggregated:
        """SQL の集計と同じ意味（NULL は数えない、行が無ければ合計は None）の素朴な実装。"""
        rows = self._rows(schema, table, where)

        def key(r: Mapping[str, object]) -> object:
            if by is None:
                return None
            if isinstance(by, str):
                return r[by]
            v = r[by.column]
            assert isinstance(v, date)
            d = v.date() if isinstance(v, datetime) else v
            return d.replace(day=1) if by.unit == "month" else d.replace(month=1, day=1) if by.unit == "year" else d

        if isinstance(by, TimeBucket):
            rows = [r for r in rows if r[by.column] is not None]
        groups: dict[object, list[Mapping[str, object]]] = {}
        for r in rows:
            groups.setdefault(key(r), []).append(r)
        if by is None:
            groups = {None: rows}

        def value(m: Measure, members: list[Mapping[str, object]]) -> object:
            if m.column is None:
                return len(members)
            xs = [r[m.column] for r in members if r[m.column] is not None]
            if not xs:
                return None
            if m.op == "count":
                return len(xs)
            if m.op == "sum":
                return sum(cast(list[Decimal], xs), Decimal(0))
            if m.op == "avg":
                return sum(cast(list[Decimal], xs), Decimal(0)) / len(xs)
            return min(cast(list[Decimal], xs)) if m.op == "min" else max(cast(list[Decimal], xs))

        out = [Group(k, tuple(value(m, ms) for m in measures)) for k, ms in groups.items()]
        if isinstance(by, TimeBucket):
            out.sort(key=lambda g: cast(date, g.key), reverse=latest_first)
        elif by is not None:
            out.sort(key=lambda g: (-len(groups[g.key]), g.key is None, cast(Decimal, g.key) if g.key is not None
                                    else Decimal(0)))
        return Aggregated(tuple(out[:limit]), truncated=len(out) > limit)


def test_links_tell_null_from_missing_targets() -> None:
    from mandara.om.model import LinkState

    customer = _table("sales.customer")
    columns = list((customer.columns or {}).keys())
    store_rel = next(r for r in customer.relations or [] if OM.table(r.target) is not None
                     and OM.table(r.target).name == "sales.store")  # pyright: ignore[reportOptionalMemberAccess]
    store_column = (store_rel.via or [])[0].from_
    base = {c: None for c in columns}
    rows = [base | {"customerid": 1, store_column: None}, base | {"customerid": 2, store_column: 999}]
    store = OM.table(store_rel.target)
    assert store is not None
    reader = DictReader({("sales", "customer"): rows,
                         ("sales", "store"): [{c: None for c in (store.columns or {})} | {"businessentityid": 1}]})
    for key, state in ((["1"], LinkState.null), (["2"], LinkState.missing)):
        it = browse.instance(OM, customer.id, key, reader, TODAY)
        link = next(l for l in it.links if l.relation == store_rel.id)
        assert link.state == state and link.target is None



# ---------------------------------------------------------------- 見出しの借用、つながりの属性


def test_tables_without_label_columns_borrow_the_label_of_their_is_a_parent() -> None:
    employee, person = _table("humanresources.employee"), _table("person.person")
    assert browse.label_columns(OM, employee) == []
    parent = browse.label_parent(OM, employee)
    assert parent is not None and parent[1].id == person.id
    index = {d.table: d for d in browse.domain_index(OM, "gold", {"adventureworks"}).domains}
    assert index[employee.id].label_from == person.id and index[person.id].label_from is None
    for row in browse.collection(OM, employee.id, READER, TODAY, None, limit=5).rows:
        assert row.label == browse.instance(OM, person.id, row.key, READER, TODAY).label  # 同じキーの人の見出し
        assert row.label_source == LabelSource.borrowed


def test_a_missing_parent_row_is_told_as_a_broken_borrow() -> None:
    """上位（人）に同じキーの行が無い従業員は、キーを見出しにして borrowed_missing と伝える（黙って借りたことにしない）。"""
    employee = _table("humanresources.employee")
    row = {c: None for c in (employee.columns or {})} | {"businessentityid": 7}
    reader = DictReader({("humanresources", "employee"): [row]})
    [found] = browse.collection(OM, employee.id, reader, TODAY, None, limit=5).rows
    assert (found.label, found.label_source) == ("7", LabelSource.borrowed_missing)


def test_an_ontology_that_fails_validation_is_not_browsed() -> None:
    """is-a の循環（検証の CYCLE）がある OM は見せない。見出しの借用が終わらなくなるため。"""
    employee, person = _table("humanresources.employee"), _table("person.person")
    back = next(r for r in employee.relations or [] if r.type == "is-a").model_copy(
        update={"id": "rel_cycle", "target": employee.id})
    cyclic = replace(OM, tables=tuple(t.model_copy(update={"relations": [*(t.relations or []), back]})
                                      if t.id == person.id else t for t in OM.tables))
    with pytest.raises(browse.InvalidOntologyError, match="検証を通らない"):
        browse.domain_index(cyclic, "gold", {"adventureworks"})
    with pytest.raises(browse.InvalidOntologyError):
        browse.collection(cyclic, employee.id, READER, TODAY)


def test_via_rows_carry_the_attributes_of_each_link_row() -> None:
    """部品表（relation）を挟んだ先の製品には、部品表の行ごとの属性（期間、数量）が添う。参照の列と代理キーは添えない。"""
    product, bom = _table("production.product"), _table("production.billofmaterials")
    groups = [g for g in browse.instance(OM, product.id, ["3"], READER, TODAY).related if g.kind == RelatedKind.via]
    assert groups and all(g.via_table == bom.id for g in groups)
    referencing = {p.from_ for r in bom.relations or [] for p in r.via or []}
    for g in groups:
        assert g.links is not None and all(r.link_cells is None for r in g.rows)  # 先の行そのものには添えない
        for row in g.links.rows:
            columns = [c.column for c in row.link_cells or []]
            assert "startdate" in columns and "perassemblyqty" in columns
            assert not set(columns) & (referencing | {"billofmaterialsid"})
    plain = browse.instance(OM, _table("person.person").id, ["1"], READER, TODAY)
    assert all(r.link_cells is None for g in plain.related if g.kind == RelatedKind.children for r in g.rows)


# ---------------------------------------------------------------- 近隣の集まりの見せ方（純粋な規則）

@given(st.integers(0, 200), st.integers(0, 200), st.booleans(), st.booleans())
def test_group_view_is_monotone_in_the_count(a: int, b: int, has_time: bool, has_period: bool) -> None:
    """件数を増やしても「詳しく並べる」側へは戻らない（一覧 → 集計・帯 → 時系列の向きにだけ変わる）。時刻の列が無ければ時系列に
    ならず、期間の列が無ければ帯にならない。"""
    rank = {GroupView.list: 0, GroupView.timeline: 1, GroupView.summary: 1, GroupView.time_series: 2}
    small, large = sorted((a, b))
    views = [browse.group_view(n, has_time, has_period) for n in (small, large)]
    assert rank[views[0]] <= rank[views[1]]
    assert all(v != GroupView.time_series or has_time for v in views)
    assert all(v != GroupView.timeline or has_period for v in views)
    assert browse.group_view(0, has_time, has_period) in (GroupView.list, GroupView.timeline)


@given(st.dates(), st.integers(0, 5000), st.integers(0, 5000))
def test_bucket_unit_grows_with_the_span(first: date, a: int, b: int) -> None:
    from datetime import timedelta

    order = ["day", "month", "year"]
    short, long = sorted((a, b))
    try:
        units = [browse.bucket_unit(first, first + timedelta(days=d)) for d in (short, long)]
    except OverflowError:
        return
    assert order.index(units[0]) <= order.index(units[1])


def test_measure_plan_follows_additivity() -> None:
    """加法は合計、非加法は平均。時間の軸がある集まりの半加法は合計せず、最新の日だけ合計する。"""
    plan = browse.measure_plan(OM, _table("sales.salesorderdetail"))
    ops = dict(plan.sums)
    assert ops["orderqty"] == "sum" and ops["unitprice"] == "avg" and plan.snapshot == []


# ---------------------------------------------------------------- 集計の法則（PostgreSQL と偽物に同じ法則）

SAMPLES = [("sales", "salesorderdetail", {"salesorderid": "43659"}, "orderqty", "modifieddate"),
           ("production", "transactionhistory", {"productid": "707"}, "quantity", "transactiondate")]


def _readers(schema: str, table: str, where: dict[str, str], columns: list[str]) -> list[object]:
    rows = READER.find(schema, table, columns, columns[:1], Equals(where), 100000).rows
    return [READER, DictReader({(schema, table): [dict(r) for r in rows]})]


@pytest.mark.parametrize(("schema", "table", "where", "measure", "time"), SAMPLES)
def test_aggregate_laws(schema: str, table: str, where: dict[str, str], measure: str, time: str) -> None:
    columns = [*where, measure, time]
    for reader in _readers(schema, table, where, columns):
        r = cast(DictReader, reader)
        rows = r.find(schema, table, columns, [time], Equals(where), 100000).rows
        [whole] = r.aggregate(schema, table, Equals(where), None, [Measure("count", None), Measure("sum", measure),
                                                                   Measure("min", time), Measure("max", time)], 1).groups
        assert whole.values[0] == len(rows)
        assert whole.values[1] == sum(cast(int, x[measure]) for x in rows if x[measure] is not None)
        assert whole.values[2] == min(cast(datetime, x[time]) for x in rows)
        by_value = r.aggregate(schema, table, Equals(where), measure, [Measure("count", None)], 1000)
        assert sum(cast(int, g.values[0]) for g in by_value.groups) == len(rows)
        counts = [cast(int, g.values[0]) for g in by_value.groups]
        assert counts == sorted(counts, reverse=True)  # 値ごとは件数の多い順
        for unit in ("day", "month", "year"):
            buckets = r.aggregate(schema, table, Equals(where), TimeBucket(time, unit), [Measure("count", None)], 100000)
            keys = [cast(date, g.key) for g in buckets.groups]
            assert keys == sorted(keys) and sum(cast(int, g.values[0]) for g in buckets.groups) == len(rows)
            latest = r.aggregate(schema, table, Equals(where), TimeBucket(time, unit), [Measure("count", None)], 1,
                                 latest_first=True)
            assert latest.groups[0].key == keys[-1] and latest.truncated == (len(keys) > 1)
        newest = r.find(schema, table, columns, [time], Equals(where), 3, descending=True).rows
        assert [x[time] for x in newest] == sorted((x[time] for x in rows), reverse=True)[:3]  # pyright: ignore


def test_postgres_and_the_fake_agree() -> None:
    """テストの偽物（DictReader）が、PostgreSQL と同じ集計を返す（偽物でのテストが本物の意味と食い違わない）。"""
    schema, table, where, measure, time = SAMPLES[1]
    columns = [*where, measure, time]
    real, fake = _readers(schema, table, where, columns)
    for by in (None, measure, TimeBucket(time, "month")):
        ms = [Measure("count", None), Measure("sum", measure)]
        a = cast(DictReader, real).aggregate(schema, table, Equals(where), by, ms, 1000)
        b = cast(DictReader, fake).aggregate(schema, table, Equals(where), by, ms, 1000)
        assert a == b


# ---------------------------------------------------------------- 見せ方の通し（実データと偽物）


def test_period_links_are_shown_as_a_timeline() -> None:
    """部品表（relation。startdate / enddate を持つ）のつながりは期間の帯。期間の列は relation の行の値として添う。"""
    product = _table("production.product")
    groups = [g for g in browse.instance(OM, product.id, ["3"], READER, TODAY).related if g.kind == RelatedKind.via]
    links = [g.links for g in groups if g.links is not None]
    assert groups and len(links) == len(groups)
    assert all(l.view == GroupView.timeline and (l.period_from, l.period_to) == ("startdate", "enddate") for l in links)
    assert all("startdate" in [c.column for c in r.link_cells or []] for l in links for r in l.rows)


def _orders(customer: int, days: list[date]) -> list[dict[str, object]]:
    header = _table("sales.salesorderheader")
    base: dict[str, object] = {c: None for c in (header.columns or {})}
    return [base | {"salesorderid": 1000 + i, "customerid": customer, "orderdate": datetime.combine(d, datetime.min.time()),
                    "subtotal": Decimal("10.00"), "taxamt": Decimal("1.00"), "freight": Decimal("0.50"),
                    "currencyrateid": None, "status": 5}
            for i, d in enumerate(days)]


def test_many_events_are_shown_as_a_time_series_with_the_latest_day() -> None:
    from datetime import timedelta

    customer = _table("sales.customer")
    days = [date(2024, 1, 1) + timedelta(days=9 * i) for i in range(40)]  # 1 年ほどに 40 件 → 月ごと
    orders = _orders(7, days)
    reader = DictReader({("sales", "customer"): [{c: None for c in (customer.columns or {})} | {"customerid": 7}],
                         ("sales", "salesorderheader"): orders})
    for today, is_today in ((days[-1], True), (TODAY, False)):
        it = browse.instance(OM, customer.id, ["7"], reader, today)
        [g] = [g for g in it.related if g.name == "sales.salesorderheader"]
        assert (g.view, g.count, g.time_column, g.series_unit) == (GroupView.time_series, 40, "orderdate", "month")
        assert sum(p.count for p in g.series or []) == 40 and g.series_measures == ["subtotal", "taxamt"]
        assert g.latest is not None and (g.latest.day, g.latest.is_today, g.latest.count) == (days[-1], is_today, 1)
        assert [r.key for r in g.rows] == [[str(1039 - i)] for i in range(browse.RECENT)]  # 新しい順
        assert {(m.column, m.value) for m in g.measures or []} >= {("subtotal", "400")}


def test_semi_additive_measures_are_summed_only_at_the_latest_instant() -> None:
    """在庫のような半加法の尺度は、集まり全体では合計せず、時刻の最大の値の行だけで合計する。"""
    header = _table("sales.salesorderheader")
    semi = OM.vocabulary.model_copy(update={"semantic_types": {
        **(OM.vocabulary.semantic_types or {}),
        "balance": SemanticType(name="balance", type=ValueType.money, description="残高", additivity=Additivity.semi_additive,
                                requires_unit=True)}})
    columns = dict(header.columns or {})
    columns["freight"] = columns["freight"].model_copy(update={"semantic_type": "balance"})
    om = replace(OM, vocabulary=semi, tables=tuple(t.model_copy(update={"columns": columns}) if t.id == header.id else t
                                                   for t in OM.tables))
    plan = browse.measure_plan(om, header.model_copy(update={"columns": columns}))
    assert "freight" in plan.snapshot and "freight" not in dict(plan.sums)
    from datetime import timedelta
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(40)] + [date(2024, 2, 9)]  # 最後の日に 2 行
    customer = _table("sales.customer")
    reader = DictReader({("sales", "customer"): [{c: None for c in (customer.columns or {})} | {"customerid": 7}],
                         ("sales", "salesorderheader"): _orders(7, days)})
    [g] = [g for g in browse.instance(om, customer.id, ["7"], reader, TODAY).related if g.name == "sales.salesorderheader"]
    assert g.latest is not None and g.latest.count == 2
    assert [(m.column, m.value) for m in g.latest.snapshot or []] == [("freight", "1")]  # 最新の時点の 2 行 × 0.50
    assert "freight" not in [m.column for m in g.measures or []]


def test_empty_buckets_are_filled_so_the_time_axis_is_not_squeezed() -> None:
    customer = _table("sales.customer")
    days = [date(2024, 1, 15)] * 20 + [date(2024, 4, 15)] * 20  # 2 月と 3 月は空
    reader = DictReader({("sales", "customer"): [{c: None for c in (customer.columns or {})} | {"customerid": 7}],
                         ("sales", "salesorderheader"): _orders(7, days)})
    [g] = [g for g in browse.instance(OM, customer.id, ["7"], reader, TODAY).related if g.name == "sales.salesorderheader"]
    assert g.series_unit == "month"
    assert [(p.start, p.count) for p in g.series or []] == [(date(2024, 1, 1), 20), (date(2024, 2, 1), 0),
                                                            (date(2024, 3, 1), 0), (date(2024, 4, 1), 20)]
    assert all(s.value is None for p in (g.series or [])[1:3] for s in p.sums)  # 0 ではなく「値無し」


def test_mixed_units_are_not_summed() -> None:
    """単位の列の値が集まりの中で混ざる尺度は合計しない（理由つき）。"""
    detail = _table("sales.salesorderdetail")
    columns = dict(detail.columns or {})
    unit_column = "carriertrackingnumber"
    columns["orderqty"] = columns["orderqty"].model_copy(update={"unit": UnitSource(column=unit_column)})
    t = detail.model_copy(update={"columns": columns})
    om = replace(OM, tables=tuple(t if x.id == detail.id else x for x in OM.tables))
    base: dict[str, object] = {c: None for c in columns}
    rows = [base | {"salesorderid": 1, "salesorderdetailid": i, "orderqty": 1, unit_column: "A" if i % 2 else "B"}
            for i in range(20)]
    header = _table("sales.salesorderheader")
    reader = DictReader({("sales", "salesorderheader"): [{c: None for c in (header.columns or {})} | {"salesorderid": 1}],
                         ("sales", "salesorderdetail"): rows})
    [g] = [g for g in browse.instance(om, header.id, ["1"], reader, TODAY).related if g.name == "sales.salesorderdetail"]
    assert "orderqty" not in [m.column for m in g.measures or []]
    assert any(s.column == "orderqty" and "単位が混ざる" in s.reason for s in g.skipped_measures or [])


def test_measures_outside_the_closed_set_are_refused_at_the_port() -> None:
    with pytest.raises(ValueError, match="集計の種類"):
        Measure(cast(Op, "drop table"), "x")
    with pytest.raises(ValueError, match="時間の区切り"):
        TimeBucket("x", cast(TimeUnit, "week"))


def test_empty_groups_aggregate_alike() -> None:
    """行が無い集まり：件数 0、合計は値無し（PostgreSQL と偽物で同じ）。"""
    where = Equals({"productid": "-1"})
    for reader in (READER, DictReader({})):
        [g] = cast(DictReader, reader).aggregate("production", "transactionhistory", where, None,
                                                 [Measure("count", None), Measure("sum", "quantity")], 1).groups
        assert g.values == (0, None)


# ---------------------------------------------------------------- 集合と、インスタンスの中の集まりは同じもの（部分集合）


@pytest.mark.parametrize(("name", "key"), [("sales.salesorderheader", ["43659"]), ("production.product", ["707"]),
                                           ("sales.customer", ["29825"])])
def test_a_group_in_an_instance_is_the_collection_within_that_instance(name: str, key: list[str]) -> None:
    """インスタンスの 1:n の集まりと、そのインスタンスで絞ったドメインの集合は、件数・見せ方・集計が同じで、
    行はどれも親を指し、全体の集合より大きくならない（八百屋 X の野菜 ⊆ 野菜）。"""
    parent = _table(name)
    it = browse.instance(OM, parent.id, key, READER, TODAY)
    groups = [g for g in it.related if g.kind == RelatedKind.children]
    assert groups
    for g in groups:
        within = browse.WithinChild(parent.id, tuple(it.key), g.relation)
        part = browse.collection(OM, g.table, READER, TODAY, within=within, limit=50)
        fields = set(GroupProfile.model_fields)
        assert part.model_dump(include=fields) == g.model_dump(include=fields)  # 見せ方と集計のすべての項目が同じ
        assert part.rows[:len(g.rows)] == g.rows  # 集まりの行は、集合の先頭の行（同じ並び、同じ添える列）
        assert part.within is not None and (part.within.label, part.within.key) == (it.label, it.key)
        whole = browse.collection(OM, g.table, READER, TODAY, limit=1)
        assert part.count <= whole.count
        for row in part.rows[:3]:
            child = browse.instance(OM, g.table, row.key, READER, TODAY)
            assert any(l.relation == g.relation and l.target is not None and l.target.key == it.key
                       for l in child.links)


@pytest.mark.parametrize(("name", "key"), [("production.product", ["3"]), ("production.product", ["971"]),
                                           ("production.product", ["948"])])  # 948 は同じ組立品に2回つながる部品
def test_a_via_group_in_an_instance_is_the_collection_within_that_instance(name: str, key: list[str]) -> None:
    """n:m の集まりも、同じつながりで絞ったドメインの集合と同じ（見せ方・集計・行・つながりの集計）。集まりの行は先の行で
    重複せず、つながりの行が指す先の行と同じ集合（打ち切っていなければ）。"""
    parent = _table(name)
    it = browse.instance(OM, parent.id, key, READER, TODAY)
    groups = [g for g in it.related if g.kind == RelatedKind.via]
    assert groups
    fields = set(GroupProfile.model_fields)
    for g in groups:
        assert g.links is not None and g.via_relation is not None
        within = browse.WithinVia(parent.id, tuple(it.key), g.relation, g.via_relation)
        part = browse.collection(OM, g.table, READER, TODAY, within=within, limit=50)
        assert part.model_dump(include=fields) == g.model_dump(include=fields)
        assert part.rows[:len(g.rows)] == g.rows
        assert part.links == g.links
        assert part.within is not None and part.within.via_relation == g.via_relation
        keys = [tuple(r.key) for r in part.rows]
        assert len(keys) == len(set(keys))
        if not g.links.truncated:  # つながりの行は、先の行・壊れた参照・先が NULL のどれかにちょうど1回数える
            assert len(g.links.rows) + g.links.missing + g.links.unlinked == g.links.count
        if not part.truncated and not g.links.truncated:
            assert set(keys) == {tuple(r.key) for r in g.links.rows}
            assert part.count == len(set(keys))


_INVENTORY = READER.find("production", "productinventory", ["productid", "locationid", "quantity"], ["productid", "locationid"],
                         None, 100000).rows
_LOCATIONS = READER.find("production", "location", ["locationid", "name", "costrate", "modifieddate"], ["locationid"],
                         None, 1000).rows
_PRODUCTS = READER.find("production", "product", ["productid", "color", "sellstartdate"], ["productid"], None, 100000).rows
_SEMIJOIN_READERS = [READER, DictReader({("production", "productinventory"): [dict(r) for r in _INVENTORY],
                                         ("production", "location"): [dict(r) for r in _LOCATIONS],
                                         ("production", "product"): [dict(r) for r in _PRODUCTS]})]


@settings(max_examples=25, deadline=None)
@given(st.sampled_from(sorted({str(r["productid"]) for r in _INVENTORY}) + ["999999"]),
       st.sampled_from(["", "a", "Stor", "zzz"]))
def test_exists_is_a_semi_join_alike_in_postgres_and_the_fake(product: str, text: str) -> None:
    """Exists の法則（PostgreSQL と偽物に同じ法則、同じ結果）：
    - 準結合：Exists で絞った行のキー = つながる表の、値に一致する行が指すキー（重複しない）
    - 件数：aggregate の件数 = find の行の数（Exists を検索と All で組んでも）。値ごと・時間の区切りごとの件数の和も同じ"""
    where = Exists("production", "productinventory", (("locationid", "locationid"),), {"productid": product})
    narrowed: Where = where if text == "" else All((where, Contains(("name",), text)))
    expected = sorted({cast(int, r["locationid"]) for r in _INVENTORY if str(r["productid"]) == product})
    results: list[object] = []
    for reader in _SEMIJOIN_READERS:
        r = cast(DictReader, reader)
        plain = r.find("production", "location", ["locationid"], ["locationid"], where, 1000)
        assert [x["locationid"] for x in plain.rows] == expected
        rows = r.find("production", "location", ["locationid", "name"], ["locationid"], narrowed, 1000).rows
        [whole] = r.aggregate("production", "location", narrowed, None, [Measure("count", None)], 1).groups
        assert whole.values[0] == len(rows)
        by_value = r.aggregate("production", "location", narrowed, "costrate", [Measure("count", None)], 1000).groups
        by_month = r.aggregate("production", "location", narrowed, TimeBucket("modifieddate", "month"),
                               [Measure("count", None)], 1000).groups
        assert sum(cast(int, g.values[0]) for g in by_value) == sum(cast(int, g.values[0]) for g in by_month) == len(rows)
        results.append((rows, whole, by_value, by_month))
    assert results[0] == results[1]


@settings(max_examples=10, deadline=None)
@given(st.sampled_from(sorted({str(r["color"]) for r in _PRODUCTS if r["color"] is not None})))
def test_exists_on_the_same_table_does_not_confuse_the_outer_and_inner_rows(color: str) -> None:
    """外と内が同じ表の Exists（自分自身とキーでつながる、色の一致する行がある）は、Equals で絞ったのと同じ行。"""
    same = Exists("production", "product", (("productid", "productid"),), {"color": color})
    for reader in _SEMIJOIN_READERS:
        r = cast(DictReader, reader)
        a = r.find("production", "product", ["productid"], ["productid"], same, 1000)
        b = r.find("production", "product", ["productid"], ["productid"], Equals({"color": color}), 1000)
        assert a == b and a.rows
    with pytest.raises(ValueError, match="NULL"):
        Exists("s", "t", (("a", "b"),), {"c": None})


def test_search_narrows_rows_but_not_the_profile() -> None:
    """検索は行だけを絞る。集合の件数と集計は検索の前の集合について。"""
    header = _table("sales.salesorderheader")
    detail = _table("sales.salesorderdetail")
    rel = next(r for r in detail.relations or [] if r.target == header.id)
    within = browse.WithinChild(header.id, ("43659",), rel.id)
    every = browse.collection(OM, detail.id, READER, TODAY, within=within)
    found = browse.collection(OM, detail.id, READER, TODAY, "776", within=within)
    assert found.count == every.count == 12 and 0 < len(found.rows) < len(every.rows)


def test_wrong_within_is_an_error() -> None:
    header, detail, product = _table("sales.salesorderheader"), _table("sales.salesorderdetail"), _table("production.product")
    rel = next(r for r in detail.relations or [] if r.target == header.id)
    with pytest.raises(browse.NotFoundError, match="関係"):
        browse.collection(OM, detail.id, READER, TODAY, within=browse.WithinChild(product.id, ("707",), rel.id))
    with pytest.raises(browse.NotFoundError, match="行が無い"):
        browse.collection(OM, detail.id, READER, TODAY, within=browse.WithinChild(header.id, ("1",), rel.id))
    with pytest.raises(browse.BadKeyError):
        browse.collection(OM, detail.id, READER, TODAY, within=browse.WithinChild(header.id, ("x",), rel.id))


def test_null_is_not_an_equality_value_and_a_null_parent_has_no_children() -> None:
    with pytest.raises(ValueError, match="NULL"):
        Equals({"a": None})
    header, detail = _table("sales.salesorderheader"), _table("sales.salesorderdetail")
    rel = next(r for r in detail.relations or [] if r.target == header.id)
    # 親の参照される列（salesorderid）が NULL の行：つながる子は無い（空の部分集合として明示）
    base: dict[str, object] = {c: None for c in (header.columns or {})}
    nulled = replace(OM, tables=tuple(
        t.model_copy(update={"identity": t.identity.model_copy(update={"primary": ["rowguid"]})})
        if t.id == header.id and t.identity is not None else t for t in OM.tables))
    reader = DictReader({("sales", "salesorderheader"): [base | {"salesorderid": None, "rowguid": "g"}]})
    part = browse.collection(nulled, detail.id, reader, TODAY,
                             within=browse.WithinChild(header.id, ("g",), rel.id))
    assert (part.count, part.view, part.rows) == (0, GroupView.list, [])


def test_parent_key_count_must_match() -> None:
    header, detail = _table("sales.salesorderheader"), _table("sales.salesorderdetail")
    rel = next(r for r in detail.relations or [] if r.target == header.id)
    with pytest.raises(browse.BadKeyError, match="キーの数"):
        browse.collection(OM, detail.id, READER, TODAY, within=browse.WithinChild(header.id, ("1", "2"), rel.id))


def test_the_memo_reads_different_conditions_separately() -> None:
    calls: list[object] = []

    class Counting(DictReader):
        def aggregate(self, schema: str, table: str, where: Where | None, by: str | TimeBucket | None,
                      measures: Sequence[Measure], limit: int, latest_first: bool = False) -> Aggregated:
            calls.append(where)
            return super().aggregate(schema, table, where, by, measures, limit, latest_first)

    memo = browse._Memo(Counting({("s", "t"): [{"a": 1}, {"a": 2}]}))  # pyright: ignore[reportPrivateUsage]
    count = [Measure("count", None)]
    one = memo.aggregate("s", "t", Equals({"a": 1}), None, count, 1)
    again = memo.aggregate("s", "t", Equals({"a": 1}), None, count, 1)
    two = memo.aggregate("s", "t", Equals({"a": 2}), None, count, 1)
    every = memo.aggregate("s", "t", None, None, count, 1)
    assert len(calls) == 3 and one == again and one.groups[0].values == (1,) and two.groups[0].values == (1,)
    assert every.groups[0].values == (2,)
    linked = Exists("s", "u", (("a", "b"),), {"c": 1})
    memo.aggregate("s", "t", linked, None, count, 1)
    memo.aggregate("s", "t", Exists("s", "u", (("a", "b"),), {"c": 2}), None, count, 1)
    memo.aggregate("s", "t", linked, None, count, 1)
    assert len(calls) == 5  # 同じ Exists の2回目は読まない。値が違えば別に読む
