#!/usr/bin/env bash

# Shared deployment helpers. Callers must enable their own strict shell options.

deploy_fail() {
  printf '错误：%s\n' "$*" >&2
  return 1
}

deploy_find_executable() {
  local name="$1"
  local service_home="${2:-}"
  local candidate
  for candidate in \
    "/opt/homebrew/opt/node@22/bin/$name" \
    "/usr/local/opt/node@22/bin/$name" \
    "$(command -v "$name" 2>/dev/null || true)" \
    "$service_home/.local/bin/$name" \
    "/opt/homebrew/bin/$name" \
    "/usr/local/bin/$name"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

deploy_service_home() {
  local user="$1"
  local result
  result="$(dscl . -read "/Users/$user" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
  if [[ -z "$result" || ! -d "$result" ]]; then
    deploy_fail "无法确定用户主目录：$user"
    return 1
  fi
  printf '%s\n' "$result"
}

deploy_validate_absolute_path() {
  local name="$1"
  local value="$2"
  if [[ -z "$value" ]]; then deploy_fail "$name 不能为空"; return 1; fi
  if [[ "$value" != /* ]]; then deploy_fail "$name 必须是绝对路径"; return 1; fi
  if [[ "$value" == "/" ]]; then deploy_fail "$name 不能是根目录"; return 1; fi
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then deploy_fail "$name 不能包含换行"; return 1; fi
}

deploy_validate_port() {
  local name="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[0-9]{1,5}$ ]]; then deploy_fail "$name 必须是 1-5 位数字"; return 1; fi
  if (( 10#$value < 1 || 10#$value > 65535 )); then deploy_fail "$name 必须在 1-65535 之间"; return 1; fi
}

deploy_validate_bind_ip() {
  local name="$1"
  local value="$2"
  local octet
  local -a octets
  if [[ ! "$value" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    deploy_fail "$name 必须是 IPv4 地址"
    return 1
  fi
  IFS=. read -r -a octets <<< "$value"
  for octet in "${octets[@]}"; do
    if [[ ! "$octet" =~ ^[0-9]+$ ]] || (( 10#$octet > 255 )); then
      deploy_fail "$name 必须是有效的 IPv4 地址"
      return 1
    fi
  done
}

deploy_write_env_value() {
  local key="$1"
  local value="$2"
  printf '%s=' "$key"
  printf '%q' "$value"
  printf '\n'
}

deploy_xml_escape() {
  local value="$1"
  value="${value//&/&amp;}"
  value="${value//</&lt;}"
  value="${value//>/&gt;}"
  printf '%s\n' "$value"
}

deploy_receipt_path() {
  local service_home="$1"
  printf '%s/.stock-data-sync/install.conf\n' "$service_home"
}

deploy_write_receipt() {
  local service_user="$1"
  local service_group="$2"
  local service_home="$3"
  local install_dir="$4"
  local repository="$5"
  local version="$6"
  local receipt_dir="$service_home/.stock-data-sync"
  local receipt_file
  receipt_file="$(deploy_receipt_path "$service_home")"
  if [[ -L "$receipt_dir" ]]; then deploy_fail "安装发现目录不能是软链接：$receipt_dir"; return 1; fi
  if [[ -L "$receipt_file" ]]; then deploy_fail "安装发现文件不能是软链接：$receipt_file"; return 1; fi
  install -d -o "$service_user" -g "$service_group" -m 0700 "$receipt_dir"
  {
    printf '# Stock Data Sync 安装发现文件。仅记录安装位置，不保存运行配置或密钥。\n'
    printf 'INSTALL_DIR=%s\n' "$install_dir"
    printf 'REPOSITORY=%s\n' "$repository"
    printf 'CHANNEL=stable\n'
    printf 'INSTALLED_VERSION=%s\n' "$version"
  } > "$receipt_file"
  chown "$service_user:$service_group" "$receipt_file"
  chmod 0600 "$receipt_file"
}

deploy_normalize_version() {
  local version="$1"
  version="${version#v}"
  if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9._-]+)?$ ]]; then
    deploy_fail "版本号必须使用语义化版本格式：$version"
    return 1
  fi
  printf '%s\n' "$version"
}

deploy_resolve_tag() {
  local mirror="$1"
  local requested="${2:-}"
  local tag
  if [[ -n "$requested" ]]; then
    if [[ "$requested" == v* ]]; then
      tag="$requested"
    else
      tag="v$requested"
    fi
    if ! git --git-dir="$mirror" rev-parse --verify --quiet "refs/tags/$tag^{commit}" >/dev/null; then
      deploy_fail "Git 仓库中不存在版本标签：$tag"
      return 1
    fi
  else
    tag="$(git --git-dir="$mirror" tag --list 'v[0-9]*' --sort=-v:refname | \
      grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -n 1 || true)"
    if [[ -z "$tag" ]]; then deploy_fail "Git 仓库中没有可安装的 vX.Y.Z 标签"; return 1; fi
  fi
  deploy_normalize_version "$tag" >/dev/null || return 1
  printf '%s\n' "$tag"
}

deploy_release_name() {
  local version="$1"
  local commit="$2"
  printf '%s-%s\n' "${version#v}" "${commit:0:12}"
}

deploy_prepare_release() {
  local install_dir="$1"
  local service_user="$2"
  local service_group="$3"
  local service_home="$4"
  local mirror="$5"
  local tag="$6"
  local repository="$7"
  local commit version release_name release_dir source_dir uv_bin npm_bin node_bin node_major build_path

  commit="$(git --git-dir="$mirror" rev-parse "$tag^{commit}")"
  version="$(deploy_normalize_version "$tag")"
  release_name="$(deploy_release_name "$version" "$commit")"
  release_dir="$install_dir/releases/$release_name"
  source_dir="$install_dir/.build/$release_name"
  BUILT_RELEASE_DIR="$release_dir"
  BUILT_RELEASE_VERSION="$version"
  BUILT_RELEASE_COMMIT="$commit"

  if [[ -f "$release_dir/BUILD_COMPLETE" ]]; then
    if [[ "$(stat -f '%Su' "$release_dir")" != "root" ]]; then deploy_fail "已构建版本目录所有者不可信：$release_name"; return 1; fi
    if [[ "$(tr -d '[:space:]' < "$release_dir/COMMIT")" != "$commit" ]]; then deploy_fail "已构建版本 commit 不匹配：$release_name"; return 1; fi
    return 0
  fi
  if [[ -e "$release_dir" && ! -f "$release_dir/BUILD_COMPLETE" ]]; then
    rm -rf -- "$release_dir"
  fi
  if [[ -e "$source_dir" ]]; then
    rm -rf -- "$source_dir"
  fi

  uv_bin="$(deploy_find_executable uv "$service_home" || true)"
  npm_bin="$(deploy_find_executable npm "$service_home" || true)"
  node_bin="$(deploy_find_executable node "$service_home" || true)"
  if [[ -z "$uv_bin" ]]; then deploy_fail "未安装 uv"; return 1; fi
  if [[ -z "$npm_bin" || -z "$node_bin" ]]; then deploy_fail "未安装 Node.js/npm"; return 1; fi
  node_major="$("$node_bin" -p 'process.versions.node.split(".")[0]')"
  if [[ "$node_major" != "22" ]]; then
    deploy_fail "必须使用 Node.js 22，当前为 $("$node_bin" --version)"
    return 1
  fi
  build_path="$(dirname "$node_bin"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

  install -d -o "$service_user" -g "$service_group" -m 0755 "$source_dir" "$release_dir"
  git --git-dir="$mirror" archive "$commit" | tar -C "$source_dir" -xf -
  chown -R "$service_user:$service_group" "$source_dir"

  /usr/bin/sudo -u "$service_user" -H env PATH="$build_path" "$npm_bin" \
    --prefix "$source_dir/src/web" ci
  /usr/bin/sudo -u "$service_user" -H env PATH="$build_path" "$npm_bin" \
    --prefix "$source_dir/src/web" run build

  install -d -o "$service_user" -g "$service_group" -m 0755 \
    "$release_dir/server" "$release_dir/web" "$release_dir/deploy/production"
  tar -C "$source_dir/src/server" \
    --exclude='.venv' \
    --exclude='.pytest_cache' \
    --exclude='.ruff_cache' \
    --exclude='.mypy_cache' \
    --exclude='**/__pycache__' \
    --exclude='tests' \
    -cf - . | tar -C "$release_dir/server" -xf -
  tar -C "$source_dir/src/web/dist" -cf - . | tar -C "$release_dir/web" -xf -
  tar -C "$source_dir/deploy/production" -cf - bin lib | \
    tar -C "$release_dir/deploy/production" -xf -
  chown -R "$service_user:$service_group" "$release_dir"

  /usr/bin/sudo -u "$service_user" -H "$uv_bin" \
    --directory "$release_dir/server" sync --frozen --no-dev --no-install-project

  printf '%s\n' "$version" > "$release_dir/VERSION"
  printf '%s\n' "$commit" > "$release_dir/COMMIT"
  printf '%s\n' "$repository" > "$release_dir/REPOSITORY"
  (
    cd "$release_dir/server"
    .venv/bin/python -m alembic heads | awk '{print $1}' | LC_ALL=C sort
  ) > "$release_dir/ALEMBIC_HEADS"
  if [[ ! -s "$release_dir/ALEMBIC_HEADS" ]]; then
    deploy_fail "无法确定目标版本的 Alembic head"
    return 1
  fi
  touch "$release_dir/BUILD_COMPLETE"
  chown -R root:wheel "$release_dir"
  chmod -R go-w "$release_dir"

  # The source mirror and versioned server sources are retained. Frontend build inputs are disposable.
  rm -rf -- "$source_dir"
}

deploy_switch_current() {
  local install_dir="$1"
  local release_dir="$2"
  local relative_target="releases/$(basename "$release_dir")"
  local temporary="$install_dir/.current.new.$$"
  ln -s "$relative_target" "$temporary"
  mv -fh "$temporary" "$install_dir/current"
}

deploy_expected_heads() {
  local release_dir="$1"
  [[ -s "$release_dir/ALEMBIC_HEADS" ]] || return 1
  LC_ALL=C sort "$release_dir/ALEMBIC_HEADS"
}

deploy_database_heads() {
  local postgres_bin_dir="$1"
  local postgres_port="$2"
  local postgres_user="$3"
  local postgres_db="$4"
  "$postgres_bin_dir/psql" \
    -h 127.0.0.1 -p "$postgres_port" -U "$postgres_user" -d "$postgres_db" \
    -tAc 'SELECT version_num FROM alembic_version ORDER BY version_num' | \
    sed '/^[[:space:]]*$/d' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | LC_ALL=C sort
}

deploy_database_is_compatible() {
  local release_dir="$1"
  local actual expected
  expected="$(deploy_expected_heads "$release_dir")" || return 1
  actual="$(deploy_database_heads "$POSTGRES_BIN_DIR" "$POSTGRES_PORT" "$POSTGRES_USER" "$POSTGRES_DB")" || return 1
  [[ "$actual" == "$expected" ]]
}
