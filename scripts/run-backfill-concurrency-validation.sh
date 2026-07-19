#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="$PROJECT_ROOT/src/server"

if [[ "${CONFIRM_TEST_DATABASE:-}" != "stock_data_sync" ]]; then
  printf '拒绝执行：请显式设置 CONFIRM_TEST_DATABASE=stock_data_sync\n' >&2
  exit 1
fi

START_DATE="${START_DATE:-2026-06-10}"
END_DATE="${END_DATE:-2026-06-20}"
RUN_ID="${RUN_ID:-v1}"
REPORT="${BACKFILL_VALIDATION_REPORT:-$PROJECT_ROOT/dist/live-validation/backfill-${START_DATE}-${END_DATE}-concurrency.json}"
export ADMIN_API_TOKEN="${ADMIN_API_TOKEN:-local-backfill-concurrency-validation-token}"
export SCHEDULER_POLL_SECONDS="${SCHEDULER_POLL_SECONDS:-10}"
# 本地与部署机共用同一个 token 时，为部署机预留至少 100 次/分钟额度。
export TUSHARE_REQUEST_BUDGET_PER_MINUTE="${TUSHARE_REQUEST_BUDGET_PER_MINUTE:-400}"

cd "$SERVER_DIR"
PYTHONPATH="$SERVER_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  UV_CACHE_DIR=/tmp/stock-data-sync-uv-cache \
  uv run python -m tests.live.verify_backfill_concurrency \
  --start "$START_DATE" \
  --end "$END_DATE" \
  --run-id "$RUN_ID" \
  --report "$REPORT"
