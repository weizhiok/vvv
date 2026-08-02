# VVV 一体化 VPS 工具

VVV 使用一个固定安装入口，在全新的 **Debian 13 + systemd** VPS 上安装和管理代理、订阅与中转线路。

## 永久固定安装地址

```bash
{ command -v curl >/dev/null 2>&1 || { apt-get -o DPkg::Lock::Timeout=10 -o Acquire::Retries=2 -o Acquire::IndexTargets::deb-src::Sources::DefaultEnabled=false update && DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=10 -o Acquire::Retries=2 install -y curl ca-certificates; }; } && curl -fsSL --retry 5 "https://raw.githubusercontent.com/weizhiok/vvv/install/vvv-install.sh?$(date +%s)" | bash
```

`install` 是固定入口分支，会自动取得 `main` 分支经过验证的最新安装程序。安装完成后统一输入：

```bash
vps
```

## 安装角色

```text
1. 安装订阅中心 + 中转主机 + 自身代理
2. 安装订阅中心 + 自身代理
3. 安装中转主机 + 自身代理
4. 安装中转副机（通过主机代理）
5. 安装直连代理
0. 退出
```

选择角色后，脚本会在真正安装前一次性收集该角色需要的全部参数：

- VLESS、Hysteria 2 或双协议；
- 统一代理端口，默认 `443`；
- REALITY 伪装域名，默认 `www.softbank.jp`；
- 可选订阅域名与订阅 HTTPS 端口，端口默认 TCP `8443`；
- 订阅中心接入码或 JPR3 对接密钥。

订阅域名可以直接按回车留空。留空时自动使用本机公网 IPv4，并申请 Let’s Encrypt 短期公网 IP 证书。参数总览显示后直接开始安装，不再要求输入 `Y`，安装过程中也不会穿插新的问题。

## 代理与中转架构

- VLESS：Xray，TCP 监听；
- Hysteria 2：sing-box，UDP 监听；
- 双协议可以使用同一个端口号，因为 TCP 与 UDP 相互独立；
- 主 Xray 与主 sing-box 的固定配置在安装后不随线路增删变化；
- 每条 VLESS/HY2 中转线路由独立槽位服务承载；
- 新建、覆盖或删除线路只操作目标槽位，不重启主 Xray 或主 sing-box；
- 删除线路后的 UUID、用户名与密码永久退役，不会被其他线路重新使用。

## 客户端订阅

每个订阅令牌提供四个独立短路径：

```text
/c   Clash Verge Rev / Mihomo
/qx  Quantumult X
/ln  Loon
/sr  Shadowrocket
```

- 四种客户端均只显示订阅地址或文本配置，不生成二维码；
- Quantumult X 只输出 VLESS；
- Loon 使用无多余引号的 Salamander 混淆密码；
- Shadowrocket 使用 Base64 编码的 VLESS/Hysteria 2 分享链接；
- 已移除 v2rayNG 的节点配置、文件和订阅入口。

## 订阅中心与 HTTPS

- 输入域名时，Caddy 自动申请和续期域名证书，并检查域名 IPv4 A 记录是否指向本机；
- 域名留空时，自动使用本机公网 IPv4；
- IP 模式使用隔离的 Certbot 5.4+ 环境申请 Let’s Encrypt `shortlived` IP 地址证书；
- IP 证书有效期较短，脚本安装 systemd 定时器每天检查两次并自动续期；
- 证书首次部署不会错误调用 Caddy reload；续期后只重启 Caddy，不重启整台 VPS，也不会中断 SSH；
- 代理部分完成后会立即显示订阅中心的分阶段进度，依赖下载和证书申请均有明确提示与超时保护；
- 两种模式都只提供 HTTPS，不提供明文 HTTP 订阅入口；
- 公网必须放行 TCP/80 和订阅 HTTPS 端口。

## 订阅中心与备份

- 节点变化后立即同步完整快照；
- 本地备份使用加密容器，只在首次安装或数据变化前后自动创建；
- 不设置定时备份，不提供手动备份菜单；
- 云备份默认关闭，可选 Google Drive 或 Microsoft OneDrive；
- 开启后使用 rclone `copyto` 上传独立加密备份，不使用 `sync` 删除云端历史；
- 其他代理或中转 VPS 只同步自身节点快照，不保存订阅中心备份。

## 系统要求

- 仅支持全新 Debian 13；
- 必须使用 systemd；
- 使用 root 用户执行；
- 固定安装命令会在缺少 curl 时先通过 APT 安装 curl 和 CA 证书；
- APT/dpkg 锁最多等待 10 秒，超过后立即显示错误，不删除锁文件、不强行终止系统更新；
- 主安装阶段一次性安装订阅中心所需的 `python3-venv`，避免代理完成后再次调用 APT；
- 安装时关闭无用的 `deb-src` 索引下载，减少软件源警告和等待；
- 不包含 Debian 12、Alpine、OpenRC 或旧版本迁移兼容逻辑。

## 安装策略

当前版本只按全新 Debian 13 首次安装设计。检测到旧 VVV 状态时会停止，不提供原地升级、迁移或旧版本兼容。
