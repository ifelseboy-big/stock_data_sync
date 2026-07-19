#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="$PROJECT_ROOT/src/server"
PERF_ROOT="$(mktemp -d /tmp/stock-data-sync-perf.XXXXXX)"
PERF_PG_DIR="$PERF_ROOT/postgres"
PERF_PROFILE="${PERF_PROFILE:-target}"
PERF_REPORT_PATH="${PERF_REPORT_PATH:-$PROJECT_ROOT/dist/performance/$PERF_PROFILE.json}"
PERF_PORT="${PERF_PORT:-}"

if [[ -n "${PERF_POSTGRES_BIN_DIR:-}" ]]; then
  export PATH="$PERF_POSTGRES_BIN_DIR:$PATH"
elif command -v brew >/dev/null 2>&1; then
  HOMEBREW_POSTGRES_BIN="$(brew --prefix postgresql@18 2>/dev/null)/bin"
  if [[ -x "$HOMEBREW_POSTGRES_BIN/postgres" ]]; then
    export PATH="$HOMEBREW_POSTGRES_BIN:$PATH"
  fi
fi

find_port() {
  local port
  for port in $(seq 55450 55550); do
    if ! pg_isready -h 127.0.0.1 -p "$port" >/dev/null 2>&1; then
      printf '%s\n' "$port"
      return
    fi
  done
  printf '没有可用的 PostgreSQL 性能测试端口\n' >&2
  exit 1
}

cleanup() {
  if [[ -f "$PERF_PG_DIR/postmaster.pid" ]]; then
    pg_ctl -D "$PERF_PG_DIR" stop -m fast >/dev/null
  fi
  if [[ "${KEEP_PERF_DATA:-0}" == "1" ]]; then
    printf '性能测试数据保留在：%s\n' "$PERF_ROOT"
    return
  fi
  case "$PERF_ROOT" in
    /tmp/stock-data-sync-perf.*) rm -rf -- "$PERF_ROOT" ;;
    *) printf '拒绝清理非性能测试临时目录：%s\n' "$PERF_ROOT" >&2 ;;
  esac
}
trap cleanup EXIT

for command_name in postgres initdb pg_ctl pg_isready createdb; do
  command -v "$command_name" >/dev/null 2>&1 || {
    printf '缺少命令：%s\n' "$command_name" >&2
    exit 1
  }
done
postgres --version | grep -q ' 18\.' || {
  printf '性能测试必须使用 PostgreSQL 18\n' >&2
  exit 1
}

if [[ -z "$PERF_PORT" ]]; then
  PERF_PORT="$(find_port)"
fi
[[ "$PERF_PORT" =~ ^[0-9]+$ ]] || {
  printf 'PERF_PORT 必须是数字\n' >&2
  exit 1
}

mkdir -p "$(dirname -- "$PERF_REPORT_PATH")"
initdb \
  -D "$PERF_PG_DIR" \
  --auth=trust \
  --username=postgres \
  --encoding=UTF8 \
  --no-locale >/dev/null
pg_ctl \
  -D "$PERF_PG_DIR" \
  -o "-p $PERF_PORT -h 127.0.0.1 -c max_connections=50" \
  -w start >/dev/null
createdb -h 127.0.0.1 -p "$PERF_PORT" -U postgres stock_data_sync_perf

export DATABASE_URL="postgresql+psycopg://postgres@127.0.0.1:$PERF_PORT/stock_data_sync_perf"
export RAW_DATA_DIR="$PERF_ROOT/raw"
mkdir -p "$RAW_DATA_DIR"

printf '执行数据库迁移...\n'
(
  cd "$SERVER_DIR"
  UV_CACHE_DIR=/tmp/stock-data-sync-uv-cache uv run alembic upgrade head
)

printf '执行 %s 规模性能测试...\n' "$PERF_PROFILE"
(
  cd "$SERVER_DIR"
  PYTHONPATH="$SERVER_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    UV_CACHE_DIR=/tmp/stock-data-sync-uv-cache \
    uv run python tests/performance/benchmark_postgresql.py \
      --profile "$PERF_PROFILE" \
      --json-output "$PERF_REPORT_PATH"
)

printf '性能测试结果：%s\n' "$PERF_REPORT_PATH"
