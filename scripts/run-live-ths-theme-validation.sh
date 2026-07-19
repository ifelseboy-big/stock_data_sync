#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="$PROJECT_ROOT/src/server"

if [[ "${CONFIRM_TEST_DATABASE:-}" != "stock_data_sync" ]]; then
  printf '拒绝执行：请显式设置 CONFIRM_TEST_DATABASE=stock_data_sync\n' >&2
  exit 1
fi

export ADMIN_API_TOKEN="${ADMIN_API_TOKEN:-local-ths-theme-validation-token}"

cd "$SERVER_DIR"
PYTHONPATH="$SERVER_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  UV_CACHE_DIR=/tmp/stock-data-sync-uv-cache \
  uv run python -m tests.live.verify_ths_theme_workflow "$@"
