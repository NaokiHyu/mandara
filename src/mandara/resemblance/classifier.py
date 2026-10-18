"""分類器 2 標本検定の分類器：k 近傍の 1 つ抜き（leave-one-out）の正答率と、ラベルの並べ替えによる帰無分布。

Domain 層の純粋な関数（乱数は種を固定。同じ入力なら同じ値）。
- 特徴量は列ごとに標準化する（ばらつきの無い特徴量は 0 にする）。距離はユークリッド
- 近傍は 1 回だけ求める。帰無分布（2 つの列が同じ母集団なら）は、ラベルを並べ替えて同じ近傍で正答率を数え直して作る。
  近傍はラベルに依らないので、並べ替えは安い（並べ替え検定として正確。二項分布の近似は要らない）
- 同じ距離の近傍は、見本の順で先のものを選ぶ（安定な並べ替え）
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

K = 5
PERMUTATIONS = 199
SEED = 0
LEVEL = 0.95
"""帰無分布のこの分位点を基準にする（同じ母集団なら、超える確率は 5%）。"""


@dataclass(frozen=True)
class TwoSample:
    accuracy: float
    """1 つ抜きの k 近傍で、値がどちらの列のものかを当てた割合。"""
    critical: float
    """ラベルを並べ替えた正答率の LEVEL 分位点。"""


def two_sample(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> TwoSample:
    """a と b（同じ数、K // 2 + 2 以上）の値を見分ける k 近傍の正答率と、その基準。"""
    if len(a) != len(b) or len(a) < K // 2 + 2:
        raise ValueError(f"両方の見本は同じ数で {K // 2 + 2} 以上: {len(a)} と {len(b)}")
    x = np.array([*a, *b], dtype=float)
    spread = x.std(axis=0)
    x = np.where(spread > 0, (x - x.mean(axis=0)) / np.where(spread > 0, spread, 1.0), 0.0)
    squared = (x * x).sum(axis=1)
    distance = squared[:, None] + squared[None, :] - 2 * x @ x.T
    np.fill_diagonal(distance, np.inf)
    neighbors = np.argsort(distance, axis=1, kind="stable")[:, :K]
    labels = np.array([0] * len(a) + [1] * len(b))
    rng = np.random.default_rng(SEED)
    null = [_accuracy(neighbors, rng.permutation(labels)) for _ in range(PERMUTATIONS)]
    return TwoSample(_accuracy(neighbors, labels), float(np.quantile(null, LEVEL, method="higher")))


def _accuracy(neighbors: NDArray[np.intp], labels: NDArray[np.int_]) -> float:
    votes = labels[neighbors].sum(axis=1)
    predicted = (2 * votes > K).astype(int)
    return float((predicted == labels).mean())
