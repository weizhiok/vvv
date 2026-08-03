# 中转副机 + 自身代理

- 自身直连代理：TCP/UDP 443，使用 `xray.service` 与 `sing-box.service`。
- 中转副机：TCP/UDP 553，使用 `vvv-landing-xray.service` 与 `vvv-landing-sing-box.service`。
- 组合安装只输入一次 JPR3；其中包含与线路绑定的订阅中心受限注册票据。
- 订阅中的中转节点统一命名为 `国家-协议-中转-日本入口IP:端口`。
- 副机自身直连节点继续使用 `国家-协议-副机IP:443`。
- 客户端支持升级会保护四个代理服务及两套配置，不能重启或改写它们。
