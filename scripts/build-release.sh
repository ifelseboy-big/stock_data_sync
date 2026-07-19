#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-}"
REPOSITORY="${2:-${STOCK_DATA_SYNC_REPOSITORY:-}}"

fail() {
  printf '发布准备失败：%s\n' "$*" >&2
  exit 1
}

[[ -n "$VERSION" ]] || fail "用法：$0 <version> [repository-url]"
VERSION="${VERSION#v}"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9._-]+)?$ ]] || fail "版本必须使用语义化版本格式"
TAG="v$VERSION"

if [[ -z "$REPOSITORY" && -n "${GITHUB_REPOSITORY:-}" ]]; then
  REPOSITORY="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY}.git"
fi
if [[ -z "$REPOSITORY" ]]; then
  REPOSITORY="$(git -C "$PROJECT_ROOT" remote get-url origin 2>/dev/null || true)"
fi
[[ -n "$REPOSITORY" ]] || fail "无法确定 Git 仓库地址，请传入 repository-url"
[[ "$REPOSITORY" != *$'\n'* && "$REPOSITORY" != *$'\r'* ]] || fail "Git 仓库地址不能包含换行"
[[ "$REPOSITORY" != *'\\'* ]] || fail "Git 仓库地址不能包含反斜杠"

git -C "$PROJECT_ROOT" diff --quiet || fail "工作区存在未提交的已跟踪文件"
git -C "$PROJECT_ROOT" diff --cached --quiet || fail "暂存区存在未提交文件"
[[ -z "$(git -C "$PROJECT_ROOT" status --porcelain --untracked-files=normal)" ]] || fail "工作区存在未跟踪文件"
git -C "$PROJECT_ROOT" rev-parse --verify --quiet "refs/tags/$TAG^{commit}" >/dev/null || fail "缺少发布标签：$TAG"
COMMIT="$(git -C "$PROJECT_ROOT" rev-parse HEAD)"
TAG_COMMIT="$(git -C "$PROJECT_ROOT" rev-parse "$TAG^{commit}")"
[[ "$COMMIT" == "$TAG_COMMIT" ]] || fail "$TAG 没有指向当前 commit"

DIST_DIR="$PROJECT_ROOT/dist"
INSTALLER_SOURCE="$PROJECT_ROOT/deploy/production/install.sh"
INSTALLER="$DIST_DIR/install.sh"
MANIFEST="$DIST_DIR/release-manifest.json"
mkdir -p "$DIST_DIR"

escaped_repository="$(printf '%s' "$REPOSITORY" | sed 's/[&|]/\\&/g')"
sed \
  -e "s|__STOCK_DATA_SYNC_REPOSITORY__|$escaped_repository|g" \
  -e "s|__STOCK_DATA_SYNC_VERSION__|$VERSION|g" \
  "$INSTALLER_SOURCE" > "$INSTALLER"
chmod 0755 "$INSTALLER"

ALEMBIC_HEADS="$(
  cd "$PROJECT_ROOT/src/server"
  UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/stock-data-sync-release-uv-cache}" \
    uv run --frozen alembic heads | awk '{print $1}' | LC_ALL=C sort | paste -sd, -
)"
[[ -n "$ALEMBIC_HEADS" ]] || fail "无法确定 Alembic head"

manifest_repository="${REPOSITORY//\"/\\\"}"

cat > "$MANIFEST" <<EOF
{
  "version": "$VERSION",
  "tag": "$TAG",
  "commit": "$COMMIT",
  "repository": "$manifest_repository",
  "buildMode": "source-on-target",
  "requiredDatabaseRevisions": "$ALEMBIC_HEADS",
  "minimumNodeMajor": 22,
  "pythonVersion": "3.12",
  "postgresqlMajor": 18
}
EOF

(cd "$DIST_DIR" && shasum -a 256 install.sh release-manifest.json > SHA256SUMS)
printf '发布资产：\n%s\n%s\n%s\n' "$INSTALLER" "$MANIFEST" "$DIST_DIR/SHA256SUMS"
