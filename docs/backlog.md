# バックログ

ゴール：**データソースを読み、解析し、提案を人が承認し（UI またはコマンド）、承認された OM をオントロジーUIで見る。**

進め方
- 解析器と UI は一緒に育てる。各段で「端から端まで細く通す」→「太くする」を繰り返す
- 各段の終わりに、正解 OM（`evals/datasets/adventureworks/gold`）に対する点数を測り、ここに記録する
- 1タスク = 1ファイル（`docs/tasks/`）。状態はこの一覧と各ファイルの両方で更新する
- OM のメタモデルと検証の細かい残課題は `docs/om-backlog.md`

状態：`未着手` / `調査済み` / `進行中` / `完了` / `保留`

## 決まっていること
| 日付 | 決定 |
|---|---|
| 2026-10-15 | UI は TypeScript + React + Vite。地図は MapLibre GL、グラフは Cytoscape.js。Python はローカルの HTTP API（FastAPI）で OM と DB の読みを出す。境界の契約は OM ファイルと JSON。TS の型は①から生成する |
| 2026-10-15 | 決定的な抽出器の主張はキャッシュとして git に乗せない。主張の ID は内容のハッシュで決める（再生成しても同じ ID） |
| 2026-10-15 | 自動生成した OM・主張・実行記録は `work/<source>/` に書き、git に乗せない。正解 OM とは場所を分ける |

## 段 1：細い通し（決定的な処理だけ。LLM なし）
到達点：AW を読んで → OM の提案が出て → CLI で承認して → UI で見える。

| ID | タスク | 状態 | 依存 |
|---|---|---|---|
| [T1.1](tasks/T1.1-postgres-catalog-connector.md) | PostgreSQL のカタログコネクタ | 調査済み | — |
| [T1.2](tasks/T1.2-claims-ledger.md) | 主張台帳（形式と置き場所） | 未着手 | — |
| [T1.3](tasks/T1.3-catalog-extractor.md) | カタログの抽出器（カタログ → 主張） | 未着手 | T1.1, T1.2 |
| [T1.4](tasks/T1.4-modeler-v0.md) | モデラー v0（規則だけで OM を提案） | 未着手 | T1.3 |
| [T1.5](tasks/T1.5-evaluation-v0.md) | 評価 v0（正解 OM との突き合わせ） | 未着手 | T1.4 |
| [T1.6](tasks/T1.6-cli.md) | コマンド（抽出・提案・評価を通しで回す） | 未着手 | T1.4 |
| [T1.7](tasks/T1.7-review-cli.md) | レビュー CLI | 未着手 | T1.6 |
| [T1.8](tasks/T1.8-ui-foundation.md) | UI の土台（HTTP API、型の生成、React の骨格、見た目の方向） | 未着手 | — |
| [T1.9](tasks/T1.9-ontology-ui-v0.md) | オントロジーUI v0（関係のグラフ、表の詳細） | 未着手 | T1.8 |
| [T1.10](tasks/T1.10-map-view.md) | 地図（geo.point のデコードと表示） | 未着手 | T1.9 |

## 段 2：プロファイル
| ID | タスク | 状態 | 依存 |
|---|---|---|---|
| [T2.1](tasks/T2.1-column-profile.md) | 列の統計（行数、一意性、NULL 率、基数、分布） | 未着手 | T1.3 |
| [T2.2](tasks/T2.2-inclusion.md) | 値の包含率と被覆率（未宣言の外部キー候補） | 未着手 | T2.1 |
| [T2.3](tasks/T2.3-enum-and-technical.md) | enum 候補と技術列の検出 | 未着手 | T2.1 |
| [T2.4](tasks/T2.4-encoding-detection.md) | 物理的な表現（encoding）の検出 | 未着手 | T2.1 |
| [T2.5](tasks/T2.5-ui-data.md) | UI：実データの表示、レビューの根拠にプロファイルを添える | 未着手 | T1.9, T2.1 |

## 段 3：LLM による推論
| ID | タスク | 状態 | 依存 |
|---|---|---|---|
| [T3.1](tasks/T3.1-llm-adapter.md) | LLM の Adapter | 未着手 | — |
| [T3.2](tasks/T3.2-modeler-v1.md) | モデラー v1（列ドメイン、表種別、grain、役割、is-a、enum ラベル、単位） | 未着手 | T3.1, T2.* |
| [T3.3](tasks/T3.3-conflicts.md) | 証拠の衝突の検出と問い化 | 未着手 | T3.2 |
| [T3.4](tasks/T3.4-hide-truth-eval.md) | 正解隠蔽テスト | 未着手 | T3.2 |
| [T3.5](tasks/T3.5-review-ui.md) | レビュー画面（問いの形式、束で承認、衝突の選択） | 未着手 | T1.7, T1.8 |
| [T3.6](tasks/T3.6-durable-claims.md) | レビューの回答と LLM の主張の保存方針 | 未着手 | T3.2 |

## 段 4：コードを掘る
| ID | タスク | 状態 | 依存 |
|---|---|---|---|
| [T4.1](tasks/T4.1-sql-extractor.md) | SQL パーサの抽出器（ビュー定義の JOIN / CASE） | 未着手 | T1.2 |
| [T4.2](tasks/T4.2-layered-recall.md) | 証拠の層ごとの復元率の記録 | 未着手 | T3.4, T4.1 |

## 段 5：オントロジーUI を太くする
| ID | タスク | 状態 | 依存 |
|---|---|---|---|
| [T5.1](tasks/T5.1-display-annotations.md) | 表示アノテーションの提案とレビュー | 未着手 | T3.5 |
| [T5.2](tasks/T5.2-default-views.md) | OM → UI の既定対応（ツリー、タイムライン、かんばん、辺のグラフ） | 未着手 | T2.5 |
| [T5.3](tasks/T5.3-graph-query.md) | 汎用のグラフ問い合わせ | 未着手 | T2.5 |

## 段 6：再実行と履歴
| ID | タスク | 状態 | 依存 |
|---|---|---|---|
| [T6.1](tasks/T6.1-idempotent-rerun.md) | 冪等な再実行（差分だけ提案、rejected は再提案しない） | 未着手 | T1.6 |
| [T6.2](tasks/T6.2-git-pr.md) | git のブランチと PR に書く | 未着手 | T6.1 |

## 点数の記録
| 日付 | 段 | 主キー | 関係 | 関係型 | 列ドメイン | 表種別 | 備考 |
|---|---|---|---|---|---|---|---|
