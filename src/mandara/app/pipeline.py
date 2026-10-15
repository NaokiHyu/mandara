"""生成パイプライン（仕様 6章）のユースケース：抽出 → 提案 → 評価、と OM の検証。

Application 層。Domain の純粋な関数（抽出器、モデラー、評価、不変条件）と Adapter（コネクタ、ストア）を順に呼ぶ。
コネクタは外から渡す（どの DB かは知らない）。時刻も外から渡す（実行の記録のため。UTC に直して書く）。

作業領域 work/<source>/ の中身（git に乗せない）：
  claims/           主張台帳と、それを作ったときの範囲（scope.json）。同じ置き換えで書くので食い違わない
  ontology/         提案した OM
  eval/report.json  正解 OM との突き合わせ
  runs/<時刻>.json   実行の記録。開始時に running で書き、終わったら succeeded / failed（失敗した段と理由）にする。
                    途中で失敗したとき、どの成果物が今回のものかを記録から判断できるように、段ごとの完了も書く
"""

import hashlib
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from mandara.evaluation.score import Report, score
from mandara.extractors import catalog as catalog_extractor
from mandara.modeler import rules as modeler
from mandara.om.invariants import Violation, check
from mandara.om.model import Claim
from mandara.om.ontology import Ontology
from mandara.omstore.claim_store import load_claim_ledger, load_ledger_scope, write_claim_ledger
from mandara.omstore.json_store import write_json
from mandara.omstore.yaml_store import (
    dump_table,
    dump_vocabulary,
    load_ontology,
    write_ontology,
    write_reviewed_ontology,
)
from mandara.review.decisions import Approve, Decision, Question, apply, questions
from mandara.source.catalog import CatalogReader, Scope

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class Workspace:
    root: Path
    """work/<source>。"""
    source: str

    @property
    def claims(self) -> Path:
        return self.root / "claims"

    @property
    def ontology(self) -> Path:
        return self.root / "ontology"

    @property
    def eval_report(self) -> Path:
        return self.root / "eval" / "report.json"

    @property
    def runs(self) -> Path:
        return self.root / "runs"


@dataclass(frozen=True)
class Extracted:
    claims: tuple[Claim, ...]
    tables: int
    scope: Scope


@dataclass(frozen=True)
class Proposed:
    proposal: modeler.Proposal
    ledger_hash: str
    """提案の入力（主張台帳）の指紋。主張 ID の集合から決まる。主張 ID は内容から決まり、確信度と抽出器の版は
    含まない（モデラーはどちらも読まない）ので、提案の入力の指紋として足りる。"""


def extract(workspace: Workspace, reader: CatalogReader, scope: Scope) -> Extracted:
    """カタログを読み、主張台帳を範囲とともに作り直す。"""
    catalog = reader.read(scope)
    claims = catalog_extractor.extract(workspace.source, catalog)
    write_claim_ledger(workspace.claims, workspace.source, catalog.scope.schemas, claims)
    return Extracted(tuple(claims), len(catalog.tables), catalog.scope)


def propose(workspace: Workspace) -> Proposed:
    """主張台帳から OM を提案して書く。範囲は台帳を作ったときのもの。"""
    claims = load_claim_ledger(workspace.claims, workspace.source)
    proposal = modeler.propose(workspace.source, load_ledger_scope(workspace.claims), claims)
    write_ontology(workspace.ontology, proposal.ontology)
    return Proposed(proposal, _ledger_hash(claims))


def evaluate(workspace: Workspace, gold: Path) -> Report:
    """提案した OM を正解 OM と突き合わせ、結果を書く。"""
    gold_ontology = load_ontology(gold)
    report = score(load_ontology(workspace.ontology), gold_ontology)
    write_json(workspace.eval_report, {"gold": _gold_record(gold, gold_ontology), **report_to_json(report)})
    return report


def read_ontology(root: Path) -> Ontology:
    """見るための OM の読み込み（UI・API から）。"""
    return load_ontology(root)


@dataclass(frozen=True)
class Validated:
    ontology: Ontology
    violations: tuple[Violation, ...]


def validate(ontology_root: Path) -> Validated:
    ontology = load_ontology(ontology_root)
    return Validated(ontology, tuple(check(ontology)))


# ---------------------------------------------------------------- レビュー（仕様 6.4、9.3）


class LedgerMismatchError(Exception):
    """主張台帳と OM が食い違う（OM の根拠の主張が台帳に無い）。台帳を作り直したあとに OM を作り直していない、など。"""


BULK_MINIMUM = 0.95
"""一括承認の閾値の下限（仕様 9.3 の既定）。これより下げてよいかは未決（仕様 15.2）。"""


class BulkThresholdError(Exception):
    """一括承認の閾値が、許す範囲（BULK_MINIMUM 以上 1 以下）の外。"""


@dataclass(frozen=True)
class ReviewQueue:
    questions: tuple[Question, ...]
    evidence: dict[str, str]
    """主張 ID → 人が読める根拠の1行。"""


def review_queue(workspace: Workspace) -> ReviewQueue:
    """まだ判断されていない提案を、問いの列にする。根拠は主張台帳から読める形にして添える。"""
    claims = {c.id: c for c in load_claim_ledger(workspace.claims, workspace.source)}
    queue = tuple(questions(load_ontology(workspace.ontology)))
    wanted = {e for q in queue for e in q.evidence}
    if missing := sorted(wanted - claims.keys()):
        raise LedgerMismatchError(f"提案の根拠の主張が台帳に無い（台帳を作り直したら、OM も作り直す）: {missing[:3]}")
    return ReviewQueue(queue, {cid: _describe(claims[cid]) for cid in wanted})


def answer(workspace: Workspace, location: str, decision: Decision, reviewer: str, on: date) -> None:
    """1つの問いに答え、すぐ書く（途中でやめても、そこまでの判断は残る）。"""
    ontology = load_ontology(workspace.ontology)
    write_reviewed_ontology(workspace.ontology, apply(ontology, location, decision, reviewer, on))


def bulk_candidates(workspace: Workspace, threshold: float) -> list[Question]:
    """一括承認の候補の一覧（仕様 9.3：確信度が閾値以上の提案は一覧で一括承認できる）。種類を選ぶ必要のある表は含めない。

    人は一覧を見てから bulk_approve で確定する。
    """
    if not BULK_MINIMUM <= threshold <= 1.0:
        raise BulkThresholdError(f"一括承認の閾値は {BULK_MINIMUM} 以上 1 以下（指定 {threshold}）。下げてよいかは未決（仕様 15.2）")
    return [q for q in questions(load_ontology(workspace.ontology))
            if q.confidence is not None and q.confidence >= threshold and not q.needs_kind]


def bulk_approve(workspace: Workspace, locations: Sequence[str], threshold: float, reviewer: str, on: date) -> int:
    """一覧で見せた候補（locations）を承認する。今も候補のままであることを確かめる（見せた後に変わっていれば止める）。"""
    candidates = {q.location for q in bulk_candidates(workspace, threshold)}
    if stale := [loc for loc in locations if loc not in candidates]:
        raise LedgerMismatchError(f"見せた一覧と今の候補が食い違う（{len(stale)} 件。例 {stale[:3]}）。一覧を出し直す")
    ontology = load_ontology(workspace.ontology)
    for location in locations:
        ontology = apply(ontology, location, Approve(), reviewer, on)
    if locations:
        write_reviewed_ontology(workspace.ontology, ontology)
    return len(locations)


def _describe(claim: Claim) -> str:
    detail = f" · {claim.text[:60]}" if claim.text else ""
    return f"{claim.predicate} · {claim.source.locator}{detail}"


@dataclass(frozen=True)
class RunResult:
    extracted: Extracted
    proposed: Proposed
    report: Report | None
    record: Path


def run(workspace: Workspace, reader: CatalogReader, scope: Scope, gold: Path | None, clock: Clock) -> RunResult:
    """抽出 → 提案 → （正解があれば）評価を通しで行い、実行の記録を書く。失敗も記録してから伝える。"""
    started = _utc(clock())
    log = _RunLog(workspace.runs / f"{started.strftime('%Y%m%dT%H%M%S%fZ')}.json", clock, {
        "status": "running",
        "started_at": started.isoformat(),
        "source": workspace.source,
        "requested_scope": list(scope.schemas),
        "versions": {"extractor": catalog_extractor.EXTRACTOR, "modeler": modeler.MODELER},
        "completed": [],
    })
    with log.stage("extract"):
        extracted = extract(workspace, reader, scope)
        log.update(scope=list(extracted.scope.schemas), tables=extracted.tables,
                   claims=dict(sorted(Counter(c.predicate for c in extracted.claims).items())))
    with log.stage("propose"):
        proposed = propose(workspace)
        log.update(ledger_hash=proposed.ledger_hash, proposal=_proposal_counts(proposed.proposal.ontology),
                   skipped=[{"claim": s.claim, "reason": s.reason} for s in proposed.proposal.skipped])
    report = None
    if gold is not None:
        with log.stage("evaluate"):
            report = evaluate(workspace, gold)
            log.update(gold=_gold_record(gold, load_ontology(gold)), evaluation={
                a.aspect: {"correct": a.correct, "gold": a.gold, "proposed": a.proposed} for a in report.aspects})
    log.update(status="succeeded", finished_at=_utc(clock()).isoformat())
    return RunResult(extracted, proposed, report, log.path)


class _RunLog:
    """実行の記録。変わるたびに書き直す（途中で落ちても、そこまでが残る）。"""

    def __init__(self, path: Path, clock: Clock, initial: dict[str, Any]) -> None:
        self.path = path
        self._clock = clock
        self._data = dict(initial)
        self._completed: list[str] = []
        write_json(self.path, self._data)

    def update(self, **fields: object) -> None:
        self._data.update(fields)
        write_json(self.path, self._data)

    def stage(self, name: str) -> "_Stage":
        return _Stage(self, name)

    def finish_stage(self, name: str) -> None:
        self._completed.append(name)
        self.update(completed=list(self._completed))

    def fail(self, name: str, error: BaseException) -> None:
        self.update(status="failed", failed_stage=name, error=f"{type(error).__name__}: {error}",
                    finished_at=_utc(self._clock()).isoformat())


class _Stage:
    def __init__(self, log: _RunLog, name: str) -> None:
        self._log = log
        self._name = name

    def __enter__(self) -> None:
        return None

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None, _tb: object) -> None:
        """None を返す（例外を握りつぶさずに伝える）。"""
        if error is not None:
            self._log.fail(self._name, error)
        else:
            self._log.finish_stage(self._name)


def report_to_json(report: Report) -> dict[str, object]:
    return {
        "aspects": [
            {"aspect": a.aspect, "gold": a.gold, "proposed": a.proposed, "correct": a.correct,
             "recall": a.recall, "false_rate": a.false_rate, "missed": list(a.missed), "wrong": list(a.wrong)}
            for a in report.aspects
        ],
        "unscored_tables": report.unscored_tables,
        "unscored_relations": report.unscored_relations,
    }


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError(f"時計がタイムゾーンの無い時刻を返した: {moment}")
    return moment.astimezone(UTC)


def _gold_record(path: Path, gold: Ontology) -> dict[str, object]:
    """どの正解と比べたか。パスと、正規形の内容のハッシュ。"""
    canonical = dump_vocabulary(gold.vocabulary) + "".join(dump_table(t) for t in sorted(gold.tables, key=lambda t: t.id))
    return {"path": str(path), "tables": len(gold.tables),
            "hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]}


def _ledger_hash(claims: tuple[Claim, ...]) -> str:
    return hashlib.sha256("\n".join(sorted(c.id for c in claims)).encode()).hexdigest()[:16]


def _proposal_counts(ontology: Ontology) -> dict[str, object]:
    return {
        "tables": len(ontology.tables),
        "kinds": dict(sorted(Counter(t.kind or "undecided" for t in ontology.tables).items())),
        "relations": sum(len(t.relations or []) for t in ontology.tables),
        "columns": sum(len(t.columns or {}) for t in ontology.tables),
    }
