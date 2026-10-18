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
                    '拡張できる語彙（意味型、enum の値、表記揺れ）はデータ（vocabulary.yaml）として持つ。',
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
    もの同士の関係を表す表（n:m 中間表）。属性を持ってよい。コマンド可。 edge を宣言すると、1行がもの同士を結ぶ1本の辺になり、列はその辺の属性になる
    """
    event = "event"
    """
    出来事を表す表。不変・追記のみ。コマンド不可
    """


class RelationType(str, Enum):
    """
    関係型（4.1）。推移性と逆関係は各値の annotations に持つ
    """
    is_a = "is-a"
    """
    型の階層。下位の型は上位の型の一種で、上位の性質を継承する
    """
    part_of = "part-of"
    """
    構成階層。全体と部分
    """
    classified_as = "classified-as"
    """
    分類への所属。ものから分類へ、下位の分類から上位の分類へ（製品 → サブカテゴリ → カテゴリ）。 全体と部分（part-of）でも、型の一種（is-a）でもない。ドリルダウン階層になる
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


class SemanticRole(str, Enum):
    """
    意味型の役割
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
    意味型の値の型。物理的な表現（バイナリ形式、文字コードなど）は含めない。 物理的な表現は列の encoding に書く。意味の型への変換は encoding ごとのコーデック（I/O の無い純粋な変換。DB コネクタから独立したもの）の責務とする
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


class TypeFamily(str, Enum):
    """
    ソースの列の値の、DB に依存しない型族（カタログ、主張）。 どれにも当てはまらない組み込みの型は other とし、ソース上の型名で区別する
    """
    integer = "integer"
    decimal = "decimal"
    float = "float"
    text = "text"
    boolean = "boolean"
    date = "date"
    time = "time"
    timestamp = "timestamp"
    binary = "binary"
    uuid = "uuid"
    xml = "xml"
    json = "json"
    other = "other"


class ObjectKind(str, Enum):
    """
    ソース上のオブジェクトの種類（カタログ、主張）
    """
    table = "table"
    view = "view"
    materialized_view = "materialized_view"


class ReferentialAction(str, Enum):
    """
    外部キーの参照先の行が消える・変わるときの動作（SQL 標準）。cascade は合成関係の証拠になる
    """
    no_action = "no_action"
    restrict = "restrict"
    cascade = "cascade"
    set_null = "set_null"
    set_default = "set_default"


class ClaimPredicate(str, Enum):
    """
    主張の述語（6.6.2）。述語ごとに、主語の粒度（表 / 列）と、値として持つフィールドが決まっている。 カタログの抽出器（T1.3）と、実データの値の抽出器（T2.4、参照の被覆）が出す。足すときは①を変え、検証の形の表も足す
    """
    identified_by = "identified_by"
    """
    表は columns で識別される（主キー）
    """
    unique = "unique"
    """
    表の columns の組は一意
    """
    references = "references"
    """
    表の columns が、target の target_columns を参照する（外部キー）。on_delete / on_update を持つ
    """
    declared_as = "declared_as"
    """
    表・ビューなどのオブジェクトの種類は object_kind
    """
    not_null = "not_null"
    """
    列は NULL を持たない
    """
    nullable = "nullable"
    """
    列は NULL を持ちうる（宣言されている）。宣言の無い列（ビューの列など）には、not_null も nullable も出さない
    """
    defaults_to = "defaults_to"
    """
    列の既定値は text の式
    """
    described_as = "described_as"
    """
    表または列は text で説明されている（コメント）
    """
    checks = "checks"
    """
    表の行は text の式を満たす（CHECK）
    """
    typed_as = "typed_as"
    """
    列のソース上の型名は type_name、型族は family、表の中の位置は position
    """
    aliased_as = "aliased_as"
    """
    列はユーザー定義の型の別名 type_name を使う
    """
    encoded_as = "encoded_as"
    """
    列の値は物理的な表現 encoding で書かれている（実データの値の抽出器 T2.4 が出す。読んだ値がすべてそのコーデックで読めた）
    """
    amounts_in = "amounts_in"
    """
    表の金額は、行が別の単位を言わない限り fixed_unit（人が宣言したソースの性質。宣言のファイルから抽出のたびに作る）
    """
    covers_all = "covers_all"
    """
    表の columns で target の target_columns を参照する行が、target のすべての行にある（実データ。target が空なら出さない）
    """
    discriminated_by = "discriminated_by"
    """
    表の columns で target の target_columns を参照される target の行は、ちょうど target の列 discriminator が discriminator_values のどれかである行（実データ。区分の列。値の一部だけが参照され、残りは参照されない）
    """
    included_in = "included_in"
    """
    表の columns（NULL を除く）の値を数えた結果：異なる値が distinct_values 個、そのうち target の target_columns （キー）にあるのが matched_values 個。target のキーの値は target_values 個。1 つの値の行は最大 max_repeats 行。 実データの測定で、宣言の無い参照（外部キー）の候補の事実。包含が成り立たない組も、否定の証拠として残す（仕様 6.7.2 B / D）
    """


class GroupView(str, Enum):
    """
    近隣の集まりの見せ方。OM（種類、意味型の役割と加法性）と件数から機械的に決める（browse.group_view）
    """
    list = "list"
    """
    個々のサマリを並べる（件数が少ない）
    """
    summary = "summary"
    """
    件数、尺度の集計、enum の内訳、新しい（または先頭の）数件
    """
    time_series = "time_series"
    """
    時系列の線グラフ（出来事の時刻の列があり、件数が多い）
    """
    timeline = "timeline"
    """
    期間の帯（valid_from と valid_to の列を持つ）
    """


class MeasureOp(str, Enum):
    """
    尺度の集計のしかた（加法性から決まる。additive は sum、non-additive は avg。semi-additive は集まり全体では集計しない （時間の軸が無いと OM から言えないため）。最新の時点の行だけで合計する（LatestDay.snapshot））
    """
    sum = "sum"
    avg = "avg"


class BucketUnit(str, Enum):
    """
    時系列の区切り（期間の幅から決まる）
    """
    day = "day"
    month = "month"
    year = "year"


class LabelSource(str, Enum):
    """
    インスタンスの見出しの出どころ
    """
    own = "own"
    """
    その表の見出しの列
    """
    borrowed = "borrowed"
    """
    is-a の上位（主キーを共有する）の同じキーの行の見出し
    """
    borrowed_missing = "borrowed_missing"
    """
    上位から借りるはずが、上位に同じキーの行が無い（壊れた参照）。見出しはキー
    """
    key = "key"
    """
    見出しの列も借りる先も無いので、キー
    """


class ClaimSourceKind(str, Enum):
    """
    主張の出所の種類（6.6.2）
    """
    schema = "schema"
    sql = "sql"
    codelist = "codelist"
    data = "data"
    """
    ソースの実データの値（列の値を読んだもの）
    """
    declaration = "declaration"
    """
    人が宣言したソースの性質（work/<source>/declarations.yaml。抽出のたびに主張に作り直す）
    """
    design_doc = "design-doc"
    memo = "memo"
    package = "package"
    world = "world"
    review = "review"


class OntologySourceKind(str, Enum):
    """
    見られる OM の出どころ
    """
    work = "work"
    """
    提案した OM（work/<source>/ontology）
    """
    gold = "gold"
    """
    正解 OM
    """


class LinkState(str, Enum):
    """
    参照先（n:1）へのリンクの状態（ドメインビューワ）
    """
    found = "found"
    """
    参照先の行がある
    """
    null = "null"
    """
    参照の列が NULL（参照していない）
    """
    missing = "missing"
    """
    参照の列に値があるのに、参照先の行が無い（壊れた参照）
    """


class RelatedKind(str, Enum):
    """
    インスタンスの近隣の集まりの種類（ドメインビューワ）
    """
    children = "children"
    """
    他のドメインの行がこのインスタンスを参照している（1:n。受注 → 明細）
    """
    via = "via"
    """
    relation の表を挟んだ先のインスタンス（n:m。製品 ⇄ 仕入先）。relation の表そのものは出さない
    """


class ReviewSubject(str, Enum):
    """
    レビューの問いが判断するもの（注釈を持つクラス）
    """
    table = "table"
    relation = "relation"
    edge = "edge"
    column = "column"
    enum_value = "enum_value"


class ReviewDecision(str, Enum):
    """
    レビューの答え（仕様 9.3）。「別の意味」は T3.6
    """
    approve = "approve"
    reject = "reject"


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
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation', 'Claim', 'ReviewQuestion']} })
    evidence: Optional[list[str]] = Field(default=None, description="""主張台帳（6.6）の主張ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation', 'ReviewQuestion']} })
    inferred_by: Optional[str] = Field(default=None, description="""提案者のモデル名と版、実行ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    reviewed_by: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    reviewed_at: Optional[date] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    note: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })


class Vocabulary(ConfiguredBaseModel):
    """
    vocabulary.yaml。拡張できる語彙の閉じた集合
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'tree_root': True})

    semantic_types: Optional[dict[str, SemanticType]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Vocabulary']} })
    aliases: Optional[dict[str, Union[str, AliasEntry]]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Vocabulary']} })


class SemanticType(ConfiguredBaseModel):
    """
    意味型。列の値が何を意味するか（住所の ID、受注日、金額）。別の表の列でも、意味が同じなら同じ意味型にする。 同じ値の型でも意味が違えば別の意味型にする（受注日と出荷日）
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    name: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType',
                       'AliasEntry',
                       'Table',
                       'Column',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'Instance',
                       'RelatedGroup',
                       'LinkGroup']} })
    description: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType']} })
    type: ValueType = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType', 'Edge', 'Relation']} })
    role: Optional[SemanticRole] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType', 'Relation', 'InstanceLink']} })
    additivity: Optional[Additivity] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType']} })
    requires_unit: Optional[bool] = Field(default=None, description="""true なら、この意味型を使う列は unit（量と単位の組、C5）を宣言しなければならない""", json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType']} })
    values: Optional[list[EnumValue]] = Field(default=None, description="""type が enum のときの値の一覧（C6）。値の集合はデータからではなく、宣言・証拠から決める""", json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType']} })


class EnumValue(Annotation):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'mixins': ['Annotation']})

    code: str = Field(default=..., description="""ソース上の値の文字列表現。ファイルには文字列で書く（数値の 1 は拒否し、\"1\" と書く）。 観測値との照合時の正規化（真偽値の表記など）はソースの Adapter の責務""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnumValue']} })
    label: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['EnumValue',
                       'OntologyListing',
                       'Within',
                       'InstanceSummary',
                       'Instance']} })
    meaning: Optional[str] = Field(default=None, description="""語彙や公開オントロジーの概念への参照（CURIE）""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnumValue']} })
    observed: Optional[bool] = Field(default=None, description="""データで観測されたか。値の集合の根拠にはしない""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnumValue']} })
    status: ReviewStatus = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation', 'Claim', 'ReviewQuestion']} })
    evidence: Optional[list[str]] = Field(default=None, description="""主張台帳（6.6）の主張ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation', 'ReviewQuestion']} })
    inferred_by: Optional[str] = Field(default=None, description="""提案者のモデル名と版、実行ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    reviewed_by: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    reviewed_at: Optional[date] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    note: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })


class AliasEntry(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    name: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType',
                       'AliasEntry',
                       'Table',
                       'Column',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'Instance',
                       'RelatedGroup',
                       'LinkGroup']} })
    terms: list[str] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['AliasEntry']} })


class Table(Annotation):
    """
    contexts/<context>/tables/<source>/<table>.yaml（4.2）
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om',
         'mixins': ['Annotation'],
         'tree_root': True})

    id: str = Field(default=..., description="""安定ID。改名しても変わらない""", json_schema_extra = { "linkml_meta": {'domain_of': ['Table', 'Relation', 'Claim', 'OntologyListing', 'EvidenceLine']} })
    name: str = Field(default=..., description="""ソース上の名前（schema.table）""", json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType',
                       'AliasEntry',
                       'Table',
                       'Column',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'Instance',
                       'RelatedGroup',
                       'LinkGroup']} })
    source: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Table', 'Claim', 'SourceRef']} })
    context: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Table', 'DomainSummary']} })
    kind: Optional[TableKind] = Field(default=None, description="""表種別。提案の段階では未決（無い）でもよい（規則や証拠で決まらないものを既定値で埋めない）。 approved の表には必須""", json_schema_extra = { "linkml_meta": {'domain_of': ['Table',
                       'ClaimSource',
                       'OntologyListing',
                       'ReviewAnswer',
                       'DomainSummary',
                       'RelatedGroup']} })
    derived: Optional[bool] = Field(default=None, description="""計算・集計で導出された表（ビューなど）。kind（何を表すか）とは別の軸で、どの kind とも組み合わさる。 コマンド不可。part-of の全体側にならない。ソースが実体の表なら書かない""", json_schema_extra = { "linkml_meta": {'domain_of': ['Table']} })
    grain: Optional[str] = Field(default=None, description="""1行が何を表すか（10.4）""", json_schema_extra = { "linkml_meta": {'domain_of': ['Table', 'DomainSummary']} })
    identity: Optional[Identity] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Table']} })
    columns: Optional[dict[str, Column]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Table',
                       'KeyColumns',
                       'Relation',
                       'Claim',
                       'DomainRows',
                       'InstanceLink',
                       'RelatedGroup']} })
    relations: Optional[list[Relation]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Table']} })
    edge: Optional[Edge] = Field(default=None, description="""relation の表だけが持てる。この表の行が実現する、もの同士の関係""", json_schema_extra = { "linkml_meta": {'domain_of': ['Table']} })
    status: ReviewStatus = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation', 'Claim', 'ReviewQuestion']} })
    evidence: Optional[list[str]] = Field(default=None, description="""主張台帳（6.6）の主張ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation', 'ReviewQuestion']} })
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


class Edge(Annotation):
    """
    中間表の1行を、もの同士を結ぶ1本の辺とみなす（プロパティグラフの辺）。 from と to は、この表が持つ n:1 の参照（has / reference）の ID。 辺の関係型の意味論（推移性、循環の禁止、ドリルダウン）は、表の間の関係と同じに効く。 from / to の列が NULL の行は辺にならない。type が has の辺は参照（reference）を表す
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'mixins': ['Annotation']})

    type: RelationType = Field(default=..., description="""has / part-of / classified-as のどれか。is-a は型の間の関係、derived-from は関数による導出なので、辺にはならない""", json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType', 'Edge', 'Relation']} })
    from_: str = Field(default=..., alias="from", description="""辺の始点側の参照の ID（part-of なら部分、classified-as なら分類されるもの）""", json_schema_extra = { "linkml_meta": {'domain_of': ['Edge', 'ColumnPair']} })
    to: str = Field(default=..., description="""辺の終点側の参照の ID（part-of なら全体、classified-as なら分類）""", json_schema_extra = { "linkml_meta": {'domain_of': ['Edge', 'ColumnPair']} })
    status: ReviewStatus = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation', 'Claim', 'ReviewQuestion']} })
    evidence: Optional[list[str]] = Field(default=None, description="""主張台帳（6.6）の主張ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation', 'ReviewQuestion']} })
    inferred_by: Optional[str] = Field(default=None, description="""提案者のモデル名と版、実行ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    reviewed_by: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    reviewed_at: Optional[date] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    note: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })


class Identity(ConfiguredBaseModel):
    """
    同一性（C1）。複合キーは列の配列としてそのまま書く。1つの値として扱うのは生成される型の側の責務
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    primary: list[str] = Field(default=..., min_length=1, json_schema_extra = { "linkml_meta": {'domain_of': ['Identity']} })
    alternates: Optional[list[KeyColumns]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Identity']} })


class KeyColumns(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    columns: list[str] = Field(default=..., min_length=1, json_schema_extra = { "linkml_meta": {'domain_of': ['Table',
                       'KeyColumns',
                       'Relation',
                       'Claim',
                       'DomainRows',
                       'InstanceLink',
                       'RelatedGroup']} })


class Column(Annotation):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'mixins': ['Annotation']})

    name: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType',
                       'AliasEntry',
                       'Table',
                       'Column',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'Instance',
                       'RelatedGroup',
                       'LinkGroup']} })
    semantic_type: Optional[str] = Field(default=None, description="""vocabulary の semantic_types のキー""", json_schema_extra = { "linkml_meta": {'domain_of': ['Column', 'Cell']} })
    pii: Optional[bool] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Column']} })
    unit: Optional[UnitSource] = Field(default=None, description="""量の単位の出所（C5）。fixed / column のちょうど1つ""", json_schema_extra = { "linkml_meta": {'domain_of': ['Column']} })
    encoding: Optional[SourceEncoding] = Field(default=None, description="""ソース上の値の物理的な表現。意味の型の標準的な表現と同じなら書かない""", json_schema_extra = { "linkml_meta": {'domain_of': ['Column', 'Claim']} })
    status: ReviewStatus = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation', 'Claim', 'ReviewQuestion']} })
    evidence: Optional[list[str]] = Field(default=None, description="""主張台帳（6.6）の主張ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation', 'ReviewQuestion']} })
    inferred_by: Optional[str] = Field(default=None, description="""提案者のモデル名と版、実行ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    reviewed_by: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    reviewed_at: Optional[date] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    note: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })


class UnitSource(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    fixed: Optional[Unit] = Field(default=None, description="""固定の単位""", json_schema_extra = { "linkml_meta": {'domain_of': ['UnitSource']} })
    column: Optional[str] = Field(default=None, description="""同じ表で単位を持つ列""", json_schema_extra = { "linkml_meta": {'domain_of': ['UnitSource',
                       'SourceRef',
                       'Cell',
                       'MeasureValue',
                       'SkippedMeasure',
                       'PointSet']} })


class Relation(Annotation):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'mixins': ['Annotation']})

    id: str = Field(default=..., description="""関係の安定ID。同じ表への複数の参照（C2）を区別する""", json_schema_extra = { "linkml_meta": {'domain_of': ['Table', 'Relation', 'Claim', 'OntologyListing', 'EvidenceLine']} })
    type: RelationType = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType', 'Edge', 'Relation']} })
    has_kind: Optional[HasKind] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Relation']} })
    role: Optional[str] = Field(default=None, description="""参照の役割（C2。例 bill_to / ship_to）""", json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType', 'Relation', 'InstanceLink']} })
    target: str = Field(default=..., description="""参照先の表の安定ID。自己参照は self と書き、自分の安定ID は書かない""", json_schema_extra = { "linkml_meta": {'domain_of': ['Relation', 'Claim', 'InstanceLink']} })
    via: Optional[list[ColumnPair]] = Field(default=None, description="""結合に使う列の対応。複合キーなら複数""", json_schema_extra = { "linkml_meta": {'domain_of': ['Relation']} })
    via_function: Optional[str] = Field(default=None, description="""列の等値で結べない関係（C4。パス文字列の木など）を結ぶ決定的関数の安定ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Relation']} })
    cardinality: Optional[Cardinality] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Relation']} })
    realized_by: Optional[IsARealization] = Field(default=None, description="""is-a の物理的な実装パターン（C3）""", json_schema_extra = { "linkml_meta": {'domain_of': ['Relation']} })
    columns: Optional[list[str]] = Field(default=None, description="""derived-from で導出される列""", json_schema_extra = { "linkml_meta": {'domain_of': ['Table',
                       'KeyColumns',
                       'Relation',
                       'Claim',
                       'DomainRows',
                       'InstanceLink',
                       'RelatedGroup']} })
    function: Optional[str] = Field(default=None, description="""derived-from の変換関数の安定ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Relation']} })
    status: ReviewStatus = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation']} })
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation', 'Claim', 'ReviewQuestion']} })
    evidence: Optional[list[str]] = Field(default=None, description="""主張台帳（6.6）の主張ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation', 'ReviewQuestion']} })
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

    from_: str = Field(default=..., alias="from", json_schema_extra = { "linkml_meta": {'domain_of': ['Edge', 'ColumnPair']} })
    to: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Edge', 'ColumnPair']} })


class ClaimFile(ConfiguredBaseModel):
    """
    work/<source>/claims/<schema>/<table>.yaml。その表を主語とする主張（列を主語とするものを含む）の一覧。 決定的な抽出器の主張は再生成できるキャッシュで、git に乗せない
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'tree_root': True})

    claims: Optional[list[Claim]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ClaimFile']} })


class Claim(ConfiguredBaseModel):
    """
    ソースについての主張。id は内容（subject、predicate、値のフィールド、source）から決まり、 抽出器の版・確信度・実行時刻には依存しない。再生成しても同じ id になる
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    id: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Table', 'Relation', 'Claim', 'OntologyListing', 'EvidenceLine']} })
    subject: SourceRef = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Claim', 'ReviewQuestion']} })
    predicate: ClaimPredicate = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    columns: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Table',
                       'KeyColumns',
                       'Relation',
                       'Claim',
                       'DomainRows',
                       'InstanceLink',
                       'RelatedGroup']} })
    target: Optional[SourceRef] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Relation', 'Claim', 'InstanceLink']} })
    target_columns: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    on_delete: Optional[ReferentialAction] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    on_update: Optional[ReferentialAction] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    text: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Claim', 'ReviewQuestion', 'EvidenceLine']} })
    type_name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    family: Optional[TypeFamily] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    position: Optional[int] = Field(default=None, description="""列の、表の中の位置（1 から）""", ge=1, json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    object_kind: Optional[ObjectKind] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    encoding: Optional[SourceEncoding] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Column', 'Claim']} })
    fixed_unit: Optional[Unit] = Field(default=None, description="""固定の単位（amounts_in）""", json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    discriminator: Optional[str] = Field(default=None, description="""区分の列の名前（target の列）""", json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    discriminator_values: Optional[list[str]] = Field(default=None, description="""区分の列の値（ソースの値の文字列表現。NULL は含めない）""", json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    distinct_values: Optional[int] = Field(default=None, description="""included_in：表の columns の、NULL を除いた異なる値の数""", ge=0, json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    matched_values: Optional[int] = Field(default=None, description="""included_in：そのうち target の target_columns にある値の数""", ge=0, json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    target_values: Optional[int] = Field(default=None, description="""included_in：target の target_columns（キー）の値の数""", ge=0, json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    max_repeats: Optional[int] = Field(default=None, description="""included_in：表の columns の1つの値を持つ行の最大の数（1 なら値は重ならない。1:1 の手がかり）""", ge=0, json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    source: ClaimSource = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Table', 'Claim', 'SourceRef']} })
    extracted_by: str = Field(default=..., description="""抽出器の名前と版""", json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    valid_at: Optional[date] = Field(default=None, description="""出所の日付（文書の日付など）。鮮度の根拠""", json_schema_extra = { "linkml_meta": {'domain_of': ['Claim']} })
    confidence: float = Field(default=..., ge=0.0, le=1.0, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation', 'Claim', 'ReviewQuestion']} })

    @field_validator('id')
    def pattern_id(cls, v):
        pattern=re.compile(r"^clm_[0-9a-f]{16}$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.search(element):
                    err_msg = f"Invalid id format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.search(v):
            err_msg = f"Invalid id format: {v}"
            raise ValueError(err_msg)
        return v


class SourceDeclarations(ConfiguredBaseModel):
    """
    人が宣言するソースの性質（work/<source>/declarations.yaml）。データやカタログからは分からないことだけを書く。 抽出のたびに主張（source.kind: declaration）にし、推論の根拠にする
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'tree_root': True})

    default_currency: Optional[Unit] = Field(default=None, description="""ソースの金額の通貨（行が別の単位を言わない限り）""", json_schema_extra = { "linkml_meta": {'domain_of': ['SourceDeclarations']} })


class SourceRef(ConfiguredBaseModel):
    """
    データソース上の表または列。OM の安定 ID ができる前の段階なので、ソース上の名前で指す
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    source: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Table', 'Claim', 'SourceRef']} })
    schema_name: str = Field(default=..., description="""スキーマ名（schema は pydantic の BaseModel と衝突するので避ける）""", json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef']} })
    table: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef',
                       'ReviewQuestion',
                       'TableApproval',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'InstanceSummary',
                       'Instance',
                       'OmittedRelation',
                       'RelatedGroup',
                       'LinkGroup',
                       'PointSet']} })
    column: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['UnitSource',
                       'SourceRef',
                       'Cell',
                       'MeasureValue',
                       'SkippedMeasure',
                       'PointSet']} })


class ClaimSource(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    kind: ClaimSourceKind = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Table',
                       'ClaimSource',
                       'OntologyListing',
                       'ReviewAnswer',
                       'DomainSummary',
                       'RelatedGroup']} })
    locator: str = Field(default=..., description="""出所の中の場所（制約名、ファイルと行など）""", json_schema_extra = { "linkml_meta": {'domain_of': ['ClaimSource']} })


class OntologyIndex(ConfiguredBaseModel):
    """
    見られる OM の一覧。プロセス境界（HTTP API → UI）で受け渡す形
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'tree_root': True})

    ontologies: list[OntologyListing] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['OntologyIndex']} })


class OntologyListing(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    id: str = Field(default=..., description="""URL に使う ID（例 work:adventureworks、gold:adventureworks）""", json_schema_extra = { "linkml_meta": {'domain_of': ['Table', 'Relation', 'Claim', 'OntologyListing', 'EvidenceLine']} })
    label: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['EnumValue',
                       'OntologyListing',
                       'Within',
                       'InstanceSummary',
                       'Instance']} })
    kind: OntologySourceKind = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Table',
                       'ClaimSource',
                       'OntologyListing',
                       'ReviewAnswer',
                       'DomainSummary',
                       'RelatedGroup']} })
    reviewable: bool = Field(default=..., description="""レビューの問いの列を読めるか（レビューするのは作業領域の OM だけ。正解 OM などは false）""", json_schema_extra = { "linkml_meta": {'domain_of': ['OntologyListing']} })


class OntologyDocument(ConfiguredBaseModel):
    """
    OM 全体を1つの文書にしたもの。プロセス境界（HTTP API → UI）で受け渡す形。 ファイルの配置（仕様 5章）とは別。受け取る側は、①から生成した JSON Schema で検証する
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'tree_root': True})

    vocabulary: Vocabulary = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['OntologyDocument']} })
    tables: list[Table] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['OntologyDocument']} })


class ReviewQueue(ConfiguredBaseModel):
    """
    レビューの問いの列（仕様 9.3）。並びは影響の大きい順。プロセス境界（HTTP API → UI）で受け渡す形
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'tree_root': True})

    ontology: str = Field(default=..., description="""見られる OM の ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['ReviewQueue', 'DomainIndex']} })
    pending: int = Field(default=..., description="""まだ判断されていない提案の数（= questions の数）""", ge=0, json_schema_extra = { "linkml_meta": {'domain_of': ['ReviewQueue']} })
    questions: list[ReviewQuestion] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['ReviewQueue']} })
    writable: bool = Field(default=..., description="""この API で答えを書けるか""", json_schema_extra = { "linkml_meta": {'domain_of': ['ReviewQueue']} })
    not_writable_reason: Optional[str] = Field(default=None, description="""書けないときの理由（writable が false のときだけ）""", json_schema_extra = { "linkml_meta": {'domain_of': ['ReviewQueue']} })
    reviewer: Optional[str] = Field(default=None, description="""書くときの reviewed_by（writable のときだけ）""", json_schema_extra = { "linkml_meta": {'domain_of': ['ReviewQueue']} })
    bulk_minimum: float = Field(default=..., description="""一括承認の閾値の下限""", json_schema_extra = { "linkml_meta": {'domain_of': ['ReviewQueue']} })
    approve_all: bool = Field(default=..., description="""開発用の全承認が使えるか（mandara serve --dev）。中身を見ずに固定するので、開発中に承認済みの状態を試すためだけに使う""", json_schema_extra = { "linkml_meta": {'domain_of': ['ReviewQueue']} })


class ReviewQuestion(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    location: str = Field(default=..., description="""答えを当てる注釈の場所（Ontology.annotations() の場所）""", json_schema_extra = { "linkml_meta": {'domain_of': ['ReviewQuestion', 'ReviewAnswer']} })
    table: Optional[str] = Field(default=None, description="""問いの属する表の安定 ID（語彙の enum の値の問いには無い）""", json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef',
                       'ReviewQuestion',
                       'TableApproval',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'InstanceSummary',
                       'Instance',
                       'OmittedRelation',
                       'RelatedGroup',
                       'LinkGroup',
                       'PointSet']} })
    subject: ReviewSubject = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Claim', 'ReviewQuestion']} })
    text: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Claim', 'ReviewQuestion', 'EvidenceLine']} })
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation', 'Claim', 'ReviewQuestion']} })
    evidence: list[EvidenceLine] = Field(default=..., description="""根拠（仕様 9.3：3行まで）""", json_schema_extra = { "linkml_meta": {'domain_of': ['Annotation', 'ReviewQuestion']} })
    evidence_total: int = Field(default=..., description="""根拠の主張の数（evidence に出さなかったものを含む）""", ge=0, json_schema_extra = { "linkml_meta": {'domain_of': ['ReviewQuestion']} })
    needs_kind: bool = Field(default=..., description="""承認するには表の種類を選ぶ""", json_schema_extra = { "linkml_meta": {'domain_of': ['ReviewQuestion']} })
    bulk_candidate: bool = Field(default=..., description="""一括承認の候補（確信度が bulk_minimum 以上で、種類を選ばなくてよい）""", json_schema_extra = { "linkml_meta": {'domain_of': ['ReviewQuestion']} })


class EvidenceLine(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    id: str = Field(default=..., description="""主張 ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Table', 'Relation', 'Claim', 'OntologyListing', 'EvidenceLine']} })
    text: str = Field(default=..., description="""人が読める1行""", json_schema_extra = { "linkml_meta": {'domain_of': ['Claim', 'ReviewQuestion', 'EvidenceLine']} })


class ReviewAnswer(ConfiguredBaseModel):
    """
    1つの問いへの答え。プロセス境界（UI → HTTP API）で受け渡す形
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'tree_root': True})

    location: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['ReviewQuestion', 'ReviewAnswer']} })
    decision: ReviewDecision = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['ReviewAnswer']} })
    kind: Optional[TableKind] = Field(default=None, description="""種類が未決の表を承認するときに選んだ種類""", json_schema_extra = { "linkml_meta": {'domain_of': ['Table',
                       'ClaimSource',
                       'OntologyListing',
                       'ReviewAnswer',
                       'DomainSummary',
                       'RelatedGroup']} })


class BulkApproval(ConfiguredBaseModel):
    """
    一覧で見せた候補の一括承認（仕様 9.3）。プロセス境界（UI → HTTP API）で受け渡す形
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'tree_root': True})

    threshold: float = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['BulkApproval']} })
    locations: list[str] = Field(default=..., description="""見せた候補の場所。今も候補のままであることを確かめてから承認する""", json_schema_extra = { "linkml_meta": {'domain_of': ['BulkApproval', 'AllApproval', 'TableApproval']} })


class AllApproval(ConfiguredBaseModel):
    """
    開発用の全承認（mandara serve --dev のときだけ）。種類を選ぶ問いを除く、まだ判断されていない問いをすべて承認する。 プロセス境界（UI → HTTP API）で受け渡す形
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'tree_root': True})

    locations: list[str] = Field(default=..., description="""見せた問いの場所。今も種類を選ばなくてよい未判断の問いのままであることを確かめてから承認する""", json_schema_extra = { "linkml_meta": {'domain_of': ['BulkApproval', 'AllApproval', 'TableApproval']} })


class TableApproval(ConfiguredBaseModel):
    """
    表ごとの一括承認（仕様 9.3）。表の提案を一覧で見せ、まとめて承認する（種類を選ぶ問いは除く）。 プロセス境界（UI → HTTP API）で受け渡す形
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'tree_root': True})

    table: str = Field(default=..., description="""表の安定 ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef',
                       'ReviewQuestion',
                       'TableApproval',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'InstanceSummary',
                       'Instance',
                       'OmittedRelation',
                       'RelatedGroup',
                       'LinkGroup',
                       'PointSet']} })
    locations: list[str] = Field(default=..., description="""見せた問いの場所。今もその表の、種類を選ばなくてよい問いのままであることを確かめてから承認する""", json_schema_extra = { "linkml_meta": {'domain_of': ['BulkApproval', 'AllApproval', 'TableApproval']} })


class DomainIndex(ConfiguredBaseModel):
    """
    ドメインビューワのドメインの一覧（仕様 7.6：承認済みの OM から作る）。domain / event の承認済みの表がドメインになる。 relation の表はリンクとしてたどるだけで、ドメインにしない。出さなかった表は理由とともに hidden に持つ（黙って消さない）。 プロセス境界（HTTP API → UI）で受け渡す形
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'tree_root': True})

    ontology: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['ReviewQueue', 'DomainIndex']} })
    contexts: list[str] = Field(default=..., description="""OM のすべてのコンテキスト（名前の順。UI はこの並びでコンテキストの色を決める。グラフと同じ並び）""", json_schema_extra = { "linkml_meta": {'domain_of': ['DomainIndex']} })
    domains: list[DomainSummary] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['DomainIndex']} })
    hidden: list[HiddenTable] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['DomainIndex']} })
    readable: bool = Field(default=..., description="""行を読めるか（ソースへの接続がある）""", json_schema_extra = { "linkml_meta": {'domain_of': ['DomainIndex']} })


class DomainSummary(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    table: str = Field(default=..., description="""表の安定 ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef',
                       'ReviewQuestion',
                       'TableApproval',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'InstanceSummary',
                       'Instance',
                       'OmittedRelation',
                       'RelatedGroup',
                       'LinkGroup',
                       'PointSet']} })
    name: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType',
                       'AliasEntry',
                       'Table',
                       'Column',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'Instance',
                       'RelatedGroup',
                       'LinkGroup']} })
    context: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Table', 'DomainSummary']} })
    kind: TableKind = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Table',
                       'ClaimSource',
                       'OntologyListing',
                       'ReviewAnswer',
                       'DomainSummary',
                       'RelatedGroup']} })
    grain: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Table', 'DomainSummary']} })
    label_columns: list[str] = Field(default=..., description="""見出しに使う列（推定：name という列、無ければ名前が name で終わる文字の列を3つまで）。 空なら label_from の見出し、それも無ければキーが見出し""", json_schema_extra = { "linkml_meta": {'domain_of': ['DomainSummary']} })
    label_from: Optional[str] = Field(default=None, description="""見出しの列が無いとき、見出しを借りる is-a の上位のドメイン（表の安定 ID。主キーを共有するので同じキーの行の見出し）""", json_schema_extra = { "linkml_meta": {'domain_of': ['DomainSummary']} })
    key_columns: list[str] = Field(default=..., description="""主キーの列""", json_schema_extra = { "linkml_meta": {'domain_of': ['DomainSummary']} })
    natural_keys: list[str] = Field(default=..., description="""代替キー（自然キー）の列（どれかの代替キーに含まれる列）""", json_schema_extra = { "linkml_meta": {'domain_of': ['DomainSummary']} })
    searchable: list[str] = Field(default=..., description="""検索に使う列（文字の部分一致）""", json_schema_extra = { "linkml_meta": {'domain_of': ['DomainSummary']} })


class HiddenTable(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    table: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef',
                       'ReviewQuestion',
                       'TableApproval',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'InstanceSummary',
                       'Instance',
                       'OmittedRelation',
                       'RelatedGroup',
                       'LinkGroup',
                       'PointSet']} })
    name: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType',
                       'AliasEntry',
                       'Table',
                       'Column',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'Instance',
                       'RelatedGroup',
                       'LinkGroup']} })
    reason: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['HiddenTable',
                       'OmittedRelation',
                       'SkippedMeasure',
                       'PointFailure']} })


class Within(ConfiguredBaseModel):
    """
    集合を絞るインスタンス（親）と、その親への関係。1:n なら子の表から親への関係（relation）。n:m なら relation の表を挟む： relation は relation の表から集合の表への関係、via_relation は relation の表から親への関係（RelatedGroup と同じ意味）
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    table: str = Field(default=..., description="""親のドメイン（表の安定 ID）""", json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef',
                       'ReviewQuestion',
                       'TableApproval',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'InstanceSummary',
                       'Instance',
                       'OmittedRelation',
                       'RelatedGroup',
                       'LinkGroup',
                       'PointSet']} })
    key: list[str] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Within',
                       'InstanceSummary',
                       'Instance',
                       'GeoPointRow',
                       'PointFailure']} })
    relation: str = Field(default=..., description="""1:n なら子の表から親への関係、n:m なら relation の表から集合の表への関係の安定 ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Within', 'OmittedRelation', 'InstanceLink', 'RelatedGroup']} })
    via_relation: Optional[str] = Field(default=None, description="""n:m のとき、relation の表から親への関係の安定 ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Within', 'RelatedGroup']} })
    label: str = Field(default=..., description="""親のインスタンスの見出し""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnumValue',
                       'OntologyListing',
                       'Within',
                       'InstanceSummary',
                       'Instance']} })
    name: str = Field(default=..., description="""親の表の名前""", json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType',
                       'AliasEntry',
                       'Table',
                       'Column',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'Instance',
                       'RelatedGroup',
                       'LinkGroup']} })


class InstanceSummary(ConfiguredBaseModel):
    """
    インスタンス（行）の見出し。key でたどれる
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    table: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef',
                       'ReviewQuestion',
                       'TableApproval',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'InstanceSummary',
                       'Instance',
                       'OmittedRelation',
                       'RelatedGroup',
                       'LinkGroup',
                       'PointSet']} })
    key: list[str] = Field(default=..., description="""主キーの値（文字列の表現、主キーの列の順）""", json_schema_extra = { "linkml_meta": {'domain_of': ['Within',
                       'InstanceSummary',
                       'Instance',
                       'GeoPointRow',
                       'PointFailure']} })
    label: str = Field(default=..., description="""見出し（出どころは label_source）""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnumValue',
                       'OntologyListing',
                       'Within',
                       'InstanceSummary',
                       'Instance']} })
    label_source: LabelSource = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['InstanceSummary']} })
    cells: list[Cell] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['InstanceSummary']} })
    link_cells: Optional[list[Cell]] = Field(default=None, description="""via の集まりの行で、この行と結んだ relation の表の行の値（参照の列、技術列、代理キーを除く。期間や数量など、 つながりの属性）。代理キーは、1列で参照でない relation の表の主キー（行の番号でしかない。構造からの推定）。 via の集まりは relation の表の行ごとに1つ（同じ先が別の期間で2回出ることがある）""", json_schema_extra = { "linkml_meta": {'domain_of': ['InstanceSummary']} })


class Cell(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    column: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['UnitSource',
                       'SourceRef',
                       'Cell',
                       'MeasureValue',
                       'SkippedMeasure',
                       'PointSet']} })
    value: Optional[str] = Field(default=None, description="""表示の文字列。NULL は無し""", json_schema_extra = { "linkml_meta": {'domain_of': ['Cell', 'MeasureValue', 'BreakdownValue', 'SeriesSum']} })
    semantic_type: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Column', 'Cell']} })


class Instance(ConfiguredBaseModel):
    """
    インスタンスのビュー（行のイメージ）。自分の値、参照先へのリンク（n:1）、近隣のインスタンスの集まり （参照してくる行 1:n、relation の表を挟んだ先 n:m）。プロセス境界（HTTP API → UI）で受け渡す形
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'tree_root': True})

    table: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef',
                       'ReviewQuestion',
                       'TableApproval',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'InstanceSummary',
                       'Instance',
                       'OmittedRelation',
                       'RelatedGroup',
                       'LinkGroup',
                       'PointSet']} })
    name: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType',
                       'AliasEntry',
                       'Table',
                       'Column',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'Instance',
                       'RelatedGroup',
                       'LinkGroup']} })
    key: list[str] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Within',
                       'InstanceSummary',
                       'Instance',
                       'GeoPointRow',
                       'PointFailure']} })
    label: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['EnumValue',
                       'OntologyListing',
                       'Within',
                       'InstanceSummary',
                       'Instance']} })
    fields: list[Cell] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Instance']} })
    links: list[InstanceLink] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['DomainRows', 'Instance', 'RelatedGroup']} })
    related: list[RelatedGroup] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Instance']} })
    omitted: list[OmittedRelation] = Field(default=..., description="""たどらなかった関係と理由（参照先・参照元がドメインでない、列の等値で結べない、など）。黙って省かない""", json_schema_extra = { "linkml_meta": {'domain_of': ['Instance']} })


class OmittedRelation(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    relation: str = Field(default=..., description="""関係の安定 ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Within', 'OmittedRelation', 'InstanceLink', 'RelatedGroup']} })
    table: str = Field(default=..., description="""その関係を持つ表の名前""", json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef',
                       'ReviewQuestion',
                       'TableApproval',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'InstanceSummary',
                       'Instance',
                       'OmittedRelation',
                       'RelatedGroup',
                       'LinkGroup',
                       'PointSet']} })
    reason: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['HiddenTable',
                       'OmittedRelation',
                       'SkippedMeasure',
                       'PointFailure']} })


class InstanceLink(ConfiguredBaseModel):
    """
    参照先（n:1）のインスタンスへのリンク。target は state が found のときだけ
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    relation: str = Field(default=..., description="""関係の安定 ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Within', 'OmittedRelation', 'InstanceLink', 'RelatedGroup']} })
    relation_type: RelationType = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['InstanceLink', 'RelatedGroup']} })
    role: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType', 'Relation', 'InstanceLink']} })
    columns: list[str] = Field(default=..., description="""参照に使う自分の列""", json_schema_extra = { "linkml_meta": {'domain_of': ['Table',
                       'KeyColumns',
                       'Relation',
                       'Claim',
                       'DomainRows',
                       'InstanceLink',
                       'RelatedGroup']} })
    target_table: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['InstanceLink']} })
    target_name: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['InstanceLink']} })
    state: LinkState = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['InstanceLink']} })
    target: Optional[InstanceSummary] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Relation', 'Claim', 'InstanceLink']} })


class GroupProfile(ConfiguredBaseModel):
    """
    集まり・集合の見せ方と集計（browse.group_view と _profile）。インスタンスの近隣の集まり（RelatedGroup）と、 ドメインの集合（DomainRows）が同じ形で持つ（部分集合も全体も同じ規則で見せる）
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'mixin': True})

    view: GroupView = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    count: int = Field(default=..., description="""集まり・集合の行の数（集合なら検索で絞る前。LinkGroup なら relation の表の行の数）。rows は先頭・新しいものからの一部""", ge=0, json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile', 'BreakdownValue', 'SeriesPoint', 'LatestDay']} })
    time_column: Optional[str] = Field(default=None, description="""出来事の時刻の列（role event_time。LinkGroup なら relation の表の列）。rows はこの列の新しい順""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    period_from: Optional[str] = Field(default=None, description="""期間の始まりの列（role valid_from。LinkGroup なら relation の表の列）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    period_to: Optional[str] = Field(default=None, description="""期間の終わりの列（role valid_to）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    measures: Optional[list[MeasureValue]] = Field(default=None, description="""尺度の集計（集まり全体）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile', 'LatestDay']} })
    skipped_measures: Optional[list[SkippedMeasure]] = Field(default=None, description="""集計しなかった尺度と理由（単位が混ざる、時間の軸で合計できない、など）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    breakdown_column: Optional[str] = Field(default=None, description="""内訳の列（enum の意味型の列）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    breakdown: Optional[list[BreakdownValue]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    breakdown_truncated: Optional[bool] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    series_unit: Optional[BucketUnit] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    series_measures: Optional[list[str]] = Field(default=None, description="""series の各点の values に入る尺度の列（合計）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    series: Optional[list[SeriesPoint]] = Field(default=None, description="""区切りの古い順。行の無い区切りも count 0 で入る（時間軸を詰めない）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    latest: Optional[LatestDay] = Field(default=None, description="""最新の日（time_column の値の日付で最も新しい日）のまとめ。データが今日まで届いていれば今日（is_today）。 届いていなければ最新の日を基準日として明示する""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })


class DomainRows(GroupProfile):
    """
    ドメインの集合のビュー（表のイメージ）。within があれば、そのインスタンスにつながる行だけの部分集合（八百屋 X の野菜）。 集合の見せ方と集計（GroupProfile。インスタンスの近隣の集まりと同じ規則・同じ形）は集合全体について、rows は検索（query）で 絞った先頭・新しいものから。プロセス境界（HTTP API → UI）で受け渡す形
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om',
         'mixins': ['GroupProfile'],
         'tree_root': True})

    table: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef',
                       'ReviewQuestion',
                       'TableApproval',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'InstanceSummary',
                       'Instance',
                       'OmittedRelation',
                       'RelatedGroup',
                       'LinkGroup',
                       'PointSet']} })
    name: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType',
                       'AliasEntry',
                       'Table',
                       'Column',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'Instance',
                       'RelatedGroup',
                       'LinkGroup']} })
    within: Optional[Within] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DomainRows']} })
    links: Optional[LinkGroup] = Field(default=None, description="""n:m で絞ったとき（within.via_relation がある）、親とのつながり（relation の行）の集計。インスタンスの n:m の 集まりの links と同じ""", json_schema_extra = { "linkml_meta": {'domain_of': ['DomainRows', 'Instance', 'RelatedGroup']} })
    query: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DomainRows']} })
    columns: list[str] = Field(default=..., description="""一覧に出す列（技術列を除く。cells の列はこの中から）""", json_schema_extra = { "linkml_meta": {'domain_of': ['Table',
                       'KeyColumns',
                       'Relation',
                       'Claim',
                       'DomainRows',
                       'InstanceLink',
                       'RelatedGroup']} })
    rows: list[InstanceSummary] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['DomainRows', 'RelatedGroup', 'LinkGroup']} })
    limit: int = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['DomainRows', 'PointSet']} })
    truncated: bool = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['DomainRows', 'RelatedGroup', 'LinkGroup', 'PointSet']} })
    view: GroupView = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    count: int = Field(default=..., description="""集まり・集合の行の数（集合なら検索で絞る前。LinkGroup なら relation の表の行の数）。rows は先頭・新しいものからの一部""", ge=0, json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile', 'BreakdownValue', 'SeriesPoint', 'LatestDay']} })
    time_column: Optional[str] = Field(default=None, description="""出来事の時刻の列（role event_time。LinkGroup なら relation の表の列）。rows はこの列の新しい順""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    period_from: Optional[str] = Field(default=None, description="""期間の始まりの列（role valid_from。LinkGroup なら relation の表の列）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    period_to: Optional[str] = Field(default=None, description="""期間の終わりの列（role valid_to）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    measures: Optional[list[MeasureValue]] = Field(default=None, description="""尺度の集計（集まり全体）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile', 'LatestDay']} })
    skipped_measures: Optional[list[SkippedMeasure]] = Field(default=None, description="""集計しなかった尺度と理由（単位が混ざる、時間の軸で合計できない、など）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    breakdown_column: Optional[str] = Field(default=None, description="""内訳の列（enum の意味型の列）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    breakdown: Optional[list[BreakdownValue]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    breakdown_truncated: Optional[bool] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    series_unit: Optional[BucketUnit] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    series_measures: Optional[list[str]] = Field(default=None, description="""series の各点の values に入る尺度の列（合計）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    series: Optional[list[SeriesPoint]] = Field(default=None, description="""区切りの古い順。行の無い区切りも count 0 で入る（時間軸を詰めない）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    latest: Optional[LatestDay] = Field(default=None, description="""最新の日（time_column の値の日付で最も新しい日）のまとめ。データが今日まで届いていれば今日（is_today）。 届いていなければ最新の日を基準日として明示する""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })


class RelatedGroup(GroupProfile):
    """
    インスタンスの近隣の集まり。見せ方・集計・行は集まりの表（table）の行について：1:n なら参照してくる子の行、n:m なら relation の表の行でつながる先の行（重複しない。八百屋 X の野菜）。n:m のつながり自体（期間・数量などの属性）は links
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'mixins': ['GroupProfile']})

    kind: RelatedKind = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Table',
                       'ClaimSource',
                       'OntologyListing',
                       'ReviewAnswer',
                       'DomainSummary',
                       'RelatedGroup']} })
    relation: str = Field(default=..., description="""たどった関係の安定 ID（via なら、relation の表から先への関係）""", json_schema_extra = { "linkml_meta": {'domain_of': ['Within', 'OmittedRelation', 'InstanceLink', 'RelatedGroup']} })
    relation_type: RelationType = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['InstanceLink', 'RelatedGroup']} })
    table: str = Field(default=..., description="""集まりの行のドメイン""", json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef',
                       'ReviewQuestion',
                       'TableApproval',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'InstanceSummary',
                       'Instance',
                       'OmittedRelation',
                       'RelatedGroup',
                       'LinkGroup',
                       'PointSet']} })
    name: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType',
                       'AliasEntry',
                       'Table',
                       'Column',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'Instance',
                       'RelatedGroup',
                       'LinkGroup']} })
    columns: list[str] = Field(default=..., description="""参照に使う列。children なら集まりの表の列（このインスタンスを指す列）、via なら relation の表の列（先を指す列）""", json_schema_extra = { "linkml_meta": {'domain_of': ['Table',
                       'KeyColumns',
                       'Relation',
                       'Claim',
                       'DomainRows',
                       'InstanceLink',
                       'RelatedGroup']} })
    via_table: Optional[str] = Field(default=None, description="""挟んだ relation の表（via のとき）""", json_schema_extra = { "linkml_meta": {'domain_of': ['RelatedGroup']} })
    via_relation: Optional[str] = Field(default=None, description="""relation の表から、このインスタンスへの関係の安定 ID（via のとき）。relation の表が同じ表へ2本の参照を持つと （部品表の組立品と部品）、どちら側から見た集まりかを区別する""", json_schema_extra = { "linkml_meta": {'domain_of': ['Within', 'RelatedGroup']} })
    via_columns: Optional[list[str]] = Field(default=None, description="""relation の表の、このインスタンスを指す列（via のとき）""", json_schema_extra = { "linkml_meta": {'domain_of': ['RelatedGroup']} })
    via_name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['RelatedGroup']} })
    rows: list[InstanceSummary] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['DomainRows', 'RelatedGroup', 'LinkGroup']} })
    truncated: bool = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['DomainRows', 'RelatedGroup', 'LinkGroup', 'PointSet']} })
    links: Optional[LinkGroup] = Field(default=None, description="""n:m（via）のとき、つながり（relation の行）の集計""", json_schema_extra = { "linkml_meta": {'domain_of': ['DomainRows', 'Instance', 'RelatedGroup']} })
    view: GroupView = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    count: int = Field(default=..., description="""集まり・集合の行の数（集合なら検索で絞る前。LinkGroup なら relation の表の行の数）。rows は先頭・新しいものからの一部""", ge=0, json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile', 'BreakdownValue', 'SeriesPoint', 'LatestDay']} })
    time_column: Optional[str] = Field(default=None, description="""出来事の時刻の列（role event_time。LinkGroup なら relation の表の列）。rows はこの列の新しい順""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    period_from: Optional[str] = Field(default=None, description="""期間の始まりの列（role valid_from。LinkGroup なら relation の表の列）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    period_to: Optional[str] = Field(default=None, description="""期間の終わりの列（role valid_to）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    measures: Optional[list[MeasureValue]] = Field(default=None, description="""尺度の集計（集まり全体）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile', 'LatestDay']} })
    skipped_measures: Optional[list[SkippedMeasure]] = Field(default=None, description="""集計しなかった尺度と理由（単位が混ざる、時間の軸で合計できない、など）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    breakdown_column: Optional[str] = Field(default=None, description="""内訳の列（enum の意味型の列）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    breakdown: Optional[list[BreakdownValue]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    breakdown_truncated: Optional[bool] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    series_unit: Optional[BucketUnit] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    series_measures: Optional[list[str]] = Field(default=None, description="""series の各点の values に入る尺度の列（合計）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    series: Optional[list[SeriesPoint]] = Field(default=None, description="""区切りの古い順。行の無い区切りも count 0 で入る（時間軸を詰めない）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    latest: Optional[LatestDay] = Field(default=None, description="""最新の日（time_column の値の日付で最も新しい日）のまとめ。データが今日まで届いていれば今日（is_today）。 届いていなければ最新の日を基準日として明示する""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })


class LinkGroup(GroupProfile):
    """
    n:m のつながり（relation の表の、親を指す行）の集まり。見せ方と集計は relation の行について（期間の帯、数量の合計など）。 rows は relation の行ごとの先のインスタンスで、その行の属性を link_cells に持つ（同じ先が2回出ることがある）
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'mixins': ['GroupProfile']})

    table: str = Field(default=..., description="""relation の表の安定 ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef',
                       'ReviewQuestion',
                       'TableApproval',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'InstanceSummary',
                       'Instance',
                       'OmittedRelation',
                       'RelatedGroup',
                       'LinkGroup',
                       'PointSet']} })
    name: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['SemanticType',
                       'AliasEntry',
                       'Table',
                       'Column',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'Instance',
                       'RelatedGroup',
                       'LinkGroup']} })
    rows: list[InstanceSummary] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['DomainRows', 'RelatedGroup', 'LinkGroup']} })
    truncated: bool = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['DomainRows', 'RelatedGroup', 'LinkGroup', 'PointSet']} })
    missing: int = Field(default=..., description="""relation の行が指す先の行が無かった数（壊れた参照）""", ge=0, json_schema_extra = { "linkml_meta": {'domain_of': ['LinkGroup']} })
    unlinked: int = Field(default=..., description="""relation の行のうち、先を指す列が NULL の数（先が無いつながり。壊れた参照ではない）。打ち切っていなければ rows の数 + missing + unlinked = 読んだ relation の行の数""", ge=0, json_schema_extra = { "linkml_meta": {'domain_of': ['LinkGroup']} })
    view: GroupView = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    count: int = Field(default=..., description="""集まり・集合の行の数（集合なら検索で絞る前。LinkGroup なら relation の表の行の数）。rows は先頭・新しいものからの一部""", ge=0, json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile', 'BreakdownValue', 'SeriesPoint', 'LatestDay']} })
    time_column: Optional[str] = Field(default=None, description="""出来事の時刻の列（role event_time。LinkGroup なら relation の表の列）。rows はこの列の新しい順""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    period_from: Optional[str] = Field(default=None, description="""期間の始まりの列（role valid_from。LinkGroup なら relation の表の列）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    period_to: Optional[str] = Field(default=None, description="""期間の終わりの列（role valid_to）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    measures: Optional[list[MeasureValue]] = Field(default=None, description="""尺度の集計（集まり全体）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile', 'LatestDay']} })
    skipped_measures: Optional[list[SkippedMeasure]] = Field(default=None, description="""集計しなかった尺度と理由（単位が混ざる、時間の軸で合計できない、など）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    breakdown_column: Optional[str] = Field(default=None, description="""内訳の列（enum の意味型の列）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    breakdown: Optional[list[BreakdownValue]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    breakdown_truncated: Optional[bool] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    series_unit: Optional[BucketUnit] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    series_measures: Optional[list[str]] = Field(default=None, description="""series の各点の values に入る尺度の列（合計）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    series: Optional[list[SeriesPoint]] = Field(default=None, description="""区切りの古い順。行の無い区切りも count 0 で入る（時間軸を詰めない）""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })
    latest: Optional[LatestDay] = Field(default=None, description="""最新の日（time_column の値の日付で最も新しい日）のまとめ。データが今日まで届いていれば今日（is_today）。 届いていなければ最新の日を基準日として明示する""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile']} })


class MeasureValue(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    column: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['UnitSource',
                       'SourceRef',
                       'Cell',
                       'MeasureValue',
                       'SkippedMeasure',
                       'PointSet']} })
    op: MeasureOp = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['MeasureValue']} })
    value: Optional[str] = Field(default=None, description="""表示の文字列。対象の値が無ければ無し""", json_schema_extra = { "linkml_meta": {'domain_of': ['Cell', 'MeasureValue', 'BreakdownValue', 'SeriesSum']} })
    unit_text: Optional[str] = Field(default=None, description="""単位（固定の単位、または単位の列の値がすべて同じならその値）""", json_schema_extra = { "linkml_meta": {'domain_of': ['MeasureValue']} })


class SkippedMeasure(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    column: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['UnitSource',
                       'SourceRef',
                       'Cell',
                       'MeasureValue',
                       'SkippedMeasure',
                       'PointSet']} })
    reason: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['HiddenTable',
                       'OmittedRelation',
                       'SkippedMeasure',
                       'PointFailure']} })


class BreakdownValue(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    value: Optional[str] = Field(default=None, description="""表示の文字列（enum はラベルつき）。NULL は無し""", json_schema_extra = { "linkml_meta": {'domain_of': ['Cell', 'MeasureValue', 'BreakdownValue', 'SeriesSum']} })
    count: int = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile', 'BreakdownValue', 'SeriesPoint', 'LatestDay']} })


class SeriesPoint(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    start: date = Field(default=..., description="""区切りの始まりの日付（ISO 8601）""", json_schema_extra = { "linkml_meta": {'domain_of': ['SeriesPoint']} })
    count: int = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile', 'BreakdownValue', 'SeriesPoint', 'LatestDay']} })
    sums: list[SeriesSum] = Field(default=..., description="""series_measures の順の合計。区切りに値が無ければ value 無し（0 とは区別する）""", json_schema_extra = { "linkml_meta": {'domain_of': ['SeriesPoint']} })


class SeriesSum(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    value: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Cell', 'MeasureValue', 'BreakdownValue', 'SeriesSum']} })


class LatestDay(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    day: date = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['LatestDay']} })
    is_today: bool = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['LatestDay']} })
    count: int = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile', 'BreakdownValue', 'SeriesPoint', 'LatestDay']} })
    measures: Optional[list[MeasureValue]] = Field(default=None, description="""加法の尺度の、最新の日の合計""", json_schema_extra = { "linkml_meta": {'domain_of': ['GroupProfile', 'LatestDay']} })
    snapshot_at: Optional[str] = Field(default=None, description="""半加法の尺度を合計した時点（time_column の最大の値。表示の文字列）""", json_schema_extra = { "linkml_meta": {'domain_of': ['LatestDay']} })
    snapshot: Optional[list[MeasureValue]] = Field(default=None, description="""半加法の尺度（残高・在庫など）の、最新の時点（snapshot_at）の行だけの合計""", json_schema_extra = { "linkml_meta": {'domain_of': ['LatestDay']} })


class PointSet(ConfiguredBaseModel):
    """
    表の geo.point の列の値を点にしたもの（地図のため）。プロセス境界（HTTP API → UI）で受け渡す形。 変換できなかった行は黙って捨てず failed に、NULL は数を nulls に持つ。 rows_read = points の数 + nulls + failed の数。truncated なら、表には limit を超える行がある
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om', 'tree_root': True})

    table: str = Field(default=..., description="""表の安定 ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['SourceRef',
                       'ReviewQuestion',
                       'TableApproval',
                       'DomainSummary',
                       'HiddenTable',
                       'DomainRows',
                       'Within',
                       'InstanceSummary',
                       'Instance',
                       'OmittedRelation',
                       'RelatedGroup',
                       'LinkGroup',
                       'PointSet']} })
    column: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['UnitSource',
                       'SourceRef',
                       'Cell',
                       'MeasureValue',
                       'SkippedMeasure',
                       'PointSet']} })
    points: list[GeoPointRow] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['PointSet']} })
    nulls: int = Field(default=..., ge=0, json_schema_extra = { "linkml_meta": {'domain_of': ['PointSet']} })
    failed: list[PointFailure] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['PointSet']} })
    rows_read: int = Field(default=..., description="""読んだ行の数（limit 以下）""", ge=0, json_schema_extra = { "linkml_meta": {'domain_of': ['PointSet']} })
    limit: int = Field(default=..., description="""読む行の上限""", ge=1, json_schema_extra = { "linkml_meta": {'domain_of': ['DomainRows', 'PointSet']} })
    truncated: bool = Field(default=..., description="""上限で打ち切った（表にはまだ行がある）""", json_schema_extra = { "linkml_meta": {'domain_of': ['DomainRows', 'RelatedGroup', 'LinkGroup', 'PointSet']} })


class GeoPointRow(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    key: list[str] = Field(default=..., description="""行の主キーの値（主キーの列の順）。各値をソースの値の文字列表現（Python の str）にしたもの。 主キーは NULL を持たない（仕様 4.2）""", json_schema_extra = { "linkml_meta": {'domain_of': ['Within',
                       'InstanceSummary',
                       'Instance',
                       'GeoPointRow',
                       'PointFailure']} })
    lat: float = Field(default=..., ge=-90, le=90, json_schema_extra = { "linkml_meta": {'domain_of': ['GeoPointRow']} })
    lon: float = Field(default=..., ge=-180, le=180, json_schema_extra = { "linkml_meta": {'domain_of': ['GeoPointRow']} })


class PointFailure(ConfiguredBaseModel):
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/mandara/om'})

    key: list[str] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Within',
                       'InstanceSummary',
                       'Instance',
                       'GeoPointRow',
                       'PointFailure']} })
    reason: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['HiddenTable',
                       'OmittedRelation',
                       'SkippedMeasure',
                       'PointFailure']} })


# Model rebuild
# see https://pydantic-docs.helpmanual.io/usage/models/#rebuilding-a-model
Annotation.model_rebuild()
Vocabulary.model_rebuild()
SemanticType.model_rebuild()
EnumValue.model_rebuild()
AliasEntry.model_rebuild()
Table.model_rebuild()
Edge.model_rebuild()
Identity.model_rebuild()
KeyColumns.model_rebuild()
Column.model_rebuild()
UnitSource.model_rebuild()
Relation.model_rebuild()
ColumnPair.model_rebuild()
ClaimFile.model_rebuild()
Claim.model_rebuild()
SourceDeclarations.model_rebuild()
SourceRef.model_rebuild()
ClaimSource.model_rebuild()
OntologyIndex.model_rebuild()
OntologyListing.model_rebuild()
OntologyDocument.model_rebuild()
ReviewQueue.model_rebuild()
ReviewQuestion.model_rebuild()
EvidenceLine.model_rebuild()
ReviewAnswer.model_rebuild()
BulkApproval.model_rebuild()
AllApproval.model_rebuild()
TableApproval.model_rebuild()
DomainIndex.model_rebuild()
DomainSummary.model_rebuild()
HiddenTable.model_rebuild()
Within.model_rebuild()
InstanceSummary.model_rebuild()
Cell.model_rebuild()
Instance.model_rebuild()
OmittedRelation.model_rebuild()
InstanceLink.model_rebuild()
GroupProfile.model_rebuild()
DomainRows.model_rebuild()
RelatedGroup.model_rebuild()
LinkGroup.model_rebuild()
MeasureValue.model_rebuild()
SkippedMeasure.model_rebuild()
BreakdownValue.model_rebuild()
SeriesPoint.model_rebuild()
SeriesSum.model_rebuild()
LatestDay.model_rebuild()
PointSet.model_rebuild()
GeoPointRow.model_rebuild()
PointFailure.model_rebuild()
