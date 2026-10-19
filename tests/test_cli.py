"""mandara コマンド（と、その下の生成パイプライン）。AdventureWorks に対して通しで動かす。

- run は work/<source>/ に範囲・主張・OM・評価・実行の記録を書く
- 2回走らせても、主張と OM は同じ（決定的）。実行の記録は増える
- 順序を飛ばす（抽出の前に提案）、範囲の誤りは、1行のエラーと終了コード 2
- validate は違反があれば終了コード 1
"""

import io
import json

import pytest
import shutil
from pathlib import Path

from mandara.cli import main

ROOT = Path(__file__).parent.parent
GOLD = ROOT / "evals/datasets/adventureworks/gold/ontology"
SCHEMAS = ["humanresources", "person", "production", "purchasing", "sales"]


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def _aw(work: Path, *extra: str, resemblance: bool = False) -> list[str]:
    """値の似かた（T2.6）は、通しの決定性を見るテストでだけ測る（ほかは --no-resemblance で速く）。"""
    return ["--source", "adventureworks", "--work", str(work), "--conninfo", "dbname=adventureworks",
            *(a for s in SCHEMAS for a in ("--schema", s)), *extra, *(() if resemblance else ("--no-resemblance",))]


def _tree(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}


def test_run_writes_everything_and_is_deterministic(tmp_path: Path) -> None:
    code, out, err = _run("run", *_aw(tmp_path, "--gold", str(GOLD), resemblance=True))
    assert (code, err) == (0, "")
    assert "\033[" not in out  # 端末でなければ色を付けない
    ws = tmp_path / "adventureworks"
    assert json.loads((ws / "claims/scope.json").read_text()) == {"schemas": SCHEMAS}
    claims, ontology = _tree(ws / "claims"), _tree(ws / "ontology")
    assert len(ontology) == 1 + 89  # vocabulary.yaml と表

    [record] = list((ws / "runs").iterdir())
    data = json.loads(record.read_text())
    assert data["status"] == "succeeded" and data["completed"] == ["extract", "propose", "evaluate"]
    assert data["versions"] == {"extractor": "catalog 0.1", "values_extractor": "values 0.1",
                                "declaration_extractor": "declarations 0.1", "joins_extractor": "joins 0.2",
                                "keys_extractor": "keys 0.1", "keys_modeler": "modeler-keys 0.1",
                                "modeler": "modeler-rules 0.1", "joins_modeler": "modeler-joins 0.1",
                                "nameless_joins_modeler": "modeler-nameless-joins 0.1",
                                "resemblance_extractor": "resemblance 0.1", "same_meaning_modeler": "modeler-same-meaning 0.1"}
    # 値の似かた（T2.6）：比べる列の形と、近い組の信号ごとの主張。並列で測っても 2 回目と同じ主張（下で比べる）
    assert data["resemblance"]["pairs"] > data["resemblance"]["kept"] > 0 and "主張にした組" in out
    assert data["claims"]["shaped_as"] > 300 and data["claims"]["resembles"] >= 3 * 1000
    decided = {(e["table"], e["column"]): e["encoding"] for e in data["encodings"]["examined"] if e["encoding"]}
    assert decided == {("person.address", "spatiallocation"): "sqlserver_geography"}
    assert "person.address.spatiallocation → sqlserver_geography" in out
    assert data["gold"]["tables"] == 14 and len(data["gold"]["hash"]) == 16
    # 宣言された外部キー 90 本と、値の包含から推定した宣言の無い結合 4 本（archive の productid → product など。T2.2）
    assert data["proposal"]["relations"] == 94 and data["skipped"] == []
    assert data["joins"]["chosen"] >= 4 and data["joins"]["overlapping"] >= data["joins"]["included"]
    # 正解 OM の関係のうち 1 本は宣言されていない結合で、値の包含から戻る（T2.2 の前は 15/17）
    assert data["evaluation"]["relation_endpoints"] == {"correct": 16, "gold": 17, "proposed": 16}
    report = json.loads((ws / "eval/report.json").read_text())
    assert [a["aspect"] for a in report["aspects"]][:3] == ["tables", "kind", "primary_key"]

    assert _run("run", *_aw(tmp_path, "--gold", str(GOLD), resemblance=True))[0] == 0
    assert _tree(ws / "claims") == claims and _tree(ws / "ontology") == ontology
    assert len(list((ws / "runs").iterdir())) == 2
    assert json.loads(next(p for p in (ws / "runs").iterdir() if p.name != record.name).read_text())[
        "ledger_hash"] == data["ledger_hash"]


def test_steps_can_run_separately(tmp_path: Path) -> None:
    assert _run("extract", *_aw(tmp_path))[0] == 0
    code, out, _ = _run("propose", "--source", "adventureworks", "--work", str(tmp_path))
    assert code == 0 and "89 tables" in out
    code, out, _ = _run("eval", "--source", "adventureworks", "--work", str(tmp_path), "--gold", str(GOLD))
    assert code == 0 and "relation_endpoints" in out


def test_propose_before_extract_is_an_error(tmp_path: Path) -> None:
    code, out, err = _run("propose", "--source", "adventureworks", "--work", str(tmp_path))
    assert code == 2 and out == "" and "主張台帳のディレクトリが無い" in err


def test_unknown_schema_is_an_error(tmp_path: Path) -> None:
    code, _, err = _run("extract", "--source", "adventureworks", "--work", str(tmp_path),
                        "--conninfo", "dbname=adventureworks", "--schema", "no_such_schema")
    assert code == 2 and "no_such_schema" in err
    assert not (tmp_path / "adventureworks").exists()


def test_validate(tmp_path: Path) -> None:
    code, out, _ = _run("validate", str(GOLD))
    assert code == 0 and "valid" in out
    broken = tmp_path / "ontology"
    shutil.copytree(GOLD, broken)
    path = broken / "contexts/sales/tables/adventureworks/salesorderdetail.yaml"
    path.write_text(path.read_text().replace("target: tbl_sales_salesorderheader", "target: tbl_nowhere"))
    code, out, _ = _run("validate", str(broken))
    assert code == 1 and "UNKNOWN_TARGET" in out


def test_failed_run_is_recorded_with_its_stage(tmp_path: Path) -> None:
    code, _, err = _run("run", *_aw(tmp_path, "--gold", str(tmp_path / "no_gold")))
    assert code == 2 and "no_gold" in err
    [record] = list((tmp_path / "adventureworks/runs").iterdir())
    data = json.loads(record.read_text())
    assert (data["status"], data["failed_stage"], data["completed"]) == ("failed", "evaluate", ["extract", "propose"])
    assert "OmLoadError" in data["error"]


def test_declare_writes_the_declaration_and_extract_makes_claims(tmp_path: Path) -> None:
    code, out, err = _run("declare", "--source", "adventureworks", "--work", str(tmp_path), "--default-currency", "USD")
    assert (code, err) == (0, "") and "default_currency USD" in out
    assert (tmp_path / "adventureworks/declarations.yaml").read_text() == "default_currency: USD\n"
    assert _run("extract", *_aw(tmp_path))[0] == 0
    claims = "".join(p.read_text() for p in (tmp_path / "adventureworks/claims").rglob("*.yaml"))
    assert claims.count("predicate: amounts_in") == 89 and "kind: declaration" in claims
    with pytest.raises(SystemExit):  # 通貨でない単位は宣言できない（引数の段で拒む）
        _run("declare", "--source", "adventureworks", "--work", str(tmp_path), "--default-currency", "each")
    (tmp_path / "adventureworks/declarations.yaml").write_text("default_currency: each\n")  # 手で書き換えても拒む
    code, _, err = _run("extract", *_aw(tmp_path))
    assert code == 2 and "通貨でない" in err
