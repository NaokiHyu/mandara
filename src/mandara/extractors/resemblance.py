"""値の似かたの測定（T2.6）：列の値の形（shaped_as）と、列の組の信号（resembles）→ 主張（source.kind: data）。

Domain 層の純粋な関数。値を読むのは呼ぶ側（ValueSampler）、組を並列に測るのも呼ぶ側（Application）。
- 比べる列：表（ビューではない）の列で、型族が比べられるもの。組は同じ群（GROUPS）の中だけ
- 信号は mandara.resemblance.signals のプラグイン。安い信号は全部の組で、重い信号は安い信号のどれかで列ごとに近い順
  COSTLY_TOP 個に入った組だけで測る（順位だけで決める。閾値で落とさない）
- 主張にするのは、どれかの信号で列ごとに近い順 KEEP_TOP 個に入った組。その組で測った信号は全部主張にする
  （近くない信号も、否定の証拠として残る）。落とした組の数は呼ぶ側が記録に残す
- 測った値は小数 4 桁に丸めて主張にする（主張 ID が浮動小数の末尾の揺れで変わらないように）。形の割合は切り捨て（和が 1 を超えない）
"""

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from mandara.extractors.catalog import quote
from mandara.om.claims import new_claim
from mandara.om.model import Claim, ClaimPredicate, ClaimSource, ClaimSourceKind, ObjectKind, SourceRef, TypeFamily
from mandara.resemblance.shape import Shape
from mandara.resemblance.signals import SIGNALS, Measurement, Signal, closeness, method
from mandara.source.catalog import Catalog, Column, Table

EXTRACTOR = "resemblance 0.1"
MEASURE_CONFIDENCE = 1.0
"""測った事実（読んだ時点の見本について）。"""
SAMPLE_LIMIT = 256
"""1 列について読む異なる値の見本の数。"""
COSTLY_TOP = 8
KEEP_TOP = 5
DIGITS = 4
GROUPS: dict[str, frozenset[TypeFamily]] = {
    "text": frozenset({TypeFamily.text}),
    "uuid": frozenset({TypeFamily.uuid}),
    "integer": frozenset({TypeFamily.integer}),
    "number": frozenset({TypeFamily.decimal, TypeFamily.float}),
    "time": frozenset({TypeFamily.date, TypeFamily.timestamp}),
}
"""比べてよい型族の群（真偽・バイト列・XML などは比べない。真偽は値が 2 つしかなく、形の手がかりにならない）。"""
_GROUP_OF = {family: name for name, families in GROUPS.items() for family in families}


def group_of(family: TypeFamily) -> str | None:
    """型族の群（比べない型族なら None）。"""
    return _GROUP_OF.get(family)


@dataclass(frozen=True)
class Target:
    table: Table
    column: Column

    @property
    def group(self) -> str:
        return _GROUP_OF[TypeFamily(self.column.family)]

    @property
    def key(self) -> tuple[str, str, str]:
        """組の向きを決める名前の順。"""
        return (self.table.schema, self.table.name, self.column.name)


def targets(catalog: Catalog) -> list[Target]:
    """比べる列（表の順、列の順）。"""
    return [Target(t, c) for t in catalog.tables if t.kind == ObjectKind.table.value for c in t.columns
            if TypeFamily(c.family) in _GROUP_OF]


def pairs(found: Sequence[Target]) -> list[tuple[int, int]]:
    """同じ群の列の組（i の名前が j より前）。"""
    order = sorted(range(len(found)), key=lambda i: found[i].key)
    out: list[tuple[int, int]] = []
    for x, i in enumerate(order):
        for j in order[x + 1:]:
            if found[i].group == found[j].group:
                out.append((i, j))
    return out


Pair = tuple[int, int]


def top(measured: Mapping[Pair, Mapping[str, Measurement]], signals: Iterable[Signal], k: int) -> set[Pair]:
    """信号ごとに、列ごとの近い順 k 個に入った組（同じ近さなら相手の番号の順。決定的）。"""
    out: set[Pair] = set()
    for signal in signals:
        partners: dict[int, list[tuple[float, int, Pair]]] = {}
        for pair, by_signal in measured.items():
            m = by_signal.get(signal.name)
            if m is None:
                continue
            c = closeness(signal, m)
            i, j = pair
            partners.setdefault(i, []).append((-c, j, pair))
            partners.setdefault(j, []).append((-c, i, pair))
        for ranked in partners.values():
            out |= {pair for _, _, pair in sorted(ranked)[:k]}
    return out


def shape_claim(source: str, target: Target, found: Shape, distinct: int, sampled: int) -> Claim:
    table, column = target.table, target.column
    return new_claim(
        subject=SourceRef(source=source, schema_name=table.schema, table=table.name, column=column.name),
        predicate=ClaimPredicate.shaped_as,
        source=ClaimSource(kind=ClaimSourceKind.data, locator=f"values {quote(table.schema, table.name)}"
                                                              f"({quote(column.name)})"),
        extracted_by=EXTRACTOR, confidence=MEASURE_CONFIDENCE,
        patterns=[p for p, _ in found.patterns], pattern_shares=[_down(s) for _, s in found.patterns],
        shape_level=found.level, distinct_values=distinct, sampled_values=sampled)


def _down(share: float) -> float:
    """割合を小数 DIGITS 桁に切り捨てる（丸めで和が 1 を超えないように）。"""
    return math.floor(share * 10 ** DIGITS) / 10 ** DIGITS


def resemblance_claims(source: str, a: Target, b: Target, measured: Mapping[str, Measurement], sampled_a: int,
                       sampled_b: int) -> list[Claim]:
    """組の信号ごとに 1 つ（SIGNALS の順）。a の名前が b より前。"""
    if a.key >= b.key:
        raise ValueError(f"組の向きは名前の順: {a.key} と {b.key}")
    where = (f"resemblance {quote(a.table.schema, a.table.name)}({quote(a.column.name)}) ~ "
             f"{quote(b.table.schema, b.table.name)}({quote(b.column.name)})")
    out: list[Claim] = []
    for signal in SIGNALS:
        m = measured.get(signal.name)
        if m is None:
            continue
        out.append(new_claim(
            subject=SourceRef(source=source, schema_name=a.table.schema, table=a.table.name),
            predicate=ClaimPredicate.resembles, source=ClaimSource(kind=ClaimSourceKind.data, locator=where),
            extracted_by=EXTRACTOR, confidence=MEASURE_CONFIDENCE, columns=[a.column.name],
            target=SourceRef(source=source, schema_name=b.table.schema, table=b.table.name),
            target_columns=[b.column.name], method=method(signal), score=round(m.score, DIGITS),
            baseline=round(m.baseline, DIGITS), sampled_values=sampled_a, target_sampled_values=sampled_b))
    return out
