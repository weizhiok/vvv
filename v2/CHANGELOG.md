# VVV v2 变更说明

## 2026-07-30

- 安装角色菜单顺序统一为订阅中心、中转主机、中转副机、直连代理、全部安装。
- 所有安装参数改为开始安装前一次性收集；选择“全部安装”时，订阅域名和订阅端口也在代理安装前设置。
- VLESS + REALITY 伪装域名支持自定义，默认 `www.softbank.jp`；现有主机重新运行时也可以在前置参数阶段修改，失败会恢复原状态和 Xray 配置。
- 订阅服务绑定 `0.0.0.0`，安装或覆盖时强制重启服务，并执行本机健康检查。
- 软件防火墙处于启用状态时，自动尝试开放订阅 TCP 端口；云厂商安全组仍需由用户账号侧允许。
- 客户端订阅默认刷新周期为 24 小时。
- 订阅中心为 Quantumult X、Loon、Shadowrocket、v2rayNG 显示 ANSI 二维码；Clash Verge Rev 仅显示地址。
- 五种客户端订阅均从统一节点快照生成；节点新增、覆盖或删除后重新生成。
- 增加订阅中心加密远程备份接口及非中心主机的定时备份拉取。
- 新增永久固定入口：

```bash
curl -fsSL --retry 5 https://raw.githubusercontent.com/weizhiok/vvv/install/vvv-install.sh | bash
```

`install` 分支只保存固定入口，实际程序继续从 `main` 分支更新。
