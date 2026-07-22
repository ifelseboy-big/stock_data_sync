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
deploy_validate_absolute_path "test" "/tmp/program" >/dev/null
if deploy_validate_absolute_path "test" "/tmp/program/../data" >/dev/null 2>&1; then
  printf 'non-canonical absolute path was accepted\n' >&2
  exit 1
fi
node_runtime="$(deploy_find_node_runtime "${HOME:-}" || true)"
[[ -n "$node_runtime" ]]
node_runtime_major="$("$node_runtime" -p 'process.versions.node.split(".")[0]')"
(( 10#$node_runtime_major >= 22 ))
[[ -x "$(dirname "$node_runtime")/npm" ]]

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

assert_installer_error "首次安装必须传入 --program-dir"
assert_installer_error "--ignore-doctor 只能与 --upgrade 同时使用" --ignore-doctor
assert_installer_error "首次安装必须传入 --data-dir" \
  --program-dir /tmp/stock-data-sync-program
assert_installer_error "首次安装必须传入 --postgres-port" \
  --program-dir /tmp/stock-data-sync-program --data-dir /tmp/stock-data-sync-data \
  --http-bind 127.0.0.1 --http-port 18080
assert_installer_error "Web/API 与 PostgreSQL 端口不能相同" \
  --program-dir /tmp/stock-data-sync-program --data-dir /tmp/stock-data-sync-data \
  --http-bind 127.0.0.1 \
  --http-port 18080 --postgres-port 18080
assert_installer_error "主程序目录与数据目录必须相互独立" \
  --program-dir /tmp/stock-data-sync --data-dir /tmp/stock-data-sync/data \
  --http-bind 127.0.0.1 --http-port 18080 --postgres-port 15432

if system_entry_output="$("$PROJECT_ROOT/deploy/production/bootstrap/system-run-service" 2>&1)"; then
  printf 'system service entry accepted missing directories\n' >&2
  exit 1
fi
[[ "$system_entry_output" == *"launchd 主程序目录无效"* ]]
grep -Fq '<key>ProgramArguments</key><array><string>/bin/bash</string><string>$xml_run</string>' \
  "$PROJECT_ROOT/deploy/production/bin/stock-data-sync"
grep -Fq 'stdout_log="$PROGRAM_DIR/logs/launchd/$service.out.log"' \
  "$PROJECT_ROOT/deploy/production/bin/stock-data-sync"
grep -Fq -- '-c "shared_buffers=1GB"' \
  "$PROJECT_ROOT/deploy/production/bin/run-service"
grep -Fq 'PostgreSQL 实际配置：shared_buffers=%s' \
  "$PROJECT_ROOT/deploy/production/bin/stock-data-sync"
grep -Fq 'PostgreSQL shared_buffers 已生效：$shared_buffers_actual' \
  "$PROJECT_ROOT/deploy/production/bin/stock-data-sync"
grep -Fq 'release_shared_buffers_config()' \
  "$PROJECT_ROOT/deploy/production/bin/stock-data-sync"
grep -Fq 'ensure_postgres_release_config "$target"' \
  "$PROJECT_ROOT/deploy/production/bin/stock-data-sync"
grep -Fq 'deploy_write_env_value PROCESSING_MAX_WORKERS 3' \
  "$PROJECT_ROOT/deploy/production/install-local.sh"
grep -Fq 'deploy_write_env_value PROCESSING_PLAN_BATCH_LIMIT 100' \
  "$PROJECT_ROOT/deploy/production/install-local.sh"
grep -Fq 'deploy_write_env_value COLLECTION_CLOSE_BATCH_LIMIT 100' \
  "$PROJECT_ROOT/deploy/production/install-local.sh"
grep -Fq 'export LC_ALL="C.UTF-8"' \
  "$PROJECT_ROOT/deploy/production/bin/run-service"
grep -Fq '<key>LC_ALL</key><string>C.UTF-8</string>' \
  "$PROJECT_ROOT/deploy/production/bin/stock-data-sync"
grep -Fq 'launchctl kickstart -k "system/$(label_for "$service")"' \
  "$PROJECT_ROOT/deploy/production/bin/stock-data-sync"
grep -Fq 'disabled_marker="\"$(label_for "$service")\" => disabled"' \
  "$PROJECT_ROOT/deploy/production/bin/stock-data-sync"
grep -Fq '/bin/bash "$MANAGER" "${UPGRADE_ARGS[@]}"' \
  "$PROJECT_ROOT/deploy/production/install.sh"
grep -Fq '确认忽略时使用 --ignore-doctor' \
  "$PROJECT_ROOT/deploy/production/bin/stock-data-sync"
grep -Fq '[[ "$HTTP_BIND" == "0.0.0.0" ]]' \
  "$PROJECT_ROOT/deploy/production/bin/stock-data-sync"
grep -Fq 'http://$health_host:$HTTP_PORT$APP_API_PREFIX/health/live' \
  "$PROJECT_ROOT/deploy/production/bin/stock-data-sync"
grep -Fq 'wait_for_api 30 && return 0' \
  "$PROJECT_ROOT/deploy/production/bin/stock-data-sync"
grep -Fq 'if wait_for_api 5; then' \
  "$PROJECT_ROOT/deploy/production/bin/stock-data-sync"
[[ "$(grep -Ec '^[[:space:]]+wait_for_server$' "$PROJECT_ROOT/deploy/production/bin/stock-data-sync")" == "3" ]]
grep -Fq 'if ! ensure_postgres_release_config "$target" || \' \
  "$PROJECT_ROOT/deploy/production/bin/stock-data-sync"
grep -Fq '! restart_selected server || ! restart_selected scheduler || ! run_doctor --phase post-upgrade; then' \
  "$PROJECT_ROOT/deploy/production/bin/stock-data-sync"
if grep -Eq 'local release=.*bootstrap=.*\$release' \
  "$PROJECT_ROOT/deploy/production/bin/stock-data-sync"; then
  printf 'upgrade bootstrap path expands release before local assignment completes\n' >&2
  exit 1
fi

(
  eval "$(sed -n '/^wait_for_api() {$/,/^}$/p' "$PROJECT_ROOT/deploy/production/bin/stock-data-sync")"
  attempt_count=0
  api_is_healthy() { (( attempt_count += 1 )); (( attempt_count >= 3 )); }
  sleep() { :; }
  wait_for_api 5
  [[ "$attempt_count" == "3" ]]
  attempt_count=0
  api_is_healthy() { (( attempt_count += 1 )); return 1; }
  ! wait_for_api 3
  [[ "$attempt_count" == "3" ]]
)

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
printf '1.2.3\n' > "$release_dir/VERSION"
printf 'abcdef1234567890\n' > "$release_dir/COMMIT"
printf 'PROGRAM_DIR=%s\nDATA_DIR=%s\nINSTALL_DIR=%s\n' "$TEST_ROOT" "$TEST_ROOT/data" "$TEST_ROOT/data" > "$TEST_ROOT/config/app.env"
ln -s 'releases/1.2.3-abcdef123456' "$TEST_ROOT/current"
touch "$TEST_ROOT/bin/stock-data-mcp"
chmod 0755 "$TEST_ROOT/bin/stock-data-mcp"

version_output="$(STOCK_DATA_SYNC_PROGRAM_DIR="$TEST_ROOT" STOCK_DATA_SYNC_DATA_DIR="$TEST_ROOT/data" \
  "$release_dir/deploy/production/bin/stock-data-sync" version)"
[[ "$version_output" == *"版本：1.2.3"* ]]
[[ "$version_output" == *"Commit：abcdef1234567890"* ]]
[[ "$version_output" == *"主程序目录：$TEST_ROOT"* ]]
[[ "$version_output" == *"数据目录：$TEST_ROOT/data"* ]]

mcp_command_output="$(STOCK_DATA_SYNC_PROGRAM_DIR="$TEST_ROOT" STOCK_DATA_SYNC_DATA_DIR="$TEST_ROOT/data" \
  "$release_dir/deploy/production/bin/stock-data-sync" mcp command)"
[[ "$mcp_command_output" == "$TEST_ROOT/bin/stock-data-mcp" ]]
if mcp_command_error="$(STOCK_DATA_SYNC_PROGRAM_DIR="$TEST_ROOT" STOCK_DATA_SYNC_DATA_DIR="$TEST_ROOT/data" \
  "$release_dir/deploy/production/bin/stock-data-sync" mcp command unexpected 2>&1)"; then
  printf 'mcp command accepted extra arguments\n' >&2
  exit 1
fi
[[ "$mcp_command_error" == *"用法：stock-data-sync mcp command"* ]]
chmod 0644 "$TEST_ROOT/bin/stock-data-mcp"
if mcp_command_error="$(STOCK_DATA_SYNC_PROGRAM_DIR="$TEST_ROOT" STOCK_DATA_SYNC_DATA_DIR="$TEST_ROOT/data" \
  "$release_dir/deploy/production/bin/stock-data-sync" mcp command 2>&1)"; then
  printf 'mcp command accepted a non-executable entry\n' >&2
  exit 1
fi
[[ "$mcp_command_error" == *"MCP启动入口不存在"* ]]

switch_root="$TEST_ROOT/switch"
mkdir -p "$switch_root/releases/1.2.3-test"
saved_umask="$(umask)"
umask 077
deploy_switch_current "$switch_root" "$switch_root/releases/1.2.3-test"
umask "$saved_umask"
[[ "$(stat -f '%Lp' "$switch_root/current")" == "755" ]]

printf 'deployment helper tests passed\n'
