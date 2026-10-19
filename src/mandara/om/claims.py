"""主張台帳（仕様 6.6）の主張：内容から決まる ID と、主張の形の検証。

Domain 層。I/O を持たない。読み書きは mandara.omstore（Adapter）が担う。
- ID は内容（主語、述語、値、出所）の正規形のハッシュ。抽出器の版・確信度・日付には依存しない。
  決定的な抽出器を再実行すれば同じ ID になり、OM の evidence が切れない
- 述語ごとに、主語の粒度（表 / 列）と、持つべき値のフィールドが決まっている（SHAPES）
"""

import hashlib
import json
import math
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from mandara.om.model import Claim, ClaimPredicate, ClaimSource, ClaimSourceKind, SourceRef

PAYLOAD_FIELDS = (
    "columns", "target", "target_columns", "on_delete", "on_update", "text", "type_name", "family", "position",
    "object_kind", "encoding", "discriminator", "discriminator_values", "allowed_values", "values_normalized", "fixed_unit",
    "distinct_values", "matched_values", "target_values", "max_repeats", "value_span",
    "target_values_in_span", "patterns", "pattern_shares", "shape_level", "sampled_values", "target_sampled_values",
    "method", "score", "baseline",
)
"""述語によって持つ・持たないが決まる、値のフィールド。"""

Level = Literal["table", "column", "either"]


@dataclass(frozen=True)
class Shape:
    subject: Level
    fields: frozenset[str]
    """持たなければならず、これと optional 以外の値のフィールドは持ってはならない。"""
    optional: frozenset[str] = frozenset()
    """持っても持たなくてもよい（持つかどうかが値の種類で決まるもの。included_in の値の範囲は整数の列だけ）。"""


def _shape(subject: Level, *fields: str, optional: tuple[str, ...] = ()) -> Shape:
    return Shape(subject, frozenset(fields), frozenset(optional))


SHAPES: dict[str, Shape] = {
    ClaimPredicate.identified_by.value: _shape("table", "columns"),
    ClaimPredicate.unique.value: _shape("table", "columns"),
    ClaimPredicate.references.value: _shape("table", "columns", "target", "target_columns", "on_delete", "on_update"),
    ClaimPredicate.declared_as.value: _shape("table", "object_kind"),
    ClaimPredicate.not_null.value: _shape("column"),
    ClaimPredicate.nullable.value: _shape("column"),
    ClaimPredicate.defaults_to.value: _shape("column", "text"),
    ClaimPredicate.described_as.value: _shape("either", "text"),
    ClaimPredicate.checks.value: _shape("table", "text"),
    ClaimPredicate.allows_values.value: _shape("column", "allowed_values", "values_normalized"),
    ClaimPredicate.typed_as.value: _shape("column", "type_name", "family", "position"),
    ClaimPredicate.aliased_as.value: _shape("column", "type_name"),
    ClaimPredicate.encoded_as.value: _shape("column", "encoding"),
    ClaimPredicate.amounts_in.value: _shape("table", "fixed_unit"),
    ClaimPredicate.covers_all.value: _shape("table", "columns", "target", "target_columns"),
    ClaimPredicate.discriminated_by.value: _shape("table", "columns", "target", "target_columns", "discriminator",
                                                  "discriminator_values"),
    ClaimPredicate.included_in.value: _shape("table", "columns", "target", "target_columns", "distinct_values",
                                             "matched_values", "target_values", "max_repeats", optional=("value_span", "target_values_in_span")),
    ClaimPredicate.unique_in_data.value: _shape("table", "columns", "distinct_values"),
    ClaimPredicate.shaped_as.value: _shape("column", "patterns", "pattern_shares", "shape_level", "distinct_values",
                                           "sampled_values"),
    ClaimPredicate.resembles.value: _shape("table", "columns", "target", "target_columns", "method", "score", "baseline",
                                           "sampled_values", "target_sampled_values"),
}
_REFERRING = {ClaimPredicate.references.value, ClaimPredicate.covers_all.value, ClaimPredicate.discriminated_by.value,
              ClaimPredicate.included_in.value, ClaimPredicate.resembles.value}
"""参照（columns → target の target_columns）を持つ述語。"""


# ---------------------------------------------------------------- ID


def claim_id(claim: Claim) -> str:
    """内容の正規形のハッシュ。

    正規形は、含めるフィールドをここで明示して組み立てる（model_dump に頼らない）。
    ①のクラスに任意のフィールドを足しても、既存の主張の ID は変わらない。
    ID の算出を変えたら tests/test_claims.py のゴールデンテストが落ちる。そのときは全台帳の再生成が要る。
    列の並びは、ソースが宣言した順のまま含める（決定的な抽出器は同じ順で出す）。
    """
    content: dict[str, object] = {
        "subject": _ref(claim.subject),
        "predicate": claim.predicate,
        "source": {"kind": claim.source.kind, "locator": claim.source.locator},
    }
    for name in PAYLOAD_FIELDS:
        value = getattr(claim, name)
        if value is not None:
            content[name] = _ref(value) if isinstance(value, SourceRef) else value
    canonical = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "clm_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _ref(ref: SourceRef) -> dict[str, str]:
    out = {"source": ref.source, "schema_name": ref.schema_name, "table": ref.table}
    if ref.column is not None:
        out["column"] = ref.column
    return out


def new_claim(
    *,
    subject: SourceRef,
    predicate: ClaimPredicate,
    source: ClaimSource,
    extracted_by: str,
    confidence: float,
    **payload: object,
) -> Claim:
    """主張を作り、内容から ID を付ける。抽出器はこれで主張を作る。"""
    unknown = set(payload) - set(PAYLOAD_FIELDS)
    if unknown:
        raise ValueError(f"値のフィールドではない: {sorted(unknown)}")
    draft = Claim.model_validate(
        {
            "id": "clm_" + "0" * 16,
            "subject": subject,
            "predicate": predicate,
            "source": source,
            "extracted_by": extracted_by,
            "confidence": confidence,
            **payload,
        }
    )
    return draft.model_copy(update={"id": claim_id(draft)})


# ---------------------------------------------------------------- 検証


class ClaimRule(Enum):
    # 読み込みの境界（生成された型の検証）で閉じた集合の外の値は拒否される（仕様 4.5）。
    # これは検証を迂回して作られた値（model_copy / model_construct）への防御
    UNKNOWN_VALUE = "閉じた集合に無い値（述語、出所の種類）"
    ID_MISMATCH = "ID が内容から決まる値と違う"
    DUPLICATE_ID = "ID が重複している"
    SHAPE = "述語と、主語の粒度・値のフィールドが合っていない"


@dataclass(frozen=True)
class ClaimViolation:
    rule: ClaimRule
    location: str
    detail: str


_SOURCE_KINDS = {k.value for k in ClaimSourceKind}


def check_claims(claims: Sequence[Claim]) -> list[ClaimViolation]:
    violations = [v for c in claims for v in _check_claim(c)]
    for claim_id_ in sorted(k for k, n in Counter(c.id for c in claims).items() if n > 1):
        violations.append(ClaimViolation(ClaimRule.DUPLICATE_ID, claim_id_, ""))
    return violations


def _check_claim(claim: Claim) -> Iterator[ClaimViolation]:
    loc = claim.id

    def wrong(detail: str) -> ClaimViolation:
        return ClaimViolation(ClaimRule.SHAPE, loc, f"{claim.predicate}: {detail}")

    if claim.source.kind not in _SOURCE_KINDS:
        yield ClaimViolation(ClaimRule.UNKNOWN_VALUE, loc, f"source.kind={claim.source.kind!r}")
    shape = SHAPES.get(claim.predicate)
    if shape is None:
        yield ClaimViolation(ClaimRule.UNKNOWN_VALUE, loc, f"predicate={claim.predicate!r}")
        return
    if claim.id != (expected := claim_id(claim)):
        yield ClaimViolation(ClaimRule.ID_MISMATCH, loc, f"内容から決まる ID は {expected}")

    on_column = claim.subject.column is not None
    if shape.subject == "table" and on_column:
        yield wrong("主語は表（column を持たない）")
    if shape.subject == "column" and not on_column:
        yield wrong("主語は列（column が要る）")

    present = {name for name in PAYLOAD_FIELDS if getattr(claim, name) is not None}
    if empty := sorted(name for name in present if getattr(claim, name) == []):
        # 空の一覧は「持っていない」とは扱わない（ハッシュは None と [] を区別する）。持つなら中身が要る
        yield wrong(f"空の一覧: {', '.join(empty)}")
    if missing := sorted(shape.fields - present):
        yield wrong(f"無い: {', '.join(missing)}")
    if extra := sorted(present - shape.fields - shape.optional):
        yield wrong(f"持ってはならない: {', '.join(extra)}")

    if claim.predicate in _REFERRING and claim.target is not None:
        if claim.target.column is not None:
            yield wrong("target は表を指す（列の対応は target_columns）")
        if claim.target.source != claim.subject.source:
            yield wrong("参照はソースをまたがない")
        if len(claim.columns or []) != len(claim.target_columns or []):
            yield wrong("columns と target_columns の数が違う")
    if claim.predicate == ClaimPredicate.included_in.value and None not in (
            claim.distinct_values, claim.matched_values, claim.target_values, claim.max_repeats):
        distinct, matched = claim.distinct_values or 0, claim.matched_values or 0
        if matched > distinct or matched > (claim.target_values or 0):
            yield wrong("参照先にある値の数が、値の数か参照先の値の数を超える")
        if (distinct == 0) != (claim.max_repeats == 0):
            yield wrong("値が無いときだけ、1 つの値の最大の行数は 0")
        span, in_span = claim.value_span, claim.target_values_in_span
        if (span is None) != (in_span is None):
            yield wrong("value_span と target_values_in_span は両方あるか、両方無いか")
        elif span is not None and in_span is not None and (
                distinct == 0 or span < distinct or not matched <= in_span <= min(span, claim.target_values or 0)):
            yield wrong("値の範囲の数が合わない（範囲の整数 ≥ 値の数、含まれる数 ≤ 範囲のキーの値 ≤ 範囲の整数・キーの値の数）")
    if claim.predicate == ClaimPredicate.unique_in_data.value and claim.distinct_values is not None:
        if claim.distinct_values < 1:
            yield wrong("行の無い表の一意は主張にしない（行の数は 1 以上）")
    if claim.predicate == ClaimPredicate.shaped_as.value and claim.patterns and claim.pattern_shares:
        shares = claim.pattern_shares
        if len(claim.patterns) != len(shares):
            yield wrong("patterns と pattern_shares の数が違う")
        if any(not 0 < x <= 1 for x in shares) or sum(shares) > 1 + 1e-9 or list(shares) != sorted(shares, reverse=True):
            yield wrong("pattern_shares は 0 より大きく 1 以下、多い順で、和は 1 以下")
        if (claim.sampled_values or 0) < 1 or (claim.sampled_values or 0) > (claim.distinct_values or 0):
            yield wrong("見本の数は 1 以上で、異なる値の数以下")
    if claim.predicate == ClaimPredicate.resembles.value:
        if claim.method is not None and not claim.method.strip():
            yield wrong("method が空")
        if not all(math.isfinite(x) for x in (claim.score, claim.baseline) if x is not None):
            yield wrong("score と baseline は有限の数")
        if (claim.sampled_values or 0) < 1 or (claim.target_sampled_values or 0) < 1:
            yield wrong("見本の数は両方 1 以上")
        if claim.target is not None and claim.columns and claim.target_columns and (
                claim.subject.schema_name, claim.subject.table, *claim.columns) >= (
                claim.target.schema_name, claim.target.table, *claim.target_columns):
            yield wrong("組の向きは名前の順で 1 つ（表と列の順で columns の側が先）")
