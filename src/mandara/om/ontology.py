"""OM の集約。語彙と表メタデータの組。

Domain 層。I/O を持たない。読み書きは mandara.omstore（Adapter）が担う。
"""

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from mandara.om.model import Column, Edge, EnumValue, Relation, Table, Vocabulary
from mandara.om.names import qualify

SELF = "self"
"""関係の target で、自分自身の表を指す値（仕様 11.1）。"""


def is_shared_key(pairs: Iterable[tuple[str, str]], own_key: Sequence[str], target_key: Sequence[str]) -> bool:
    """参照の列の対応（自分の列 → target の列）が、自分の主キーの各列を、target の主キーの同じ位置の列に結ぶ（共有主キー）。

    対応の並びは問わない（外部キーの列の並びが主キーの並びと違ってもよい）。自分自身への参照かどうかは呼ぶ側が見る。
    """
    return len(own_key) == len(target_key) and sorted(pairs) == sorted(zip(own_key, target_key, strict=True))


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
        """注釈（4.4）を持つものを、場所と組ですべてたどる。注釈を持つクラスを①に足したら、ここにも足す。

        場所は部品を修飾名の規則（mandara.om.names.qualify）でつないだ文字列。部品に "." があっても重ならない。
        """
        for name, semantic_type in (self.vocabulary.semantic_types or {}).items():
            for value in semantic_type.values or []:
                yield qualify("vocabulary", name, value.code), value
        for table in self.tables:
            yield table.id, table
            for name, column in (table.columns or {}).items():
                yield qualify(table.id, "columns", name), column
            for relation in table.relations or []:
                yield qualify(table.id, "relations", relation.id), relation
            if table.edge is not None:
                yield qualify(table.id, "edge"), table.edge

    def replace_annotation(self, location: str, new: Annotated) -> "Ontology":
        """annotations() の location の注釈を new に置き換えた OM。"""
        if dict(self.annotations()).get(location) is None:
            raise KeyError(f"注釈が無い: {location}")
        semantic_types = {
            name: semantic_type.model_copy(update={"values": [
                new if qualify("vocabulary", name, value.code) == location else value for value in semantic_type.values]})
            if semantic_type.values else semantic_type
            for name, semantic_type in (self.vocabulary.semantic_types or {}).items()
        }
        vocabulary = self.vocabulary.model_copy(update={"semantic_types": semantic_types}) if semantic_types else self.vocabulary

        def table(t: Table) -> Table:
            if t.id == location:
                assert isinstance(new, Table)
                return new
            update: dict[str, object] = {}
            if t.columns and any(qualify(t.id, "columns", n) == location for n in t.columns):
                update["columns"] = {n: (new if qualify(t.id, "columns", n) == location else c)
                                     for n, c in t.columns.items()}
            if t.relations and any(qualify(t.id, "relations", r.id) == location for r in t.relations):
                update["relations"] = [new if qualify(t.id, "relations", r.id) == location else r for r in t.relations]
            if t.edge is not None and qualify(t.id, "edge") == location:
                update["edge"] = new
            return t.model_copy(update=update) if update else t

        return Ontology(vocabulary=vocabulary, tables=tuple(table(t) for t in self.tables))
