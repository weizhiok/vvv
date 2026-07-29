# VVV 一体化 VPS 工具

一个入口脚本，支持以下可叠加角色：

1. 订阅中心（含本机直连代理）
2. 中转主机（含本机代理、VPS 中转、HTTP/HTTPS/SOCKS5 中转）
3. 中转副机
4. 仅直连代理
5. 单机全能模式（订阅中心 + 直连代理 + 中转管理）

## 一行安装

```bash
curl -fsSL --retry 3 https://raw.githubusercontent.com/weizhiok/vvv/main/install.sh | bash
```

安装完成后统一使用：

```bash
vps
```

## 订阅中心

- 支持域名 HTTPS 和公网 IP HTTPS。
- 默认监听 TCP/8443，不占用 VLESS/Hysteria 2 使用的 TCP/UDP 443。
- 域名模式会强制检查 A/AAAA 记录是否指向本机。
- 客户端建议每 24 小时自动刷新。
- 节点变化后立即向订阅中心同步完整快照。
- 中转主机自动保存订阅中心的加密异地备份。
- GitHub 仓库只保存程序，不保存节点、密钥、代理凭证或订阅数据。

## 支持的客户端订阅

- Clash Verge Rev / Mihomo
- Quantumult X
- Loon
- Shadowrocket
- v2rayNG

## 系统说明

- 主机/订阅中心：Debian 12/13。
- 中转副机：沿用脚本现有 Debian/Alpine 支持。
- 推荐以 root 用户执行。
