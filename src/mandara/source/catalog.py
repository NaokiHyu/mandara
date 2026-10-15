"""データソースの構造（カタログ）と、それを読むポート。

Domain 層。I/O を持たず、特定の DB を知らない。
- 型名や CHECK の式は、ソースの方言の文字列のまま運ぶ（解釈は抽出器・推論の責務）
- 中立な型族（TypeFamily）への寄せは、各コネクタ（Adapter）の責務。TypeFamily と ReferentialAction は主張台帳と共有するので①で定義する
- 分からないことは分からないと持つ（ビューの列の NULL 可否は None）
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from mandara.om.model import ReferentialAction, TypeFamily


class ObjectKind(Enum):
    TABLE = "table"
    VIEW = "view"
    MATERIALIZED_VIEW = "materialized_view"


@dataclass(frozen=True)
class Column:
    name: str
    position: int
    source_type: str
    """ソース上の型名（例 character varying(50)）。方言の文字列のまま。型の別名を使う列では、別名の基底の型。"""
    family: TypeFamily
    nullable: bool | None
    """None は不明（ビューの列など、ソースが宣言していない）。"""
    default: str | None
    comment: str | None
    type_alias: str | None
    """ユーザー定義の型の別名（PostgreSQL のドメイン型、SQL Server の別名型）。修飾名。"""


@dataclass(frozen=True)
class Key:
    name: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class ForeignKey:
    name: str
    columns: tuple[str, ...]
    target_schema: str
    target_table: str
    target_columns: tuple[str, ...]
    on_delete: ReferentialAction
    on_update: ReferentialAction


@dataclass(frozen=True)
class Check:
    name: str
    expression: str
    """真偽を返す式。ソースの方言の文字列（CHECK の包みや制約の属性は含めない）。"""


@dataclass(frozen=True)
class Table:
    schema: str
    name: str
    kind: ObjectKind
    comment: str | None
    columns: tuple[Column, ...]
    """position の順。"""
    primary_key: Key | None
    uniques: tuple[Key, ...]
    """主キー以外の一意（制約と一意インデックス）。名前の順。"""
    foreign_keys: tuple[ForeignKey, ...]
    """名前の順。"""
    checks: tuple[Check, ...]
    """名前の順。"""

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


@dataclass(frozen=True)
class Scope:
    """読む範囲。仕様 9.2：接続 → スキーマ一覧 → 含めるもの。表単位の選択はしない。"""

    schemas: tuple[str, ...]


@dataclass(frozen=True)
class Catalog:
    scope: Scope
    """読んだ範囲。スキーマは名前の順で重複なし（指定の順序に依存させない）。"""
    tables: tuple[Table, ...]
    """(schema, name) の順。"""

    def table(self, schema: str, name: str) -> Table | None:
        return next((t for t in self.tables if t.schema == schema and t.name == name), None)


class CatalogReader(Protocol):
    """カタログを読むポート。各コネクタが実装する。

    失敗は SourceError（とその下位）で表す。DB 固有の例外を外に出さない。
    """

    def read(self, scope: Scope) -> Catalog: ...


# ---------------------------------------------------------------- エラー


class SourceError(Exception):
    """データソースを読めなかった。原因（DB 固有の例外など）を cause に保持する。"""

    def __init__(self, message: str, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class UnknownSchemaError(SourceError):
    """範囲に指定したスキーマが無い。黙って空のカタログを返さない。"""

    def __init__(self, schemas: Sequence[str]) -> None:
        super().__init__(f"スキーマが無い: {', '.join(schemas)}")
        self.schemas = tuple(schemas)


@dataclass(frozen=True)
class Unsupported:
    feature: str
    """未対応の機能（例 partitioned table）。"""
    location: str
    """どこで見つかったか（例 sales.orders）。"""


class UnsupportedFeatureError(SourceError):
    """範囲内に、このコネクタが未対応の機能があった。見つかったものを全部持つ。"""

    def __init__(self, found: Sequence[Unsupported]) -> None:
        lines = "\n".join(f"  - {u.location}: {u.feature}" for u in found)
        super().__init__(f"未対応の機能がある（{len(found)} 件）:\n{lines}")
        self.found = tuple(found)
