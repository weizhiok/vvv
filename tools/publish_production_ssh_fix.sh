#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com

python3 - <<'PY_PATCH'
from pathlib import Path

host=Path('core-src/host.sh')
text=host.read_text(encoding='utf-8')

old_make='''make_pairing_key() {
  local state_path="$1" relay_id="$2" subscription_bootstrap="null"
  if [[ -s /etc/vvv/client.json && -x /usr/local/lib/vvv/sync_agent.py ]]; then
    subscription_bootstrap="$(python3 /usr/local/lib/vvv/sync_agent.py relay-ticket "$relay_id" 2>/dev/null || printf 'null')"
  fi
  python3 - "$state_path" "$relay_id" "$subscription_bootstrap" <<'PY_JPR3' '''.rstrip()
new_make='''require_relay_subscription_registration() {
  [[ -s /etc/vvv/client.json ]] || fail "中转主机尚未注册订阅中心。请先在 vps 菜单完成订阅中心注册，再新建 VPS 副机中转线路。"
  [[ -x /usr/local/lib/vvv/sync_agent.py ]] || fail "订阅同步程序不存在，无法为 JPR3 生成受限注册票据。"
  local role
  role="$(jq -r '.role // empty' /etc/vvv/client.json 2>/dev/null || true)"
  [[ "$role" == "relay" || "$role" == "center-relay" ]] || fail "当前订阅中心登记角色不是中转主机，无法签发副机注册票据。"
}

request_subscription_bootstrap() {
  local relay_id="$1" bootstrap
  require_relay_subscription_registration || return 1
  bootstrap="$(python3 /usr/local/lib/vvv/sync_agent.py relay-ticket "$relay_id")" || fail "订阅中心拒绝签发该线路的副机注册票据。线路状态已保留在升级前状态。"
  jq -e --arg id "$relay_id" '
    (.api_base_url|type=="string" and length>0) and
    (.relay_id==$id) and
    (.registration_token|type=="string" and length>=20)
  ' <<<"$bootstrap" >/dev/null || fail "订阅中心返回的副机注册票据不完整。"
  printf '%s' "$bootstrap"
}

make_pairing_key() {
  local state_path="$1" relay_id="$2" subscription_bootstrap="${3:-}"
  [[ -n "$subscription_bootstrap" ]] || subscription_bootstrap="$(request_subscription_bootstrap "$relay_id")" || return 1
  python3 - "$state_path" "$relay_id" "$subscription_bootstrap" <<'PY_JPR3' '''.rstrip()
if text.count(old_make) != 1:
    raise SystemExit('make_pairing_key anchor mismatch')
text=text.replace(old_make,new_make,1)

old_python='''try:
    subscription_bootstrap=json.loads(sys.argv[3]) if sys.argv[3] else None
except Exception:
    subscription_bootstrap=None
payload={'''
new_python='''try:
    subscription_bootstrap=json.loads(sys.argv[3])
except Exception as exc:
    raise SystemExit(f"订阅中心注册票据无法解析：{exc}")
if not isinstance(subscription_bootstrap,dict) or not subscription_bootstrap.get("api_base_url") or not subscription_bootstrap.get("registration_token") or subscription_bootstrap.get("relay_id") != r["id"]:
    raise SystemExit("订阅中心注册票据缺失或与线路不匹配。")
payload={'''
if text.count(old_python) != 1:
    raise SystemExit('JPR3 Python bootstrap anchor mismatch')
text=text.replace(old_python,new_python,1)

old_local='''  local count old relay_id now candidate test_vless test_hy2 remote_hy2
  count="$(jq --arg n "$node_name" '[.relays[]|select(.name==$n)]|length' "$STATE_FILE")"'''
new_local='''  require_relay_subscription_registration || return 1

  local count old relay_id now candidate test_vless test_hy2 remote_hy2 old_state
  old_state="$(mktemp --suffix=.json /tmp/jp-relay-before-ticket.XXXXXX)"
  TMP_FILES+=("$old_state")
  cp -a "$STATE_FILE" "$old_state"
  count="$(jq --arg n "$node_name" '[.relays[]|select(.name==$n)]|length' "$STATE_FILE")"'''
if text.count(old_local) != 1:
    raise SystemExit('prepare relay preflight anchor mismatch')
text=text.replace(old_local,new_local,1)

old_staging='''  local staging package_dir key
  staging="$(mktemp -d "${PACKAGE_ROOT}/.${relay_id}.staging.XXXXXX")"
  TMP_FILES+=("$staging")
  generate_client_files "$candidate" "$relay_id" "$staging" relay >/dev/null
  key="$(make_pairing_key "$candidate" "$relay_id")"
  printf '%s\\n' "$key" > "$staging/落地VPS对接密钥.txt"
  cat > "$staging/使用说明.txt" <<EOF_RELAY_HELP'''
new_staging='''  local staging package_dir key
  staging="$(mktemp -d "${PACKAGE_ROOT}/.${relay_id}.staging.XXXXXX")"
  TMP_FILES+=("$staging")
  generate_client_files "$candidate" "$relay_id" "$staging" relay >/dev/null
  cat > "$staging/使用说明.txt" <<EOF_RELAY_HELP'''
if text.count(old_staging) != 1:
    raise SystemExit('pre-commit pairing-key anchor mismatch')
text=text.replace(old_staging,new_staging,1)

old_apply='''  chmod 600 "$staging"/*

  apply_candidate_with_rollback "$candidate"

  package_dir="${PACKAGE_ROOT}/${relay_id}"'''
new_apply='''  chmod 600 "$staging"/*

  apply_candidate_with_rollback "$candidate"

  if ! key="$(make_pairing_key "$STATE_FILE" "$relay_id")"; then
    echo "副机注册票据生成失败，正在恢复新建线路前的状态……" >&2
    if apply_candidate_with_rollback "$old_state"; then
      fail "副机注册票据生成失败；线路、槽位和运行配置已回滚。请确认中转主机能连接订阅中心后重试。"
    fi
    fail "副机注册票据生成失败，且自动回滚未完成；请立即生成诊断报告。"
  fi
  printf '%s\\n' "$key" > "$staging/落地VPS对接密钥.txt"
  chmod 600 "$staging/落地VPS对接密钥.txt"

  package_dir="${PACKAGE_ROOT}/${relay_id}"'''
if text.count(old_apply) != 1:
    raise SystemExit('post-commit ticket anchor mismatch')
text=text.replace(old_apply,new_apply,1)

host.write_text(text,encoding='utf-8')

bootstrap=Path('core-src/bootstrap.sh')
text=bootstrap.read_text(encoding='utf-8')
old4='''    ask_required_jpr3
    LANDING_REMOTE_PORT="$(jpr_field "$key" remote_public_port)" || fail "无法读取 JPR3 中转端口。"
    ask_proxy_parameters'''
new4='''    ask_required_jpr3
    jpr_field "$key" subscription_bootstrap.api_base_url >/dev/null || fail "该 JPR3 不含订阅中心注册票据。请在已注册订阅中心的中转主机重新生成。"
    LANDING_REMOTE_PORT="$(jpr_field "$key" remote_public_port)" || fail "无法读取 JPR3 中转端口。"
    ask_proxy_parameters'''
if text.count(old4) != 1:
    raise SystemExit('combined JPR3 preflight anchor mismatch')
text=text.replace(old4,new4,1)
old5='''    (main_state_valid || landing_state_valid || center_partial) && fail "中转副机只允许在全新系统安装。"
    ask_required_jpr3
    ;;'''
new5='''    (main_state_valid || landing_state_valid || center_partial) && fail "中转副机只允许在全新系统安装。"
    ask_required_jpr3
    jpr_field "$key" subscription_bootstrap.api_base_url >/dev/null || fail "该 JPR3 不含订阅中心注册票据。请在已注册订阅中心的中转主机重新生成。"
    ;;'''
if text.count(old5) != 1:
    raise SystemExit('landing-only JPR3 preflight anchor mismatch')
bootstrap.write_text(text.replace(old5,new5,1),encoding='utf-8')

path=Path('tests/landing_direct_role_validation.py')
test=path.read_text(encoding='utf-8')
anchor="""    require('subscription_bootstrap' in host and 'relay-ticket' in host, 'JPR3 没有受限订阅注册票据')"""
replacement="""    require('subscription_bootstrap' in host and 'relay-ticket' in host, 'JPR3 没有受限订阅注册票据')
    require('make_pairing_key \"$candidate\"' not in host, 'JPR3 仍在线路提交前申请注册票据')
    prepare = host.index('prepare_add_or_overwrite()')
    apply_pos = host.index('apply_candidate_with_rollback \"$candidate\"', prepare)
    key_pos = host.index('make_pairing_key \"$STATE_FILE\" \"$relay_id\"', apply_pos)
    require(apply_pos < key_pos, '注册票据没有在线路正式生效及同步后申请')
    require('apply_candidate_with_rollback \"$old_state\"' in host, '票据失败没有回滚线路状态')
    require("subscription_bootstrap.api_base_url" in (CORE / 'bootstrap.sh').read_text(encoding='utf-8'),
            '副机安装前没有拒绝缺少注册票据的 JPR3')
    require("|| printf 'null'" not in host, 'JPR3 票据失败仍被静默降级为空值')"""
if test.count(anchor) != 1:
    raise SystemExit('ticket timing test anchor mismatch')
path.write_text(test.replace(anchor,replacement,1),encoding='utf-8')
PY_PATCH

bash -n core-src/host.sh
bash -n core-src/bootstrap.sh
python3 tests/landing_direct_role_validation.py

git rm -rf --ignore-unmatch core-src/__pycache__ tests/__pycache__
git fetch --no-tags --depth=1 origin main
git checkout FETCH_HEAD -- \
  tools/publish_production_ssh_fix.sh \
  .github/workflows/publish-production-ssh-fix.yml

git add -A
git diff --cached --check
git commit -m 'Make JPR3 ticket issuance transactional'
git push origin HEAD
