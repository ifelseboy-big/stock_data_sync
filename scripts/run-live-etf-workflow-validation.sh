#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="$PROJECT_ROOT/src/server"

if [[ "${CONFIRM_TEST_DATABASE:-}" != "stock_data_sync" ]]; then
  printf '拒绝执行：请显式设置 CONFIRM_TEST_DATABASE=stock_data_sync\n' >&2
  exit 1
fi

export ADMIN_API_TOKEN="${ADMIN_API_TOKEN:-local-etf-workflow-validation-token}"
export LIVE_ETF_VALIDATION_REPORT="${LIVE_ETF_VALIDATION_REPORT:-$PROJECT_ROOT/dist/live-validation/recent-etf-workflows.json}"

cd "$SERVER_DIR"
PYTHONPATH="$SERVER_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  UV_CACHE_DIR=/tmp/stock-data-sync-uv-cache \
  uv run python tests/live/verify_recent_etf_workflows.py "$@"

