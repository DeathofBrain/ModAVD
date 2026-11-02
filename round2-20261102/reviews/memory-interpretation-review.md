# 第二轮随机内存结果的独立解释复核

复核日期：2026-11-02；正式采集在上海时间 16:11:25 完成。本复核仅读取已经保存的 423 行 CSV、协议／执行 JSON、部署与源码快照、启动日志、事后 vmmap 诊断及公开 issue 回复。没有运行设备、分配负载、修改已有结果或开始新实验。

**目前结果与维护者怀疑的“客体释放页后，官方模拟器没有相应降低宿主账面内存成本”一致，值得作为待解决问题继续推进。它没有证明与 Windows 是同一实现机制，也没有证明新增 1 GiB 泄漏。** 核心限制是：正式负载开始前 footprint 已约 7 GiB，而三轮随机填充没有形成对应的新增 1 GiB footprint，不能用随后没有下降 1 GiB 推算“未回收比例”。

## 结果与窗口已独立重算

从 [memory.csv](../random-memory/memory.csv) 和 [progress.json](../random-memory/progress.json) 的单调时钟边界，独立选取基线末 12 点、完整落在持有区间内的末 3 点、释放后观察末 12 点，所有中位数与 [summary.json](../random-memory/summary.json) 一致。各轮均有 12／3／12 点，实际观察 600.023／600.034／600.037 秒。423 点没有 host／guest 采样错误，间隔为 4.966–5.035 秒；单次采样最长 421.251 ms。

下表单位均为 MiB。footprint 与 RSS 的“增量”均为持有末窗减基线末窗；“释放变化”均为释放末窗减持有末窗。

| 轮次 | guest MemAvailable：基线→持有→释放 | footprint：基线→持有→释放 | footprint 分配增量 | footprint 释放变化 | RSS 分配增量／释放变化 |
|---|---:|---:|---:|---:|---:|
| 1 | 2512.85→1474.47→2467.72 | 7108.73→7130.07→7147.04 | +21.34 | +16.97 | +84.27／−114.57 |
| 2 | 2467.72→1459.00→2494.35 | 7147.04→7150.85→7148.71 | +3.81 | −2.14 | +289.05／−293.09 |
| 3 | 2494.35→1480.93→2529.14 | 7148.71→7150.29→7150.16 | +1.58 | −0.13 | +60.66／+20.14 |

三轮完整填充和读验均为 1,073,741,824 B、使用不同的 getrandom 种子，执行退出码均为 0，munmap 明确成功。填充耗时为 4.698／2.679／2.385 秒，独立再生成并读验耗时为 1.223／1.250／1.224 秒；持有为 60.081／60.061／60.072 秒。客体 MemAvailable 分别回升 993.25／1035.35／1048.21 MiB。MemAvailable 是内核估计值，会受缓存及其他活动影响，后两轮相对回升超过 100% 不表示凭空回收了额外内存。

16 MiB 设备 smoke 的完整数据已由独立 Python ChaCha20 逐字节核验，4096 个页面摘要全部唯一，zlib 比率 1.0003055。正式每轮也完整再生成读验。因此，第一轮“每页少量非零字节，实际上高度可压缩”的主要负载缺陷已消除。但 smoke 证明的是所构造随机流，不是“宿主全部内存都不可压缩”。

## 为什么 footprint 增幅很小，基线却已很高

**采集字段没有错位。** 留存的 [soak_tests.py](../random-memory/provenance/01-soak_tests.py) 调用 `proc_pid_rusage(pid, 2, ...)`，越过 16 B UUID 后取 64 位字段 6／7；本机 SDK sys/resource.h（本地来源未公开） 的 `rusage_info_v2` 确认它们分别是 `ri_resident_size`／`ri_phys_footprint`，不是 lifetime peak。事后 vmmap 也独立报告 current footprint 7.0G、peak 7.1G，与 CSV 一致。

**footprint 不是当前驻留 RAM。** Apple [XNU 的 task ledger 说明](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/kern/task.c) 明确把 internal、internal_compressed、IOKit mappings、非易失 purgeable memory 与页表等记账项纳入 footprint。因此 RSS 很低并不能推出“这台 VM 的总内存成本已经降到几百 MiB”；压缩或换出也不必让 footprint 按同样幅度下降。这里引用的是公开 XNU 对指标的语义说明，不是声称已经逐项核对当前内核的所有账本。

**实际驻留发生过明显变化。** 三轮 allocate／hold 全阶段的 RSS 采样峰值为 1698.88／1657.80／1787.67 MiB，高于持有末窗的 360.78／535.27／302.83 MiB。即使随机页难以压缩，宿主也可以把它们换出，或把其他旧页挤出。随机数据只排除了“这段数据本身很容易压缩”，没有消除交换与复用已有 guest RAM 背衬的影响。三轮客体 swap 使用量的基线／持有变化仅为 +0.25、+0.25、−36.25 MiB，没有证据表明那 1 GiB 随机工作集主要被客体 zram 吃掉。

**宿主确有大量压缩／交换活动。** 本轮全机 swap used 在 8849.19–10835.00 MiB 间变化，首末增加 950.32 MiB；全机 compressor physical 范围为 4951.95–7719.83 MiB。按已记录的 16 KiB 宿主页大小，swapins／swapouts 增量分别约 4.77／5.71 GiB。这些是全机计数，不能全归因于 QEMU，也不能与进程 footprint 简单相加，但足以说明本轮没有隔离一个几乎不交换的宿主环境。

**高基线的构成仍未查清。** 正式基线在 15:36:11 开始，模拟器于 15:24:42 启动，中间已过约 11 分 29 秒；缺少从进程创建到 Android 启动阶段的连续分项内存轨迹。一个合理解释是，guest 启动／预热已使一批客体 RAM 对应宿主页进入记账，后续 1 GiB 分配主要重用这些已计费页，而非新增等量宿主账面负担。GPU／共享映射／HVF 的记账也可能贡献高基线；这些只是候选解释，不能从单个总数中选定一个。

事后 vmmap-summary.stdout（本地来源未公开）（16:14:53，主观察窗之外）显示 shared memory 虚拟大小 4.0G、resident 9.9M、SWAPPED 2.8G；总 resident 339.6M、dirty 22.1M、SWAPPED 3.0G。这个 4.0G 区域与 guest RAM 大小相符，但仅有 `-summary`，未得到具体映射名称与详细账本，不能据此断言每一字节的归属。工具的 SWAPPED 分类也不能直接等同于全机 swap 文件新增量。其 footprint 7.0G 与可见 region 汇总不能简单逐项相加对齐，进一步说明需要详细 footprint／task ledger 分解，而非把差额全算成 GPU 或泄漏。

原始 CSV 还有约每 60 秒一次、约 97 MiB 的 footprint 短峰，基线与释放观察期都存在；来源未识别。末窗中位数没有被单点短峰主导，但这个背景活动也说明“几十 MiB 的差”不宜解释为纯分配／回收效应。全程 footprint 首末仅 +38.64 MiB，不能描述成每轮又永久叠加了 1 GiB。

## 与 issue #28 的关系

已只读核对[维护者 2026-11-02 01:21（上海时间）的回复](https://github.com/ModAVD/ModAVD/issues/28#issuecomment-6561297507)。其要求是随机数据重测 3×1 GiB，并关注 guest 还页后 `phys_footprint` 是否下降；其“官方模拟器应该没开 free page reporting”本身用的是推测措辞，0035 的宿主释放实现又是针对 Windows。

本轮存在一条额外实现线索：完整 QEMU 启动参数（本地来源未公开） 明确包含 `-nodefaults`，完整 `-device` 列表没有 virtio-balloon 或 free-page-reporting 设备。这支持维护者的怀疑，比单看 footprint 不降更接近机制层证据；但尚未记录运行时 virtio 设备／feature 协商，也未审阅本版 Darwin 回收后端，不能把它升级为“已证实不存在任何还页路径”。

可以支持的表述是：

> 三轮已通过完整随机写验，guest 释放并恢复约 1 GiB 可用量，但官方模拟器约 7 GiB 的 footprint 没有相应下降；本轮启动参数亦未见 balloon 设备。这与维护者担心的宿主还页缺口一致，值得继续核实 free-page-reporting／mac 后端。由于分配前 footprint 已高，填充仅产生 1.58–21.34 MiB 的末窗增量，且宿主持续交换，本轮无法可靠估计新增负载的宿主回收比例，也不足以证明泄漏或与 Windows 相同的根因。

不能支持的说法包括“已经证明 3 GiB 泄漏”“每轮明确泄漏 1 GiB”“随机数据仍被高比例压缩所以测试无效”“RSS 下降就是同量 footprint 返还”“7 GiB 都是 guest RAM 或都是真实驻留 RAM”“0035 原样移植 mac 就必定解决”。脚本把三轮 footprint 的 `reclaimed_fraction` 全部保留为 `null` 是正确的。

## 最小后续验证路径

先做范围小的实现与运行状态确认，不必立即重复整套三轮长测：

1. 用同版官方环境核对实际 virtio 设备、guest `virtio_balloon`／page reporting 支持和协商 feature；同时只读审阅对应 QEMU／0035 的页报告接收与 Darwin 最终释放路径。明确区分“设备没建立”“feature 没协商”“有报告但 mac 未释放”三个位置。
2. 下一次仅做一个受控 1 GiB 短周期，从 QEMU 进程创建前就记录宿主状态；在 Android 启动后、填充后、释放后各取详细 `footprint`／`vmmap` 和 guest meminfo。目标首先是解释那约 7 GiB 基线及已记账页复用，而非再用一个缺少起始分解的总量曲线猜机制。维持相同实例参数，记录宿主活动，尽可能降低与测试无关的交换；不需要人为制造更大的内存压力。
3. 若本版官方工具允许开启兼容的 balloon／reporting 设备，再以同版官方模拟器做一次开／关对照并记录 feature 协商与账本变化；这是待确认能力，尚未执行。若官方没有可用后端，届时才讨论有针对性的 mac 实现或构建。

这些建议没有在本轮执行；现阶段保留原始结果，继续本轮游戏与真实文件验证。主报告可交叉参照 issue-28-round2.md（本地来源未公开）。
