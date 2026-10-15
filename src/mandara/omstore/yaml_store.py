"""OM ファイルと主張台帳（YAML）の読み書き。

Adapter 層。ファイルの形（キーを名前にした辞書、固定のキー順）と、
メタモデル①から生成した型との変換をここに閉じ込める。

ファイルの形：
- 辞書で書く集まり（columns, column_domains, aliases）は、キーが要素の name になる。
  生成された型では name は要素の中にあるので、読むときに注入し、書くときに外す
- キー順は固定：型のフィールド順。ただし注釈の共通フィールド（4.4）は末尾に回す
- YAML のコメントは使わない（人のメモは note に書く）
- 配置は仕様 5章：contexts/<context>/tables/<source>/<table>.yaml。中身の context / source / name と一致させる
- 主張台帳は <root>/<schema>/<table>.yaml（root は work/<source>/claims、名前は符号化する）。主語の表ごとに1ファイル。
  決定的な抽出器が丸ごと作り直すキャッシュなので、書くときは台帳全体を置き換える
"""

import shutil
import tempfile
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, ValidationError

from mandara.om.claims import ClaimViolation, check_claims
from mandara.om.model import Claim, ClaimFile, ClaimSourceKind, Table, Vocabulary
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


# ---------------------------------------------------------------- 主張台帳
#
# 配置：<root>/<name(schema)>/<name(table)>.yaml。root は work/<source>/claims。
# - ソース上の名前は何でもありうる（"/"、".."、大文字、合成済み・分解済みの Unicode）。そのままパスにすると、
#   root の外に書いたり、大文字小文字を区別しないファイルシステム（macOS の既定）で黙って上書きしたりする。
#   そこで名前を可逆に符号化する：[a-z0-9_-] 以外のバイト（大文字、"."、"/"、非 ASCII）は %XX にする
# - 台帳は決定的な抽出器が丸ごと作り直すキャッシュ。書けるのは決定的な出所（schema / sql / codelist）の主張だけ。
#   再生成できない主張（review など）を、書き直しで消さないため（保存方針は仕様 15.2 で未決）
# - 単一の書き手を前提にする。並行して書かない（ロックは持たない）

_DETERMINISTIC_KINDS = frozenset({ClaimSourceKind.schema.value, ClaimSourceKind.sql.value, ClaimSourceKind.codelist.value})
_SAFE_BYTES = frozenset(b"abcdefghijklmnopqrstuvwxyz0123456789_-")


class InvalidClaimsError(Exception):
    """書こうとした・読んだ主張が、主張の検証（mandara.om.claims）を通らない。"""

    def __init__(self, violations: Sequence[ClaimViolation]) -> None:
        lines = "\n".join(f"  - {v.location}: {v.rule.name} {v.detail}" for v in violations)
        super().__init__(f"主張が不正（{len(violations)} 件）:\n{lines}")
        self.violations = tuple(violations)


def encode_name(name: str) -> str:
    """ソース上の名前を、ファイル名の部品に可逆に符号化する。"""
    if not name:
        raise ValueError("空の名前はファイル名にできない")
    return "".join(chr(b) if b in _SAFE_BYTES else f"%{b:02X}" for b in name.encode("utf-8"))


def decode_name(encoded: str) -> str:
    out = bytearray()
    i = 0
    while i < len(encoded):
        if encoded[i] == "%":
            out.append(int(encoded[i + 1 : i + 3], 16))
            i += 3
        else:
            out.append(ord(encoded[i]))
            i += 1
    name = out.decode("utf-8")
    if encode_name(name) != encoded:
        raise ValueError(f"正規の符号化ではない: {encoded!r}")
    return name


def parse_claims(text: str) -> ClaimFile:
    return ClaimFile.model_validate(_parse(text))


def dump_claims(claims: ClaimFile) -> str:
    return _dump(_to_plain(claims))


def load_claim_ledger(root: Path, source: str) -> tuple[Claim, ...]:
    """台帳をすべて読む。配置との食い違い、主張の検証の違反はエラー。"""
    if not root.is_dir():
        # 台帳が無いことを、主張が0件であることと取り違えない
        raise OmLoadError(root, FileNotFoundError("主張台帳のディレクトリが無い"))
    claims: list[Claim] = []
    for path in _claim_files(root):
        try:
            schema, table = decode_name(path.parent.name), decode_name(path.stem)
        except ValueError as e:
            raise OmLoadError(path, e) from e
        claim_file = _load(path, ClaimFile.model_validate, ClaimFile)
        for claim in claim_file.claims or []:
            subject = claim.subject
            if (subject.source, subject.schema_name, subject.table) != (source, schema, table):
                raise OmLoadError(path, ValueError(
                    f"{claim.id} の主語 {subject.source}:{subject.schema_name}.{subject.table} が"
                    f"配置 {source}:{schema}.{table} と食い違う"))
            claims.append(claim)
    # 台帳のファイルは手で編集されうる。ID が内容と合っているかを含め、読む境界で検証する
    if violations := check_claims(claims):
        raise OmLoadError(root, InvalidClaimsError(violations))
    return tuple(claims)


def write_claim_ledger(root: Path, source: str, claims: Iterable[Claim]) -> None:
    """台帳を丸ごと書き直す。書く前に主張を検証し、root に台帳以外のものがあれば消さずにエラーにする。"""
    claims = list(claims)
    if foreign := sorted({c.subject.source for c in claims} - {source}):
        raise ValueError(f"ソース {source} の台帳に、別のソースの主張がある: {foreign}")
    if kinds := sorted({c.source.kind for c in claims} - _DETERMINISTIC_KINDS):
        raise ValueError(f"再生成できない出所の主張は台帳に書けない（書き直しで消える）: {kinds}")
    if violations := check_claims(claims):
        raise InvalidClaimsError(violations)
    if root.exists():
        _claim_files(root)  # 台帳でないものがあれば、何も書く前に止まる

    by_table: dict[tuple[str, str], list[Claim]] = {}
    for claim in claims:
        by_table.setdefault((claim.subject.schema_name, claim.subject.table), []).append(claim)

    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=root.parent, prefix=f".{root.name}.new-"))
    for (schema, table), group in sorted(by_table.items()):
        path = staging / encode_name(schema) / f"{encode_name(table)}.yaml"
        path.parent.mkdir(exist_ok=True)
        ordered = sorted(group, key=lambda c: (c.subject.column or "", c.predicate, c.id))
        path.write_text(dump_claims(ClaimFile(claims=ordered)), encoding="utf-8")

    if root.exists():
        retired = Path(tempfile.mkdtemp(dir=root.parent, prefix=f".{root.name}.old-"))
        root.rename(retired / root.name)
        staging.rename(root)
        shutil.rmtree(retired)
    else:
        staging.rename(root)


def _claim_files(root: Path) -> list[Path]:
    """台帳の配置に合うファイルの一覧。合わないもの（他のファイル、シンボリックリンク、深い階層）があればエラー。"""
    if not root.is_dir() or root.is_symlink():
        raise OmLoadError(root, NotADirectoryError("主張台帳のディレクトリではない"))
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        depth = len(path.relative_to(root).parts)
        if path.is_symlink():
            raise OmLoadError(path, ValueError("台帳の中にシンボリックリンクがある"))
        if path.is_dir() and depth == 1:
            continue
        if path.is_file() and depth == 2 and path.suffix == ".yaml":
            files.append(path)
            continue
        raise OmLoadError(path, ValueError("<schema>/<table>.yaml の形ではない"))
    return files
