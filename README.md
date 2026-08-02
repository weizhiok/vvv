# VVV 一体化 VPS 工具

VVV 使用一个固定安装入口，在 **Debian 13 + systemd** VPS 上安装和管理代理、订阅与中转线路。安装命令可以重复运行：每次都会进入安装菜单，并按当前状态续装、修复或追加角色。

## 永久固定安装地址

```bash
{ command -v curl >/dev/null 2>&1 || { apt-get -o DPkg::Lock::Timeout=10 -o Acquire::Retries=2 -o Acquire::IndexTargets::deb-src::Sources::DefaultEnabled=false update && DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=10 -o Acquire::Retries=2 install -y curl ca-certificates; }; } && curl -fsSL --retry 5 "https://raw.githubusercontent.com/weizhiok/vvv/install/vvv-install.sh?$(date +%s)" | bash
```

`install` 是固定入口分支，会自动取得 `main` 分支经过验证的最新安装程序。无论首次安装、SSH 中断后续装，还是已经安装后追加其他角色，重新运行同一条安装命令都会先显示安装菜单。安装完成后统一输入：

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
0. 退出
```

选择角色后，脚本会在真正安装前一次性收集该角色需要的全部参数：

- VLESS、Hysteria 2 或双协议；
- 统一代理端口，默认 `443`；
- REALITY 伪装域名，默认 `www.softbank.jp`；
- 订阅域名/IP、传输方式、服务端口与统一订阅后缀；
- 订阅中心接入码或 JPR3 对接密钥。

订阅中心支持直接 HTTPS、直接 HTTP、Cloudflare Tunnel 三种传输方式。统一订阅后缀直接回车时随机生成 8 位大小写字母和数字；手动输入允许 6–32 位。参数总览显示后直接开始安装，不再要求二次确认。

重复安装规则：

- 已安装的本机代理会复用原协议、端口和永久凭证，不重新生成节点；
- 已安装的订阅中心会保留订阅密钥、已注册主机和备份数据；
- 旧版 schema 2 订阅中心会在安装菜单出现前原地升级为统一订阅入口，自动生成新的 8 位后缀，并保留主机令牌、注册记录、节点、备份和证书；旧四路径同时失效；
- 后续选择新的角色时，只追加缺少的模块，并自动合并最终角色。例如先安装菜单 2，再运行菜单 3，最终会成为“订阅中心 + 中转主机 + 自身代理”；
- 直连副机注册时可输入 IP、域名或完整 HTTP/HTTPS 地址；输入裸地址时依次探测 HTTPS 8443、HTTPS 443 和 HTTP 8443；首次留空后，可随时输入 `vps` 补注册；
- 直连或中转注册只有在订阅中心接收首份节点状态并重新生成订阅后才算成功，SSH 会以绿色显示“订阅中心注册成功”；
- 已安装的订阅中心在重复运行安装器时会自动刷新服务程序，但保留订阅密钥、节点数据和备份；
- 中转副机必须输入完整 JPR3 对接密钥，留空或格式错误都不会开始安装；
- SSH 在参数输入或源码下载期间中断，不会再把 VPS 判定为“必须重装系统”；
- 订阅中心安装中途断开时，下次选择带订阅中心的角色会先备份残留，再清理不完整组件并续装；
- 中转副机与本机代理/订阅中心/中转主机不能安装在同一台 VPS，但安装菜单仍会正常显示并给出明确提示。

## 代理与中转架构

- VLESS：Xray，TCP 监听；
- Hysteria 2：sing-box，UDP 监听；
- 双协议可以使用同一个端口号，因为 TCP 与 UDP 相互独立；
- 主 Xray 与主 sing-box 的固定配置在安装后不随线路增删变化；
- 每条 VLESS/HY2 中转线路由独立槽位服务承载；
- 新建、覆盖或删除线路只操作目标槽位，不重启主 Xray 或主 sing-box；
- 删除线路后的 UUID、用户名与密码永久退役，不会被其他线路重新使用。

## 客户端订阅

所有支持客户端共用一个订阅地址，例如：

```text
https://v.example.com:8443/Ud2xR9zN
```

服务端根据客户端请求头自动返回 Clash Verge Rev/Mihomo、Quantumult X、Loon 或 Shadowrocket 格式。旧 `/r/密钥/c`、`/qx`、`/ln`、`/sr` 路径及 `format` 查询参数均已移除。未知客户端返回 415；可在 `vps → 订阅中心管理 → 客户端请求头识别调试` 中查看脱敏请求信息。客户端适配器可独立更新，不重装系统、不修改节点数据。

## 订阅中心传输方式

- 直接 HTTPS：域名由 Caddy 自动申请公共证书；公网 IP 由 Certbot 申请 Let’s Encrypt 短期 IP 证书；
- 直接 HTTP：不申请证书，适合频繁重装测试，但节点凭据以明文传输；
- HTTP 模式可在 `vps` 菜单事务式开启 HTTPS；失败自动恢复 HTTP，成功后原 HTTP 入口立即失效；
- Cloudflare Tunnel：公开地址使用标准 `https://域名/后缀`，VPS 只提供本地 HTTP 源站，迁移服务器时可保持客户端地址不变；
- Tunnel 模式需要提前在 Cloudflare 创建 Tunnel 公共主机名，并将其指向脚本显示的本地 HTTP 地址；
- 已注册 VVV 主机从 HTTP 中心同步时会优先尝试同地址 HTTPS，成功后永久升级且不再降级。

## 订阅中心与备份

- 节点变化后立即同步完整快照；
- 本地备份使用加密容器，只在首次安装或数据变化前后自动创建；
- 不设置定时备份，不提供手动备份菜单；
- 云备份默认关闭，可选 Google Drive 或 Microsoft OneDrive；开启后加密包自动包含 Let’s Encrypt、Caddy 域名证书及 Cloudflare Tunnel 配置；
- 开启后使用 rclone `copyto` 上传独立加密备份，不使用 `sync` 删除云端历史；
- 其他代理或中转 VPS 只同步自身节点快照，不保存订阅中心备份。

## 系统要求

- 仅支持 Debian 13；
- 必须使用 systemd；
- 使用 root 用户执行；
- 固定安装命令会在缺少 curl 时先通过 APT 安装 curl 和 CA 证书；
- APT/dpkg 锁最多等待 10 秒，超过后立即显示错误，不删除锁文件、不强行终止系统更新；
- 主安装阶段一次性安装订阅中心所需的 `python3-venv`，避免代理完成后再次调用 APT；
- 安装时关闭无用的 `deb-src` 索引下载，减少软件源警告和等待；
- 不包含 Debian 12、Alpine、OpenRC 或旧版本迁移兼容逻辑。

## 安装策略

安装入口支持重复执行和角色追加，但仍不迁移 Debian 12、Alpine、OpenRC 或其他旧系统方案。每次运行都会刷新并验证安装源码，然后进入安装菜单；现有完整模块会被保留，只安装所选角色缺少的部分。
