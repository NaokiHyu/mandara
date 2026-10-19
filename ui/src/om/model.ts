// 生成物。手で編集しない。元: src/mandara/om/schema/om.yaml / 生成: scripts/gen_ui_types.sh
export type date = string;
export type SemanticTypeName = string;
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
* 意味型の役割
*/
export enum SemanticRole {
    
    event_time = "event_time",
    valid_from = "valid_from",
    valid_to = "valid_to",
    /** 業務の意味を持たない列（行の技術ID、監査用の日時・更新者）。UI・ツール面・推論から既定で外す */
    technical = "technical",
};
/**
* 意味型の値の型。物理的な表現（バイナリ形式、文字コードなど）は含めない。 物理的な表現は列の encoding に書く。意味の型への変換は encoding ごとのコーデック（I/O の無い純粋な変換。DB コネクタから独立したもの）の責務とする
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
* 主張の述語（6.6.2）。述語ごとに、主語の粒度（表 / 列）と、値として持つフィールドが決まっている。 カタログの抽出器（T1.3）と、実データの値の抽出器（T2.4、参照の被覆）が出す。足すときは①を変え、検証の形の表も足す
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
    /** 列の値は物理的な表現 encoding で書かれている（実データの値の抽出器 T2.4 が出す。読んだ値がすべてそのコーデックで読めた） */
    encoded_as = "encoded_as",
    /** 表の金額は、行が別の単位を言わない限り fixed_unit（人が宣言したソースの性質。宣言のファイルから抽出のたびに作る） */
    amounts_in = "amounts_in",
    /** 表の columns で target の target_columns を参照する行が、target のすべての行にある（実データ。target が空なら出さない） */
    covers_all = "covers_all",
    /** 表の columns で target の target_columns を参照される target の行は、ちょうど target の列 discriminator が discriminator_values のどれかである行（実データ。区分の列。値の一部だけが参照され、残りは参照されない） */
    discriminated_by = "discriminated_by",
    /** 表の columns（NULL を除く）の値を数えた結果：異なる値が distinct_values 個、そのうち target の target_columns （キー）にあるのが matched_values 個。target のキーの値は target_values 個。1 つの値の行は最大 max_repeats 行。 整数の列なら、列の値の最小から最大までの整数の数 value_span と、その範囲にあるキーの値の数 target_values_in_span （整数でない列、値の無い列では持たない）。 実データの測定で、宣言の無い参照（外部キー）の候補の事実。包含が成り立たない組も、否定の証拠として残す（仕様 6.7.2 B / D） */
    included_in = "included_in",
    /** 表の columns の値の組が、実データで重複せず NULL も無い（行が distinct_values 行。T2.7）。組は極小（真部分集合は 一意でない）。宣言の unique とは別：測った時点の事実で、制約ではない */
    unique_in_data = "unique_in_data",
    /** 列の値の形（実データ。T2.6）：NULL を除いた異なる値 distinct_values 個のうち sampled_values 個の見本を、構文パターンに 一般化した上位の patterns と、それぞれに当たる見本の割合 pattern_shares。shape_level は MDL で選んだ一般化の段 （1 = 文字の種類と長さ、2 = 文字の種類、3 = 文字の種類の集合）。 値の文字列は入れない（共通の接頭辞のような定数も） */
    shaped_as = "shaped_as",
    /** 表の columns と target の target_columns の値の似かた（実データ。T2.6）：信号 method（名前と版）で測った値 score と、 同じ母集団ならこのくらいという基準 baseline。見本の数は sampled_values と target_sampled_values。 読み方（近いと言えるか）は信号ごとに決まる（mandara.resemblance.signals）。組の向きは名前の順で 1 つ */
    resembles = "resembles",
};
/**
* 近隣の集まりの見せ方。OM（種類、意味型の役割と加法性）と件数から機械的に決める（browse.group_view）
*/
export enum GroupView {
    
    /** 個々のサマリを並べる（件数が少ない） */
    list = "list",
    /** 件数、尺度の集計、enum の内訳、新しい（または先頭の）数件 */
    summary = "summary",
    /** 時系列の線グラフ（出来事の時刻の列があり、件数が多い） */
    time_series = "time_series",
    /** 期間の帯（valid_from と valid_to の列を持つ） */
    timeline = "timeline",
};
/**
* 尺度の集計のしかた（加法性から決まる。additive は sum、non-additive は avg。semi-additive は集まり全体では集計しない （時間の軸が無いと OM から言えないため）。最新の時点の行だけで合計する（LatestDay.snapshot））
*/
export enum MeasureOp {
    
    sum = "sum",
    avg = "avg",
};
/**
* 時系列の区切り（期間の幅から決まる）
*/
export enum BucketUnit {
    
    day = "day",
    month = "month",
    year = "year",
};
/**
* インスタンスの見出しの出どころ
*/
export enum LabelSource {
    
    /** その表の見出しの列 */
    own = "own",
    /** is-a の上位（主キーを共有する）の同じキーの行の見出し */
    borrowed = "borrowed",
    /** 上位から借りるはずが、上位に同じキーの行が無い（壊れた参照）。見出しはキー */
    borrowed_missing = "borrowed_missing",
    /** 見出しの列も借りる先も無いので、キー */
    key = "key",
};
/**
* 主張の出所の種類（6.6.2）
*/
export enum ClaimSourceKind {
    
    schema = "schema",
    sql = "sql",
    codelist = "codelist",
    /** ソースの実データの値（列の値を読んだもの） */
    data = "data",
    /** 人が宣言したソースの性質（work/<source>/declarations.yaml。抽出のたびに主張に作り直す） */
    declaration = "declaration",
    design_doc = "design-doc",
    memo = "memo",
    package = "package",
    world = "world",
    review = "review",
};
/**
* 見られる OM の出どころ
*/
export enum OntologySourceKind {
    
    /** 提案した OM（work/<source>/ontology） */
    work = "work",
    /** 正解 OM */
    gold = "gold",
};
/**
* 参照先（n:1）へのリンクの状態（ドメインビューワ）
*/
export enum LinkState {
    
    /** 参照先の行がある */
    found = "found",
    /** 参照の列が NULL（参照していない） */
    null = "null",
    /** 参照の列に値があるのに、参照先の行が無い（壊れた参照） */
    missing = "missing",
};
/**
* インスタンスの近隣の集まりの種類（ドメインビューワ）
*/
export enum RelatedKind {
    
    /** 他のドメインの行がこのインスタンスを参照している（1:n。受注 → 明細） */
    children = "children",
    /** relation の表を挟んだ先のインスタンス（n:m。製品 ⇄ 仕入先）。relation の表そのものは出さない */
    via = "via",
};
/**
* レビューの問いが判断するもの（注釈を持つクラス）
*/
export enum ReviewSubject {
    
    table = "table",
    relation = "relation",
    edge = "edge",
    column = "column",
    enum_value = "enum_value",
};
/**
* レビューの答え（仕様 9.3）。「別の意味」は T3.6
*/
export enum ReviewDecision {
    
    approve = "approve",
    reject = "reject",
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
    semantic_types?: {[index: SemanticTypeName]: SemanticType },
    aliases?: {[index: AliasEntryName]: AliasEntry },
}


/**
 * 意味型。列の値が何を意味するか（住所の ID、受注日、金額）。別の表の列でも、意味が同じなら同じ意味型にする。 同じ値の型でも意味が違えば別の意味型にする（受注日と出荷日）
 */
export interface SemanticType {
    name: string,
    description?: string,
    type: string,
    role?: string,
    additivity?: string,
    /** true なら、この意味型を使う列は unit（量と単位の組、C5）を宣言しなければならない */
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
 * entity_groups/<entity_group>/tables/<source>/<table>.yaml（4.2）
 */
export interface Table extends Annotation {
    /** 安定ID。改名しても変わらない */
    id: string,
    /** ソース上の名前（schema.table） */
    name: string,
    source: string,
    /** エンティティグループ：ドメインの意味的な集まり（仕様の用語集と 4.2）。境界づけられたコンテキストではない（それはスキーマコンテキスト。4.3） */
    entity_group: string,
    /** 表種別。提案の段階では未決（無い）でもよい（規則や証拠で決まらないものを既定値で埋めない）。 approved の表には必須 */
    kind?: string,
    /** 計算・集計で導出された表（ビューなど）。kind（何を表すか）とは別の軸で、どの kind とも組み合わさる。 コマンド不可。part-of の全体側にならない。ソースが実体の表なら書かない */
    derived?: boolean,
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
    /** vocabulary の semantic_types のキー */
    semantic_type?: string,
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
    encoding?: string,
    /** 固定の単位（amounts_in） */
    fixed_unit?: string,
    /** 区分の列の名前（target の列） */
    discriminator?: string,
    /** 区分の列の値（ソースの値の文字列表現。NULL は含めない） */
    discriminator_values?: string[],
    /** included_in：表の columns の、NULL を除いた異なる値の数 */
    distinct_values?: number,
    /** included_in：そのうち target の target_columns にある値の数 */
    matched_values?: number,
    /** included_in：target の target_columns（キー）の値の数 */
    target_values?: number,
    /** included_in：表の columns の1つの値を持つ行の最大の数（1 なら値は重ならない。1:1 の手がかり） */
    max_repeats?: number,
    /** included_in：整数の列だけ。表の columns の値の最小から最大までにある整数の数（最大 − 最小 + 1） */
    value_span?: number,
    /** included_in：整数の列だけ。target の target_columns（キー）の値のうち、表の columns の値の最小から最大までにある数。 value_span と比べると、列の値の範囲でのキーの詰まり方が分かる（隙間の多い範囲で全部含まれるなら、偶然ではない） */
    target_values_in_span?: number,
    /** shaped_as：構文パターン（多い順） */
    patterns?: string[],
    /** shaped_as：patterns の順に、そのパターンに当たる見本の割合（0〜1） */
    pattern_shares?: number[],
    /** shaped_as：MDL で選んだ一般化の段（1〜3） */
    shape_level?: number,
    /** shaped_as / resembles：表の columns の値の見本の数（異なる値） */
    sampled_values?: number,
    /** resembles：target の target_columns の値の見本の数（異なる値） */
    target_sampled_values?: number,
    /** resembles：信号の名前と版（"ncd 0.1" など） */
    method?: string,
    /** resembles：信号の測った値 */
    score?: number,
    /** resembles：同じ母集団ならこのくらい、という基準の値（信号ごとに意味が決まる） */
    baseline?: number,
    source: ClaimSource,
    /** 抽出器の名前と版 */
    extracted_by: string,
    /** 出所の日付（文書の日付など）。鮮度の根拠 */
    valid_at?: date,
    confidence: number,
}


/**
 * 人が宣言するソースの性質（work/<source>/declarations.yaml）。データやカタログからは分からないことだけを書く。 抽出のたびに主張（source.kind: declaration）にし、推論の根拠にする
 */
export interface SourceDeclarations {
    /** ソースの金額の通貨（行が別の単位を言わない限り） */
    default_currency?: string,
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
 * 見られる OM の一覧。プロセス境界（HTTP API → UI）で受け渡す形
 */
export interface OntologyIndex {
    ontologies: OntologyListing[],
}



export interface OntologyListing {
    /** URL に使う ID（例 work:adventureworks、gold:adventureworks） */
    id: string,
    label: string,
    kind: string,
    /** レビューの問いの列を読めるか（レビューするのは作業領域の OM だけ。正解 OM などは false） */
    reviewable: boolean,
}


/**
 * OM 全体を1つの文書にしたもの。プロセス境界（HTTP API → UI）で受け渡す形。 ファイルの配置（仕様 5章）とは別。受け取る側は、①から生成した JSON Schema で検証する
 */
export interface OntologyDocument {
    vocabulary: Vocabulary,
    tables: Table[],
}


/**
 * レビューの問いの列（仕様 9.3）。並びは影響の大きい順。プロセス境界（HTTP API → UI）で受け渡す形
 */
export interface ReviewQueue {
    /** 見られる OM の ID */
    ontology: string,
    /** まだ判断されていない提案の数（= questions の数） */
    pending: number,
    questions: ReviewQuestion[],
    /** この API で答えを書けるか */
    writable: boolean,
    /** 書けないときの理由（writable が false のときだけ） */
    not_writable_reason?: string,
    /** 書くときの reviewed_by（writable のときだけ） */
    reviewer?: string,
    /** 一括承認の閾値の下限 */
    bulk_minimum: number,
    /** 開発用の全承認が使えるか（mandara serve --dev）。中身を見ずに固定するので、開発中に承認済みの状態を試すためだけに使う */
    approve_all: boolean,
}



export interface ReviewQuestion {
    /** 答えを当てる注釈の場所（Ontology.annotations() の場所） */
    location: string,
    /** 問いの属する表の安定 ID（語彙の enum の値の問いには無い） */
    table?: string,
    subject: string,
    text: string,
    confidence?: number,
    /** 根拠（仕様 9.3：3行まで） */
    evidence: EvidenceLine[],
    /** 根拠の主張の数（evidence に出さなかったものを含む） */
    evidence_total: number,
    /** 承認するには表の種類を選ぶ */
    needs_kind: boolean,
    /** 一括承認の候補（確信度が bulk_minimum 以上で、種類を選ばなくてよい） */
    bulk_candidate: boolean,
}



export interface EvidenceLine {
    /** 主張 ID */
    id: string,
    /** 人が読める1行 */
    text: string,
}


/**
 * 1つの問いへの答え。プロセス境界（UI → HTTP API）で受け渡す形
 */
export interface ReviewAnswer {
    location: string,
    decision: string,
    /** 種類が未決の表を承認するときに選んだ種類 */
    kind?: string,
}


/**
 * 一覧で見せた候補の一括承認（仕様 9.3）。プロセス境界（UI → HTTP API）で受け渡す形
 */
export interface BulkApproval {
    threshold: number,
    /** 見せた候補の場所。今も候補のままであることを確かめてから承認する */
    locations: string[],
}


/**
 * 開発用の全承認（mandara serve --dev のときだけ）。種類を選ぶ問いを除く、まだ判断されていない問いをすべて承認する。 プロセス境界（UI → HTTP API）で受け渡す形
 */
export interface AllApproval {
    /** 見せた問いの場所。今も種類を選ばなくてよい未判断の問いのままであることを確かめてから承認する */
    locations: string[],
}


/**
 * 表ごとの一括承認（仕様 9.3）。表の提案を一覧で見せ、まとめて承認する（種類を選ぶ問いは除く）。 プロセス境界（UI → HTTP API）で受け渡す形
 */
export interface TableApproval {
    /** 表の安定 ID */
    table: string,
    /** 見せた問いの場所。今もその表の、種類を選ばなくてよい問いのままであることを確かめてから承認する */
    locations: string[],
}


/**
 * ドメインビューワのドメインの一覧（仕様 7.6：承認済みの OM から作る）。domain / event の承認済みの表がドメインになる。 relation の表はリンクとしてたどるだけで、ドメインにしない。出さなかった表は理由とともに hidden に持つ（黙って消さない）。 プロセス境界（HTTP API → UI）で受け渡す形
 */
export interface DomainIndex {
    ontology: string,
    /** OM のすべてのエンティティグループ（名前の順。UI はこの並びでエンティティグループの色を決める。グラフと同じ並び） */
    entity_groups: string[],
    domains: DomainSummary[],
    hidden: HiddenTable[],
    /** 行を読めるか（ソースへの接続がある） */
    readable: boolean,
}



export interface DomainSummary {
    /** 表の安定 ID */
    table: string,
    name: string,
    entity_group: string,
    kind: string,
    grain?: string,
    /** 見出しに使う列（推定：name という列、無ければ名前が name で終わる文字の列を3つまで）。 空なら label_from の見出し、それも無ければキーが見出し */
    label_columns: string[],
    /** 見出しの列が無いとき、見出しを借りる is-a の上位のドメイン（表の安定 ID。主キーを共有するので同じキーの行の見出し） */
    label_from?: string,
    /** 主キーの列 */
    key_columns: string[],
    /** 代替キー（自然キー）の列（どれかの代替キーに含まれる列） */
    natural_keys: string[],
    /** 検索に使う列（文字の部分一致） */
    searchable: string[],
}



export interface HiddenTable {
    table: string,
    name: string,
    reason: string,
}


/**
 * ドメインの集合のビュー（表のイメージ）。within があれば、そのインスタンスにつながる行だけの部分集合（八百屋 X の野菜）。 集合の見せ方と集計（GroupProfile。インスタンスの近隣の集まりと同じ規則・同じ形）は集合全体について、rows は検索（query）で 絞った先頭・新しいものから。プロセス境界（HTTP API → UI）で受け渡す形
 */
export interface DomainRows extends GroupProfile {
    table: string,
    name: string,
    within?: Within,
    /** n:m で絞ったとき（within.via_relation がある）、親とのつながり（relation の行）の集計。インスタンスの n:m の 集まりの links と同じ */
    links?: LinkGroup,
    query?: string,
    /** 一覧に出す列（技術列を除く。cells の列はこの中から） */
    columns: string[],
    rows: InstanceSummary[],
    limit: number,
    truncated: boolean,
}


/**
 * 集合を絞るインスタンス（親）と、その親への関係。1:n なら子の表から親への関係（relation）。n:m なら relation の表を挟む： relation は relation の表から集合の表への関係、via_relation は relation の表から親への関係（RelatedGroup と同じ意味）
 */
export interface Within {
    /** 親のドメイン（表の安定 ID） */
    table: string,
    key: string[],
    /** 1:n なら子の表から親への関係、n:m なら relation の表から集合の表への関係の安定 ID */
    relation: string,
    /** n:m のとき、relation の表から親への関係の安定 ID */
    via_relation?: string,
    /** 親のインスタンスの見出し */
    label: string,
    /** 親の表の名前 */
    name: string,
}


/**
 * インスタンス（行）の見出し。key でたどれる
 */
export interface InstanceSummary {
    table: string,
    /** 主キーの値（文字列の表現、主キーの列の順） */
    key: string[],
    /** 見出し（出どころは label_source） */
    label: string,
    label_source: string,
    cells: Cell[],
    /** via の集まりの行で、この行と結んだ relation の表の行の値（参照の列、技術列、代理キーを除く。期間や数量など、 つながりの属性）。代理キーは、1列で参照でない relation の表の主キー（行の番号でしかない。構造からの推定）。 via の集まりは relation の表の行ごとに1つ（同じ先が別の期間で2回出ることがある） */
    link_cells?: Cell[],
}



export interface Cell {
    column: string,
    /** 表示の文字列。NULL は無し */
    value?: string,
    semantic_type?: string,
}


/**
 * インスタンスのビュー（行のイメージ）。自分の値、参照先へのリンク（n:1）、近隣のインスタンスの集まり （参照してくる行 1:n、relation の表を挟んだ先 n:m）。プロセス境界（HTTP API → UI）で受け渡す形
 */
export interface Instance {
    table: string,
    name: string,
    key: string[],
    label: string,
    fields: Cell[],
    links: InstanceLink[],
    related: RelatedGroup[],
    /** たどらなかった関係と理由（参照先・参照元がドメインでない、列の等値で結べない、など）。黙って省かない */
    omitted: OmittedRelation[],
}



export interface OmittedRelation {
    /** 関係の安定 ID */
    relation: string,
    /** その関係を持つ表の名前 */
    table: string,
    reason: string,
}


/**
 * 参照先（n:1）のインスタンスへのリンク。target は state が found のときだけ
 */
export interface InstanceLink {
    /** 関係の安定 ID */
    relation: string,
    relation_type: string,
    role?: string,
    /** 参照に使う自分の列 */
    columns: string[],
    target_table: string,
    target_name: string,
    state: string,
    target?: InstanceSummary,
}


/**
 * 集まり・集合の見せ方と集計（browse.group_view と _profile）。インスタンスの近隣の集まり（RelatedGroup）と、 ドメインの集合（DomainRows）が同じ形で持つ（部分集合も全体も同じ規則で見せる）
 */
export interface GroupProfile {
    view: string,
    /** 集まり・集合の行の数（集合なら検索で絞る前。LinkGroup なら relation の表の行の数）。rows は先頭・新しいものからの一部 */
    count: number,
    /** 出来事の時刻の列（role event_time。LinkGroup なら relation の表の列）。rows はこの列の新しい順 */
    time_column?: string,
    /** 期間の始まりの列（role valid_from。LinkGroup なら relation の表の列） */
    period_from?: string,
    /** 期間の終わりの列（role valid_to） */
    period_to?: string,
    /** 尺度の集計（集まり全体） */
    measures?: MeasureValue[],
    /** 集計しなかった尺度と理由（単位が混ざる、時間の軸で合計できない、など） */
    skipped_measures?: SkippedMeasure[],
    /** 内訳の列（enum の意味型の列） */
    breakdown_column?: string,
    breakdown?: BreakdownValue[],
    breakdown_truncated?: boolean,
    series_unit?: string,
    /** series の各点の values に入る尺度の列（合計） */
    series_measures?: string[],
    /** 区切りの古い順。行の無い区切りも count 0 で入る（時間軸を詰めない） */
    series?: SeriesPoint[],
    /** 最新の日（time_column の値の日付で最も新しい日）のまとめ。データが今日まで届いていれば今日（is_today）。 届いていなければ最新の日を基準日として明示する */
    latest?: LatestDay,
}


/**
 * インスタンスの近隣の集まり。見せ方・集計・行は集まりの表（table）の行について：1:n なら参照してくる子の行、n:m なら relation の表の行でつながる先の行（重複しない。八百屋 X の野菜）。n:m のつながり自体（期間・数量などの属性）は links
 */
export interface RelatedGroup extends GroupProfile {
    kind: string,
    /** たどった関係の安定 ID（via なら、relation の表から先への関係） */
    relation: string,
    relation_type: string,
    /** 集まりの行のドメイン */
    table: string,
    name: string,
    /** 参照に使う列。children なら集まりの表の列（このインスタンスを指す列）、via なら relation の表の列（先を指す列） */
    columns: string[],
    /** 挟んだ relation の表（via のとき） */
    via_table?: string,
    /** relation の表から、このインスタンスへの関係の安定 ID（via のとき）。relation の表が同じ表へ2本の参照を持つと （部品表の組立品と部品）、どちら側から見た集まりかを区別する */
    via_relation?: string,
    /** relation の表の、このインスタンスを指す列（via のとき） */
    via_columns?: string[],
    via_name?: string,
    rows: InstanceSummary[],
    truncated: boolean,
    /** n:m（via）のとき、つながり（relation の行）の集計 */
    links?: LinkGroup,
}


/**
 * n:m のつながり（relation の表の、親を指す行）の集まり。見せ方と集計は relation の行について（期間の帯、数量の合計など）。 rows は relation の行ごとの先のインスタンスで、その行の属性を link_cells に持つ（同じ先が2回出ることがある）
 */
export interface LinkGroup extends GroupProfile {
    /** relation の表の安定 ID */
    table: string,
    name: string,
    rows: InstanceSummary[],
    truncated: boolean,
    /** relation の行が指す先の行が無かった数（壊れた参照） */
    missing: number,
    /** relation の行のうち、先を指す列が NULL の数（先が無いつながり。壊れた参照ではない）。打ち切っていなければ rows の数 + missing + unlinked = 読んだ relation の行の数 */
    unlinked: number,
}



export interface MeasureValue {
    column: string,
    op: string,
    /** 表示の文字列。対象の値が無ければ無し */
    value?: string,
    /** 単位（固定の単位、または単位の列の値がすべて同じならその値） */
    unit_text?: string,
}



export interface SkippedMeasure {
    column: string,
    reason: string,
}



export interface BreakdownValue {
    /** 表示の文字列（enum はラベルつき）。NULL は無し */
    value?: string,
    count: number,
}



export interface SeriesPoint {
    /** 区切りの始まりの日付（ISO 8601） */
    start: date,
    count: number,
    /** series_measures の順の合計。区切りに値が無ければ value 無し（0 とは区別する） */
    sums: SeriesSum[],
}



export interface SeriesSum {
    value?: number,
}



export interface LatestDay {
    day: date,
    is_today: boolean,
    count: number,
    /** 加法の尺度の、最新の日の合計 */
    measures?: MeasureValue[],
    /** 半加法の尺度を合計した時点（time_column の最大の値。表示の文字列） */
    snapshot_at?: string,
    /** 半加法の尺度（残高・在庫など）の、最新の時点（snapshot_at）の行だけの合計 */
    snapshot?: MeasureValue[],
}


/**
 * 表の geo.point の列の値を点にしたもの（地図のため）。プロセス境界（HTTP API → UI）で受け渡す形。 変換できなかった行は黙って捨てず failed に、NULL は数を nulls に持つ。 rows_read = points の数 + nulls + failed の数。truncated なら、表には limit を超える行がある
 */
export interface PointSet {
    /** 表の安定 ID */
    table: string,
    column: string,
    points: GeoPointRow[],
    nulls: number,
    failed: PointFailure[],
    /** 読んだ行の数（limit 以下） */
    rows_read: number,
    /** 読む行の上限 */
    limit: number,
    /** 上限で打ち切った（表にはまだ行がある） */
    truncated: boolean,
}



export interface GeoPointRow {
    /** 行の主キーの値（主キーの列の順）。各値をソースの値の文字列表現（Python の str）にしたもの。 主キーは NULL を持たない（仕様 4.2） */
    key: string[],
    lat: number,
    lon: number,
}



export interface PointFailure {
    key: string[],
    reason: string,
}



