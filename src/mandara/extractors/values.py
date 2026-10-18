"""実データの値の抽出器：ソースの実データ → 主張（source.kind: data）。

物理的な表現（encoded_as、T2.4）：列の値の見本から。
参照の被覆（covers_all、discriminated_by）：主キーを共有する外部キー（is-a の候補）について、上位の行のうち下位の行がある行。

Domain 層の純粋な関数。値を読むのは呼ぶ側（ColumnReader）。どの DB の値でも同じに働く。
- 見るのは型族が binary の列だけ（形式の判別が要るのはバイト列。ほかの型族の値は DB の型で表現が決まっている）
- 行を特定するのに主キーを使うので、主キーの無い表（ビューなど）の列は見ない。見なかった列は理由とともに返す
- 判別は mandara.codecs.detect。決まったときだけ主張にする。決まらなかった理由は呼ぶ側が記録に残す
- 主張の ID は内容から決まる（見本の行数は含めない）。同じ判別なら、見本が変わっても同じ ID
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from mandara.codecs.detect import Detection
from mandara.extractors.catalog import quote
from mandara.om.claims import new_claim
from mandara.om.ontology import is_shared_key
from mandara.om.model import Claim, ClaimPredicate, ClaimSource, ClaimSourceKind, SourceRef, TypeFamily
from mandara.source.catalog import Catalog, Column, Table
from mandara.source.coverage import Coverage, value_text

EXTRACTOR = "values 0.1"
SAMPLE_LIMIT = 100
"""1列について読む値の数の上限（主キーの順に先頭から）。"""
CLAIM_CONFIDENCE = 0.95
"""見本の値がすべてそのコーデックで読めた。見本の外の行は読んでいない。"""
COVERAGE_CONFIDENCE = 1.0
"""被覆は表を全部数えた事実（読んだ時点のデータについて）。"""
MAX_DISCRIMINATOR_VALUES = 20
"""区分の列とみなす値の種類の上限。"""
MIN_ROWS_PER_VALUE = 2
"""区分の列とみなすには、値1つあたりの行が平均でこれ以上（値の種類 × これ ≤ 行の数）。"""
_DISCRIMINATOR_FAMILIES = frozenset({TypeFamily.text, TypeFamily.integer, TypeFamily.boolean})


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


# ---------------------------------------------------------------- 参照の被覆（is-a の手がかり）


class Reference(Protocol):
    """参照：宣言された外部キー（ForeignKey）か、値の包含から推定した参照（InferredReference）。name は主張の出所に使う。"""

    @property
    def name(self) -> str: ...
    @property
    def columns(self) -> tuple[str, ...]: ...
    @property
    def target_schema(self) -> str: ...
    @property
    def target_table(self) -> str: ...
    @property
    def target_columns(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class InferredReference:
    """値の包含から推定した参照（宣言の無い外部キー）。name は推定の根拠の主張の出所。"""

    name: str
    columns: tuple[str, ...]
    target_schema: str
    target_table: str
    target_columns: tuple[str, ...]


@dataclass(frozen=True)
class SharedKey:
    """主キーを共有する参照：表の主キーの列が、ちょうど target の主キーの列を参照する。"""

    table: Table
    foreign_key: Reference
    target: Table

    @property
    def split_by(self) -> list[str]:
        """区分の列の候補：target の、主キーでない文字列・整数・真偽の列。"""
        key = set(self.target.primary_key.columns) if self.target.primary_key is not None else set[str]()
        return [c.name for c in self.target.columns if c.family in _DISCRIMINATOR_FAMILIES and c.name not in key]


def shared_keys(catalog: Catalog, inferred: Sequence[tuple[str, str, InferredReference]] = ()) -> list[SharedKey]:
    """範囲内の表同士の、主キーを共有する参照（範囲外の参照先は読まない）。宣言された外部キーと、inferred（表のスキーマ、
    表の名前、推定した参照）の両方から。"""
    tables = {(t.schema, t.name): t for t in catalog.tables}
    extra: dict[tuple[str, str], list[Reference]] = {}
    for schema, name, ref in inferred:
        extra.setdefault((schema, name), []).append(ref)
    found: list[SharedKey] = []
    for table in catalog.tables:
        if table.primary_key is None:
            continue
        for fk in [*table.foreign_keys, *extra.get((table.schema, table.name), [])]:
            target = tables.get((fk.target_schema, fk.target_table))
            if target is None or target.primary_key is None or target is table:
                continue
            if is_shared_key(zip(fk.columns, fk.target_columns, strict=True), table.primary_key.columns,
                             target.primary_key.columns):
                found.append(SharedKey(table, fk, target))
    return found


def coverage_claims(source: str, shared: SharedKey, coverage: Coverage) -> list[Claim]:
    """すべての行を覆う（covers_all）、区分の列の値とちょうど一致する（discriminated_by）。どちらでもなければ何も出さない。

    区分の列：値ごとに、すべての行が参照されているか、どの行も参照されていないかのどちらか。参照されている値も、
    されていない値も1つ以上ある。参照されている側に NULL があれば区分にしない（値の一覧で表せない）。
    値の種類が行の数の半分を超える列は区分にしない（値ごとの行が少ないと、一意に近い列でも偶然に当てはまる）。
    """
    table, fk, target = shared.table, shared.foreign_key, shared.target
    subject = SourceRef(source=source, schema_name=table.schema, table=table.name)
    reference = {"target": SourceRef(source=source, schema_name=target.schema, table=target.name),
                 "columns": list(fk.columns), "target_columns": list(fk.target_columns)}
    where = f"coverage {quote(table.schema, table.name)} {quote(fk.name)}"

    def claim(predicate: ClaimPredicate, locator: str, **payload: object) -> Claim:
        return new_claim(subject=subject, predicate=predicate, source=ClaimSource(kind=ClaimSourceKind.data, locator=locator),
                         extracted_by=EXTRACTOR, confidence=COVERAGE_CONFIDENCE, **reference, **payload)

    claims: list[Claim] = []
    if coverage.rows > 0 and coverage.covered == coverage.rows:
        claims.append(claim(ClaimPredicate.covers_all, where))
    for column, values in coverage.split.items():
        if values is None or len(values) * MIN_ROWS_PER_VALUE > coverage.rows:
            continue
        covered = [v for v in values if v.covered == v.rows]
        uncovered = [v for v in values if v.covered == 0]
        if len(covered) + len(uncovered) != len(values) or not covered or not uncovered:
            continue
        named = [v.value for v in covered if v.value is not None]
        if len(named) != len(covered):
            continue
        claims.append(claim(ClaimPredicate.discriminated_by, f"{where} by {quote(column)}", discriminator=column,
                            discriminator_values=sorted(value_text(v) for v in named)))
    return claims
