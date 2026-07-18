#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

INSTALL_DIR=""
HTTP_PORT=""
START_AFTER_INSTALL=1

fail() {
  printf '安装失败：%s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
用法：sudo ./deploy/production/install.sh [选项]

选项：
  --install-dir PATH  安装目录，必填，不提供默认值
  --http-port PORT    Web 访问端口，未传入时交互输入
  --no-start          完成安装和镜像构建，但不启动服务
  -h, --help          显示帮助

也可以不传参数并按提示输入。安装目录输入为空时安装立即终止。
EOF
}

while (( $# > 0 )); do
  case "$1" in
    --install-dir)
      (( $# >= 2 )) || fail "--install-dir 缺少路径"
      INSTALL_DIR="$2"
      shift 2
      ;;
    --http-port)
      (( $# >= 2 )) || fail "--http-port 缺少端口"
      HTTP_PORT="$2"
      shift 2
      ;;
    --no-start)
      START_AFTER_INSTALL=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "未知参数：$1"
      ;;
  esac
done

(( EUID == 0 )) || fail "请使用 sudo 或 root 用户执行安装"

if [[ -z "$INSTALL_DIR" ]]; then
  [[ -t 0 ]] || fail "非交互安装必须传入 --install-dir"
  read -r -p "请输入安装目录（绝对路径，无默认值）: " INSTALL_DIR
fi
[[ -n "$INSTALL_DIR" ]] || fail "安装目录不能为空"
[[ "$INSTALL_DIR" == /* ]] || fail "安装目录必须是绝对路径"
[[ "$INSTALL_DIR" != "/" ]] || fail "安装目录不能是根目录"
[[ "$INSTALL_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]] || fail "安装目录只能包含字母、数字、点、下划线、短横线和斜杠"

if [[ -z "$HTTP_PORT" ]]; then
  [[ -t 0 ]] || fail "非交互安装必须传入 --http-port"
  read -r -p "请输入 Web 访问端口（无默认值）: " HTTP_PORT
fi
[[ "$HTTP_PORT" =~ ^[0-9]+$ ]] || fail "Web 端口必须是数字"
(( HTTP_PORT >= 1 && HTTP_PORT <= 65535 )) || fail "Web 端口必须在 1-65535 之间"

if [[ -d "$INSTALL_DIR" ]]; then
  shopt -s nullglob dotglob
  existing_files=("$INSTALL_DIR"/*)
  shopt -u nullglob dotglob
  (( ${#existing_files[@]} == 0 )) || fail "安装目录不是空目录：$INSTALL_DIR"
fi

for required_command in docker openssl tar; do
  command -v "$required_command" >/dev/null 2>&1 || fail "缺少命令：$required_command"
done
docker compose version >/dev/null 2>&1 || fail "需要 Docker Compose v2"
[[ -d "$PROJECT_ROOT/src/server" ]] || fail "发布包缺少 src/server"
[[ -d "$PROJECT_ROOT/src/web" ]] || fail "发布包缺少 src/web"

TUSHARE_VALUE="${TUSHARE_TOKEN:-}"
if [[ -z "$TUSHARE_VALUE" && -t 0 ]]; then
  read -r -s -p "请输入 Tushare Token（可留空，安装后再配置）: " TUSHARE_VALUE
  printf '\n'
fi
[[ "$TUSHARE_VALUE" != *$'\n'* ]] || fail "Tushare Token 不能包含换行"

POSTGRES_PASSWORD="$(openssl rand -hex 24)"
if [[ -f "$PROJECT_ROOT/VERSION" ]]; then
  APP_VERSION="$(tr -d '[:space:]' < "$PROJECT_ROOT/VERSION")"
else
  APP_VERSION="dev-$(date -u +%Y%m%d%H%M%S)"
fi
[[ "$APP_VERSION" =~ ^[A-Za-z0-9._-]+$ ]] || fail "发布版本号格式不正确"

install -d -m 0755 \
  "$INSTALL_DIR/app" \
  "$INSTALL_DIR/bin" \
  "$INSTALL_DIR/config" \
  "$INSTALL_DIR/data/postgres" \
  "$INSTALL_DIR/logs/server" \
  "$INSTALL_DIR/logs/scheduler" \
  "$INSTALL_DIR/logs/nginx" \
  "$INSTALL_DIR/logs/postgres" \
  "$INSTALL_DIR/backups"

tar -C "$PROJECT_ROOT" \
  --exclude='src/server/.venv' \
  --exclude='src/server/.pytest_cache' \
  --exclude='src/server/.ruff_cache' \
  --exclude='src/server/.mypy_cache' \
  --exclude='src/server/**/__pycache__' \
  --exclude='src/server/tests' \
  --exclude='src/web/node_modules' \
  --exclude='src/web/dist' \
  --exclude='src/web/.vite' \
  --exclude='src/web/coverage' \
  -cf - src/server src/web deploy/docker \
  | tar -C "$INSTALL_DIR/app" -xf -

install -m 0644 "$SCRIPT_DIR/compose.yaml" "$INSTALL_DIR/compose.yaml"
install -m 0755 "$SCRIPT_DIR/bin/stock-data-sync" "$INSTALL_DIR/bin/stock-data-sync"
install -m 0644 "$PROJECT_ROOT/deploy/docker/app.dockerignore" "$INSTALL_DIR/app/.dockerignore"

umask 077
{
  printf 'INSTALL_DIR=%s\n' "$INSTALL_DIR"
  printf 'APP_VERSION=%s\n' "$APP_VERSION"
  printf 'HTTP_BIND=0.0.0.0\n'
  printf 'HTTP_PORT=%s\n' "$HTTP_PORT"
  printf 'POSTGRES_DB=stock_data_sync\n'
  printf 'POSTGRES_USER=stock_sync\n'
  printf 'POSTGRES_PASSWORD=%s\n' "$POSTGRES_PASSWORD"
  printf 'TUSHARE_TOKEN=%s\n' "$TUSHARE_VALUE"
  printf 'TUSHARE_REQUEST_LIMIT_PER_MINUTE=500\n'
  printf 'TUSHARE_REQUEST_BUDGET_PER_MINUTE=480\n'
  printf 'TUSHARE_TIMEOUT_SECONDS=30\n'
  printf 'TUSHARE_MAX_ATTEMPTS=3\n'
  printf 'TUSHARE_RETRY_WAIT_SECONDS=2\n'
  printf 'SCHEDULER_TIMEZONE=Asia/Shanghai\n'
  printf 'SCHEDULER_JOBSTORE_TABLE=apscheduler_jobs\n'
  printf 'SCHEDULER_ADVISORY_LOCK_ID=731500001\n'
  printf 'SCHEDULER_MAX_WORKERS=4\n'
  printf 'SCHEDULER_POLL_SECONDS=30\n'
  printf 'APP_LOG_MAX_BYTES=52428800\n'
  printf 'APP_LOG_BACKUP_COUNT=10\n'
} > "$INSTALL_DIR/config/app.env"
chmod 0600 "$INSTALL_DIR/config/app.env"

COMPOSE=(
  docker compose
  --project-directory "$INSTALL_DIR"
  --env-file "$INSTALL_DIR/config/app.env"
  -f "$INSTALL_DIR/compose.yaml"
)

"${COMPOSE[@]}" config >/dev/null
"${COMPOSE[@]}" pull postgres

POSTGRES_UID="$(docker run --rm --entrypoint sh postgres:16-alpine -c 'id -u postgres')"
POSTGRES_GID="$(docker run --rm --entrypoint sh postgres:16-alpine -c 'id -g postgres')"
chown -R "$POSTGRES_UID:$POSTGRES_GID" "$INSTALL_DIR/data/postgres" "$INSTALL_DIR/logs/postgres"
chown -R 10001:10001 "$INSTALL_DIR/logs/server" "$INSTALL_DIR/logs/scheduler"

"${COMPOSE[@]}" build --pull server web

if (( START_AFTER_INSTALL == 1 )); then
  "$INSTALL_DIR/bin/stock-data-sync" start
fi

printf '\n安装完成。\n'
printf '安装目录：%s\n' "$INSTALL_DIR"
printf '管理命令：%s/bin/stock-data-sync\n' "$INSTALL_DIR"
printf '服务地址：http://<服务器IP>:%s\n' "$HTTP_PORT"
