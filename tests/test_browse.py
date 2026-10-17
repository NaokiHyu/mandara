"""ドメインビューワ（mandara.app.browse）の法則。ローカルの AdventureWorks を読む（正解 OM の表を承認済みにして使う）。

- ドメインは承認済みの domain / event の表だけ。ほかの表は理由つきで hidden に出る（どの表も、どちらかにちょうど1回）
- 検索の結果の行は、そのキーでインスタンスとして開ける。部分一致の % と _ は文字として扱う
- 参照先へのリンクを開くと、その参照先のインスタンスになる。子の集まり（1:n）の行は、開くとこのインスタンスを指し返す
- relation の表は、ドメインにも近隣の集まりにも出ない（n:m の向こう側だけが出る）
- 行を読むアダプタ：値はパラメータで渡す（識別子も値も SQL に埋め込まない）
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from mandara.app import browse
from mandara.connectors.postgres.record_reader import PostgresRecordReader
from mandara.om.model import LabelSource, RelatedKind, Table
from mandara.om.ontology import Ontology
from mandara.omstore.yaml_store import load_ontology
from mandara.source.records import Contains, Equals, Records

GOLD = Path(__file__).parent.parent / "evals/datasets/adventureworks/gold/ontology"
READER = PostgresRecordReader("dbname=adventureworks")


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
        browse.search(GOLD_OM, GOLD_OM.tables[0].id, READER, None)


@pytest.mark.parametrize("name", [d.name for d in browse.domain_index(OM, "gold", set()).domains])
def test_search_rows_open_as_instances_and_neighbors_lead_back(name: str) -> None:
    table = _table(name)
    rows = browse.search(OM, table.id, READER, None, 3)
    assert rows.rows, f"{name} に行が無い"
    first = rows.rows[0]
    it = browse.instance(OM, table.id, first.key, READER)
    assert it.key == first.key and it.label == first.label
    for link in it.links:
        target = OM.table(link.target_table)
        assert target is not None and browse.hidden_reason(target) is None
        if link.target is not None:
            back = browse.instance(OM, link.target_table, link.target.key, READER)
            assert back.key == link.target.key
    for group in it.related:
        group_table = OM.table(group.table)
        assert group_table is not None and group_table.kind != "relation"
        if group.kind == RelatedKind.children:
            child = browse.instance(OM, group.table, group.rows[0].key, READER)
            assert any(l.relation == group.relation and l.target is not None and l.target.key == it.key
                       for l in child.links), f"{group.name} の子が {name} を指し返さない"
        else:
            middle = OM.table(group.via_table or "")
            assert middle is not None and middle.kind == "relation"


def test_search_finds_by_text_and_by_key() -> None:
    product = _table("production.product")
    found = browse.search(OM, product.id, READER, "mountain", 50)
    assert found.rows and all("mountain" in r.label.lower() or any("mountain" in (c.value or "").lower()
                                                                     for c in r.cells) for r in found.rows)
    by_key = browse.search(OM, product.id, READER, "707", 50)
    assert ["707"] in [r.key for r in by_key.rows]


def test_instance_shows_children_and_hides_technical_columns() -> None:
    header = _table("sales.salesorderheader")
    it = browse.instance(OM, header.id, ["43659"], READER)
    assert "rowguid" not in [f.column for f in it.fields] and "modifieddate" not in [f.column for f in it.fields]
    children = [g for g in it.related if g.kind == RelatedKind.children and g.name == "sales.salesorderdetail"]
    assert children and len(children[0].rows) == 12 and not children[0].truncated  # 受注 43659 の明細は 12 行
    assert all("salesorderid" not in [c.column for c in r.cells] for r in children[0].rows)  # 自分を指す列は添えない


def test_unknown_rows_and_wrong_keys_are_errors() -> None:
    product = _table("production.product")
    with pytest.raises(browse.BrowseError, match="行が無い"):
        browse.instance(OM, product.id, ["999999"], READER)
    with pytest.raises(browse.BrowseError, match="キーの数"):
        browse.instance(OM, product.id, ["1", "2"], READER)


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
        browse.instance(OM, product.id, ["abc"], READER)


# ---------------------------------------------------------------- リンクの状態（偽の読み手で、壊れた参照を作る）


class DictReader:
    """表ごとの行のリストから読む偽物。Equals は値の文字列で比べる（偽物なので型は持たない）。無い表は空。"""

    def __init__(self, tables: Mapping[tuple[str, str], Sequence[Mapping[str, object]]]) -> None:
        self.tables = tables

    def find(self, schema: str, table: str, columns: Sequence[str], order_by: Sequence[str],
             where: Contains | Equals | None, limit: int) -> Records:
        rows = self.tables.get((schema, table), [])
        if isinstance(where, Equals):
            rows = [r for r in rows if all(str(r[c]) == str(v) for c, v in where.values.items())]
        picked = tuple({c: r[c] for c in columns} for r in rows)
        return Records(picked[:limit], truncated=len(picked) > limit)


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
        it = browse.instance(OM, customer.id, key, reader)
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
    for row in browse.search(OM, employee.id, READER, None, 5).rows:
        assert row.label == browse.instance(OM, person.id, row.key, READER).label  # 同じキーの人の見出し
        assert row.label_source == LabelSource.borrowed


def test_a_missing_parent_row_is_told_as_a_broken_borrow() -> None:
    """上位（人）に同じキーの行が無い従業員は、キーを見出しにして borrowed_missing と伝える（黙って借りたことにしない）。"""
    employee = _table("humanresources.employee")
    row = {c: None for c in (employee.columns or {})} | {"businessentityid": 7}
    reader = DictReader({("humanresources", "employee"): [row]})
    [found] = browse.search(OM, employee.id, reader, None, 5).rows
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
        browse.search(cyclic, employee.id, READER, None)


def test_via_rows_carry_the_attributes_of_each_link_row() -> None:
    """部品表（relation）を挟んだ先の製品には、部品表の行ごとの属性（期間、数量）が添う。参照の列と代理キーは添えない。"""
    product, bom = _table("production.product"), _table("production.billofmaterials")
    groups = [g for g in browse.instance(OM, product.id, ["3"], READER).related if g.kind == RelatedKind.via]
    assert groups and all(g.via_table == bom.id for g in groups)
    referencing = {p.from_ for r in bom.relations or [] for p in r.via or []}
    for g in groups:
        for row in g.rows:
            columns = [c.column for c in row.link_cells or []]
            assert "startdate" in columns and "perassemblyqty" in columns
            assert not set(columns) & (referencing | {"billofmaterialsid"})
    plain = browse.instance(OM, _table("person.person").id, ["1"], READER)
    assert all(r.link_cells is None for g in plain.related if g.kind == RelatedKind.children for r in g.rows)
