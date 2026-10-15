#!/usr/bin/env bash
# すべての検証を一度に回す（CI 相当）。どれか1つでも落ちたら失敗。
#   Python：型検査（pyright strict）、テスト（生成物の食い違い、契約、AdventureWorks に対する通しを含む）
#   UI：型検査（生成した型を含む）、テスト（言語をまたぐ契約）、本番用のビルド、スモーク（ブラウザのコンソールにエラーが無い）
# AdventureWorks の PostgreSQL（dbname=adventureworks）、uv / pnpm、Google Chrome が要る。ポート 8765 と 4173 を使う。
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
echo "◆ python: pyright";   uv run pyright
echo "◆ python: pytest";    uv run pytest -q
cd ui
echo "◆ ui: install";       pnpm install --frozen-lockfile --silent
echo "◆ ui: typecheck";     pnpm typecheck
echo "◆ ui: test";          pnpm test
echo "◆ ui: build";         pnpm build >/dev/null
cd "$ROOT"
echo "◆ ui: smoke（API と本番ビルドを立て、ヘッドレスの Chrome で画面を開く）"
for port in 8765 4173; do
  if lsof -iTCP:$port -sTCP:LISTEN >/dev/null 2>&1; then
    echo "ポート $port が使われている（開発中の mandara serve / vite など）。止めてから回す" >&2
    exit 1
  fi
done
LOGS=$(mktemp -d)
set -m  # バックグラウンドの各ジョブを別のプロセスグループにし、子・孫ごと止められるようにする
uv run mandara serve --gold adventureworks=evals/datasets/adventureworks/gold/ontology \
  --connect adventureworks dbname=adventureworks --port 8765 >"$LOGS/serve.log" 2>&1 &
SERVE=$!
(cd ui && exec pnpm vite preview --port 4173 --strictPort --host 127.0.0.1) >"$LOGS/preview.log" 2>&1 &
PREVIEW=$!
set +m
trap 'kill -- -$SERVE -$PREVIEW 2>/dev/null || true' EXIT
up=""
for _ in $(seq 1 60); do
  if curl -sf http://127.0.0.1:4173/api/ontologies >/dev/null; then up=1; break; fi
  sleep 0.5
done
if [ -z "$up" ]; then
  echo "API か UI が立ち上がらない" >&2
  cat "$LOGS/serve.log" "$LOGS/preview.log" >&2
  exit 1
fi
node scripts/ui_smoke.mjs http://127.0.0.1:4173/
echo "◆ all checks passed"
