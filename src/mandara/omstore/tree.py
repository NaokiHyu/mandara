"""work/ の下に丸ごと作り直すディレクトリ（主張台帳、生成した OM）の読み書き。

Adapter 層。
- ソース上の名前は何でもありうる（"/"、".."、大文字、合成済み・分解済みの Unicode）。そのままパスにすると、
  root の外に書いたり、大文字小文字を区別しないファイルシステム（macOS の既定）で黙って上書きしたりする。
  そこで名前を可逆に符号化する：[a-z0-9_-] 以外のバイト（大文字、"."、"/"、非 ASCII）は %XX にする
- 置き換えるときは、新しい中身を別の場所に書いてから入れ替える。既存の root に配置規則に合わないもの
  （人のメモ、シンボリックリンク）があれば、何も消さずにエラーにする
- 単一の書き手を前提にする。並行して書かない（ロックは持たない）
"""

import shutil
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

from mandara.omstore.codec import OmLoadError

_SAFE_BYTES = frozenset(b"abcdefghijklmnopqrstuvwxyz0123456789_-")
MAX_ENCODED = 240
"""符号化した部品の長さの上限。多くのファイルシステムの NAME_MAX（255 バイト）から、拡張子などの余裕を引いた値。"""

Layout = Callable[[tuple[str, ...], bool], bool]
"""root からの相対パスの部品と、ディレクトリかどうかを受け取り、配置規則に合うかを返す。"""


def encode_name(name: str) -> str:
    """ソース上の名前を、ファイル名の部品に可逆に符号化する。"""
    if not name:
        raise ValueError("空の名前はファイル名にできない")
    encoded = "".join(chr(b) if b in _SAFE_BYTES else f"%{b:02X}" for b in name.encode("utf-8"))
    if len(encoded) > MAX_ENCODED:
        raise ValueError(f"名前を符号化すると長すぎる（{len(encoded)} > {MAX_ENCODED}）: {name[:20]!r}…")
    return encoded


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


def files_in(root: Path, layout: Layout, what: str) -> list[Path]:
    """配置規則に合うファイルの一覧。合わないもの・シンボリックリンクがあればエラー。"""
    if not root.is_dir() or root.is_symlink():
        raise OmLoadError(root, NotADirectoryError(f"{what}のディレクトリではない"))
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts
        if path.is_symlink():
            raise OmLoadError(path, ValueError(f"{what}の中にシンボリックリンクがある"))
        if not layout(parts, path.is_dir()):
            raise OmLoadError(path, ValueError(f"{what}の配置規則に合わない"))
        if path.is_file():
            files.append(path)
    return files


def replace_tree(root: Path, files: Mapping[tuple[str, ...], str], layout: Layout, what: str) -> None:
    """root を、files（相対パスの部品 → 中身）だけを持つディレクトリに置き換える。"""
    for parts in files:
        if not layout(parts, False):
            raise ValueError(f"書こうとしたファイルが{what}の配置規則に合わない: {parts}")
    if root.exists():
        files_in(root, layout, what)  # 配置規則に合わないものがあれば、何も書く前に止まる

    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=root.parent, prefix=f".{root.name}.new-"))
    try:
        for parts, text in sorted(files.items()):
            path = staging.joinpath(*parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    except BaseException:
        shutil.rmtree(staging)  # 書きかけは残さない。既存の root には触れていない
        raise

    if not root.exists():
        staging.rename(root)
        return
    retired = Path(tempfile.mkdtemp(dir=root.parent, prefix=f".{root.name}.old-"))
    root.rename(retired / root.name)
    try:
        staging.rename(root)
    except BaseException:
        (retired / root.name).rename(root)  # 入れ替えに失敗したら、元に戻す
        shutil.rmtree(staging)
        shutil.rmtree(retired)
        raise
    shutil.rmtree(retired)
