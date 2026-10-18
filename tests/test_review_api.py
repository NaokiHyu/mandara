"""レビューの HTTP API（仕様 9.3、T3.5）。

- 問いの列は契約（review.schema.json）に従う
- 完了条件：画面（API）で答えても、CLI と同じユースケースで答えても、同じ OM になる（失敗も同じ）
- 書けるのは work の OM で、--reviewer があるときだけ。書く要求は JSON だけ、Origin はこの機械のものだけ
"""

import json
import shutil
import tempfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any, cast

import jsonschema.validators
import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

from mandara.api.server import Reviewing, create_app, discover
from mandara.app import pipeline
from mandara.app.pipeline import Workspace
from mandara.connectors.postgres.catalog_reader import PostgresCatalogReader
from mandara.connectors.postgres.column_reader import PostgresColumnReader
from mandara.connectors.postgres.coverage_reader import PostgresCoverageReader
from mandara.connectors.postgres.inclusion_reader import PostgresInclusionReader
from mandara.review.decisions import Approve, Decision, Reject, ReviewError
from mandara.om.model import TableKind
from mandara.omstore.yaml_store import load_ontology
from mandara.source.catalog import Scope

ROOT = Path(__file__).parent.parent
GOLD = ROOT / "evals/datasets/adventureworks/gold/ontology"
TODAY = date(2026, 10, 15)
SCOPE = Scope(("person", "sales"))


def _validator(name: str) -> Any:
    schema: dict[str, Any] = json.loads((ROOT / "ui/src/om" / name).read_text())
    cls = jsonschema.validators.validator_for(schema)
    return cls(schema, format_checker=cls.FORMAT_CHECKER)


REVIEW_VALIDATOR = _validator("review.schema.json")


@pytest.fixture(scope="module")
def prepared() -> Iterator[Path]:
    """抽出と提案を済ませた作業領域（work/adventureworks）。テストごとに写して使う。"""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "work"
        ws = Workspace(work / "adventureworks", "adventureworks")
        pipeline.extract(ws, PostgresCatalogReader("dbname=adventureworks"), SCOPE,
                         PostgresColumnReader("dbname=adventureworks"), PostgresCoverageReader("dbname=adventureworks"),
                         PostgresInclusionReader("dbname=adventureworks"), None)
        pipeline.propose(ws)
        yield work


def _copy(prepared: Path, to: Path) -> Path:
    shutil.copytree(prepared, to)
    return to


def _client(work: Path, reviewer: str | None = "alice", dev: bool = False) -> TestClient:
    reviewing = Reviewing(reviewer, lambda: TODAY, dev) if reviewer is not None else None
    return TestClient(create_app(discover(work, {"adventureworks": GOLD}), reviewing=reviewing))


def _request(client: TestClient, method: str, path: str, body: object = None,
             headers: dict[str, str] | None = None) -> tuple[int, Any]:
    """TestClient の型が解決できない（starlette が httpx から移行中）ので、ここに閉じ込める。"""
    c = cast(Any, client)
    if method == "GET":
        response = c.get(path)
    else:
        response = c.post(path, content=json.dumps(body), headers={"content-type": "application/json", **(headers or {})})
    return int(response.status_code), response.json()


QUEUE = "/api/ontologies/work:adventureworks/review"


def test_queue_follows_the_contract(prepared: Path, tmp_path: Path) -> None:
    client = _client(_copy(prepared, tmp_path / "work"))
    status, queue = _request(client, "GET", QUEUE)
    assert status == 200
    assert [e.message for e in REVIEW_VALIDATOR.iter_errors(queue)] == []
    assert queue["writable"] and queue["reviewer"] == "alice" and queue["pending"] == len(queue["questions"]) > 0
    assert all(len(q["evidence"]) <= 3 and q["evidence_total"] >= len(q["evidence"]) for q in queue["questions"])
    assert any(q["bulk_candidate"] for q in queue["questions"])


def test_gold_is_not_reviewed(prepared: Path, tmp_path: Path) -> None:
    client = _client(_copy(prepared, tmp_path / "work"))
    status, body = _request(client, "GET", "/api/ontologies/gold:adventureworks/review")
    assert status == 409 and "work の OM だけ" in body["error"]
    status, _ = _request(client, "POST", "/api/ontologies/gold:adventureworks/review/answers",
                         {"location": "x", "decision": "reject"})
    assert status == 409


def test_without_reviewer_the_queue_is_read_only(prepared: Path, tmp_path: Path) -> None:
    client = _client(_copy(prepared, tmp_path / "work"), reviewer=None)
    _, queue = _request(client, "GET", QUEUE)
    assert not queue["writable"] and "--reviewer" in queue["not_writable_reason"]
    status, body = _request(client, "POST", f"{QUEUE}/answers",
                            {"location": queue["questions"][0]["location"], "decision": "reject"})
    assert status == 403 and "--reviewer" in body["error"]


def test_writes_need_json_from_this_machine(prepared: Path, tmp_path: Path) -> None:
    client = _client(_copy(prepared, tmp_path / "work"))
    _, queue = _request(client, "GET", QUEUE)
    body = {"location": queue["questions"][0]["location"], "decision": "reject"}
    form = cast(Any, client).post(f"{QUEUE}/answers", content=json.dumps(body), headers={"content-type": "text/plain"})
    assert form.status_code == 415
    status, _ = _request(client, "POST", f"{QUEUE}/answers", body, {"origin": "https://evil.example"})
    assert status == 403
    status, after = _request(client, "POST", f"{QUEUE}/answers", body, {"origin": "http://127.0.0.1:5173"})
    assert status == 200 and after["pending"] == queue["pending"] - 1


def test_malformed_answers_are_rejected(prepared: Path, tmp_path: Path) -> None:
    client = _client(_copy(prepared, tmp_path / "work"))
    for body in [{"location": "x"}, {"location": "x", "decision": "maybe"}, {"location": "x", "decision": "reject", "extra": 1}]:
        status, _ = _request(client, "POST", f"{QUEUE}/answers", body)
        assert status == 422
    status, body = _request(client, "POST", f"{QUEUE}/answers", {"location": "nowhere", "decision": "reject"})
    assert status == 409 and "注釈が無い" in body["error"]


# ---------------------------------------------------------------- CLI と画面で同じ結果


def _snapshot(work: Path) -> dict[str, bytes]:
    root = work / "adventureworks" / "ontology"
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


Choice = tuple[int, str]
"""（問いの番号、答え）。答えは approve / reject / approve:<種類>。"""

choices = st.lists(st.tuples(st.integers(0, 400), st.sampled_from(
    ["approve", "reject", *(f"approve:{k.value}" for k in TableKind)])), min_size=1, max_size=8)


def _decision(choice: str) -> tuple[Decision, dict[str, Any]]:
    if choice == "reject":
        return Reject(), {"decision": "reject"}
    if choice == "approve":
        return Approve(), {"decision": "approve"}
    kind = choice.split(":")[1]
    return Approve(TableKind(kind)), {"decision": "approve", "kind": kind}


@settings(max_examples=12, deadline=None)
@given(answers=choices)
def test_cli_and_screen_give_the_same_ontology(prepared: Path, answers: list[Choice]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        via_cli = _copy(prepared, Path(tmp) / "cli")
        via_api = _copy(prepared, Path(tmp) / "api")
        client = _client(via_api)
        workspace = Workspace(via_cli / "adventureworks", "adventureworks")
        for index, choice in answers:
            questions = pipeline.review_queue(workspace).questions
            location = questions[index % len(questions)].location
            decision, body = _decision(choice)
            try:
                pipeline.answer(workspace, location, decision, "alice", TODAY)
                cli_ok = True
            except ReviewError:
                cli_ok = False
            status, _ = _request(client, "POST", f"{QUEUE}/answers", {"location": location, **body})
            assert (status == 200) == cli_ok, (location, choice, status)
            assert status in (200, 409)
        assert _snapshot(via_cli) == _snapshot(via_api)


def test_bulk_approval_matches_the_cli(prepared: Path, tmp_path: Path) -> None:
    via_cli, via_api = _copy(prepared, tmp_path / "cli"), _copy(prepared, tmp_path / "api")
    workspace = Workspace(via_cli / "adventureworks", "adventureworks")
    candidates = [q.location for q in pipeline.bulk_candidates(workspace, pipeline.BULK_MINIMUM)]
    pipeline.bulk_approve(workspace, candidates, pipeline.BULK_MINIMUM, "alice", TODAY)

    client = _client(via_api)
    _, queue = _request(client, "GET", QUEUE)
    shown = [q["location"] for q in queue["questions"] if q["bulk_candidate"]]
    assert shown == candidates
    status, after = _request(client, "POST", f"{QUEUE}/bulk", {"threshold": queue["bulk_minimum"], "locations": shown})
    assert status == 200 and not any(q["bulk_candidate"] for q in after["questions"])
    assert _snapshot(via_cli) == _snapshot(via_api)

    # 見せた一覧が古ければ（もう承認済み）止める
    status, body = _request(client, "POST", f"{QUEUE}/bulk", {"threshold": queue["bulk_minimum"], "locations": shown[:1]})
    assert status == 409 and "一覧を出し直す" in body["error"]
    status, _ = _request(client, "POST", f"{QUEUE}/bulk", {"threshold": 0.5, "locations": []})
    assert status == 422


def test_a_failed_reload_after_writing_is_not_reported_as_a_refusal(prepared: Path, tmp_path: Path) -> None:
    """書いた後に問いの列を作り直せなくても、「答えを当てられなかった」とは言わない（画面が答え直させないように）。"""
    work = _copy(prepared, tmp_path / "work")
    client = _client(work)
    _, queue = _request(client, "GET", QUEUE)
    location = queue["questions"][0]["location"]
    # 台帳を壊す：答えは書けるが、問いの列（根拠を台帳から読む）は作れない
    for claim_file in (work / "adventureworks" / "claims").rglob("*.yaml"):
        claim_file.write_text("broken: [\n")
    status, body = _request(client, "POST", f"{QUEUE}/answers", {"location": location, "decision": "reject"})
    assert status == 500 and body["written"] is True and body["error"].startswith("答えは書いた")
    ontology = pipeline.read_ontology(work / "adventureworks" / "ontology")
    assert dict(ontology.annotations())[location].status == "rejected"


def test_table_approval_approves_what_was_shown_for_that_table(prepared: Path, tmp_path: Path) -> None:
    work = _copy(prepared, tmp_path / "work")
    client = _client(work)
    _, queue = _request(client, "GET", QUEUE)
    table = next((q["table"] for q in queue["questions"] if q.get("table") and not q["needs_kind"]), None)
    assert table is not None
    shown = [q["location"] for q in queue["questions"] if q.get("table") == table and not q["needs_kind"]]
    kept = [q["location"] for q in queue["questions"] if q.get("table") == table and q["needs_kind"]]
    assert shown
    status, after = _request(client, "POST", f"{QUEUE}/table", {"table": table, "locations": shown})
    assert status == 200
    remaining = {q["location"] for q in after["questions"]}
    assert not remaining & set(shown) and set(kept) <= remaining  # 種類を選ぶ問いは残る
    approved = dict(load_ontology(work / "adventureworks" / "ontology").annotations())
    assert all(approved[loc].status == "approved" and approved[loc].reviewed_by == "alice" for loc in shown)

    # 見せた一覧が古い（もう承認済み）、他の表の問い、空の一覧は止める。止めたときは何も書かない
    status, body = _request(client, "POST", f"{QUEUE}/table", {"table": table, "locations": shown[:1]})
    assert status == 409 and "一覧を出し直す" in body["error"]
    other = next((q for q in after["questions"] if q.get("table") not in (None, table) and not q["needs_kind"]), None)
    assert other is not None
    other_table = [q["location"] for q in after["questions"] if q.get("table") == other["table"] and not q["needs_kind"]]
    before = _snapshot(work)
    # 正しい場所に、古い場所や別の表の場所を混ぜても、全体を止めて何も書かない
    for mixed in ([*other_table, shown[0]], [*other_table, kept[0]] if kept else [*other_table, "nope"]):
        status, _ = _request(client, "POST", f"{QUEUE}/table", {"table": other["table"], "locations": mixed})
        assert status == 409 and _snapshot(work) == before
    status, _ = _request(client, "POST", f"{QUEUE}/table", {"table": table, "locations": [other["location"]]})
    assert status == 409 and _snapshot(work) == before
    status, _ = _request(client, "POST", f"{QUEUE}/table", {"table": table, "locations": []})
    assert status == 409
    status, _ = _request(client, "POST", f"{QUEUE}/table", {"table": "tbl_nope", "locations": shown})
    assert status == 409


def test_approve_all_is_only_for_dev_and_leaves_the_kind_questions(prepared: Path, tmp_path: Path) -> None:
    work = _copy(prepared, tmp_path / "work")
    _, queue = _request(_client(work), "GET", QUEUE)
    assert queue["approve_all"] is False
    shown = [q["location"] for q in queue["questions"] if not q["needs_kind"]]
    kind = [q["location"] for q in queue["questions"] if q["needs_kind"]]
    before = _snapshot(work)
    status, _ = _request(_client(work), "POST", f"{QUEUE}/all", {"locations": shown})
    assert status == 403 and _snapshot(work) == before  # --dev でなければ受け付けない

    client = _client(work, dev=True)
    _, queue = _request(client, "GET", QUEUE)
    assert queue["approve_all"] is True
    status, _ = _request(client, "POST", f"{QUEUE}/all", {"locations": shown[1:]})  # 見せた一覧が今と違う
    assert status == 409 and _snapshot(work) == before
    status, after = _request(client, "POST", f"{QUEUE}/all", {"locations": shown})
    assert status == 200 and sorted(q["location"] for q in after["questions"]) == sorted(kind)
    approved = dict(load_ontology(work / "adventureworks" / "ontology").annotations())
    assert all(approved[loc].status == "approved" and approved[loc].reviewed_by == "alice" for loc in shown)


DOMAINS_VALIDATOR = _validator("domains.schema.json")
ROWS_VALIDATOR = _validator("domain_rows.schema.json")
INSTANCE_VALIDATOR = _validator("instance.schema.json")


def test_domain_viewer_follows_the_contracts(prepared: Path, tmp_path: Path) -> None:
    """承認した作業領域を、ドメインビューワの API で読む。応答は契約に従い、接続の無いソースは読めないと答える。"""
    from mandara.connectors.postgres.record_reader import PostgresRecordReader

    work = _copy(prepared, tmp_path / "work")
    workspace = Workspace(work / "adventureworks", "adventureworks")
    for q in pipeline.review_queue(workspace).questions:
        if q.needs_kind:  # 種類を選ぶ表は domain と答える（規則だけの提案は種類が未定の表が多い）
            pipeline.answer(workspace, q.location, Approve(TableKind.domain), "alice", TODAY)
    shown = [q.location for q in pipeline.review_queue(workspace).questions if not q.needs_kind]
    pipeline.approve_all(workspace, shown, "alice", TODAY)
    reviewing = Reviewing("alice", lambda: TODAY)
    client = TestClient(create_app(discover(work, {}), reviewing=reviewing,
                                   records={"adventureworks": PostgresRecordReader("dbname=adventureworks")}))
    path = "/api/ontologies/work:adventureworks/domains"
    status, index = _request(client, "GET", path)
    assert status == 200 and index["readable"] and index["domains"]
    DOMAINS_VALIDATOR.validate(index)
    person = next(d for d in index["domains"] if d["name"] == "person.person")
    # 規則だけの提案には意味型が無いので、検索の列はキーだけ
    assert person["searchable"] == []
    status, rows = _request(client, "GET", f"{path}/{person['table']}/rows?q=1")
    assert status == 200 and rows["rows"]
    ROWS_VALIDATOR.validate(rows)
    key = rows["rows"][0]["key"]
    status, it = _request(client, "GET", f"{path}/{person['table']}/instance?" + "&".join(f"key={k}" for k in key))
    assert status == 200 and it["key"] == key
    INSTANCE_VALIDATOR.validate(it)
    status, body = _request(client, "GET", f"{path}/{person['table']}/instance?key=999999999")
    assert status == 404 and "行が無い" in body["error"]

    # 子の集まり（1:n）は、親で絞った集合として開ける。within は3つそろえる
    group = next(g for g in it["related"] if g["kind"] == "children")
    within = f"within_table={person['table']}&" + "&".join(f"within_key={k}" for k in key)
    status, part = _request(client, "GET", f"{path}/{group['table']}/rows?{within}&within_relation={group['relation']}")
    assert status == 200 and part["count"] == group["count"] and part["within"]["key"] == key
    ROWS_VALIDATOR.validate(part)
    status, body = _request(client, "GET", f"{path}/{group['table']}/rows?{within}")
    assert status == 422 and "そろえて" in body["error"]
    status, body = _request(client, "GET", f"{path}/{group['table']}/rows?within_via={group['relation']}")
    assert status == 422 and "そろえて" in body["error"]  # n:m の関係だけでは絞れない
    status, body = _request(client, "GET", f"{path}/{group['table']}/rows?within_table={person['table']}"
                                           f"&within_key=999999999&within_relation={group['relation']}")
    assert status == 404 and "行が無い" in body["error"]
    status, body = _request(client, "GET", f"{path}/{group['table']}/rows?{within}&within_relation=rel_nope")
    assert status == 404 and "関係" in body["error"]

    unconnected = TestClient(create_app(discover(work, {}), reviewing=reviewing))
    status, index = _request(unconnected, "GET", path)
    assert status == 200 and not index["readable"]
    status, body = _request(unconnected, "GET", f"{path}/{person['table']}/rows")
    assert status == 409 and "--connect" in body["error"]
