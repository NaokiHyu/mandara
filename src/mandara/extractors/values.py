"""実データの値の抽出器：列の値の見本 → 主張（source.kind: data）。今は物理的な表現（encoded_as）だけ（T2.4）。

Domain 層の純粋な関数。値を読むのは呼ぶ側（ColumnReader）。どの DB の値でも同じに働く。
- 見るのは型族が binary の列だけ（形式の判別が要るのはバイト列。ほかの型族の値は DB の型で表現が決まっている）
- 行を特定するのに主キーを使うので、主キーの無い表（ビューなど）の列は見ない。見なかった列は理由とともに返す
- 判別は mandara.codecs.detect。決まったときだけ主張にする。決まらなかった理由は呼ぶ側が記録に残す
- 主張の ID は内容から決まる（見本の行数は含めない）。同じ判別なら、見本が変わっても同じ ID
"""

from collections.abc import Sequence
from dataclasses import dataclass

from mandara.codecs.detect import Detection
from mandara.extractors.catalog import quote
from mandara.om.claims import new_claim
from mandara.om.model import Claim, ClaimPredicate, ClaimSource, ClaimSourceKind, SourceRef, TypeFamily
from mandara.source.catalog import Catalog, Column, Table

EXTRACTOR = "values 0.1"
SAMPLE_LIMIT = 100
"""1列について読む値の数の上限（主キーの順に先頭から）。"""
CLAIM_CONFIDENCE = 0.95
"""見本の値がすべてそのコーデックで読めた。見本の外の行は読んでいない。"""


@dataclass(frozen=True)
class Target:
    table: Table
    column: Column


@dataclass(frozen=True)
class NotExamined:
    table: str
    column: str
    reason: str


def targets(catalog: Catalog) -> tuple[list[Target], list[NotExamined]]:
    found: list[Target] = []
    not_examined: list[NotExamined] = []
    for table in catalog.tables:
        for column in table.columns:
            if column.family != TypeFamily.binary:
                continue
            if table.primary_key is None:
                not_examined.append(NotExamined(table.qualified_name, column.name, "主キーが無く、行を特定して読めない"))
            else:
                found.append(Target(table, column))
    return found, not_examined


def encoding_claim(source: str, target: Target, detection: Detection) -> Claim | None:
    if detection.encoding is None:
        return None
    table = target.table
    return new_claim(
        subject=SourceRef(source=source, schema_name=table.schema, table=table.name, column=target.column.name),
        predicate=ClaimPredicate.encoded_as,
        source=ClaimSource(kind=ClaimSourceKind.data, locator=f"values {quote(table.schema, table.name, target.column.name)}"),
        extracted_by=EXTRACTOR,
        confidence=CLAIM_CONFIDENCE,
        encoding=detection.encoding,
    )


def key_columns(target: Target) -> Sequence[str]:
    key = target.table.primary_key
    if key is None:
        raise AssertionError(f"targets で主キーのある表だけを選んだはず: {target.table.qualified_name}")
    return key.columns
