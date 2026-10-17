"""宣言の抽出器：人が宣言したソースの性質（SourceDeclarations）→ 主張（source.kind: declaration）。

Domain 層の純粋な関数。宣言はソース全体についてなので、範囲内の表ごとに主張にする（推論は表ごとに主張を読むため）。
- default_currency → amounts_in（表の金額は、行が別の単位を言わない限りその通貨）
- 宣言は人が書いたものなので確信度 1.0（宣言が誤っていれば人の誤り。推論は根拠として引く）
"""

from mandara.extractors.catalog import quote
from mandara.om.claims import new_claim
from mandara.om.model import Claim, ClaimPredicate, ClaimSource, ClaimSourceKind, SourceDeclarations, SourceRef
from mandara.source.catalog import Catalog

EXTRACTOR = "declarations 0.1"
CONFIDENCE = 1.0


def extract(source: str, catalog: Catalog, declarations: SourceDeclarations) -> list[Claim]:
    if declarations.default_currency is None:
        return []
    return [new_claim(subject=SourceRef(source=source, schema_name=t.schema, table=t.name),
                      predicate=ClaimPredicate.amounts_in,
                      source=ClaimSource(kind=ClaimSourceKind.declaration,
                                         locator=f"declarations.yaml default_currency for {quote(t.schema, t.name)}"),
                      extracted_by=EXTRACTOR, confidence=CONFIDENCE, fixed_unit=declarations.default_currency)
            for t in catalog.tables]
