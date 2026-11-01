# 隔离网络专项

`network_probe.c` 与 `network_matrix.py` 只连接客体的 `10.0.2.2` 和宿主随机 loopback 端口，不连接外部网站。客体只写入带随机 owner 的 `/data/local/tmp/modavd-netqa-<token>/probe`，完成后逐个清理；不启动或接管 ADB，不修改系统网络设置。

Android 模式固定使用既有的 disposable reclaim QA 身份、私有 ADB 20150 / console 20160，并要求显式提供当前 QEMU PID。每次 ADB 操作重新验证记录的 PID/启动身份和 AVD 名称。请与内存实验协调客体使用时间，不另起实例绕过内存保护。

```sh
.macos-work/android-ndk-r27d/toolchains/llvm/prebuilt/darwin-x86_64/bin/aarch64-linux-android34-clang -std=c11 -O2 -Wall -Wextra -Werror ports/macos/qa/network_probe.c -o .macos-work/qa/network-probe-arm64
python3 ports/macos/qa/network_matrix.py --binary .macos-work/qa/network-probe-arm64 --expected-pid PID --output .macos-work/qa/network-matrix-guest-NEW.json
```

| 项目 | 有界负载和校验 |
| --- | --- |
| 多连接 | 160 条 TCP 全部提交请求后才一起发送，证明同时存活超过 128；逐字节校验每路 8 KiB 并要求 EOF。 |
| 单连接 | 32 MiB，逐字节校验，记录耗时。 |
| 并行传输 | 8 路 × 4 MiB，逐字节校验，记录耗时。 |
| 半关闭 | 客体先 SHUT_WR，仍正确收取 4 KiB 和 EOF。 |
| 拒绝连接 | 宿主前后证实选中端口无 TCP listener，客体必须得到 ECONNREFUSED。 |
| 重置 | 宿主 SO_LINGER=0 主动 RST，客体必须得到 ECONNRESET。 |
| UDP | 160 个独立 socket，每路带 token/index 的 256 B 数据原样回显。 |
| 故障后恢复 | 上述错误及 UDP 后，再建立新 TCP 并完整读回 8 KiB。 |

宿主对照使用同一 C 源码与 Python 服务：

```sh
clang -std=c11 -O2 -Wall -Wextra -Werror ports/macos/qa/network_probe.c -o .macos-work/qa/network-probe-host
python3 ports/macos/qa/network_matrix.py --local --binary .macos-work/qa/network-probe-host --output .macos-work/qa/network-matrix-host-NEW.json
```

2026-11-01 的[宿主对照 v2](../../../.macos-work/qa/network-matrix-host-v2-20261101.json) 八项通过。首次对照使用 bound/non-listening TCP socket，Darwin 对该端口 SYN 超时，不能用作拒绝连接的预期；原失败证据保留，v2 改为前后检查真实无 listener 的端口。宿主结果不代替 Android/slirp 结果。

同日 [Android/slirp 实机](../../../.macos-work/qa/network-matrix-guest-20261101.json) 八项也通过：宿主确认同时活跃 TCP 最高 160、UDP 回显 160、最终 active=0；拒绝返回 errno111、重置 errno104，数据校验无错，boot ID 不变，客体临时目录清理成功。32 MiB 单路为2098 ms，8×4 MiB 为4135 ms，包含逐字节校验，且宿主同时编译 renderer，不用于性能提升判断。[产物关联收据](../../../.macos-work/qa/network-matrix-guest-provenance-20261101.json) 固定为 PID58200 的 pre-ASG media 候选；虽然请求了暂停审计环境，PAGE_POISON 门槛导致实际未进入暂停路径，故本结果不证明暂停或回收正确。

此矩阵不覆盖 TCP dead-peer keepalive、外部黑洞、ICMP fallback 洪泛、实际 CDN、丢包网络或长时间稳定性。耗时包含数据校验和本机调度，没有上游同条件基线，不能报告吞吐提升百分比。

后续[实际回收事务开启的 v4 网络复测](../../../.macos-work/qa/network-matrix-guest-reclaim1-20261101.json)八项通过，但它与另一次内存探针没有时间重叠，旧证据没有改动。v5 的[网络矩阵](../../../.macos-work/qa/network-matrix-guest-v5-concurrent-20261101.json)与[128 MiB 内存探针](../../../.macos-work/qa/reclaim-probe-v5-network-concurrent-20261101/capture.json)从同一次工具调用并行启动，均通过；[独立时间戳复核](../../../.macos-work/qa/network-memory-overlap-v5-20261101.json)确认4.274秒重叠，内存21次活数据校验无错。该重叠以网络整轮宿主窗口计，不代表八个case分别全部重叠。v5是私有实验核心，生产包未因此默认启用回收，目标mincore变化亦不作为旧物理页释放证据。
