# 带宽定位与同端口对照

## 不混淆方向与指标

对下载，明确“目标 → 中转 → 客户端”，客户端是接收端。不要从反向上传推断下载；不要把历史测速当作当前速度。

默认样本 16 MiB = 16,777,216 B。报告实际收到的字节数、耗时、是否完成和超时上限。
`Mbps = bytes × 8 / seconds / 1,000,000`；`MB/s`、`MiB/s` 不混用。curl 的 `speed_download` 为 B/s。测试内容不压缩、尽量不涉及磁盘；SSH 下载零字节时明确关闭 Compression。协议不同仍是非严格对照。

## 按路径定位

下载测试顺序执行，避免同时争抢链路：

1. 目标到中转：中转直接通过隧道下载目标 HTTP 测试文件。
2. 中转到客户端：尽量用相同协议/内容；只能 SSH 测试时注明协议、端口、发送端算法都不同。
3. 目标经中转到客户端：同一个入口端口下载同一内容。
4. 切换一个端口至 TCP 代理后，重复第 3 项；只有这项明显改善才推广。

`scripts/speed_probe.py serve --bind <target-wg-ip> --port <test-port> --lifetime 300` 会提供 `/speed-test`；`download --url http://<relay>:<port>/speed-test --expected-bytes 16777216 --timeout 120` 不使用系统 HTTP 代理。默认内容是零字节，仅适合不压缩的吞吐诊断，不代表真实业务负载。

给临时服务配置 RuntimeMaxSec 或脚本 lifetime，捕获退出状态并清理。客户端超时不算完成；短样本平均值与完整样本只能作有条件比较。

## 采样 TCP 与接口

传输中在真正的发送端查看 `ss -tin`，关联正确的测试端口/对端：

- 拥塞算法、RTT/minrtt、MSS/PMTU、cwnd、ssthresh、retrans、bytes_retrans。
- reord_seen、发送队列、notsent、接收窗口、delivery_rate。
- `sysctl net.ipv4.tcp_congestion_control` 是默认配置；当前 socket 的实际算法以 ss 为准。
- CPU、负载、接口错误/丢弃、WireGuard 握手/收发与防火墙规则命中。

反复缩小的 cwnd、持续重传、充足的待发数据和接收窗口支持“拥塞控制在限制发送”，不等于已经确定某家运营商限速。重传字节占比不等于链路丢包率，乱序也可能触发重传。

隧道接口零丢弃不能排除公网丢包。10 次 ping 无丢包不能证明大流量 TCP 路径可靠。累计 CPU 平均使用率也不能排除瞬时 CPU 瓶颈；实际隧道高吞吐是更有力的容量证据。

当前隧道很快、端到端很慢时，TCP 分段可让目标与中转单独确认数据，中转承担客户端侧拥塞控制。客户端侧如果采用 BBR，可能更耐受部分丢包；不能保证提升，也不能宣称只有 CUBIC 是唯一原因。代理同时改变 RTT、算法、MSS 和连接边界，需要说明对照的局限。

精确定位网络段需要两端时间同步的包头抓取：中转公网入口/出口和目标隧道抓取同一测试连接，关联序号、ACK、重传和乱序。只抓测试流量，避免业务 payload/密钥，设置时限，未经授权不安装抓包软件或公开上传 pcap。

发现 UDP 偶发超时应重复样本，报告收到 X/N；不要把一次超时当配置故障，也不要把一次成功说成稳定无丢包。测试后确认临时服务仍在运行，排除 lifetime 到期造成的假失败。

参考：[Linux TCP 参数](https://kernel.org/doc/html/latest/networking/ip-sysctl.html)、[ss 手册](https://man7.org/linux/man-pages/man8/ss.8.html)。
