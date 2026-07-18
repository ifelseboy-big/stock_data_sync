#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-}"

if [[ -z "$VERSION" ]]; then
  printf '用法：%s <version>\n' "$0" >&2
  exit 1
fi
[[ "$VERSION" =~ ^[A-Za-z0-9._-]+$ ]] || {
  printf '版本号只能包含字母、数字、点、下划线和短横线\n' >&2
  exit 1
}

DIST_DIR="$PROJECT_ROOT/dist"
STAGE_DIR="$(mktemp -d)"
BUNDLE_NAME="stock-data-sync-$VERSION"
BUNDLE_DIR="$STAGE_DIR/$BUNDLE_NAME"
ARCHIVE="$DIST_DIR/$BUNDLE_NAME.tar.gz"

cleanup() {
  rm -rf -- "$STAGE_DIR"
}
trap cleanup EXIT

mkdir -p "$DIST_DIR" "$BUNDLE_DIR"

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
  -cf - \
  src/server \
  src/web \
  deploy/docker \
  deploy/production \
  docs/deployment.md \
  | tar -C "$BUNDLE_DIR" -xf -

printf '%s\n' "$VERSION" > "$BUNDLE_DIR/VERSION"
tar -C "$STAGE_DIR" -czf "$ARCHIVE" "$BUNDLE_NAME"

if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
else
  shasum -a 256 "$ARCHIVE" > "$ARCHIVE.sha256"
fi

printf '发布包：%s\n' "$ARCHIVE"
printf '校验文件：%s.sha256\n' "$ARCHIVE"
