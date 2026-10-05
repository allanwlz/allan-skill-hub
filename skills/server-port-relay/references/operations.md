# 部署、切换和回滚

## 环境发现

确认中转/目标 SSH 连接方式、账号、sudo、内核、发行版、IPv4/IPv6、私网接口及地址、域名解析、既有 VPN 和服务。先查看工作区与 git 状态；不要把整理目录变成未经授权的迁移。

部署目录按项目组织：`workspace/prd/<project>` 放运行配置和必要二进制，`dev/<project>/{src,build,downloads,archive}` 放源码、构建、下载与备份，`test/<project>` 放测试与结果。系统注册文件仍在 `/etc/systemd/system`。不要自动搬动 `/etc`、账号目录、容器数据或正在运行的进程工作目录。

检查 `ss -lntup`、`iptables-save`、nftables、Docker 链、`systemctl cat/status`。确认已有规则使用哪套后端，避免混用 iptables-legacy、iptables-nft 与原生 nftables。若保留范围与本机服务冲突，先停止部署并让用户选择，不杀掉既有服务。

## 隧道

示例地址仅用于设计：中转 `10.90.0.1/30`，目标 `10.90.0.2/30`。先检查是否与已有 VPN、容器及路由重叠。

- 优先使用 WireGuard 内核实现；目标 peer 的 AllowedIPs 只包含中转隧道 `/32`，中转只包含目标 `/32`，不要顺手设置 `0.0.0.0/0` 影响现有 VPN。
- 目标主动向中转的 UDP endpoint 建立连接，`PersistentKeepalive=25` 是 NAT 场景可选值。只在必要节点开放隧道 UDP 监听，云安全组来源可限制为对端公网 `/32`。
- 私钥在各自服务器生成，root 管理、0600；日志、git、聊天及本地下载均不包含密钥。生产服务账号不需要读取 WireGuard 私钥。
- MTU 1380 是本案例的可用起点，不是通用最优值。验证实际 PMTU、MSS 和隧道握手，再调整；不要仅因下载慢就不断降低 MTU。

旧内核缺少可用模块时，先评估维护成本；需要兼容才采用官方 `wireguard-go`。不要为修复单个中转问题自行升级内核或重启系统。

### 旧系统用户态实现的特殊问题

本案例旧系统上，`wg-quick`/`wg setconf` 与用户态实现组合出现配置应用后命令不返回。仅在重现并确认这一问题时考虑官方 UAPI，不要给所有机器都加绕行方案。

经过验证的启动顺序：前台运行 `wireguard-go -f <iface>`，等待 `/var/run/wireguard/<iface>.sock`（总等待有上限），设置接口地址并将 link/MTU 启用，再通过 Unix socket 发送 `set=1`、十六进制 private/public key、listen_port、replace_peers、allowed_ip 等字段，以空行结束；读取空行终止的响应，只有 `errno=0` 才算成功。socket 读写设超时，失败时删除本次创建的专属接口并让 systemd 重启。不要输出请求内容中的私钥。

参考：[官方 UAPI](https://www.wireguard.com/xplatform/)、[官方用户态源码](https://git.zx2c4.com/wireguard-go/)。使用前核对实际版本协议。

## 初始 NAT 模式

配置必须参数化：入口接口、入口私网地址、隧道接口、目标隧道地址、范围与专属链名前缀。云主机公网 DNAT 后，本机看到的目的地址通常是私网地址；不要盲目匹配公网 IP。

在 IPv4 iptables 模式下需要以下逻辑（本说明不是可直接执行的脚本）：

| 位置 | 作用 |
| --- | --- |
| nat PREROUTING → 专属 DNAT 链 | 仅匹配入口接口、目的地址、指定协议/范围，转发到目标隧道地址，不指定目标端口以保留同号端口 |
| nat OUTPUT → 专属链，可选 | 支持中转机器自身通过入口私网地址测试；不要重定向所有本机出口 |
| nat POSTROUTING → 专属 SNAT 链 | 仅隧道出口、目标地址、指定协议/范围，来源改为中转隧道地址，保证回程 |
| filter FORWARD → 专属链 | 匹配入口到隧道的业务流量与反向 ESTABLISHED/RELATED；不能开放整个 FORWARD |
| mangle FORWARD → 专属链 | 对进入/离开隧道的 TCP SYN 限制 MSS，可采用 clamp-mss-to-pmtu |

开启 IP forwarding 必须纳入持久化和回滚记录。Docker 可能将 FORWARD 默认策略设为 DROP，业务 ACCEPT 钩子需位于相应 DROP 之前；不改全局策略为 ACCEPT。规则用 `-C` 检查、`-w` 加锁，启动幂等，停止只删除专属钩子和链；不得清空整台防火墙。

目标应用须监听隧道地址或适当的非回环地址；仅 `127.0.0.1` 的服务不能由隧道访问。SNAT 会隐藏真实客户 IP，先告知用户。

## TCP 分段代理切换

先读 performance.md 并对单个空闲端口做对照，不先切整个范围。HAProxy 的 `server target <ip>` 省略端口时使用原目的端口；使用官方配置校验，不依靠记忆验证复杂语法。

模板位于 `assets/`。填写具体入口私网 IP、范围、目标隧道 IP、账号、路径及隧道/防火墙服务名。范围每个监听端口消耗一个 fd；10001 端口加上并发连接时需要调整服务 `LimitNOFILE` 并实测资源，而非无限放大 maxconn。选择合理并发、空闲超时与 keepalive，说明这与 NAT 的连接行为不同。

推荐事务边界：

1. 归档项目原脚本、服务定义与防火墙快照。不要把包含密钥的配置归档到可公开目录。
2. 校验代理配置，先在实际范围建立监听，确认没有失败/冲突。
3. 再撤去专属链中的 TCP 入站/本机 OUTPUT DNAT；UDP DNAT、隧道与回程保持原状。记录专用模式开关，使防火墙服务下次启动不会又加回 TCP DNAT。
4. 如单端口试验使用临时 RETURN 规则，确认切换时删除它。新连接生效；旧 NAT conntrack 连接可能继续走旧路径，不要清空整表。
5. 开机启用服务，定义依赖与重启顺序，验证防火墙/代理重启后规则、端口与速度。测试失败时恢复原 NAT，不在半切换状态停下。

短时试验必须安排自动清理。若 systemd 单元以普通用户运行，`ExecStopPost=iptables ...` 同样默认使用普通用户，可能清理失败；采用独立的 root 清理单元或受控 root 监督流程，并实际核对临时规则已消失，不仅检查服务已停止。

不终止 TLS、不添加 PROXY protocol 时，HAProxy 可透传业务字节。东京将看到中转的隧道来源；若业务要求真实客户 IP，需要单独授权、设计与后端适配，不直接开启 PROXY protocol。

## 验收与回滚

测试范围起点、中间、终点的 TCP 和 UDP；同时确认实际链/监听覆盖全范围。确认 SSH、网站、原有 VPN、容器和其他中转服务未中断。停止临时后端并检查无残留测试进程；生产代理在整个保留范围继续监听是预期行为，不应误删。

端口探测示例（替换占位符，服务器与客户端使用同一个本次测试标记）：
```sh
# 目标服务器：仅绑定隧道 IP；先确认这些端口无业务监听。
python3 scripts/port_probe.py serve --bind <target-wg-ip> --ports 20000,25000,30000 --marker <run-marker> --lifetime 180
# 客户端：通过中转入口测试，逐项报告成功次数。
python3 scripts/port_probe.py client --host <relay-host> --ports 20000,25000,30000 --marker <run-marker> --repeats 10
```

回滚顺序：先关闭代理模式开关并幂等恢复 TCP DNAT，再停止/禁用 TCP 代理；已有代理连接可能中断。保留 UDP 与隧道。不要直接 `iptables-restore` 整份历史快照覆盖 Docker 后续规则。

不必因为技能强调验证而擅自重启整机；只有授权且安排维护窗口才测试整机启动。交付中区分“服务重启通过”“开机已启用”和“整机重启已测试”。

官方参考：[WireGuard Quick Start](https://www.wireguard.com/quickstart/)、[HAProxy 配置](https://docs.haproxy.org/)、[HAProxy 下载与维护分支](https://www.haproxy.org/)。
