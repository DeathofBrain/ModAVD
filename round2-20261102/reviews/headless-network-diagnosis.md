# 无窗口长测：网络证据与验收口径

网络连续性验收状态：`invalidated_by_user_network_change`。

用户已明确说明测试期间自行切换网络，并要求这次网络异常“不算”。因此本轮网络连续性受环境中断影响，不计模拟器缺陷，也不能将它改写成网络连续性通过。以下原始失败全部保留；本说明不修改 `headless-game-soak/` 内 JSONL、CSV、metadata 或 summary。

## 已记录的辅助 TCP 事件

| 探针范围 | 原始结果 | 事件与时间 |
| --- | --- | --- |
| Host 本机回环 | passed | 7171 次有效 heartbeat ACK，0 错误、0 重连。 |
| Guest → Host `10.0.2.2:63085` | failed | 1 次 heartbeat error：guest monotonic `4462466519086` ns，距 guest config 约 4144.996 秒，errno 104，发送 65536 B、接收 0 B。随后重连并收到有效 ACK；检测错误至 ACK 1.506772334 秒，失败请求开始至 ACK 1.507247250 秒，最大相邻有效 ACK 间隔 2.497553668 秒。 |
| Host → 远端 echo | failed | 北京时间 2026-11-02 19:34:55.163220 最后有效 ACK，19:34:56.051624 首次 heartbeat error（errno 54，接收 0 B）。总计 510 次 heartbeat error、1 次 connect error、509 次重连，后续无有效 ACK；最后错误 20:26:06.168238。 |
| Guest → 同一远端 echo | failed | 最后 ACK：guest monotonic `4451164570122` ns；首次错误 `4451945603331` ns，距自身 config 约 4134.472 秒。总计 510 次 heartbeat error（errno 104）、1 次 connect error（errno 115）、509 次重连，后续无有效 ACK。 |

Guest JSONL 没有 UTC 字段，host 与 guest monotonic 时钟不能直接相减。表中 guest 时间只按其自身起点计算，不伪造精确墙钟时刻。本机 guest 错误在 guest WAN 首次错误之后约 10.521 秒；这是同一 guest 时钟内可比较的顺序，不等于已确定网络切换的准确时刻。

## 解释边界

- 远端配置为 `tcpbin.com:4242`，记录的连接地址 `198.18.0.10`，使用 64 B、line64 echo。metadata 已注明当前路径可能经过 FakeIP、TUN 或代理，不能据此认定直接公网路由。
- Host errno 54 对应连接重置；host 10001 是包装层对无有效 errno 异常的归一值，不能一律解读成内核错误或内容校验失败。后续 509 次该值的记录均接收 0 B、耗时约 5 秒，与读取未完成相符，原日志未保存异常文本，无法进一步区分。
- Guest probe 会把接收 EOF 合成为 104，故 guest errno 104 只能确认本次请求没有完成，不能单凭该值区分真实 `ECONNRESET` 与 EOF。connect error 115 保留原值，不据此独立定位根因。
- `connected` 和重连计数表示套接字建立成功；恢复必须有后续校验通过的 ACK。本机 guest 确实恢复，远端 host/guest 均未恢复有效 echo；`network_complete` 仅表示循环结束。
- 这些是独立辅助 TCP 探针，无法证明游戏连接断线、游戏服务器状态或游戏会话是否恢复。不得把辅助 TCP 失败写成已证实的游戏断线。

本轮处理只依据既有文件与用户归因完成，未操作 AVD、未调用 ADB、未进行主动网络测试。

原始证据：`headless-game-soak/{host_network,guest_network}.jsonl`、`headless-game-soak/network_summary.json`、`headless-game-soak/wan/{host_network,guest_network}.jsonl`、`headless-game-soak/wan/network_summary.json` 及两处 `metadata.json`。错误值映射参照 `probes/avd_probe.c` 与 `soak_tests.py`。
