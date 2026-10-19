"""地図の点：OM の geo.point の列を、ソースから読んでコーデックで点にする（ゼロコピー）。

- AdventureWorks の住所 19,614 件がすべて点になる。上限で打ち切ったことを伝える
- NULL は数え、変換できない値は理由とともに返す（黙って捨てない）
- geo.point でない列、物理的な表現が分からない列はエラー
- API の応答は契約（points.schema.json）に従う。接続の無いソースは 409
"""

import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import jsonschema.validators
import pytest
from fastapi.testclient import TestClient

from mandara.api.server import create_app, discover
from mandara.app.pipeline import PointsError, read_points
from mandara.codecs.geography import encode_sqlserver_geography
from mandara.connectors.postgres.column_reader import PostgresColumnReader
from mandara.source.rows import ColumnReader, ColumnRow, ColumnSample
from mandara.omstore.yaml_store import load_ontology
from mandara.source.values import GeoPoint

ROOT = Path(__file__).parent.parent
GOLD = ROOT / "evals/datasets/adventureworks/gold/ontology"
ADDRESS = "tbl_person_address"
GOLD_OM = load_ontology(GOLD)


class FakeReader:
    def __init__(self, values: Sequence[object]) -> None:
        self.values = values

    def read_column(self, schema: str, table: str, key_columns: Sequence[str], column: str,
                    limit: int) -> ColumnSample:
        rows = tuple(ColumnRow((i,), v) for i, v in enumerate(self.values))
        return ColumnSample(rows[:limit], truncated=len(rows) > limit)


def test_adventureworks_addresses_become_points() -> None:
    reader = PostgresColumnReader("dbname=adventureworks")
    points = read_points(GOLD_OM, ADDRESS, "spatiallocation", reader, 50_000)
    assert (len(points.points), points.nulls, points.failed, points.truncated) == (19614, 0, (), False)
    assert points.rows_read == len(points.points) + points.nulls + len(points.failed)
    limited = read_points(GOLD_OM, ADDRESS, "spatiallocation", reader, 100)
    assert (len(limited.points), limited.truncated) == (100, True)


def test_nulls_are_counted_and_failures_are_reported() -> None:
    good = encode_sqlserver_geography(GeoPoint(35.68, 139.76))
    points = read_points(GOLD_OM, ADDRESS, "spatiallocation", FakeReader([good, None, b"broken", None]), 10)
    assert [p for _, p in points.points] == [GeoPoint(35.68, 139.76)]
    assert points.nulls == 2
    assert points.rows_read == 4 == len(points.points) + points.nulls + len(points.failed)
    assert [(key, "単一の点" in reason) for key, reason in points.failed] == [(("2",), True)]


def test_non_geo_column_is_an_error() -> None:
    with pytest.raises(PointsError, match="geo.point の列ではない"):
        read_points(GOLD_OM, ADDRESS, "city", FakeReader([]), 10)


def test_unknown_encoding_is_an_error(tmp_path: Path) -> None:
    root = tmp_path / "ontology"
    shutil.copytree(GOLD, root)
    path = root / "entity_groups/person/tables/adventureworks/address.yaml"
    path.write_text(path.read_text().replace("    encoding: sqlserver_geography\n", ""))
    with pytest.raises(PointsError, match="encoding"):
        read_points(load_ontology(root), ADDRESS, "spatiallocation", FakeReader([]), 10)


# ---------------------------------------------------------------- API

SCHEMA: dict[str, Any] = json.loads((ROOT / "ui/src/om/points.schema.json").read_text())
_cls = jsonschema.validators.validator_for(SCHEMA)
VALIDATOR = _cls(SCHEMA, format_checker=_cls.FORMAT_CHECKER)


def _get(readers: dict[str, ColumnReader], path: str) -> tuple[int, Any]:
    client = TestClient(create_app(discover(Path("/nonexistent"), {"aw": GOLD}), readers))
    response = cast(Any, client).get(path)
    return int(response.status_code), response.json()


def test_points_api_follows_the_contract() -> None:
    good = encode_sqlserver_geography(GeoPoint(-33.86, 151.21))
    status, body = _get({"adventureworks": FakeReader([good, None, b"x"])},
                        f"/api/ontologies/gold:aw/tables/{ADDRESS}/points?column=spatiallocation")
    assert status == 200
    assert [e.message for e in VALIDATOR.iter_errors(body)] == []
    assert (len(body["points"]), body["nulls"], len(body["failed"])) == (1, 1, 1)


def test_points_api_errors() -> None:
    path = f"/api/ontologies/gold:aw/tables/{ADDRESS}/points?column=spatiallocation"
    status, body = _get({}, path)
    assert status == 409 and "--connect adventureworks" in body["error"]
    status, body = _get({"adventureworks": FakeReader([])},
                        f"/api/ontologies/gold:aw/tables/{ADDRESS}/points?column=city")
    assert status == 422 and "geo.point" in body["error"]
    status, _ = _get({"adventureworks": FakeReader([])}, path + "&limit=0")
    assert status == 422


def test_column_reader_limit_boundary_and_composite_keys() -> None:
    """上限ちょうどなら打ち切りではない。1 少なければ打ち切り。複合主キーは列の順に返る。"""
    reader = PostgresColumnReader("dbname=adventureworks")
    count = 238  # person.countryregion の行数
    exact = reader.read_column("person", "countryregion", ["countryregioncode"], "name", count)
    assert (len(exact.rows), exact.truncated) == (count, False)
    short = reader.read_column("person", "countryregion", ["countryregioncode"], "name", count - 1)
    assert (len(short.rows), short.truncated) == (count - 1, True)
    detail = reader.read_column("sales", "salesorderdetail", ["salesorderid", "salesorderdetailid"], "orderqty", 3)
    assert [len(r.key) for r in detail.rows] == [2, 2, 2]
    keys = [cast(tuple[int, int], r.key) for r in detail.rows]
    assert keys == sorted(keys)


def test_points_api_reports_truncation() -> None:
    good = encode_sqlserver_geography(GeoPoint(1.0, 2.0))
    status, body = _get({"adventureworks": FakeReader([good] * 5)},
                        f"/api/ontologies/gold:aw/tables/{ADDRESS}/points?column=spatiallocation&limit=3")
    assert status == 200 and [e.message for e in VALIDATOR.iter_errors(body)] == []
    assert (len(body["points"]), body["rows_read"], body["limit"], body["truncated"]) == (3, 3, 3, True)
