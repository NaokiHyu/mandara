"""値の似かたの測定（T2.6）のオーケストレーション：列の見本を 1 列 1 回だけ読み、列の組の信号をプロセスを分けて並列に測る。

Application 層。測り方は Domain（mandara.resemblance、mandara.extractors.resemblance）。値はポート（ValueSampler）で読む。
- 信号は互いに独立なので、組ごと・信号ごとに並列に回せる。安い信号は全部の組で、重い信号は安い信号で絞った組だけで測る
- 並列の結果は、回る順に依らない（測り方が決定的で、組と信号で集める）。workers=1 なら同じプロセスで回す（テストと比べるため）
"""

import os
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

from mandara.extractors import resemblance as extractor
from mandara.om.model import Claim
from mandara.resemblance.shape import shape
from mandara.resemblance.signals import BY_NAME, SIGNALS, Measurement, Prepared, prepare
from mandara.resemblance.values import render
from mandara.source.catalog import Catalog
from mandara.source.samples import ValueSampler

Pair = tuple[int, int]
CHUNK = 200
"""1 回にワーカーへ渡す組の数。"""


@dataclass(frozen=True)
class ResemblanceMeasured:
    """測定の数（足切りで落とした組・測らなかった信号は主張にしないので、ここに残す）。"""

    columns: int
    """比べる列（型族の合う、表の列）。"""
    empty: int
    """値の無い列（比べない）。"""
    pairs: int
    """同じ群の列の組。"""
    narrowed: int
    """安い信号で列ごとに近い順に入り、重い信号の候補になった組。"""
    kept: int
    """主張にした組。"""
    too_few_values: dict[str, int]
    """信号ごとの、値が少なくて測らなかった組の数（信号の min_values）。"""
    workers: int
    """実際に使ったプロセスの数（仕事が少なければ 1）。"""


@dataclass(frozen=True)
class MeasuredResemblance:
    claims: list[Claim]
    measured: ResemblanceMeasured


def measure_resemblance(source: str, catalog: Catalog, sampler: ValueSampler, workers: int | None = None,
                        chunk: int = CHUNK) -> MeasuredResemblance:
    """workers を省くと CPU の数。chunk はワーカーへ 1 回に渡す仕事の数（仕事が chunk 以下なら同じプロセスで回す）。"""
    found = extractor.targets(catalog)
    prepared: list[Prepared | None] = []
    claims: list[Claim] = []
    for target in found:
        sample = sampler.sample(target.table.schema, target.table.name, target.column.name, extractor.SAMPLE_LIMIT)
        if not sample.values:
            prepared.append(None)
            continue
        values = [render(v) for v in sample.values]
        p = prepare(values)
        prepared.append(p)
        claims.append(extractor.shape_claim(source, target, shape(values), sample.distinct, len(values)))
    live = [i for i, p in enumerate(prepared) if p is not None]
    columns = [found[i] for i in live]
    ready = tuple(p for p in prepared if p is not None)
    pairs = extractor.pairs(columns)
    n = workers if workers is not None else (os.cpu_count() or 1)
    cheap = [s for s in SIGNALS if not s.costly]
    costly = [s for s in SIGNALS if s.costly]
    measured: dict[Pair, dict[str, Measurement]] = {}
    too_few: dict[str, int] = {s.name: 0 for s in SIGNALS}
    used = 1
    for signals, candidates in ((cheap, pairs), (costly, None)):
        chosen = candidates if candidates is not None else sorted(extractor.top(measured, cheap, extractor.COSTLY_TOP))
        jobs: list[tuple[Pair, str]] = []
        for pair in chosen:
            for s in signals:
                if min(len(ready[pair[0]].values), len(ready[pair[1]].values)) >= s.min_values:
                    jobs.append((pair, s.name))
                else:
                    too_few[s.name] += 1
        results, workers_used = _measure(ready, jobs, n, chunk)
        used = max(used, workers_used)
        _merge(measured, results)
    narrowed = len(extractor.top(measured, cheap, extractor.COSTLY_TOP))
    kept = sorted(extractor.top(measured, SIGNALS, extractor.KEEP_TOP))
    for i, j in kept:
        claims += extractor.resemblance_claims(source, columns[i], columns[j], measured[(i, j)],
                                               len(ready[i].values), len(ready[j].values))
    return MeasuredResemblance(claims, ResemblanceMeasured(len(found), len(found) - len(live), len(pairs), narrowed,
                                                           len(kept), too_few, used))


def _merge(into: dict[Pair, dict[str, Measurement]], found: Sequence[tuple[Pair, str, Measurement]]) -> None:
    for pair, name, m in found:
        into.setdefault(pair, {})[name] = m


Job = tuple[Pair, str]


def _measure(ready: tuple[Prepared, ...], jobs: Sequence[Job], workers: int,
             chunk: int) -> tuple[list[tuple[Pair, str, Measurement]], int]:
    """仕事を測り、結果と使ったプロセスの数を返す。"""
    if workers <= 1 or len(jobs) <= chunk:
        return _run_with(ready, jobs), 1
    chunks = [jobs[i:i + chunk] for i in range(0, len(jobs), chunk)]
    used = min(workers, len(chunks))
    with ProcessPoolExecutor(max_workers=used, initializer=_init, initargs=(ready,)) as pool:
        return [x for part in pool.map(_run, chunks) for x in part], used


def _run_with(ready: tuple[Prepared, ...], jobs: Sequence[Job]) -> list[tuple[Pair, str, Measurement]]:
    return [((i, j), name, BY_NAME[name].measure(ready[i], ready[j])) for (i, j), name in jobs]


# ワーカープロセスの中だけで使う列の見本（プロセスごとに 1 回だけ受け取る。組ごとに見本を送り直さない）。
# 同じプロセスで回すときは使わない（引数で渡す）
_ready: tuple[Prepared, ...] = ()


def _init(ready: tuple[Prepared, ...]) -> None:
    global _ready
    _ready = ready


def _run(jobs: Sequence[Job]) -> list[tuple[Pair, str, Measurement]]:
    return _run_with(_ready, jobs)
