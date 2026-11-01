# 私有 v7：Android 驻留对象释放与成本边界

2026-11-01。此记录补充 D9/G2 文档快照，不改变生产默认，也不覆盖先前失败、零收益或非 exact 的记录。

## 已验证的释放

固定 256 KiB 独立 VM object 的 telemetry-only 客体（PID 2276，已正常停机）记录了 266 次提交：262 次旧对象 PRESENT=0，4 次旧对象 PRESENT=16 个 16 KiB 宿主页。后四次均为旧对象引用数 min=max=1、paged-out/reusable/query-error=0、cleanup=0；目标 HVA resident 从 262144 降至 0，最终引用清理窗口的全局 free 精确增加 16 页，speculative 不变。

4 次驻留释放累计为 1 MiB，覆盖 3 个不同的 256 KiB 地址范围，即 **768 KiB 去重范围**。同 offset 280756224 的两次旧 object ID 不同，证明该地址在两代 backing 上都完成释放。累计提交量和去重范围均不是当前净省内存量。

262 个冷对象窗口中，7 个窗口 free delta 非零，另有 3 个 free=0 但 speculative delta 非零；free/speculative 联合非 exact 为 10/262，252 个窗口两者均为零。全部 guest 窗口的严格物理判定仍为 false，不能用 4 个精确窗口替代整段计数。

最终正向日志 SHA-256：`67ba470466168cfdb9534726d4200f2b12bb35aaeab8b1114d0cb12c55a4c3fc`。原始证据、二进制身份和脚本见 [最终 assessment](../../../../incoming/macos-qemu/experimental/evidence/20261101-v7-pause-telemetry-guest/assessment.json)、[计时](../../../../incoming/macos-qemu/experimental/evidence/20261101-v7-pause-telemetry-guest/timing-assessment.json)及[噪声口径补充](../../../../incoming/macos-qemu/experimental/evidence/20261101-v7-pause-telemetry-guest/physical-noise-assessment.json)。

## 暂停与工作预算

当前 2 次 native 尝试 / 32 次对象检查的预算，在每个 `qemu_ram_reclaim_report()`（每个 SG/range）调用重置；不是整次 BH 暂停的总预算。一次 BH 可 drain 最多 32 个元素，每元素可有多个 SG。实际 19 个 BH 中，最多 64 次 native 尝试，最多 498 次对象检查；对象检查 32 次的每 SG 上限尚未触发。

最大 pause request 到 resume 返回为 14.068 ms，同次静止处理为 13.925 ms，插桩 BH 区间为 15.144 ms。插桩起点在 snapshot/GArray 构造之后，终点在最终计时日志之前；这些是本次观察值，不是最坏耗时上限，也不是每个 vCPU 精确停止运行的时间。现有 `report_work_us` 不包括暂停等待、全部 SG 解析、重叠校验和调用者的其它工作。

## 正确性与生产决定

同次客体通过有限的 live/释放后读零/重用检查、网络并发和 SIMD 检查。网络与后续内存探针的 host 窗口重叠 8.8741 秒，但 4 次驻留回收均出现在网络测试之前，未证明逐事务的驻留回收与网络同时发生。SIMD 单忙 worker 经 10 次暂停恢复、3 次快照保存/载入/删除，2,007,078,627 次检查零错；不外推全部 vCPU、宿主睡眠或游戏运行。

随后同二进制的独立 map-fault 客体 PID12525 已正常停机：271 次事务全部 rollback、0 commit、cleanup/query/mincore error 均为零；9 次驻留回滚均 free=0/speculative=0、目标 HVA resident 保持，重复 offset 保留同一旧 object ID。256/512 MiB 各 3 轮 live 检查无错、boot 身份不变。其冷窗口仍有 1 个 speculative-only 噪声，全 guest 严格物理判定仍 false。最大 request→resume 返回为 9.556 ms；这不是成功释放或最坏时延证据。见 [map-fault assessment](../../../../incoming/macos-qemu/experimental/evidence/20261101-v7-pause-telemetry-fault-map/assessment.json)。

两次运行的 raw log、物理窗口、计时及已封存二进制身份链经[独立复核](../../../../.macos-work/qa/v7-timing-positive-independent-review-20261101/assessment.json) 67 项检查通过。正向 stop 结果是既有会话工具结果的转录，原冻结仅验证 PID 已退出；map-fault 保存了独立 subprocess stop 回执。

## 尾部候选与报告阶数

后续只读诊断使用单独二进制，正常停机后冻结。131 个 4 MiB SG 的首 2 个对象仍按原策略处理，尾部 14 个对象只查询、不回收。尾部共 1834 个不同的 256 KiB 对象（458.5 MiB），全部满足未曝光及完整 metadata，PRESENT/PAGEDOUT/REUSABLE 均为零。共 29344 次真实宿主页查询；128/256 KiB 分组复用同次查询，不能双计。就这些覆盖尾部而言，64/16 KiB 子组的驻留候选上界也为零；不外推被 exposure 门禁拒绝的其它 SG。

因此此次没有发现冷首部挤掉驻留尾部的实际饥饿证据，458.5 MiB 不是释放量。128 MiB 一轮 live/读零/重用通过，stop 回执保留；[尾部独立复核](../../../../.macos-work/qa/v7-cold-tail-independent-review-20261101/assessment.json) 159 项检查通过。诊断查询成本合计 28.028 ms、每 SG 最大 252 µs，仅为诊断函数测量，不是完整暂停或优化收益。

另已发现环境差异：probe 实测 guest 页为 4 KiB，旧 v6 裸启动客体 sysfs 实读 order10，v7 四次运行的 SG 均为 4 MiB，且未执行 Manager 调优；没有逐次实读 v7 的 sysfs，不能仅从大 SG 反推最低扫描阶数必为 10。Manager 生产启动会写入并读回 order9（2 MiB）。固定 kernel 的 `page_reporting_order` 是最低 buddy 扫描/触发阶数，降低它会纳入更小空闲块，已有高阶块仍可作为较大 SG 报告。现有 buddy 快照只说明较低阶块存在，不能证明其 host 驻留或安全回收资格。当时尚未做显式order9/6对照；后续单组结果见本页末节，仍未证明整体收益，也未改生产配置或host 256KiB粒度；见[阶数审计](../../../../incoming/macos-qemu/experimental/REPORTING-ORDER-ASSESSMENT.md)及其[源码独立复核](../../../../.macos-work/qa/reporting-order-source-independent-review-20261101/assessment.json)。

2.5 GiB untouched 分配的单次成本测量中，2 MiB 粒度为 1280 个对象、构造 7.441 ms；256 KiB 粒度为 10240 个对象、构造 40.631 ms、释放 1.190 ms。VM region 计数在 2 MiB 粒度时为分配前 55→构造后 1337→释放后 55，256 KiB 粒度为 55→10297→55（其中 target 为 10240）；释放后恢复不表示构造期间 region 没有增长。没有测量内核 object metadata 净成本；region 或进程 footprint 不变不能证明该成本为零。

因此保留私有显式开启：**不进入生产默认，不宣称整体净 RAM 收益已对齐。** 后续需要依据候选诊断决定公平选择和共享 BH 工作预算，并测量有代表性的整体收益及 metadata 成本；不得为了增加提交数放宽 guest 所有权、DMA/ASG/Media、allocation receipt、暂停或快照安全门禁。


## 后续单组 order 9 / 6 对照

在同一冻结 f022 私有D9四件套、`TAIL_DIAG=0`、256KiB backing及全部安全门禁下完成一组fresh boot A9→B6。两次均先实读10，再显式root写/读回9或6，各执行10s idle、256/512MiB各3轮8s探针及末尾idle，四个probe均无数据错误；两guest正常sync/graceful停机，最终PID均消失、私有console20160无listener。它们没有使用G2新backend。完整原始窗口、身份/哈希、非exact行、失败历史和退出证据见[结果JSON](../../../../incoming/macos-qemu/experimental/REPORTING-ORDER-RESULTS.json)与[结果说明](../../../../incoming/macos-qemu/experimental/REPORTING-ORDER-RESULTS.md)。

| 严格比较窗口观测 | A：order9 | B：order6 |
| --- | ---: | ---: |
| 窗口秒数 | 86.593 | 87.304 |
| BH / SG | 4 / 42 | 83 / 2298 |
| resident / cold commit | 6 / 2 | 173 / 8 |
| 全resident地址去重范围MiB | 1 | 28.5 |
| 精确完整resident窗 | 6 / 6 | 169 / 173 |
| 精确子集：地址去重 / 累计目标 / 旧PRESENT MiB | 1 / 1.5 / 1.5 | 27.75 / 42.25 / 42.25 |
| request→resume返回：合计 / 最大ms | 14.068 / 5.889 | 136.264 / 3.723 |
| 插桩BH区间：合计 / 最大ms | 14.910 / 6.109 | 139.924 / 4.105 |

B6确实纳入256KiB/512KiB/1MiB块，也保留2MiB/4MiB块；降低最低报告阶数没有拆分已有高阶SG。169个精确完整resident窗口均旧ref1、PRESENT16/16宿主页、mincore256KiB→0、free+16/spec0且query/cleanup无错误。这里的累计目标、旧驻留总量及地址去重都是有限对象窗口统计，包含同址多代，**不是当前净省内存**。

另4个B6 resident窗口不能计为精确释放：三个partial PRESENT/pagedout为8/8、11/5、14/2，free分别+16/+16/+17；一个完整PRESENT16却free+19。还有一个cold PRESENT0/pagedout16窗口free+16。五者spec均0，目标mincore后均0，但全局计数不匹配原因未解决。不能用169个精确子集把整轮改写成strict global physical PASS。

两轮各排除5个写阶数前BH，没有跨比较边界或窗口结束后BH。metadata为顺序读取，不是原子快照；guest布局及报告时序可不同。B6覆盖扩大伴随BH数量和累计暂停上升；较小的单次最大值不是普适性能优势。现有插桩仍不覆盖完整BH body、完整停顿或每vCPU精确off-run时间；每SG预算仍未变成BH共享预算。此单组结果不构成普适性能、净内存收益或kernel metadata成本证明，生产order保持9，实验保持私有opt-in。

首次A9的printf参数写入失败发生在测量前并正常停机。成功A9即时freezer曾在正常stop后仍看到短暂退出中的QEMU而拒绝，随后PID消失；原始log、append-only校验及离线重试均保留，未重跑guest负载。A9/B6间runner唯一变化是正常stop后有界等待所选QEMU消失，前后源码/diff/哈希保留。B6正常停机及冻结。最终35项执行/身份检查通过；不覆盖或删除先前的失败收据。
