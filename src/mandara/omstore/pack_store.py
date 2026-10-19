"""知識パックの置き場所（packs/<name>/。仕様 16.3）の読み書き：目録 pack.yaml と語彙 vocabulary.yaml。

Adapter 層。形だけを確かめる（意味型の決まりは mandara.modeler.pack.check。Application が読んだ後に当てる）。
パックは人が読んで直してよい。あれば上書きしない（作り直すときは人が消す）。
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from mandara.om.model import PackManifest, Vocabulary
from mandara.omstore import codec
from mandara.omstore.codec import OmLoadError

MANIFEST = "pack.yaml"
VOCABULARY = "vocabulary.yaml"


class PackLoadError(Exception):
    """パックの置き場所を読めない（無い、目録や語彙が無い）。"""


class PackExistsError(Exception):
    """パックがもうある（人が直したかもしれないので上書きしない）。"""


@dataclass(frozen=True)
class LoadedPack:
    manifest: PackManifest
    vocabulary: Vocabulary
    sha256: str
    """目録と語彙のファイルのバイトのハッシュ（どのパックを使ったかの記録）。"""


def _read(path: Path) -> tuple[bytes, str]:
    try:
        raw = path.read_bytes()
        return raw, raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise OmLoadError(path, e) from e


def load_pack(root: Path) -> LoadedPack:
    missing = [f for f in (MANIFEST, VOCABULARY) if not (root / f).is_file()]
    if missing:
        raise PackLoadError(f"パックの置き場所に {missing} が無い: {root}")
    manifest_raw, manifest_text = _read(root / MANIFEST)
    vocabulary_raw, vocabulary_text = _read(root / VOCABULARY)
    digest = hashlib.sha256(manifest_raw + b"\0" + vocabulary_raw).hexdigest()
    return LoadedPack(codec.parse_at(root / MANIFEST, manifest_text, PackManifest),
                      codec.parse_at(root / VOCABULARY, vocabulary_text, Vocabulary), digest)


def write_pack(root: Path, manifest: PackManifest, vocabulary: Vocabulary) -> Path:
    if root.exists():
        raise PackExistsError(f"パックがもうある（人が直したかもしれないので上書きしない。作り直すなら消す）: {root}")
    root.mkdir(parents=True)
    (root / MANIFEST).write_text(codec.dump(manifest), encoding="utf-8")
    (root / VOCABULARY).write_text(codec.dump(vocabulary), encoding="utf-8")
    return root
