#!/usr/bin/env bash
# すべての検証を一度に回す（CI 相当）。どれか1つでも落ちたら失敗。
#   Python：型検査（pyright strict）、テスト（生成物の食い違い、契約、AdventureWorks に対する通しを含む）
#   UI：型検査（生成した型を含む）、テスト（言語をまたぐ契約）、本番用のビルド
# AdventureWorks の PostgreSQL（dbname=adventureworks）と uv / pnpm が要る。
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
echo "◆ all checks passed"
