# ModAVD macOS 构建与运行检查

## 目标
将 DeathofBrain/ModAVD 完整移植到 Apple Silicon macOS，并依据源码、产物和本机验证逐项核对原版功能及优化。

## 已授权的完整移植（2026-10-31）
- Apple Silicon 原生完整移植；新建 ARM64 Android 实例，本机使用，本地签名。
- 保留模拟器改进、管理器、CLI/MCP、宿主输入法、高速共享内存截图及更新恢复。
- 游戏回归仅B服《明日方舟》，未来使用Maa Verge + AVD增强截图；用户随后要求检查MAA当前截图方式，已完成两轮测试，尚未确认消费增强。未修改MAA，不移植AUTO-MAS本体；登录后实战按用户决定暂缓。
- 使用现有空间分阶段推进，保留 20 GiB，低于停线暂停下载/构建并保留断点。
- [complete] 固定 Darwin manifest、同步依赖、验证上游与全部原补丁的原生构建。
- [in_progress] 管理器/CLI 的 Darwin 宿主层与完整功能。
- [in_progress] Darwin 网络/稀疏盘/输入法等追加补丁。
- [in_progress] 独立运行环境与 ModAVD 自身的 SHM 验证。
- [in_progress] 原生开机与完整功能验收；B 服已安装并启动，用户选择暂不登录，游戏实战回归暂缓。

## 初次兼容性检查阶段（历史）
1. [complete] 检查工作区与本机工具，克隆仓库并记录版本。
2. [complete] 阅读构建说明、依赖与平台实现，检查 macOS 支持范围。
3. [complete] 执行适合 macOS 的构建/启动检查，记录输出和限制。
4. [complete] 生成可复查报告，说明结论、复现命令和后续选择。

## 决策
- 仓库直接克隆到用户工作区 /Users/azuki/ModAVD。
- 优先验证原仓库；只有必要且范围明确时才做本地兼容修改。
- 不将静态检查、局部构建误称为完整运行通过。

## 错误与处理
- 初始 rg 文件发现返回 1：工作区为空，按计划 clone。
- 工具探测 command -v 返回 1：部分候选工具不存在；后续只核查实际依赖。
- 上游快照 README 首次读取误用文件名，改为实际的 .local-check/upstream/README.md；不影响源码和检查。
- 初次 xcodebuild 因全局选择 CommandLineTools 退出 1；后续已发现完整 Xcode 27.1，以进程级 DEVELOPER_DIR 使用，SDK 27.0，无需修改全局选择。

## 当前实施检查点
- [complete] 原89补丁回放并核对目标树，追加9个Darwin QEMU功能补丁、native EGL绑定检测和配套AEMU声明overlay。
- [complete] 管理器原生编译、Go测试/vet、Windows交叉编译、签名和HTTP smoke。
- [complete] runtime工具和POSIX SHM本地测试；不等于Android实机通过。
- [complete] 62 个固定 SHA 浅取与 Darwin 稀疏依赖检出、checkout 收据、12 个 QEMU Darwin overlay 完整树验证；当前QEMU树 `3058e5f90f327708e08e4df6eff63b28362479b3`，gfxstream 2overlay树 `d14277bc86e27f74baa1a1716b1ee29f71587895`，AEMU overlay树 `04a678d0a729c6f9870e5f2c8149256294489298`。
- [complete] 核心 configure、编译、install/strip、最终 ad-hoc 签名；76/76 Mach-O 严格签名、实际两引擎装载与 HVF 检查通过；89 项选定核心测试通过、2 项上游主动跳过。
- [complete] 本地完整目录装配、APFS clone 与版本/校验清单；默认无 ZIP。管理器后续修复另装新目录，保留原验收产物。
- [complete] 用户明确接受两份官方 SDK / ARM 镜像条款，并授权安装、开机；明日方舟渠道确定为 B 服。
- [complete] 官方 adb 37.0.1 / Android 14 ARM64 r14 安装与全部文件 SHA-256 复核；headless / Qt 客体开机。
- [complete] 真实窗口显隐、静态/动态 SHM、中文预编辑/提交/删除/退格客体往返、网络哈希、HVF 请求 16 核裁剪为 8 核。
- [complete] 管理器新建/首次初始化/正常关机/带窗口开机，真实 MCP stdio 初始化与 list/info/check。
- [complete] B 服 APK ZIP/ABI/manifest 核对、安装及首次启动到游戏协议页；代理未处理协议或登录，用户暂缓登录后实战，不推断用户后续动作。
- [complete] 隔离QA的16→32 GiB保数据扩容、前后qcow2检查与4 MiB随机sentinel SHA-256保持。
- [in_progress] 最新 GUI 的其余实际按钮与提示、多实例隔离验收；不绕过本机内存保护。
- [pending] macOS 真实键盘组合输入与完整 Cocoa IME（Accessibility预检false，未注入）；HVF 安全物理内存回收；用户暂缓的游戏登录后资源/实战；远程发行服务。

## 2026-11-01 原版对齐审计

- [complete] 原89补丁最终状态/平台条件、Manager20个CLI/11个MCP/26个HTTP注册、组件/随包工具的独立核查；结果见macOS-compatibility-report.md及三个PARITY-AUDIT附录。
- [complete] 运行包verify489项、最新Manager严格签名复查；确认运行包acc3c046与最新dist bdf40ea4不同。
- [complete] 新客体只读证据确认reporting协商bit5/order9、紧凑inode、write-through与dirty200/100；仅证明配置生效，不证明物理回收或吞吐收益。本轮runtime定向合成测试65项通过，审计文档和入口集合完整性检查通过。
- [complete] 固定Google/AOSP基线继承的HVF Q30→Q20已由D0006修复；真实HVF VM的32SIMD寄存器put/get、guest STR/LDR和原缺陷负对照通过，两引擎已重建签名并装入新包。
- [complete] Darwin日志清理已用libproc/vnode、PID启动身份复验和跨进程目录锁修复；同UID非子/孤立writer、计数/容量与race通过。
- [complete] ARM Vulkan原缺陷真实GPU复现，v2修复12组读回/pNext/四线程8192bind通过；Manager2182已自动生成/推送/挂载/zygote重启，重复start及客体reboot/cache复用通过。
- [complete] ARM idle-poll完成六轮8KiB+fsync本机A/B和动态busy/idle/off测试，已接入正式Manager并真实起watcher。
- [complete] 组件补全/问题包/脱敏报告四入口随包，真实ZIP/状态/报告与34项隔离测试通过；真实Qt/.app/MachO更新ZIP安装及中断recover、签名/清单、SDK用户hash保持通过。
- [in_progress] 安全HVF物理回收：私有v4/v5已实际暂停/提交事务，真实Android释放/读零/重用、三类失败回滚、快照后禁回收通过；checked-sized DMA/ASG/Media同步已有独立专项。v5新增目标mincore统计，但旧VMobject仍可能驻留，物理收益未证，未并入生产或默认开启。
- [complete] Manager1955补齐mounted SHA和restart失败安全回滚，负例/构建/签名通过；新组合417clone/507payload、自动Vulkan、旧sentinel/32GiB/µLauncher保持及受控kill恢复通过。真实mount的restart失败注入未执行（预先umount失败），不冒称通过。
- [complete] 隔离Android/slirp网络8项：160活跃TCP、32MiB单路及8x4MiB、半关闭、拒绝、重置、160UDP、故障后新TCP，逐字节校验、boot ID保持和临时目录清理通过；不含dead-peer/ICMP/外部黑洞/CDN或上游吞吐对照。
- [complete] 已定位误删version_num.cache导致上游重建qcow2，源QA完整；完整stopped AVD复制保留标记后旧sentinel/32GiB/µLauncher实机保持，错误副本单独保留，不覆盖源。

- [complete] D0007独立修复Google基线三个VideoToolbox数组owner声明；D0008补视频dispatcher清理、Context先销毁decoder后释放alias及HEVC snapshot计数。完整树回放、main双引擎/backend增量构建、install/strip和deep/strict签名通过；actual dispatcher/Context ASan与原代码负对照通过。
- [complete] v5网络与128MiB内存探针从同一exec并发启动，真实重叠4.274秒，两者通过；范围与时间戳见network-memory-overlap-v5-20261101.json。
- [complete] 全QEMU SIMD worker经10次pause/resume和3次snapshot save/load/delete，2,135,867,594轮无错；只覆盖单CPU忙worker，不泛称全CPU/宿主睡眠。
- [complete] D8独立新包ModAVD-gapfix-media装配418clone并verify，Qt 32.87秒开机，旧sentinel/32GiB/home/driver保持，正常sync关机3.74秒。原游戏包及1955包不覆盖。
- [in_progress] SHM四方向/多reader窄QA：旧文件与设置恢复通过；exact ADB像素比较及旋转契约未闭环，首轮false证据完整保留，不把静态画面帧号不变判作生产故障。
- [in_progress] v6连续VA/独立2MiB对象机制已有native物理正证、分配收据、两代与故障验证；实际Android前两轮仅回收未驻留对象、物理收益仍零，正在定位报告阶数/资格门禁，不默认启用。

后续SHM契约收证：`.macos-work/qa/manager-shm-media-contract-20261101/report.json`确认四方向固定natural1280×720、每方向3reader×12只读mapping、设置精确恢复；`ok`只表示收证与metadata，`pixelParityEstablished=false`明确保留，未赋色差容差。首轮错误旋转/静态heartbeat假设及false证据不覆盖；源契约说明见`.macos-work/qa/manager-shm-source-review-20261101/assessment.md`。D8构建、包内二进制和各专项报告hash汇总见`.macos-work/qa/d8-integration-index-20261101.json`。

D9生产补齐已完成构建和签名：新增`0009-shared-memory-recording-lifecycle.patch`，QEMU为3构建+9功能overlay，完整树`3058e5f90f327708e08e4df6eff63b28362479b3`。start/stop/析构幂等、同步callback注销及无frame队列回收的host QA为12普通+12ASan/UBSan通过、6个原代码负控全部失败；独立review38项及既有CallbackRegistry6项通过。24B SHM ABI不变，stop保留旧映射；`shm_ready`不证明stream active。backend与D8精确相同，既有硬解/GPU专项可按identity复用。新包`ModAVD-gapfix-shm`418clone、508payload verify通过，实机证据另列。首次start在后台编译期间被正常内存预检挡下（可用4.3GB<4.5GB）；未绕过门禁，原失败记录保留。

私有RAM v7已通过固定256KiB对象、精确allocation granule/serial收据与每个SG/range调用最多2次native尝试/32对象检查（非整个queue element或暂停BH的总预算）的native及成本gate。2.5GiB untouched对象构造从2MiB粒度1280objects/7.441ms变为256KiB粒度10240objects/40.631ms，释放1.190ms，region分配前55→构造后10297→释放后55（其中target10240）；单次值不作SLA，footprint不变不代表kernel metadata无成本。v7实际allocator两代native正控4608、monolithic负控2304及故障检查通过，systemwide counter非exact窗口原样保留。见`.macos-work/qa/exact-v7-native-20261101/assessment.json`和`dcr-construction-cost-20261101/assessment.json`。真实Android驻留页释放、安全与第二代验证仍在进行，生产默认未变。

D9封存独立包`ModAVD-gapfix-shm`现已完成真实双引擎验证：Qt开机34.8589秒、headless12.2905秒，各0warnings；各80次重复start、80次重复stop、40轮stop/start、停止期间真实Settings显示变化而SHM header/pixels不变、restart观察新帧及恢复HOME/活跃帧、同一boot ID全部通过。两次均graceful/synced=true关机（Qt3.9737秒、headless见stop收据），旧sentinel/32GiB/µLauncher/driver保持。证据为`.macos-work/qa/manager-shm-d9-{qt,headless}-20261101/report.json`、`gapfix-shm-retention-20261101.json`和`gapfix-shm-{qt,headless}-stop-20261101.json`。这些是专用QA实例公共console与SHM短观察，内部refcount由host专项证明；每次read重新open不能证明旧reader跨stop持有同一object。不声称ADB像素相等、帧无撕裂或MAA已消费增强。原游戏包和MAA未修改，私有RAM仍不在此包中。

补充预算边界：v7的2次native尝试/32对象计数在每个`qemu_ram_reclaim_report()`调用重置，virtio-balloon逐SG调用；一次BH暂停最多drain32个元素，每元素可有多个SG，故这些数值不构成整次暂停上限。既有`report_work_us`也不含全vCPU暂停等待、元素解析及全部候选审计扫描。正在补完整分段遥测，不把同步函数耗时误报为整次pause成本。

- [complete] 私有 v7 telemetry-only 正向客体已正常停止并冻结：266 次提交中 4 次释放真实驻留旧对象，累计 1 MiB、768 KiB 去重地址范围，含同地址两代 backing；网络/内存探针并发及单忙 worker SIMD/快照窄范围正确性通过。完整暂停插桩已补齐，最大 request→resume 返回观察值 14.068 ms，不是上限。
- [complete] 同 binary map-fault 271次回滚、9个resident窗口无释放；cold-tail只读1834个256KiB对象全部未驻留。整BH共享预算和kernel metadata净成本尚未闭合。联合free/speculative的冷对象非exact窗口10/262保留，生产默认未变。详见 [v7 增量记录](ports/macos/qa/hvf-object-reclaim/v7-android-progress.md)。
- [complete] G0006 原版 noGL 读回丢弃失败结果/未初始化 cache 已以独立G2 overlay补齐：保留void API、新增checked bool、每display复用scratch，成功才发布。生产构建/签名、8组CPU正负控及8模式192帧native v4验证通过，新包双引擎与SHM收尾结果见后续记录。
- [complete] 冻结f022私有RAM binary的单组order9/order6对照已完成，两个guest正常停止并冻结；B6扩大精确resident覆盖但累计pause增加，4个resident及1个cold全局计数非exact，净metadata/整体收益未证。生产Manager保持9，见[结果](incoming/macos-qemu/experimental/REPORTING-ORDER-RESULTS.md)。

G2新检查点：`0002-no-gl-readback-publish-only-success.patch`已完成生产构建/签名，gfxstream完整树`d14277bc86e27f74baa1a1716b1ee29f71587895`（2 overlays），backend SHA`35fbd4366185ea5985e41e495c73c910030e7522075ef23de7c6317ea6f60920`；CPU正负控8组及native v4共8模式192帧通过。新包`ModAVD-gapfix-readback`418clone/508payload verify，Qt31.794854458s/headless13.251739375s开机，各7项数据/配置/实际backend保留和7项SHM生命周期及HOME/stream恢复通过，graceful/synced停机分别3.64848575s/3.383434625s，后续确认两PID消失。失败历史、RGBX两模式契约、额外scratch/copy成本和覆盖边界见[G2验证说明](ports/macos/qa/framebuffer-readback/G2-VALIDATION.md)。G2 backend与D9已不同，旧视频结果不能写成新G2视频重跑；生产RAM和原游戏包/MAA状态不变。

G2跨文档快照已封存：15份文档、169项检查通过，QA导出另9项通过，见[新快照](.macos-work/qa/g2-cross-doc-final-review-20261101/assessment.json)。后续order9/6单组结果是独立增量，不改写该历史snapshot或D9证据。
