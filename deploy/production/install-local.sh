#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/deploy-common.sh
source "$SCRIPT_DIR/lib/deploy-common.sh"

PROGRAM_DIR=""
DATA_DIR=""
REPOSITORY=""
VERSION=""
BOOTSTRAP_MIRROR=""
HTTP_PORT=""
HTTP_BIND=""
POSTGRES_PORT=""
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

while (( $# > 0 )); do
  case "$1" in
    --program-dir) PROGRAM_DIR="$2"; shift 2 ;;
    --data-dir) DATA_DIR="$2"; shift 2 ;;
    --repository) REPOSITORY="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --bootstrap-mirror) BOOTSTRAP_MIRROR="$2"; shift 2 ;;
    --http-port) HTTP_PORT="$2"; shift 2 ;;
    --http-bind) HTTP_BIND="$2"; shift 2 ;;
    --postgres-port) POSTGRES_PORT="$2"; shift 2 ;;
    --service-user) SERVICE_USER="$2"; shift 2 ;;
    --backup-dir) BACKUP_DIR="$2"; shift 2 ;;
    --no-start) START_AFTER_INSTALL=0; shift ;;
    *) fail "未知参数：$1" ;;
  esac
done

doctor_pass() { printf 'PASS  %s\n' "$*"; }
doctor_warn() { printf 'WARN  %s\n' "$*" >&2; }
doctor_fail() { printf 'FAIL  %s\n' "$*" >&2; DOCTOR_FAILURES=$((DOCTOR_FAILURES + 1)); }

pre_install_doctor() {
  local service_home="$1"
  local brew_bin postgres_prefix node_bin npm_bin node_major program_disk_path data_disk_path available_kb
  DOCTOR_FAILURES=0
  printf '安装前检查：\n'
  [[ "$(uname -s)" == "Darwin" ]] && doctor_pass "操作系统为 macOS" || doctor_fail "仅支持 macOS"
  (( EUID == 0 )) && doctor_pass "已使用管理员权限" || doctor_fail "必须使用 sudo"
  [[ -n "$PROGRAM_DIR" && "$PROGRAM_DIR" == /* && "$PROGRAM_DIR" != "/" ]] && \
    doctor_pass "主程序目录由用户明确指定" || doctor_fail "主程序目录无效"
  [[ -n "$DATA_DIR" && "$DATA_DIR" == /* && "$DATA_DIR" != "/" ]] && \
    doctor_pass "数据目录由用户明确指定" || doctor_fail "数据目录无效"
  if [[ -d "$PROGRAM_DIR" ]]; then
    shopt -s nullglob dotglob
    local existing=("$PROGRAM_DIR"/*)
    shopt -u nullglob dotglob
    (( ${#existing[@]} == 0 )) && doctor_pass "主程序目录为空" || doctor_fail "主程序目录不是空目录"
  else
    doctor_pass "主程序目录尚未使用"
  fi
  if [[ -d "$DATA_DIR" ]]; then
    shopt -s nullglob dotglob
    local data_existing=("$DATA_DIR"/*)
    shopt -u nullglob dotglob
    (( ${#data_existing[@]} == 0 )) && doctor_pass "数据目录为空" || doctor_fail "数据目录不是空目录"
  else
    doctor_pass "数据目录尚未使用"
  fi
  [[ -n "$SERVICE_USER" ]] && id "$SERVICE_USER" >/dev/null 2>&1 && \
    doctor_pass "服务用户存在：$SERVICE_USER" || doctor_fail "服务用户不存在"
  [[ -n "$service_home" && -d "$service_home" ]] && doctor_pass "服务用户主目录可用" || doctor_fail "服务用户主目录不可用"
  for command_name in git uv openssl tar plutil launchctl curl; do
    deploy_find_executable "$command_name" "$service_home" >/dev/null 2>&1 && \
      doctor_pass "依赖可用：$command_name" || doctor_fail "缺少依赖：$command_name"
  done
  node_bin="$(deploy_find_node_runtime "$service_home" || true)"
  npm_bin="${node_bin:+$(dirname "$node_bin")/npm}"
  if [[ -n "$node_bin" && -x "$npm_bin" ]]; then
    node_major="$("$node_bin" -p 'process.versions.node.split(".")[0]')"
    (( 10#$node_major >= 22 )) && doctor_pass "Node.js 版本符合要求：$("$node_bin" --version)" || doctor_fail "必须使用 Node.js 22 或更高版本"
    doctor_pass "npm 与所选 Node.js 来自同一安装"
  else
    doctor_fail "缺少可用的 Node.js/npm"
  fi
  brew_bin="$(deploy_find_executable brew "$service_home" || true)"
  if [[ -n "$brew_bin" ]]; then
    postgres_prefix="$(/usr/bin/sudo -u "$SERVICE_USER" -H "$brew_bin" --prefix postgresql@18 2>/dev/null || true)"
    [[ -x "$postgres_prefix/bin/postgres" ]] && doctor_pass "PostgreSQL 18 已安装" || doctor_fail "未安装 PostgreSQL 18"
  else
    doctor_fail "未安装 Homebrew"
  fi
  deploy_validate_port "Web/API 端口" "$HTTP_PORT" >/dev/null 2>&1 || doctor_fail "Web/API 端口无效"
  deploy_validate_port "PostgreSQL 端口" "$POSTGRES_PORT" >/dev/null 2>&1 || doctor_fail "PostgreSQL 端口无效"
  deploy_validate_bind_ip "Web/API 监听 IP" "$HTTP_BIND" >/dev/null 2>&1 && \
    doctor_pass "Web/API 监听 IP 合法：$HTTP_BIND" || doctor_fail "Web/API 监听 IP 无效"
  if [[ "$HTTP_BIND" == "0.0.0.0" || "$HTTP_BIND" == "127.0.0.1" ]] || \
    /sbin/ifconfig | awk -v address="$HTTP_BIND" '$1 == "inet" && $2 == address {found=1} END {exit !found}'; then
    doctor_pass "Web/API 监听 IP 可在本机使用"
  else
    doctor_fail "Web/API 监听 IP 不属于本机：$HTTP_BIND"
  fi
  [[ "$HTTP_PORT" != "$POSTGRES_PORT" ]] && doctor_pass "应用与数据库端口不同" || doctor_fail "应用与数据库端口不能相同"
  program_disk_path="$(deploy_existing_parent "$PROGRAM_DIR")"
  data_disk_path="$(deploy_existing_parent "$DATA_DIR")"
  available_kb="$(df -Pk "$program_disk_path" | awk 'NR == 2 {print $4}')"
  [[ "$available_kb" =~ ^[0-9]+$ && "$available_kb" -ge 5242880 ]] && \
    doctor_pass "主程序磁盘至少有 5 GiB 可用空间" || doctor_fail "主程序磁盘可用空间不足 5 GiB"
  available_kb="$(df -Pk "$data_disk_path" | awk 'NR == 2 {print $4}')"
  [[ "$available_kb" =~ ^[0-9]+$ && "$available_kb" -ge 5242880 ]] && \
    doctor_pass "数据磁盘至少有 5 GiB 可用空间" || doctor_fail "数据磁盘可用空间不足 5 GiB"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$HTTP_PORT" -sTCP:LISTEN >/dev/null 2>&1 && doctor_fail "Web/API 端口已占用：$HTTP_PORT" || doctor_pass "Web/API 端口可用"
    lsof -nP -iTCP:"$POSTGRES_PORT" -sTCP:LISTEN >/dev/null 2>&1 && doctor_fail "PostgreSQL 端口已占用：$POSTGRES_PORT" || doctor_pass "PostgreSQL 端口可用"
  fi
  for label in com.stockdatasync.postgres com.stockdatasync.server com.stockdatasync.scheduler com.stockdatasync.backup; do
    if launchctl print "system/$label" >/dev/null 2>&1 || [[ -e "/Library/LaunchDaemons/$label.plist" ]]; then
      doctor_fail "服务已经注册：$label"
    fi
  done
  [[ ! -e /usr/local/bin/stock-data-sync ]] && doctor_pass "全局命令入口可创建" || doctor_fail "/usr/local/bin/stock-data-sync 已存在"
  [[ ! -e /usr/local/libexec/stock-data-sync ]] && doctor_pass "系统服务入口可创建" || doctor_fail "/usr/local/libexec/stock-data-sync 已存在"
  (( DOCTOR_FAILURES == 0 )) || fail "安装前 doctor 检查失败"
}

deploy_validate_absolute_path "主程序目录" "$PROGRAM_DIR" || exit 1
deploy_validate_absolute_path "数据目录" "$DATA_DIR" || exit 1
[[ "$PROGRAM_DIR" != "$DATA_DIR" && "$PROGRAM_DIR" != "$DATA_DIR/"* && "$DATA_DIR" != "$PROGRAM_DIR/"* ]] || \
  fail "主程序目录与数据目录必须相互独立"
deploy_validate_bind_ip "Web/API 监听 IP" "$HTTP_BIND" || exit 1
deploy_validate_port "Web/API 端口" "$HTTP_PORT" || exit 1
deploy_validate_port "PostgreSQL 端口" "$POSTGRES_PORT" || exit 1
[[ "$HTTP_PORT" != "$POSTGRES_PORT" ]] || fail "Web/API 与 PostgreSQL 端口不能相同"
[[ -n "$REPOSITORY" && -n "$VERSION" && -d "$BOOTSTRAP_MIRROR" ]] || fail "缺少源码安装上下文"
[[ -n "$SERVICE_USER" ]] || fail "无法确定服务用户，请传入 --service-user"
id "$SERVICE_USER" >/dev/null 2>&1 || fail "服务用户不存在：$SERVICE_USER"
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
SERVICE_HOME="$(deploy_service_home "$SERVICE_USER")" || exit 1
if [[ -n "$BACKUP_DIR" ]]; then
  deploy_validate_absolute_path "备份目录" "$BACKUP_DIR" || exit 1
  [[ "$BACKUP_DIR" != "$DATA_DIR" && "$BACKUP_DIR" != "$DATA_DIR/"* ]] || fail "外部备份目录不能位于数据目录内部"
fi

pre_install_doctor "$SERVICE_HOME"

BREW_BIN="$(deploy_find_executable brew "$SERVICE_HOME")"
UV_BIN="$(deploy_find_executable uv "$SERVICE_HOME")"
POSTGRES_PREFIX="$(/usr/bin/sudo -u "$SERVICE_USER" -H "$BREW_BIN" --prefix postgresql@18)"
POSTGRES_BIN_DIR="$POSTGRES_PREFIX/bin"
"$POSTGRES_BIN_DIR/postgres" --version | grep -q ' 18\.' || fail "必须使用 PostgreSQL 18"

install -d -o root -g wheel -m 0755 "$PROGRAM_DIR"
[[ "$(stat -f '%Su' "$PROGRAM_DIR")" == "root" ]] || fail "主程序目录无法设置为 root 所有，请选择已启用 ownership 的磁盘"
install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0755 "$DATA_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0755 \
  "$DATA_DIR/data/postgres" "$DATA_DIR/data/raw" \
  "$DATA_DIR/logs/postgres" "$DATA_DIR/logs/server" "$DATA_DIR/logs/scheduler" "$DATA_DIR/logs/backup" \
  "$DATA_DIR/backups"
install -d -o root -g wheel -m 0755 \
  "$PROGRAM_DIR/bin" "$PROGRAM_DIR/config" "$PROGRAM_DIR/config/launchd" \
  "$PROGRAM_DIR/source" "$PROGRAM_DIR/releases" "$PROGRAM_DIR/.build"
install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0755 "$PROGRAM_DIR/logs/launchd"
if [[ -n "$BACKUP_DIR" ]]; then
  install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0750 "$BACKUP_DIR"
fi

git clone --quiet --mirror "$BOOTSTRAP_MIRROR" "$PROGRAM_DIR/source/repository.git"
git --git-dir="$PROGRAM_DIR/source/repository.git" remote set-url origin "$REPOSITORY"
chown -R root:wheel "$PROGRAM_DIR/source/repository.git"
chmod -R go-w "$PROGRAM_DIR/source/repository.git"
TAG="$(deploy_resolve_tag "$PROGRAM_DIR/source/repository.git" "$VERSION")"
deploy_prepare_release \
  "$PROGRAM_DIR" "$SERVICE_USER" "$SERVICE_GROUP" "$SERVICE_HOME" \
  "$PROGRAM_DIR/source/repository.git" "$TAG" "$REPOSITORY"
RELEASE_DIR="$BUILT_RELEASE_DIR"
APP_VERSION="$BUILT_RELEASE_VERSION"

POSTGRES_PASSWORD="$(openssl rand -hex 24)"
ADMIN_API_TOKEN="$(openssl rand -hex 32)"
ORIGINAL_UMASK="$(umask)"
umask 077
{
  printf '# Stock Data Sync 生产配置。修改后执行：sudo stock-data-sync config validate\n'
  printf '# 每个配置项上方标注了含义和是否允许用户修改。\n\n'
  printf '# =============================================================================\n# 安装与应用基础配置\n# =============================================================================\n\n'
  printf '# 用户首次指定的主程序、源码与配置目录。[安装器维护，请勿修改]\n'; deploy_write_env_value PROGRAM_DIR "$PROGRAM_DIR"
  printf '# 用户首次指定的数据、日志与备份目录。[安装器维护，请勿修改]\n'; deploy_write_env_value DATA_DIR "$DATA_DIR"
  printf '# 应用内部使用的数据根目录，保持与 DATA_DIR 一致。[安装器维护，请勿修改]\n'; deploy_write_env_value INSTALL_DIR "$DATA_DIR"
  printf '# 运行服务的 macOS 用户。[安装器维护，请勿修改]\n'; deploy_write_env_value SERVICE_USER "$SERVICE_USER"
  printf '# 源码 Git 仓库地址，升级只拉取正式版本标签。[安装器维护，请勿修改]\n'; deploy_write_env_value SOURCE_REPOSITORY "$REPOSITORY"
  printf '# 应用运行环境。[安装器维护，请勿修改]\n'; deploy_write_env_value APP_ENV production
  printf '# 应用显示名称。[用户可修改]\n'; deploy_write_env_value APP_NAME "Stock Data Sync"
  printf '# 是否启用调试模式，生产环境应保持 false。[用户可修改]\n'; deploy_write_env_value APP_DEBUG false
  printf '# API 路径前缀。[一般不要修改]\n'; deploy_write_env_value APP_API_PREFIX /api/v1
  printf '# 允许跨域的来源，使用 JSON 数组。[用户可修改]\n'; deploy_write_env_value APP_CORS_ORIGINS '[]'
  printf '# 单个应用日志文件最大字节数。[用户可修改]\n'; deploy_write_env_value APP_LOG_MAX_BYTES 52428800
  printf '# 应用日志滚动保留数量。[用户可修改]\n'; deploy_write_env_value APP_LOG_BACKUP_COUNT 10
  printf '\n# =============================================================================\n# Web/API 配置\n# =============================================================================\n\n'
  printf '# Web/API 监听地址；127.0.0.1 仅本机，0.0.0.0 可供局域网访问。[用户可修改]\n'; deploy_write_env_value HTTP_BIND "$HTTP_BIND"
  printf '# Web/API 监听端口，范围 1-65535。[用户可修改]\n'; deploy_write_env_value HTTP_PORT "$HTTP_PORT"
  printf '\n# =============================================================================\n# PostgreSQL 配置\n# =============================================================================\n\n'
  printf '# PostgreSQL 18 可执行文件目录。[安装器维护，请勿修改]\n'; deploy_write_env_value POSTGRES_BIN_DIR "$POSTGRES_BIN_DIR"
  printf '# PostgreSQL 仅本机监听的端口。[安装器维护，请勿修改]\n'; deploy_write_env_value POSTGRES_PORT "$POSTGRES_PORT"
  printf '# 生产数据库名称。[安装器维护，请勿修改]\n'; deploy_write_env_value POSTGRES_DB stock_data_sync
  printf '# 生产数据库用户。[安装器维护，请勿修改]\n'; deploy_write_env_value POSTGRES_USER stock_sync
  printf '# PostgreSQL 随机密码。[安装器维护，敏感信息]\n'; deploy_write_env_value POSTGRES_PASSWORD "$POSTGRES_PASSWORD"
  printf '# 应用数据库连接地址。[安装器维护，敏感信息]\n'; deploy_write_env_value DATABASE_URL "postgresql+psycopg://stock_sync:$POSTGRES_PASSWORD@127.0.0.1:$POSTGRES_PORT/stock_data_sync"
  printf '\n# =============================================================================\n# 数据、容量与前端配置\n# =============================================================================\n\n'
  printf '# 不可变 Parquet 原始资产目录。[安装器维护，请勿修改]\n'; deploy_write_env_value RAW_DATA_DIR "$DATA_DIR/data/raw"
  printf '# 磁盘使用率达到该百分比时告警。[用户可修改]\n'; deploy_write_env_value RAW_STORAGE_WARNING_USED_PERCENT 85
  printf '# 磁盘使用率达到该百分比时停止新增采集。[用户可修改]\n'; deploy_write_env_value RAW_STORAGE_PROTECT_USED_PERCENT 92
  printf '# 剩余空间低于该字节数时告警。[用户可修改]\n'; deploy_write_env_value RAW_STORAGE_WARNING_FREE_BYTES 21474836480
  printf '# 剩余空间低于该字节数时停止新增采集。[用户可修改]\n'; deploy_write_env_value RAW_STORAGE_PROTECT_FREE_BYTES 10737418240
  printf '# 当前版本 Web 构建目录由服务入口动态覆盖。[安装器维护，请勿修改]\n'; deploy_write_env_value WEB_DIST_DIR "$PROGRAM_DIR/current/web"
  printf '# 可选的数据目录外备份目标。[用户可修改]\n'; deploy_write_env_value BACKUP_TARGET_DIR "$BACKUP_DIR"
  printf '\n# =============================================================================\n# 访问密钥与 Tushare 配置\n# =============================================================================\n\n'
  printf '# Tushare API Token；安装后由用户填写。[用户填写，敏感信息]\n'; deploy_write_env_value TUSHARE_TOKEN ""
  printf '# 管理写接口 Bearer Token，管理页面自动读取。[安装器维护，可由管理员轮换]\n'; deploy_write_env_value ADMIN_API_TOKEN "$ADMIN_API_TOKEN"
  printf '# Tushare 每分钟供应方上限。[用户可修改]\n'; deploy_write_env_value TUSHARE_REQUEST_LIMIT_PER_MINUTE 500
  printf '# 应用每分钟请求预算，不得超过供应方上限。[用户可修改]\n'; deploy_write_env_value TUSHARE_REQUEST_BUDGET_PER_MINUTE 480
  printf '# 单次供应方请求超时秒数。[用户可修改]\n'; deploy_write_env_value TUSHARE_TIMEOUT_SECONDS 30
  printf '# 单次供应方请求最大尝试次数。[用户可修改]\n'; deploy_write_env_value TUSHARE_MAX_ATTEMPTS 3
  printf '# 供应方请求重试等待秒数。[用户可修改]\n'; deploy_write_env_value TUSHARE_RETRY_WAIT_SECONDS 2
  printf '# 默认同步的市场指数代码，使用 JSON 数组且不可重复。[用户可修改]\n'; deploy_write_env_value MARKET_INDEX_CODES '["000001.SH","399001.SZ","000016.SH","000300.SH","000905.SH","399006.SZ"]'
  printf '\n# =============================================================================\n# Scheduler 与任务并发配置\n# =============================================================================\n\n'
  printf '# 调度业务时区。[用户可修改]\n'; deploy_write_env_value SCHEDULER_TIMEZONE Asia/Shanghai
  printf '# APScheduler 数据表名称。[安装器维护，请勿修改]\n'; deploy_write_env_value SCHEDULER_JOBSTORE_TABLE apscheduler_jobs
  printf '# Scheduler 单例 advisory lock ID。[安装器维护，请勿修改]\n'; deploy_write_env_value SCHEDULER_ADVISORY_LOCK_ID 731500001
  printf '# Processing advisory lock ID。[安装器维护，请勿修改]\n'; deploy_write_env_value PROCESSING_ADVISORY_LOCK_ID 731500002
  printf '# Scheduler 最大工作线程数。[用户可修改]\n'; deploy_write_env_value SCHEDULER_MAX_WORKERS 4
  printf '# Scheduler 轮询间隔秒数。[用户可修改]\n'; deploy_write_env_value SCHEDULER_POLL_SECONDS 30
  printf '# 提前创建的月分区数量。[用户可修改]\n'; deploy_write_env_value PARTITION_MONTHS_AHEAD 3
  printf '# 采集任务最大并发数。[用户可修改]\n'; deploy_write_env_value COLLECTION_MAX_WORKERS 4
  printf '# 采集任务运行超时秒数。[用户可修改]\n'; deploy_write_env_value COLLECTION_RUNNING_TIMEOUT_SECONDS 1800
  printf '# 加工任务运行超时秒数。[用户可修改]\n'; deploy_write_env_value PROCESSING_RUNNING_TIMEOUT_SECONDS 21600
} > "$PROGRAM_DIR/config/app.env"
chown root:wheel "$PROGRAM_DIR/config/app.env"
chmod 0600 "$PROGRAM_DIR/config/app.env"
chmod +a "$SERVICE_USER allow read" "$PROGRAM_DIR/config/app.env"
umask "$ORIGINAL_UMASK"

install -o root -g wheel -m 0755 "$SCRIPT_DIR/bootstrap/installed-stock-data-sync" "$PROGRAM_DIR/bin/stock-data-sync"
install -o root -g wheel -m 0755 "$SCRIPT_DIR/bootstrap/run-service" "$PROGRAM_DIR/bin/run-service"
install -d -o root -g wheel -m 0755 /usr/local/bin
install -o root -g wheel -m 0755 "$SCRIPT_DIR/bootstrap/global-stock-data-sync" /usr/local/bin/stock-data-sync
install -d -o root -g wheel -m 0755 /usr/local/libexec
install -d -o root -g wheel -m 0755 /usr/local/libexec/stock-data-sync
install -o root -g wheel -m 0755 "$SCRIPT_DIR/bootstrap/system-run-service" /usr/local/libexec/stock-data-sync/run-service

deploy_switch_current "$PROGRAM_DIR" "$RELEASE_DIR"
deploy_write_receipt "$SERVICE_USER" "$SERVICE_GROUP" "$SERVICE_HOME" "$PROGRAM_DIR" "$DATA_DIR" "$REPOSITORY" "$APP_VERSION"

/usr/bin/sudo -u "$SERVICE_USER" test -x /usr/local/libexec/stock-data-sync/run-service || \
  fail "服务用户无法执行系统启动入口"
/usr/bin/sudo -u "$SERVICE_USER" test -x "$PROGRAM_DIR/current/deploy/production/bin/run-service" || \
  fail "服务用户无法执行当前程序入口"

PASSWORD_FILE="$PROGRAM_DIR/config/.postgres-password"
(umask 077; printf '%s\n' "$POSTGRES_PASSWORD" > "$PASSWORD_FILE")
chown "$SERVICE_USER:$SERVICE_GROUP" "$PASSWORD_FILE"
chmod 0600 "$PASSWORD_FILE"
/usr/bin/sudo -u "$SERVICE_USER" -H "$POSTGRES_BIN_DIR/initdb" \
  --pgdata="$DATA_DIR/data/postgres" --encoding=UTF8 --username=stock_sync \
  --pwfile="$PASSWORD_FILE" --auth-local=scram-sha-256 --auth-host=scram-sha-256
rm -f -- "$PASSWORD_FILE"
PASSWORD_FILE=""

"$PROGRAM_DIR/bin/stock-data-sync" install-services
"$PROGRAM_DIR/bin/stock-data-sync" start postgres
"$PROGRAM_DIR/bin/stock-data-sync" migrate

if (( START_AFTER_INSTALL == 1 )); then
  "$PROGRAM_DIR/bin/stock-data-sync" start
  "$PROGRAM_DIR/bin/stock-data-sync" doctor --phase post-install
else
  "$PROGRAM_DIR/bin/stock-data-sync" doctor --phase installed-no-start
fi

printf '\n安装完成。\n'
printf '主程序目录：%s\n' "$PROGRAM_DIR"
printf '数据目录：%s\n' "$DATA_DIR"
printf '当前版本：%s\n' "$APP_VERSION"
printf '管理命令：sudo stock-data-sync\n'
printf '配置文件：%s/config/app.env\n' "$PROGRAM_DIR"
printf '监听地址：http://%s:%s\n' "$HTTP_BIND" "$HTTP_PORT"
if [[ "$HTTP_BIND" == "0.0.0.0" ]]; then
  printf '访问地址：http://<Mac-IP>:%s\n' "$HTTP_PORT"
fi
