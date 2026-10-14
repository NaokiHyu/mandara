"""OM の集約。語彙と表メタデータの組。

Domain 層。I/O を持たない。読み書きは mandara.omstore（Adapter）が担う。
"""

from dataclasses import dataclass

from mandara.om.model import Table, Vocabulary

SELF = "self"
"""関係の target で、自分自身の表を指す値（仕様 11.1）。"""


@dataclass(frozen=True)
class Ontology:
    vocabulary: Vocabulary
    tables: tuple[Table, ...]

    def table(self, table_id: str) -> Table | None:
        return next((t for t in self.tables if t.id == table_id), None)
