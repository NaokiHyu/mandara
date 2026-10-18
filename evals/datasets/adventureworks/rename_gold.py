"""段 3 の世界（make_bare.sh 3）の正解 OM：元の正解 OM の列名を、段 3 の DB と同じ意味の無い名前に変える。

使い方: uv run python evals/datasets/adventureworks/rename_gold.py 元の正解OM 出力先
名前は c_ ＋ md5("スキーマ.表.列") の 16 進の先頭 6 桁（make_bare.sh と同じ）。列名を持つのは：列の一覧のキーと列の name、主キー・代替キー、
関係の via（from はこの表の列、to は参照先の表の列）、単位の列。生成物は git に乗せない（work/ の下に出す）。
"""

import hashlib
import sys
from pathlib import Path

from mandara.om.model import ColumnPair, Table
from mandara.om.names import split_qualified
from mandara.om.ontology import SELF, Ontology
from mandara.omstore.yaml_store import load_ontology, write_ontology


def opaque(table: str, column: str) -> str:
    schema, name = split_qualified(table)
    return "c_" + hashlib.md5(f"{schema}.{name}.{column}".encode()).hexdigest()[:6]


def rename(ontology: Ontology) -> Ontology:
    by_id = {t.id: t.name for t in ontology.tables}
    return Ontology(ontology.vocabulary, tuple(_table(t, by_id) for t in ontology.tables))


def _table(t: Table, by_id: dict[str, str]) -> Table:
    def mine(c: str) -> str:
        return opaque(t.name, c)

    columns = {mine(n): c.model_copy(update={
        "name": mine(n),
        "unit": c.unit.model_copy(update={"column": mine(c.unit.column)}) if c.unit and c.unit.column else c.unit})
        for n, c in (t.columns or {}).items()} or None
    identity = t.identity.model_copy(update={
        "primary": [mine(c) for c in t.identity.primary or []] or None,
        "alternates": [k.model_copy(update={"columns": [mine(c) for c in k.columns]})
                       for k in t.identity.alternates or []] or None,
    }) if t.identity else None
    relations = []
    for r in t.relations or []:
        target = t.name if r.target == SELF else by_id[r.target]
        via = [ColumnPair.model_validate({"from": mine(p.from_), "to": opaque(target, p.to)}) for p in r.via or []]
        derived = [mine(c) for c in r.columns or []] or None  # derived-from で導出される、この表の列
        relations.append(r.model_copy(update={"via": via or None, "columns": derived}))
    return t.model_copy(update={"columns": columns, "identity": identity, "relations": relations or None})


if __name__ == "__main__":
    source, out = Path(sys.argv[1]), Path(sys.argv[2])
    write_ontology(out, rename(load_ontology(source)))
    print(f"{out}: 列名を段 3 の名前にした正解 OM")
