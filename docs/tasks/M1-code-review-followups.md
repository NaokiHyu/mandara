# M1 コードの見直しで見つかった残り

- 状態：未着手
- 段：保守　依存：—

## 目的
2026-10-21 のソースの読み込み（全体の見直し）で見つかった問題のうち、急がないものを積んでおく。
直すときは、項目ごとにどの層の責務かを決めてから直す（症状の出た場所に継ぎ足さない）。

## 済み（2026-10-21）
- `run` が正解（gold）の表の順を、refine で先に問う順として渡していた → 外した。テスト
  `test_run_asks_the_same_questions_with_or_without_gold` で固定
- `Document` を `modeler.writer` から `source.documents` へ移した（omstore が modeler に依存しない）
- レビューの検査（`review_changes`）を store から Application（`pipeline.write_reviewed`）へ移した
- 再評価（aw_bare4、手引き＋パック、3 回）：列の型 94〜98、組 124〜173。揺れの幅（T3.10）の中で、効果は区別できない。
  予算で止まらない実行では、順の漏れは点にほぼ効かない

## やること（優先の高い順）
### 誤りの扱い（原則 1）
- `connectors/postgres/record_reader.py:89`：`psycopg.DataError` をすべて `InvalidKeyError` にしている。aggregate の
  桁あふれや Contains の型変換の失敗も「キーが不正」に見える。キーの解釈の失敗だけをキーの誤りにする
- reader の一部（`inclusion_reader._count/_ordered/_span`、`sample_reader:40`、`uniqueness_reader:62`、
  `coverage_reader._split_value`）が `AssertionError` / `ValueError` を投げる。ポートの契約（`SourceError`）の外
- `connectors/postgres/check_values.py`：sqlglot で読めない CHECK は理由を残さずに値の一覧を出さない。負の数
  （`IN (-1, 0, 1)`）も `exp.Literal` でないので黙って落ちる。読めなかったことを記録する
- `api/server.py:76`：`work/` が無いと空の一覧を返す（画面は「OM が無い」）。無いことをエラーにする

### 層と責務
- 書き込みの方針が層に分かれている：「人の判断を含む OM を上書きしない」は store（`write_ontology`）、「レビューでは判断
  だけを変える」は Application（`write_reviewed`）。公開の `replace_ontology` を呼べば検査を飛ばせる（呼ぶのは今
  `write_reviewed` だけ）。どちらの層が持つかを揃える。T6.1（判断を保った統合）と一緒に決める
- プロンプトと出力のパーサが Domain の `modeler/` にある（`classify.py` の NAMING_SYSTEM など）。CLAUDE.md の
  「プロンプトと出力のパースは Adapter 側」と食い違う。どちらに合わせるか決める（CLAUDE.md を直すなら相談）
- `modeler/llm.py` は LLM ではなく答えの当て方を持つ。名前が中身と合わない
- `om.yaml` に OM のメタモデルと API・画面の契約（`OntologyIndex`、`ReviewQueue` など、:885〜）が同居している
- `browse.display` が日本語の表示文字列を作り、デコードの失敗をセルの文字として返す（表示の責務が Application にある）
- `ui/src/review.ts` が `decisions.is_table_batch_member` / `is_approve_all_member` を作り直している（サーバーが 409 で守る）

### 確信度
- 「部品ごとの較正」は測っているだけで、値は変えていない。`WEAK_BELOW` は 14 表の 1 回の実行から手で決めた値
- `weak_parts` が `<` で比べるので、規則の値（所属 0.7、種類 0.9）はいつも「弱くない」
- 表の確信度は規則だと 0.7 以下、LLM だと 0.9 が上限なので、一括承認（0.95）に表は入らない
- `_revert` で種類を戻しても、種類のために足した evidence / inferred_by が残る（`modeler/llm.py:582`）
- `refine` は defect で止まった後も `prune` して OM を書き直す（`pipeline.py:620`）

### テスト
- 契約の法則テストの無いポート：`ColumnReader`、`CoverageReader`、`RecordReader`
- 応答の契約の検査の無い API：domains、domain_rows、instance（Python 側も UI 側も）
- 実 DB（`dbname=adventureworks`）の要るテストが、DB の無いときに何をするか決まっていない
- `replace_ontology` が `InvalidOntologyError` を出すことのテストが無い

### 小さなもの
- Claude の Adapter が返ってきたモデル名を確かめない（Jev の Adapter は確かめる）。`count_tokens` は予算に数えない
- `api/server.py` の domains 系の応答だけ `by_alias=True` が無い
- `settings/llm_prices.py` の `DEFAULT_PATH` はソースの checkout を前提にしている
- 抽出器が `table.kind != ObjectKind.table.value` のように enum と文字列を混ぜて比べる
- 過去の点数の記録（`docs/backlog.md` の段 3、予算 ¥10 で途中停止した回）は、正解の表を先に問うていたので、正解寄りに
  出ていた可能性がある（再測定はしていない）

## 完了条件
- 各項目を直すか、直さない理由を書いて閉じる
