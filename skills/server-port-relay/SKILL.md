---
name: server-port-relay
description: 部署、诊断或修复经中转服务器访问目标服务器的同号端口转发，涵盖 WireGuard、TCP 分段代理、UDP NAT、分段测速与回滚。适用于跨地域服务器中转，不用于一般网站开发或客户端 VPN 安装。
---

# 跨服务器端口中转

目标：客户端通过中转服务器的域名/IP 和指定端口范围访问目标服务器；在不破坏已有服务的前提下，验证可达性与实际速度，并保留回滚能力。

## 先确定任务模式

- 分析/诊断：进行只读检查和有时限的临时测速，不据此修改生产配置。
- 部署/修复：用户授权后才能改防火墙、安装软件或切换代理。云控制台安全组需要用户手动处理时，准确给出协议、端口和来源范围。
- 测速方向必须写成“发送端 → 接收端（接收端下载）”；不要只写“香港 → 东京速度”。

先读取 [references/operations.md](references/operations.md)。首次设计或遇到中转不提速时，同时读取 [references/performance.md](references/performance.md)。维护已有案例时可参考 [references/case-study.md](references/case-study.md)，其中的历史数字不能当作当前结果。

## 关键判断

NAT 经隧道转发不会拆分 TCP；拥塞控制仍在业务发送端。中转服务器开启 BBR，不会让转发的连接自动使用 BBR。隧道吞吐高也不保证端到端吞吐高。

需要网站反向代理、域名和证书管理时选择 HTTP/HTTPS 代理；需要原样透传 TCP/TLS 或同号端口范围时可以选择 HAProxy TCP 代理。UDP 不应套用 TCP 代理方案，按业务保持 NAT 或设计独立 UDP 代理。只有同端口对照验证明显改善后，才将试验方案推广至生产范围。

## 可复用资源

- [scripts/speed_probe.py](scripts/speed_probe.py)：标准库实现的有时限 HTTP 下载测试，默认 16 MiB；客户端统计完整字节数和吞吐，支持仅绑定本地回环地址的 selftest。
- [scripts/port_probe.py](scripts/port_probe.py)：TCP/UDP 同号端口探测；标记必须由本次任务指定，以免误把别的服务当成成功。
- [assets/haproxy.cfg.template](assets/haproxy.cfg.template)：TCP 范围代理配置。所有占位符需要填入已确认值；它不提供 UDP 或证书终止。
- [assets/tcp-proxy.service.template](assets/tcp-proxy.service.template)：非 root 运行的 systemd 示例，须匹配实际目录、账号和服务名。

配置是示例，不是“一键修复”。不要执行带占位符的模板，不要照抄历史公网地址、私钥、机器名或旧版本号；核实官方文档、当前维护版本和主机兼容性。脚本不负责开放公网端口或更改生产防火墙。

## 完成标准

记录修改前后的同路径、同内容测速；检查 TCP/UDP 范围两端及中间点，不能将三个样本描述为扫描了全部端口。验证专属规则、启动顺序、服务重启恢复及原有业务存活；说明是否测过整机重启。

临时服务、临时规则和测速监听必须清理，并确认生产服务保留。交付入口、范围、协议差异、来源地址、连接超时、已知限制和确切回滚命令。公网仍有波动时，明确说明，不承诺固定吞吐。
