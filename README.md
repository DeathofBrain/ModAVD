# issue #28：Google 官方 AVD 在 Apple Silicon 上的基线实测

对应 [ModAVD/ModAVD#28](https://github.com/ModAVD/ModAVD/issues/28)。测试日期 **2026-11-01（Asia/Shanghai）**；本提交于 2026-11-02 汇总既有记录，没有新增测试。SDK、系统镜像和 userdata 独立准备，未使用 ModAVD 二进制、补丁或既有数据盘。

**基础项已完成并保留失败及复测；游戏已安装，用户确认资源下载完成并进入主界面，MAA 日常、真实非空游戏文件持久性和进入游戏后的正式无头长测尚未完成。** 本报告不将整张 issue 清单宣告通过。

## 环境

| 字段 | 实测值 |
|---|---|
| 硬件 | MacBook Air / Apple M4 / 16 GiB |
| 系统 | macOS 26.6.2，build 25G83；宿主页 16384 B |
| 官方 Emulator | 37.2.13.0，build 16740729，aarch64 |
| Platform Tools | 37.0.1 |
| 系统镜像 | API 34，google_apis，arm64-v8a，revision 14；客体页 4096 B |
| 基础 AVD | 4 vCPU / 4096 MiB / 1280×720 / 原始数据盘 8 GiB |
| 游戏 AVD | 独立新建 32 GiB 数据盘；不是已有 8 GiB 数据盘扩容成功 |
| 加速、GPU | Hypervisor.Framework；`-gpu host`，日志识别 Apple M4 OpenGL ES Translator |
| 运行方式 | 基础项无头；另有有窗口 smoke；下午登录/下载为有窗口阶段 |

基础启动参数如下；各阶段实际 argv 见 [环境记录](results/environment.json)、扩容各轮 JSON 和[下午窗口启动记录](results/game-final/20261101-180339/window-launch.json)。

```text
emulator -avd official_clean -no-snapshot -gpu host -memory 4096 -cores 4
  -ports 22160,22161 -grpc 22162 -grpc-use-jwt
  -no-audio -no-boot-anim -no-metrics -verbose -no-window
```

[硬件记录](metadata/host-hardware.json)、[官方版本/下载锁](metadata/official-sdk-lock.json)及同目录下载回执保留来源和校验值。未更改官方默认磁盘缓存及内存回收策略。

## 按 issue 清单逐项结果

| 项目 | 怎么测 | 结果与证据 |
|---|---|---|
| 无头启动、sync 后退出 | 10 轮，每轮写 1 MiB，`adb shell sync` → `adb emu kill` → 冷启动复验 | **10/10 大小与 SHA-256 一致**。这 10 次重启 P50 12.366 s，12.240–17.612 s。[逐轮记录](results/sync_shutdown.json) |
| 不 sync 直接退出 | 创建/覆盖各 16 个 64 KiB 合成文件，close 标记后等待 0/100/1000 ms，各 10 轮 | 创建 **480/480 零字节**；覆盖 **1/480 零字节、479/480 新内容完整**。无 missing/old/其他 corrupt。[逐文件记录](results/no_sync_shutdown.json)及所引用 `logs/*.stdout` |
| 客体释放后宿主回收 | 3×1 GiB，逐页非零触碰，持有约 60 s，munmap 后每轮观察完整 600 s | 客体 MemAvailable 恢复 1023.68–1257.91 MiB；模拟器 phys_footprint 末窗变化 +38.00、+5.52、−0.16 MiB，**未见对应 GiB 量级净下降**。[原始 CSV](results/memory/memory.csv)、[逐轮事件](results/memory/rounds.json)、[分析](reports/memory-analysis.md) |
| 长时间空载内存 | 无游戏，7200.010 s / 1441 点，5 s 采样 | 零采样错误；phys_footprint 首末 **+180.59 MiB**，OLS +55.44 MiB/h，排除前 15 min 后 +41.96 MiB/h。[CSV](results/soak/memory.csv)、[分析](reports/idle-soak-analysis.md) |
| ADB 截图 | Launcher/Settings 滚动，raw/PNG 各 100 张，共 400 张 | **400/400 有效**；分组 P50/P95 见下表。[全部样本记录](results/screenshots.json)、四份 `results/screenshots-*.csv`、[400 份原始载荷压缩包](screenshot-payloads.zip) |
| `-share-vid` | 只读 POSIX SHM，动态画面实读 60.0075 s | 帧号推进 3595，**59.909 fps**；60 个 1 Hz 像素哈希不同，无帧号逆跳。[记录](results/shared_video.json)、[轮询 CSV](results/shared-video-frames.csv) |
| 已有数据盘自动扩容 | 停机备份独立克隆，8→32 GiB；首次 +3 复测 | 4 次均未增长客体容量，df 保持 8,302,063,616 B，marker 完整。[首次](results/partition_expansion.json)、[复测](results/expansion_retests.json) |
| 已有数据盘手动扩容 | 官方 qemu-img 同时扩大两层 qcow2，再用客体 resize2fs；首次 +3 复测，各冷启动复查 | 两层镜像/块设备为 32 GiB；4 次 resize2fs 均 rc=1，文件系统仅增至 **10,547,974,144 B**，冷启动后相同、marker 完整，**扩容失败**。[完整记录](results/manual_expansion_retests.json)、[逐轮](results/manual-expansion-retests/) |
| 长连接稳定性 | 空载正式 7200 s，本地 NAT/远端各设 host/guest 对照，共四路 | 四路均完成判据、**零错误/零重连**。[总调度](results/soak/orchestration.json)、[本地](results/soak/network_summary.json)、[远端](results/soak/wan/network_summary.json)及同目录原始 JSONL |
| 游戏下载、进服与脚本 | 明日方舟 B 服；上午安装/冷启动/MAA 取图，下午手工更新/登录 | 用户确认下载完成并进主界面；实际 MAA 日常尚未完成。详见[单独游戏兼容性报告](game-compatibility.md) |

### 持久性问题的复现边界

| 请求等待 | 轮数 | 宿主收到 CLOSED 至调用 kill 的中位 ms | create：zero / new | overwrite：zero / new |
|---|---:|---:|---:|---:|
| 0 ms | 10 | 4.587 | 160 / 0 | 1 / 159 |
| 100 ms | 10 | 108.698 | 160 / 0 | 0 / 160 |
| 1000 ms | 10 | 1008.526 | 160 / 0 | 0 / 160 |

所有写入命令成功并返回 CLOSED 后才调用控制台退出；不是对宿主发送 SIGKILL，也不是 Android 内正常关机。客体 close 早于宿主接收标记，不能把表中数值称为精确 close 延迟。**这些比例只属于合成负载，不是游戏损坏率。** 官方 verbose 中出现 `cache=unsafe`，但本轮未做缓存策略 A/B，未证明具体因果或 ModAVD 数据盘直写在 macOS 上的修复效果。

真实游戏文件早期只有一次 **0 B→0 B** 观察，非空内容样本数为 0；[解释记录](results/game-cache/e2a0a73b8aae-interpretation.json)明确为 blocked，不能计通过或截断损坏。

### 截图数据与共享视频边界

| 场景 | 格式 | 有效 | P50 ms | P95 ms |
|---|---|---:|---:|---:|
| 静态 Launcher | raw | 100/100 | 172.68 | 258.19 |
| 静态 Launcher | PNG | 100/100 | 300.96 | 321.16 |
| 动态 Settings 滚动 | raw | 100/100 | 179.31 | 205.91 |
| 动态 Settings 滚动 | PNG | 100/100 | 79.21 | 82.76 |

计时为宿主单调时钟的完整 ADB 命令往返，图像验证不在计时内。P95 按 `(N−1)×0.95` 线性插值。静态 PNG 约 660 KB、动态约 47 KB，压缩复杂度不同，不能推导 PNG 或动态场景普遍更快。raw 为 16 B 头 + 1280×720 RGBA8888；PNG 经签名、chunk CRC、IEND、解压尺寸验证。原 `pixel_sha256` 对 PNG 实为滤波扫描行哈希；审计解滤还原后，像素去重数仍为静态 1 / 动态 96。

共享视频帧率由帧号推进实测，没有把目标 60 fps 当结果。60 次像素拷贝中 58 次前后帧号稳定、2 次改变；生产者可覆盖同一缓冲，**不保证无撕裂**。官方协议源码留在 `results/share-vid-official-source/`，其固定提交仅作协议参考，不宣称与本二进制完全同版。

### 内存、网络与扩容的结论范围

内存负载高度可压缩；phys_footprint、RSS、客体 MemAvailable 与整机压缩/swap 口径不同，结果不足以证明泄漏、完全不回收或必须移植某种 balloon。空载两小时不是游戏长测，也不能宣布占用已经稳定。

远端 tcpbin.com 经当时宿主路由；解析到 198.18.0.4，可能经过 FakeIP/TUN/代理，不是直连公网结论。正式长测前两次客体短预检各有一次可恢复 ECONNRESET，均保留在 [第一次记录](results/history/idle-second-remote-transient-reset/wan-guest-preflight.jsonl)和[三轮成对预检](results/remote-preflight-retests/)；根因未分离，正式零错误不抹去早期异常。初始二进制消息与按行 echo 服务不匹配由 host 同样复现，[诊断](results/remote-service-framing-diagnostic.json)后改用 line64，不计作 AVD 故障。无错误的正式长测没有恢复事件，恢复耗时应为不适用，不能写 0。

扩容 marker 只覆盖 1 MiB 校验文件，不能代表全部既有数据或文件系统健康。手动路径内核报 ext4 `reserve_backup_gdb` / `ext4_resize_fs` error −22；成功增大镜像头、块设备或冷启动均不等于完成文件系统扩容。本结论只覆盖所测版本和路径。

## 下午游戏阶段补充

客户端由上午 2.7.81/code 200 更新到下午 2.7.91/code 210；18:04–20:04 的有窗口登录/资源阶段采集 1441 个内存点、1440 个网络点。模拟器 phys_footprint **6.593→8.555 GiB**；客体已验证路由接口累计 RX **15,399,125,626 B**、TX **220,524,838 B**，1439 个有效区间、7195.060 s、零采样错误。接口流量包含 Google/后台服务，不等于游戏 UID 流量或资源包大小。辅助远端 TCP host/guest 对照完整 3600 s，均零错误/重连；这段记录不能替代进入游戏后的独立 7200 s headless 测试。

用户在后续聊天明确确认“已下载完成并进入游戏主界面”。本包保留[该确认的最小文字摘录](user-observation.json)，仅证明用户观察来源，不伪装成进服画面或 MAA 自动核验。完成时刻没有精确首/末字节计时，不能用整个接口观察窗计算“游戏资源下载用时”。

尚未完成：实际 MAA `StartUp → Infrast → Award`、逐任务识别/控制/收取画面核验、真实非空近期游戏文件 no-sync 复测、进入游戏后的正式无头两小时四路 TCP+内存观察。下午监测器已到时结束，旧 `progress.json` 是过期快照，不能据它认定整套计划仍在采集或已经完成；本报告也不把上午清理记录当下午最终清理。

## 对后续 macOS 支持的建议

1. **可直接接入官方现有能力**：无头/HVF/host GPU、ADB raw/PNG 与约 60 fps 共享视频已具备。共享内存的 macOS 接入仍需自己的读帧一致性处理，测出帧不等于 MAA 已集成增强截图。
2. **需要补足退出前落盘保障**：所测合成负载已复现未 sync 零字节。应明确正常停机 sync 和异常退出策略，再对数据盘直写等候选方案做 macOS A/B；当前没有证据指定唯一实现。
3. **需要验证数据保留扩容流程**：默认自动路径与所测手动在线 resize 路径均失败，应以客体实际文件系统容量和数据校验判定。
4. **内存回收和游戏适配继续验证**：当前数据支持进一步测试宿主回收策略，尚不足以宣布必须移植 balloon 或网络补丁；完整游戏日常/长测结论待补。

## 复现、证据与校验

基础入口：

```sh
python3 basic_tests.py sync --rounds 10
python3 basic_tests.py nosync --rounds 10 --clone-prefix NEW_DISPOSABLE_PREFIX
python3 capture_tests.py all --count 100 --seconds 60
python3 expansion_retests.py --source STOPPED_BACKUP --prefix NEW_PREFIX --rounds 3
```

这些命令会实际操作 AVD；公开包主要用于**离线审阅**，SDK/AVD/APK 不随包分发。脚本保持测试时结构，`${WORKSPACE}` 是脱敏占位，使用前须配置本地测试根目录。内存/网络参数、探针旧新版本和判据详见 [方法复审](metadata/measurement-review.md)、[内存分析](reports/memory-analysis.md)、[空载分析](reports/idle-soak-analysis.md)。

[历史独立审计](reports/evidence-audit.md)按其快照范围读取：其中上午“尚未登录”的阶段描述已由本报告和游戏补充更新；它不覆盖下午进服确认。旧总草稿、旧 test-status/progress 和 live 临时分析未作为当前报告发布。公开副本只替换本机路径/临时目录；[源文件与公开文件双哈希](source-and-public-manifest.json)记录每份副本是否改变，历史审计中的源哈希可对照 `source_sha256`。400 份截图载荷和 SHM 首帧在 ZIP 内保持原字节；其逐文件摘要也在该 manifest。

公开包不含账号、令牌、ADB/JWT 密钥、AVD 数据盘、APK、游戏私有文件名/内容、登录后截图、完整 logcat/MAA 日志或宿主 netstat。图表在 [reports/figures](reports/figures/)，必要命令在 [筛选命令记录](logs/commands-selected.jsonl)。截图 ZIP 按完整文件 SHA-256 去重，401 个原路径（400 次 ADB 截图 + SHM 首帧）映射到 201 份唯一载荷；`archive_member` 可取出原字节，重复截图保留逐次记录。运行 `python3 verify_evidence.py` 可校验公开副本和全部截图映射。发布文件另可用 `shasum -a 256 -c SHA256SUMS` 离线验证；该清单不包含自身。
