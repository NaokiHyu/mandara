"""主張台帳の法則。

- ID：同じ内容なら同じ ID。抽出器の版・確信度・日付では変わらず、内容が変われば変わる
- 健全：妥当な主張には違反が無い。完全：壊した主張は、対応する規則で必ず検出される
- ストア：書いて読むと同じ集合。書き直すと古い表の主張は残らない。台帳でないものは消さない
"""

from collections.abc import Callable, Iterable
from datetime import date
from typing import cast
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mandara.om.claims import PAYLOAD_FIELDS, ClaimRule, check_claims, claim_id, new_claim
from mandara.om.model import (
    Claim,
    ClaimFile,
    ClaimPredicate,
    ClaimSource,
    ClaimSourceKind,
    ObjectKind,
    ReferentialAction,
    SourceRef,
    TypeFamily,
)
from mandara.omstore.yaml_store import (
    InvalidClaimsError,
    OmLoadError,
    decode_name,
    dump_claims,
    encode_name,
    load_claim_ledger,
    parse_claims,
    write_claim_ledger,
)

from tests.strategies import claim_ledgers, claims

DETERMINISTIC = (ClaimSourceKind.schema, ClaimSourceKind.sql, ClaimSourceKind.codelist)


@given(claim_ledgers())
def test_valid_claims_have_no_violations(ledger: tuple[Claim, ...]) -> None:
    assert check_claims(ledger) == []


@given(claim_ledgers())
def test_claim_file_roundtrip(ledger: tuple[Claim, ...]) -> None:
    claim_file = ClaimFile(claims=list(ledger))
    assert parse_claims(dump_claims(claim_file)) == claim_file


@given(claim_ledgers())
def test_claim_file_dump_is_idempotent(ledger: tuple[Claim, ...]) -> None:
    once = dump_claims(ClaimFile(claims=list(ledger)))
    assert dump_claims(parse_claims(once)) == once


# ---------------------------------------------------------------- ID


@given(claims(), st.text(min_size=1), st.floats(0.0, 1.0), st.dates())
def test_id_ignores_provenance_of_extraction(claim: Claim, extracted_by: str, confidence: float, valid_at: date) -> None:
    """抽出器の版・確信度・日付を変えても ID は変わらない（再実行しても evidence が切れない）。"""
    other = claim.model_copy(update={"extracted_by": extracted_by, "confidence": confidence, "valid_at": valid_at})
    assert claim_id(other) == claim.id


def _other(values: Iterable[str], current: str) -> str:
    return next(v for v in values if v != current)


def _changed(value: object, field: str) -> object:
    """値を、同じ型の別の値に変える。"""
    enums: dict[str, Iterable[str]] = {
        "on_delete": [a.value for a in ReferentialAction], "on_update": [a.value for a in ReferentialAction],
        "family": [f.value for f in TypeFamily], "predicate": [p.value for p in ClaimPredicate],
        "kind": [k.value for k in ClaimSourceKind], "object_kind": [k.value for k in ObjectKind],
    }
    if field in enums:
        assert isinstance(value, str)
        return _other(enums[field], value)
    if isinstance(value, SourceRef):
        return value.model_copy(update={"table": value.table + "x"})
    if isinstance(value, list):
        return [*cast(list[str], value), "zz_extra"]
    if isinstance(value, int):
        return value + 1
    if value is None:
        return "added"
    assert isinstance(value, str)
    return value + "x"


@given(claim=claims())
def test_id_changes_with_every_content_field(claim: Claim) -> None:
    """主語・述語・出所・値の、どのフィールドを変えても ID が変わる（値は持っているものすべて）。"""
    variants: dict[str, Claim] = {
        "predicate": claim.model_copy(update={"predicate": _changed(claim.predicate, "predicate")}),
        "source.kind": claim.model_copy(update={"source": claim.source.model_copy(
            update={"kind": _changed(claim.source.kind, "kind")})}),
        "source.locator": claim.model_copy(update={"source": claim.source.model_copy(
            update={"locator": claim.source.locator + "x"})}),
        **{f"subject.{f}": claim.model_copy(update={"subject": claim.subject.model_copy(
            update={f: _changed(getattr(claim.subject, f), f)})}) for f in ("source", "schema_name", "table", "column")},
        **{f: claim.model_copy(update={f: _changed(getattr(claim, f), f)})
           for f in PAYLOAD_FIELDS if getattr(claim, f) is not None},
    }
    for name, variant in variants.items():
        assert claim_id(variant) != claim.id, name


PINNED_ID = "clm_fc51173a35ce89dd"
"""2026-10-15 の正規形で算出した値。変化の検出器であって、正しさの根拠ではない。"""


def test_id_is_pinned() -> None:
    """ID の算出（正規形）が変わったことを検出する。変えたら、全台帳の再生成が要る。"""
    claim = new_claim(
        subject=SourceRef(source="adventureworks", schema_name="sales", table="salesorderdetail"),
        predicate=ClaimPredicate.references,
        source=ClaimSource(kind=ClaimSourceKind.schema, locator="constraint FK_SalesOrderDetail_SalesOrderHeader"),
        extracted_by="test",
        confidence=1.0,
        columns=["salesorderid"],
        target=SourceRef(source="adventureworks", schema_name="sales", table="salesorderheader"),
        target_columns=["salesorderid"],
        on_delete=ReferentialAction.cascade,
        on_update=ReferentialAction.no_action,
    )
    assert claim.id == PINNED_ID


def test_new_claim_rejects_unknown_payload() -> None:
    with pytest.raises(ValueError, match="値のフィールドではない"):
        new_claim(
            subject=SourceRef(source="s", schema_name="p", table="t", column="c"),
            predicate=ClaimPredicate.not_null,
            source=ClaimSource(kind=ClaimSourceKind.schema, locator="x"),
            extracted_by="t",
            confidence=1.0,
            colums=["typo"],
        )


# ---------------------------------------------------------------- 壊す

Mutation = Callable[[tuple[Claim, ...]], tuple[Claim, ...]]


def _first(ledger: tuple[Claim, ...], update: dict[str, object], *, rehash: bool = True) -> tuple[Claim, ...]:
    changed = ledger[0].model_copy(update=update)
    if rehash:
        changed = changed.model_copy(update={"id": claim_id(changed)})
    return (changed, *ledger[1:])


def _rehashed(claim: Claim, **update: object) -> Claim:
    changed = claim.model_copy(update=update)
    return changed.model_copy(update={"id": claim_id(changed)})


def _column_claim() -> Claim:
    return new_claim(
        subject=SourceRef(source="src", schema_name="s", table="t", column="c"),
        predicate=ClaimPredicate.not_null,
        source=ClaimSource(kind=ClaimSourceKind.schema, locator="x"),
        extracted_by="t",
        confidence=1.0,
    )


def _reference_claim() -> Claim:
    return new_claim(
        subject=SourceRef(source="src", schema_name="s", table="t"),
        predicate=ClaimPredicate.references,
        source=ClaimSource(kind=ClaimSourceKind.schema, locator="fk"),
        extracted_by="t",
        confidence=1.0,
        columns=["a"],
        target=SourceRef(source="src", schema_name="s", table="u"),
        target_columns=["b"],
        on_delete=ReferentialAction.no_action,
        on_update=ReferentialAction.no_action,
    )


SHAPE_BREAKS: dict[str, Callable[[], Claim]] = {
    "extra field": lambda: _rehashed(_column_claim(), text="余計"),
    "missing field": lambda: _rehashed(_reference_claim(), on_delete=None),
    "empty list": lambda: _rehashed(_column_claim(), columns=[]),
    "column predicate on table": lambda: _rehashed(_column_claim(), subject=SourceRef(source="src", schema_name="s", table="t")),
    "table predicate on column": lambda: _rehashed(_reference_claim(), subject=SourceRef(
        source="src", schema_name="s", table="t", column="c")),
    "target is a column": lambda: _rehashed(_reference_claim(), target=SourceRef(
        source="src", schema_name="s", table="u", column="b")),
    "target in other source": lambda: _rehashed(_reference_claim(), target=SourceRef(
        source="other", schema_name="s", table="u")),
    "column count mismatch": lambda: _rehashed(_reference_claim(), target_columns=["b", "c"]),
}


@pytest.mark.parametrize("kind", list(SHAPE_BREAKS))
@given(ledger=claim_ledgers())
def test_every_shape_break_is_detected(kind: str, ledger: tuple[Claim, ...]) -> None:
    broken = SHAPE_BREAKS[kind]()
    assert {v.rule for v in check_claims((*ledger, broken)) if v.location == broken.id} == {ClaimRule.SHAPE}


MUTATIONS: dict[ClaimRule, Mutation] = {
    ClaimRule.UNKNOWN_VALUE: lambda o: _first(o, {"predicate": "maybe"}),
    ClaimRule.ID_MISMATCH: lambda o: _first(o, {"id": "clm_" + "f" * 16}, rehash=False),
    ClaimRule.DUPLICATE_ID: lambda o: (*o, o[0]),
    ClaimRule.SHAPE: lambda o: (*o, SHAPE_BREAKS["extra field"]()),
}


def test_every_rule_has_a_mutation() -> None:
    assert set(MUTATIONS) == set(ClaimRule)


@pytest.mark.parametrize("rule", list(ClaimRule), ids=lambda r: r.name)
@given(data=st.data())
def test_broken_claims_are_detected(rule: ClaimRule, data: st.DataObject) -> None:
    ledger = data.draw(claim_ledgers())
    assert rule in {v.rule for v in check_claims(MUTATIONS[rule](ledger))}


# ---------------------------------------------------------------- ストア


@given(claim_ledgers(kinds=DETERMINISTIC))
def test_ledger_write_then_load(tmp_path_factory: pytest.TempPathFactory, ledger: tuple[Claim, ...]) -> None:
    root = tmp_path_factory.mktemp("ledger") / "claims"
    write_claim_ledger(root, "src", ledger)
    loaded = load_claim_ledger(root, "src")
    assert sorted(loaded, key=lambda c: c.id) == sorted(ledger, key=lambda c: c.id)


def _claim(table: str, schema: str = "s", source: str = "src") -> Claim:
    return new_claim(
        subject=SourceRef(source=source, schema_name=schema, table=table, column="c"),
        predicate=ClaimPredicate.not_null,
        source=ClaimSource(kind=ClaimSourceKind.schema, locator="x"),
        extracted_by="t",
        confidence=1.0,
    )


def test_rewrite_replaces_whole_ledger(tmp_path: Path) -> None:
    root = tmp_path / "claims"
    write_claim_ledger(root, "src", [_claim("old")])
    write_claim_ledger(root, "src", [_claim("new")])
    assert [c.subject.table for c in load_claim_ledger(root, "src")] == ["new"]
    assert not (root / "s" / "old.yaml").exists()
    assert [p.name for p in tmp_path.iterdir()] == ["claims"]  # 作業用のディレクトリが残らない


def test_rewrite_refuses_to_delete_foreign_files(tmp_path: Path) -> None:
    root = tmp_path / "claims"
    root.mkdir()
    (root / "notes.txt").write_text("人のメモ")
    with pytest.raises(OmLoadError):
        write_claim_ledger(root, "src", [_claim("t")])
    assert (root / "notes.txt").exists()


def test_misplaced_claim_is_an_error(tmp_path: Path) -> None:
    root = tmp_path / "claims"
    write_claim_ledger(root, "src", [_claim("t")])
    (root / "s" / "t.yaml").rename(root / "s" / "u.yaml")
    with pytest.raises(OmLoadError, match="食い違う"):
        load_claim_ledger(root, "src")


def test_missing_ledger_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(OmLoadError, match="無い"):
        load_claim_ledger(tmp_path / "nothing", "src")


@given(st.text(min_size=1))
def test_name_encoding_roundtrip(name: str) -> None:
    encoded = encode_name(name)
    assert decode_name(encoded) == name
    assert set(encoded) <= set("abcdefghijklmnopqrstuvwxyz0123456789_-%ABCDEF")


@given(st.text(min_size=1), st.text(min_size=1))
def test_name_encoding_is_case_insensitive_safe(a: str, b: str) -> None:
    """大文字小文字を区別しないファイルシステムでも、違う名前は違うファイルになる。"""
    if a != b:
        assert encode_name(a).casefold() != encode_name(b).casefold()


def test_hostile_names_stay_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "claims"
    ledger = [_claim("a/b", schema=".."), _claim("Foo"), _claim("foo"), _claim("caf\u00e9"), _claim("cafe\u0301")]
    write_claim_ledger(root, "src", ledger)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["claims"]
    assert sorted(c.id for c in load_claim_ledger(root, "src")) == sorted(c.id for c in ledger)


def test_write_rejects_other_sources(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="別のソース"):
        write_claim_ledger(tmp_path / "claims", "src", [_claim("t", source="other")])


def test_write_rejects_non_regenerable_claims(tmp_path: Path) -> None:
    review = _rehashed(_claim("t"), source=ClaimSource(kind=ClaimSourceKind.review, locator="q1"))
    with pytest.raises(ValueError, match="再生成できない"):
        write_claim_ledger(tmp_path / "claims", "src", [review])


def test_write_rejects_invalid_claims(tmp_path: Path) -> None:
    with pytest.raises(InvalidClaimsError):
        write_claim_ledger(tmp_path / "claims", "src", [_claim("t").model_copy(update={"id": "clm_" + "0" * 16})])
    assert not (tmp_path / "claims").exists()


def test_tampered_ledger_is_rejected_on_load(tmp_path: Path) -> None:
    root = tmp_path / "claims"
    write_claim_ledger(root, "src", [_claim("t")])
    path = root / "s" / "t.yaml"
    path.write_text(path.read_text().replace("column: c", "column: d"))
    with pytest.raises(OmLoadError) as e:
        load_claim_ledger(root, "src")
    assert isinstance(e.value.cause, InvalidClaimsError)


def test_root_that_is_a_file_is_an_error(tmp_path: Path) -> None:
    root = tmp_path / "claims"
    root.write_text("not a ledger")
    with pytest.raises(OmLoadError):
        write_claim_ledger(root, "src", [_claim("t")])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["claims"]


def test_symlink_in_ledger_is_not_followed_or_deleted(tmp_path: Path) -> None:
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "data.yaml").write_text("x")
    root = tmp_path / "claims"
    write_claim_ledger(root, "src", [_claim("t")])
    (root / "link").symlink_to(precious)
    with pytest.raises(OmLoadError, match="シンボリックリンク"):
        write_claim_ledger(root, "src", [_claim("t")])
    assert (precious / "data.yaml").exists()
