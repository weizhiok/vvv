# VVV 一体化 VPS 工具

VVV 使用一个固定安装入口，在 **Debian 13 + systemd** VPS 上安装和管理代理、订阅、中转线路与云恢复。安装命令可以重复运行：每次都会进入菜单，并按当前状态续装、修复或追加角色。

## 永久固定安装地址

```bash
{ command -v curl >/dev/null 2>&1 || { apt-get -o DPkg::Lock::Timeout=10 -o Acquire::Retries=2 -o Acquire::IndexTargets::deb-src::Sources::DefaultEnabled=false update && DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=10 -o Acquire::Retries=2 install -y curl ca-certificates; }; } && curl -fsSL --retry 5 "https://raw.githubusercontent.com/weizhiok/vvv/install/vvv-install.sh?$(date +%s)" | bash
```

安装完成后统一输入：

```bash
vps
```

## 安装角色

```text
1. 安装订阅中心 + 中转主机 + 自身代理
2. 安装订阅中心 + 自身代理
3. 安装中转主机 + 自身代理
4. 安装中转副机
5. 安装直连代理
6. 从云备份恢复
0. 退出
```

所有参数在安装开始前一次性输入。参数总览显示后不再询问协议、端口、域名、限速、对接码或确认信息。

代理参数包括：

- VLESS、Hysteria 2 或双协议；
- 统一代理端口，默认 `443`；
- REALITY 伪装域名，默认 `www.softbank.jp`；
- Hysteria 2 服务端每连接强制限速，默认 `50M`，允许 `30M–100M`；
- 订阅传输、域名/IP、端口和统一后缀；
- VVC1 订阅中心对接码或 JPR3 中转副机密钥。

## Hysteria 2 限速

Hysteria 2 使用 sing-box 服务端的 `up_mbps`、`down_mbps` 和 `ignore_client_bandwidth`：

- 限制的是每个客户端连接，不是整台服务器总带宽；
- 客户端把速度填写得更高也不能突破服务器上限；
- 不同客户端连接分别执行同一上限；
- 限速值写入状态、JPR3 和客户端模板。

## 对接码与副机通讯

- `VVC1`：订阅中心永久对接码；
- `JPR3`：中转副机安装密钥；
- 两种代码使用不同前缀、结构和校验上下文，粘贴错误会明确提示；
- VVC1 和 JPR3 中的主机通讯地址统一使用公网 IP，不使用订阅域名；
- 直连副机可在 `vps` 中修改订阅中心 IP；
- 中转副机可在 `vps` 中修改主机 IP；
- 修改 IP 会先验证候选配置，失败自动恢复旧配置。

## 订阅与客户端

所有客户端共用一个订阅地址。服务端根据请求头自动返回：

- Clash Verge Rev / Mihomo；
- Quantumult X（仅 VLESS）；
- Loon；
- Shadowrocket。

订阅中心支持修改客户端显示名称。改名只改变订阅中的名称，不修改服务器、端口、UUID、密码或副机配置；客户端刷新订阅后生效。

## 订阅入口管理

首次安装可选择：

1. 直接 HTTPS；
2. 直接 HTTP；
3. Cloudflare Tunnel。

HTTP 只用于调试，并会醒目提示：

```text
HTTP 仅限调试使用，请勿长期使用。
```

订阅中心的二级菜单提供：

- 修改订阅后缀；
- 修改订阅域名；
- 修改直接 HTTPS 端口；
- 修改 Tunnel Token；
- 在直接 HTTPS 与 Tunnel 之间事务式切换。

不允许通过该菜单切换到 HTTP。订阅入口变化只影响客户端订阅 URL；副机同步继续使用固定 IP API，不受订阅域名、后缀、端口和 Tunnel 变化影响。

## 正式与临时中转线路

正式线路支持：

- 新建 VPS 副机中转线路；
- 新建 HTTP/HTTPS/SOCKS5 中转线路；
- 查看、检测和删除线路。

临时线路只允许从现有正式线路复制：

- 创建临时 VPS 中转线路；
- 创建临时 HTTP/HTTPS/SOCKS5 中转线路；
- 自动销毁时间默认 30 分钟，允许 1–10080 分钟；
- 生成新的 UUID/密码和独立凭据槽位；
- 不生成 JPR3，不修改副机，不需要副机执行任何操作；
- 到期后永久退役临时凭据，客户端即使缓存旧节点也无法继续连接；
- 正式线路和副机不受影响；
- 从备份恢复时一律不恢复临时节点。

主 Xray 和主 sing-box 的固定配置不会因线路增删而重启。正式和临时线路通过独立槽位服务生效；删除后的凭据永久退役，不会重新分配给其他线路。

## 云备份与恢复

云备份支持 Google Drive 和 Microsoft OneDrive，固定目录为：

```text
vvv/
├── RecoverKey.ini
├── BackupIndex.json
└── backups/
```

规则：

- 只备份配置、状态、证书与必要密钥；
- 禁止备份 Xray、sing-box、Caddy、cloudflared、rclone 或 VVV 程序二进制；
- 恢复时重新下载当前官方最新稳定版程序；
- 本地和云端各最多 100 份、总容量最多 1 GiB；
- 备份文件名包含完整日期、时间、原因和随机短 ID；
- 事件发生前后自动备份，不设置无意义的定时重复备份；
- `RecoverKey.ini` 明文存放于云盘固定目录，用户无需保存恢复码或解密密码。

全新 VPS 选择“从云备份恢复”后：

1. 重新完成云盘 OAuth 授权；
2. 从最近最多 100 份备份中选择日期，直接回车恢复最新；
3. 自动校验、解密、恢复配置并重新下载程序；
4. 最新备份损坏时自动尝试上一份；
5. 自动清除所有临时节点；
6. 自动重建代理、订阅中心、中转槽位、证书和 Tunnel；
7. 自动更新本机公网 IP，并输出恢复日志。

Cloudflare Tunnel 恢复后可以保持客户端订阅地址不变。固定域名直接 HTTPS 恢复时，域名 A 记录必须先指向新 VPS。

## 诊断报告

`vps` 主菜单可生成脱敏诊断报告：

```text
/root/VVV-诊断报告-年月日-时分秒.txt
```

报告包含系统资源、角色、程序版本、服务、监听端口、BBR、订阅传输、备份、临时节点和最近错误日志，并隐藏 UUID、密码、私钥、Token、VVC1、JPR3、Tunnel Token、OAuth 和恢复密码。

## 系统要求

- 仅支持 Debian 13；
- 必须使用 systemd；
- 必须使用 root；
- 支持 x86_64/amd64 与 arm64/aarch64；
- APT/dpkg 锁最多等待 10 秒；
- 只查询并安装官方最新稳定版，排除 draft 和 prerelease；
- 不包含 Debian 12、Alpine、OpenRC 或其他旧系统兼容逻辑。
