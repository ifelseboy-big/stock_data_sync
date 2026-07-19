#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck source=../lib/deploy-common.sh
source "$PROJECT_ROOT/deploy/production/lib/deploy-common.sh"

TEST_ROOT="$(mktemp -d)"
cleanup() { rm -rf -- "$TEST_ROOT"; }
trap cleanup EXIT

[[ "$(deploy_normalize_version v1.2.3)" == "1.2.3" ]]
[[ "$(deploy_normalize_version 1.2.3-rc.1)" == "1.2.3-rc.1" ]]
if deploy_normalize_version latest >/dev/null 2>&1; then
  printf 'invalid version was accepted\n' >&2
  exit 1
fi
[[ "$(deploy_xml_escape '/A & B/<x>')" == "/A &amp; B/&lt;x&gt;" ]]
deploy_validate_bind_ip "test" "127.0.0.1" >/dev/null
deploy_validate_bind_ip "test" "0.0.0.0" >/dev/null
if deploy_validate_bind_ip "test" "300.0.0.1" >/dev/null 2>&1; then
  printf 'invalid bind IP was accepted\n' >&2
  exit 1
fi

generated_installer() {
  sed \
    -e 's|__STOCK_DATA_SYNC_REPOSITORY__|https://github.com/example/stock-data-sync.git|g' \
    -e 's|__STOCK_DATA_SYNC_VERSION__|1.2.3|g' \
    "$PROJECT_ROOT/deploy/production/install.sh"
}

assert_installer_error() {
  local expected="$1" output
  shift
  if output="$(generated_installer | bash -s -- "$@" 2>&1)"; then
    printf 'installer unexpectedly accepted invalid arguments\n' >&2
    exit 1
  fi
  [[ "$output" == *"$expected"* ]] || {
    printf 'installer error did not contain: %s\n%s\n' "$expected" "$output" >&2
    exit 1
  }
}

assert_installer_error "首次安装必须传入 --postgres-port" \
  --install-dir /tmp/stock-data-sync-test --http-bind 127.0.0.1 --http-port 18080
assert_installer_error "Web/API 与 PostgreSQL 端口不能相同" \
  --install-dir /tmp/stock-data-sync-test --http-bind 127.0.0.1 \
  --http-port 18080 --postgres-port 18080

git_work="$TEST_ROOT/git-work"
git_mirror="$TEST_ROOT/git-mirror.git"
mkdir -p "$git_work"
git -C "$git_work" init -q
git -C "$git_work" config user.name deployment-test
git -C "$git_work" config user.email deployment-test@example.invalid
printf 'test\n' > "$git_work/source.txt"
git -C "$git_work" add source.txt
git -C "$git_work" commit -qm initial
git -C "$git_work" tag v1.2.3
git -C "$git_work" tag v1.2.4
git -C "$git_work" tag v1.3.0-rc.1
git clone -q --mirror "$git_work" "$git_mirror"
[[ "$(deploy_resolve_tag "$git_mirror" "")" == "v1.2.4" ]]
[[ "$(deploy_resolve_tag "$git_mirror" "1.3.0-rc.1")" == "v1.3.0-rc.1" ]]

release_dir="$TEST_ROOT/releases/1.2.3-abcdef123456"
mkdir -p \
  "$release_dir/deploy/production/bin" \
  "$release_dir/deploy/production/lib" \
  "$release_dir/server" \
  "$TEST_ROOT/bin" \
  "$TEST_ROOT/config"
cp "$PROJECT_ROOT/deploy/production/bin/stock-data-sync" \
  "$release_dir/deploy/production/bin/stock-data-sync"
cp "$PROJECT_ROOT/deploy/production/lib/deploy-common.sh" \
  "$release_dir/deploy/production/lib/deploy-common.sh"
cp "$PROJECT_ROOT/deploy/production/bootstrap/installed-stock-data-sync" \
  "$TEST_ROOT/bin/stock-data-sync"
printf '1.2.3\n' > "$release_dir/VERSION"
printf 'abcdef1234567890\n' > "$release_dir/COMMIT"
printf '# test\n' > "$TEST_ROOT/config/app.env"
ln -s 'releases/1.2.3-abcdef123456' "$TEST_ROOT/current"

version_output="$("$TEST_ROOT/bin/stock-data-sync" version)"
[[ "$version_output" == *"版本：1.2.3"* ]]
[[ "$version_output" == *"Commit：abcdef1234567890"* ]]
[[ "$version_output" == *"安装目录：$TEST_ROOT"* ]]

printf 'deployment helper tests passed\n'
