"""YAML の読み書き（Adapter）が、ファイル形式の落とし穴で黙って値を変えないこと。"""

from pathlib import Path

import pytest

from mandara.om.model import Column, Identity, ReviewStatus, Table, TableKind
from mandara.omstore.yaml_store import OmLoadError, dump_table, load_ontology, parse_table

MINIMAL = """\
id: tbl_x
name: s.x
source: src
context: ctx
kind: domain
identity:
  primary: [id]
columns:
  id: {domain: key, status: rejected}
status: rejected
"""


@pytest.mark.parametrize(
    ("old", "new"),
    [("{domain: key,", "{domain: key, encoding: wkb,"), ("kind: domain", "kind: view")],
    ids=["encoding", "kind"],
)
def test_value_outside_closed_set_is_rejected_on_read(old: str, new: str) -> None:
    """閉じた集合（①の enum）の外の値は、読み込みの境界でエラーになる。Domain の検査まで届かない。"""
    with pytest.raises(ValueError, match="validation error"):
        parse_table(MINIMAL.replace(old, new))


def test_minimal_table_is_readable() -> None:
    assert list(parse_table(MINIMAL).columns or {}) == ["id"]


def test_yaml11_boolean_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="文字列ではない"):
        parse_table(MINIMAL.replace("  id: {domain: key", "  on: {domain: key"))


def test_quoted_yaml11_key_is_kept() -> None:
    table = parse_table(MINIMAL.replace("  id: {domain: key", "  'on': {domain: key").replace("[id]", "['on']"))
    assert list(table.columns or {}) == ["on"]


def test_name_inside_body_is_rejected() -> None:
    with pytest.raises(ValueError, match="name はキーで書く"):
        parse_table(MINIMAL.replace("{domain: key,", "{name: id, domain: key,"))


def test_dump_rejects_key_name_mismatch() -> None:
    table = Table(
        id="tbl_x", name="s.x", source="src", context="ctx", kind=TableKind.domain,
        identity=Identity(primary=["a"]),
        columns={"a": Column(name="b", domain="key", status=ReviewStatus.rejected)},
        status=ReviewStatus.rejected,
    )
    with pytest.raises(ValueError, match="食い違う"):
        dump_table(table)


def _ontology_root(tmp_path: Path) -> Path:
    (tmp_path / "vocabulary.yaml").write_text("column_domains:\n  key: {type: identifier}\n", encoding="utf-8")
    return tmp_path


def test_placement_mismatch_is_rejected(tmp_path: Path) -> None:
    root = _ontology_root(tmp_path)
    path = root / "contexts/other/tables/src/x.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(MINIMAL, encoding="utf-8")
    with pytest.raises(OmLoadError, match="配置と中身が食い違う"):
        load_ontology(root)


def test_stray_file_is_rejected(tmp_path: Path) -> None:
    root = _ontology_root(tmp_path)
    good = root / "contexts/ctx/tables/src/x.yaml"
    good.parent.mkdir(parents=True)
    good.write_text(MINIMAL, encoding="utf-8")
    assert len(load_ontology(root).tables) == 1
    (good.parent / "y.yml").write_text(MINIMAL, encoding="utf-8")
    with pytest.raises(OmLoadError, match="の形ではない"):
        load_ontology(root)
