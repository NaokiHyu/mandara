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
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from mandara.app import pipeline
from mandara.app.pipeline import Workspace
from mandara.connectors.postgres.catalog_reader import PostgresCatalogReader
from mandara.evaluation.score import InvalidForScoringError, Report
from mandara.modeler.rules import InvalidInputError
from mandara.omstore.claim_store import InvalidClaimsError, LedgerRefusedError
from mandara.omstore.codec import OmLoadError
from mandara.omstore.tree import UnstorableNameError
from mandara.omstore.yaml_store import InvalidOntologyError, OntologyWriteRefusedError
from mandara.source.catalog import Scope, SourceError

_KNOWN_ERRORS = (SourceError, OmLoadError, InvalidClaimsError, LedgerRefusedError, UnstorableNameError,
                 InvalidOntologyError, OntologyWriteRefusedError, InvalidInputError, InvalidForScoringError)
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


def main(argv: Sequence[str] | None = None, out: TextIO = sys.stdout, err: TextIO = sys.stderr) -> int:
    args = _parser().parse_args(argv)
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
    p.set_defaults(handler=_run)

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

    p = commands.add_parser("validate", help="OM のディレクトリを検証する（仕様 4.5）")
    p.add_argument("ontology", type=Path)
    p.set_defaults(handler=_validate)
    return parser


def _workspace(args: argparse.Namespace) -> Workspace:
    return Workspace(root=args.work / args.source, source=args.source)


def _reader(args: argparse.Namespace) -> PostgresCatalogReader:
    return PostgresCatalogReader(args.conninfo)


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- コマンド


def _run(args: argparse.Namespace, out: TextIO, style: _Style) -> int:
    workspace = _workspace(args)
    scope = Scope(tuple(args.schema))
    _banner(out, style, workspace, scope)
    started = time.monotonic()
    result = pipeline.run(workspace, _reader(args), scope, args.gold, _now)
    _extract_line(out, style, result.extracted)
    _propose_line(out, style, result.proposed)
    if result.report is not None:
        _scoreboard(out, style, result.report, args.gold)
    print(f"  {style.dim('record')}    {result.record}   {style.dim(f'{time.monotonic() - started:.1f}s')}", file=out)
    return 0


def _extract(args: argparse.Namespace, out: TextIO, style: _Style) -> int:
    workspace = _workspace(args)
    scope = Scope(tuple(args.schema))
    _banner(out, style, workspace, scope)
    _extract_line(out, style, pipeline.extract(workspace, _reader(args), scope))
    return 0


def _propose(args: argparse.Namespace, out: TextIO, style: _Style) -> int:
    _propose_line(out, style, pipeline.propose(_workspace(args)))
    return 0


def _eval(args: argparse.Namespace, out: TextIO, style: _Style) -> int:
    _scoreboard(out, style, pipeline.evaluate(_workspace(args), args.gold), args.gold)
    return 0


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


def _propose_line(out: TextIO, style: _Style, proposed: pipeline.Proposed) -> None:
    ontology = proposed.proposal.ontology
    kinds: dict[str, int] = {}
    for t in ontology.tables:
        kinds[t.kind or "undecided"] = kinds.get(t.kind or "undecided", 0) + 1
    breakdown = " · ".join(f"{k} {n}" for k, n in sorted(kinds.items()))
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
