#!/usr/bin/env bash
set -Eeuo pipefail

# GitHub release workflow replaces this placeholder in the published installer asset.
DEFAULT_REPOSITORY="__STOCK_DATA_SYNC_REPOSITORY__"
DEFAULT_VERSION="__STOCK_DATA_SYNC_VERSION__"
UNSET_REPOSITORY="__STOCK_DATA_SYNC_"'REPOSITORY__'
UNSET_VERSION="__STOCK_DATA_SYNC_"'VERSION__'
PROGRAM_DIR=""
DATA_DIR=""
HTTP_PORT=""
HTTP_BIND=""
POSTGRES_PORT=""
REPOSITORY="${STOCK_DATA_SYNC_REPOSITORY:-$DEFAULT_REPOSITORY}"
VERSION="${STOCK_DATA_SYNC_VERSION:-$DEFAULT_VERSION}"
BOOTSTRAP_DIR=""

fail() {
  printf '安装失败：%s\n' "$*" >&2
  exit 1
}

validate_port() {
  local name="$1" value="$2"
  [[ "$value" =~ ^[0-9]{1,5}$ ]] && (( 10#$value >= 1 && 10#$value <= 65535 )) || \
    fail "$name 必须在 1-65535 之间"
}

validate_ipv4() {
  local value="$1" octet
  local -a octets
  [[ "$value" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || \
    fail "Web/API 监听 IP 必须是 IPv4 地址"
  IFS=. read -r -a octets <<< "$value"
  for octet in "${octets[@]}"; do
    [[ "$octet" =~ ^[0-9]+$ ]] && (( 10#$octet <= 255 )) || \
      fail "Web/API 监听 IP 无效：$value"
  done
}

cleanup() {
  if [[ -n "$BOOTSTRAP_DIR" && -d "$BOOTSTRAP_DIR" ]]; then
    rm -rf -- "$BOOTSTRAP_DIR"
  fi
}
trap cleanup EXIT

usage() {
  cat <<'EOF'
用法：
  curl -fsSL RELEASE_INSTALLER_URL | sudo bash -s -- \
    --program-dir PATH --data-dir PATH --http-bind IPv4 --http-port PORT \
    --postgres-port PORT [选项]

必填：
  --program-dir PATH    主程序、源码和配置目录；必须位于启用 ownership 的磁盘
  --data-dir PATH       PostgreSQL、行情数据和日志目录；允许外接盘关闭 ownership
  --http-bind IPv4      Web/API 监听 IPv4，例如 127.0.0.1 或 0.0.0.0
  --http-port PORT      Web/API 监听端口，范围 1-65535
  --postgres-port PORT  PostgreSQL 本机监听端口，范围 1-65535

选项：
  --repository URL     Git 仓库地址；正式 GitHub Release 安装器已内置
  --version VERSION    安装指定 vX.Y.Z；默认选择仓库最新稳定标签
  --service-user USER  服务用户，默认使用发起 sudo 的用户
  --backup-dir PATH    可选的外部每日备份目录
  --no-start           安装和初始化后暂不启动应用服务
  -h, --help           显示帮助

安装器只从不可变的 vX.Y.Z 标签取源码，在目标 Mac 本地构建。已有安装请执行：
  sudo stock-data-sync upgrade
EOF
}

forwarded=()
while (( $# > 0 )); do
  case "$1" in
    --program-dir)
      (( $# >= 2 )) || fail "--program-dir 缺少路径"
      PROGRAM_DIR="$2"
      forwarded+=("$1" "$2")
      shift 2
      ;;
    --data-dir)
      (( $# >= 2 )) || fail "--data-dir 缺少路径"
      DATA_DIR="$2"
      forwarded+=("$1" "$2")
      shift 2
      ;;
    --repository)
      (( $# >= 2 )) || fail "--repository 缺少地址"
      REPOSITORY="$2"
      shift 2
      ;;
    --version)
      (( $# >= 2 )) || fail "--version 缺少版本"
      VERSION="$2"
      shift 2
      ;;
    --http-port)
      (( $# >= 2 )) || fail "--http-port 缺少参数"
      HTTP_PORT="$2"
      forwarded+=("$1" "$2")
      shift 2
      ;;
    --http-bind)
      (( $# >= 2 )) || fail "--http-bind 缺少参数"
      HTTP_BIND="$2"
      forwarded+=("$1" "$2")
      shift 2
      ;;
    --postgres-port)
      (( $# >= 2 )) || fail "--postgres-port 缺少参数"
      POSTGRES_PORT="$2"
      forwarded+=("$1" "$2")
      shift 2
      ;;
    --service-user|--backup-dir)
      (( $# >= 2 )) || fail "$1 缺少参数"
      forwarded+=("$1" "$2")
      shift 2
      ;;
    --no-start)
      forwarded+=("$1")
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

[[ -n "$PROGRAM_DIR" ]] || fail "首次安装必须传入 --program-dir"
[[ -n "$DATA_DIR" ]] || fail "首次安装必须传入 --data-dir"
[[ -n "$HTTP_BIND" ]] || fail "首次安装必须传入 --http-bind"
[[ -n "$HTTP_PORT" ]] || fail "首次安装必须传入 --http-port"
[[ -n "$POSTGRES_PORT" ]] || fail "首次安装必须传入 --postgres-port"
[[ "$PROGRAM_DIR" == /* && "$PROGRAM_DIR" != "/" ]] || fail "主程序目录必须是非根目录的绝对路径"
[[ "$DATA_DIR" == /* && "$DATA_DIR" != "/" ]] || fail "数据目录必须是非根目录的绝对路径"
[[ "$PROGRAM_DIR" != *$'\n'* && "$PROGRAM_DIR" != *$'\r'* ]] || fail "主程序目录不能包含换行"
[[ "$DATA_DIR" != *$'\n'* && "$DATA_DIR" != *$'\r'* ]] || fail "数据目录不能包含换行"
[[ "$PROGRAM_DIR" != */ && "$PROGRAM_DIR" != *//* && "$PROGRAM_DIR" != */./* && "$PROGRAM_DIR" != */. && "$PROGRAM_DIR" != */../* && "$PROGRAM_DIR" != */.. ]] || fail "主程序目录必须使用规范绝对路径"
[[ "$DATA_DIR" != */ && "$DATA_DIR" != *//* && "$DATA_DIR" != */./* && "$DATA_DIR" != */. && "$DATA_DIR" != */../* && "$DATA_DIR" != */.. ]] || fail "数据目录必须使用规范绝对路径"
[[ "$PROGRAM_DIR" != "$DATA_DIR" && "$PROGRAM_DIR" != "$DATA_DIR/"* && "$DATA_DIR" != "$PROGRAM_DIR/"* ]] || \
  fail "主程序目录与数据目录必须相互独立"
validate_ipv4 "$HTTP_BIND"
validate_port "Web/API 端口" "$HTTP_PORT"
validate_port "PostgreSQL 端口" "$POSTGRES_PORT"
[[ "$HTTP_PORT" != "$POSTGRES_PORT" ]] || fail "Web/API 与 PostgreSQL 端口不能相同"
(( EUID == 0 )) || fail "请使用 sudo 执行安装"
[[ "$(uname -s)" == "Darwin" ]] || fail "生产安装器仅支持 macOS"
command -v git >/dev/null 2>&1 || fail "缺少 Git"
command -v tar >/dev/null 2>&1 || fail "缺少 tar"

if [[ "$REPOSITORY" == "$UNSET_REPOSITORY" || -z "$REPOSITORY" ]]; then
  fail "当前安装器未内置 GitHub 仓库地址，请传入 --repository"
fi
[[ "$VERSION" != "$UNSET_VERSION" ]] || VERSION=""
[[ "$REPOSITORY" != *$'\n'* && "$REPOSITORY" != *$'\r'* ]] || fail "Git 仓库地址不能包含换行"

BOOTSTRAP_DIR="$(mktemp -d)"
MIRROR="$BOOTSTRAP_DIR/repository.git"
SOURCE_DIR="$BOOTSTRAP_DIR/source"
git clone --quiet --mirror -- "$REPOSITORY" "$MIRROR" || fail "无法克隆 Git 仓库：$REPOSITORY"

if [[ -n "$VERSION" ]]; then
  tag="$VERSION"
  [[ "$tag" == v* ]] || tag="v$tag"
else
  tag="$(git --git-dir="$MIRROR" tag --list 'v[0-9]*' --sort=-v:refname | \
    grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -n 1 || true)"
fi
[[ -n "$tag" ]] || fail "仓库中没有可安装的 vX.Y.Z 标签"
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9._-]+)?$ ]] || fail "版本标签格式无效：$tag"
git --git-dir="$MIRROR" rev-parse --verify --quiet "refs/tags/$tag^{commit}" >/dev/null || \
  fail "仓库中不存在版本标签：$tag"

mkdir -p "$SOURCE_DIR"
git --git-dir="$MIRROR" archive "$tag^{commit}" | tar -C "$SOURCE_DIR" -xf -
LOCAL_INSTALLER="$SOURCE_DIR/deploy/production/install-local.sh"
[[ -x "$LOCAL_INSTALLER" || -f "$LOCAL_INSTALLER" ]] || fail "目标版本缺少 install-local.sh"

bash "$LOCAL_INSTALLER" \
  "${forwarded[@]}" \
  --repository "$REPOSITORY" \
  --version "$tag" \
  --bootstrap-mirror "$MIRROR"
