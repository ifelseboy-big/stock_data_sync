#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
INSTALL_DIR=""
HTTP_PORT=""
HTTP_BIND="0.0.0.0"
POSTGRES_PORT="5432"
SERVICE_USER="${SUDO_USER:-}"
BACKUP_DIR=""
START_AFTER_INSTALL=1
PASSWORD_FILE=""

fail() {
  printf '安装失败：%s\n' "$*" >&2
  exit 1
}

cleanup() {
  if [[ -n "$PASSWORD_FILE" && -f "$PASSWORD_FILE" ]]; then
    rm -f -- "$PASSWORD_FILE"
  fi
}
trap cleanup EXIT

usage() {
  cat <<'EOF'
用法：sudo ./deploy/production/install.sh [选项]

选项：
  --install-dir PATH    安装目录，必填且不提供默认值
  --http-port PORT      Web/API 访问端口，未传入时交互输入
  --http-bind ADDRESS   监听地址，默认 0.0.0.0
  --postgres-port PORT  PostgreSQL 本机端口，默认 5432
  --service-user USER   运行服务的 macOS 用户，默认使用 sudo 发起用户
  --backup-dir PATH     安装目录外备份目标；配置后每日 03:00 自动备份
  --no-start            完成安装但不启动服务
  -h, --help            显示帮助

安装目录输入为空时立即终止。数据库、原始数据、日志、备份、配置和应用环境
全部位于用户指定的安装目录。
EOF
}

validate_port() {
  local name="$1"
  local value="$2"
  [[ "$value" =~ ^[0-9]+$ ]] || fail "$name 必须是数字"
  (( value >= 1 && value <= 65535 )) || fail "$name 必须在 1-65535 之间"
}

find_executable() {
  local name="$1"
  local service_home="$2"
  local candidate
  for candidate in \
    "$(command -v "$name" 2>/dev/null || true)" \
    "$service_home/.local/bin/$name" \
    "/opt/homebrew/bin/$name" \
    "/usr/local/bin/$name"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return
    fi
  done
  return 1
}

write_env_value() {
  local key="$1"
  local value="$2"
  printf '%s=' "$key"
  printf '%q' "$value"
  printf '\n'
}

write_launchd_plist() {
  local service="$1"
  local label="com.stockdatasync.$service"
  local plist="$INSTALL_DIR/config/launchd/$label.plist"
  local stdout_log="$INSTALL_DIR/logs/$service/launchd.out.log"
  local stderr_log="$INSTALL_DIR/logs/$service/launchd.err.log"

  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$INSTALL_DIR/bin/run-service</string>
    <string>$service</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$INSTALL_DIR</string>
  <key>UserName</key>
  <string>$SERVICE_USER</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>$stdout_log</string>
  <key>StandardErrorPath</key>
  <string>$stderr_log</string>
</dict>
</plist>
EOF
}

write_backup_launchd_plist() {
  local label="com.stockdatasync.backup"
  local plist="$INSTALL_DIR/config/launchd/$label.plist"

  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$INSTALL_DIR/bin/run-service</string>
    <string>backup</string>
    <string>$BACKUP_DIR</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$INSTALL_DIR</string>
  <key>UserName</key>
  <string>$SERVICE_USER</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>3</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>$INSTALL_DIR/logs/backup/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>$INSTALL_DIR/logs/backup/launchd.err.log</string>
</dict>
</plist>
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
    --http-bind)
      (( $# >= 2 )) || fail "--http-bind 缺少地址"
      HTTP_BIND="$2"
      shift 2
      ;;
    --postgres-port)
      (( $# >= 2 )) || fail "--postgres-port 缺少端口"
      POSTGRES_PORT="$2"
      shift 2
      ;;
    --service-user)
      (( $# >= 2 )) || fail "--service-user 缺少用户名"
      SERVICE_USER="$2"
      shift 2
      ;;
    --backup-dir)
      (( $# >= 2 )) || fail "--backup-dir 缺少路径"
      BACKUP_DIR="$2"
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

[[ "$(uname -s)" == "Darwin" ]] || fail "生产安装器仅支持 macOS"
(( EUID == 0 )) || fail "请使用 sudo 执行安装"

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
  read -r -p "请输入 Web/API 访问端口（无默认值）: " HTTP_PORT
fi
validate_port "Web/API 端口" "$HTTP_PORT"
validate_port "PostgreSQL 端口" "$POSTGRES_PORT"
[[ "$HTTP_BIND" =~ ^[A-Za-z0-9.:_-]+$ ]] || fail "监听地址格式不正确"
if [[ -n "$BACKUP_DIR" ]]; then
  [[ "$BACKUP_DIR" == /* ]] || fail "备份目录必须是绝对路径"
  [[ "$BACKUP_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]] || \
    fail "备份目录只能包含字母、数字、点、下划线、短横线和斜杠"
  [[ "$BACKUP_DIR" != "$INSTALL_DIR" && "$BACKUP_DIR" != "$INSTALL_DIR/"* ]] || \
    fail "备份目录不能位于安装目录内部"
fi

[[ -n "$SERVICE_USER" ]] || fail "无法确定服务用户，请传入 --service-user"
[[ "$SERVICE_USER" =~ ^[A-Za-z0-9._-]+$ ]] || fail "服务用户名格式不正确"
id "$SERVICE_USER" >/dev/null 2>&1 || fail "服务用户不存在：$SERVICE_USER"
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
SERVICE_HOME="$(dscl . -read "/Users/$SERVICE_USER" NFSHomeDirectory | awk '{print $2}')"
[[ -d "$SERVICE_HOME" ]] || fail "服务用户主目录不存在：$SERVICE_HOME"

if [[ -d "$INSTALL_DIR" ]]; then
  shopt -s nullglob dotglob
  existing_files=("$INSTALL_DIR"/*)
  shopt -u nullglob dotglob
  (( ${#existing_files[@]} == 0 )) || fail "安装目录不是空目录：$INSTALL_DIR"
fi

for command_name in openssl tar plutil launchctl; do
  command -v "$command_name" >/dev/null 2>&1 || fail "缺少命令：$command_name"
done

UV_BIN="$(find_executable uv "$SERVICE_HOME" || true)"
[[ -n "$UV_BIN" ]] || fail "未安装 uv，请先执行：brew install uv"
BREW_BIN="$(find_executable brew "$SERVICE_HOME" || true)"
[[ -n "$BREW_BIN" ]] || fail "未安装 Homebrew"
POSTGRES_PREFIX="$(/usr/bin/sudo -u "$SERVICE_USER" -H "$BREW_BIN" --prefix postgresql@16 2>/dev/null || true)"
[[ -n "$POSTGRES_PREFIX" ]] || fail "未安装 PostgreSQL 16，请先执行：brew install postgresql@16"
POSTGRES_BIN_DIR="$POSTGRES_PREFIX/bin"
[[ -x "$POSTGRES_BIN_DIR/postgres" ]] || fail "未安装 PostgreSQL 16，请先执行：brew install postgresql@16"
"$POSTGRES_BIN_DIR/postgres" --version | grep -q ' 16\.' || fail "必须使用 PostgreSQL 16"

[[ -d "$PROJECT_ROOT/src/server" ]] || fail "发布包缺少 src/server"
[[ -f "$PROJECT_ROOT/src/web/dist/index.html" ]] || fail "发布包缺少 Web 构建产物 src/web/dist"

service_labels=(com.stockdatasync.postgres com.stockdatasync.server com.stockdatasync.scheduler)
[[ -n "$BACKUP_DIR" ]] && service_labels+=(com.stockdatasync.backup)
for label in "${service_labels[@]}"; do
  launchctl print "system/$label" >/dev/null 2>&1 && fail "服务已经注册：$label"
  [[ ! -e "/Library/LaunchDaemons/$label.plist" ]] || fail "launchd 配置已经存在：$label"
done

if command -v lsof >/dev/null 2>&1; then
  lsof -nP -iTCP:"$HTTP_PORT" -sTCP:LISTEN >/dev/null 2>&1 && fail "Web/API 端口已被占用：$HTTP_PORT"
  lsof -nP -iTCP:"$POSTGRES_PORT" -sTCP:LISTEN >/dev/null 2>&1 && fail "PostgreSQL 端口已被占用：$POSTGRES_PORT"
fi

TUSHARE_VALUE="${TUSHARE_TOKEN:-}"
if [[ -z "$TUSHARE_VALUE" && -t 0 ]]; then
  read -r -s -p "请输入 Tushare Token（可留空，安装后再配置）: " TUSHARE_VALUE
  printf '\n'
fi
[[ "$TUSHARE_VALUE" != *$'\n'* ]] || fail "Tushare Token 不能包含换行"

POSTGRES_PASSWORD="$(openssl rand -hex 24)"
ADMIN_API_TOKEN="$(openssl rand -hex 32)"
if [[ -f "$PROJECT_ROOT/VERSION" ]]; then
  APP_VERSION="$(tr -d '[:space:]' < "$PROJECT_ROOT/VERSION")"
else
  APP_VERSION="dev-$(date -u +%Y%m%d%H%M%S)"
fi
[[ "$APP_VERSION" =~ ^[A-Za-z0-9._-]+$ ]] || fail "发布版本号格式不正确"

install -d -m 0755 \
  "$INSTALL_DIR/app/server" \
  "$INSTALL_DIR/app/web" \
  "$INSTALL_DIR/backups" \
  "$INSTALL_DIR/bin" \
  "$INSTALL_DIR/config/launchd" \
  "$INSTALL_DIR/data/postgres" \
  "$INSTALL_DIR/data/raw" \
  "$INSTALL_DIR/logs/backup" \
  "$INSTALL_DIR/logs/postgres" \
  "$INSTALL_DIR/logs/scheduler" \
  "$INSTALL_DIR/logs/server"
if [[ -n "$BACKUP_DIR" ]]; then
  install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0750 "$BACKUP_DIR"
fi

tar -C "$PROJECT_ROOT/src/server" \
  --exclude='.venv' \
  --exclude='.pytest_cache' \
  --exclude='.ruff_cache' \
  --exclude='.mypy_cache' \
  --exclude='**/__pycache__' \
  --exclude='tests' \
  -cf - . \
  | tar -C "$INSTALL_DIR/app/server" -xf -
tar -C "$PROJECT_ROOT/src/web/dist" -cf - . | tar -C "$INSTALL_DIR/app/web" -xf -

install -m 0755 "$SCRIPT_DIR/bin/stock-data-sync" "$INSTALL_DIR/bin/stock-data-sync"
install -m 0755 "$SCRIPT_DIR/bin/run-service" "$INSTALL_DIR/bin/run-service"

umask 077
{
  write_env_value INSTALL_DIR "$INSTALL_DIR"
  write_env_value APP_VERSION "$APP_VERSION"
  write_env_value SERVICE_USER "$SERVICE_USER"
  write_env_value APP_ENV production
  write_env_value APP_NAME "Stock Data Sync"
  write_env_value APP_DEBUG false
  write_env_value APP_API_PREFIX /api/v1
  write_env_value APP_CORS_ORIGINS '[]'
  write_env_value APP_LOG_MAX_BYTES 52428800
  write_env_value APP_LOG_BACKUP_COUNT 10
  write_env_value HTTP_BIND "$HTTP_BIND"
  write_env_value HTTP_PORT "$HTTP_PORT"
  write_env_value POSTGRES_BIN_DIR "$POSTGRES_BIN_DIR"
  write_env_value POSTGRES_PORT "$POSTGRES_PORT"
  write_env_value POSTGRES_DB stock_data_sync
  write_env_value POSTGRES_USER stock_sync
  write_env_value POSTGRES_PASSWORD "$POSTGRES_PASSWORD"
  write_env_value DATABASE_URL "postgresql+psycopg://stock_sync:$POSTGRES_PASSWORD@127.0.0.1:$POSTGRES_PORT/stock_data_sync"
  write_env_value RAW_DATA_DIR "$INSTALL_DIR/data/raw"
  write_env_value RAW_STORAGE_WARNING_USED_PERCENT 85
  write_env_value RAW_STORAGE_PROTECT_USED_PERCENT 92
  write_env_value RAW_STORAGE_WARNING_FREE_BYTES 21474836480
  write_env_value RAW_STORAGE_PROTECT_FREE_BYTES 10737418240
  write_env_value WEB_DIST_DIR "$INSTALL_DIR/app/web"
  write_env_value BACKUP_TARGET_DIR "$BACKUP_DIR"
  write_env_value TUSHARE_TOKEN "$TUSHARE_VALUE"
  write_env_value ADMIN_API_TOKEN "$ADMIN_API_TOKEN"
  write_env_value TUSHARE_REQUEST_LIMIT_PER_MINUTE 500
  write_env_value TUSHARE_REQUEST_BUDGET_PER_MINUTE 480
  write_env_value TUSHARE_TIMEOUT_SECONDS 30
  write_env_value TUSHARE_MAX_ATTEMPTS 3
  write_env_value TUSHARE_RETRY_WAIT_SECONDS 2
  write_env_value SCHEDULER_TIMEZONE Asia/Shanghai
  write_env_value SCHEDULER_JOBSTORE_TABLE apscheduler_jobs
  write_env_value SCHEDULER_ADVISORY_LOCK_ID 731500001
  write_env_value PROCESSING_ADVISORY_LOCK_ID 731500002
  write_env_value SCHEDULER_MAX_WORKERS 4
  write_env_value SCHEDULER_POLL_SECONDS 30
  write_env_value PARTITION_MONTHS_AHEAD 3
  write_env_value COLLECTION_MAX_WORKERS 4
  write_env_value COLLECTION_RUNNING_TIMEOUT_SECONDS 1800
  write_env_value PROCESSING_RUNNING_TIMEOUT_SECONDS 21600
} > "$INSTALL_DIR/config/app.env"

chown root:wheel "$INSTALL_DIR"
chown -R "$SERVICE_USER:$SERVICE_GROUP" \
  "$INSTALL_DIR/app" \
  "$INSTALL_DIR/backups" \
  "$INSTALL_DIR/data" \
  "$INSTALL_DIR/logs"
chown -R root:wheel "$INSTALL_DIR/bin" "$INSTALL_DIR/config"
chmod 0600 "$INSTALL_DIR/config/app.env"
chmod +a "$SERVICE_USER allow read" "$INSTALL_DIR/config/app.env"

/usr/bin/sudo -u "$SERVICE_USER" -H \
  "$UV_BIN" --directory "$INSTALL_DIR/app/server" sync \
  --frozen --no-dev --no-install-project

PASSWORD_FILE="$INSTALL_DIR/config/.postgres-password"
printf '%s\n' "$POSTGRES_PASSWORD" > "$PASSWORD_FILE"
chown "$SERVICE_USER:$SERVICE_GROUP" "$PASSWORD_FILE"
chmod 0600 "$PASSWORD_FILE"
/usr/bin/sudo -u "$SERVICE_USER" -H \
  "$POSTGRES_BIN_DIR/initdb" \
  --pgdata="$INSTALL_DIR/data/postgres" \
  --encoding=UTF8 \
  --username=stock_sync \
  --pwfile="$PASSWORD_FILE" \
  --auth-local=scram-sha-256 \
  --auth-host=scram-sha-256
rm -f -- "$PASSWORD_FILE"
PASSWORD_FILE=""

write_launchd_plist postgres
write_launchd_plist server
write_launchd_plist scheduler
[[ -n "$BACKUP_DIR" ]] && write_backup_launchd_plist
chown -R root:wheel "$INSTALL_DIR/config/launchd"
chmod 0644 "$INSTALL_DIR/config/launchd"/*.plist
plutil -lint "$INSTALL_DIR/config/launchd"/*.plist >/dev/null

install_services=(postgres server scheduler)
[[ -n "$BACKUP_DIR" ]] && install_services+=(backup)
for service in "${install_services[@]}"; do
  install -o root -g wheel -m 0644 \
    "$INSTALL_DIR/config/launchd/com.stockdatasync.$service.plist" \
    "/Library/LaunchDaemons/com.stockdatasync.$service.plist"
done

if (( START_AFTER_INSTALL == 1 )); then
  "$INSTALL_DIR/bin/stock-data-sync" start
fi

printf '\n安装完成。\n'
printf '安装目录：%s\n' "$INSTALL_DIR"
printf '服务用户：%s\n' "$SERVICE_USER"
printf '管理命令：sudo %s/bin/stock-data-sync\n' "$INSTALL_DIR"
printf '访问地址：http://<Mac-mini-IP>:%s\n' "$HTTP_PORT"
