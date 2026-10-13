# ローカル DB の見方

AdventureWorks（自転車メーカーの業務DB）がローカルの PostgreSQL 17 に入っている。データの入れ直しは `evals/datasets/adventureworks/README.md` を参照。

## 接続情報

| 項目 | 値 |
|---|---|
| ホスト | `localhost`（または Unix ソケット `/tmp`） |
| ポート | `5432` |
| DB | `adventureworks` |
| ユーザー | macOS のユーザー名（`hyu`） |
| パスワード | なし（ローカル接続は trust） |

GUI ツール（TablePlus、DBeaver、DataGrip など）からも、この値でそのまま繋がる。

## まず動かす

```sh
# psql は PATH に入っていないので、最初に通す（~/.zshrc に書いてもよい）
export PATH=/opt/homebrew/opt/postgresql@17/bin:$PATH

# Hello world：全体像、注文のたどり方、国別売上、status 列
psql -X -d adventureworks -f evals/datasets/adventureworks/hello.sql

# 対話で入る
psql -d adventureworks
```

## psql の中でよく使うもの

| コマンド | 何をするか |
|---|---|
| `\dn` | スキーマ一覧 |
| `\dt sales.*` | sales スキーマの表一覧 |
| `\dt+ sales.*` | 同上。サイズと表の説明つき |
| `\d+ sales.salesorderheader` | 列、型、制約、FK、列の説明 |
| `\x auto` | 横に長い結果を縦表示に切り替える |
| `\q` | 終了 |

表と列には英語の説明（コメント）が付いている。

```sql
SELECT obj_description('sales.salesorderheader'::regclass);       -- 表の説明
SELECT col_description('sales.salesorderheader'::regclass, 6);    -- 6 列目（status）の説明
```

## 中身の地図

| スキーマ | 内容 | 主な表 |
|---|---|---|
| `person` | 人、住所、州、国 | `person`、`address`、`stateprovince`、`countryregion` |
| `sales` | 顧客、注文、営業、テリトリー | `customer`、`salesorderheader`、`salesorderdetail`、`salesterritory` |
| `production` | 製品、在庫、製造指示 | `product`、`productcategory`、`workorder` |
| `purchasing` | 仕入先、発注 | `vendor`、`purchaseorderheader` |
| `humanresources` | 従業員、部署 | `employee`、`department` |

`pe` / `hr` / `pr` / `pu` / `sa` スキーマは、変換元（lorint 版）が足した便利ビュー。元の AdventureWorks には無いので、見るときは無視してよい。

## サーバーの起動と停止

```sh
brew services start postgresql@17     # 起動（ログイン時にも自動で起動する）
brew services stop postgresql@17      # 停止
pg_isready                            # 動いているか確認
```
