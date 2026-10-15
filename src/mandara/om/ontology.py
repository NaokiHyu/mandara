"""OM の集約。語彙と表メタデータの組。

Domain 層。I/O を持たない。読み書きは mandara.omstore（Adapter）が担う。
"""

from collections.abc import Iterator
from dataclasses import dataclass

from mandara.om.model import Column, Edge, EnumValue, Relation, Table, Vocabulary

SELF = "self"
"""関係の target で、自分自身の表を指す値（仕様 11.1）。"""


Annotated = Table | Column | Relation | Edge | EnumValue
ANNOTATED_CLASSES = (Table, Column, Relation, Edge, EnumValue)
"""注釈（4.4）を持つクラス。Ontology.annotations() はこれをすべてたどる。"""


@dataclass(frozen=True)
class Ontology:
    vocabulary: Vocabulary
    tables: tuple[Table, ...]

    def table(self, table_id: str) -> Table | None:
        return next((t for t in self.tables if t.id == table_id), None)

    def annotations(self) -> Iterator[tuple[str, Annotated]]:
        """注釈（4.4）を持つものを、場所と組ですべてたどる。注釈を持つクラスを①に足したら、ここにも足す。"""
        for name, domain in (self.vocabulary.column_domains or {}).items():
            for value in domain.values or []:
                yield f"vocabulary.{name}.{value.code}", value
        for table in self.tables:
            yield table.id, table
            for name, column in (table.columns or {}).items():
                yield f"{table.id}.columns.{name}", column
            for relation in table.relations or []:
                yield f"{table.id}.relations.{relation.id}", relation
            if table.edge is not None:
                yield f"{table.id}.edge", table.edge
