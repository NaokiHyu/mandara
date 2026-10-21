"""スキーマコンテキスト（世界）に人が置いた文書（設計書、メモなど。仕様 6.6.1 の知識源）。

Domain 層。文書は信頼できない入力として扱う（読むのは omstore.document_store、使うのはライターなど）。
"""

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Document:
    name: str
    """文書の置き場所からの相対パス。"""
    text: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()
