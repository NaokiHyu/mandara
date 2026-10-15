"""カタログの抽出器の法則。

- 健全：出す主張はすべて主張の検証を通り、ID が重複しない
- 情報を失わない：主張だけから、カタログの事実をすべて組み立て直せる。制約は名前ごとにちょうど1つの主張
- 決定的：同じカタログから同じ主張。ハッシュのシードを変えた別プロセスでも同じ
- AdventureWorks：制約の数と主張の数が一致し、台帳に書いて読める
"""

import json
import os
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from hypothesis import given

from mandara.connectors.postgres.catalog_reader import PostgresCatalogReader
from mandara.extractors.catalog import COMMENT_CONFIDENCE, extract, quote
from mandara.om.claims import check_claims
from mandara.om.model import Claim, ClaimPredicate, ObjectKind
from mandara.omstore.claim_store import load_claim_ledger, write_claim_ledger
from mandara.source.catalog import Catalog, Scope, Table

from tests.catalog_strategies import catalogs

@given(catalogs())
def test_claims_are_valid(catalog: Catalog) -> None:
    assert check_claims(extract("src", catalog)) == []


# ---------------------------------------------------------------- 情報を失わない


@dataclass
class ColumnFacts:
    type: tuple[object, ...] | None = None
    nullable: bool | None = None
    default: str | None = None
    alias: str | None = None
    comment: str | None = None


@dataclass
class TableFacts:
    kind: str | None = None
    comment: str | None = None
    constraints: dict[str, tuple[object, ...]] = field(default_factory=lambda: dict[str, tuple[object, ...]]())
    """locator（制約の名前を含む）→ 中身。同じ locator の主張が2つあれば失敗にする。"""
    columns: dict[str, ColumnFacts] = field(default_factory=lambda: dict[str, ColumnFacts]())


def _rebuild(claims: list[Claim]) -> dict[tuple[str, str, str], TableFacts]:
    """主張だけから、表ごとのカタログの事実を組み立て直す。"""
    facts: dict[tuple[str, str, str], TableFacts] = {}
    for c in claims:
        t = facts.setdefault((c.subject.source, c.subject.schema_name, c.subject.table), TableFacts())
        name = c.subject.column
        col = t.columns.setdefault(name, ColumnFacts()) if name is not None else None

        def constraint(*content: object) -> None:
            assert c.source.locator not in t.constraints, f"制約の主張が重複: {c.source.locator}"
            t.constraints[c.source.locator] = (c.predicate, *content)

        match c.predicate, col:
            case ClaimPredicate.declared_as.value, None:
                t.kind = c.object_kind
            case ClaimPredicate.described_as.value, None:
                t.comment = c.text
            case ClaimPredicate.described_as.value, ColumnFacts():
                col.comment = c.text
            case ClaimPredicate.identified_by.value | ClaimPredicate.unique.value, None:
                constraint(tuple(c.columns or []))
            case ClaimPredicate.references.value, None if c.target is not None:
                constraint(tuple(c.columns or []), c.target.source, c.target.schema_name, c.target.table,
                           tuple(c.target_columns or []), c.on_delete, c.on_update)
            case ClaimPredicate.checks.value, None:
                constraint(c.text)
            case ClaimPredicate.typed_as.value, ColumnFacts():
                col.type = (c.type_name, c.family, c.position)
            case ClaimPredicate.aliased_as.value, ColumnFacts():
                col.alias = c.type_name
            case ClaimPredicate.not_null.value, ColumnFacts():
                assert col.nullable is None
                col.nullable = False
            case ClaimPredicate.nullable.value, ColumnFacts():
                assert col.nullable is None
                col.nullable = True
            case ClaimPredicate.defaults_to.value, ColumnFacts():
                col.default = c.text
            case _:
                raise AssertionError(f"想定外の主張: {c}")
    return facts


def _facts(table: Table) -> TableFacts:
    where = quote(table.schema, table.name)
    constraints: dict[str, tuple[object, ...]] = {}
    if table.primary_key:
        constraints[f"constraint {quote(table.primary_key.name)} on {where}"] = (
            "identified_by", table.primary_key.columns)
    for k in table.uniques:
        constraints[f"constraint {quote(k.name)} on {where}"] = ("unique", k.columns)
    for f in table.foreign_keys:
        constraints[f"constraint {quote(f.name)} on {where}"] = (
            "references", f.columns, "src", f.target_schema, f.target_table, f.target_columns,
            f.on_delete.value, f.on_update.value)
    for c in table.checks:
        constraints[f"constraint {quote(c.name)} on {where}"] = ("checks", c.expression)
    return TableFacts(
        kind=table.kind.value,
        comment=table.comment,
        constraints=constraints,
        columns={c.name: ColumnFacts((c.source_type, c.family.value, c.position), c.nullable, c.default,
                                     c.type_alias, c.comment) for c in table.columns},
    )


@given(catalogs())
def test_claims_preserve_catalog_facts(catalog: Catalog) -> None:
    """主張だけから、カタログの事実をすべて組み立て直せる（NULL 可否は 可 / 不可 / 宣言なし の3値のまま）。"""
    assert _rebuild(extract("src", catalog)) == {("src", t.schema, t.name): _facts(t) for t in catalog.tables}


@given(catalogs())
def test_comments_are_less_certain_than_declarations(catalog: Catalog) -> None:
    for c in extract("src", catalog):
        expected = COMMENT_CONFIDENCE if c.predicate == ClaimPredicate.described_as.value else 1.0
        assert c.confidence == expected


# ---------------------------------------------------------------- AdventureWorks

AW_SCOPE = Scope(("humanresources", "person", "production", "purchasing", "sales"))
_AW_IDS = """
import json
from mandara.connectors.postgres.catalog_reader import PostgresCatalogReader
from mandara.extractors.catalog import extract
from mandara.source.catalog import Scope
catalog = PostgresCatalogReader("dbname=adventureworks").read(Scope(tuple(json.loads(input()))))
print(json.dumps([c.id for c in extract("adventureworks", catalog)]))
"""


def _ids_in_fresh_process(hash_seed: str) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-c", _AW_IDS], input=json.dumps(AW_SCOPE.schemas), capture_output=True, text=True,
        check=True, env={**os.environ, "PYTHONHASHSEED": hash_seed},
    )
    return json.loads(result.stdout)


def test_adventureworks_claims(tmp_path: Path) -> None:
    catalog = PostgresCatalogReader("dbname=adventureworks").read(AW_SCOPE)
    claims = extract("adventureworks", catalog)
    assert check_claims(claims) == []
    counts = Counter(c.predicate for c in claims)
    tables = [t for t in catalog.tables if t.kind is ObjectKind.table]
    assert counts[ClaimPredicate.declared_as] == 68 + 19 + 2
    assert counts[ClaimPredicate.references] == 90
    assert counts[ClaimPredicate.checks] == 88
    assert counts[ClaimPredicate.identified_by] == 68
    assert counts[ClaimPredicate.unique] == 3
    assert counts[ClaimPredicate.typed_as] == sum(len(t.columns) for t in catalog.tables)
    assert counts[ClaimPredicate.aliased_as] == 43 + 63 + 5
    assert counts[ClaimPredicate.not_null] + counts[ClaimPredicate.nullable] == sum(len(t.columns) for t in tables)

    root = tmp_path / "claims"
    write_claim_ledger(root, "adventureworks", AW_SCOPE.schemas, claims)
    assert sorted(c.id for c in load_claim_ledger(root, "adventureworks")) == sorted(c.id for c in claims)


def test_adventureworks_ids_do_not_depend_on_hash_seed() -> None:
    assert _ids_in_fresh_process("1") == _ids_in_fresh_process("2")
