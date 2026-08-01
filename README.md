# VVV 一体化 VPS 工具

VVV 使用一个固定安装入口，在全新的 **Debian 13 + systemd** VPS 上安装和管理代理、订阅与中转线路。

## 永久固定安装地址

```bash
curl -fsSL --retry 5 https://raw.githubusercontent.com/weizhiok/vvv/install/vvv-install.sh | bash
```

`install` 是固定入口分支，会自动取得 `main` 分支经过验证的最新安装程序。安装完成后统一输入：

```bash
vps
```

## 安装角色

```text
1. 安装订阅中心（含自身代理）
2. 安装中转主机（含自身代理）
3. 安装中转副机
4. 安装直连代理
5. 以上全部安装（不含副机）
0. 退出
```

选择角色后，脚本会在真正安装前一次性收集该角色需要的全部参数：

- VLESS、Hysteria 2 或双协议；
- 统一代理端口，默认 `443`；
- REALITY 伪装域名，默认 `www.softbank.jp`；
- 订阅域名与订阅端口，默认 TCP `8443`；
- 订阅中心接入码或 JPR3 对接密钥。

参数总览显示后直接开始安装，不再要求输入 `Y`，安装过程中也不会穿插新的问题。

## 代理与中转架构

- VLESS：Xray，TCP 监听；
- Hysteria 2：sing-box，UDP 监听；
- 双协议可以使用同一个端口号，因为 TCP 与 UDP 相互独立；
- 主 Xray 与主 sing-box 的固定配置在安装后不随线路增删变化；
- 每条 VLESS/HY2 中转线路由独立槽位服务承载；
- 新建、覆盖或删除线路只操作目标槽位，不重启主 Xray 或主 sing-box；
- 删除线路后的 UUID、用户名与密码永久退役，不会被其他线路重新使用。

## 客户端订阅

每个订阅令牌提供五个独立短路径：

```text
/c   Clash Verge Rev / Mihomo
/qx  Quantumult X
/ln  Loon
/sr  Shadowrocket
/v2  v2rayNG
```

- Clash、Quantumult X、Loon：显示订阅地址，不生成二维码；
- Shadowrocket、v2rayNG：显示订阅地址并在 SSH 终端显示带白边二维码；
- Quantumult X 只输出 VLESS；
- Loon 使用无多余引号的 Salamander 混淆密码；
- v2rayNG 使用独立 `hy2://` 链接，不写入 `pinSHA256`。

## 订阅中心与备份

- 域名模式使用 HTTPS，并检查域名 IPv4 A 记录是否指向本机；
- 不使用域名时可用本机 IP + HTTP；
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
- 不包含 Debian 12、Alpine、OpenRC 或旧版本迁移兼容逻辑。
