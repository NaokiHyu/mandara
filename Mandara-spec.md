# Mandara（仮称）仕様書 v0.4

雑に積まれたRDB・ファイル群・メディア・既存システムのAPIから、AI推論と人間レビューを経て**オントロジカルメタデータ**を半自動生成し、それをgit管理されたファイルとして保持する。生成されたオントロジー層は、データを自前で持たず（ゼロコピー）、DWH・ナレッジDBとして使えると同時に、DDDスタイルのプログラミングインターフェース・セマンティックモデル・UIを自動で提供し、AIにも認可付きで操作権を与える。書き込みは既存システムの書き込み経路（同期呼び出し：API・ストアドプロシージャ、または非同期の受入表）だけを通す。

---

## 1. 目的と非目的

### 目的
- 既存データ資産（RDB、DWH、ストレージ上のファイル、画像・音声、既存システムのAPI仕様）から、意味を持つメタデータを**半自動**で起こす
- メタデータを**人が読めて、diffでき、レビューできる**ファイルとしてgitで管理する
- オントロジー層を「データの辞書」で止めず、**実行可能なドメインモデル**にする
- 同じメタデータから、集計・統計のための**セマンティックモデル**も生成する
- 人間とAIが**同じ型つき世界**の上で、同じ面を見ながら操作できる状態を作る

### 非目的
- 完全自動化。AIは提案のみを書き、承認は常に人が行う
- 顧客向けの磨かれたUX。自動UIは管理・運用画面を対象とする
- 既存DBの置き換え。既存DBは正本のまま残す
- 自前のストレージ・クエリエンジン。データの保管と集計は既存のDB・セマンティックレイヤーに委ねる
- 既存システムの業務ルールの再実装。書き込みは既存の書き込み経路を通す。受入経路の無い表は読み取り専用とし、SQL直書きやRPAで補わない
- OWL完全互換の推論器。推論は限定的なルール（is-a継承、推移、逆関係）に絞る

---

## 2. 用語

| 用語 | 意味 |
|---|---|
| ソース | 接続先のRDB、DWH、ファイル群（CSV/Excel/Parquet/PDF/Markdown等）、メディア（画像・音声）、既存システムのAPI仕様（OpenAPI/WSDL/ストアド定義） |
| オントロジカルメタデータ（OM） | 表種別・列ドメイン・関係型などの意味注釈の集合 |
| 語彙 | OMで使用を許可されたアノテーション値の閉じた集合 |
| コンテキスト | DDDの境界づけられたコンテキスト。同名概念が別物であることを許す単位 |
| ドメインオブジェクト | domain種別の表から生成される、メソッドを持つ型 |
| 型つきモデルコード（TMC） | Typed Model Code。OMから自動生成した型と、その上に人が書く小さな参照透過な関数（メソッド）からなるコード層。OMが「何があるか」、TMCが「どう振る舞うか」を担う（7章） |
| クエリ / コマンド | 値を返すメソッド / 実行したい操作を `Command` の値として返すメソッド。どちらも参照透過で、実際の読み書きは実行環境が行う（7.3） |
| モデラー | OMとその周辺の宣言を提案するAI。生成パイプライン（6章）として動く |
| ビルダー | TMCのコードを書くAI。開発者の道具として動く。Mandaraの外のツールでもよい（3.3） |
| オペレーター | TMCを使って業務を動かすAI。認可と承認キューの下で動く（8章） |
| バックエンド | コマンドの実行経路。`call`（同期呼び出し：既存API・ストアド）と `handoff`（非同期：既存システムが取りに来る受入表・受入ファイル）の2種のみ（7.4） |
| 変換関数 | メディアや文書から列・表を導出する関数。決定的／確率的の2種 |
| メトリクス | セマンティックモデル上の指標定義（計算式、粒度、フィルタ） |
| 表示アノテーション | UI生成のための注釈。OMとは別ファイル |
| ゼロコピー | Mandaraが業務データを自前で持たない原則 |
| write-through | コマンドをソース側の書き込み経路で直接実行し、オントロジー層に状態を持たないこと。経路は `call` か `handoff` に限る |

---

## 3. アーキテクチャ（三層）

```
┌─────────────────────────────────────────────────┐
│ インターフェース層                                │
│  ・プログラミングIF（TMC：型つきモデルコード）      │
│  ・自動UI                                        │
│  ・AIツール面（メソッドとメトリクス問い合わせ、認可付き）│
├─────────────────────────────────────────────────┤
│ オントロジー層（gitリポジトリ）                    │
│  ・OM、語彙、コンテキストマップ                    │
│  ・メソッド、コマンドアダプタ、変換関数、メトリクス定義│
│  ・表示アノテーション、認可ポリシー                 │
│  ・推論層（限定ルール）                           │
├─────────────────────────────────────────────────┤
│ ソース層（正本のまま）                            │
│  ・RDB / DWH          ← 読み：SQL／フェデレーション │
│  ・既存システムのAPI・ストアド・受入表 ← 書き：コマンド│
│  ・ファイル群、メディア（S3等）                    │
│  ・セマンティックレイヤー（書き出し先）             │
└─────────────────────────────────────────────────┘
```

原則：
- **ゼロコピー**。Mandaraは業務データを自前で持たない。持つのは OM・ポリシー・監査ログ・評価セットのみ。Mandaraを撤去しても業務データは何も失われない
- **正本はソース層**。コマンドは write-back ではなく **write-through**：ソース側の書き込み経路で直接実行し、オントロジー層に状態を持たない
- **読みと書きの経路を分ける**。読みは SQL／フェデレーション、書きは既存システムの API・ストアド（`call`）か受入表（`handoff`）の2経路だけ。SQL 直書きと画面操作は持たない。どちらも無い表は読み取り専用（7.4）
- **OMはgitが正本**。DBやUIが持つOMはすべてリポジトリから派生する
- **AIは提案のみ**。`status: approved` を書けるのは人間だけ。AIの役割は3つに分ける（3.3）

### 3.1 ゼロコピーの定義とキャッシュ

ゼロコピーは「保存しない」ではなく、Mandaraが持ってよいものの3条件で定義する：(1) TTLで消える、(2) ソースから再生成できる、(3) 取得した本人以外に出さない。例外として、**表単位で明示宣言し鮮度SLAを付けた読み取りレプリカ**（第3段）だけはTTLを外せる。黙って持たない、が本質。

| 段 | 対象 | 規則 |
|---|---|---|
| 0：メタデータ | 解析済みOM、生成した型、ツールスキーマ、ポリシーのコンパイル結果、主張台帳の索引、列名・文書言及の埋め込み | Mandaraの所有物。OMのマージで無効化（9.7）。プロセス内LRU |
| 1：プロファイル統計 | 基数、NULL率、ヒストグラム、包含判定の材料 | 値ではなく**スケッチ**（HyperLogLog、MinHash、Bloom）で持つ。サンプル行だけ例外：マスキング後、TTLつき、レビュー表示のみ、`purge` で全消去。ソースごとにDuckDBファイル1つ |
| 2：読み取り結果 | 一覧・分析の結果 | キーは（主体の認可スコープ、クエリ、OM版、ポリシー版）。スコープを含めないと行レベル認可が破れる。メモリのみ、短TTL、永続化しない。Mandara経由のコマンド成功（`handoff` は完了判定時）で対象表を無効化、外部更新はTTLが保険 |
| 3：読み取りレプリカ | 速くしたい表の複製 | 表メタデータの `read_from`（4.2）で宣言。読み経路だけが切り替わり、**write-through は変えない**。DDL・同期ジョブ・PIIマスクはOMから生成。結果に `as_of` を付け、コマンド成功後は対象行を短時間ソースから読む（read-your-writes） |

持ってはいけないもの：利用者をまたぐ共有結果キャッシュ、宣言のないメディアの複製、書き込みバッファ（承認キューは意図のログであってデータではない）。第2段を超える需要は、まずデータを持つ側に押し下げ（ソースDB内 `Mandara_derived` のマテビュー、セマンティックレイヤーの事前集計）、それでも足りない表だけ第3段のレプリカを宣言する。

レプリカが向く表：横断JOINの常用、アナリシスUI（9.6）の下、全文検索、グラフのたどり、ソースが遅い・夜間しか叩けない。向かない表：承認待ちコマンドの判断材料（鮮度が命）、`handoff` バックエンドしか無く完了判定まで更新を検知できない表。

### 3.2 ゼロコピーの代償と、その扱い

| 代償 | 方針 |
|---|---|
| 複数ソースにまたがるコマンドで単一トランザクションが組めない | 既定は「1コマンド＝1ソース」。またがる場合は補償手順つき（サガ）として宣言を必須にする。各ステップが既存APIなら、補償は多くの場合「取消API」として既に存在する |
| 横断クエリの性能（フェデレーションは遅い） | 実体化が必要なら**ソースDB内の別スキーマ**（例：`Mandara_derived`）に置く。導出列・メディア由来の列も同じ。Mandara側のストレージには置かない |
| コピーによる緩衝が無く、オペレーターの操作が即・本番に効く | 承認キューと可逆性フラグを安全装置として扱う。保留中のコマンドはデータではなく**ログ**として持つ |
| 既存APIの冪等性が弱い | 冪等キーはMandaraが管理し、再試行はアウトボックス方式（7.4）。`handoff` は受入表のキーで冪等にする |
| 既存APIが無い・足りないソース | 既存システム側に受入表（`handoff`）を用意してもらう。それも無い表は**読み取り専用**。SQL直書き・画面操作で補わない（7.4） |

### 3.3 AIの3つの役割

Mandaraで働くAIは、役割で3つに分ける。層に対応しており、権限の線引きが名前で言える。

| 役割 | すること | 対応する層 | 動き方 | 書けるもの | 縛り |
|---|---|---|---|---|---|
| **モデラー** | 「何があるか」を提案する | OM | 生成パイプライン（6章）。ソースと主張台帳から自動で走る | OM、コマンドアダプタ、メトリクス定義、変換関数の仕様、表示アノテーション、watch の宣言 | `proposed` のみ。`evidence` と `confidence` が必須 |
| **ビルダー** | 「どう振る舞うか」を書く | TMC | 開発者と対話する道具（9.5） | メソッド、変換関数の実装と評価セット、認可ポリシーの草案。宣言の草案も書ける | PRのみ。マージには人の承認とCIの通過が要る |
| **オペレーター** | 実際に動かす | インターフェース層 | 人の依頼、または watch（7.5）で起こされる | 何も書かない。クエリを実行し、コマンドを起案する | 認可ポリシー（8.2）の範囲。不可逆なコマンドは承認キュー（8.3） |

規則：
- **リポジトリに書くのはモデラーとビルダー、業務データを変えられるのはオペレーターだけ**。オペレーターはOMもTMCも書き換えない。自分の権限を自分で広げられない
- モデラーとビルダーの違いは、書くものではなく動き方にある。モデラーは証拠から自動で提案し、ビルダーは開発者の指示で書く。同じ宣言を両方が書きうるが、どちらも同じレビュー経路を通り、既存の `approved`／`rejected` は上書きしない（6.3）
- 役割は権限の単位であり、モデルや製品の単位ではない。同じLLMが3役を務めてもよいが、主体と資格情報は役割ごとに分ける
- 人は3役すべての上位にいる。モデラーの提案に答え（9.3）、ビルダーのPRを承認し（9.5）、オペレーターの承認依頼を決裁する（9.4）

#### ビルダーはMandaraの外にいてもよい
ビルダーは役割であって、Mandaraの部品とは限らない。ブラウザ内のワークベンチに組み込んだエージェントでも、git＋エディタ＋市販のコーディングエージェントという手元の組み合わせでもよい（9.5）。外のビルダーをMandaraは制御できないので、縛りはツールの中ではなくリポジトリの入口に置く。

- **入口はPRだけ**。保護ブランチへの直接の書き込みは禁止する
- **検査はCIで行う**。バリデーション（4.5）、TMCの型の再生成と型検査（7.6）、評価（17章）
- **`status: approved` への変更は人のものだけを通す**。ビルダーのコミットに承認の書き込みが含まれていれば、CIで落とす
- **文脈はリポジトリから渡す**。OMのスキーマ、生成済みの型、規約を書いたファイルを置き、外のビルダーが読めば正しく書けるようにする
- **ソースには直結させない**。データを見る必要があるときは、Mandaraのツール面を通し、読み取り専用のクエリと `dry-run` だけを開発者の認可の範囲で許す。ソースの資格情報は渡さない

---

## 4. オントロジカルメタデータ仕様

### 4.1 語彙（`vocabulary.yaml`）

語彙は閉じている。語彙に無い値はバリデーションで拒否する。語彙の変更自体がレビュー対象。

```yaml
table_kinds:
  domain:   "ものを表す表（エンティティ、マスタ）。ライフサイクル（状態変化）を持ちうる。コマンド可"
  relation: "もの同士の関係を表す表（n:m中間表）。edge を宣言すると、1行がもの同士を結ぶ1本の辺になり、列は辺の属性になる（4.2）。コマンド可"
  event:    "出来事を表す表（ログ、計測、履歴）。不変・追記のみ。コマンド不可"
  derived:  "計算・集計で導出された表。コマンド不可"

relation_types:
  is-a:
    transitive: true
    inverse: has-subtype
    description: "型の階層。下位の型は上位の型の一種で、上位の性質を継承する"
  part-of:
    transitive: true
    inverse: has-part
    description: "構成階層。全体と部分"
  classified-as:
    transitive: true
    inverse: classifies
    description: "分類への所属。ものから分類へ、下位の分類から上位の分類へ（製品 → サブカテゴリ → カテゴリ）。全体と部分でも、型の一種でもない。ドリルダウン階層になる"
  has:
    transitive: false
    inverse: belongs-to
    description: "属性または参照。細分は has_kinds で指定"
  derived-from:
    transitive: true
    inverse: source-of
    description: "導出元。列や表が、どのソース（メディア含む）からどの関数で生まれたか。11章参照"

has_kinds:
  attribute: "値として持つ（住所、金額）"
  reference: "別のものを参照する（担当者、所属）"

cardinalities: ["1:1", "1:n", "n:1", "n:m"]

column_domains:                                        # type と単位（unit.fixed）は閉じた集合で、OMのメタモデル（7.6 の①）の enum として持つ
  customer_id: { type: identifier, description: "顧客の正本ID" }
  lat:         { type: geo.latitude }
  lon:         { type: geo.longitude }
  location:    { type: geo.point }                                                # 物理的な表現（バイナリ形式など）は列の encoding に書く（4.2）
  money:       { type: money, additivity: additive, requires_unit: true }        # 単位（通貨）は列の unit で指定する（4.2）
  quantity:    { type: quantity, additivity: additive, requires_unit: true }
  unit_price:  { type: money, additivity: non-additive, requires_unit: true }
  balance:     { type: money, additivity: semi-additive, requires_unit: true }
  occurred_at: { type: datetime, role: event_time }
  valid_from:  { type: datetime, role: valid_from }
  valid_to:    { type: datetime, role: valid_to }
  status:      { type: enum }
  image:       { type: media.image }
  audio:       { type: media.audio }
  document:    { type: media.document }
  row_uuid:    { type: identifier, role: technical, description: "行の技術的な一意ID（rowguid 等）。業務上の識別には使わない" }
  created_at:  { type: datetime, role: technical, description: "行の作成日時（監査用）" }
  modified_at: { type: datetime, role: technical, description: "行の最終更新日時（監査用。modifieddate 等）" }
  modified_by: { type: identifier, role: technical, description: "行の最終更新者（監査用）" }

additivities: [additive, semi-additive, non-additive]   # 10.4 参照

function_kinds:
  deterministic: "同じ入力から常に同じ出力（EXIF抽出、OCR、文字起こし）"
  probabilistic: "モデル推論を含み、出力に確信度を伴う（画像分類、物体検出、話者推定）"

aliases:                                               # 表記揺れ辞書（6.6 の根拠づけに使う）
  customer_id: ["顧客番号", "顧客ID", "得意先コード", "cust_no", "KUNNR"]
  order:       ["受注", "注文", "オーダー", "VBAK"]

command_backends:                                      # 7.4 参照。この2種のみ
  call:      "同期呼び出し。既存システムの REST / SOAP / RPC、ストアドプロシージャ。契約・検証・トランザクションは相手側。既定"
  handoff:   "非同期。既存システムが取りに来る受入表・受入ファイルに書く。受入表のキー・完了判定・却下行の宣言が必須"
```

列ドメインは「同じ意味」を表す。同じ型でも意味が違えば別ドメインにする（例：`order_date` と `ship_date`）。尺度になりうるドメインは `additivity` を持つ。`role: technical` のドメイン（行の技術ID、監査用の日時・更新者）は業務の意味を持たない列を表し、UI・オペレーターのツール面・セマンティックモデルでは既定で隠す。関係や同一性の推論にも使わない。

### 4.2 表メタデータ（`contexts/<context>/tables/<source>/<table>.yaml`）

```yaml
id: tbl_01HXY...            # 安定ID。改名しても変わらない
name: sales.orders          # 表示名（ソース上の名前）
context: sales
kind: domain                # 注文は状態（受注→出荷→取消）を持つので domain。出来事の記録は event（例：order_status_history）
grain: "1行 = 1注文"         # 尺度を持つ domain 表と event 表では必須（10.4）
status: approved
reviewed_by: alice
reviewed_at: 2026-10-11

identity:
  primary: [order_id]                 # 列の配列。複合キーは複数の列を並べる（1つの値として扱うのは生成される型の側）
  alternates:
    - columns: [legacy_order_no]

backend:                    # この表へのコマンドの既定経路（7.4）。省略時は読み取り専用
  default: call
  call: { source: erp_api, spec: "sources/erp_api.yaml" }

read_from:                  # 読み経路（3.1 第3段）。省略時は source
  mode: replica             # source | replica
  target: Mandara_derived    # ソースDB内の別スキーマ、または専用の読み取りDB（DuckDB / ClickHouse）
  sync: snapshot            # snapshot | cdc | trigger
  every: 15m                # snapshot のとき
  freshness_sla: 30m        # 超えたら UI と AI ツールの結果に「古い」を表示

columns:
  order_id:
    domain: order_id
    status: approved
  customer_id:
    domain: customer_id
    status: approved
  amount:
    domain: money                     # 量と単位を分ける。単位は unit で、固定値か同じ表の列を指す
    unit: { fixed: JPY }
    status: proposed
    confidence: 0.88
    evidence:
      - "列名 amount、整数、全値が正"
      - "ドキュメント 'orders仕様.pdf' p.3 に『税込金額（円）』"
  lat:
    domain: lat
    status: approved
  lon:
    domain: lon
    status: approved

relations:
  - id: rel_01HXZ...         # 関係の安定ID。同じ表への複数の参照を区別する
    type: has
    has_kind: reference
    role: orderer            # 参照の役割（任意。同じ表を複数回参照するときに使う）
    target: tbl_01HXZ...     # crm.customers。自己参照は self
    via: [{ from: customer_id, to: customer_id }]   # 列の対応。列で結べない関係は via_function に決定的関数の ID を書く
    cardinality: n:1
    status: proposed
    confidence: 0.92
    evidence:
      - "値の包含率 99.8%（orders.customer_id ⊂ customers.id）"
      - "列名一致"
```

列・関係の書き方の補足：
- is-a には、ソースでの実装パターンを `realized_by` で書ける。今あるのは `shared_key`（下位の表が上位の表と同じ主キーを持つ）だけ。排他的な参照や区分列などは、具体例で裏付けが取れてから足す
- `role: technical` のドメイン（4.1）の列は、業務の意味を持たない
- 中間表（`kind: relation`）は `edge` を宣言できる。1行が、もの同士を結ぶ1本の辺（プロパティグラフの辺）になり、表の列は辺の属性になる。`from` / `to` は、この表が持つ n:1 の参照（has / reference）の ID。辺の型は `has` / `part-of` / `classified-as` のどれかで、推移閉包・循環の禁止・ドリルダウンは表の間の関係と同じに効く。同じ表の行同士を結ぶ辺（部品表）はインスタンスの木で、表の間の循環ではない。行の間に循環が無いこと（本当に木であること）はデータの性質なので、OM の静的な検査ではなく、データについての主張（6.6）として検証する（self の関係も同じ）。`valid_from` / `valid_to` の列を持てば期間つきの辺、尺度の列を持てば重みつきの辺になる。`from` / `to` の列が NULL の行は辺にならない
  ```yaml
  # production.billofmaterials（kind: relation）
  edge:
    type: part-of
    from: rel_billofmaterials_component   # 部分（部品）の側の参照
    to: rel_billofmaterials_assembly      # 全体（組立品）の側の参照
  ```
- 主キー・代替キーの列は NULL を持たない。NULL を含みうる列の組は、データ上一意でも代替キーにしない
- ソース上の値の物理的な表現が、意味の型の標準的な表現と違うときは、列に `encoding` を書く（例：`domain: location` ＋ `encoding: sqlserver_geography`）。意味の型への変換は、encoding ごとのコーデック（Adapters の中で、DB コネクタから独立したもの）が行う。DB コネクタは形式を知らない。列ドメインには物理的な表現を持ち込まない。encoding の変換先の型と、列ドメインの型は一致しなければならない（4.5）
- 分類への所属（製品 → 製品分類）は `classified-as` で書く。上司への参照は `has`（reference）で書く。どちらも全体と部分ではないので `part-of` にしない
- enum の列ドメインは、値ごとに `code`・`label`・`observed`（データで観測されたか）と注釈の共通フィールド（4.4）を持ち、値ごとにレビューする。値の集合はデータからではなく、宣言と証拠から決める

### 4.3 コンテキストとコンテキストマップ（`contexts/<context>/context.yaml`, `context-map.yaml`）

同じ「顧客」でも営業と請求では別の概念であることを許す。

```yaml
# context-map.yaml
mappings:
  - from: { context: sales,   table: tbl_01HXZ... }   # 営業の顧客
    to:   { context: billing, table: tbl_01HYA... }   # 請求の顧客先
    kind: translation            # shared-kernel | translation | anti-corruption
    via:
      - { from: customer_id, to: account_no, method: lookup_table, source: "billing.customer_map" }
    status: approved
```

### 4.4 アノテーションの共通フィールド

すべてのアノテーションは以下を持てる。

| フィールド | 必須 | 意味 |
|---|---|---|
| `status` | ○ | `proposed` / `approved` / `rejected` |
| `confidence` | 提案時 | 0.0〜1.0。提案者（モデラーまたはビルダー）の確信度 |
| `evidence` | 提案時 | 主張台帳（6.6）の主張IDの配列。表示時は人が読める1行に展開する。本書の例では可読性のため文字列で示す |
| `inferred_by` | 提案時 | モデル名とバージョン、実行ID |
| `reviewed_by` / `reviewed_at` | 承認時 | 承認者と日時 |
| `note` | 任意 | 人間のメモ |

### 4.5 不変条件（バリデーション）
- 語彙に無い値を使っていない。①の enum（表種別、関係型、値の型、単位など）の外の値は、OMファイルを読み込む境界で拒否する（最初の1件で止まる）
- `derived` / `event` の表にコマンドが定義されていない。`derived` の表が `part-of` の全体側になっていない
- 推移的な関係（`is-a`、`part-of`、`classified-as`、`derived-from`）が、表をまたいで循環していない
- 列の `encoding` の変換先の型が、列ドメインの型と一致する
- `edge` は `relation` の表にだけあり、`from` / `to` はその表の、互いに異なる n:1 の has（reference）を指す。辺の型は has / part-of / classified-as。`part-of` の辺の全体側が `derived` の表でない
- 参照先の安定IDが存在する。`derived-from` が指す関数が `functions/` に存在する
- `approved` のアノテーションに `reviewed_by` がある
- `event` 表、および尺度を持つ `domain` 表に `grain` がある
- `semi-additive` / `non-additive` のドメインを `sum` するメトリクスが無い
- すべてのコマンドにバックエンドが決まる（表の `backend.default`、またはメソッド側の宣言）。`backend` の無い表にコマンドは定義されない。`handoff` には `inbound`、`key`、`completion` がある
- `read_from.mode: replica` の表には `freshness_sla` がある。`pii: true` の列はレプリカでマスクまたは除外される
- すべての `watch` に `budget` と `dedupe` がある。`tools_allowed` は認可ポリシー（8.2）の範囲内

---

## 5. リポジトリ構造

```
ontology/
  vocabulary.yaml
  context-map.yaml
  contexts/
    sales/
      context.yaml
      tables/
        pg_main/
          orders.yaml
          customers.yaml
      methods/
        orders.scala            # ユーザー定義メソッド（参照透過な関数）。TMC（7章）。言語は 7.6
      adapters/
        orders.ship.yaml        # コマンドアダプタ：コマンド→既存APIの写像（7.4）
      metrics/
        revenue.yaml            # メトリクス定義（10.4）
      display/
        customers.yaml          # 表示アノテーション
    facility/
      tables/
        s3_photos/
          equipment_photos.yaml
      functions/
        photo_to_equipment.yaml # 変換関数の仕様（11章）
        photo_to_equipment.py   # 変換関数の実装
      evals/
        photo_to_equipment/     # 評価セット（入力サンプルと期待出力）
    billing/
      ...
  policies/
    ai-ops-agent.yaml           # 認可ポリシー（8.2）
  packs/
    facility-maintenance/       # 有効化したパック（16章）。本体は外部リポジトリでもよい
  knowledge/
    sources.yaml                # 知識源の登録（種類、場所、抽出器、優先層）。文書本体は置かない
    codelists/
      jis_x0401_2023.yaml       # 取り込んだ公開コード表（版つき）
  sources/
    pg_main.yaml                # 接続定義（秘密情報は含めない）
    erp_api.yaml                # 既存APIの仕様参照（OpenAPI/WSDL）、冪等性の有無、サービスアカウント名
    s3_raw.yaml
    semantic_layer.yaml         # セマンティックモデルの書き出し先（10.4.4）
  runs/
    2026-10-11T0000Z.json       # 推論実行の記録（入力ハッシュ、モデル、提案数）
```

ontology リポジトリの外（git に乗せない作業領域）：
```
work/
  pg_main/
    claims/
      sales/orders.yaml         # 主張台帳（6.6）。主語の表ごとに1ファイル。決定的な抽出器が丸ごと作り直すキャッシュ
```

方針：
- 表1つにつき1ファイル。diffが局所化される
- キーは固定順序で書き出す。再生成で無意味なdiffを出さない
- 接続情報の秘密はリポジトリ外（環境変数またはシークレット管理）

---

## 6. 生成パイプライン

### 6.1 接続
- RDB：スキーマ（表、列、型、PK、FK、制約、コメント）、行数、列統計、サンプル行（上限と匿名化ルール付き）
- 表形式ファイル（CSV/Excel/Parquet）：表として扱う
- 文書（PDF/Word/Markdown/Confluence等）：**意味の証拠**として扱う。表や列に紐づく記述を抽出する
- メディア（画像・音声）：資産表として扱い、変換関数の生成対象にする（11章）
- 既存システムのAPI仕様（OpenAPI/WSDL）、ストアドプロシージャの定義、受入表・受入ファイルのレイアウト定義：**コマンドアダプタの証拠**として扱う（7.4）
- 既存のSQL、BIダッシュボード、dbtモデル、Excelの集計式：**メトリクス定義の証拠**として扱う（10.4.3）

### 6.2 プロファイル
| 観点 | 用途 |
|---|---|
| 一意性・NULL率 | 識別子候補の検出 |
| 値の包含率（A ⊂ B） | 未宣言の外部キー検出 |
| 値の重なり・分布の類似 | 同一ドメイン候補の検出 |
| 列名の正規化と類似 | ドメイン候補、関係名の候補 |
| 更新パターン（追記のみ等） | event / derived の判定材料 |

### 6.3 推論（モデラー）
入力：プロファイル結果、既存OM（承認済み）、語彙、主張台帳（6.6）、API仕様、既存の集計定義。推論は仮説駆動：スキーマから仮説を立て、台帳に賛成・反対の主張を探す（6.7）
出力：`status: proposed` の提案群。各提案に `confidence` と `evidence` を必ず付ける。提案の種類：
- OM（表種別、列ドメイン、関係、同一性、粒度）
- コマンドアダプタ（既存APIとコマンドの対応。7.4）
- メトリクス定義（10.4）
- 変換関数の仕様・コード・評価セット（11章）
- 表示アノテーション（9章）
- 監視（watch、7.5）

制約：
- 既存の `approved` / `rejected` を上書きしない
- 語彙に無い値を提案したい場合は、`vocabulary.yaml` への変更として別提案にする
- 確信度が閾値未満の提案は出さない（既定 0.6、調整可能）

### 6.4 レビュー
- モデラーは作業ブランチにYAMLを書き、**Pull Request**を作成する。PRは監査証跡であり、人が主に見る面ではない
- 人は **OMレビューUI（9.3）** で提案に答える。UIの操作がそのまま同じブランチのYAMLに `status: approved` / `rejected` と `reviewed_by` を書き込む
- 開発者はPRのdiffを直接編集してもよい。UIとPRは同じファイルを触るので併用できる
- マージ時にバリデーション（4.5）をCIで実行する

### 6.5 書き出しと再実行
- 再実行は冪等。ソースに変化がなければ提案は出ない
- ソースに追加・変更があれば、差分部分だけが `proposed` として積まれる
- `rejected` はソースが変わらない限り再提案しない（理由を `note` に残す）

---

### 6.6 知識源と主張台帳

「文書をAIに読ませる」のではなく、文書から**主張（claim）**を抽出して台帳にし、推論は台帳に対して行う。文書本体は元の場所（SharePoint、wiki、リポジトリ）に置いたままにする（ゼロコピーは知識にも適用する）。

#### 6.6.1 知識源の3層

| 層 | 知識源 | 性質 |
|---|---|---|
| 組織固有 | SQL・ビュー・ストアド・ORM・ETL・BI定義・Excelマクロ、設計書・画面定義・運用手順書、wiki・Slack・議事録・引き継ぎメモ、**レビューの回答** | 最も強い。組織の意味は常にこれが正 |
| パッケージ・規約 | SAP / Oracle EBS / Dynamics / Salesforce / kintone 等のスキーマ辞書、Rails / Django / JPA の命名規約 | ソース指定（9.2）で申告させる |
| オープンドメイン | 公開コード表（JIS X 0401、市区町村コード、ISO 3166 / 4217、業種分類、HSコード、GS1）、公開オントロジー（schema.org、FIBO、FHIR、UN/CEFACT）、LLMの世界知識 | 事前分布。組織固有と衝突したら組織固有が勝つ |

- 公開コード表・オントロジーは**版と出典**を記録して `knowledge/codelists/` に取り込む。市区町村コードのように改訂されるものは版が無いと証拠にならない
- `vocabulary.yaml` の初期語彙は公開オントロジーに寄せ、空から始めない
- 商用辞書・公開オントロジーはライセンスを確認して取り込む

#### 6.6.2 主張の形式

```yaml
# work/pg_main/claims/sales/orders.yaml（抜粋）
claims:
- id: clm_3f9a0c1d2e4b5a67      # 内容（subject、predicate、値、source）のハッシュ
  subject: { source: pg_main, schema_name: sales, table: orders }
  predicate: references         # 述語ごとに、主語の粒度と値のフィールドが決まっている（①の ClaimPredicate）
  columns: [customer_id]
  target: { source: pg_main, schema_name: crm, table: customers }
  target_columns: [id]
  on_delete: no_action
  on_update: no_action
  source: { kind: schema, locator: 'constraint "fk_orders_customer" on "sales"."orders"' }   # 出所の中の場所
  extracted_by: catalog 0.1     # 抽出器の名前と版。カタログの抽出器は DB に依存しない
  confidence: 1.0
```

- 主語は、OM の安定 ID ではなくソース上の名前で指す。主張は OM より先に作られるため
- `locator` は出所の中の場所（どの表・列・制約か）を指す。6.6.5 の「同じ出所は1票」はこの単位で束ねる
- **ID は内容から決まる**：主語、述語、値のフィールド、出所（種類と場所）の正規形のハッシュ。抽出器の版・確信度・日付には依存しない。決定的な抽出器を再実行すれば同じ ID になり、OM の `evidence` が切れない
- 決定的な抽出器（スキーマ、SQL、公開コード表）の主張は再生成できるキャッシュで、**git に乗せない**（`work/`）。再生成できない主張（レビューの回答、LLM による抽出）の保存方針は未決（15.2）
- 述語は①で閉じる。今あるのはカタログの抽出器が出すもの（declared_as、identified_by、unique、references、not_null、nullable、defaults_to、described_as、checks、typed_as、aliased_as）。カタログの事実はすべて主張にし、モデラーは主張だけを根拠にする（evidence で指せるように）。`means`、`same_as`、否定形（`not_*`）などは、それを出す抽出器と一緒に足す。否定形の述語で反証を記録できるようにする
- `source.kind` は `schema | sql | codelist | design-doc | memo | package | world | review`
- 提案（OM、アダプタ、メトリクス、関数）の `evidence` は主張IDを指す

#### 6.6.3 抽出器は入力の種類ごとに分ける

| 入力 | 抽出器 | 性質 |
|---|---|---|
| スキーマ制約（FK、CHECK、enum型） | カタログ読取 | 決定的 |
| SQL・ビュー・ストアド・ORM・ETL・BI定義 | **パーサ**。JOIN、CASE、WHERE の定数、`ref()`、VLOOKUP の参照先 | 決定的 |
| 公開コード表 | 完全一致 | 決定的 |
| 設計書・画面定義 | 表・列への言及の紐付け＋文の抽出 | 半決定的 |
| メモ・議事録・Slack | LLM抽出。出力は 6.6.2 の形式のみ。自由記述は出させない | 確率的 |
| レビューの回答（9.3） | そのまま主張化。`source.kind: review` | 最上位 |

LLMは構造化できない最後の層だけに使う。

#### 6.6.4 根拠づけ（grounding）
文書中の「顧客番号」「受注テーブル」を、スキーマのどの列・表かに当てる工程。独立した提案として 9.3 のレビューに出し、紐付かない主張は推論に使わない。`vocabulary.yaml` の `aliases`（表記揺れ辞書）を使い、レビューの回答で辞書を育てる。日本語の略語・社内用語の揺れがここに集中するため、全体の精度はこの工程で決まる。

#### 6.6.5 証拠の既定確信度と合成

| 証拠 | 既定 |
|---|---|
| スキーマ制約（FK、CHECK） | 1.0 |
| レビューの回答 | 1.0 |
| 既存SQLのJOIN／CASE | 0.95 |
| 設計書・公開コード表の一致 | 0.85 |
| スキーマのコメント（表・列の説明） | 0.85 |
| 行単位の一致（アンカー結合、6.7） | 0.8 |
| パッケージ知識 | 0.8 |
| 結合妥当性（LLM判定、6.7） | 0.7 |
| 包含率・分布 | 0.6 |
| 列名類似・世界知識 | 0.5 |

- 同じ出所の主張は束ねて1票として数える（同じ設計書の2箇所は独立ではない）
- 衝突する主張（設計書は「3＝出荷」、SQLは「3＝取消」）は平均せず、`valid_at` を添えて**衝突として問いに出す**（9.3）
- 推論実行ごとの入力は `runs/` に記録し、どの主張から提案が出たかを再現できるようにする

#### 6.6.6 段階投入
スキーマ制約 → コード → 公開コード表 → 設計書 → メモ → 世界知識、の順に入れる。後の層は、前の層で決まらなかった仮説にだけ使う。一括投入はしない。

### 6.7 関係検出の手法

関係（`has` 参照、同一ドメイン）の検出が生成パイプラインの核であり、包含関係（値の一致）だけでは低カーディナリティ列で破綻する。手法を4群に分け、決定的なものから順に適用する。

#### 6.7.1 発想の転換
1. **まずコードを掘る**。既存SQL・ビュー・ストアド・ORM・BI・ETL・クエリログ（pg_stat_statements、Query Store、AWR）のJOIN条件は、関係の宣言そのもの。統計より先に、確信度も高い
2. **低カーディナリティ列は、関係ではなく列挙ドメインとして扱う**。`status` が 1〜5 なら、コード表への参照より「status という意味の enum」を先に当てる。ラベルは `CASE WHEN status=1 THEN '受注'`、アプリの定数、画面の選択肢、公開コード表から拾う。問いが「どの表を参照するか」から「このコードの意味は何か」に変わる

#### 6.7.2 手法

**A. 決定的な証拠（最優先）**
- JOIN条件、CASE式、VLOOKUP の参照先、ORM の関連定義、`ref()`
- スキーマの残滓：インデックスの有無、同じ型と長さ（`CHAR(2)` 同士）、CHECK制約、デフォルト値、列コメント

**B. 統計（包含の先）**
- **被覆率**：参照先の値のうち、参照元に現れる割合。`1〜5 ⊂ 1〜100` は包含として成立しても被覆率が低い
- **ランダム性**：参照元の分布が、参照先からの無作為抽出らしいか
- **アンカー結合による行単位の一致**：確定済みの高カーディナリティのキー（`order_id`）で2表を結合し、候補列が行ごとに一致する率を見る。集合の重なりではなく行の一致なので、低カーディナリティでも効く。同一ドメイン判定の主手法
- **相互情報量／関数従属**：候補列が同じ表の他列をどれだけ決定するかを、参照先候補の属性と突き合わせる

**C. 意味**
- 列名・表名・ラベルの埋め込み。`aliases` で日本語・略語・和製英語の揺れを吸収する
- **結合妥当性の判定**：仮説で結合したサンプル行（注文123、status=3→「出荷済」、日付、金額）をLLMに見せ、業務として自然かを答えさせる
- 文書証拠（6.6）との照合

**D. 大域整合**
- 候補を列単位で独立に決めず、全体で割り当てを解く。事前分布：1列は1参照先、コード表は多数から参照される、コンテキストをまたぐ参照は少ない
- 負の証拠：孤児値（参照元にあって参照先に無い）、型不一致、不自然な自己参照。`not_references` として台帳に残す

#### 6.7.3 低カーディナリティ列の処方箋
コードを掘る → enum 候補として提示 → 公開コード表と照合 → コード表があればアンカー結合（B）と結合妥当性（C）で紐付け → 残りは大域整合（D）。包含率は補助に降格させる。

---

## 7. プログラミングインターフェース（型つきモデルコード、TMC）

プログラミングインターフェースの実体を**型つきモデルコード（Typed Model Code、TMC）**と呼ぶ。TMCは、OMから**自動生成した型**と、その上に人が書く**小さな参照透過な関数（メソッド）**からなる。OMが「何があるか」を表すデータで、TMCが「どう振る舞うか」を表すコードである。

人は型を書かず、I/O も書かない。読み込み、書き込み、認可、承認、冪等性、再試行は実行環境が担う（functional core, imperative shell）。メソッドは「値を受け取り、値を返す」ことだけをする。言語は固定しない（7.6）。

### 7.1 ドメインオブジェクトとグラフ問い合わせ
- `kind: domain` の表ごとに型を生成する。関係は型つきの参照として現れる
- ドメインオブジェクトは**不変の値**で、読み込んだ時点の `as_of` を持つ
- 読みは、OMから自動で提供する**汎用のグラフ問い合わせ**で行う。関係のたどり、is-a 判定、part-of の上下を含み、ユーザーのコードなしで動く。人間UI（9.4）、オペレーター（8章）、対話的な問い合わせ面（REPL）はすべてこれを使う

```
# 記法は例。具体的な構文は言語ごとの型生成（7.6）で決まる
Customer.get(CustomerId("C-1001"))
  .orders                  # has 1:n → コレクション
  .region.parent           # part-of → 親
Customer.isA(Account)      # is-a 判定（推移的）
```

### 7.2 デフォルトメソッド
汎用のグラフ問い合わせとコマンドアダプタ（7.4）から提供する。ユーザーのコードは要らない。

| 種別 | メソッド | 備考 |
|---|---|---|
| クエリ | `get`, `find`, `list`, `count` | 全種別 |
| クエリ | `history(at=...)` | temporal がある場合 |
| クエリ | `parts()`, `whole()` | part-of |
| クエリ | `subtypes()`, `supertypes()` | is-a |
| コマンド | `create`, `update`, `delete` | domain / relation のみ。event / derived は不可。表の `backend.default` 経由で実行され、`backend` の無い表（読み取り専用）には生成されない |
| コマンド | `link(rel, target)`, `unlink` | relation |

### 7.3 ユーザー定義メソッド（参照透過な関数）
`contexts/<context>/methods/<table>.<拡張子>` に定義する。クエリかコマンドかを必ず宣言する。

```scala
// contexts/sales/methods/orders.scala（例。言語は 7.6）

// クエリ：必要な部分グラフを宣言し、値を返す
@query(reads = "self { totaldue, customer { creditlimit } }")
def overCreditLimit(self: Order): Boolean =
  self.totaldue > self.customer.creditlimit

// コマンド：実行したい操作を Command の値として返す。送信は実行環境が行う
@command(backend = "call", reversible = false, approval = "required")
def ship(self: Order, carrier: Carrier): Either[ShipError, Command[Shipment]] =
  if self.status != OrderStatus.Approved then Left(ShipError.NotApproved(self.status))
  else Right(Command.adapter("Order.ship", carrier = carrier))   // adapters/orders.ship.yaml
```

形の規則：
- **メソッドは参照透過な関数**。入力は自分（`self`）、宣言した部分グラフ、引数だけで、同じ入力には常に同じ値を返す。現在時刻や乱数が要るときは引数として受け取る。副作用を持たないことは、型（I/O に届く API を生成しない）、静的検査（I/O・時刻・乱数・グローバル状態に触る API の禁止）、実行環境（資格情報もネットワークも無い隔離実行）の3段で保証する。どれか1つに頼らない
- **読む範囲は先に宣言する**（`reads`）。実行環境が部分グラフを取得し、不変のスナップショットとして `as_of` つきで渡す。関数の中で関係を遅延で読むこと（隠れた I/O）は許さない。宣言していないフィールドや関係に触ったらエラーにする（できれば型エラー、できなければ実行時エラー）。NULL や空で黙って続行しない
- **部分グラフには件数の上限がある**。上限を超えたら、切り詰めずにエラーにする
- **集計は書かない**。合計、平均、統計は `metric.query`（10.4.5）に押し下げる。メソッドが担うのはオブジェクト単位の業務ロジックまで（ゼロコピーと性能のため）
- **コマンドは `Command` の値を返す**。どのコマンドアダプタ（7.4）を、どの引数で呼ぶかという意図の記述である。認可（8.2）、承認キュー（8.3）、冪等キー、`call` / `handoff` の実行は実行環境が担う。承認キューが「意図のログ」であることと同じ形になる
- 前提条件を満たさないときは、値としてのエラー（`Either` など、言語の慣習に従う）を返す。例外で抜けない

宣言と継承：
- `@command` は `backend`、`reversible`、`approval`、`limits` を宣言する。`backend` を省略すると表の `backend.default` に従う
- is-a の上位で定義したメソッドは下位に継承される
- OM・メソッド・アダプタ・表示アノテーションは**同じPR**で変更する。CIで整合性を検証する

参照透過であることの帰結：
- 法則を Property-based テストで検証できる。入力が値だけなので、生成器から直接テストできる
- 結果をキャッシュしやすい。キャッシュは実行環境の責務で、キーは 3.1 第2段に揃える：主体の認可スコープ、メソッド、引数、スナップショットの同一性（ソースごとの `as_of` または版）、OM版、ポリシー版。スコープを外すと行レベル認可が破れる。コマンド成功時の無効化も 3.1 第2段に従う
- そのままオペレーターのツール（8.1）として公開でき、隔離実行（16.5）もしやすい

### 7.4 実行モデル（読みと書きの分離）

**読み**はSQL／フェデレーションで行う。**書き**は既存システムの書き込み経路を通す。Mandaraは業務ルール・検証・トランザクション境界を再実装せず、既にそれを持つ場所に委譲する（DDDの腐敗防止層）。委譲先が無い表には書かない。

#### バックエンド（2種のみ）

| 種別 | 性質 | 含むもの | 使う場面 | 備考 |
|---|---|---|---|---|
| `call` | 同期呼び出し | REST / SOAP / RPC、ストアドプロシージャ | 相手側に呼び出し可能な契約がある | **既定**。検証とトランザクションは相手側。補償は取消呼び出し |
| `handoff` | 非同期の受け渡し | 連携用テーブル、受入ファイル、リバースETL（TROCCO 等） | 契約は無いが、既存システムが取りに来る受入経路がある | 受入表のキー・完了判定・却下行を宣言する。完了判定まで「実行中」 |

どちらも無い表は**読み取り専用**。コマンドは生成されず、UIにボタンも出ず、オペレーターのツールにも現れない。受入経路を用意するのは既存システム側の作業であり、Mandaraの外。SQL直書き（業務ルールの部分的な再実装になる）と画面操作（可逆性を保証できない）は持たない。書きたくなった表から順に受入表を1つずつ開けるため、段階導入の単位にもなる。

`handoff` の宣言（表メタデータ）：

```yaml
backend:
  default: handoff
  handoff:
    inbound: erp.if_shipment_in            # 受入表（またはファイルのパス）
    key: [order_id, carrier_code]          # 冪等性はこの表のキーで担保。同じキーの行は二度書かない
    completion: { poll: erp.if_shipment_result, every: 5m }   # 完了判定。poll | file | webhook
    reject: erp.if_shipment_error          # 却下行。読み戻して承認者に返す
```

#### コマンドアダプタ（`adapters/<table>.<command>.yaml`）
コマンドと既存APIは1対1とは限らない。写像はアダプタとして宣言し、モデラーまたはビルダーが提案、人が承認する。

```yaml
command: Order.ship
backend: call
source: erp_api
call: POST /orders/{order_id}/shipments
map:
  order_id: self.order_id
  carrier_code: carrier.code
idempotency:
  key: "ship:{order_id}:{carrier.code}"
  api_supports: false           # false なら送信前に重複チェック、再試行はアウトボックス方式
compensation:
  call: DELETE /orders/{order_id}/shipments/{shipment_id}
status: proposed
confidence: 0.9
evidence:
  - "OpenAPI の operationId createShipment"
  - "画面『出荷登録』が同じエンドポイントを呼んでいる"
```

#### 実行の規則
- 既定は「1コマンド＝1ソース」。複数ソースにまたがるコマンドは `saga: true` と各ステップの補償手順を必須とする（`call` は取消呼び出し、`handoff` は受入行の取り下げ、または却下行の読み戻し）
- 冪等キーはMandaraが管理する。APIが冪等でない場合は送信前に重複を確認し、再試行はアウトボックス方式で行う。`handoff` は受入表の `key` で冪等にする
- `handoff` は完了判定が出るまで「実行中」として扱い、`reject` に行が現れたら失敗として承認者に返す。完了判定の無い受入経路は宣言できない（4.5）
- 既存システムの資格情報はAIに渡さない。Mandaraがサービスアカウントを保持し、認可ポリシー（8.2）でオペレーターの主体を対応づける
- 外部副作用（メール送信、外部API）を持つコマンドは `external: true` を宣言する
- すべてのコマンドに `dry-run` モードがある：認可判定、アダプタの写像、冪等キーの生成まで行い、送信しない。ワークベンチ（9.5）の既定
- すべてのコマンドを監査ログに記録する：主体、コマンド、バックエンド、引数、対象行、結果、承認者

---

### 7.5 監視と通知（watch）

REPL（7.1）はオペレーターが世界に問いかける方向。watch はその逆で、世界の変化がオペレーターを起こす。これにより道具からオペレーションルームになる。

#### 宣言
watch は表メタデータの注釈であり、他の注釈と同じくモデラーが提案し、人がレビューする。コードではない。

```yaml
# contexts/sales/tables/pg_main/orders.yaml（抜粋）
watches:
  - id: wch_01J...
    on: transition             # transition | threshold | staleness | arrival | schedule | anomaly
    condition: "status: 出荷済 -> 返品"
    notify:
      subject: "ai:ops-agent"
      intent: "返品理由の分類と、担当者への連絡案の作成"
    tools_allowed: ["Order.*.query.*", "Order.propose_followup"]
    budget: { per_event_tokens: 20k, per_hour: 30 }
    dedupe: { key: order_id, window: 24h }
    cascade_depth: 2
    status: proposed
    confidence: 0.8
    evidence: ["運用手順書『返品対応』p.2", "返品は月平均 40 件（過去 12 か月）"]
```

`on` の種類は OM から導ける。監視候補の自動提案はこれに基づく。

| OM | watch の種類 |
|---|---|
| `enum` 列（status） | transition：状態遷移 |
| `additivity` を持つ尺度、メトリクス（10.4） | threshold / anomaly：閾値・異常 |
| `freshness_sla`（3.1）、`valid_to` | staleness：鮮度・期限切れ |
| `event` 表 | arrival：到着 |
| `role: event_time` | schedule：定期評価 |

#### 発火と通知
- 発火元：ソース側の CDC・トリガー・Webhook、レプリカの差分（3.1 第3段と同じ経路）、`metric.query` の定期評価、Mandara経由のコマンド成功
- オペレーターに渡すのは**変化の記述**だけ：before / after、`as_of`、どの watch がどの行で発火したか。生の表は渡さず、必要なものはオペレーターが REPL で引く
- 通知は不変の事実として `event` 表（`Mandara_derived` 内）に追記する。監査ログもタイムラインUI（9.4）もそのまま効く

#### 行動の縛り
- 起こされたオペレーターも通常の AI 実行面（8章）と同じ：ツールは `tools_allowed` と認可ポリシーの交わり、コマンドは承認キュー経由。「アラートだから特別に動かせる」は作らない
- 自律実行は `reversible: true` かつポリシーで明示許可された範囲のみ。それ以外の出力は提案か承認依頼
- **予算**：1イベントあたりのトークン、時間あたりの件数。超えたら通知をキューに溜め、人に知らせる
- **デデュープ**：同じキーで窓内に再発火しない
- **カスケード抑制**：オペレーターのコマンドが別の watch を発火させてループになる。通知に起点 ID を持たせ、同一起点の深さを `cascade_depth`（既定 2）で止める

#### REPL との関係
- REPL からは `Order.on("transition", ...)` のように見える。ただし REPL 内の購読はセッションと共に消える一時的なもので、永続する購読は watch として OM に書く
- 同じ watch に対して、人は通知を受け、オペレーターは起こされて考える。ここでも人とオペレーターは同じ面に乗る（9.4）

#### 実装
watch の評価とオペレーターの起動は Temporal のワークフローとして置く（7.4 のアウトボックス、8.3 の承認キューと同じ基盤）。

### 7.6 型生成と言語

#### 方針
- **OMが正、型は派生**。TMCの型はOMから生成し、手では書かない。手で書くのはメソッド（7.3）だけ
- **スキーマは LinkML で2段に持つ**
  - ① **OMのメタモデル**：「表の注釈とは何か」（`kind`、`grain`、`status`、`confidence`、`evidence` など）。Mandara 自身が持ち、人が書く。4.5 のバリデーションの一部はここから導く
  - ② **ドメインのスキーマ**：承認済みのOMから出力する、そのオントロジーのクラス・関係・列挙。人は書かない。各言語の型、OWL / JSON Schema などのエクスポート（10.3）は②から生成する
- **OMそのものはコードにしない**。OMを書くのはモデラーの提案とレビューUIであり（6.4）、注釈ごとに `status`・`confidence`・`evidence` を持つ。データのままにしておくことで、機械的な書き換えと注釈単位のdiffが保てる
- **型で前倒しする**。「同じ型でも意味が違えば別ドメイン」（4.1）、「クエリはコマンドを実行できない」（7.3）、「event / derived はコマンド不可」（7.2）を、実行時の検査に加えて、言語が許す範囲で型検査の時点でも保証する。型で保証できない言語では、同じ規則を実行時の検査で守る

#### OM → 型の対応

| OM | 生成される型（言語ごとの表現は型生成の差し込み口が決める） |
|---|---|
| 列ドメイン（`customer_id`、`order_id`） | 名前つきの別の型（Scala の opaque type、Python の `NewType` など）。取り違えは型エラー |
| `type: money` ＋ `currency` | 通貨つきの金額型。通貨の違う加算は型エラー |
| `additivity` | 加法の尺度だけが合計を持つ。単価や残高には `sum` が生えない |
| `type: enum` ＋ ラベル | 列挙型。分岐の網羅性を検査する |
| 基数、NULL許容 | 値、省略可能な値、コレクションの区別 |
| `is-a` | 継承。上位のメソッドは下位で使える |
| `part-of` | `parts`／`whole` を持つ型 |
| 表種別 `event`／`derived` | コマンドを持たない型 |
| `@query`／`@command` | 戻り値は値／`Command` の値。クエリは `Command` を返せない |
| コンテキスト | 名前空間。`sales.Customer` と `billing.Customer` は別の型で、変換はコンテキストマップ（4.3）から生成した関数だけが行う |
| `temporal` | `history(at)` を持つ型 |

#### 規則
- 型は `status: approved` の注釈だけから生成する。`proposed` は型に現れない
- CIで②と型を再生成し、既存のメソッドが型検査を通らなければマージを止める。7.3 の整合性検証はこれで行う
- ゼロコピーなので、型はスキーマではなくソースを見るレンズである。型に合わない値（enum に無い値、NULLのはずがない列のNULL）は読み込み時に検出し、例外ではなく値として返す。「不明」値の意味論は15章
- コマンドの戻り値の型は既存APIの応答で決まる。アダプタ（7.4）の `map` に戻り値側の写像も宣言する

#### 言語
- **言語は固定しない**。選定基準は、その部品が使う手法に適合するライブラリがあるかどうか。型は②から言語ごとに生成する（差し込み口 16.1）ので、言語を足しても型を二重に書かない
- 言語を変えてよいのはプロセス境界だけで、そこには言語非依存の契約（OMファイル、主張台帳、ツール面／クエリAPI）がある
- **OMの生成と検証（6章）は Python**。SQL パーサ（sqlglot）、LLM による推論、統計、評価（17章）の資産が揃っているため
- **メソッド（7.3）は Scala 3**。DDD の記述に関数型の表現力（ADT、opaque type、enum、網羅性検査）を使うため。ただし Scala も副作用を型だけでは防げないので、7.3 の3段（型・静的検査・隔離実行）で保証する
- Rust は、変換関数とプラグインの隔離実行（16.5）の候補として残す。WASMでのサンドボックスが利点

---

## 8. AI実行面（オペレーター）

オペレーター（3.3）が業務を動かす面。オペレーターの主体は `ai:<名前>` で表し、用途ごとに複数置ける（以下の `ai:ops-agent` は一例）。

### 8.1 ツール公開
- ドメインオブジェクトのメソッドと、メトリクス問い合わせ（`metric.query`、10.4.5）を、そのままオペレーター向けツール（MCP互換）として公開する
- ツールの説明文はOMの `description` と `evidence` から生成する
- オペレーターは生SQLを発行しない。常に型つきのメソッド経由で操作する。コマンドは7.4のバックエンドを通るため、オペレーターが既存システムに直接触れることもない

### 8.2 認可
認可は「主体 × メソッド × 対象範囲 × 条件」で定義する。

```yaml
# policies/ai-ops-agent.yaml
subject: ai:ops-agent
grants:
  - methods: ["*.query.*"]
    scope: { context: sales }
  - methods: ["Order.ship"]
    scope: { rows: "region in (JP)" }
    limits: { per_hour: 50, max_amount_jpy: 1000000 }
    approval: human          # 不可逆のため人の承認を挟む
denies:
  - methods: ["*.delete"]
```

原則：
- **クエリは広く、コマンドは狭く**
- **不可逆（`reversible: false`）は既定で人の承認を要求**。`handoff` は完了判定まで結果が見えないため、`reversible: true` でも既定で人の承認を要求する
- 行・属性レベルの範囲、回数・金額の上限を持つ
- `derived` / `event` の表に対するコマンドは常に拒否
- オペレーターは既存システムの資格情報を持たない。ポリシーがオペレーターの主体とMandara側のサービスアカウントを対応づける

### 8.3 監査と承認キュー
- オペレーターのすべての呼び出しを7.4の監査ログに記録する
- 承認待ちのコマンドはキューに入り、人間UI上に承認カードとして現れる。キューはログであり、データではない（3.1）

---

## 9. MandaraのUI

### 9.1 全体構成
MandaraのUIは1つのアプリで、役割によって入口が変わる5つの画面からなる。すべての画面は同じOMファイル群を読む。画面間で意味がずれることはなく、代わりにOMの更新を各画面へ伝播させる仕組みを持つ（9.7）。

| 画面 | 主な利用者 | 役割 | gitの見え方 |
|---|---|---|---|
| 9.2 データソース指定 | データ管理者 | 接続先とスキーマの範囲、マスキング方針を決める | 隠す |
| 9.3 OMレビュー | データ管理者、現場のドメイン担当者 | モデラーの提案に答える | 隠す（裏で同じブランチに書く） |
| 9.4 オントロジーUI | 現場、運用 | ドメインオブジェクトを見て操作する。承認カードもここ | 隠す |
| 9.5 DDDワークベンチ | 開発者 | メソッド・アダプタ・関数を書く。ビルダーつき | 見せる |
| 9.6 アナリシスUI | 分析者 | セマンティックモデル上で集計・探索する | 隠す |

### 9.2 データソース指定
- 接続 → スキーマ一覧 → 含める／除外する、の3段。表単位の選択はしない（表の種別はモデラーが提案する）
- 生成前に人が決めるのはここだけ：`pii` の初期マスキング、サンプル行の上限と匿名化、プロファイルの実行範囲
- 接続直後にプロファイルの概要（表数、行数、未宣言FKの候補数、メディア件数）を見せ、推論の規模を先に把握させる
- API仕様（OpenAPI/WSDL）、BI・dbt・Excel、設計書・wiki・メモ等の知識源もここで登録する（6.1、6.6）。パッケージ製品のDBなら製品名を申告させる
- 知識源は場所と抽出器を登録するだけで、本体を取り込まない

### 9.3 OMレビュー
認知負荷を下げる方針は「diffを見せず、問いに答えさせる」。

- **質問の形**：提案を「`amount` は税込金額（円）ですか？」のような問いに変換し、根拠（`evidence`）を3行まで添える。回答は「はい／いいえ／別の意味」。「別の意味」は語彙から選ぶか、語彙への追加提案になる
- **影響順**：承認すると下流に何が生まれるか（UIのビュー、メトリクス、メソッド、ツール）の数で並べる。影響が大きい問いを先に出す
- **束で承認**：確信度が閾値（既定 0.95）以上の提案は一覧で一括承認できる。それ未満は1件ずつ
- **結果の予告**：「承認すると顧客一覧に地図ビューが付きます」「このメトリクスはBIの月次売上と一致します」のように、承認の帰結を先に見せる
- **不一致の提示**：10.4.3 の「同名メトリクスの定義が複数ある」のような提案は、選択肢を並べて選ばせる。モデラーは正を選ばない
- **確率的関数のレビュー**：11.2 のサンプリングレビューは、サンプル画像と推論結果を並べて正誤を付ける専用の形にする
- 回答の自由記述（「別の意味」の説明）は、`source.kind: review` の主張として台帳（6.6）に戻る。暗黙知はレビューの瞬間に形式化する
- 根拠づけ（6.6.4）の提案もこの画面に出る：「設計書の『顧客番号』は `customers.cust_no` のことですか？」
- 監視（7.5）の提案は帰結を予告する：「承認すると、返品が月 40 件程度オペレーターに通知され、承認依頼が増えます」
- 操作はそのまま同じブランチのYAMLに書き込まれ、PRとして履歴に残る（6.4）。gitは監査証跡であり、この画面には現れない

### 9.4 オントロジーUI（ドメインオブジェクトビューワ）
OMから自動生成される運用画面。表示のパターン（一覧、詳細、地図、ツリー、タイムライン、かんばん、グラフ、チャート）はあらかじめ作り込んでおき、OMがどれを使うかを決める。

#### 表示アノテーション（`display/<table>.yaml`）
意味（OM）と表示は分離する。表示アノテーションもAI提案→人レビュー（9.3）の流れに乗る。

```yaml
table: tbl_01HXZ...
label: 顧客
primary_fields: [name, region, status]
default_view: map            # list | detail | map | timeline | tree | graph | chart | kanban
column_order: [name, region, lat, lon, status]
hidden: [legacy_order_no]
overrides:
  detail: custom:components/CustomerDetail   # 手書きビューで上書き
```

初期値の推定根拠：アクセス頻度、基数、NULL率、列名、OMの種別・ドメイン。

#### OM → UI の既定対応

| OM | UI |
|---|---|
| kind: domain | 一覧 + 詳細 |
| kind: event | タイムライン / カレンダー |
| kind: relation | グラフビュー |
| kind: derived | チャート |
| domain: lat + lon | 地図 |
| domain: money | 通貨表記、合計 |
| domain: enum (status) | かんばん、フィルタ |
| domain: datetime | 期間絞り込み |
| domain: image / audio / document | プレビュー。確率的関数の出力は未確定（「不明」）を区別して表示 |
| relation: part-of | ツリー |
| relation: has 1:n | 詳細画面のタブ |
| relation: has n:1 | リンク + 検索つき選択 |
| relation: is-a | 親UIを継承し差分のみ定義 |
| context map | コンテキスト切替。対応する概念へのリンク |
| @query | 絞り込み・集計UI |
| @command | ボタン + 引数からのフォーム。認可がなければ非表示（`backend` の無い表にはそもそも出ない）。`approval: human` なら承認依頼として送信。`handoff` は完了判定まで「受付済」を表示 |
| watch（7.5） | 対象オブジェクトの通知タイムライン。人への通知設定と、オペレーターの反応（提案・承認依頼）を同じ列に表示 |

#### 承認と監査
- オペレーターの承認待ちコマンド（8.3）は、この画面に承認カードとして現れる。カードには主体、コマンド、対象行、引数、可逆性、バックエンドを表示する。判断材料がレプリカ経由なら `as_of` も出す
- 監査ログは対象オブジェクトの詳細画面から辿れる
- 人間UIが呼ぶクエリ・コマンドと、オペレーターが呼ぶものは**同じメソッド**。人とオペレーターは同じ面を見ている

### 9.5 DDDワークベンチ
開発者がメソッド（7.3）、コマンドアダプタ（7.4）、変換関数（11章）、メトリクス（10.4）、認可ポリシー（8.2）を書く場所。ここだけはgitが表に出る。

- **二つの選択肢**を残す。ブラウザ内のワークベンチ（組み込みのビルダー）と、手元のリポジトリをcloneしてローカルのエディタと市販のコーディングエージェントで作業する方法（外のビルダー）。どちらも同じファイルを触るので併用できる。外のビルダーへの縛りは 3.3
- **ビルダー**の仕事：OMからのメソッドの雛形生成、API仕様からのアダプタ提案、変換関数の評価セット生成、認可ポリシーの草案、既存BI集計からのメトリクス提案。出力はすべて `proposed`
- **ゼロコピーの帰結**：手元に試験用データは無い。クエリは本番ソースに対して読み取り専用で実行し、コマンドは `dry-run`（7.4）を既定とする。実送信は承認済みのPRをマージし、認可ポリシーに従った実行環境からのみ行う
- OMとそれに依存するコード（メソッド、アダプタ、関数、メトリクス、表示）の整合性をCIで検証し、結果をワークベンチに表示する

### 9.6 アナリシスUI
10.4 のセマンティックモデルから自動生成される、半自動の探索画面。

- ディメンション・尺度・階層（part-of / classified-as / is-a）がそのまま操作面になる。ドリルダウンは階層に沿う
- 自然言語 → `metric.query` → チャート。`grain` と `additivity` により、オペレーターが誤った集計を出せない
- 結果には来歴（メトリクス定義のIDとバージョン、粒度、フィルタ、「不明」値の扱い）を常に表示する
- **ループを閉じる**：分析中に見つけた新しい切り口（「地域×品目分類の粗利」）を、そのまま新メトリクスの `proposed` として 9.3 のレビューに戻す。分析が語彙を育てる
- 既存BIとの共存：書き出し先のセマンティックレイヤー（10.4.4）を既存BIが参照できるため、アナリシスUIは置き換えではなく探索の入口になる

### 9.7 横断の規則
- 全画面は同じOMファイル群を読む。OMのマージをイベントとして各画面に伝播し、画面側のキャッシュを無効化する
- OMレビュー（9.3）が滞ると全体が止まる。レビュー待ちの件数と影響の大きさを、データ管理者の入口に常に表示する
- 画面ごとの手書き上書き（9.4 の `overrides`、9.5 のコード）は、OMの変更で壊れた場合にCIで検出し、該当画面に警告を出す

---

## 10. DWH / ナレッジDBとしての利用

### 10.1 時間
- `valid_from` / `valid_to` ロールを持つ列があれば、関係と属性を時点指定で引ける（`history(at=...)`）
- event 表は追記のみとして扱う

### 10.2 推論（限定）
- is-a の継承、part-of・classified-as・is-a の推移閉包、逆関係の導出
- 実装は再帰CTEまたはDatalog。開世界推論・OWL完全推論は対象外

### 10.3 エクスポート（出口の確保）
- OM → RDFS/OWL（語彙はクラス・プロパティへ）。ドメインのスキーマ（LinkML、7.6 の②）の生成器を使う
- OM → R2RML（既存RDBを仮想的にRDFとして見せる）
- OM → グラフDB用のロード定義（ノード・エッジのマッピング）
- OM → セマンティックモデル（10.4）

### 10.4 セマンティックモデルの半自動生成

OM から、集計・統計のためのディメンショナルモデル（セマンティックモデル）を生成する。OM が「意味」であるのに対し、セマンティックモデルは「意味を数える方法」である。自前のクエリエンジンは持たず、既存のセマンティックレイヤー形式に書き出す（ゼロコピーの方針）。

#### 10.4.1 OM からの対応

| OM | セマンティックモデル |
|---|---|
| kind: domain（尺度なし） | ディメンション |
| kind: domain（尺度あり、`grain` 宣言） | 累積スナップショット型ファクト（注文のように状態を持つもの） |
| kind: event | トランザクション型ファクト |
| kind: relation | ブリッジ表 |
| kind: derived | 集計済み表、メトリクスの定義元 |
| has n:1（event → domain） | ファクトからディメンションへの結合 |
| part-of / classified-as / is-a | ドリルダウン階層（地域→支店、品目→品目分類） |
| valid_from / valid_to | 履歴ディメンション（SCD Type 2） |
| role: event_time | 日付ディメンションと時間粒度 |
| money / quantity 系ドメイン | 尺度（メジャー）候補 |
| context map | コンフォームドディメンション（コンテキストをまたいで同じ軸） |

#### 10.4.2 OM に追加する注釈

| 注釈 | 対象 | 意味 |
|---|---|---|
| `grain` | event 表、尺度を持つ domain 表 | 1行が何を表すか（注文、注文明細、日次残高）。欠けると二重集計の原因になるため必須（4.5） |
| `additivity` | 列ドメイン（語彙で定義） | `additive`（合計可）、`semi-additive`（時間方向は不可。残高など）、`non-additive`（単価、率） |
| `metric` | メトリクス定義ファイル | 名前、計算式、対象ファクト、既定フィルタ、「不明」値の扱い |

```yaml
# contexts/sales/metrics/revenue.yaml
id: met_01J...
name: 売上
fact: tbl_01HXY...                 # sales.orders（domain、grain: 1行=1注文）
measure: amount                    # domain: money, additivity: additive
aggregation: sum
filters:
  - "status != 'cancelled'"
unknown_handling: exclude          # exclude | separate_bucket
time_dimension: occurred_at
status: proposed
confidence: 0.85
evidence:
  - "BI ダッシュボード '月次売上' の SQL と一致"
  - "dbt モデル fct_revenue と同一のフィルタ"
  - "営業部 Excel では cancelled を含めている（定義の不一致あり）"
```

#### 10.4.3 半自動化の根拠
既存の SQL、BI ダッシュボード、dbt モデル、Excel の集計式を**メトリクス定義の証拠**として読む。同じ名前のメトリクスに複数の定義が見つかった場合は、統一案ではなく**不一致そのものを提案**として出し、レビューで決着させる。

#### 10.4.4 出力形式
第一候補はベンダー中立の **Apache Ossie（incubating、旧 Open Semantic Interchange）** の `.ossie.yaml`。そこから MetricFlow（Apache 2.0）、Cube、LookML、Malloy へ変換する。書き出し先は `sources/semantic_layer.yaml` で宣言する。実行はそのセマンティックレイヤーが行い、Mandaraは集計結果を保持しない。

#### 10.4.5 オペレーターによる集計・統計
- オペレーターに渡すツールは生 SQL ではなく、メトリクス単位の問い合わせ：`metric.query(metric, by=[...], filter=..., grain=...)`
- `grain` と `additivity` がモデルにあるため、ファンアウトや半加法尺度の誤集計を**型で**防ぐ
- 統計処理（前年比、季節調整、異常検知、検定）は、`metric.query` の結果を入力に取る `@query` メソッド（7.3）として `derived` 層に定義し、7章と同じ認可に従う。集計そのものはメソッドの中で行わない
- 結果には来歴を付ける：使用したメトリクス定義の ID とバージョン、粒度、フィルタ、「不明」値の扱い
- メトリクス定義・統計メソッドは OM と同じ PR で変更し、既存の BI 集計との差分を CI で検出する

---

## 11. 非構造データ（画像・音声・文書）と変換関数

メディアは「ソース」であると同時に「変換関数の生成対象」として扱う。目標は、雑に積まれた写真フォルダとDBのURLを渡すと、設備台帳・地図・点検履歴が半自動で立ち上がる状態。

### 11.1 OM上の扱い
- メディアファイルは `kind: domain` の表（資産）として現れ、列ドメイン `image` / `audio` / `document` を持つ
- メディアから導出された列・表は、関係 `derived-from` で導出元と**関数**を指す
- 変換関数はコードとしてリポジトリに置き、OM・メソッド・表示アノテーションと同じPRで変更する

```yaml
# contexts/facility/tables/s3_photos/equipment_photos.yaml（抜粋）
kind: domain
columns:
  file:
    domain: image
  lat: { domain: lat }
  lon: { domain: lon }
relations:
  - type: derived-from
    target: self
    columns: [lat, lon]
    function: fn_exif_geo            # functions/ 内の安定ID
    function_kind: deterministic
    status: approved
```

```yaml
# contexts/facility/functions/photo_to_equipment.yaml
id: fn_01J...
name: photo_to_equipment
kind: probabilistic
input:  { domain: image }
output:
  - { column: equipment_type, domain: equipment_type }   # enum
  - { column: equipment_id,   domain: equipment_id }     # 銘板OCR + 照合
model: { name: "...", version: "2026.09" }
rerun_policy: incremental        # full | incremental | frozen
threshold: 0.75
eval_set: evals/photo_to_equipment/
status: proposed
confidence: 0.81
evidence:
  - "写真 1,240 枚中 1,180 枚に銘板らしき矩形領域"
  - "equipment_master.id と OCR 結果の一致率 91%（サンプル 200）"
```

### 11.2 関数の二分類

| 種類 | 例 | 導出列の扱い | 承認の単位 |
|---|---|---|---|
| deterministic | EXIF→lat/lon、OCR、文字起こし | 通常の derived | 関数のコードとサンプル出力 |
| probabilistic | 画像分類、物体検出、話者・感情推定 | 行ごとに `confidence` と `model_version` を持つ | 評価セット＋サンプリングレビュー＋閾値 |

確率的関数の出力で閾値未満のものは、NULLではなく**「不明」**として区別する（開世界の扱いをここだけ持ち込む）。UI上は未確定として表示し、クエリで `unknown` を明示的に扱えるようにする。

### 11.3 AIが提案するもの（3段階）
1. **関数の仕様**（モデラー）：入力ドメイン、出力ドメイン、決定的／確率的、採用モデル、再実行方針
2. **関数のコードと評価セット**（ビルダー）：人はコードと評価結果（サンプル出力）を見て承認する
3. **新しい概念**（モデラー）：設備写真の集合から「設備」という domain 表を提案する等。構造化データからの推論より確信度が低くなるため、最も強いレビューを要する

### 11.4 再実行と履歴
- 導出列・導出表は3.2に従い、ソースDB内の別スキーマ（`Mandara_derived`）に置く。メディアがS3等にある場合の置き場は未決（15章）
- 導出列は必ず `function` と `model_version` を持つ。モデル更新で値が変わることを前提にする
- 関数ごとに `rerun_policy` を宣言する：`full`（全件再計算）、`incremental`（新規・変更分のみ）、`frozen`（承認時点の値を固定）
- 評価セットは回帰テストとしてCIで実行し、精度が閾値を下回る変更はマージできない

### 11.5 制約
- 確率的関数の出力表は `derived` 種別であり、コマンドを持たない
- 個人が写り得る画像・音声には `pii: true` を付け、推論入力・サンプル表示の対象から除外できるようにする
- 大量メディアのプロファイルは増分実行とし、全件走査は明示的な指示でのみ行う

---

## 12. 競合分析と位置づけ

### 12.1 比較

| 対象 | 得意なこと | Mandaraとの関係 |
|---|---|---|
| Palantir Foundry Ontology + AIP | オブジェクト・リンク・アクション。認可付きでAIが実行。Ontology MCP で型・アクション・関数をツールとして公開 | **正面競合**（ただし市場が違う） |
| データカタログ（Atlan / Collibra / DataHub / OpenMetadata） | 記述・系譜・用語集。AIによる説明文・用語の大量生成 | 共存。OMのエクスポート先・インポート元 |
| セマンティックレイヤー（MetricFlow / Cube / LookML / Malloy）、Apache Ossie | 指標の定義と集計の実行。Ossie は交換仕様 | 共存。Mandaraのセマンティックモデルの書き出し先・実行エンジン（10.4）。Ossie 準拠で製品非依存 |
| OBDA・オントロジーツール（Ontop / R2RML / Protégé） | 形式論理、RDBの仮想RDF化 | 共存・部品。エクスポート先 |
| グラフDB（Neo4j / Apache AGE / PuppyGraph） | 関係のたどり | 共存。ロード先 |
| 自動API・管理UI（Hasura / PostgREST / Django admin / Retool） | **型**からの画面・API生成 | 共存。描画層として利用可 |
| iPaaS・リバースETL（統合基盤、TROCCO 等） | 既存システムへの書き込み経路 | 共存。コマンドの `handoff` バックエンドの実装として利用可。RPA は使わない |
| DB直結MCP・text-to-SQL | AIに生SQLを渡す | **思想として競合** |

### 12.2 他に無い組み合わせ
1. 雑なデータ（構造化・メディア双方）から OM と変換関数を**半自動生成**する。根拠と確信度つき
2. OM がベンダーの箱ではなく **git 上のオープンなファイル**。「モデラーとビルダーは `proposed` しか書けない」というレビュー駆動が形式に埋め込まれている
3. **1つの語彙**から、プログラミングIF・UI・AIツール面・認可の4つの面を導出する
4. **ゼロコピー**。既存DBを正本として捨てず、データを自前で持たない。write-through で操作する

### 12.3 Foundry との違いと、FDE についての見解
Foundry はソースを自社ストレージに取り込み、オントロジーのオブジェクトはその上に載る。編集はまず Foundry 内に溜まり、基幹システムへ戻す（write-back）のは別途の統合作業になる。アクションのたびに「正本はどちらか」の調停が発生し、それを人（FDE）が担い続ける。これはコピーを持つ設計の構造的な帰結であり、プラットフォーム品質の問題ではない。Mandaraはデータを持たない（3章）ため、この調停そのものが存在しない。

また Foundry はオントロジーを**人が設計する前提**で、その工程も FDE が担う。公開情報で観察されるのは、Palantir 自身の技術的負債というより、顧客側のオントロジーが劣化する現象（任意項目の蓄積、ほぼ重複したオブジェクト型、回避策として生まれたアクション）である。FDE は生成工程が無いことの帰結であり、Mandaraはその工程の大半を AI 提案＋レビューで置き換える。

| | Foundry | Mandara |
|---|---|---|
| データ | 自社ストレージにコピー | 持たない（ゼロコピー） |
| 操作 | write-back（Foundry 内→基幹へ同期） | write-through（基幹の API・ストアド、または受入表経由で直接） |
| 業務ルール | オントロジー側に再実装 | 既存システム側のものを再利用 |
| オントロジー設計 | 人（FDE）が設計 | モデラーが提案、人が承認 |
| OM の置き場 | Ontology Manager（箱の中） | git（オープンなファイル） |
| 退出 | データ移行が必要 | OM ファイルが残るだけ |
| 緩衝 | コピーが事実上の緩衝 | 承認キューと可逆性フラグ |

系譜としては、OBDA（仮想RDF）や Hasura（DBを持たないAPI層）と同じ「持たない層」に、生成と実行面を足したものと位置づける。

ただし FDE が実際に担っている価値は設計だけではない。Mandaraは以下に答えを持つ必要がある。

| FDE の隠れた価値 | Mandaraの答え |
|---|---|
| データそのものを直す（汚いマスタ、壊れたキー） | 名寄せ提案（`identity.alternates`）、未宣言FKの検出。完全な代替ではなく部分的な肩代わり |
| 組織の合意を取る（どの「顧客」が正か） | コンテキストマップで「両方正しい」を許す。合意ではなく翻訳で解く |
| 現場知識をモデルに反映する | 根拠つき提案とレビューを、現場のドメイン担当者に**分散**する |

### 12.4 競合か共存か
- Foundry：正面競合だが、AIP は FDE とセットで提供される大企業向け。Mandaraの市場は「既存DBを捨てられず、FDE を雇えない」中堅企業・SI 文脈
- カタログ／セマンティックレイヤー／グラフDB／OBDA：Mandaraは上流（生成）と下流（実行）を担い、中間の保管・系譜は彼らに委ねる。**OM のインポート／エクスポートを早期に用意する**ことが連携上の要
- オントロジーの劣化はMandaraでも起きる。語彙の閉鎖、重複ドメイン検出、`rejected` の記録が対策であり、設計上の前提として扱う

### 12.5 リスク
- カタログ勢がアクション面（メソッド・認可）に下りてくる
- Foundry が「生成の入口」を安価に開放する
- 守りは 12.2 の 2（オープン形式＋レビュー駆動の作法）。大手が最も真似しにくい部分

---

## 13. 非機能

| 項目 | 方針 |
|---|---|
| セキュリティ | サンプル行は匿名化ルールを通す。個人情報列は `pii: true` を付け、モデラーの推論入力から除外可能にする |
| 秘密情報 | リポジトリには置かない。既存システムの資格情報はMandaraのサービスアカウントとして保持し、モデラー・ビルダー・オペレーターのどれにも渡さない |
| 信頼性 | コマンドは冪等キー付きで記録し、アウトボックス方式で再試行する。`handoff` バックエンドは完了判定まで「実行中」として扱う |
| 再現性 | 推論実行ごとに入力ハッシュ・モデル・プロンプトバージョンを `runs/` に記録 |
| 規模 | 表数万・列数十万を想定。プロファイルは増分実行 |
| 性能 | 深い関係のたどりはグラフDBへのエクスポートで逃がす |

---

## 14. 段階計画

### フェーズ0：MVP
- ソース：PostgreSQL 1つ + ファイル1フォルダ（CSV/Markdown）
- OM：表種別、列ドメイン、is-a / part-of / classified-as / has（基数つき）、grain。OMのメタモデル（7.6 の①）を LinkML で定義する
- パイプライン：接続→プロファイル→推論→PR作成→バリデーション
- 主張台帳：スキーマ制約とSQLパーサ（JOIN／CASE）の2抽出器。関係検出はA群とアンカー結合
- UI：データソース指定（9.2）と、質問形式のOMレビュー（9.3）。この2画面が無いとレビューが回らないため、MVPに含める
- 語彙は閉じる。コンテキストは1つ
- 差し込み口（16章）を最初から切り、最小の4パック（16.6）で動かす

### フェーズ1：実行可能化
- 承認済みOMからドメインのスキーマ（LinkML、7.6 の②）を出力し、型を生成する
- 汎用のグラフ問い合わせとデフォルトメソッド（7.1、7.2）。ユーザーのコードは要らない
- OpenAPI 1つを読み、コマンドアダプタを提案。`call` バックエンドのコマンド実行、冪等キー、アウトボックス
- 認可ポリシーと監査ログ
- AIツール面（MCP互換）
- ストアドプロシージャ（`call`）と `handoff` は順次。`handoff` の最初の実装は受入表＋ポーリング

### フェーズ2：面の提供
- オントロジーUI（9.4）：表示アノテーション、既定パターン、承認カード
- ユーザー定義メソッド（7.3、参照透過な関数）。メソッドの言語はここで決める
- DDDワークベンチ（9.5）：ブラウザ内とローカルの両方、`dry-run`
- 監視と通知（7.5）：transition / arrival から。anomaly はフェーズ3のメトリクス後
- 複数コンテキストとコンテキストマップ

### フェーズ3：DWH/ナレッジDB
- 時間（有効期間）
- 限定推論
- OWL/R2RML/グラフDBエクスポート。カタログとのインポート／エクスポート
- セマンティックモデル生成（grain / additivity / metric）と dbt Semantic Layer への書き出し
- メトリクス単位の AI ツール（`metric.query`）
- アナリシスUI（9.6）
- 読み取りレプリカ（`read_from`）。まず `snapshot`、CDC は後から

### フェーズ4：非構造データ
- メディア表と `derived-from`
- 決定的関数（EXIF、OCR、文字起こし）
- 確率的関数と評価セット、サンプリングレビュー
- メディアからの新概念提案

---

## 15. 未決事項

### 15.1 深い設計上の未解決問題
小手先の規則で埋めず、設計思想として決着させる必要があるもの。

- **コマンドの認可の単位**（7.3、8.2）：認可は「主体 × メソッド」で定義しているが、実際に実行されるのはメソッドが返す `Command` の値（どのアダプタを、どの行に、どの引数で）である。あるメソッドが別のアダプタを指す `Command` を返せるなら、メソッドへの許可が、実際に実行されるアダプタへの許可にすり替わる。認可と承認を、メソッドに対して行うのか、返された `Command` の値に対して行うのか、両方なのか。メソッド名とアダプタ名（`Order.ship`）の関係、1つのメソッドが複数の `Command` を返す場合（サガ）も含めて決める
- **判断した時点と実行する時点のずれ**（7.3、8.3、3.1）：メソッドの前提条件はスナップショットの `as_of` 時点で評価されるが、実行は承認キューを通った後（数時間後もありうる）。実行直前に最新のソースで再評価するのか、検証は既存システムに任せるのか。承認者が見た判断材料と、実際に送信される内容がずれることをどう扱うか。コマンドを返すメソッドの `reads` がレプリカ（3.1 第3段）を経由してよいか、部分グラフが複数ソースにまたがるときの `as_of` の一貫性も含めて決める

### 15.2 その他
- 安定IDの採番方式（ULID想定）と、ソース側で表が分割・統合された場合の追跡。モデラー v0 は、存在の場所（ソース・スキーマ・表名、制約の場所）のハッシュで ID を作る。再実行しても同じ ID になるが、改名すると変わる（4.2 の「改名しても変わらない」を満たさない）。既存の OM との突き合わせによる追跡は再実行（6.5）で扱う
- 再生成できない主張（レビューの回答、LLM による抽出）をどこに、どう残すか（git に乗せるか、OM の注釈に畳むか）
- `classified-as` の UI 表現（分類のツリー、ファセットでの絞り込みなど）。UI に着手するときに決める。TMC 側は汎用のグラフ問い合わせ（7.1）でたどる
- 語彙のドメイン型（`geo.latitude` など）と固定の単位は、①の enum として閉じた集合にした。初期セットをどこまで標準で持つか（通貨を ISO 4217 から全部入れるか、使うものだけ足すか）
- ドメインのスキーマ（7.6 の②）と生成した型をリポジトリにコミットするか、ビルド時に生成するか
- メソッド（7.3）の副作用禁止を静的に検査する手段（scalafix の規則、capture checking など）
- 部分グラフの宣言（`reads`）の記法。GraphQL の fragment 風にするか、LinkML のパス式に寄せるか。件数の上限の既定値
- 外のビルダー（3.3）のコミットをどう識別するか（専用アカウント、署名、コミットの付記）。識別できない場合に `status: approved` の書き込みをどう防ぐか
- 外のビルダーに文脈を渡す規約ファイルの形式と、Mandaraのツール面をビルダーに開く範囲
- モデラーとビルダーが同じ宣言に別の提案を出したときの扱い
- `Command` の値の表現（言語をまたいで同じ形にするか）と、前提条件エラーの型
- 状態遷移（受注→出荷→取消）と不変条件をOMの注釈として持つか。持てば、状態ごとの型と検証コードをTMCに生成できる
- 変換関数（11章）の実装言語。Pythonのままにするか、隔離実行のためにRust／WASMへ寄せるか
- OMレビューUIの「質問」への変換ルール。提案の種類ごとに問いの型をどこまで標準化するか
- 一括承認の閾値（既定 0.95）の妥当性と、一括承認を後から取り消す手順
- 「同じ意味」判定のレビュー指針（人によるぶれを抑える手引き）
- 主張台帳の `predicate` の初期セット。否定形をどこまで用意するか
- 根拠づけ（6.6.4）の精度目標と、紐付かない主張の扱い（捨てるか、保留として表示するか）
- クエリログの取得に必要な権限と保持期間。ログに含まれる個人情報の扱い
- オペレーターの承認待ちコマンドのタイムアウトと、期限切れ時の扱い
- 確率的関数の「サンプリングレビュー」の最小サンプル数と、承認が無効になる精度低下の基準
- 正解隠蔽テスト（17.2）の合格基準（復元率・誤提案率の下限／上限）と、データセットごとの重み
- 「不明」値のクエリ意味論。メトリクス定義ごとの `unknown_handling` で足りるか、全体既定も要るか
- 複数コンテキストにまたがるメトリクス（全社売上）の定義をどのコンテキストに置くか
- 統計メソッドの結果（検定の p 値など）を導出表として保存するか、都度計算にとどめるか
- モデル更新時に `frozen` の導出列をいつ解凍するか
- カタログとの往復で、どちらの変更を優先するか（OMが常に正か、双方向か）
- 実体化スキーマ（`Mandara_derived`）を置けないソース（読み取り専用DB、SaaS、S3上のメディア）の扱い
- サガの補償手順をどこまで自動生成するか
- read-your-writes の保持時間と、レプリカ同期の完了をどう検知するか
- watch の予算を超えたときの既定動作（溜める／捨てる／要約して1件にする）
- 起こされたオペレーターのセッションをどこまで保持するか（同一起点の連続イベントで文脈を引き継ぐか）
- `handoff` の完了判定の種類（`poll` / `file` / `webhook`）の初期セットと、完了判定のタイムアウト
- `handoff` の却下行の形式が既存システムごとに違う。却下理由をどう正規化して承認者に返すか
- 受入表のレイアウト定義（固定長・CSV・連携用テーブル）からアダプタの `map` をどこまで自動提案するか

---

## 16. 拡張アーキテクチャ

Mandaraのコアは小さく保ち、種別ごとの知識・型・画面・接続はプラグインとして差す。差し込み口は8つで、どれも既存の概念の一般化である。これにより小さな実装から始められる。

### 16.1 差し込み口

| 口 | 何を差すか | 既存の章 |
|---|---|---|
| ソース接続 | RDB種別、S3、SaaS API | 6.1 |
| 抽出器 | 入力種別 → 主張台帳 | 6.6.3 |
| 型と変換関数 | 列ドメイン型＋決定的／確率的関数＋プロファイル＋プレビュー | 4.1、11 |
| ビュー | OMのパターン → UI部品、およびその合成 | 9.4 |
| コマンドバックエンド | `call` / `handoff` の製品別実装（SAP RFC・BAPI、Salesforce、kintone、受入表の規約） | 7.4 |
| エクスポータ | OM → Ossie / OWL / R2RML / グラフ / カタログ | 10.3、10.4.4、12 |
| 型生成 | ドメインのスキーマ（LinkML）→ TMCの型。言語ごと | 7.6 |
| 知識パック | 語彙の種、`aliases`、公開コード表、関係の事前分布、メトリクス雛形 | 6.6.1 |

### 16.2 3つの統一
1. **メディア種別は「変換関数つきの列ドメイン型」**。画像・音声を特別扱いせず、`type` が変換関数を持てると一般化する。JSON列→派生列、WKT→緯度経度、XML帳票→項目、センサーログ→統計量も同じ仕組みに乗る。11章は型プラグインの一例
2. **ドメインUIは「ビューの合成パターン」**。地図・タイムライン・かんばんが原子的なビューで、「設備＝地図＋写真ギャラリー＋点検タイムライン」は合成。ドメインパックが持つのは合成の定義と表示の既定値であり、新しい描画部品はほぼ要らない
3. **プラグインの寄与は、すべて同じレビュー経路を通る**。知識パックが持ち込む語彙も関係の事前分布も `proposed` で入る。プラグインをAIより信用しない

### 16.3 パック（束ね）
配布単位は単体プラグインではなくパック。1つのマニフェストで複数の口に寄与する。

```yaml
# packs/facility-maintenance/pack.yaml
name: facility-maintenance
version: 0.3.0
requires_core: ">=0.2"
license: Apache-2.0
contributes:
  vocabulary:   vocabulary.yaml          # 列ドメイン、aliases、関係の事前分布
  codelists:    [codelists/*.yaml]
  types:        [types/image.yaml]
  functions:    [functions/*.yaml]
  views:        [views/equipment.yaml]   # 合成：map + gallery + inspection_timeline
  metrics:      [metrics/*.yaml]         # 雛形
  methods:      [methods/*]              # 雛形（TMC、7.3）
  backends:     []
  exporters:    []
```

- **ドメインパック**＝知識＋型＋ビュー合成＋メトリクス雛形＋メソッド雛形
- **製品パック**＝ソース接続＋スキーマ辞書＋バックエンド（SAP、Salesforce、kintone、freee）
- **規格パック**＝公開コード表＋公開オントロジー（JIS、ISO、GS1、FHIR、FIBO）

### 16.4 候補（初期に価値が高い順）
- 型：画像、文書スキャン（OCR）、音声、地理空間（GeoTIFF / Shapefile）、センサー時系列、動画
- 製品：kintone、Salesforce、SAP、freee／弥生、電子帳票
- 規格：JIS X 0401／市区町村コード、法人番号、ISO 3166／4217、GS1、FHIR
- ドメイン：設備保全、物流（配送・車両・位置）、小売（商品・在庫・店舗）、不動産（物件・契約・図面）、自治体（施設・住民サービス）
- ビュー：ギャラリー、音声プレーヤー、BOMツリー、フロアプラン、カレンダー
- エクスポータ：Ossie、OWL、R2RML、Neo4j、OpenMetadata

### 16.5 設計規則
- 寄与は**データ優先**（YAML）。コードは関数・ビュー・バックエンドに限る
- 関数は隔離されたワーカーで実行し、ビューはモジュール分離（iframe／module federation）。プラグインのコードはコアと同じプロセスに入れない
- `evidence` にパック名と版を残す。パックを外したとき、何が消えるか追える
- 同じ `alias` を2つのパックが主張したら、語彙PRの衝突としてレビュー（9.3）に出す
- パックの有効化はソースまたはコンテキスト単位（9.2 で申告）

### 16.6 最小構成
コア＝OM・語彙・主張台帳・レビュー・バリデーション・レビューUI。最初のパックは「PostgreSQL接続」「SQLパーサ抽出器」「REST `call` バックエンド」「一覧＋詳細ビュー」の4つで、フェーズ0〜1（14章）はこれで成立する。

---

## 17. テスト方法

Mandaraの中核は推論の品質なので、通常のソフトウェアテストに加えて**推論の評価**を仕組みとして持つ。原則は「正解を隠して推論させ、復元率と誤提案率を測る」。評価はCIで回し、6.6.5 の既定確信度の校正にも使う。

### 17.1 評価の層

| 層 | 対象 | 方法 |
|---|---|---|
| 単体 | 抽出器、バリデーション、アダプタの写像、エクスポータ | 通常のユニットテスト。抽出器は入力→主張の固定テスト |
| 推論 | 関係検出、列ドメイン、表種別、粒度、メトリクス定義 | **正解隠蔽**（17.2） |
| 根拠づけ | 文書の言及 → スキーマ要素 | 人手の正解セットに対する一致率 |
| 変換関数 | 決定的／確率的関数 | 評価セット（11.4）。確率的関数は精度閾値をマージ条件に |
| 統合 | 接続→プロファイル→推論→PR→バリデーション | 小さなデータセットでの通し実行。再実行の冪等性（6.5） |
| 安全 | 認可、`dry-run`、キャッシュの認可スコープ | 主体を変えて同じクエリを投げ、他人の行が出ないことを確認（3.1 第2段） |

### 17.2 正解隠蔽テスト
1. 正解を持つデータセット（17.4）から、FK制約・ORMの関連・文書の注記を**取り除いた**コピーを作る
2. Mandaraに推論させ、`proposed` を集める
3. 隠した正解と突き合わせる

指標：
- **復元率**：隠した関係・ドメインのうち、提案に含まれた割合
- **誤提案率**：提案のうち、正解に無かった割合
- **確信度の校正**：確信度帯（0.6–0.7、0.7–0.8、…）ごとの的中率。帯の数字と的中率がずれていれば 6.6.5 の既定を直す
- **順位**：正解が提案の何番目に出たか（9.3 の影響順の妥当性）
- **低カーディナリティ別**：基数 ≤ 20 の列だけで同じ指標を出す。ここが弱点になりやすい（6.7）

証拠の層ごとにも測る：スキーマ制約だけ、＋コード、＋文書、＋世界知識、と段階投入（6.6.6）の各段で復元率がどう上がるかを記録する。上がらない層は入れる価値がない。

### 17.3 レビュー品質のテスト
- **問いの形式**（9.3）：同じ提案を複数人がレビューし、回答の一致率（κ係数）を測る。一致率が低い問いの型は言い換える
- **レビュー時間**：1問あたりの所要時間。束で承認できる割合
- **後戻り**：承認後に却下へ変わった件数。一括承認の閾値（0.95）の妥当性を見る

### 17.4 評価用データセット

| 目的 | データセット | 使い方 |
|---|---|---|
| 関係検出（正解つき） | Sakila / Pagila、Chinook、Northwind、MySQL Employees | FKを消して復元。最初の動作確認 |
| 関係検出（中規模） | AdventureWorks | 表70超、文書あり。本命 |
| 未宣言FK・低カーディナリティ | Stack Exchange データダンプ | FK宣言なし、本文つき |
| セマンティックモデル | TPC-H / TPC-DS、TheLook eCommerce、dbt jaffle_shop | 星型スキーマで grain・加法性・メトリクスの正解 |
| コードからの主張抽出 | Odoo、ERPNext、Dolibarr、Apache OFBiz、GitLab、Redmine | ORMの関連定義とマイグレーション履歴が正解 |
| 文書を証拠にする推論 | Spider 2.0（Lite: BigQuery / SQLite、DBT: DuckDB）、BIRD（evidence 注記つき） | 根拠づけと文書証拠の評価 |
| 日本語・規格パック | デジタル庁 自治体標準オープンデータセット、法人番号公表サイト、e-Stat、国土数値情報 | 公開コード表の照合、名寄せ、地図 |
| メディア・変換関数 | Mapillary（EXIF）、COCO / Open Images、Common Voice（日本語）、ReazonSpeech、FUNSD / SROIE / DocLayNet、NDL OCR学習データ | 型プラグインと評価セット |
| 暗黙知の抽出 | Enron メール | メモ層のLLM抽出、`pii` の扱い |
| 医療・規格 | Synthea（FHIR） | 規格パックの往復 |

### 17.5 運用
- 評価データセットごとに `evals/datasets/<name>/` に正解と隠蔽スクリプトを置く。本体は外部から取得し、リポジトリに入れない（ライセンスと容量）
- 推論モデルやプロンプトを変えるPRは、17.2 の指標の差分をCIで表示する。復元率の低下、誤提案率の上昇はマージを止める
- パック（16章）は、対応するデータセットでの評価結果を同梱する

---

## 18. 先行研究

OMの生成、リレーション検出、ドメイン検出について、論文・定番手法・特許（Palantir中心）を調べた結果と、本仕様との対応をまとめる。調査は2026-10時点。

### 18.1 結論
- 3つの技術はどれも個別には公知である。定石は「統計で候補を絞る → LLMで意味を判定する → スキーマ全体で整合させる」に収束しており、6.7.2 の B・C・D はこの定石と一致する
- 先行研究が手薄なのは、既存コードを最優先の証拠にすること（6.7.2 A）、アンカー結合による行単位の一致（6.7.2 B）、主張台帳（6.6）である。本仕様の技術的な独自性はここにある
- 全体としての新しさは検出手法ではなく、12.2 の組み合わせ（閉じた語彙、根拠つき提案、git上のレビュー）にある。検出アルゴリズム単体での新規性の主張は難しいが、公知技術が厚いぶん実装の自由度は高い

### 18.2 取り込む手法

効果の大きい順に4つ。

| # | 手法 | 効果 | 本仕様との対応 |
|---|---|---|---|
| 1 | 統計で絞り、LLMが判定する二段構え（HoPF、LLM-FK、Tursio） | 関係検出の骨格。包含率や一意性で枝刈りしてからLLMに渡すので、候補空間が2〜3桁減り、コストと幻覚を抑えられる。統計値がそのまま `evidence` になる | 6.7.2 の B → C。設計済み |
| 2 | ArcheType方式のドメイン検出 | 学習データなしで、LLMの出力を許可されたラベル集合に写し直す。閉じた語彙と同じ構造で、語彙を増やしても再学習が要らない | 6.3 の列ドメイン提案。**再マッピングの工程が未記載** |
| 3 | Auto-BI型の全体整合 | 列ペアを個別に判定すると矛盾する提案が残る。スキーマグラフ全体で最適化して誤提案を減らし、レビュー件数を減らす | 6.7.2 の D。**定式化が未記載** |
| 4 | RIGOR型の生成パイプライン | 表ごとに来歴つきの差分を生成し、Judge-LLMが精査してから統合する。人に届く提案の質が上がる | 6.3、6.4。表1つ1ファイルの構造と合う。**Judgeの段が未記載** |

優先は1と2。3と4は提案の量が増えた段階で足せば間に合う。

### 18.3 リレーション検出
- **古典**：包含従属性（IND）を列挙し、外部キー候補を絞る。難所は偽のINDが大量に出ること。6.7 冒頭の「包含関係だけでは破綻する」と同じ認識
- **HoPF（2019）**：一意列組（UCC）とINDから、スコア関数で主キーと外部キーを同時に抽出する。主キーを平均88%、外部キーを91%回収
- **Auto-BI（VLDB 2023）**：列ペアの局所予測と、スキーマグラフ全体の最適化（k-Min-Cost-Arborescence）を組み合わせる。10万件超の実BIモデルとTPCベンチマークでF1は0.9超
- **LLM-FK（2026）**：Profiler、Interpreter、Refiner、Verifierの4エージェント。一意キーを起点に候補を分解して候補空間を2〜3桁削減し、最後にスキーマ全体の矛盾を解消する。F1は93%超
- **Tursio（2026）**：統計で主キー候補とINDを枝刈りし、閾値で絞った残りをLLMに判定させる

### 18.4 ドメイン検出
- **Sherlock／Sato／Doduo**：学習型の意味型検出。Sherlockは約67万列で78の意味型を学習した。型が学習時に固定され、型ごとに学習データが要るため、組織ごとに語彙が違う本仕様には合わない
- **ArcheType（VLDB 2024）**：列のサンプリング、プロンプト化、LLMへの問い合わせ、ラベルの再マッピングの4段で、ゼロショットで判定する
- **数値列の弱点**：既存モデルが安定するのはテキスト列で、数値列では精度が落ちる（Langenecker 2024）。金額・数量・加法性（10.4）の判定がここに当たる。プロファイル統計と主張台帳の証拠をLLMに一緒に渡して補う
- **スキーママッチング**：COMA、Cupid、Similarity Floodingが古典。Magneto（2024）は小型モデルで候補を検索し、LLMで再ランクする。ソースをまたぐ同一ドメインの検出に使える

### 18.5 OM生成
- **BootOX（ISWC 2015）**：RDBからオントロジーとR2RMLマッピングを自動抽出する。表→クラス、外部キー→オブジェクトプロパティという機械的な対応で、意味づけは浅い
- **RIGOR（2025）**：スキーマと文書、既存のドメインオントロジー、成長中のコアオントロジーをRAGで引き、外部キーをたどって表ごとに来歴つきの差分を生成する。Judge-LLMまたは人が精査してから統合する。6.6.1 の3層と構造が近い
- **Snowflake Semantic View Autopilot（2026-02 GA）**：既存のクエリ履歴とBI資産からセマンティックビューを自動生成する。10.4.3 と 6.7.1 の「まずコードを掘る」と同じ発想

### 18.6 特許

Palantirを中心に確認した範囲。網羅調査ではない。

| 特許 | 内容 | 本仕様との関係 |
|---|---|---|
| US7962495 ほか（Palantir、動的オントロジー） | ユーザー入力でオブジェクト型とプロパティ型を定義し、パーサ定義で入力データを変換する。ファミリーは US12386803 まで続く | 人が定義する前提で、AIによる生成は対象外 |
| US11176116（Palantir、typeclass） | 学習済みモデルで列の型クラスを判定して注釈する。値と形式の対応確率が閾値を超えた別の列にも適用し、過去の結合頻度をもとに結合する | **最も近い。**列ドメイン検出（6.3）から同一ドメイン・関係の提案（6.7）への流れに重なる。クレームは結合履歴の頻度を要件に含む |
| US10942947（Palantir） | データセット間の関係尺度から結合キーを算出し、結合を提案する | 6.7.2 B に近い |
| US12299022（Palantir、2025年登録） | LLMでテキストからトリプルを抽出して型分類し、オントロジーと類似検索で照合する。不一致分はUIで処理してオントロジーを更新する | 6.6.3 のLLM抽出、11.3、9.3 に近い。クレームは2段のプロンプトによるトリプル抽出を要件に含む |
| US12405983（Palantir、2025年登録） | 自然言語の問い合わせからLLMがクエリを生成し、オントロジーを更新する | 8章のAI実行面に近い |
| US11138202（Salesforce） | 独立性の検定で結合候補のデータセットを順位づけする | 6.7.2 B に近い |

### 18.7 本仕様への反映（提案）

以下は提案であり、該当の章はまだ書き換えていない。

- **6.3**：列ドメインの提案に再マッピングの工程を足す。LLMの出力が語彙に無いとき、近い語彙値に写すか、語彙への追加提案に回すかを決める規則を置く
- **6.7.2 D**：大域整合の定式化として、Auto-BIのグラフ最適化とLLM-FKのVerifierを出発点にする
- **6.3〜6.4**：提案をレビューに出す前にJudgeの段を置く。9.7 のレビュー滞留への対策になる
- **12.3**：「Foundryは人が設計する前提」という対比は古くなりつつある。自然言語からオントロジー構築まで行うAI FDEが出ている（二次情報）。12.5 の「生成の入口の開放」が現実になりつつあるので、差別化の軸をゼロコピー、write-through、オープン形式、レビュー駆動に寄せる
- **12.5**：特許リスクを追加する。US11176116 と US12299022 は専門家の確認を先に行う
- **14章 フェーズ0**：関係検出は「A群とアンカー結合」のままでよい。列ドメインはArcheType方式で始める
- **17.2**：「低カーディナリティ別」と並べて「数値列別」の指標を足す
- **17.4**：LLM-FKが評価に使うMusicBrainzを、大規模な関係検出の候補に加える

### 18.8 調査の限界
- 特許は網羅調査ではない。日本出願と、公開前の出願は確認していない
- 特許の記述は公開情報の要約であり、侵害の有無についての法的判断ではない
- 論文の精度は各論文のベンチマーク上の値で、実務のRDBでの再現は未検証。17.2 で自前で測る

### 18.9 参考文献
1. Jiang, Naumann. Holistic Primary Key and Foreign Key Detection. JIIS, 2019. <https://doi.org/10.1007/s10844-019-00562-z>
2. Lin, He, Chaudhuri. Auto-BI: Automatically Build BI-Models Leveraging Local Join Prediction and Global Schema Graph. VLDB 2023. <https://arxiv.org/abs/2306.12515>
3. Tang et al. LLM-FK: Multi-Agent LLM Reasoning for Foreign Key Detection in Large-Scale Complex Databases. 2026. <https://arxiv.org/abs/2603.07278>
4. Scalable Join Inference for Large Context Graphs（Tursio）. 2026. <https://arxiv.org/abs/2603.04176>
5. Feuer et al. ArcheType: A Novel Framework for Open-Source Column Type Annotation using Large Language Models. VLDB 2024. <https://arxiv.org/abs/2310.18208>
6. Langenecker. 博士論文（エンタープライズ・データレイクでの意味型検出）. TU Darmstadt, 2024. <https://tuprints.ulb.tu-darmstadt.de/id/eprint/27469>
7. Liu et al. Magneto: Combining Small and Large Language Models for Schema Matching. 2024. <https://arxiv.org/abs/2412.08194>
8. Jiménez-Ruiz et al. BootOX: Practical Mapping of RDBs to OWL 2. ISWC 2015. <https://www.cs.ox.ac.uk/publications/publication9666-abstract.html>
9. Nayyeri et al. Retrieval-Augmented Generation of Ontologies from Relational Databases（RIGOR）. 2025. <https://arxiv.org/abs/2506.01232>
10. Snowflake. Semantic View Autopilot. 2026. <https://www.snowflake.com/en/blog/semantic-view-autopilot/>
11. US7962495. Creating data in a data store using a dynamic ontology. <https://patents.google.com/patent/US7962495>
12. US11176116. Systems and methods for annotating datasets. <https://patents.google.com/patent/US11176116>
13. US10942947. Systems and methods for determining relationships between datasets. <https://patents.google.com/patent/US10942947>
14. US12299022. Language model-based data object extraction and visualization. <https://patents.google.com/patent/US12299022B2>
15. US12405983. Interacting with ontology-based databases using machine learning. <https://patentsgazette.uspto.gov/week35/OG/html/1538-1/US12405983-20250902.html>
16. US11138202. Techniques for determining and presenting dataset join candidates. <https://patents.google.com/patent/US11138202>
17. AI FDEに関する二次情報. <https://dev.to/s3atoshi_leading_ai/palantirs-secret-weapon-isnt-ai-its-ontology-heres-why-engineers-should-care-kk8>

---

## 付録A. 候補OSS（2026-10時点）

| 用途 | 候補 | 注意 |
|---|---|---|
| プロファイル・フェデレーション | DuckDB、Trino | |
| 従属性発見（IND / FD / UCC） | Desbordante、Metanome | Desbordante は AGPL-3.0。別プロセスで呼ぶ |
| SQL・定義の抽出 | sqlglot、sqllineage、lkml、dbt-core の manifest、openpyxl、tree-sitter | |
| 文書の構造化 | Docling、MarkItDown、Apache Tika | |
| 日本語処理・根拠づけ | GiNZA、SudachiPy、sentence-transformers（multilingual-e5 / ruri）、pgvector | |
| OM の表現・検証 | ruamel.yaml（キー順・コメント保持）、LinkML、pydantic | |
| オントロジー出力 | rdflib、pySHACL、Owlready2、Ontop / Morph-KGC（R2RML） | |
| 推論 | Logica（Datalog→SQL）、Soufflé | |
| グラフ出力先 | Apache AGE、Neo4j Community（GPLv3） | Kùzu は 2025-10 にアーカイブ。後継フォークは RyuGraph |
| セマンティックモデル | Apache Ossie（交換仕様）、MetricFlow（Apache 2.0）、Cube、Malloy、Lightdash（Explore の参考） | |
| 実行・サガ・承認待ち | Temporal | 7.4 のアウトボックスと 8.3 の承認キューを担う本命 |
| 認可 | Open Policy Agent または Cedar | |
| API アダプタ・受入表連携 | openapi-core、zeep、TROCCO（リバースETL） | Playwright（RPA）は使わない |
| ツール公開 | MCP Python SDK、FastMCP | |
| UI | Refine / React-Admin、TanStack Table、MapLibre GL、Cytoscape.js / React Flow、vis-timeline、dnd-kit、Monaco / code-server | |
| 変換関数 | PaddleOCR / Tesseract、faster-whisper / ReazonSpeech、Pillow / exifread、OWLv2 / SigLIP | YomiToku は非商用条件、YOLO は AGPL |
| カタログ連携 | OpenMetadata、DataHub、OpenLineage | |
| 監査・トレース | OpenTelemetry | |

本命5つ：DuckDB、sqlglot、Temporal、MetricFlow＋Ossie、Refine。

---

## 付録B. 変更履歴

| 版 | 日付 | 変更 |
|---|---|---|
| v0.4 | 2026-10-14 | TMC を「自動生成した型＋参照透過な関数」に変更。メソッドは読む部分グラフを宣言して不変のスナップショットを受け取り、コマンドは `Command` の値を返す。集計はメソッドに書かず `metric.query` に押し下げる。スキーマは LinkML で2段（OMのメタモデル／ドメインのスキーマ）に持つ。言語は固定せず、手法への適合で選ぶ（OMの生成と検証は Python、メソッドは Scala 3）。副作用の禁止は型・静的検査・隔離実行の3段で保証する。列ドメインに `role: technical`（行の技術ID、監査列）を追加。コマンドの認可単位と、判断と実行の時点のずれを、深い未解決問題（15.1）として記録。ユーザー定義メソッドをフェーズ2へ移動。表メタデータの形を変更（主キー・代替キー・via を列の配列に、関係に安定 ID と role、量と単位の分離、is-a の realized_by、enum の値ごとのレビュー）。ドメインの type と固定の単位を閉じた集合にし、値の物理的な表現（encoding）を意味の型から分離。関係型に `classified-as`（分類への所属）を追加。中間表が辺（edge）を宣言できるようにした。主キー・代替キーの列は NULL を持たない。関連：2章、3.3、4.1、4.2、5章、7.1〜7.3、7.6、10.3、10.4.5、14章、15章、16章 |
| v0.3 | 2026-10-12 | コマンドバックエンドを `call`（同期呼び出し）と `handoff`（非同期の受入表）の2種に限定。`procedure` は `call` に統合、`batch` は `handoff` に置換、`sql` と `ui` は廃止。受入経路の無い表は読み取り専用とし、コマンド・UIボタン・AIツールを生成しない。関連：1章、2章、3章、4.1、4.2、4.5、6.1、7.2〜7.4、8.2、9.4、12.1、12.3、13章、14章、15章、16章、付録A |
| v0.2 | 2026-10-11 | 競合分析・先行研究・テスト方法・拡張アーキテクチャを追加 |
