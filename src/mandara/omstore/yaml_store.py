"""OM ファイル（YAML）の読み書き。

Adapter 層。ファイルの形（キーを名前にした辞書、固定のキー順）と、
メタモデル①から生成した型との変換をここに閉じ込める。

ファイルの形：
- 辞書で書く集まり（columns, column_domains, aliases）は、キーが要素の name になる。
  生成された型では name は要素の中にあるので、読むときに注入し、書くときに外す
- キー順は固定：型のフィールド順。ただし注釈の共通フィールド（4.4）は末尾に回す
- YAML のコメントは使わない（人のメモは note に書く）
- 配置は仕様 5章：contexts/<context>/tables/<source>/<table>.yaml。中身の context / source / name と一致させる
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, ValidationError

from mandara.om.model import Table, Vocabulary
from mandara.om.ontology import Ontology

_ANNOTATION_FIELDS = ("status", "confidence", "evidence", "inferred_by", "reviewed_by", "reviewed_at", "note")
_KEYED_COLLECTIONS: dict[type[BaseModel], tuple[str, ...]] = {
    Vocabulary: ("column_domains", "aliases"),
    Table: ("columns",),
}


class OmLoadError(Exception):
    """OM ファイルを読めなかった。どのファイルの、どこで失敗したかを持つ。"""

    def __init__(self, path: Path, cause: Exception) -> None:
        super().__init__(f"{path}: {cause}")
        self.path = path
        self.cause = cause


# ---------------------------------------------------------------- 読み


def load_vocabulary(path: Path) -> Vocabulary:
    return _load(path, Vocabulary.model_validate, Vocabulary)


def load_table(path: Path) -> Table:
    return _load(path, Table.model_validate, Table)


def load_ontology(root: Path) -> Ontology:
    """root は仕様 5章の ontology/ に当たるディレクトリ。

    contexts/ の下で、表ファイルの配置規則に合わないファイルがあればエラーにする（黙って読み飛ばさない）。
    """
    vocabulary = load_vocabulary(root / "vocabulary.yaml")
    files = sorted(p for p in (root / "contexts").rglob("*") if p.is_file())
    tables: list[Table] = []
    for path in files:
        parts = path.relative_to(root).parts
        if len(parts) != 5 or parts[2] != "tables" or path.suffix != ".yaml":
            raise OmLoadError(path, ValueError("contexts/<context>/tables/<source>/<table>.yaml の形ではない"))
        table = load_table(path)
        _check_placement(path, table, context=parts[1], source=parts[3])
        tables.append(table)
    return Ontology(vocabulary=vocabulary, tables=tuple(tables))


def _check_placement(path: Path, table: Table, *, context: str, source: str) -> None:
    expected = {"context": context, "source": source, "name の表名部分": path.stem}
    actual = {"context": table.context, "source": table.source, "name の表名部分": table.name.rsplit(".", 1)[-1]}
    mismatched = [f"{k}: 配置={expected[k]!r}, 中身={actual[k]!r}" for k in expected if expected[k] != actual[k]]
    if mismatched:
        raise OmLoadError(path, ValueError("配置と中身が食い違う: " + "; ".join(mismatched)))


def parse_table(text: str) -> Table:
    return Table.model_validate(_inject_names(_parse(text), Table))


def parse_vocabulary(text: str) -> Vocabulary:
    return Vocabulary.model_validate(_inject_names(_parse(text), Vocabulary))


def _load[M: BaseModel](path: Path, validate: Callable[[Any], M], model: type[M]) -> M:
    try:
        raw = _parse(path.read_text(encoding="utf-8"))
        return validate(_inject_names(raw, model))
    except (OSError, yaml.YAMLError, ValidationError, ValueError) as e:
        raise OmLoadError(path, e) from e


def _parse(text: str) -> dict[str, Any]:
    raw: object = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ValueError(f"最上位が辞書ではない: {type(raw).__name__}")
    return cast(dict[str, Any], raw)


def _inject_names(raw: dict[str, Any], model: type[BaseModel]) -> dict[str, Any]:
    out = dict(raw)
    for key in _KEYED_COLLECTIONS.get(model, ()):
        entries: object = out.get(key)
        if entries is None:
            continue
        if not isinstance(entries, dict):
            raise ValueError(f"{key} は辞書で書く")
        injected: dict[str, Any] = {}
        for name, body in cast(dict[object, object], entries).items():
            if not isinstance(name, str):
                # YAML 1.1 では on / no / null などのキーが真偽値や null になる。黙って文字列化しない
                raise ValueError(f"{key} のキー {name!r} が文字列ではない。引用符で囲む")
            if not isinstance(body, dict):
                raise ValueError(f"{key}.{name} が辞書ではない")
            body = cast(dict[str, Any], body)
            if "name" in body:
                raise ValueError(f"{key}.{name}: name はキーで書く。本体に name を書かない")
            injected[name] = {"name": name, **body}
        out[key] = injected
    return out


# ---------------------------------------------------------------- 書き


def dump_table(table: Table) -> str:
    return _dump(_to_plain(table))


def dump_vocabulary(vocabulary: Vocabulary) -> str:
    return _dump(_to_plain(vocabulary))


def _dump(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000)


def _to_plain(value: object) -> Any:
    if isinstance(value, BaseModel):
        fields = list(type(value).model_fields)
        ordered = [f for f in fields if f not in _ANNOTATION_FIELDS] + [f for f in _ANNOTATION_FIELDS if f in fields]
        keyed = _KEYED_COLLECTIONS.get(type(value), ())
        out: dict[str, Any] = {}
        for name in ordered:
            item = getattr(value, name)
            if item is None:
                continue
            field = type(value).model_fields[name]
            key = field.alias or name
            if name in keyed:
                out[key] = {k: _without_name(k, _to_plain(v)) for k, v in item.items()}
            else:
                out[key] = _to_plain(item)
        return out
    if isinstance(value, list):
        return [_to_plain(v) for v in cast(list[object], value)]
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in cast(dict[str, object], value).items()}
    if hasattr(value, "isoformat"):
        return cast(Any, value).isoformat()
    return value


def _without_name(key: str, body: dict[str, Any]) -> dict[str, Any]:
    if body.get("name") != key:
        raise ValueError(f"辞書のキー {key!r} と name {body.get('name')!r} が食い違う。書き出すとどちらかが消える")
    return {k: v for k, v in body.items() if k != "name"}
