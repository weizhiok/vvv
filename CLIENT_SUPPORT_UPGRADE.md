# 客户端支持独立升级

VVV 的“客户端支持升级”与完整安装、代理核心维护完全分离。

## 唯一正确的升级入口

在任意已安装角色的 VPS 输入：

```bash
vps
```

选择 `0. 退出` 上方最后一项：

```text
升级客户端支持
```

默认升级地址：

```text
https://raw.githubusercontent.com/weizhiok/vvv/client-support/client_upgrade.py
```

按回车使用默认地址，也可以临时输入一个完整 HTTPS URL。自定义 URL 只对本次执行有效，不会修改系统默认值。

## 自动识别角色

升级引擎自动识别：

- 包含订阅中心的 VPS 主机；
- 不包含订阅中心的直连副机；
- 不包含订阅中心的中转副机。

包含订阅中心时，会同步更新订阅请求头识别及订阅格式；其他角色只重新生成本机客户端配置。

## 永远不会执行的操作

客户端支持升级不会：

- 运行 APT 或升级系统软件包；
- 更新内核、Swap、BBR、时区或重启策略；
- 下载、替换或重启 Xray、sing-box、Caddy、cloudflared；
- 重写代理配置或 systemd 单元；
- 修改节点、端口、UUID、密码、Reality 密钥、HY2 证书或 JPR3；
- 重装角色、重装系统或重启 VPS。

包含订阅中心且客户端模块确有变化时，只允许重启 `vvv-sub.service`，不会中断代理连接。

## 硬保护

升级前后会比较：

- Xray、sing-box 二进制 SHA-256；
- Xray、sing-box 配置 SHA-256；
- 主机/副机节点状态 SHA-256；
- 订阅中心和同步配置 SHA-256；
- Xray、sing-box systemd 单元 SHA-256；
- 当前内核版本；
- Xray、sing-box PID 与进程启动时间。

任何受保护项目发生变化，升级立即失败并恢复旧客户端适配器及旧客户端输出。

## 新客户端请求头调试

订阅中心进入：

```text
客户端请求头识别调试
```

显示的每条 JSON 都会附带 `client_support_handoff`，其中包含：

- 仓库：`weizhiok/vvv`；
- 分支：`client-support`；
- 目标文件：`client_upgrade.py`；
- 默认升级 URL；
- 当前客户端支持版本；
- 新 ChatGPT 对话所需的修改说明和安全边界。

把完整 JSON 发到新的 ChatGPT 对话后，对方应只修改 `client-support/client_upgrade.py`，不得修改完整安装器、代理核心、节点或系统设置。

## 禁止使用完整安装器升级客户端

新增或修复客户端支持时，不得重新运行完整 VVV 安装器并重复选择现有角色。完整安装器用于首次安装、角色安装和完整维护；客户端支持只能使用独立菜单。
