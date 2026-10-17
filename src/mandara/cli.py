"""mandara コマンド。

UI 層（入口）であり、コネクタを組み立てる場所（composition root）。ユースケースは mandara.app.pipeline。
- 失敗は、既知のエラー（データソース、ファイル、検証）なら1行で表に出して終了コード 2。想定外は握りつぶさずに落ちる
- 検証の違反（validate）は終了コード 1
- 色は端末のときだけ。NO_COLOR が設定されていれば付けない
"""

import argparse
import os
import sys
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TextIO, cast

from mandara.app import pipeline
from mandara.app.pipeline import BulkThresholdError, LedgerMismatchError
from mandara.app.pipeline import Workspace
from mandara.connectors.postgres.catalog_reader import PostgresCatalogReader
from mandara.connectors.postgres.column_reader import PostgresColumnReader
from mandara.connectors.postgres.coverage_reader import PostgresCoverageReader
from mandara.connectors.postgres.record_reader import PostgresRecordReader
from mandara.evaluation.score import InvalidForScoringError, Report
from mandara.llm.claude.model import ClaudeModel
from mandara.llm.decision import BudgetedDecisionModel, Choice, DecisionRequest
from mandara.llm.model import Budget, BudgetedModel, LlmError, StructuredRequest, summarize
from mandara.llm.typesafe.model import JevModel
from mandara.settings.llm import LlmSettings, SettingsError, parse_settings
from mandara.settings.llm_prices import UnknownPricingError, load_pricing
from mandara.om.model import TableKind
from mandara.modeler.rules import InvalidInputError
from mandara.omstore.claim_store import InvalidClaimsError, LedgerRefusedError
from mandara.omstore.codec import OmLoadError
from mandara.omstore.tree import UnstorableNameError
from mandara.omstore.yaml_store import InvalidOntologyError, OntologyWriteRefusedError
from mandara.review.decisions import Approve, Decision, Question, Reject, ReviewError
from mandara.source.catalog import Scope, SourceError
from mandara.source.records import RecordReader
from mandara.source.rows import ColumnReader

class UsageError(Exception):
    """コマンドの引数の誤り。"""


_KNOWN_ERRORS = (SourceError, OmLoadError, InvalidClaimsError, LedgerRefusedError, UnstorableNameError,
                 InvalidOntologyError, OntologyWriteRefusedError, InvalidInputError, InvalidForScoringError,
                 ReviewError, LedgerMismatchError, BulkThresholdError, UsageError, LlmError, SettingsError,
                 UnknownPricingError)
"""データ・ファイル・操作に原因のある失敗。これ以外（モデラーの出力が検証を通らない InvalidProposalError、
pydantic の ValidationError など）はバグなので、捕まえずにトレースバックごと落とす。"""


class _Style:
    def __init__(self, out: TextIO) -> None:
        self.on = out.isatty() and "NO_COLOR" not in os.environ

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.on else text

    def accent(self, text: str) -> str:
        return self._wrap("38;5;208;1", text)  # 橙（コンソールルームの警告灯）

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def good(self, text: str) -> str:
        return self._wrap("38;5;114", text)

    def bad(self, text: str) -> str:
        return self._wrap("38;5;203", text)


def main(
    argv: Sequence[str] | None = None, out: TextIO = sys.stdout, err: TextIO = sys.stderr, inp: TextIO = sys.stdin
) -> int:
    args = _parser().parse_args(argv)
    args.input = inp
    style = _Style(out)
    try:
        return args.handler(args, out, style)
    except _KNOWN_ERRORS as e:
        print(f"{style.bad('error')}  {e}", file=err)
        return 2


# ---------------------------------------------------------------- 引数


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mandara", description="データソースから OM を提案する")
    commands = parser.add_subparsers(required=True, metavar="command")

    def source_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--source", required=True, help="データソースの名前（work/<source> になる）")
        p.add_argument("--work", type=Path, default=Path("work"), help="作業領域（git に乗せない。既定 work）")

    def connection_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--schema", action="append", required=True, help="読むスキーマ（繰り返し指定）")
        p.add_argument("--conninfo", default="",
                       help="libpq の接続文字列。省略時は環境変数（PGHOST、PGDATABASE など）。秘密はここに書かない")

    p = commands.add_parser("run", help="抽出 → 提案 → 評価を通しで行い、実行の記録を書く")
    source_args(p)
    connection_args(p)
    p.add_argument("--gold", type=Path, help="正解 OM のディレクトリ（あれば評価する）")
    p.add_argument("--llm", action="store_true", help="LLM で推論する（.env の設定、1 回の予算の範囲で）")
    p.add_argument("--env", type=Path, default=Path(".env"), help="LLM の設定のファイル（既定 .env）。環境変数が優先")
    p.set_defaults(handler=_run)

    p = commands.add_parser("refine", help="提案した OM を LLM で詰める（.env の設定、1 回の予算の範囲で）")
    source_args(p)
    p.add_argument("--first", action="append", default=[], metavar="SCHEMA.TABLE", help="先に問う表（繰り返し指定）")
    p.add_argument("--env", type=Path, default=Path(".env"))
    p.set_defaults(handler=_refine)

    p = commands.add_parser("extract", help="カタログを読み、主張台帳を作り直す")
    source_args(p)
    connection_args(p)
    p.set_defaults(handler=_extract)

    p = commands.add_parser("propose", help="主張台帳から OM を提案する")
    source_args(p)
    p.set_defaults(handler=_propose)

    p = commands.add_parser("eval", help="提案した OM を正解 OM と突き合わせる")
    source_args(p)
    p.add_argument("--gold", type=Path, required=True)
    p.set_defaults(handler=_eval)

    p = commands.add_parser("review", help="提案に答える（承認・却下）。答えるたびに OM に書く")
    source_args(p)
    p.add_argument("--reviewer", required=True, help="レビューする人の名前（reviewed_by に書く）")
    p.add_argument("--bulk", type=float, metavar="THRESHOLD",
                   help="確信度がこの値以上の提案を一覧で見せ、確認してから一括で承認する（0.95 以上）")
    p.set_defaults(handler=_review)

    p = commands.add_parser("serve", help="UI のための HTTP API を立てる（--reviewer があればレビューの答えを書ける）")
    p.add_argument("--work", type=Path, help="作業領域（work/<source>/ontology を出す。既定 work。指定したなら存在すること）")
    p.add_argument("--gold", action="append", default=[], metavar="NAME=DIR", help="正解 OM（繰り返し指定）")
    p.add_argument("--connect", nargs=2, action="append", default=[], metavar=("SOURCE", "CONNINFO"),
                   help="ソースの接続（地図の点などの実データを読む）。CONNINFO は libpq の接続文字列。繰り返し指定。"
                        "パスワードは書かない（ps で見える）。~/.pgpass か PGPASSFILE を使う")
    p.add_argument("--reviewer", help="画面からのレビューの答えを、この名前で書く（reviewed_by）。無ければ答えは書けない")
    p.add_argument("--dev", action="store_true",
                   help="開発用：画面に全承認を出す（中身を見ずに固定する。承認済みの状態を試すためだけ）。--reviewer が要る")
    p.add_argument("--host", default="127.0.0.1", help="既定はこの機械の中だけ")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(handler=_serve)

    p = commands.add_parser("llm-check", help="LLM と判断モデルの設定と接続を確かめる（本物の API をそれぞれ1回呼ぶ。費用は予算の範囲）")
    p.add_argument("--env", type=Path, default=Path(".env"), help="設定のファイル（既定 .env）。環境変数が優先")
    p.set_defaults(handler=_llm_check)

    p = commands.add_parser("validate", help="OM のディレクトリを検証する（仕様 4.5）")
    p.add_argument("ontology", type=Path)
    p.set_defaults(handler=_validate)
    return parser


def _workspace(args: argparse.Namespace) -> Workspace:
    return Workspace(root=args.work / args.source, source=args.source)


def _reader(args: argparse.Namespace) -> PostgresCatalogReader:
    return PostgresCatalogReader(args.conninfo)


def _values(args: argparse.Namespace) -> PostgresColumnReader:
    return PostgresColumnReader(args.conninfo)


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- コマンド


def _run(args: argparse.Namespace, out: TextIO, style: _Style) -> int:
    workspace = _workspace(args)
    scope = Scope(tuple(args.schema))
    _banner(out, style, workspace, scope)
    started = time.monotonic()
    llm = _llm(args.env) if args.llm else None
    result = pipeline.run(workspace, _reader(args), _values(args), PostgresCoverageReader(args.conninfo), scope, args.gold, _now, llm[0] if llm else None)
    _extract_line(out, style, result.extracted)
    _propose_line(out, style, result.proposed)
    if result.refined is not None and llm is not None:
        _refine_line(out, style, result.refined, *llm)
    if result.report is not None:
        _scoreboard(out, style, result.report, args.gold)
    print(f"  {style.dim('record')}    {result.record}   {style.dim(f'{time.monotonic() - started:.1f}s')}", file=out)
    return 0


def _extract(args: argparse.Namespace, out: TextIO, style: _Style) -> int:
    workspace = _workspace(args)
    scope = Scope(tuple(args.schema))
    _banner(out, style, workspace, scope)
    _extract_line(out, style, pipeline.extract(workspace, _reader(args), scope, _values(args), PostgresCoverageReader(args.conninfo)))
    return 0


def _propose(args: argparse.Namespace, out: TextIO, style: _Style) -> int:
    _propose_line(out, style, pipeline.propose(_workspace(args)))
    return 0


def _eval(args: argparse.Namespace, out: TextIO, style: _Style) -> int:
    _scoreboard(out, style, pipeline.evaluate(_workspace(args), args.gold), args.gold)
    return 0


_KIND_KEYS = {"d": TableKind.domain, "r": TableKind.relation, "e": TableKind.event}


def _review(args: argparse.Namespace, out: TextIO, style: _Style) -> int:
    workspace = _workspace(args)
    today = _now().date()
    inp: TextIO = args.input
    if args.bulk is not None:
        return _bulk(out, style, inp, workspace, args.bulk, args.reviewer, today)

    queue = pipeline.review_queue(workspace)
    total = len(queue.questions)
    print(f"{style.accent('◆ REVIEW')}  {workspace.source}  {total:,} pending", file=out)
    answered = 0
    try:
        for i, q in enumerate(queue.questions, 1):
            outcome = _ask(out, style, inp, workspace, queue, q, f"[{i}/{total}]", args.reviewer, today)
            if outcome == "stop":
                break
            answered += outcome == "answered"
    except KeyboardInterrupt:  # 答えるたびに書いているので、ここまでの判断は残っている
        print(file=out)
    return _review_summary(out, style, answered, total)


def _bulk(out: TextIO, style: _Style, inp: TextIO, workspace: Workspace, threshold: float, reviewer: str,
          today: date) -> int:
    candidates = pipeline.bulk_candidates(workspace, threshold)
    print(f"{style.accent('◆ REVIEW')}  bulk ≥ {threshold}: {len(candidates):,} candidates", file=out)
    for q in candidates:  # 一覧で見せる（仕様 9.3）
        print(f"   {style.dim(f'{q.confidence:.2f}')}  {q.text}", file=out)
    if not candidates:
        return 0
    print(f"   approve all {len(candidates):,}? [y/N] > ", end="", file=out, flush=True)
    if inp.readline().strip().lower() != "y":
        print(f"{style.accent('◆')} cancelled", file=out)
        return 0
    approved = pipeline.bulk_approve(workspace, [q.location for q in candidates], threshold, reviewer, today)
    print(f"{style.accent('◆')} {approved:,} approved", file=out)
    return 0


def _ask(out: TextIO, style: _Style, inp: TextIO, workspace: Workspace, queue: pipeline.ReviewQueue,
         q: Question, progress: str, reviewer: str, today: date) -> str:
    """1つの問いを出して答えを当てる。"answered" / "skipped" / "stop" を返す。"""
    confidence = "" if q.confidence is None else style.dim(f"conf {q.confidence:.2f}")
    print(f"\n{style.dim(progress)}  {q.text}   {confidence}", file=out)
    shown = q.evidence_shown
    for j, claim_id in enumerate(shown):
        branch = "└" if j == len(shown) - 1 else "├"
        print(style.dim(f"   {branch} {queue.evidence[claim_id]}"), file=out)
    keys = "[d]omain [r]elation [e]vent" if q.needs_kind else "[y]es"
    while True:
        print(f"   {keys}  [n]o  [s]kip  [q]uit > ", end="", file=out, flush=True)
        line = inp.readline()
        if not line:  # 入力の終わり
            print(file=out)
            return "stop"
        key = line.strip().lower()
        decision: Decision | None = None
        if key == "q":
            return "stop"
        if key == "s":
            return "skipped"
        if key == "n":
            decision = Reject()
        elif not q.needs_kind and key == "y":
            decision = Approve()
        elif q.needs_kind and key in _KIND_KEYS:
            decision = Approve(kind=_KIND_KEYS[key])
        if decision is None:
            print(style.bad("   ?"), file=out)
            continue
        try:
            pipeline.answer(workspace, q.location, decision, reviewer, today)
        except ReviewError as e:  # この答えは当てられない。選び直してもらう
            print(style.bad(f"   {e}"), file=out)
            continue
        return "answered"


def _review_summary(out: TextIO, style: _Style, answered: int, total: int) -> int:
    print(f"{style.accent('◆')} {answered} answered · {total - answered} left", file=out)
    return 0


def _serve(args: argparse.Namespace, out: TextIO, style: _Style) -> int:
    import uvicorn

    from mandara.api.server import Reviewing, create_app, discover

    gold: dict[str, Path] = {}
    for item in args.gold:
        name, sep, path = item.partition("=")
        if not sep or not name or not path:
            raise UsageError(f"--gold は NAME=DIR の形: {item!r}")
        if name in gold:
            raise UsageError(f"--gold の NAME が重複している: {name}")
        if not Path(path).is_dir():
            raise UsageError(f"--gold のディレクトリが無い: {path}")
        gold[name] = Path(path)
    if args.work is not None and not args.work.is_dir():
        raise UsageError(f"--work のディレクトリが無い: {args.work}")
    sources = discover(args.work or Path("work"), gold)
    known = {t.source for s in sources for t in pipeline.read_ontology(s.root).tables}
    readers: dict[str, ColumnReader] = {}
    records: dict[str, RecordReader] = {}
    for name, conninfo in args.connect:
        if name in readers:
            raise UsageError(f"--connect の SOURCE が重複している: {name}")
        if name not in known:
            raise UsageError(f"--connect の SOURCE {name} は、どの OM の表のソースでもない（ある: {sorted(known)}）")
        readers[name] = PostgresColumnReader(conninfo)
        records[name] = PostgresRecordReader(conninfo)
    if args.reviewer is not None and not args.reviewer.strip():
        raise UsageError("--reviewer は空にしない")
    if args.dev and args.reviewer is None:
        raise UsageError("--dev には --reviewer が要る（全承認を誰の名前で書くか）")
    reviewing = Reviewing(args.reviewer, lambda: _now().date(), args.dev) if args.reviewer is not None else None
    print(f"{style.accent('◆ MANDARA')}  serving {len(sources)} ontologies on http://{args.host}:{args.port}", file=out)
    print(style.dim(f"   review as {args.reviewer}" if reviewing else "   review: read only (--reviewer NAME to answer)"),
          file=out)
    if args.dev:
        print(f"   {style.bad('dev')}  approve-all is enabled", file=out)
    for s in sources:
        print(style.dim(f"   {s.id:<28} {s.root}"), file=out)
    for name in readers:
        print(style.dim(f"   connect {name}"), file=out)
    uvicorn.run(create_app(sources, readers, reviewing, records), host=args.host, port=args.port, log_level="warning")
    return 0


def _refine(args: argparse.Namespace, out: TextIO, style: _Style) -> int:
    inference, settings = _llm(args.env)
    refined = pipeline.refine(_workspace(args), inference.model, inference.decider, args.first)
    _refine_line(out, style, refined, inference, settings)
    return 0


def _refine_line(out: TextIO, style: _Style, refined: pipeline.Refined, inference: pipeline.Inference,
                 settings: LlmSettings) -> None:
    counts: dict[str, int] = {}
    for o in refined.outcomes:
        counts[o.status] = counts.get(o.status, 0) + 1
    spent = inference.budget.spent_usd
    breakdown = " · ".join(f"{k} {n}" for k, n in sorted(counts.items()))
    print(f"  {style.dim('refine')}    {breakdown}  {style.dim(f'{inference.decider.model} + {inference.model.model}')}  "
          f"¥{spent * settings.jpy_per_usd:.2f} / ¥{settings.budget_jpy:g}", file=out)
    if failures := sum(len(o.stage_failures) for o in refined.outcomes):
        print(f"            {style.bad(f'{failures} stage failures')}  (the items of those stages were dropped)", file=out)
    for o in refined.outcomes:
        if o.status != "refined" or o.stage_failures:
            print(style.dim(f"            {o.status:<7} {o.table}  {o.detail or (o.dropped[0].reason if o.dropped else '')}"),
                  file=out)
    if refined.stopped is not None:
        print(f"            {style.bad('stopped')}  {refined.stopped}  ({len(refined.not_reached)} tables left as v0)",
              file=out)


def _llm(env_file: Path) -> tuple[pipeline.Inference, LlmSettings]:
    """.env と環境変数から、予算を共有する LLM と判断モデルを組み立てる（composition root）。"""
    from dotenv import dotenv_values

    # .env は任意（無ければ環境変数だけ）。足りない設定は parse_settings が名前を挙げてエラーにする
    from_file = dotenv_values(env_file) if env_file.is_file() else {}
    settings = parse_settings({**from_file, **os.environ})
    budget = Budget(settings.budget_usd)
    model = BudgetedModel(ClaudeModel.from_api_key(settings.model, settings.api_key), load_pricing(settings.model),
                          budget)
    decider = BudgetedDecisionModel(JevModel.from_api_key(settings.decision_model, settings.decision_api_key),
                                    load_pricing(settings.decision_model), budget)
    return pipeline.Inference(model, decider), settings


def _llm_check(args: argparse.Namespace, out: TextIO, style: _Style) -> int:
    inference, _ = _llm(args.env)
    llm = inference.model
    request = StructuredRequest(
        purpose="llm-check", system="あなたは接続の確認に答える。", prompt="ok に true を入れて答える。",
        schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        max_output_tokens=50,
    )
    response = llm.ask(request)
    data: object = response.data
    ok = isinstance(data, dict) and cast(dict[str, object], data).get("ok") is True
    decided = inference.decider.decide(DecisionRequest(
        purpose="llm-check decision", state={"message": "This is a connection check."},
        questions={"check": Choice("Is the `message` a connection check?", {"yes": None, "no": None})}))
    decision_ok = decided.answers["check"].choice == "yes"
    for label, model_name, good in (("LLM", response.model, ok), ("DECISION", decided.model, decision_ok)):
        spent = summarize([s for s in inference.budget.spends
                           if s.model == (inference.model.model if label == "LLM" else inference.decider.model)])
        print(f"{style.accent(f'◆ {label}')}  {model_name}  {style.good('ok') if good else style.bad('unexpected answer')}  "
              f"{spent['input_tokens']} in / {spent['output_tokens']} out  ${spent['cost_usd']}", file=out)
    print(style.dim(f"   budget ${inference.budget.limit_usd:.4f}"), file=out)
    return 0 if ok and decision_ok else 1


def _validate(args: argparse.Namespace, out: TextIO, style: _Style) -> int:
    validated = pipeline.validate(args.ontology)
    ontology, violations = validated.ontology, validated.violations
    for v in violations:
        print(f"  {style.bad(v.rule.name):<24} {v.location}  {style.dim(v.detail)}", file=out)
    verdict = style.good("valid") if not violations else style.bad(f"{len(violations)} violations")
    print(f"{style.accent('◆')} {args.ontology}  {len(ontology.tables)} tables  {verdict}", file=out)
    return 1 if violations else 0


# ---------------------------------------------------------------- 表示


def _banner(out: TextIO, style: _Style, workspace: Workspace, scope: Scope) -> None:
    print(f"{style.accent('◆ MANDARA')}  {workspace.source}  {style.dim(' '.join(scope.schemas))}", file=out)


def _extract_line(out: TextIO, style: _Style, extracted: pipeline.Extracted) -> None:
    print(f"  {style.dim('extract')}   {extracted.tables} objects → {len(extracted.claims):,} claims", file=out)
    for e in extracted.examined:
        where = f"{e.table}.{e.column}"
        if e.detection.encoding is not None:
            print(f"  {style.dim('encoding')}  {where} → {e.detection.encoding}  "
                  f"{style.dim(f'{e.detection.values} 件を読めた')}", file=out)
        else:
            print(f"  {style.dim('encoding')}  {where} → 決まらない: {e.detection.reason}", file=out)
    for n in extracted.not_examined:
        print(f"  {style.dim('encoding')}  {n.table}.{n.column} → 見ていない: {n.reason}", file=out)
    for c in extracted.covered:
        facts = [f"{x.discriminator} ∈ {{{', '.join(x.discriminator_values or [])}}}" if x.discriminator else "すべての行"
                 for x in c.claims]
        print(f"  {style.dim('shared')}    {c.table} → {c.target}  {c.covered:,}/{c.rows:,} 行"
              + (f"  {'; '.join(facts)}" if facts else ""), file=out)


def _propose_line(out: TextIO, style: _Style, proposed: pipeline.Proposed) -> None:
    ontology = proposed.proposal.ontology
    kinds: dict[str, int] = {}
    for t in ontology.tables:
        kinds[t.kind or "undecided"] = kinds.get(t.kind or "undecided", 0) + 1
    breakdown = " · ".join(f"{k} {n}" for k, n in sorted(kinds.items()))
    derived = sum(t.derived is True for t in ontology.tables)
    breakdown += f"; derived {derived}" if derived else ""
    relations = sum(len(t.relations or []) for t in ontology.tables)
    skipped = len(proposed.proposal.skipped)
    tail = f" · {style.bad(f'{skipped} skipped')}" if skipped else ""
    print(f"  {style.dim('propose')}   {len(ontology.tables)} tables ({breakdown}) · {relations} relations{tail}",
          file=out)


def _scoreboard(out: TextIO, style: _Style, report: Report, gold: Path) -> None:
    print(f"  {style.dim('evaluate')}  vs {gold}", file=out)
    width = 20
    for a in report.aspects:
        recall = a.recall
        filled = 0 if recall is None else round(recall * width)
        bar = style.good("█" * filled) + style.dim("·" * (width - filled))
        false = "      " if a.false_rate is None else (
            style.good("err 0%") if a.false_rate == 0 else style.bad(f"err {a.false_rate:.0%}"))
        print(f"            {a.aspect:<20} {bar} {a.correct:>4}/{a.gold:<4} {false}", file=out)
    if report.unscored_tables or report.unscored_relations:
        print(style.dim(f"            outside gold: {report.unscored_tables} tables, "
                        f"{report.unscored_relations} relations (not scored)"), file=out)


if __name__ == "__main__":
    sys.exit(main())
