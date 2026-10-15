"""レビューの法則。

- 当てた結果は、判断の項目（と未決だった表の種類）以外を変えない。検証も通ったまま
- 判断済みは当て直せない。未決の表の承認には種類が要る
- 問いは、未判断の注釈ごとにちょうど1つ。表が先
- 書く側の境界（write_reviewed_ontology）は、判断以外の変更を拒否する
- コマンド：一括承認、対話での答え、レビュー後の再実行の拒否（AdventureWorks で通しに）
"""

import io
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from mandara.cli import main
from mandara.om.invariants import check
from mandara.om.model import AliasEntry, Column, Edge, EnumValue, Relation, ReviewStatus, Table, TableKind
from mandara.om.ontology import Ontology
from mandara.omstore.yaml_store import OntologyWriteRefusedError, load_ontology, write_ontology, write_reviewed_ontology
from mandara.review.decisions import Approve, Reject, ReviewError, apply, questions, review_changes

from tests.strategies import ontologies

TODAY = date(2026, 10, 15)


def _pending(ontology: Ontology) -> list[str]:
    return [w for w, a in ontology.annotations() if a.status == ReviewStatus.proposed.value]


@given(ontologies(), st.data())
def test_review_changes_only_decisions(ontology: Ontology, data: st.DataObject) -> None:
    pending = _pending(ontology)
    assume(pending)
    location = data.draw(st.sampled_from(pending))
    target = dict(ontology.annotations())[location]
    needs_kind = isinstance(target, Table) and target.kind is None
    decision = data.draw(st.sampled_from(
        [Reject(), Approve(kind=TableKind.domain) if needs_kind else Approve()]))
    try:
        after = apply(ontology, location, decision, "alice", TODAY)
    except ReviewError as e:  # 答えで検証が通らなくなるときだけ拒否される
        assert "検証を通らない" in str(e)
        return

    assert review_changes(ontology, after) == []
    decided = dict(after.annotations())[location]
    assert decided.status == (ReviewStatus.rejected if isinstance(decision, Reject) else ReviewStatus.approved).value
    assert (decided.reviewed_by, decided.reviewed_at) == ("alice", TODAY)
    assert _pending(after) == [w for w in pending if w != location]
    assert set(check(after)) <= set(check(ontology))  # 答えは違反を増やさない


@given(ontologies())
def test_one_question_per_pending_annotation_tables_first(ontology: Ontology) -> None:
    qs = questions(ontology)
    assert sorted(q.location for q in qs) == sorted(_pending(ontology))
    rank = {Table: 0, Relation: 1, Edge: 1, Column: 2, EnumValue: 3}
    by_place = dict(ontology.annotations())
    ranks = [rank[type(by_place[q.location])] for q in qs]
    assert ranks == sorted(ranks)  # 表 → 関係・辺 → 列 → enum の値
    tables = [ontology.table(q.location) for q in qs if isinstance(by_place[q.location], Table)]
    counts = [len(t.relations or []) for t in tables if t is not None]
    assert counts == sorted(counts, reverse=True)  # 表は関係の多い順


@given(ontologies(), st.data())
def test_decided_cannot_be_decided_again(ontology: Ontology, data: st.DataObject) -> None:
    decided = [w for w, a in ontology.annotations() if a.status != ReviewStatus.proposed.value]
    assume(decided)
    with pytest.raises(ReviewError, match="判断済み"):
        apply(ontology, data.draw(st.sampled_from(decided)), Reject(), "alice", TODAY)


def _undecided_table(ontology: Ontology) -> str | None:
    return next((t.id for t in ontology.tables if t.kind is None and t.status == "proposed"), None)


@given(ontologies())
def test_undecided_table_needs_a_kind(ontology: Ontology) -> None:
    location = _undecided_table(ontology)
    assume(location is not None)
    assert location is not None
    with pytest.raises(ReviewError, match="種類を選ぶ"):
        apply(ontology, location, Approve(), "alice", TODAY)
    after = apply(ontology, location, Approve(kind=TableKind.relation), "alice", TODAY)
    table = after.table(location)
    assert table is not None and table.kind == TableKind.relation.value
    assert review_changes(ontology, after) == []


def _with_table(o: Ontology, i: int, **update: object) -> Ontology:
    return replace(o, tables=(*o.tables[:i], o.tables[i].model_copy(update=update), *o.tables[i + 1:]))


def _first_column(o: Ontology) -> tuple[int, str] | None:
    return next(((i, n) for i, t in enumerate(o.tables) for n in (t.columns or {})), None)


Tamper = Callable[[Ontology], Ontology | None]
"""判断以外を変える。当てられない OM なら None。"""

TAMPERS: dict[str, Tamper] = {
    "table field": lambda o: _with_table(o, 0, grain=(o.tables[0].grain or "") + "x"),
    "column field": lambda o: None if (c := _first_column(o)) is None else _with_table(o, c[0], columns={
        **(o.tables[c[0]].columns or {}), c[1]: (o.tables[c[0]].columns or {})[c[1]].model_copy(update={"pii": True})}),
    "column removed": lambda o: None if (c := _first_column(o)) is None else _with_table(o, c[0], columns={
        n: col for n, col in (o.tables[c[0]].columns or {}).items() if n != c[1]} or None),
    "columns reordered": lambda o: next((_with_table(o, i, columns=dict(reversed(list((t.columns or {}).items()))))
                                         for i, t in enumerate(o.tables) if len(t.columns or {}) > 1), None),
    "tables reordered": lambda o: replace(o, tables=tuple(reversed(o.tables))) if len(o.tables) > 1 else None,
    "alias added": lambda o: replace(o, vocabulary=o.vocabulary.model_copy(update={"aliases": {
        **(o.vocabulary.aliases or {}), "zz": AliasEntry(name="zz", terms=["x"])}})),
    "kind without approval": lambda o: next((_with_table(o, i, kind="domain") for i, t in enumerate(o.tables)
                                             if t.kind is None), None),
    "reviewed without deciding": lambda o: next((_with_table(o, i, reviewed_by="mallory") for i, t in
                                                 enumerate(o.tables) if t.status == "proposed"), None),
    "rejected without reviewer": lambda o: next((_with_table(o, i, status="rejected") for i, t in
                                                 enumerate(o.tables) if t.status == "proposed"), None),
    "decision changed": lambda o: next((_with_table(o, i, status="rejected" if t.status == "approved" else "approved",
                                                    reviewed_by="x", reviewed_at=TODAY)
                                        for i, t in enumerate(o.tables) if t.status != "proposed"), None),
}


@pytest.mark.parametrize("tamper", list(TAMPERS))
@given(ontology=ontologies())
def test_every_non_decision_change_is_detected(tamper: str, ontology: Ontology) -> None:
    changed = TAMPERS[tamper](ontology)
    assume(changed is not None)  # どの壊し方も、当てられれば必ず何かを変える（dict の並びは == では比べられない）
    assert changed is not None
    assert review_changes(ontology, changed) != []


@given(ontologies())
def test_choosing_a_kind_that_breaks_validation_is_refused(ontology: Ontology) -> None:
    """grain の無い表を event にすると検証を通らない（MISSING_GRAIN）。その答えは ReviewError で、OM は変わらない。"""
    target = next((t for t in ontology.tables if t.kind is None and t.status == "proposed" and not t.grain), None)
    assume(target is not None)
    assert target is not None
    with pytest.raises(ReviewError, match="MISSING_GRAIN"):
        apply(ontology, target.id, Approve(kind=TableKind.event), "alice", TODAY)


def test_reviewer_is_required() -> None:
    ontology = load_ontology(GOLD)
    with pytest.raises(ReviewError, match="名前"):
        apply(ontology, ontology.tables[0].id, Reject(), "  ", TODAY)


GOLD = Path(__file__).parent.parent / "evals/datasets/adventureworks/gold/ontology"


def test_store_refuses_non_decision_changes(tmp_path: Path) -> None:
    root = tmp_path / "ontology"
    gold = load_ontology(GOLD)
    write_ontology(root, gold)
    table = gold.tables[0]
    tampered = replace(gold, tables=(table.model_copy(update={"note": "書き換え"}), *gold.tables[1:]))
    with pytest.raises(OntologyWriteRefusedError, match="判断以外"):
        write_reviewed_ontology(root, tampered)
    write_reviewed_ontology(root, apply(gold, table.id, Reject(), "alice", TODAY))
    reviewed = load_ontology(root).table(table.id)
    assert reviewed is not None and reviewed.status == "rejected"


# ---------------------------------------------------------------- コマンド

SCHEMAS = ["humanresources", "person", "production", "purchasing", "sales"]


def _cli(*argv: str, stdin: str = "") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), out=out, err=err, inp=io.StringIO(stdin))
    return code, out.getvalue(), err.getvalue()


def _ws(tmp_path: Path) -> list[str]:
    return ["--source", "adventureworks", "--work", str(tmp_path)]


def test_review_command_end_to_end(tmp_path: Path) -> None:
    assert _cli("run", *_ws(tmp_path), "--conninfo", "dbname=adventureworks",
                *(a for s in SCHEMAS for a in ("--schema", s)))[0] == 0
    root = tmp_path / "adventureworks/ontology"

    code, _, err = _cli("review", *_ws(tmp_path), "--reviewer", "alice", "--bulk", "0.5")
    assert code == 2 and "0.95 以上" in err  # 閾値を下げて一件ずつのレビューを迂回できない

    code, out, _ = _cli("review", *_ws(tmp_path), "--reviewer", "alice", "--bulk", "0.95", stdin="n\n")
    assert code == 0 and "cancelled" in out
    assert all(c.status == "proposed" for t in load_ontology(root).tables for c in (t.columns or {}).values())

    code, out, _ = _cli("review", *_ws(tmp_path), "--reviewer", "alice", "--bulk", "0.95", stdin="y\n")
    assert code == 0
    ontology = load_ontology(root)
    columns = [c for t in ontology.tables for c in (t.columns or {}).values()]
    assert all(c.status == "approved" for c in columns)  # 列の存在（確信度 1.0）は一覧を見せてから一括で承認
    assert all(t.status == "proposed" for t in ontology.tables)  # 表（0.7、種類の選択が要る）は含めない
    assert all(r.status == "proposed" for t in ontology.tables for r in t.relations or [])  # 関係（0.9）も含めない
    assert f"{len(columns):,} approved" in out

    # 最初の問いは関係の多い表。不正なキー → event（grain が無いので拒否）→ domain で承認、次を却下、次を飛ばして終わる
    code, out, _ = _cli("review", *_ws(tmp_path), "--reviewer", "alice", stdin="x\ne\nd\nn\ns\nq\n")
    assert code == 0 and "   ?" in out and "MISSING_GRAIN" in out and "2 answered" in out
    ontology = load_ontology(root)
    decided = [t for t in ontology.tables if t.status != "proposed"]
    assert sorted((t.status, t.kind or "") for t in decided) == [("approved", "domain"), ("rejected", "")]
    assert check(ontology) == []

    # 入力が途中で終わっても、それまでの答えは残り、正常に終わる
    code, out, _ = _cli("review", *_ws(tmp_path), "--reviewer", "alice", stdin="n\n")
    assert code == 0 and "1 answered" in out
    assert sum(t.status == "rejected" for t in load_ontology(root).tables) == 2

    # 人の判断がある OM は、再実行で上書きしない
    code, _, err = _cli("run", *_ws(tmp_path), "--conninfo", "dbname=adventureworks",
                        *(a for s in SCHEMAS for a in ("--schema", s)))
    assert code == 2 and "人の判断" in err
