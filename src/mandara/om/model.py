# 生成物。手で編集しない。元: src/mandara/om/schema/om.yaml / 生成: scripts/gen_om_model.sh
from __future__ import annotations

import re
import sys
from datetime import (
    date,
    datetime,
    time
)
from decimal import Decimal
from enum import Enum
from typing import (
    Any,
    ClassVar,
    Literal,
    Optional,
    Union
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer
)


metamodel_version = "1.12.0"
version = "0.1.0"


class ConfiguredBaseModel(BaseModel):
    model_config = ConfigDict(
        serialize_by_alias = True,
        validate_by_name = True,
        validate_assignment = True,
        validate_default = True,
        extra = "forbid",
        arbitrary_types_allowed = True,
        use_enum_values = True,
        strict = False,
    )





class LinkMLMeta(RootModel):
    root: dict[str, Any] = {}
    model_config = ConfigDict(frozen=True)

    def __getattr__(self, key:str):
        return getattr(self.root, key)

    def __getitem__(self, key:str):
        return self.root[key]

    def __setitem__(self, key:str, value):
        self.root[key] = value

    def __contains__(self, key:str) -> bool:
        return key in self.root


linkml_meta = LinkMLMeta({'default_prefix': 'om',
     'default_range': 'string',
     'description': 'オントロジカルメタデータ（OM）の形を定義する。仕様 4章、7.6 の①。 '
                    'ここに書くのは「注釈とは何か」であり、個々のオントロジーの中身（顧客、注文など）ではない。 '
                    '構造的な語彙（表種別、関係型など）は Mandara の意味論に結びつくので enum として持つ。 '
                    '拡張できる語彙（列ドメイン、enum の値、表記揺れ）はデータ（vocabulary.yaml）として持つ。',
     'id': 'https://w3id.org/mandara/om',
     'imports': ['linkml:types'],
     'license': 'Apache-2.0',
     'name': 'mandara_om',
     'prefixes': {'linkml': {'prefix_prefix': 'linkml',
                             'prefix_reference': 'https://w3id.org/linkml/'},
                  'om': {'prefix_prefix': 'om',
                         'prefix_reference': 'https://w3id.org/mandara/om/'}},
     'source_file': 'src/mandara/om/schema/om.yaml',
     'title': 'Mandara OM メタモデル（①）'} )

class ReviewStatus(str, Enum):
    """
    注釈のレビュー状態（4.4）
    """
    proposed = "proposed"
    approved = "approved"
    rejected = "rejected"


class TableKind(str, Enum):
    """
    表種別（4.1）
    """
    domain = "domain"
    """
    ものを表す表。ライフサイクルを持ちうる。コマンド可
    """
    relation = "relation"
    """
    もの同士の関係を表す表（n:m 中間表）。属性を持ってよい。コマンド可
    """
    event = "event"
    """
    出来事を表す表。不変・追記のみ。コマンド不可
    """
    derived = "derived"
    """
    計算・集計で導出された表。コマンド不可
    """


class RelationType(str, Enum):
    """
    関係型（4.1）。推移性と逆関係は各値の annotations に持つ
    """
    is_a = "is-a"
    """
    分類階層。下位は上位の性質を継承する
    """
    part_of = "part-of"
    """
    構成階層。全体と部分
    """
    has = "has"
    """
    属性または参照。細分は has_kind で指定
    """
    derived_from = "derived-from"
    """
    導出元。列や表が、どのソースからどの関数で生まれたか
    """


class HasKind(str, Enum):
    attribute = "attribute"
    reference = "reference"


class Cardinality(str, Enum):
    number_1COLON1 = "1:1"
    number_1COLONn = "1:n"
    nCOLON1 = "n:1"
    nCOLONm = "n:m"


class Additivity(str, Enum):
    """
    尺度の加法性（10.4）
    """
    additive = "additive"
    semi_additive = "semi-additive"
    non_additive = "non-additive"


class DomainRole(str, Enum):
    """
    列ドメインの役割
    """
    event_time = "event_time"
    valid_from = "valid_from"
    valid_to = "valid_to"
    technical = "technical"
    """
    業務の意味を持たない列（行の技術ID、監査用の日時・更新者）。UI・ツール面・推論から既定で外す
    """


class ValueType(str, Enum):
    """
    列ドメインの値の意味の型。物理的な表現（バイナリ形式、文字コードなど）は含めない。 物理的な表現は列の encoding に書く。意味の型への変換は encoding ごとのコーデック（Adapters の中で、DB コネクタから独立したもの）の責務とする
    """
    identifier = "identifier"
    code = "code"
    """
    外部の体系に従うコード（国コード、単位コードなど）。値の一覧は持たない
    """
    enum = "enum"
    """
    値の一覧（values）を語彙に持つ
    """
    text = "text"
    integer = "integer"
    ratio = "ratio"
    quantity = "quantity"
    """
    単位を伴う量
    """
    money = "money"
    """
    通貨を単位とする金額
    """
    date = "date"
    datetime = "datetime"
    geoFULL_STOPpoint = "geo.point"
    """
    地理上の点（緯度・経度）。UI では地図に出す
    """
    geoFULL_STOPlatitude = "geo.latitude"
    geoFULL_STOPlongitude = "geo.longitude"
    mediaFULL_STOPimage = "media.image"
    mediaFULL_STOPaudio = "media.audio"
    mediaFULL_STOPdocument = "media.document"
    documentFULL_STOPxml = "document.xml"
    """
    XML の文書。物理的な表現を名前に含んでいるので見直す（om-backlog M）
    """


class Unit(str, Enum):
    """
    固定の単位（UnitSource.fixed）。足すときは①を変える
    """
    USD = "USD"
    JPY = "JPY"
    each = "each"
    """
    個数
    """
    day = "day"
    hour = "hour"


class SourceEncoding(str, Enum):
    """
    ソース上の値の物理的な表現が、意味の型（ValueType）の標準的な表現と違うときに書く。 具体例のあるものだけを置く。各値の annotations の decodes_to は、変換先の ValueType
    """
    sqlserver_geography = "sqlserver_geography"
    """
    SQL Server の geography 型のバイナリ（SRID 4 バイト、版、属性、緯度・経度の float64）
    """


class IsARealization(str, Enum):
    """
    論理的な is-a が、ソースでどう実装されているか（C3）。 具体例のあるものだけを置く。排他的な参照・区分列は AW で裏付けが取れていないので未定義
    """
    shared_key = "shared_key"
    """
    下位の表が上位の表と同じ主キーを持ち、それで参照する
    """



class Annotation(ConfiguredBaseModel):
    """
    すべての注釈が持つ共通フィールド（4.4）
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'mixin': True})

    status: ReviewStatus = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    evidence: Optional[list[str]] = Field(default=None, description="""主張台帳（6.6）の主張ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    inferred_by: Optional[str] = Field(default=None, description="""提案者のモデル名と版、実行ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    reviewed_by: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    reviewed_at: Optional[date] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    note: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })


class Vocabulary(ConfiguredBaseModel):
    """
    vocabulary.yaml。拡張できる語彙の閉じた集合
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'tree_root': True})

    column_domains: Optional[dict[str, ColumnDomain]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Vocabulary']} })
    aliases: Optional[dict[str, Union[str, AliasEntry]]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Vocabulary']} })


class ColumnDomain(ConfiguredBaseModel):
    """
    列ドメイン。「同じ意味」を表す
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    name: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['ColumnDomain', 'AliasEntry', 'Table', 'Column']} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ColumnDomain']} })
    type: ValueType = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['ColumnDomain', 'Relation']} })
    role: Optional[DomainRole] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ColumnDomain', 'Relation']} })
    additivity: Optional[Additivity] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ColumnDomain']} })
    requires_unit: Optional[bool] = Field(default=None, description="""true なら、この列ドメインを使う列は unit（量と単位の組、C5）を宣言しなければならない""", json_schema_extra = { "linkml_meta": {'domain_of': ['ColumnDomain']} })
    values: Optional[list[EnumValue]] = Field(default=None, description="""type が enum のときの値の一覧（C6）。値の集合はデータからではなく、宣言・証拠から決める""", json_schema_extra = { "linkml_meta": {'domain_of': ['ColumnDomain']} })


class EnumValue(Annotation):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'mixins': ['Annotation']})

    code: str = Field(default=..., description="""ソース上の値の文字列表現。ファイルには文字列で書く（数値の 1 は拒否し、\"1\" と書く）。 観測値との照合時の正規化（真偽値の表記など）はソースの Adapter の責務""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnumValue']} })
    label: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['EnumValue']} })
    meaning: Optional[str] = Field(default=None, description="""語彙や公開オントロジーの概念への参照（CURIE）""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnumValue']} })
    observed: Optional[bool] = Field(default=None, description="""データで観測されたか。値の集合の根拠にはしない""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnumValue']} })
    status: ReviewStatus = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    evidence: Optional[list[str]] = Field(default=None, description="""主張台帳（6.6）の主張ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    inferred_by: Optional[str] = Field(default=None, description="""提案者のモデル名と版、実行ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    reviewed_by: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    reviewed_at: Optional[date] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    note: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })


class AliasEntry(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    name: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['ColumnDomain', 'AliasEntry', 'Table', 'Column']} })
    terms: list[str] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['AliasEntry']} })


class Table(Annotation):
    """
    contexts/<context>/tables/<source>/<table>.yaml（4.2）
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om',
         'mixins': ['Annotation'],
         'tree_root': True})

    id: str = Field(default=..., description="""安定ID。改名しても変わらない""", json_schema_extra = { "linkml_meta": {'domain_of': ['Table', 'Relation']} })
    name: str = Field(default=..., description="""ソース上の名前（schema.table）""", json_schema_extra = { "linkml_meta": {'domain_of': ['ColumnDomain', 'AliasEntry', 'Table', 'Column']} })
    source: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Table']} })
    context: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Table']} })
    kind: TableKind = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Table']} })
    grain: Optional[str] = Field(default=None, description="""1行が何を表すか（10.4）""", json_schema_extra = { "linkml_meta": {'domain_of': ['Table']} })
    identity: Optional[Identity] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Table']} })
    columns: Optional[dict[str, Column]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Table', 'KeyColumns', 'Relation']} })
    relations: Optional[list[Relation]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Table']} })
    status: ReviewStatus = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    evidence: Optional[list[str]] = Field(default=None, description="""主張台帳（6.6）の主張ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    inferred_by: Optional[str] = Field(default=None, description="""提案者のモデル名と版、実行ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    reviewed_by: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    reviewed_at: Optional[date] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    note: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })

    @field_validator('id')
    def pattern_id(cls, v):
        pattern=re.compile(r"^tbl_[0-9A-Za-z_]+$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.search(element):
                    err_msg = f"Invalid id format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.search(v):
            err_msg = f"Invalid id format: {v}"
            raise ValueError(err_msg)
        return v


class Identity(ConfiguredBaseModel):
    """
    同一性（C1）。複合キーは列の配列としてそのまま書く。1つの値として扱うのは生成される型の側の責務
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    primary: list[str] = Field(default=..., min_length=1, json_schema_extra = { "linkml_meta": {'domain_of': ['Identity']} })
    alternates: Optional[list[KeyColumns]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Identity']} })


class KeyColumns(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    columns: list[str] = Field(default=..., min_length=1, json_schema_extra = { "linkml_meta": {'domain_of': ['Table', 'KeyColumns', 'Relation']} })


class Column(Annotation):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'mixins': ['Annotation']})

    name: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['ColumnDomain', 'AliasEntry', 'Table', 'Column']} })
    domain: Optional[str] = Field(default=None, description="""vocabulary の column_domains のキー""", json_schema_extra = { "linkml_meta": {'domain_of': ['Column']} })
    pii: Optional[bool] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Column']} })
    unit: Optional[UnitSource] = Field(default=None, description="""量の単位の出所（C5）。fixed / column のちょうど1つ""", json_schema_extra = { "linkml_meta": {'domain_of': ['Column']} })
    encoding: Optional[SourceEncoding] = Field(default=None, description="""ソース上の値の物理的な表現。意味の型の標準的な表現と同じなら書かない""", json_schema_extra = { "linkml_meta": {'domain_of': ['Column']} })
    status: ReviewStatus = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    evidence: Optional[list[str]] = Field(default=None, description="""主張台帳（6.6）の主張ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    inferred_by: Optional[str] = Field(default=None, description="""提案者のモデル名と版、実行ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    reviewed_by: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    reviewed_at: Optional[date] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    note: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })


class UnitSource(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    fixed: Optional[Unit] = Field(default=None, description="""固定の単位""", json_schema_extra = { "linkml_meta": {'domain_of': ['UnitSource']} })
    column: Optional[str] = Field(default=None, description="""同じ表で単位を持つ列""", json_schema_extra = { "linkml_meta": {'domain_of': ['UnitSource']} })


class Relation(Annotation):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'mixins': ['Annotation']})

    id: str = Field(default=..., description="""関係の安定ID。同じ表への複数の参照（C2）を区別する""", json_schema_extra = { "linkml_meta": {'domain_of': ['Table', 'Relation']} })
    type: RelationType = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['ColumnDomain', 'Relation']} })
    has_kind: Optional[HasKind] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Relation']} })
    role: Optional[str] = Field(default=None, description="""参照の役割（C2。例 bill_to / ship_to）""", json_schema_extra = { "linkml_meta": {'domain_of': ['ColumnDomain', 'Relation']} })
    target: str = Field(default=..., description="""参照先の表の安定ID。自己参照は self と書き、自分の安定ID は書かない""", json_schema_extra = { "linkml_meta": {'domain_of': ['Relation']} })
    via: Optional[list[ColumnPair]] = Field(default=None, description="""結合に使う列の対応。複合キーなら複数""", json_schema_extra = { "linkml_meta": {'domain_of': ['Relation']} })
    via_function: Optional[str] = Field(default=None, description="""列の等値で結べない関係（C4。パス文字列の木など）を結ぶ決定的関数の安定ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Relation']} })
    cardinality: Optional[Cardinality] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Relation']} })
    realized_by: Optional[IsARealization] = Field(default=None, description="""is-a の物理的な実装パターン（C3）""", json_schema_extra = { "linkml_meta": {'domain_of': ['Relation']} })
    columns: Optional[list[str]] = Field(default=None, description="""derived-from で導出される列""", json_schema_extra = { "linkml_meta": {'domain_of': ['Table', 'KeyColumns', 'Relation']} })
    function: Optional[str] = Field(default=None, description="""derived-from の変換関数の安定ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Relation']} })
    status: ReviewStatus = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    evidence: Optional[list[str]] = Field(default=None, description="""主張台帳（6.6）の主張ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    inferred_by: Optional[str] = Field(default=None, description="""提案者のモデル名と版、実行ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    reviewed_by: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    reviewed_at: Optional[date] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    note: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })

    @field_validator('id')
    def pattern_id(cls, v):
        pattern=re.compile(r"^rel_[0-9A-Za-z_]+$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.search(element):
                    err_msg = f"Invalid id format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.search(v):
            err_msg = f"Invalid id format: {v}"
            raise ValueError(err_msg)
        return v


class ColumnPair(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    from_: str = Field(default=..., alias="from", json_schema_extra = { "linkml_meta": {'domain_of': ['ColumnPair']} })
    to: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['ColumnPair']} })


# Model rebuild
# see https://pydantic-docs.helpmanual.io/usage/models/#rebuilding-a-model
Annotation.model_rebuild()
Vocabulary.model_rebuild()
ColumnDomain.model_rebuild()
EnumValue.model_rebuild()
AliasEntry.model_rebuild()
Table.model_rebuild()
Identity.model_rebuild()
KeyColumns.model_rebuild()
Column.model_rebuild()
UnitSource.model_rebuild()
Relation.model_rebuild()
ColumnPair.model_rebuild()
