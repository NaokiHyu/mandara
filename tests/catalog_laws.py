"""CatalogReader の契約（全コネクタ共通の法則）。

各コネクタのテストは、自分の reader と範囲を渡してこれらを当てる。
法則は「違反の一覧」を返す。空なら成立。
"""

from mandara.om.model import ObjectKind
from mandara.source.catalog import Catalog, CatalogReader, Scope


def references_exist(catalog: Catalog) -> list[str]:
    """主キー・一意・外部キーが指す列は、その表に実在する。範囲内の外部キーの参照先は実在する。"""
    problems: list[str] = []
    for table in catalog.tables:
        names = {c.name for c in table.columns}
        keys = [k for k in (table.primary_key, *table.uniques) if k is not None]
        for key in keys:
            problems += [f"{table.qualified_name}.{key.name}: 列 {c} が無い" for c in key.columns if c not in names]
        for fk in table.foreign_keys:
            loc = f"{table.qualified_name}.{fk.name}"
            problems += [f"{loc}: 列 {c} が無い" for c in fk.columns if c not in names]
            if len(fk.columns) != len(fk.target_columns):
                problems.append(f"{loc}: 列の数が参照先と違う")
            if fk.target_schema not in catalog.scope.schemas:
                continue
            target = catalog.table(fk.target_schema, fk.target_table)
            if target is None:
                problems.append(f"{loc}: 参照先 {fk.target_schema}.{fk.target_table} が無い")
                continue
            problems += [f"{loc}: 参照先の列 {c} が無い" for c in fk.target_columns if target.column(c) is None]
    return problems


def canonical_order(catalog: Catalog) -> list[str]:
    """出力の順序が正規化されている（同じソースから同じ順序で出る前提）。"""
    problems: list[str] = []
    keys = [(t.schema, t.name) for t in catalog.tables]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        problems.append("表が (schema, name) の順でない、または重複している")
    for table in catalog.tables:
        positions = [c.position for c in table.columns]
        if positions != sorted(set(positions)):
            problems.append(f"{table.qualified_name}: 列が position の順でない、または重複している")
        for label, names in (
            ("uniques", [k.name for k in table.uniques]),
            ("foreign_keys", [f.name for f in table.foreign_keys]),
            ("checks", [c.name for c in table.checks]),
        ):
            if names != sorted(names):
                problems.append(f"{table.qualified_name}: {label} が名前の順でない")
    return problems


def within_scope(catalog: Catalog) -> list[str]:
    """範囲外のスキーマの表を返さない。範囲は名前の順で重複なし。"""
    problems = [t.qualified_name for t in catalog.tables if t.schema not in catalog.scope.schemas]
    if list(catalog.scope.schemas) != sorted(set(catalog.scope.schemas)):
        problems.append(f"範囲が正規化されていない: {catalog.scope.schemas}")
    return problems


def tables_know_nullability(catalog: Catalog) -> list[str]:
    """表の列は NULL 可否が分かっている（None はビューなど、ソースが宣言しないものだけ）。"""
    return [
        f"{t.qualified_name}.{c.name}"
        for t in catalog.tables
        if t.kind is ObjectKind.table
        for c in t.columns
        if c.nullable is None
    ]


def all_laws(catalog: Catalog) -> list[str]:
    return [*references_exist(catalog), *canonical_order(catalog), *within_scope(catalog),
            *tables_know_nullability(catalog)]


def deterministic(reader: CatalogReader, scope: Scope) -> bool:
    """同じソースを2回読むと一致する。"""
    return reader.read(scope) == reader.read(scope)
