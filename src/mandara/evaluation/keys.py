"""キーの評価（T2.7）：提案した OM の主キーを、元の DB で宣言されていた主キー（正解）と突き合わせる。

Domain 層の純粋な関数。宣言を抜いた世界（evals/datasets/adventureworks/make_bare.sh 4）で、推定した主キーがどれだけ戻ったかを測る。
- 比べるのは、正解で主キーの宣言のある実体の表だけ。主キーは列の集合として比べる
- 当たり：同じ集合。部分：宣言の真部分集合（データではそれだけで一意。データだけからは区別できない）。外れ：それ以外。
  未決：OM に主キーが無い
- 範囲は呼ぶ側が揃える（正解も提案も、同じスキーマの範囲の表だけ）
"""

from dataclasses import dataclass

from mandara.om.model import ObjectKind
from mandara.om.ontology import Ontology
from mandara.source.catalog import Catalog


@dataclass(frozen=True)
class KeyMiss:
    table: str
    declared: tuple[str, ...]
    proposed: tuple[str, ...] | None


@dataclass(frozen=True)
class KeyScore:
    tables: int
    """正解で主キーの宣言のある表の数。"""
    exact: int
    subset: tuple[KeyMiss, ...]
    wrong: tuple[KeyMiss, ...]
    undecided: tuple[KeyMiss, ...]


def score(ontology: Ontology, truth: Catalog) -> KeyScore:
    proposed = {t.name: frozenset(t.identity.primary) if t.identity is not None else None for t in ontology.tables}
    exact = 0
    subset: list[KeyMiss] = []
    wrong: list[KeyMiss] = []
    undecided: list[KeyMiss] = []
    declared = [t for t in truth.tables if t.kind == ObjectKind.table.value and t.primary_key is not None]
    for t in declared:
        if t.primary_key is None:
            raise AssertionError("主キーの宣言のある表だけを選んだはず")
        key = frozenset(t.primary_key.columns)
        if t.qualified_name not in proposed:
            raise ValueError(f"正解の表 {t.qualified_name} が OM に無い（範囲か世界が違う）")
        found = proposed[t.qualified_name]
        miss = KeyMiss(t.qualified_name, tuple(sorted(key)), tuple(sorted(found)) if found is not None else None)
        if found is None:
            undecided.append(miss)
        elif found == key:
            exact += 1
        elif found < key:
            subset.append(miss)
        else:
            wrong.append(miss)
    return KeyScore(len(declared), exact, tuple(subset), tuple(wrong), tuple(undecided))
