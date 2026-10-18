"""UI のための HTTP API と、プロセス境界の契約。

- 応答の OM は、①から生成した JSON Schema（UI が検証に使うもの）に必ず従う（生成した OM すべてで）
- 見られる OM の一覧、無い OM は 404（知っている ID を添える）、読めない OM は 500（理由を添える）
"""

import json
import shutil
from pathlib import Path
from typing import Any, cast

import jsonschema.validators
from fastapi.testclient import TestClient
from hypothesis import given, settings

from mandara.api.server import create_app, discover, to_document
from mandara.om.ontology import Ontology
from mandara.omstore.yaml_store import load_ontology

from tests.strategies import ontologies

ROOT = Path(__file__).parent.parent
GOLD = ROOT / "evals/datasets/adventureworks/gold/ontology"
def _validator(name: str) -> Any:
    """スキーマが宣言する版の検証器。format（date など）も検証する。"""
    schema: dict[str, Any] = json.loads((ROOT / "ui/src/om" / name).read_text())
    cls = jsonschema.validators.validator_for(schema)
    return cls(schema, format_checker=cls.FORMAT_CHECKER)


VALIDATOR = _validator("om.schema.json")
INDEX_VALIDATOR = _validator("index.schema.json")


@settings(max_examples=50)
@given(ontologies())
def test_documents_follow_the_contract(ontology: Ontology) -> None:
    errors = [e.message for e in VALIDATOR.iter_errors(to_document(ontology))]
    assert errors == []


def _get(client: TestClient, path: str) -> tuple[int, Any]:
    """TestClient の型が解決できない（starlette が httpx から移行中）ので、ここに閉じ込める。"""
    response = cast(Any, client).get(path)
    return int(response.status_code), response.json()


def _client(tmp_path: Path) -> TestClient:
    work = tmp_path / "work"
    shutil.copytree(GOLD, work / "copy/ontology")
    (work / "empty").mkdir()  # ontology の無いディレクトリは出さない
    return TestClient(create_app(discover(work, {"adventureworks": GOLD})))


def test_list_and_get(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _, listed = _get(client, "/api/ontologies")
    assert [e.message for e in INDEX_VALIDATOR.iter_errors(listed)] == []
    assert [(o["id"], o["kind"]) for o in listed["ontologies"]] == [("work:copy", "work"), ("gold:adventureworks", "gold")]
    # レビューの列を読めるかはサーバが決めて一覧に出す（作業領域の OM だけ）。読めない OM のレビューは 409
    assert [o["reviewable"] for o in listed["ontologies"]] == [True, False]

    status, document = _get(client, "/api/ontologies/gold:adventureworks")
    assert status == 200
    assert [e.message for e in VALIDATOR.iter_errors(document)] == []
    assert len(document["tables"]) == len(load_ontology(GOLD).tables)


def test_unknown_ontology_is_404(tmp_path: Path) -> None:
    status, body = _get(_client(tmp_path), "/api/ontologies/gold:nothing")
    assert status == 404 and "gold:adventureworks" in body["known"]


def test_unreadable_ontology_is_500_with_reason(tmp_path: Path) -> None:
    client = _client(tmp_path)
    (tmp_path / "work/copy/ontology/vocabulary.yaml").write_text("- not a mapping\n")
    status, body = _get(client, "/api/ontologies/work:copy")
    assert status == 500 and "最上位が辞書ではない" in body["error"]


def test_dates_are_checked_by_the_contract() -> None:
    document = to_document(load_ontology(GOLD))
    document["tables"][0]["reviewed_at"] = "not-a-date"
    assert any("date" in e.message for e in VALIDATOR.iter_errors(document))


def test_foreign_host_is_refused(tmp_path: Path) -> None:
    """DNS リバインディング：外部の名前で来た要求には答えない。"""
    response = cast(Any, _client(tmp_path)).get("/api/ontologies", headers={"host": "evil.example"})
    assert response.status_code == 400
