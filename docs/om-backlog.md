# OM（オントロジカルメタデータ）タスク洗い出し

仕様書 v0.4 を元に、AdventureWorks（AW）の実物を見ながら洗い出したもの。未整理・未優先。`★` は AW を見て見つかった、仕様にまだ書かれていない論点。

---

## 0. OM が表現するもの（棚卸し）

| 対象 | 中身 | 仕様 |
|---|---|---|
| 語彙 | 表種別、関係型（推移・逆）、has の細分、基数、列ドメイン、加法性、関数種別、表記揺れ、バックエンド種別 | 4.1 |
| 表 | 安定ID、名前、コンテキスト、種別、粒度、同一性（主キー・代替キー）、読み経路、バックエンド | 4.2 |
| 列 | ドメイン、PII | 4.2、13 |
| 関係 | 型、has の細分、対象、経由列、基数、時間 | 4.2 |
| コンテキスト | 境界づけられたコンテキスト、コンテキストマップ（shared-kernel / translation / anti-corruption） | 4.3 |
| 注釈の共通フィールド | status、confidence、evidence、inferred_by、reviewed_by/at、note | 4.4 |
| 不変条件 | 4.5 の検証規則 | 4.5 |
| 主張台帳 | subject / predicate / object / source / valid_at / confidence | 6.6 |
| 導出 | derived-from、変換関数、model_version | 11 |
| 集計の意味 | grain、additivity、メトリクス | 10.4 |
| 時間 | valid_from / valid_to、event_time | 10.1 |
| 後のフェーズ | コマンドアダプタ、watch、表示アノテーション、認可ポリシー | 7.4、7.5、9.4、8.2 |

---

## A. メタモデル①（LinkML）
2026-10-14 着手：`src/mandara/om/schema/om.yaml`。pydantic の型は `scripts/gen_om_model.sh` で生成する（`src/mandara/om/model.py`、生成物）。
- 構造的な語彙（表種別、関係型、has の細分、基数、加法性、ドメインの役割、レビュー状態）は①の enum にした。関係型の推移性と逆関係は、enum の値の annotations に持たせた
- 拡張できる語彙（列ドメイン、enum の値、表記揺れ）はデータ（vocabulary.yaml）に置いた
- まだ①に無いもの：コマンド、backend、read_from、watch、メトリクス、変換関数、コンテキストマップ、主張
- [ ] ①の置き場所とパッケージ構成を決める（Mandara 本体側。ontology リポジトリ側ではない）
- [ ] 注釈の共通フィールド（4.4）を mixin として定義する
- [ ] 表・列・関係・コンテキスト・コンテキストマップのクラス
- [ ] 語彙（4.1）を、①の enum として持つか、データ（`vocabulary.yaml`）として持つかを決める。語彙は「閉じているが変更可能」なので、たぶんデータ側に置き、①は語彙の形だけを定義する
- [ ] 主張（6.6.2）のクラス
- [ ] 変換関数、メトリクスのクラスは後回しにしてよいか決める
- [ ] ①から Python の型（pydantic など）を生成して、往復できるか確かめる
- [ ] LinkML で表現できない規則の洗い出し。4.5 のうち、①で書けるものと、手書きの検証になるものを分ける

## B. 語彙
- [ ] 初期語彙を公開オントロジーに寄せる（6.6.1）。schema.org、FIBO のどこを種にするか
- [ ] ★ **通貨が JPY 決め打ちになっている**。AW は USD で、`sales.currencyrate` で通貨換算もある。`money` の通貨を固定値・別の列（`currencycode`）・換算レート参照のどれでも表せるようにする
- [ ] ★ **単位が別の列に入っている**。`production.product.weight` と `weightunitmeasurecode` のような組み合わせ。quantity ドメインに、単位の参照を持たせる
- [ ] ★ **enum のラベルを置く場所が無い**。`status: { type: enum }` だけで、1=In process、…、6=Cancelled をどこに書くかが決まっていない。値ごとのラベル、出典（主張）、承認状態を持てるようにする
- [ ] ★ **技術列のドメインが無い**。`rowguid`、`modifieddate` がほぼ全表にある。監査用・技術用の列として、UI やツール面から隠せるようにする
- [ ] ★ 名前・ラベル列、自由記述、フラグ（boolean）、コード（ISO 国コード、通貨コード、州コード）のドメインを用意する
- [ ] 公開コード表（ISO 3166、ISO 4217）を `knowledge/codelists/` に版つきで入れる。AW の `countryregion` と `currency` と照合できる
- [ ] `aliases` の初期値（AW は英語なので、日本語の揺れの評価は別のデータセットでやる）
- [ ] 語彙の変更自体をレビュー対象にする仕組み（語彙 PR）

## C. 表・列・関係の表現力（AW で引っかかったもの）

2026-10-14 の議論の結果。「方針」が付いているものは合意済み、「提案」はまだ合意待ち。

### C1. 複合主キー（AW では 23 表）　提案：合成値型。ハッシュは使わない
- ユーザーの案：全列を連結してハッシュにし、`primary` を1つにする
- **ハッシュはまずい**。理由は3つ
  - **不可逆**：ハッシュから元の列に戻れない。ゼロコピーなので、毎回ソースの列で引く必要があるが、それができない
  - **ソースの索引が効かない**：ハッシュ値はソースに存在しないので、索引を使った検索にならない
  - **FK と突き合わせられない**：参照する側（`salesorderdetail` を指す表）も複合 FK を持つ。両側で同じハッシュを計算し続けることになる
- **提案**：「`primary` を1つの値として扱いたい」という狙いは、表現を2層に分ければ満たせる
  - **OM**：真実をそのまま書く。`identity.primary: [salesorderid, salesorderdetailid]`（順序つきの列の配列）
  - **生成される型**：1つの識別子の値にする（`SalesOrderDetailId(salesorderid, salesorderdetailid)`）。文字列表現が要るときも、可逆な正規エンコード（例：`43659|1`）にする
- [ ] `identity.primary` と `via` を、列の配列に変える

### C2. 同じ表への複数の参照　方針：役割は LLM 型の判定で推定する
- 例：`billtoaddressid` と `shiptoaddressid`
- 推定の材料：名前、データの中身、ビューの SQL
- OM 側には、推定した役割を置く場所（`role`）が要る
- [ ] 関係に `role` を追加する（提案の対象。`status`、`evidence` つき）
- [ ] 役割名の推定を、推論のタスク（6.7 C）に足す

### C3. 抽象の表現（is-a）　方針：オントロジー的な継承の話として、まとめて扱う
AW には、is-a の物理的な実装パターンが少なくとも3つある。どれも同じ「論理的な is-a」の別の実装として扱う。

| パターン | AW の例 |
|---|---|
| 共有 PK | `businessentity` ← `person` / `store` / `vendor` |
| 排他的な参照 | `customer` → `person` または `store` のどちらか |
| 区分列 | `person.persontype`（SC / IN / SP / EM / VC / GC） |

- **提案**：OM では、論理（`is-a` の関係）と物理（どう実装されているか。`realized_by: shared_key | exclusive_ref | discriminator`）を分ける。型と UI が見るのは論理だけ
- **2026-10-14 実データで確認した結果**
  - `customer` は排他的ではなかった。personid だけが 18,484 行、storeid だけが 701 行、**両方ある行が 635 行**（店舗の顧客と、その窓口の人と読める）。なので、役割の違う2本の参照として表した
  - 区分列（persontype）の下位クラスには、対応する表が無い。表を単位にした今の OM では表現できない
  - なので①には、具体例のある `shared_key` だけを入れた。`exclusive_ref` と `discriminator` は、裏付けのある例が出てから足す
- [ ] 抽象の表現の設計（論理と物理の分離、区分列の値と下位クラスの対応）

### C4. パス文字列で表された木　提案：関数経由の関係
- 例：`organizationnode`、`documentnode`（`/1/1/` 形式）
- **提案**：part-of の `via` に、列の等値ではなく決定的な関数（`parent(path)` は最後の区切りまでの接頭辞）を書けるようにする
  - 関数は 11章の変換関数と同じ仕組み（16.2 の統一）
  - 親を表す列は導出列（`derived-from`）として扱う
- [ ] `via` に関数を書ける形の設計

### C5. 金額と単位　方針：量と単位を分けて持つ
- **提案**：金額・量のドメインは「量の列」と「単位の出所」の組にする。単位の出所は次の3通り
  - 固定値：AW の注文金額は USD
  - 別の列：`weight` と `weightunitmeasurecode`
  - 参照先：通貨換算の `currencyrate`
- [ ] 語彙の `money_jpy` などを、通貨・単位から独立したドメインに直す

### C6. enum のラベル　方針：ここが OM のキモ。きちんと表現する
- **提案**：enum のドメインに値の一覧を持たせる。値ごとに、コード、ラベル、意味（語彙の概念への参照）、`status`、`evidence` を持つ
  ```yaml
  columns:
    status:
      domain: order_status
  # vocabulary（またはコンテキストの語彙）側
  order_status:
    type: enum
    values:
      - { code: 1, label: In process,  status: proposed, evidence: [clm_comment_status] }
      - { code: 5, label: Shipped,     status: proposed, evidence: [clm_comment_status], observed: true }
      - { code: 6, label: Cancelled,   status: proposed, evidence: [clm_comment_status] }
  ```
- 値の集合はデータから取らない。データで観測されたかどうか（`observed`）は別に持つ。AW の status は全行が 5 なので、観測では1つしか取れない
- 値ごとに承認する。「5 = Shipped」は承認、「3 = Backordered」は保留、のように分けられる
- 状態遷移（1 → 2 → 5、2 → 4 など）を持つかどうかは 15章の未決事項。持つなら、この値の一覧の上に載せる
- [ ] enum 値の表現の設計と、語彙のどこに置くか（全体の語彙か、コンテキストごとか）

### C7. 技術列　済：仕様 4.1 に `role: technical` のドメインを追加した
- 追加したドメイン：`row_uuid`、`created_at`、`modified_at`、`modified_by`
- 既定の扱い：UI、ツール面、セマンティックモデルでは隠す。推論にも使わない

### C8. その他（未着手）
- [ ] 属性つきの n:m（`productvendor`、`specialofferproduct`）。relation の表が属性を持つことを許す
- [ ] 履歴表（`*history`）。valid_from / valid_to のロールと、event か domain か
- [ ] AW 自身のビュー 19 個を derived として扱い、SQL を主張の証拠にする
- [ ] XML 列（demographics、resume など）を変換関数の対象にする
- [ ] バイナリ列（商品写真の GIF、Word 文書、geo）をメディアのドメインとして扱う。geo の変換関数
- [ ] PII と秘密情報の区分（`person.password` は推論にもサンプルにも出さない）
- [ ] 列単位と表単位の `status` の組み合わせ
- [ ] スキーマ → コンテキストの既定の対応と、共有するもの（`person`）の扱い
- [ ] grain を自由文だけで持つか、キー列の集合として検証できる形にもするか

## D. 検証（4.5）と法則テスト
- [ ] 4.5 の各不変条件を 1 つずつ実装する（語彙外の値、event / derived にコマンドが無い、is-a と part-of の非循環、参照先 ID の存在、approved に reviewed_by がある、grain が必須、semi-additive / non-additive の sum 禁止、backend の決定、replica の freshness_sla、watch の budget と dedupe）
- [ ] 検証エラーの型を設計する。どの注釈の、どの規則に、なぜ違反したか。複数のエラーをまとめて返す
- [ ] Hypothesis の生成器：妥当な OM を生成するものと、壊れた OM を生成するもの
- [ ] 法則
  - [ ] YAML の往復（読む → 書く → 読むで一致する）
  - [ ] 書き出しの冪等性（2 回書いてもバイト単位で同じ）
  - [ ] キー順の決定性（入力の順序に依存しない）
  - [ ] 提案のマージが既存の approved / rejected を上書きしない
  - [ ] 推移閉包（is-a / part-of）の性質
  - [ ] 逆関係の対合（inverse(inverse(r)) = r）
  - [ ] 壊れた OM は必ず検出される（生成した違反の各種が、対応する規則で落ちる）

## E. 直列化とリポジトリ
- [ ] 安定ID の採番（ULID）と、改名・分割・統合されたときの追跡（15章）
- [ ] 表ごとに 1 ファイル、キー順は固定、YAML コメントは禁止（`note` を使う）
- [ ] `contexts/<context>/tables/<source>/<table>.yaml` のレイアウトで AW を置く
- [ ] `sources/adventureworks.yaml`（接続定義。秘密情報なし）
- [ ] `runs/` の記録形式（入力ハッシュ、モデル、プロンプトの版、提案数）

## F. 主張台帳と抽出器（決定的なものから）
- [ ] 主張の形式と、predicate の初期セット（否定形を含む）
- [ ] 抽出器①カタログ：PK、FK（AW は 90）、CHECK（88）、UNIQUE、NOT NULL、型、列コメント（359）、表コメントを主張にする
- [ ] 抽出器②SQL パーサ（sqlglot）：AW のビュー 19 個の JOIN / CASE を主張にする
- [ ] 列コメントからの抽出：status のラベルのような enum の意味（コメントは「設計書」相当の層）
- [ ] 公開コード表との照合：ISO 3166 と `countryregion`、ISO 4217 と `currency`
- [ ] 同じ出所の主張を 1 票に束ねる（6.6.5）
- [ ] 衝突する主張を、平均せずに「衝突」として残す

## G. 提案とレビューのライフサイクル
- [ ] status の状態遷移を ADT として定義する（proposed → approved / rejected。approved → rejected の後戻りは許すか）
- [ ] 再実行の冪等性：ソースが変わらなければ提案は出ない（6.5）
- [ ] rejected の記憶：ソースが変わらない限り再提案しない
- [ ] 確信度の閾値（0.6）と、主張から提案を合成する規則（6.6.5 の既定確信度）
- [ ] 提案を「問い」に変換する規則（9.3 の手前。UI の前に、データとしての問いの形を決める）

## H. ドメインのスキーマ②と出力
- [ ] 承認済みの OM から②（LinkML）を出力する
- [ ] ②から Python の型、JSON Schema、OWL を生成し、往復を確かめる
- [ ] ②からグラフの形（ノードとエッジ）を出力する。「グラフ DB っぽいオントロジー」として目で見られるように
- [ ] ②をコミットするか、ビルド時に生成するか（15章）

## I. AW の正解 OM と評価
2026-10-14：`evals/datasets/adventureworks/gold/ontology/` に 13 表の下書きを書いた。AI が書いたので全部 proposed。人のレビューで approved にする。
下書きを書く中で見つかった「証拠どうしの衝突」（主張台帳の衝突検出のテストケースになる）：
- `salesorderdetail.unitpricediscount`：列コメントは Discount amount（金額）。値は 0〜0.4 の 9 種類で、率と読める
- `salesorderheader.customerid`：列コメントは Customer.BusinessEntityID への FK。実際は customer.customerid
- `store.businessentityid`：列コメントは Customer への FK。実際の FK は businessentity
- `salesorderdetail.productid`：列コメントは Product への FK。product への直接の FK は無く、(specialofferid, productid) → specialofferproduct の複合 FK を経由している
- product の name と productnumber、employee の nationalidnumber と loginid は、データ上は一意だが UNIQUE 制約が無い（lorint 版で落ちている）
- [ ] AW の数表を手で OM にする（正解の種）。`salesorderheader`、`salesorderdetail`、`customer`、`person`、`businessentity`、`address`、`stateprovince`、`countryregion`、`product`、`productcategory` / `productsubcategory`（part-of 階層）
- [ ] 正解隠蔽の準備（17.2）：FK、コメント、ビューを外した AW の複製を作るスクリプト
- [ ] 指標：復元率、誤提案率、確信度の校正、低カーディナリティ別、数値列別

## J. OM に関わる未決事項（15章から抜粋）
- [ ] 語彙のドメイン型の初期セットをどこまで標準で持つか
- [ ] 「同じ意味」判定のレビュー指針
- [ ] 主張の predicate の初期セット
- [ ] 状態遷移と不変条件を OM の注釈として持つか（AW の status 1〜6 は良い題材）
- [ ] 「不明」値の意味論

## K. OM で表現するが、後のフェーズでよいもの
- コマンドアダプタ、`backend`、`handoff`（7.4）
- watch（7.5）
- 表示アノテーション（9.4）。ただし UI を磨く段階では最優先になる。geo → 地図の既定対応はここ
- メトリクス（10.4）
- 変換関数の実装（11章）。geo の bytea → lat/lon が最初の 1 本になる
- 認可ポリシー（8.2）

## L. 仕様 v0.4 のレビュー指摘（abstraction-reviewer、未対応分）
対応済み：キャッシュのキー（7.3 を 3.1 第2段に揃えた）、副作用禁止の保証の手段（3段にした）、宣言外アクセスはエラーにする。深い未解決問題として 15.1 に記録：コマンドの認可の単位、判断と実行の時点のずれ。

残り（TMC に着手するときにまとめて片付ける）：
- [ ] 「集計は書かない」と矛盾する記述が残っている：7.6 の表の `sum`、9.4 の「@query → 集計UI」、10.4.5 で `metric.query` の結果を誰が渡すか
- [ ] 汎用グラフ問い合わせが OM を直接読むのか②を読むのか。経路を②に一本化する
- [ ] ①と②の置き場所を 5章に書く。②をコミットする場合の CI 規則（再生成して差分ゼロ）
- [ ] 16.1 の差し込み口の契約に LinkML という名前が漏れている。エクスポータの入力を②（approved のみ）に揃える
- [ ] 言語境界の契約に、スナップショットと `Command` の直列化形式を足す
- [ ] エラーの経路を2つに分ける（ドメインの `Left` と実行環境のエラー）。どちらも監査に記録する。「不明」値の受け取り方も書く
- [ ] `@command` の `backend` とアダプタの `backend` が二重になっている。`approval` の語彙のずれも直す
- [ ] 件数上限を `reads` 宣言の一部にする
- [ ] 17.1 にメソッドと TMC の法則テストを足す（決定性、再生成の冪等性、②→OWL の往復）
- [ ] is-a の継承でメソッドをどう選ぶか（ディスパッチ規則）と、下位の `reads` が上位の `reads` を含むこと

## M. OM 実装のレビュー指摘（abstraction-reviewer、2026-10-14）のうち未対応
対応済み：
- YAML 1.1 のキー化け（on / null など）をエラーにした
- 辞書キーと name の食い違いを、Domain の違反と書き出し時のエラーの両方で検出する
- 自分の ID を target に書いた is-a / 自己参照を禁止した（自己参照は self だけ）
- 生成物から絶対パスを外し、linkml の版を固定した
- 関係型の性質（推移・逆関係）を①から生成し、循環検査はそれを見る
- 具体例の無い UnitSource.via と Temporal を①から外した
- check() が不正な値で例外を投げないようにした
- 空白だけの grain を欠落として扱う
- 配置と中身の食い違いと、配置規則に合わないファイルをエラーにした
- 法則テストを広げた。すべての規則に壊し方を用意し、生成器も複合キー・役割・単位列・self への関係・紛らわしい列名まで作る。語彙の往復も足した
- gold を正規形にした（コメントは README に移した）
- 仕様 4.1 / 4.2 を合意した形に揃えた

2026-10-14 の判断で済んだもの：
- [x] `ColumnDomain.type` と `unit.fixed` を①の enum（ValueType / Unit）にした。不明な値は UNKNOWN_VALUE
- [x] 意味の型 `geo.point` と、列の `encoding`（SourceEncoding。今は sqlserver_geography だけ）に分けた。変換はソースの Adapter の責務
- [x] product → subcategory、subcategory → category、employee の上司を has（reference）にした。分類への所属や上司は全体と部分ではない
- [x] encoding の変換先の型を①に持ち（decodes_to → encoding_traits.py）、列ドメインの型と違えば ENCODING_MISMATCH。変換は DB コネクタではなく encoding ごとのコーデックの責務と仕様に明記
- [x] 仕様 10.4 の「品目→品目分類」を part-of のドリルダウン例から外し、未決（15.2）にした

要相談：
- [x] （済：関係型 classified-as を追加。推移的）分類の階層（製品 → サブカテゴリ → カテゴリ）を has で書くと、ドリルダウン階層（10.x、「品目→品目分類」）として拾えない。分類専用の関係型（例 `classified-as`、推移的）を①に足すか、has のまま UI 側で階層とみなすか
- [x] （済：billofmaterials の辺。製品同士の多段の part-of で、推移閉包を試せる）gold に良い part-of の例が少ない（今は 注文明細 → 注文、州 → 国）。部品表（production.billofmaterials：製品の自己参照の part-of）を gold に足すか
- [ ] 住所 → 州を has のままにするか（州 → 国 は part-of にしている。地理の包含を part-of とみるなら揃える）
- [x] （済：読み込みの境界で拒否する、と仕様 4.5 に明記）閉じた集合の外の値は読み込みで OmLoadError になり、UNKNOWN_VALUE として集まらない（既存の status / kind も同じ）。読み込みの境界で拒否する今の形でよいか（仕様 15.2）
- [x] （済：中間表の edge。部品表を part-of の辺にした）中間表が実現する、もの同士の関係を①で直接書けない
- [ ] 行レベルの非循環（部品表や self の木が本当に木であること）を、データの主張（6.6）として検証する。推移閉包とドリルダウンの前提
- [ ] 辺（edge）に安定 ID を持たせるか。今は 1 表に 1 本で、`<表 ID>.edge` で指す
- [ ] 他の中間表（employeedepartmenthistory：期間つきの辺、businessentityaddress：住所の種類つきの辺、specialofferproduct：純粋な n:m）を gold に足すか
- [ ] 中間表を通らない、何段もの経路の合成（受注明細 → 特売製品 → 製品）。具体例が要るまでやらない
- [ ] 行ごとに役割が変わる辺（住所の種類で請求先／出荷先）。今は辺の属性として持つだけ
- [ ] 代替キーの列が NULL を持たないことは、今は検査できない（①に列の NULL 可否が無い）。プロファイル／カタログの主張（6.6）が入ったら検査する
- [ ] `document.xml` も物理的な表現（XML）を型の名前に含んでいる。geo と同じく、意味の型と encoding に分けるか
- [ ] 仕様 7.6 付近の表（`type: money` ＋ `currency`）が、量と単位の分離（unit）の前の書き方のまま
- [ ] enum の code と観測値を照合するときの正規化（PostgreSQL の真偽値は t / f、gold は "true" / "false"）。ソースの Adapter の責務として決める
