"""カタログの抽出器：Catalog → 主張（source.kind: schema）。

Domain 層の純粋な関数。DB に触れない（カタログはコネクタが読む）。どの DB のカタログでも同じに働く。
- カタログの事実はすべて主張にする。モデラーはカタログを直接読まず、主張だけを根拠にする（evidence で指せるように）
- 制約・型・宣言は DB が宣言した事実なので確信度 1.0、コメントは人の書いた説明なので 0.85（仕様 6.6.5）
- locator は出所の中の場所（どの表・列・制約か）を指す。仕様 6.6.5 の「同じ出所は1票」はこの単位で束ねる
- 主張の解釈（どの関係型か、どの意味型か）はしない。それはモデラーの責務
"""

from collections.abc import Iterator

from mandara.om.claims import new_claim
from mandara.om.model import Claim, ClaimPredicate, ClaimSource, ClaimSourceKind, SourceRef
from mandara.source.catalog import Catalog, Table

EXTRACTOR = "catalog 0.1"
CONSTRAINT_CONFIDENCE = 1.0
COMMENT_CONFIDENCE = 0.85


def extract(source: str, catalog: Catalog) -> list[Claim]:
    return [claim for table in catalog.tables for claim in _table_claims(source, table)]


def quote(*names: str) -> str:
    """SQL 標準の引用符つき識別子で、名前の並びを曖昧さなく書く（名前に "." や '"' があってもよい）。

    locator 用に常に引用する（名前の中身によって書き方が変わらない）。OM の name は読みやすさのために
    必要なときだけ引用する（mandara.om.names.qualify）。用途が違うので別にしている。
    """
    return ".".join('"' + n.replace('"', '""') + '"' for n in names)


def _table_claims(source: str, table: Table) -> Iterator[Claim]:
    subject = SourceRef(source=source, schema_name=table.schema, table=table.name)
    where = quote(table.schema, table.name)

    def claim(predicate: ClaimPredicate, locator: str, ref: SourceRef = subject,
              confidence: float = CONSTRAINT_CONFIDENCE, **payload: object) -> Claim:
        return new_claim(
            subject=ref,
            predicate=predicate,
            source=ClaimSource(kind=ClaimSourceKind.schema, locator=locator),
            extracted_by=EXTRACTOR,
            confidence=confidence,
            **payload,
        )

    yield claim(ClaimPredicate.declared_as, f"object {where}", object_kind=table.kind)
    if table.comment is not None:
        yield claim(ClaimPredicate.described_as, f"comment on {where}", confidence=COMMENT_CONFIDENCE,
                    text=table.comment)
    if table.primary_key is not None:
        key = table.primary_key
        yield claim(ClaimPredicate.identified_by, f"constraint {quote(key.name)} on {where}", columns=list(key.columns))
    for key in table.uniques:
        yield claim(ClaimPredicate.unique, f"constraint {quote(key.name)} on {where}", columns=list(key.columns))
    for fk in table.foreign_keys:
        yield claim(
            ClaimPredicate.references,
            f"constraint {quote(fk.name)} on {where}",
            columns=list(fk.columns),
            target=SourceRef(source=source, schema_name=fk.target_schema, table=fk.target_table),
            target_columns=list(fk.target_columns),
            on_delete=fk.on_delete,
            on_update=fk.on_update,
        )
    for check in table.checks:
        yield claim(ClaimPredicate.checks, f"constraint {quote(check.name)} on {where}", text=check.expression)

    for column in table.columns:
        ref = subject.model_copy(update={"column": column.name})
        declared = f"column {quote(table.schema, table.name, column.name)}"
        yield claim(ClaimPredicate.typed_as, declared, ref,
                    type_name=column.source_type, family=column.family, position=column.position)
        if column.type_alias is not None:
            yield claim(ClaimPredicate.aliased_as, declared, ref, type_name=column.type_alias)
        if column.nullable is not None:  # None は宣言が無い（ビューの列など）。どちらの主張も出さない
            yield claim(ClaimPredicate.nullable if column.nullable else ClaimPredicate.not_null, declared, ref)
        if column.default is not None:
            yield claim(ClaimPredicate.defaults_to, declared, ref, text=column.default)
        if column.comment is not None:
            yield claim(ClaimPredicate.described_as, f"comment on {quote(table.schema, table.name, column.name)}", ref,
                        confidence=COMMENT_CONFIDENCE, text=column.comment)
