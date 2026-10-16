"""OM・主張台帳の YAML と、①から生成した型との変換。

Adapter 層。ファイルの形をここに閉じ込める。
- 辞書で書く集まり（columns, semantic_types, aliases）は、キーが要素の name になる。
  生成された型では name は要素の中にあるので、読むときに注入し、書くときに外す
- キー順は固定：型のフィールド順。ただし注釈の共通フィールド（4.4）は末尾に回す
- YAML のコメントは使わない（人のメモは note に書く）
"""

from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, ValidationError

from mandara.om.model import Table, Vocabulary

ANNOTATION_FIELDS = ("status", "confidence", "evidence", "inferred_by", "reviewed_by", "reviewed_at", "note")
_KEYED_COLLECTIONS: dict[type[BaseModel], tuple[str, ...]] = {
    Vocabulary: ("semantic_types", "aliases"),
    Table: ("columns",),
}


class OmLoadError(Exception):
    """OM・主張台帳のファイルを読めなかった。どのファイルの、どこで失敗したかを持つ。"""

    def __init__(self, path: Path, cause: Exception) -> None:
        super().__init__(f"{path}: {cause}")
        self.path = path
        self.cause = cause


def parse[M: BaseModel](text: str, model: type[M]) -> M:
    return model.model_validate(_inject_names(_parse_mapping(text), model))


def load[M: BaseModel](path: Path, model: type[M]) -> M:
    try:
        return parse(path.read_text(encoding="utf-8"), model)
    except (OSError, yaml.YAMLError, ValidationError, ValueError) as e:
        raise OmLoadError(path, e) from e


def dump(value: BaseModel) -> str:
    return yaml.safe_dump(_to_plain(value), sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000)


def _parse_mapping(text: str) -> dict[str, Any]:
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


def _to_plain(value: object) -> Any:
    if isinstance(value, BaseModel):
        fields = list(type(value).model_fields)
        ordered = [f for f in fields if f not in ANNOTATION_FIELDS] + [f for f in ANNOTATION_FIELDS if f in fields]
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
