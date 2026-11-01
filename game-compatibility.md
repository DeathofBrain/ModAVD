# 明日方舟 B 服：官方 AVD 独立兼容性报告

本报告供 issue #28 的第二条评论引用，测试日期为 2026-11-01。结论：安装、登录前显示、MAA 单帧取图有直接证据；用户随后报告“已下载完成并进入游戏主界面”，但尚无对应完成截图或 MAA 日常核验，**不能据此判定完整游戏兼容通过或能跑一轮日常**。

## 硬件

| 项目 | 配置 |
|---|---|
| CPU / GPU | MacBook Air M4 / Apple M4 |
| 内存 / 系统 | 16 GiB / macOS 26.6.2，build 25G83 |
| AVD | official_game，4 vCPU、4096 MiB、1280×720、全新 32 GiB 数据盘 |
| 加速 / 图形 | Hypervisor.Framework / GPU host |

宿主页面为 16 KiB，客体页面为 4 KiB。32 GiB 是数据盘配置容量，不是实际已占用空间；本次没有使用此前扩容失败的克隆替代全新实例，也没有把其他空载实例的测试归入游戏结果。

## 软件

| 项目 | 版本与范围 |
|---|---|
| ModAVD / AUTO-MAS | 不适用；本次为独立 Google 官方 SDK 与全新 userdata |
| Emulator / ADB | 37.2.13.0（16740729）/ Platform Tools 37.0.1 |
| 镜像 | API 34，google_apis，arm64-v8a，revision 14 |
| 游戏 | 明日方舟 Bilibili，com.hypergryph.arknights.bilibili |
| 游戏分阶段版本 | 前期安装为 2.7.81 / code 200；后续窗口更新为 2.7.91 / code 210，不能混用 |
| 自动化脚本 | MAA；Maa Verge 0.1.3 打包 service/worker，MaaCore v6.20.1，资源 mirrorchyan:2026-10-31 16:10:56.000；Tauri GUI 未测 |

## 结果

安装返回 Success，耗时 15.154 秒。首次启动返回桌面，后续三次独立冷启动均到达未同意的协议页，未再次复现；首次失败原因未确定。该阶段全部 headless，不能称为游戏实际运行场景验证。证据：`results/game/runs/1793511406217923000-825db5d4/game.json`、`results/game/prelogin-retests.json`。

登录前 MAA 实际连接成功一次，截图方式 RawWithGzip；取得一帧 1280×720 协议页，PNG 校验通过。device.capture 为 73 ms，device.frame latencyMs 为 84 ms，RPC 往返 85.519 ms，三个字段口径不同，且单帧不代表稳定性能。取图不等于识别、点击控制或日常通过。证据：`results/game/prelogin-maa-probe-1793511829426254000.json`。

后续由用户在窗口中手动处理登录与更新。包版本已核实为 2.7.91；用户随后在此前测试聊天中明确报告下载完成并进入主界面。此处仅引用用户观察，现有归档没有该完成时刻的独立画面核验。目录中的 progress.json 停留在较早快照，不能覆盖后续用户确认。

尚未运行的日常方案为纯收取，碎石、药及加急预算均为零；配置准备与静态审阅不算执行证据。实际仍须按唯一运行编号核对整轮和各子任务状态，并结合画面判断是否有可领取项；任务无奖励可领时也不能虚构领取成功。

窗口登录/资源阶段于 18:04–20:04（UTC+8）完成约 7200 秒采集：内存 1441 点、间隔 5 秒、host/guest 采样错误均为 0。模拟器 phys_footprint 为 6.593→8.555 GiB，峰值 8.563 GiB；RSS 为 2.672→0.710 GiB，峰值 5.418 GiB；guest MemAvailable 为 1.962→1.793 GiB。整机 swap 为 3.738→6.733 GiB，不能全部归因模拟器，也不足以判断泄漏。

同阶段网络有 1440 点、0 采样错误；1439 个有效 wlan0 区间合计 7195.060 秒，RX 15,399,125,626 B（14.342 GiB），TX 220,524,838 B。这是客体全应用及后台接口流量，不能当作游戏资源包大小。另有 3600 秒辅助 TCP 对照：host/guest ACK 3587/3596，均为 1 连接、0 重连、0 心跳/连接/socket 错误；最大 ACK 间隔 2.672/2.390 秒。端点为同一 FakeIP，宿主可能经过 TUN/代理，未证明直连公网。

上述摘要分别见 `results/game-final/20261101-180339/download-memory/memory_summary.json`、`results/game-final/20261101-180339/download-network/summary.json`、`results/game-final/20261101-180339/download-tcp-control/observation/network_summary.json`。**窗口阶段两小时采集与一小时辅助 TCP 均不能替代进入游戏后的 headless 两小时长测。**

尚未完成：StartUp→Infrast 纯收取→Award 的实际任务与画面核验；真实非空近期游戏文件的 no-sync 克隆测试；独立 headless 7200 秒游戏内存及四路 TCP 长测；本轮最终清理核验。早期缓存样本只是 0 B→0 B，派生解释仍判非空内容持久性阻塞，见 `results/game-cache/e2a0a73b8aae-interpretation.json`。本报告不以进程退出码、空文件一致或接口 canControl 字段替代这些结果。

缓存观察范围只涉及包私有目录中的候选文件，不覆盖所有下载资源；同 UID 的 SDK 写入也可能入选。即使取得一个非空一致样本，也只能支持该文件该次观察，不能外推为全部游戏缓存、设置或各关机延迟条件均可靠。
