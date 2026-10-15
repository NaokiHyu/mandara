"""UI のための HTTP API（ローカル）。

UI 層の入口。ユースケース（mandara.app.pipeline）を呼び、プロセス境界の契約（①の OntologyDocument）の形で返す。
- 応答の OM は、①から生成した JSON Schema（ui/src/om/om.schema.json）に従う。UI は受け取る側でそれを検証する
- 読むだけ（v0）。既定ではこの機械の中（127.0.0.1）からしか繋げない。Host ヘッダも localhost に限る（DNS リバインディング対策）
- 失敗は、何が無いのか・何が読めなかったのかを本文に書いて返す（黙って空を返さない）
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from mandara.app import pipeline
from mandara.om.model import OntologyDocument, OntologyIndex, OntologyListing, OntologySourceKind
from mandara.om.ontology import Ontology
from mandara.omstore.codec import OmLoadError


@dataclass(frozen=True)
class OntologySource:
    id: str
    """URL に使う ID（例 work:adventureworks、gold:adventureworks）。"""
    label: str
    kind: OntologySourceKind
    root: Path


def discover(work: Path, gold: dict[str, Path]) -> list[OntologySource]:
    """work/<source>/ontology と、指定された正解 OM を、見られる OM の一覧にする。"""
    sources = [OntologySource(f"work:{d.name}", d.name, OntologySourceKind.work, d / "ontology")
               for d in sorted(work.iterdir()) if (d / "ontology").is_dir()] if work.is_dir() else []
    sources += [OntologySource(f"gold:{name}", f"{name}（正解）", OntologySourceKind.gold, path)
                for name, path in sorted(gold.items())]
    return sources


def to_document(ontology: Ontology) -> dict[str, Any]:
    document = OntologyDocument(vocabulary=ontology.vocabulary, tables=list(ontology.tables))
    return document.model_dump(mode="json", by_alias=True, exclude_none=True)


LOCAL_HOSTS = ["127.0.0.1", "localhost", "testserver"]
"""受け付ける Host ヘッダ。testserver は FastAPI の TestClient が名乗る名前。"""


def to_index(sources: list[OntologySource]) -> dict[str, Any]:
    index = OntologyIndex(ontologies=[OntologyListing(id=s.id, label=s.label, kind=s.kind) for s in sources])
    return index.model_dump(mode="json", by_alias=True, exclude_none=True)


def create_app(sources: list[OntologySource]) -> FastAPI:
    by_id = {s.id: s for s in sources}
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

    app.add_api_route("/api/ontologies", list_ontologies, methods=["GET"])
    app.add_api_route("/api/ontologies/{ontology_id}", get_ontology, methods=["GET"])
    return app
