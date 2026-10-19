"""スキーマコンテキスト（世界）の文書の置き場所（<docs>/）と、ライターの手引き（<docs>/primer.yaml）の読み書き。

Adapter 層。
- 文書は人が雑に置くもの。読むのはテキストの文書（.md、.txt）だけで、それ以外のファイルはエラー（黙って読み飛ばさない。
  PDF などは T4.3 の抽出器が読めるようになってから）。隠しファイル（. で始まる）と手引き自身は文書ではない
- 手引きは人が読んで直してよい。あれば上書きしない（作り直すときは人が消す）
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from mandara.modeler.writer import Document
from mandara.om.model import Primer
from mandara.omstore import codec
from mandara.omstore.codec import OmLoadError

PRIMER = "primer.yaml"
TEXT_SUFFIXES = frozenset({".md", ".txt"})


class DocumentLoadError(Exception):
    """文書の置き場所を読めない（無い、読めない形式のファイルがある）。"""


class PrimerExistsError(Exception):
    """手引きがもうある（人が直したかもしれないので上書きしない）。"""


def read_documents(root: Path) -> list[Document]:
    if not root.is_dir():
        raise DocumentLoadError(f"文書の置き場所が無い: {root}")
    documents: list[Document] = []
    unsupported: list[str] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root)
        if any(part.startswith(".") for part in relative.parts) or relative == Path(PRIMER):
            continue
        if path.is_symlink() or path.suffix.lower() not in TEXT_SUFFIXES:
            unsupported.append(str(relative))
            continue
        try:
            documents.append(Document(str(relative), path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError) as e:
            raise DocumentLoadError(f"文書を読めない: {path}: {e}") from e
    if unsupported:
        raise DocumentLoadError(f"読めない形式のファイルがある（文書は {sorted(TEXT_SUFFIXES)}）: {unsupported}")
    if not documents:
        raise DocumentLoadError(f"文書が無い（{sorted(TEXT_SUFFIXES)} のファイルを置く）: {root}")
    return documents


def primer_path(root: Path) -> Path:
    return root / PRIMER


@dataclass(frozen=True)
class LoadedPrimer:
    primer: Primer
    sha256: str
    """読んだファイルのバイトのハッシュ（どの手引きを使ったかの記録）。"""


def load_primer(root: Path) -> LoadedPrimer | None:
    """手引きが無ければ None（まだ書いていない。黙った代用ではない）。形だけを確かめる（中身は writer.check_*）。"""
    path = primer_path(root)
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise OmLoadError(path, e) from e
    return LoadedPrimer(codec.parse_at(path, text, Primer), hashlib.sha256(raw).hexdigest())


def write_primer(root: Path, primer: Primer) -> Path:
    path = primer_path(root)
    if path.exists():
        raise PrimerExistsError(f"手引きがもうある（人が直したかもしれないので上書きしない。作り直すなら消す）: {path}")
    path.write_text(codec.dump(primer), encoding="utf-8")
    return path
