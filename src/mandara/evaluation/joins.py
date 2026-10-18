"""結合の評価（T2.2）：提案した OM の関係の結合を、元の DB で宣言されていた外部キー（正解）と突き合わせる。

Domain 層の純粋な関数。宣言を抜いた世界（evals/datasets/adventureworks/make_bare.sh）で、推定した結合がどれだけ戻ったかを測る。
- 結合は名前で比べる：（表、列の並び）→（参照先の表、参照先の列の並び）。列の組は対応を保ったまま並べ替えて比べる
- 再現率 = 正解の結合のうち、提案にあるもの。適合率 = 提案した結合のうち、正解にあるもの（どちらも 0 件なら None）
- 1 列の結合と複合の結合を分けて数える（推定は 1 列のキーへの参照だけを測るので、複合は今は戻らない）
- 範囲は呼ぶ側が揃える（正解も提案も、同じスキーマの範囲の表だけ）
"""

from collections.abc import Iterable
from dataclasses import dataclass

from mandara.om.ontology import SELF, Ontology
from mandara.source.catalog import Catalog

Join = tuple[str, tuple[tuple[str, str], ...], str]
"""（表の名前、（列, 参照先の列）の組の並び、参照先の表の名前）。"""


def _join(table: str, columns: Iterable[str], target: str, target_columns: Iterable[str]) -> Join:
    return table, tuple(sorted(zip(columns, target_columns, strict=True))), target


def truth(catalog: Catalog) -> set[Join]:
    """元の DB で宣言されていた外部キー（範囲内の表から範囲内の表へ）。"""
    names = {(t.schema, t.name) for t in catalog.tables}
    return {_join(f"{t.schema}.{t.name}", fk.columns, f"{fk.target_schema}.{fk.target_table}", fk.target_columns)
            for t in catalog.tables for fk in t.foreign_keys if (fk.target_schema, fk.target_table) in names}


def proposed(ontology: Ontology) -> set[Join]:
    """提案した OM の、列の等値で結ぶ関係。"""
    out: set[Join] = set()
    for t in ontology.tables:
        for r in t.relations or []:
            if not r.via:
                continue
            target = t if r.target == SELF else ontology.table(r.target)
            if target is None:
                raise ValueError(f"関係 {r.id} の参照先 {r.target} が OM に無い（検証を通った OM のはず）")
            out.add(_join(t.name, (p.from_ for p in r.via), target.name, (p.to for p in r.via)))
    return out


@dataclass(frozen=True)
class Part:
    truth: int
    proposed: int
    correct: int

    @property
    def recall(self) -> float | None:
        return None if self.truth == 0 else self.correct / self.truth

    @property
    def precision(self) -> float | None:
        return None if self.proposed == 0 else self.correct / self.proposed


@dataclass(frozen=True)
class JoinScore:
    single: Part
    """1 列の結合。"""
    composite: Part
    """複合の結合。"""
    missed: tuple[Join, ...]
    """正解にあって提案に無い結合（名前の順）。"""
    wrong: tuple[Join, ...]
    """提案にあって正解に無い結合（名前の順）。"""


def score_joins(found: set[Join], expected: set[Join]) -> JoinScore:
    def part(single: bool) -> Part:
        t = {j for j in expected if (len(j[1]) == 1) == single}
        p = {j for j in found if (len(j[1]) == 1) == single}
        return Part(len(t), len(p), len(t & p))
    return JoinScore(part(True), part(False), tuple(sorted(expected - found)), tuple(sorted(found - expected)))
