"""UI のための HTTP API（ローカル）。

UI 層の入口。ユースケース（mandara.app.pipeline）を呼び、プロセス境界の契約（①の OntologyDocument）の形で返す。
- 応答の OM は、①から生成した JSON Schema（ui/src/om/om.schema.json）に従う。UI は受け取る側でそれを検証する
- 地図の点は、接続を渡されたソースからだけ読む（ゼロコピー。上限つき）。接続の無いソースは 409 で理由を返す
- 既定ではこの機械の中（127.0.0.1）からしか繋げない。Host ヘッダも localhost に限る（DNS リバインディング対策）
- 書くのはレビューの答えだけ（仕様 9.3）。CLI と同じユースケース（pipeline.answer / bulk_approve）を呼ぶ。
  表ごとの一括承認（pipeline.approve_table）と、開発用の全承認（pipeline.approve_all。serve --dev のときだけ）は画面だけ。
  書けるのは work の OM で、serve に --reviewer を渡したときだけ。正解 OM には書かない。
  書く要求は JSON（application/json）に限り、Origin があればこの機械のものに限る（別のサイトから書かせない）。
  書き込みはこのプロセスの中で1つずつ（読んで、当てて、書く、の間に別の書き込みを挟まない）。
  同じ作業領域に CLI の mandara review を同時に走らせることは守らない（作業領域のロックは未実装。同時に使わない）
- 失敗は、何が無いのか・何が読めなかったのかを本文に書いて返す（黙って空を返さない）
"""

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import urlsplit

from fastapi import FastAPI, Query, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from mandara.app import browse, pipeline
from mandara.om.model import (
    AllApproval,
    BulkApproval,
    EvidenceLine,
    GeoPointRow,
    OntologyDocument,
    OntologyIndex,
    OntologyListing,
    OntologySourceKind,
    PointFailure,
    PointSet,
    ReviewAnswer,
    ReviewDecision,
    ReviewQuestion,
    ReviewQueue,
    TableApproval,
    TableKind,
)
from mandara.om.ontology import Ontology
from mandara.omstore.codec import OmLoadError
from mandara.omstore.yaml_store import InvalidOntologyError, OntologyWriteRefusedError
from mandara.review.decisions import Approve, Decision, Reject, ReviewError, is_bulk_candidate
from mandara.source.catalog import SourceError
from mandara.source.records import RecordReader
from mandara.source.rows import ColumnReader

POINT_LIMIT = 50_000
"""地図に出す点の上限の既定。"""


@dataclass(frozen=True)
class OntologySource:
    id: str
    """URL に使う ID（例 work:adventureworks、gold:adventureworks）。"""
    label: str
    kind: OntologySourceKind
    root: Path
    workspace: pipeline.Workspace | None = None
    """作業領域（work の OM だけ）。レビューの問いと答えはここを通す。"""


def discover(work: Path, gold: dict[str, Path]) -> list[OntologySource]:
    """work/<source>/ontology と、指定された正解 OM を、見られる OM の一覧にする。"""
    sources = [OntologySource(f"work:{d.name}", d.name, OntologySourceKind.work, d / "ontology",
                              pipeline.Workspace(d, d.name))
               for d in sorted(work.iterdir()) if (d / "ontology").is_dir()] if work.is_dir() else []
    sources += [OntologySource(f"gold:{name}", f"{name}（正解）", OntologySourceKind.gold, path)
                for name, path in sorted(gold.items())]
    return sources


@dataclass(frozen=True)
class Reviewing:
    """レビューの答えを書くための設定（mandara serve --reviewer）。"""

    reviewer: str
    today: Callable[[], date]
    """reviewed_at に書く日付（時刻は外から渡す）。"""
    approve_all: bool = False
    """開発用の全承認を受け付ける（mandara serve --dev）。"""


def to_document(ontology: Ontology) -> dict[str, Any]:
    document = OntologyDocument(vocabulary=ontology.vocabulary, tables=list(ontology.tables))
    return document.model_dump(mode="json", by_alias=True, exclude_none=True)


LOCAL_HOSTS = ["127.0.0.1", "localhost", "testserver"]
"""受け付ける Host ヘッダ。testserver は FastAPI の TestClient が名乗る名前。"""


def to_index(sources: list[OntologySource]) -> dict[str, Any]:
    index = OntologyIndex(ontologies=[OntologyListing(id=s.id, label=s.label, kind=s.kind) for s in sources])
    return index.model_dump(mode="json", by_alias=True, exclude_none=True)


def to_point_set(points: pipeline.Points) -> dict[str, Any]:
    point_set = PointSet(
        table=points.table, column=points.column, nulls=points.nulls, truncated=points.truncated,
        rows_read=points.rows_read, limit=points.limit,
        points=[GeoPointRow(key=list(key), lat=p.lat, lon=p.lon) for key, p in points.points],
        failed=[PointFailure(key=list(key), reason=reason) for key, reason in points.failed],
    )
    return point_set.model_dump(mode="json", by_alias=True, exclude_none=True)


NOT_WRITABLE = "答えを書くには mandara serve --reviewer NAME で立てる"


def to_review_queue(source: OntologySource, queue: pipeline.ReviewQueue, reviewing: Reviewing | None) -> dict[str, Any]:
    writable = reviewing is not None
    contract = ReviewQueue(
        ontology=source.id,
        pending=len(queue.questions),
        writable=writable,
        not_writable_reason=None if writable else NOT_WRITABLE,
        reviewer=reviewing.reviewer if reviewing is not None else None,
        bulk_minimum=pipeline.BULK_MINIMUM,
        approve_all=reviewing is not None and reviewing.approve_all,
        questions=[ReviewQuestion(
            location=q.location, table=q.table, subject=q.subject, text=q.text, confidence=q.confidence,
            evidence=[EvidenceLine(id=e, text=queue.evidence[e]) for e in q.evidence_shown],
            evidence_total=len(q.evidence), needs_kind=q.needs_kind,
            bulk_candidate=is_bulk_candidate(q, pipeline.BULK_MINIMUM),
        ) for q in queue.questions],
    )
    return contract.model_dump(mode="json", by_alias=True, exclude_none=True)


def to_decision(answer: ReviewAnswer) -> Decision:
    """答えの種類は全列挙する（①に値が増えたら、知らない答えとして止める。黙って承認にしない）。"""
    match ReviewDecision(answer.decision):
        case ReviewDecision.approve:
            return Approve(kind=TableKind(answer.kind) if answer.kind is not None else None)
        case ReviewDecision.reject:
            if answer.kind is not None:
                raise ReviewError("却下の答えに種類は付けない")
            return Reject()


LOCAL_ORIGIN_HOSTS = {"127.0.0.1", "localhost"}


def _write_refused(request: Request) -> JSONResponse | None:
    """書く要求の入口の確かめ。JSON だけ（フォームの送信では書かせない）、Origin があればこの機械のものだけ。"""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        return JSONResponse({"error": f"書く要求は application/json だけ（{content_type or 'なし'}）"}, status_code=415)
    origin = request.headers.get("origin")
    if origin is not None and urlsplit(origin).hostname not in LOCAL_ORIGIN_HOSTS:
        return JSONResponse({"error": f"この機械の外の Origin からは書けない: {origin}"}, status_code=403)
    return None


def create_app(sources: list[OntologySource], readers: dict[str, ColumnReader] | None = None,
               reviewing: Reviewing | None = None, records: dict[str, RecordReader] | None = None,
               today: Callable[[], date] = date.today) -> FastAPI:
    """readers はソースの名前（表の source）→ 列を読むもの。無いソースの点は読めない。
    records はソースの名前 → 行を読むもの（ドメインビューワ）。無いソースのインスタンスは読めない。
    today はドメインビューワの「今日」（近隣の集まりの最新の日が今日か）。
    reviewing が無ければ、レビューの問いは読めるが答えは書けない。"""
    readers = readers or {}
    records = records or {}
    by_id = {s.id: s for s in sources}
    write_lock = threading.Lock()
    app = FastAPI(title="Mandara", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)

    def list_ontologies() -> JSONResponse:
        return JSONResponse(to_index(sources))

    def get_ontology(ontology_id: str) -> JSONResponse:
        source = by_id.get(ontology_id)
        if source is None:
            return JSONResponse({"error": f"OM が無い: {ontology_id}", "known": sorted(by_id)}, status_code=404)
        try:
            ontology = pipeline.read_ontology(source.root)
        except OmLoadError as e:
            return JSONResponse({"error": f"OM を読めなかった: {e}"}, status_code=500)
        return JSONResponse(to_document(ontology))

    def get_points(ontology_id: str, table_id: str, column: str, limit: int = POINT_LIMIT) -> JSONResponse:
        source = by_id.get(ontology_id)
        if source is None:
            return JSONResponse({"error": f"OM が無い: {ontology_id}"}, status_code=404)
        if not 1 <= limit <= POINT_LIMIT:
            return JSONResponse({"error": f"limit は 1 以上 {POINT_LIMIT} 以下"}, status_code=422)
        try:
            ontology = pipeline.read_ontology(source.root)
            table = ontology.table(table_id)
            if table is None:
                return JSONResponse({"error": f"表が無い: {table_id}"}, status_code=404)
            reader = readers.get(table.source)
            if reader is None:
                return JSONResponse({"error": f"ソース {table.source} の接続が無い（mandara serve --connect "
                                              f"{table.source} CONNINFO）"}, status_code=409)
            points = pipeline.read_points(ontology, table_id, column, reader, limit)
        except pipeline.PointsError as e:
            return JSONResponse({"error": str(e)}, status_code=422)
        except (OmLoadError, SourceError) as e:
            return JSONResponse({"error": f"読めなかった: {e}"}, status_code=502)
        return JSONResponse(to_point_set(points))

    def browsing(ontology_id: str, table_id: str) -> tuple[Ontology, RecordReader] | JSONResponse:
        source = by_id.get(ontology_id)
        if source is None:
            return JSONResponse({"error": f"OM が無い: {ontology_id}"}, status_code=404)
        ontology = pipeline.read_ontology(source.root)
        table = ontology.table(table_id)
        if table is None:
            return JSONResponse({"error": f"表が無い: {table_id}"}, status_code=404)
        reader = records.get(table.source)
        if reader is None:
            return JSONResponse({"error": f"ソース {table.source} の接続が無い（mandara serve --connect "
                                          f"{table.source} CONNINFO）"}, status_code=409)
        return ontology, reader

    def get_domains(ontology_id: str) -> JSONResponse:
        source = by_id.get(ontology_id)
        if source is None:
            return JSONResponse({"error": f"OM が無い: {ontology_id}", "known": sorted(by_id)}, status_code=404)
        try:
            ontology = pipeline.read_ontology(source.root)
        except OmLoadError as e:
            return JSONResponse({"error": f"OM を読めなかった: {e}"}, status_code=500)
        try:
            index = browse.domain_index(ontology, ontology_id, set(records))
        except browse.InvalidOntologyError as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        return JSONResponse(index.model_dump(mode="json", exclude_none=True))

    def get_rows(ontology_id: str, table_id: str, q: str | None = None, limit: int = browse.SEARCH_LIMIT,
                 within_table: str | None = None, within_key: Annotated[list[str] | None, Query()] = None,
                 within_relation: str | None = None, within_via: str | None = None) -> JSONResponse:
        given = [within_table is not None, within_key is not None, within_relation is not None]
        if any(given) and not all(given) or within_via is not None and not all(given):
            return JSONResponse({"error": "within_table・within_key・within_relation はそろえて渡す"
                                          "（within_via は n:m のときにそれらと一緒に）"}, status_code=422)
        within: browse.WithinParent | None = None
        if all(given) and within_via is None:
            within = browse.WithinChild(cast(str, within_table), tuple(within_key or []), cast(str, within_relation))
        elif all(given):
            within = browse.WithinVia(cast(str, within_table), tuple(within_key or []), cast(str, within_relation),
                                      cast(str, within_via))
        if not 1 <= limit <= browse.SEARCH_LIMIT:
            return JSONResponse({"error": f"limit は 1 以上 {browse.SEARCH_LIMIT} 以下"}, status_code=422)
        try:
            found = browsing(ontology_id, table_id)
            if isinstance(found, JSONResponse):
                return found
            ontology, reader = found
            rows = browse.collection(ontology, table_id, reader, today(), q, within, limit)
        except browse.InvalidOntologyError as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        except browse.NotFoundError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        except browse.BrowseError as e:
            return JSONResponse({"error": str(e)}, status_code=422)
        except OmLoadError as e:
            return JSONResponse({"error": f"OM を読めなかった: {e}"}, status_code=500)
        except SourceError as e:
            return JSONResponse({"error": f"読めなかった: {e}"}, status_code=502)
        return JSONResponse(rows.model_dump(mode="json", exclude_none=True))

    def get_instance(ontology_id: str, table_id: str, key: Annotated[list[str], Query()]) -> JSONResponse:
        try:
            found = browsing(ontology_id, table_id)
            if isinstance(found, JSONResponse):
                return found
            ontology, reader = found
            shown = browse.instance(ontology, table_id, key, reader, today())
        except browse.InvalidOntologyError as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        except browse.NotFoundError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        except browse.BrowseError as e:
            return JSONResponse({"error": str(e)}, status_code=422)
        except OmLoadError as e:
            return JSONResponse({"error": f"OM を読めなかった: {e}"}, status_code=500)
        except SourceError as e:
            return JSONResponse({"error": f"読めなかった: {e}"}, status_code=502)
        return JSONResponse(shown.model_dump(mode="json", exclude_none=True))

    def reviewable(ontology_id: str) -> tuple[OntologySource, pipeline.Workspace] | JSONResponse:
        source = by_id.get(ontology_id)
        if source is None:
            return JSONResponse({"error": f"OM が無い: {ontology_id}", "known": sorted(by_id)}, status_code=404)
        if source.workspace is None:
            return JSONResponse({"error": f"{ontology_id} はレビューしない（レビューするのは work の OM だけ）"},
                                status_code=409)
        return source, source.workspace

    def queue_response(source: OntologySource, workspace: pipeline.Workspace) -> JSONResponse:
        try:
            queue = pipeline.review_queue(workspace)
        except pipeline.LedgerMismatchError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        except OmLoadError as e:
            return JSONResponse({"error": f"OM を読めなかった: {e}"}, status_code=500)
        return JSONResponse(to_review_queue(source, queue, reviewing))

    def get_review(ontology_id: str) -> JSONResponse:
        found = reviewable(ontology_id)
        return found if isinstance(found, JSONResponse) else queue_response(*found)

    async def body_of(request: Request) -> object:
        return await request.json()

    def write(ontology_id: str, request: Request, act: Callable[[pipeline.Workspace, Reviewing], None]) -> JSONResponse:
        if (refused := _write_refused(request)) is not None:
            return refused
        found = reviewable(ontology_id)
        if isinstance(found, JSONResponse):
            return found
        if reviewing is None:
            return JSONResponse({"error": NOT_WRITABLE}, status_code=403)
        source, workspace = found
        try:
            with write_lock:
                act(workspace, reviewing)
        except (ReviewError, pipeline.LedgerMismatchError, OntologyWriteRefusedError, InvalidOntologyError) as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        except pipeline.BulkThresholdError as e:
            return JSONResponse({"error": str(e)}, status_code=422)
        except OmLoadError as e:
            return JSONResponse({"error": f"OM を読めなかった: {e}"}, status_code=500)
        after = queue_response(source, workspace)
        if after.status_code != 200:
            # 書いたあとで、問いの列を作り直せなかった。書けなかったのとは違う（選び直させない）
            return JSONResponse({"error": "答えは書いた。ただし問いの列を読み直せなかった: "
                                          + json.loads(bytes(after.body))["error"], "written": True},
                                status_code=500)
        return after

    async def post_answer(ontology_id: str, request: Request) -> JSONResponse:
        try:
            answer = ReviewAnswer.model_validate(await body_of(request))
        except ValueError as e:  # JSON でない本文、契約に合わない本文
            return JSONResponse({"error": f"答えの形が違う: {e}"}, status_code=422)

        def act(workspace: pipeline.Workspace, r: Reviewing) -> None:
            pipeline.answer(workspace, answer.location, to_decision(answer), r.reviewer, r.today())
        return await run_in_threadpool(write, ontology_id, request, act)

    async def post_bulk(ontology_id: str, request: Request) -> JSONResponse:
        try:
            bulk = BulkApproval.model_validate(await body_of(request))
        except ValueError as e:
            return JSONResponse({"error": f"一括承認の形が違う: {e}"}, status_code=422)

        def act(workspace: pipeline.Workspace, r: Reviewing) -> None:
            pipeline.bulk_approve(workspace, bulk.locations, bulk.threshold, r.reviewer, r.today())
        return await run_in_threadpool(write, ontology_id, request, act)

    async def post_all(ontology_id: str, request: Request) -> JSONResponse:
        if reviewing is None or not reviewing.approve_all:
            return JSONResponse({"error": "全承認は開発用（mandara serve --dev のときだけ）"}, status_code=403)
        try:
            batch = AllApproval.model_validate(await body_of(request))
        except ValueError as e:
            return JSONResponse({"error": f"全承認の形が違う: {e}"}, status_code=422)

        def act(workspace: pipeline.Workspace, r: Reviewing) -> None:
            pipeline.approve_all(workspace, batch.locations, r.reviewer, r.today())
        return await run_in_threadpool(write, ontology_id, request, act)

    async def post_table(ontology_id: str, request: Request) -> JSONResponse:
        try:
            batch = TableApproval.model_validate(await body_of(request))
        except ValueError as e:
            return JSONResponse({"error": f"表ごとの承認の形が違う: {e}"}, status_code=422)

        def act(workspace: pipeline.Workspace, r: Reviewing) -> None:
            pipeline.approve_table(workspace, batch.table, batch.locations, r.reviewer, r.today())
        return await run_in_threadpool(write, ontology_id, request, act)

    app.add_api_route("/api/ontologies/{ontology_id}/review", get_review, methods=["GET"])
    app.add_api_route("/api/ontologies/{ontology_id}/review/answers", post_answer, methods=["POST"])
    app.add_api_route("/api/ontologies/{ontology_id}/review/bulk", post_bulk, methods=["POST"])
    app.add_api_route("/api/ontologies/{ontology_id}/review/table", post_table, methods=["POST"])
    app.add_api_route("/api/ontologies/{ontology_id}/review/all", post_all, methods=["POST"])
    app.add_api_route("/api/ontologies", list_ontologies, methods=["GET"])
    app.add_api_route("/api/ontologies/{ontology_id}/tables/{table_id}/points", get_points, methods=["GET"])
    app.add_api_route("/api/ontologies/{ontology_id}/domains", get_domains, methods=["GET"])
    app.add_api_route("/api/ontologies/{ontology_id}/domains/{table_id}/rows", get_rows, methods=["GET"])
    app.add_api_route("/api/ontologies/{ontology_id}/domains/{table_id}/instance", get_instance, methods=["GET"])
    app.add_api_route("/api/ontologies/{ontology_id}", get_ontology, methods=["GET"])
    return app
