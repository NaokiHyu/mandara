// 生成物。手で編集しない。元: src/mandara/om/schema/om.yaml / 生成: scripts/gen_ui_types.sh
export type ColumnDomainName = string;
export type AliasEntryName = string;
export type TableId = string;
export type ColumnName = string;
/**
* 注釈のレビュー状態（4.4）
*/
export enum ReviewStatus {
    
    proposed = "proposed",
    approved = "approved",
    rejected = "rejected",
};
/**
* 表種別（4.1）
*/
export enum TableKind {
    
    /** ものを表す表。ライフサイクルを持ちうる。コマンド可 */
    domain = "domain",
    /** もの同士の関係を表す表（n:m 中間表）。属性を持ってよい。コマンド可。 edge を宣言すると、1行がもの同士を結ぶ1本の辺になり、列はその辺の属性になる */
    relation = "relation",
    /** 出来事を表す表。不変・追記のみ。コマンド不可 */
    event = "event",
    /** 計算・集計で導出された表。コマンド不可 */
    derived = "derived",
};
/**
* 関係型（4.1）。推移性と逆関係は各値の annotations に持つ
*/
export enum RelationType {
    
    /** 型の階層。下位の型は上位の型の一種で、上位の性質を継承する */
    is_a = "is-a",
    /** 構成階層。全体と部分 */
    part_of = "part-of",
    /** 分類への所属。ものから分類へ、下位の分類から上位の分類へ（製品 → サブカテゴリ → カテゴリ）。 全体と部分（part-of）でも、型の一種（is-a）でもない。ドリルダウン階層になる */
    classified_as = "classified-as",
    /** 属性または参照。細分は has_kind で指定 */
    has = "has",
    /** 導出元。列や表が、どのソースからどの関数で生まれたか */
    derived_from = "derived-from",
};

export enum HasKind {
    
    attribute = "attribute",
    reference = "reference",
};

export enum Cardinality {
    
    number_1COLON1 = "1:1",
    number_1COLONn = "1:n",
    nCOLON1 = "n:1",
    nCOLONm = "n:m",
};
/**
* 尺度の加法性（10.4）
*/
export enum Additivity {
    
    additive = "additive",
    semi_additive = "semi-additive",
    non_additive = "non-additive",
};
/**
* 列ドメインの役割
*/
export enum DomainRole {
    
    event_time = "event_time",
    valid_from = "valid_from",
    valid_to = "valid_to",
    /** 業務の意味を持たない列（行の技術ID、監査用の日時・更新者）。UI・ツール面・推論から既定で外す */
    technical = "technical",
};
/**
* 列ドメインの値の意味の型。物理的な表現（バイナリ形式、文字コードなど）は含めない。 物理的な表現は列の encoding に書く。意味の型への変換は encoding ごとのコーデック（Adapters の中で、DB コネクタから独立したもの）の責務とする
*/
export enum ValueType {
    
    identifier = "identifier",
    /** 外部の体系に従うコード（国コード、単位コードなど）。値の一覧は持たない */
    code = "code",
    /** 値の一覧（values）を語彙に持つ */
    enum = "enum",
    text = "text",
    integer = "integer",
    ratio = "ratio",
    /** 単位を伴う量 */
    quantity = "quantity",
    /** 通貨を単位とする金額 */
    money = "money",
    date = "date",
    datetime = "datetime",
    /** 地理上の点（緯度・経度）。UI では地図に出す */
    geoFULL_STOPpoint = "geo.point",
    geoFULL_STOPlatitude = "geo.latitude",
    geoFULL_STOPlongitude = "geo.longitude",
    mediaFULL_STOPimage = "media.image",
    mediaFULL_STOPaudio = "media.audio",
    mediaFULL_STOPdocument = "media.document",
    /** XML の文書。物理的な表現を名前に含んでいるので見直す（om-backlog M） */
    documentFULL_STOPxml = "document.xml",
};
/**
* 固定の単位（UnitSource.fixed）。足すときは①を変える
*/
export enum Unit {
    
    USD = "USD",
    JPY = "JPY",
    /** 個数 */
    each = "each",
    day = "day",
    hour = "hour",
};
/**
* ソース上の値の物理的な表現が、意味の型（ValueType）の標準的な表現と違うときに書く。 具体例のあるものだけを置く。各値の annotations の decodes_to は、変換先の ValueType
*/
export enum SourceEncoding {
    
    /** SQL Server の geography 型のバイナリ（SRID 4 バイト、版、属性、緯度・経度の float64） */
    sqlserver_geography = "sqlserver_geography",
};
/**
* ソースの列の値の、DB に依存しない型族（カタログ、主張）。 どれにも当てはまらない組み込みの型は other とし、ソース上の型名で区別する
*/
export enum TypeFamily {
    
    integer = "integer",
    decimal = "decimal",
    float = "float",
    text = "text",
    boolean = "boolean",
    date = "date",
    time = "time",
    timestamp = "timestamp",
    binary = "binary",
    uuid = "uuid",
    xml = "xml",
    json = "json",
    other = "other",
};
/**
* ソース上のオブジェクトの種類（カタログ、主張）
*/
export enum ObjectKind {
    
    table = "table",
    view = "view",
    materialized_view = "materialized_view",
};
/**
* 外部キーの参照先の行が消える・変わるときの動作（SQL 標準）。cascade は合成関係の証拠になる
*/
export enum ReferentialAction {
    
    no_action = "no_action",
    restrict = "restrict",
    cascade = "cascade",
    set_null = "set_null",
    set_default = "set_default",
};
/**
* 主張の述語（6.6.2）。述語ごとに、主語の粒度（表 / 列）と、値として持つフィールドが決まっている。 今あるのはカタログの抽出器（T1.3）が出すものだけ。足すときは①を変え、検証の形の表も足す
*/
export enum ClaimPredicate {
    
    /** 表は columns で識別される（主キー） */
    identified_by = "identified_by",
    /** 表の columns の組は一意 */
    unique = "unique",
    /** 表の columns が、target の target_columns を参照する（外部キー）。on_delete / on_update を持つ */
    references = "references",
    /** 表・ビューなどのオブジェクトの種類は object_kind */
    declared_as = "declared_as",
    /** 列は NULL を持たない */
    not_null = "not_null",
    /** 列は NULL を持ちうる（宣言されている）。宣言の無い列（ビューの列など）には、not_null も nullable も出さない */
    nullable = "nullable",
    /** 列の既定値は text の式 */
    defaults_to = "defaults_to",
    /** 表または列は text で説明されている（コメント） */
    described_as = "described_as",
    /** 表の行は text の式を満たす（CHECK） */
    checks = "checks",
    /** 列のソース上の型名は type_name、型族は family、表の中の位置は position */
    typed_as = "typed_as",
    /** 列はユーザー定義の型の別名 type_name を使う */
    aliased_as = "aliased_as",
};
/**
* 主張の出所の種類（6.6.2）
*/
export enum ClaimSourceKind {
    
    schema = "schema",
    sql = "sql",
    codelist = "codelist",
    design_doc = "design-doc",
    memo = "memo",
    package = "package",
    world = "world",
    review = "review",
};
/**
* 論理的な is-a が、ソースでどう実装されているか（C3）。 具体例のあるものだけを置く。排他的な参照・区分列は AW で裏付けが取れていないので未定義
*/
export enum IsARealization {
    
    /** 下位の表が上位の表と同じ主キーを持ち、それで参照する */
    shared_key = "shared_key",
};


/**
 * すべての注釈が持つ共通フィールド（4.4）
 */
export interface Annotation {
    status: string,
    confidence?: number,
    /** 主張台帳（6.6）の主張ID */
    evidence?: string[],
    /** 提案者のモデル名と版、実行ID */
    inferred_by?: string,
    reviewed_by?: string,
    reviewed_at?: date,
    note?: string,
}


/**
 * vocabulary.yaml。拡張できる語彙の閉じた集合
 */
export interface Vocabulary {
    column_domains?: {[index: ColumnDomainName]: ColumnDomain },
    aliases?: {[index: AliasEntryName]: AliasEntry },
}


/**
 * 列ドメイン。「同じ意味」を表す
 */
export interface ColumnDomain {
    name: string,
    description?: string,
    type: string,
    role?: string,
    additivity?: string,
    /** true なら、この列ドメインを使う列は unit（量と単位の組、C5）を宣言しなければならない */
    requires_unit?: boolean,
    /** type が enum のときの値の一覧（C6）。値の集合はデータからではなく、宣言・証拠から決める */
    values?: EnumValue[],
}



export interface EnumValue extends Annotation {
    /** ソース上の値の文字列表現。ファイルには文字列で書く（数値の 1 は拒否し、"1" と書く）。 観測値との照合時の正規化（真偽値の表記など）はソースの Adapter の責務 */
    code: string,
    label: string,
    /** 語彙や公開オントロジーの概念への参照（CURIE） */
    meaning?: string,
    /** データで観測されたか。値の集合の根拠にはしない */
    observed?: boolean,
}



export interface AliasEntry {
    name: string,
    terms: string[],
}


/**
 * contexts/<context>/tables/<source>/<table>.yaml（4.2）
 */
export interface Table extends Annotation {
    /** 安定ID。改名しても変わらない */
    id: string,
    /** ソース上の名前（schema.table） */
    name: string,
    source: string,
    context: string,
    /** 表種別。提案の段階では未決（無い）でもよい（規則や証拠で決まらないものを既定値で埋めない）。 approved の表には必須 */
    kind?: string,
    /** 1行が何を表すか（10.4） */
    grain?: string,
    identity?: Identity,
    columns?: {[index: ColumnName]: Column },
    relations?: Relation[],
    /** relation の表だけが持てる。この表の行が実現する、もの同士の関係 */
    edge?: Edge,
}


/**
 * 中間表の1行を、もの同士を結ぶ1本の辺とみなす（プロパティグラフの辺）。 from と to は、この表が持つ n:1 の参照（has / reference）の ID。 辺の関係型の意味論（推移性、循環の禁止、ドリルダウン）は、表の間の関係と同じに効く。 from / to の列が NULL の行は辺にならない。type が has の辺は参照（reference）を表す
 */
export interface Edge extends Annotation {
    /** has / part-of / classified-as のどれか。is-a は型の間の関係、derived-from は関数による導出なので、辺にはならない */
    type: string,
    /** 辺の始点側の参照の ID（part-of なら部分、classified-as なら分類されるもの） */
    from: string,
    /** 辺の終点側の参照の ID（part-of なら全体、classified-as なら分類） */
    to: string,
}


/**
 * 同一性（C1）。複合キーは列の配列としてそのまま書く。1つの値として扱うのは生成される型の側の責務
 */
export interface Identity {
    primary: string[],
    alternates?: KeyColumns[],
}



export interface KeyColumns {
    columns: string[],
}



export interface Column extends Annotation {
    name: string,
    /** vocabulary の column_domains のキー */
    domain?: string,
    pii?: boolean,
    /** 量の単位の出所（C5）。fixed / column のちょうど1つ */
    unit?: UnitSource,
    /** ソース上の値の物理的な表現。意味の型の標準的な表現と同じなら書かない */
    encoding?: string,
}



export interface UnitSource {
    /** 固定の単位 */
    fixed?: string,
    /** 同じ表で単位を持つ列 */
    column?: string,
}



export interface Relation extends Annotation {
    /** 関係の安定ID。同じ表への複数の参照（C2）を区別する */
    id: string,
    type: string,
    has_kind?: string,
    /** 参照の役割（C2。例 bill_to / ship_to） */
    role?: string,
    /** 参照先の表の安定ID。自己参照は self と書き、自分の安定ID は書かない */
    target: string,
    /** 結合に使う列の対応。複合キーなら複数 */
    via?: ColumnPair[],
    /** 列の等値で結べない関係（C4。パス文字列の木など）を結ぶ決定的関数の安定ID */
    via_function?: string,
    cardinality?: string,
    /** is-a の物理的な実装パターン（C3） */
    realized_by?: string,
    /** derived-from で導出される列 */
    columns?: string[],
    /** derived-from の変換関数の安定ID */
    function?: string,
}



export interface ColumnPair {
    from: string,
    to: string,
}


/**
 * work/<source>/claims/<schema>/<table>.yaml。その表を主語とする主張（列を主語とするものを含む）の一覧。 決定的な抽出器の主張は再生成できるキャッシュで、git に乗せない
 */
export interface ClaimFile {
    claims?: Claim[],
}


/**
 * ソースについての主張。id は内容（subject、predicate、値のフィールド、source）から決まり、 抽出器の版・確信度・実行時刻には依存しない。再生成しても同じ id になる
 */
export interface Claim {
    id: string,
    subject: SourceRef,
    predicate: string,
    columns?: string[],
    target?: SourceRef,
    target_columns?: string[],
    on_delete?: string,
    on_update?: string,
    text?: string,
    type_name?: string,
    family?: string,
    /** 列の、表の中の位置（1 から） */
    position?: number,
    object_kind?: string,
    source: ClaimSource,
    /** 抽出器の名前と版 */
    extracted_by: string,
    /** 出所の日付（文書の日付など）。鮮度の根拠 */
    valid_at?: date,
    confidence: number,
}


/**
 * データソース上の表または列。OM の安定 ID ができる前の段階なので、ソース上の名前で指す
 */
export interface SourceRef {
    source: string,
    /** スキーマ名（schema は pydantic の BaseModel と衝突するので避ける） */
    schema_name: string,
    table: string,
    column?: string,
}



export interface ClaimSource {
    kind: string,
    /** 出所の中の場所（制約名、ファイルと行など） */
    locator: string,
}


/**
 * OM 全体を1つの文書にしたもの。プロセス境界（HTTP API → UI）で受け渡す形。 ファイルの配置（仕様 5章）とは別。受け取る側は、①から生成した JSON Schema で検証する
 */
export interface OntologyDocument {
    vocabulary: Vocabulary,
    tables?: Table[],
}



