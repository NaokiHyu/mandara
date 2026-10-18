"""宣言の無い参照の測定：列の値が、別の表のキーの値にどれだけ含まれるか → 主張（included_in。source.kind: data）。

Domain 層の純粋な関数。値を読むのは呼ぶ側（InclusionReader）。仕様 6.2、6.7.2 B / D。
- 参照先の候補は、1 列のキー（主キー、または NULL の無い列の一意）。複合キーへの参照はまだ測らない
- 参照元の候補は、表（ビューではない）の列で、キーと同じ型族のもの（ソースの型名は問わない。smallint の列が integer の
  キーを指すこともあるし、ファイルのソースでは型名がばらつく）。キーの列そのものは除く
- 全部を数える前に、安い順に足切りする（足切りは事実の主張にしない。落とした数は呼ぶ側が記録に残す）：
  1. 型族が同じ
  2. 異なる値の数（参照元 ≤ キー）と、順序に意味のある型なら値の範囲（参照元の最小・最大がキーの範囲の中）
  3. 見本の重なり（参照元の値の先頭 PROBE_SAMPLE 個に、キーの値が1つも無ければ落とす）
- 残った組は全部数え、包含が成り立たない組も主張にする（否定の証拠。仕様 6.7.2 D）
- 整数の列なら、列の値の範囲の幅と、その範囲にあるキーの値の数も主張に載せる（偶然の包含を見分ける材料。確率にするのは
  モデラー）
- 主張の値は数えた数そのもの。どれを参照とみなすかは決めない（モデラーの mandara.modeler.joins が決める）
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from mandara.extractors.catalog import quote
from mandara.om.claims import new_claim
from mandara.om.model import Claim, ClaimPredicate, ClaimSource, ClaimSourceKind, ObjectKind, SourceRef, TypeFamily
from mandara.source.catalog import Catalog, Column, Table
from mandara.source.inclusion import ColumnProfile, Inclusion, KeyRef, Ordered

EXTRACTOR = "joins 0.2"
MEASURE_CONFIDENCE = 1.0
"""数えた事実（読んだ時点のデータについて）。"""
PROBE_SAMPLE = 50
"""見本の重なりで見る、参照元の異なる値の数。"""
ORDERED_FAMILIES = frozenset({TypeFamily.integer, TypeFamily.decimal, TypeFamily.float, TypeFamily.date,
                              TypeFamily.timestamp})
"""最小・最大で範囲を比べてよい型族（文字列は照合順序が DB に依るので比べない）。"""
_KEY_FAMILIES = frozenset({TypeFamily.integer, TypeFamily.decimal, TypeFamily.text, TypeFamily.uuid, TypeFamily.date,
                           TypeFamily.timestamp})
"""参照先のキーになりうる型族（真偽・バイト列・XML などのキーは参照の対象にしない）。"""


@dataclass(frozen=True)
class Key:
    table: Table
    column: Column

    @property
    def ref(self) -> KeyRef:
        return KeyRef(self.table.schema, self.table.name, self.column.name)


@dataclass(frozen=True)
class Candidate:
    """参照元の列と、型の合うキー（参照先の候補）。"""

    table: Table
    column: Column
    keys: tuple[Key, ...]


def keys(catalog: Catalog) -> list[Key]:
    """1 列のキー：表の主キー、または NULL の無い列の一意。"""
    out: list[Key] = []
    for table in catalog.tables:
        if table.kind != ObjectKind.table.value:
            continue
        by_name = {c.name: c for c in table.columns}
        single = [k.columns for k in (table.primary_key, *table.uniques) if k is not None and len(k.columns) == 1]
        for (name,) in dict.fromkeys(single):
            column = by_name[name]
            if column.family in _KEY_FAMILIES and (column.nullable is False or (table.primary_key is not None
                                                                                and table.primary_key.columns == (name,))):
                out.append(Key(table, column))
    return out


def candidates(catalog: Catalog, found: Sequence[Key]) -> list[Candidate]:
    """表の列ごとに、型族が同じキー。キーの列そのものは参照元にしない（自分を指すだけ）。"""
    out: list[Candidate] = []
    for table in catalog.tables:
        if table.kind != ObjectKind.table.value:
            continue
        for column in table.columns:
            matching = tuple(k for k in found if k.column.family == column.family
                             and not (k.table is table and k.column.name == column.name))
            if matching:
                out.append(Candidate(table, column, matching))
    return out


def comparable(a: Ordered, b: Ordered) -> bool:
    """範囲を比べてよい2つの値か：同じ種類の値（数どうし、日付どうし、日時どうし）で、日時ならタイムゾーンの有無が同じ、
    10進数なら NaN でない。比べられない組は、範囲では足切りしない（落とさない側に倒す。包含は全部数えて確かめる）。"""
    if _nan(a) or _nan(b):
        return False
    if isinstance(a, int | Decimal | float) and isinstance(b, int | Decimal | float):
        return True
    if isinstance(a, datetime) and isinstance(b, datetime):
        return (a.tzinfo is None) == (b.tzinfo is None)
    return not isinstance(a, datetime) and not isinstance(b, datetime) and isinstance(a, date) and isinstance(b, date)


def _nan(value: Ordered) -> bool:
    return (isinstance(value, Decimal) and value.is_nan()) or (isinstance(value, float) and value != value)


def _below(a: Ordered, b: Ordered) -> bool:
    """a < b。比べられない組（comparable でない）は誤りとして止める（黙って偽にしない）。"""
    if isinstance(a, datetime) and isinstance(b, datetime):
        return a < b
    if isinstance(a, date) and isinstance(b, date) and not isinstance(a, datetime) and not isinstance(b, datetime):
        return a < b
    if isinstance(a, int | Decimal) and isinstance(b, int | Decimal):
        return Decimal(a) < Decimal(b)
    if isinstance(a, int | float) and isinstance(b, int | float):
        return a < b
    if isinstance(a, Decimal | float) and isinstance(b, Decimal | float):
        return Decimal(a) < Decimal(b)
    raise ValueError(f"範囲を比べられない値の組: {type(a).__name__} と {type(b).__name__}")


def narrow(candidate: Candidate, profile: ColumnProfile, profiles: Mapping[KeyRef, ColumnProfile]) -> list[Key]:
    """数え方（異なる値の数、範囲）で、包含しえないキーを落とす。値の無い列は何も残さない。範囲は比べられる組だけで見る
    （comparable。たとえばタイムゾーンの有無が違う日時は比べない）。"""
    if profile.distinct == 0:
        return []
    kept: list[Key] = []
    for key in candidate.keys:
        k = profiles[key.ref]
        if profile.distinct > k.distinct:
            continue
        if (profile.low is not None and profile.high is not None and k.low is not None and k.high is not None
                and comparable(profile.low, k.low) and comparable(profile.high, k.high)):
            if _below(profile.low, k.low) or _below(k.high, profile.high):
                continue
        kept.append(key)
    return kept


def claims(source: str, candidate: Candidate, measured: Sequence[Key], inclusion: Inclusion) -> list[Claim]:
    """数えた組ごとに1つ（包含が成り立たない組も）。"""
    if len(measured) != len(inclusion.matched):
        raise ValueError(f"数えたキーと結果の数が合わない: {len(measured)} と {len(inclusion.matched)}")
    table, column = candidate.table, candidate.column
    subject = SourceRef(source=source, schema_name=table.schema, table=table.name)
    out: list[Claim] = []
    if (inclusion.span is not None) != (column.family == TypeFamily.integer and inclusion.distinct > 0):
        raise ValueError(f"値の範囲の幅は整数の列で値があるときだけ: {table.name}.{column.name} の幅 {inclusion.span}"
                         f"（型族 {column.family}）")
    in_span: Sequence[int | None] = ((None,) * len(measured) if inclusion.key_values_in_span is None
                                     else inclusion.key_values_in_span)
    for key, matched, key_values, in_range in zip(measured, inclusion.matched, inclusion.key_values, in_span,
                                                  strict=True):
        where = (f"inclusion {quote(table.schema, table.name)}({quote(column.name)}) in "
                 f"{quote(key.table.schema, key.table.name)}({quote(key.column.name)})")
        out.append(new_claim(
            subject=subject, predicate=ClaimPredicate.included_in, source=ClaimSource(kind=ClaimSourceKind.data, locator=where),
            extracted_by=EXTRACTOR, confidence=MEASURE_CONFIDENCE, columns=[column.name],
            target=SourceRef(source=source, schema_name=key.table.schema, table=key.table.name),
            target_columns=[key.column.name], distinct_values=inclusion.distinct, matched_values=matched,
            target_values=key_values, max_repeats=inclusion.max_repeats,
            value_span=inclusion.span, target_values_in_span=in_range))
    return out
