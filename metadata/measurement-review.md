# 内存与 TCP 测量复审

复审对象：`soak_tests.py`、`probes/avd_probe.c`；只读检查官方测试流程，未操作 guest、模拟器或开始长测。发现的证据与判定缺口已按协调代理授权修正；默认负载和时长保持不变。

- **3 轮内存负载**：默认每轮 mmap 1 GiB 匿名私有内存，按 4096 字节步长执行 volatile 非零写入，再写最后一个字节；每页均为 1～255 非零值。持有 60 秒后 munmap；退出码和 touched/released 事件均校验。此项是匿名内存释放，不会清缓存或改变官方回收策略。实际 guest 页大小仍应由环境记录核实；步长不大于客体页大小即可覆盖每页。
- **600 秒观察**：每轮在释放并收到 ADB 返回后开始完整等待 600 秒。新增 UTC/monotonic 起止、`actual_observe_seconds` 和 `observation_completed`；中断不能计入完成轮。CSV 首末采样点约覆盖 595 秒属于采样端点现象，不等同于少等 5 秒。
- **采样可靠性**：宿主 RSS/phys_footprint 使用同用户的 libproc，不需 sudo；压缩和 swap 是全宿主指标。新增后台致命异常捕获与主线程传播，等待可由错误及时唤醒；单次采样错误保留在 CSV/summary 中，报告必须检查其数量。5 秒是目标采样周期；慢采样真实耗时写入 `sample_duration_ms`，不可假称严格等间隔。
- **7200 秒 TCP**：双端各自按本地 monotonic deadline 运行；全局观察不会因 guest 提前退出而静默缩短。阻塞请求可能使结束稍晚于 deadline。summary 新增每端明确状态：需要结束事件、足够事件时长、覆盖观测窗口的已校验心跳、零错误；只有 `completed` 不能判通过。时间覆盖容差默认 2 秒，心跳跨度容差为初始 bulk 实测时长加 3 个心跳间隔，均写入结果。
- **传输与恢复**：guest 循环处理短 send/recv，host 使用 sendall 并循环 recv，完整回显逐字节校验；错误关闭连接、退避 1 秒后重连。`recovery_seconds` 兼容旧字段，明确表示“检测到错误后到恢复 ACK”；新增“失败请求起点到恢复 ACK”和重连握手耗时。任何一个都不能声称已知物理断网的精确起点。`verified_received_bytes` 只计校验完整的载荷；原始字节总数可能含失败的部分 I/O。
- **对照与归因**：host/guest 的 monotonic 值不跨机相减，只比较各自 RTT、持续时间与错误。127.0.0.1 对 10.0.2.2 仅验证 AVD NAT 路径；公共 echo 是补充远端路径。当前 tcpbin.com DNS 返回 198.18.0.4，可能经过 FakeIP/TUN，不可写成直连公网；远端服务失败也不能仅归因于 AVD。

宿主验证：`py_compile` 通过；注入监控线程异常后主线程立即收到错误；2 秒真实宿主 loopback 回显完成 32 MiB bulk 和 19 个心跳；缺少 guest 证据时整体判为 blocked。所有短测均使用临时目录，未进入正式结果。

`run_soak.py` 提供单 MemoryMonitor 下的双组对照：本地维持 64 KiB/秒及初始 32 MiB bulk，公共远端组固定 64 字节/秒且 bulk=0。C 探针支持该低负载；正式启动前由协调代理完成 10 秒 guest 远端预检，runner 只验证提供的预检日志，不能自行替代该步骤。未登录游戏时必须使用非游戏 workload 标签，游戏长测继续标为阻塞。

## 正式长测前的消息格式修正

首次远端预检中，二进制 64 B 载荷只返回首个 LF 之前的 11 B。相同宿主诊断也复现；改为 63 个可打印字节加 LF 后完整回显，见 `results/remote-service-framing-diagnostic.json`。因此这是测试载荷与按行服务的格式不匹配，不计作 AVD 故障。首次短时本地观察已优雅中止、sync 停机，并完整归档于 `results/history/idle-first-line-framing-mismatch/`，不计入正式 7200 秒。

远端正式探针采用 `line64`：前 16 字节为序号、后续可打印字节、末尾单个 LF，逐字节验证完整回显。预检核对 `network_config` 中的端点、64 B/秒、无 bulk 和 framing，且要求连续序号、约 10 秒运行及无错误。本地组继续使用原二进制载荷。新探针 SHA-256 为 `8d8b76ea4b9b1ce5181ee2de338ac7fd0524b7c104bfe0b534b19a9534c95a74`；完成三轮内存测量所用的旧源码、二进制及构建 manifest 留存于 `probes/history/pre-line-framing/`，不能把新哈希代入旧测量。

正式判据还要求相邻已校验 ACK 最大间隔不超过 `max(3×目标间隔, 3秒)`，数量达到目标数量的 99%，并独立满足完整观察时长、ACK 首末跨度及零错误。99% 是启动前设定的调度容差：客户端按相对 sleep 发请求，目标 1 秒并非硬实时周期。该容差不允许用少量首末 ACK 掩盖中断；实测 ACK 数、最大间隔、RTT 及错误仍须分别报告，不能把心跳调度不足直接归因为 TCP 断线。
