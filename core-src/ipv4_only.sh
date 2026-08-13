#!/bin/sh
# VVV IPv4-only enforcement for Debian 12/13.
# Sourced by bootstrap and every role installer. Idempotent by design.

VVV_IPV4_ONLY_ETC_ROOT="${VVV_IPV4_ONLY_ETC_ROOT:-/etc}"
VVV_IPV4_ONLY_PROC_ROOT="${VVV_IPV4_ONLY_PROC_ROOT:-/proc}"
VVV_IPV4_ONLY_BOOT_ROOT="${VVV_IPV4_ONLY_BOOT_ROOT:-/boot}"
VVV_IPV4_ONLY_IP_BIN="${VVV_IPV4_ONLY_IP_BIN:-ip}"

vvv_ipv4_only_note() {
  printf '%s\n' "$*"
}

vvv_ipv4_only_error() {
  printf '错误：%s\n' "$*" >&2
  return 1
}

vvv_ipv4_only_is_container() {
  case "${VVV_IPV4_ONLY_CONTAINER_MODE:-auto}" in
    1|yes|true) return 0 ;;
    0|no|false) return 1 ;;
  esac
  [ -e /.dockerenv ] && return 0
  grep -qiE 'docker|lxc|containerd|kubepods|podman|incus' "$VVV_IPV4_ONLY_PROC_ROOT/1/cgroup" 2>/dev/null && return 0
  grep -qE 'lxcfs|/dev/\.incus|/dev/incus' "$VVV_IPV4_ONLY_PROC_ROOT/mounts" 2>/dev/null && return 0
  return 1
}

vvv_ipv4_only_network_checks_enabled() {
  [ "$VVV_IPV4_ONLY_PROC_ROOT" = /proc ] || [ "${VVV_IPV4_ONLY_FORCE_NETWORK_CHECK:-0}" = 1 ]
}

vvv_ipv4_only_ip_available() {
  command -v "$VVV_IPV4_ONLY_IP_BIN" >/dev/null 2>&1
}

vvv_ipv4_only_write_persistent_files() {
  mkdir -p "$VVV_IPV4_ONLY_ETC_ROOT/sysctl.d" "$VVV_IPV4_ONLY_ETC_ROOT/modprobe.d" || return 1
  cat > "$VVV_IPV4_ONLY_ETC_ROOT/sysctl.d/99-vvv-ipv4-only.conf" <<'EOF_VVV_IPV4_SYSCTL'
# Managed by VVV. This server is intentionally IPv4-only.
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 1
net.ipv6.conf.all.accept_ra = 0
net.ipv6.conf.default.accept_ra = 0
net.ipv6.conf.all.autoconf = 0
net.ipv6.conf.default.autoconf = 0
EOF_VVV_IPV4_SYSCTL
  chmod 644 "$VVV_IPV4_ONLY_ETC_ROOT/sysctl.d/99-vvv-ipv4-only.conf" || return 1

  cat > "$VVV_IPV4_ONLY_ETC_ROOT/modprobe.d/99-vvv-ipv4-only.conf" <<'EOF_VVV_IPV4_MODPROBE'
# Managed by VVV. If IPv6 is modular, keep the module disabled as well.
options ipv6 disable=1
EOF_VVV_IPV4_MODPROBE
  chmod 644 "$VVV_IPV4_ONLY_ETC_ROOT/modprobe.d/99-vvv-ipv4-only.conf" || return 1
}

vvv_ipv4_only_apply_runtime() {
  conf_root="$VVV_IPV4_ONLY_PROC_ROOT/sys/net/ipv6/conf"
  [ -d "$conf_root" ] || return 0

  failed=0
  for iface_dir in "$conf_root"/*; do
    [ -d "$iface_dir" ] || continue
    for knob_name in disable_ipv6 accept_ra autoconf; do
      knob="$iface_dir/$knob_name"
      [ -e "$knob" ] || continue
      case "$knob_name" in
        disable_ipv6) desired=1 ;;
        *) desired=0 ;;
      esac
      if ! printf '%s\n' "$desired" > "$knob" 2>/dev/null; then
        failed=1
      fi
    done
  done

  if [ "$failed" -ne 0 ]; then
    if vvv_ipv4_only_is_container; then
      vvv_ipv4_only_note "提示：受限容器不允许修改宿主机 IPv6 内核开关；VVV 仍会保持所有自身监听和节点为 IPv4。"
      return 0
    fi
    vvv_ipv4_only_error "无法关闭当前内核的 IPv6；拒绝继续以避免出现双栈代理。"
    return 1
  fi
}

vvv_ipv4_only_write_grub_policy() {
  grub_dir="$VVV_IPV4_ONLY_ETC_ROOT/default/grub.d"
  grub_cfg="$grub_dir/99-vvv-ipv4-only.cfg"

  if vvv_ipv4_only_is_container; then
    vvv_ipv4_only_note "提示：受限容器无法修改宿主机启动参数；已跳过 GRUB 层 IPv6 禁用。"
    return 0
  fi

  mkdir -p "$grub_dir" || return 1
  cat > "$grub_cfg" <<'EOF_VVV_IPV4_GRUB'
# Managed by VVV. grub-mkconfig sources /etc/default/grub.d/*.cfg after /etc/default/grub.
case " ${GRUB_CMDLINE_LINUX:-} " in
  *" ipv6.disable=1 "*) ;;
  *) GRUB_CMDLINE_LINUX="${GRUB_CMDLINE_LINUX:+${GRUB_CMDLINE_LINUX} }ipv6.disable=1" ;;
esac
export GRUB_CMDLINE_LINUX
EOF_VVV_IPV4_GRUB
  chmod 644 "$grub_cfg" || return 1

  [ "${VVV_IPV4_ONLY_SKIP_GRUB_UPDATE:-0}" = 1 ] && return 0

  if command -v update-grub >/dev/null 2>&1; then
    update-grub >/dev/null || {
      vvv_ipv4_only_error "写入 IPv4-only 启动参数后 update-grub 失败。"
      return 1
    }
  elif command -v grub-mkconfig >/dev/null 2>&1 && [ -d "$VVV_IPV4_ONLY_BOOT_ROOT/grub" ]; then
    grub-mkconfig -o "$VVV_IPV4_ONLY_BOOT_ROOT/grub/grub.cfg" >/dev/null || {
      vvv_ipv4_only_error "写入 IPv4-only 启动参数后 grub-mkconfig 失败。"
      return 1
    }
  else
    vvv_ipv4_only_note "提示：未检测到可更新的 GRUB；永久 sysctl 仍会在每次启动时关闭 IPv6。"
  fi
}

vvv_ipv4_only_cleanup_routes() {
  vvv_ipv4_only_network_checks_enabled || return 0
  vvv_ipv4_only_ip_available || return 0
  vvv_ipv4_only_is_container && return 0

  # Some VPS kernels keep RA-learned IPv6 routes until their lifetime expires
  # even after every IPv6 address has disappeared. IPv4-only means those routes
  # must not remain usable, so purge the IPv6 FIB after disabling RA/autoconf.
  "$VVV_IPV4_ONLY_IP_BIN" -6 route flush table all >/dev/null 2>&1 || true
  "$VVV_IPV4_ONLY_IP_BIN" -6 route flush cache >/dev/null 2>&1 || true
}

vvv_ipv4_only_list_usable_global_routes() {
  vvv_ipv4_only_ip_available || return 0
  "$VVV_IPV4_ONLY_IP_BIN" -6 route show table all 2>/dev/null | awk '
    /^[[:space:]]*$/ { next }
    $1 ~ /^(unreachable|blackhole|prohibit|throw)$/ { next }
    $1 ~ /^(local|broadcast|multicast|anycast)$/ { next }
    $1 ~ /^::1(\/128)?$/ { next }
    $1 ~ /^fe[89abAB][0-9a-fA-F]*:/ { next }
    $1 ~ /^ff[0-9a-fA-F][0-9a-fA-F]*:/ { next }
    { print }
  '
}

vvv_ipv4_only_verify_runtime() {
  conf_root="$VVV_IPV4_ONLY_PROC_ROOT/sys/net/ipv6/conf"
  if [ -d "$conf_root" ]; then
    for iface_dir in "$conf_root"/*; do
      [ -d "$iface_dir" ] || continue
      knob="$iface_dir/disable_ipv6"
      [ -e "$knob" ] || continue
      value="$(cat "$knob" 2>/dev/null || true)"
      if [ "$value" != 1 ]; then
        if vvv_ipv4_only_is_container; then
          return 0
        fi
        vvv_ipv4_only_error "IPv6 内核开关仍未关闭：$knob=$value"
        return 1
      fi
    done
  fi

  if vvv_ipv4_only_network_checks_enabled && vvv_ipv4_only_ip_available && ! vvv_ipv4_only_is_container; then
    if "$VVV_IPV4_ONLY_IP_BIN" -6 addr show 2>/dev/null | grep -q 'inet6 '; then
      vvv_ipv4_only_error "关闭 IPv6 后仍检测到 IPv6 地址。"
      return 1
    fi
    usable_routes="$(vvv_ipv4_only_list_usable_global_routes)"
    if [ -n "$usable_routes" ]; then
      first_route="$(printf '%s\n' "$usable_routes" | head -n1)"
      vvv_ipv4_only_error "关闭 IPv6 后仍检测到可用的全局 IPv6 路由：$first_route"
      return 1
    fi
  fi
  return 0
}

vvv_enforce_ipv4_only() {
  vvv_ipv4_only_write_persistent_files || {
    vvv_ipv4_only_error "无法写入永久 IPv4-only 系统配置。"
    return 1
  }
  vvv_ipv4_only_apply_runtime || return 1
  vvv_ipv4_only_write_grub_policy || return 1
  vvv_ipv4_only_apply_runtime || return 1
  vvv_ipv4_only_cleanup_routes || return 1
  vvv_ipv4_only_verify_runtime || return 1
  vvv_ipv4_only_note "IPv4-only：已强制启用（运行时禁 IPv6/RA/自动配置 + 清理 IPv6 路由 + 永久 sysctl + IPv6 模块策略 + GRUB 启动参数）。"
}
