# ModAVD macOS 与原版对齐审计

清理状态（2026-11-01）：代码已同步至 `codex/macos-port-checkpoint`；随后按用户要求删除全部本地 ModAVD 生成资源、实例及虚拟盘。本报告中的包路径和原始本地日志链接现为历史记录，不代表本机仍有这些文件。保留[验收摘要](ports/macos/qa/validation-summary-20261101.json)及[清理回执](ports/macos/qa/local-resource-cleanup-20261101.json)，官方 AVD 基线尚需单独准备。

审计及补齐日期：2026-11-01（Asia/Shanghai）。**尚未达到全功能、全优化对齐。** 已修复 HVF SIMD 映射、Darwin 活动日志保护、视频所有权/生命周期和SHM启停；补齐维护工具、真实本地更新事务与ARM Vulkan自动修复。后续G2又补齐截图cache初始化及失败不发布，独立新包双引擎已通过。私有v7已有Android驻留页释放证据，生产内存净收益及其余验收仍未闭合。

本报告对照原版的最终行为，不按“补丁应用成功数”计算完成率。代码保留、能力枚举、功能实测和性能对照是不同层次的证据。

## 基准与产物

| 对象 | 本次核查结果 |
| --- | --- |
| 原 ModAVD | 本仓库 `3f7962114938fd4c09a8ee9e3a6e8add50851ade`；AOSP Emulator 37.2.10 / mas-36 / ModAVD 0.36.0 |
| 原补丁 | `series.json` 的 QEMU mas36 65 项、gfxstream mas35 23 项、Crashpad 1 项，共 89 项；不重复累计旧系列 |
| 当前核心源码 | 原89补丁 + 3 个构建 overlay + 9 个 Darwin QEMU 功能 overlay；QEMU 完整树 `3058e5f90f327708e08e4df6eff63b28362479b3`；gfxstream 2个overlay（native-bind及成功读回才发布）树 `d14277bc86e27f74baa1a1716b1ee29f71587895`，配套AEMU声明树 `04a678d0a729c6f9870e5f2c8149256294489298` |
| Manager 基准 | `21c2a3ffa22eb37a5e39e61b1b53f2258d0f3e7a` |
| 历史1955补齐包 | `.macos-work/package/ModAVD-gapfix-1955`，Manager补丁 `1955b54a9c6916647daf6354fb28687e342cb10d10082f87e355827ea4afc91d`；417 APFS clone、507 项不可变负载校验通过。ARM Vulkan/idlepoll、完整AVD复制后数据保持和受控kill恢复已实测，当前正常停止。先前2a16/2182包各保留独立证据。 |
| 历史D8独立补齐包 | `.macos-work/package/ModAVD-gapfix-media`，D0007/D0008及AEMU声明overlay，418 APFS clone并verify通过；Qt 32.87秒开机，旧sentinel/32GiB/home/driver保持、sync正常关机3.74秒。main backend SHA `67812b82a0bbfcad9b6a65184b45d18b848ef66867b05bacb90ba26a29ec6d99`。无RAM实验补丁。 |
| 历史D9独立补齐包 | `.macos-work/package/ModAVD-gapfix-shm`，3构建+9功能overlay；418clone、508payload verify。Qt34.8589秒/headless12.2905秒开机，两条引擎各80start/80stop/40cycles及停止冻结/恢复新帧均过，正常sync关机；旧数据/驱动保持。backend与D8精确相同。无RAM实验。 |
| 当前G2独立补齐包 | `.macos-work/package/ModAVD-gapfix-readback`，418clone/508payload verify；Qt31.7949秒/headless13.2517秒，各7项retention与7项SHM生命周期、HOME/流恢复及正常sync停机通过。新backend SHA `35fbd4366185ea5985e41e495c73c910030e7522075ef23de7c6317ea6f60920`，无私有RAM；新backend视频未重跑。 |
| 最新本地更新ZIP | `.macos-work/releases/ModAVD-gapfix-1955.zip`，391,402,596 B，SHA-256 `c1f617d92488c2e4965de7fc9f5b927cb387769b40e2157e55504400e3ad1562`；不含SDK/实例。相同0.36.0的prepare正确拒绝，不将此轮记作安装成功；早前真实更新/中断恢复证据单独有效。 |
| 原游戏包 | `.macos-work/package/ModAVD-validated`，Manager 补丁仍为 `acc3c046e9eecfdc85711c2bf4322358c91776d8025b91d654cebfb8a4240a09`；审计采样时运行，本轮后续只读复核 PID54065与console20000已不在。未覆盖该包，也未在本轮向它执行关机。 |
| 宿主 / 客体 | Apple M4、16 GiB；Xcode 27.1 / SDK 27.0；官方 Android 14 Google APIs ARM64 r14、adb 37.0.1 |

用户范围保持：本地个人使用、ad-hoc 签名、新建 ARM64 实例、保留至少 20 GiB 可用空间；不移植 AUTO-MAS，不迁移 Windows 数据，不做 x86 执行桥。ARM 原生运行不意味着所有被 ARM 分支跳过的优化都已有等价实现。

## 审计发现与补齐状态

1. **HVF SIMD 映射错误已修复。** 固定 AOSP 原已有 Q30→Q20 的错误，ModAVD 和此前移植产物继承；D0006 将其改为 Q30→Q30。真实隔离 HVF VM 的32寄存器 put/get、guest STR/LDR 及原缺陷负对照通过，两个引擎已重新构建签名并装入新包。完整QEMU单CPU SIMDworker又通过10次暂停/恢复、3次snapshot保存/载入/删除，2,135,867,594轮无错；不外推每vCPU和宿主睡眠。本次又查到 Google `emu-main-dev` 的 `cf0f40a0`（2026-10-27）仍有同一错误，不泛指所有 Google 发行版，也不推断游戏曾因此损坏。证据：`.macos-work/qa/hvf-simd-roundtrip-fixed-20261101.json`、`core-hvf-simd-update-20261101.json`、[当前 Google 开发分支查询](.macos-work/qa/google-qemu-current-branches-hvf-map-20261101.json)。
2. **物理 RAM 回收：仍未进入生产。** 私有v7的独立256KiB backing已在真实Android观察到四次resident物理释放，覆盖三个地址范围/768KiB去重范围，含同offset两代；同binary map-fault的271事务全部回滚、9个resident窗口无释放。此前v6仅cold提交的记录仍保留。累计提交、候选数、RSS/footprint或mincore归零不是当前净节省。每SG预算不约束整个暂停BH，metadata净成本未测，生产默认不变。v6实读order10；Manager生产写/读回9；v7旧launch没有逐次读sysfs，不能从4MiB SG推断最低order。冻结binary已完成单组order9/6对照：B6扩大resident覆盖且累计pause上升，4resident及1cold全局计数非exact，仍不改生产默认，详见[v7增量记录](ports/macos/qa/hvf-object-reclaim/v7-android-progress.md)。
3. **ARM guest Vulkan 已修复并接入正式启动。** 原 ARM r14 驱动在真实 GPU 读回中6组KHR更新重复首值；修复后12组buffer/image/view × core/KHR测试全部正确，四线程4096对image/buffer共8192次绑定通过。正式 Manager 已从客体原库自动生成精确哈希候选、推送、挂载并重启zygote，GPU再次通过；重复start保持zygote/SystemUI PID与boot ID。证据见 `.macos-work/qa/guest-vulkan-{original,candidate-v2,auto-manager}/` 与 `gapfix-integrated-{start,adopt-verified}-20261101.json`。
4. **现有游戏包保留，新包独立验收。** `.macos-work/package/ModAVD-validated` 没有被覆盖。新包 `ModAVD-gapfix-native` 的实例3已真实初始化、Qt/headless启动、正常关机与16→32 GiB扩容；GUI其余操作仍按各自证据判断。
5. **随包维护和本地更新已补齐。** 四个 shell 入口提供组件补全、许可、问题 ZIP、脱敏报告；已实查固定组件和 B 服2.7.81版本。真实含 Qt链接、Mach-O与`.app`的ZIP已通过 prepare/install/中断 recover，SDK和用户状态哈希保留，安装后包清单复核通过。离线更新不依赖远程发行服务；远程分发验收仍未做。
6. **Darwin 活动日志保护已修复。** 新 Manager 使用 libproc/vnode 活动文件扫描、PID启动时间复验和跨进程日志目录锁；同UID非子进程 writer也受保护。数量/总量清理、锁竞争、孤立 writer 与Go/race测试通过；不再依靠 macOS 上无效的 rename-to-self 拒删语义。见 `ports/macos/manager/VALIDATION.md` 与 `.macos-work/qa/update-native-real/`。

7. **视频所有权与生命周期已补齐。** D0007修复Google baseline已有的三处数组owner类型；D0008修复dispatcher残余plugins清理、Context在释放alias前销毁decoder，以及HEVC snapshot active count。完整源码ASan与原负对照通过；D8封存生产backend的H264/HEVC硬件helper普通/ASan各56会话、448帧、5,898,240B精确对比通过。真实FrameBuffer在GL/无GL两模式普通/ASan各16帧验证RGBA/BGRA、变化alpha、GPU读回和GetPixels，通过且实际dyld库身份确认。ASan仅新编译driver，不冒称整个backend/Apple框架插桩；未覆盖Android视频服务、VP8/VP9、异常码流、纹理池和长稳。证据为`d8-native-gpu-video-suite-20261101.json`与`media-lifecycle-d0008-suite-20261101.json`；旧已错误framing的HEVC snapshot不自动修复。

## 功能与优化对齐矩阵

“实机通过”仅覆盖表内场景；“部分”表示有实现或窄证据；“缺口”表示明确未完成。性能数字没有本机对照时不判通过。

| 原版功能 / 优点 | 判断 | 证据与尚缺项 |
| --- | --- | --- |
| 硬件加速、ARM64 应用运行 | 基础实机与 SIMD 专项通过 | headless / Qt 均启动 API34 ARM64；B 服 APK 以 arm64-v8a 安装运行。D0006 Q30修复及32SIMD寄存器HVF往返通过，新包真实开机。 |
| 合理核数 | 实机通过窄范围 | 核心请求 16 核裁剪至 8，guest present/online 0–7。Manager 仍保持原 2/4/6 核选项，不等于 GUI 提供 8 核。 |
| 低虚拟化开销、计时、中断优化 | 未证明效果对齐 | HVF 有 ARM timer/WFI；ranchu 仍用 QEMU GICv2，未增加 HVF GIC offload 或同粒度 VM-exit 统计。暂停/恢复、宿主睡眠、持续负载待测。 |
| free-page reporting / 归还物理 RAM | **实现缺口** | 实机已确认 reporting 协商 bit5=1、order9；Darwin 安全物理回收生产后端没有完成。 |
| 磁盘 write-through、L2 cache、聚合 I/O | 部分 | 真实客体块设备读回 write through，共用参数和 POSIX 聚合 I/O保留；小同步I/O A/B与受控一次guest sync后SIGKILL已验收：3种qcow2无错，重启后sentinel/32GiB/home保持。该受控试验不涵盖任意掉电或未同步写入。不能承诺未同步应用数据零丢失。 |
| APFS 稀疏盘、紧凑 inode | 部分 | 真实 `/data` 为 ext4、4 KiB block、1,048,576 inode；vda–vde 逻辑/物理扇区均为512 B，文件系统块不能混称4K磁盘扇区。卷判断适配和紧凑布局保留；不能引用 Windows 省空间/提速数字作本机收益。 |
| 保数据扩容 | 16→32 GiB 实机通过 | 新包实例3的块设备和ext4均扩至32 GiB；4 MiB随机文件SHA-256逐字节保持，前后正常停机的qemu-img check均无错误。证据为`.macos-work/qa/gapfix-disk-{before,after}-20261101.json`及对应check文本；不外推任意旧盘布局和所有容量。 |
| slirp 缓冲、SO_ERROR、keepalive、连接稳定 | 专项部分通过 | 共用修复及 Darwin keepalive；33,792 B HTTP 哈希和真实 Android/slirp 160并发TCP、160UDP、半关闭/refusal/RST/恢复及32MiB传输全字节校验通过，见[网络专项](ports/macos/qa/NETWORK-CHECKS.md)。尚无上游吞吐对照、dead-peer/黑洞、ICMP洪泛或CDN证据。 |
| Wi-Fi / eth0 fastpath、客体写回等调优 | 部分 | 真实初始化已执行；dirty_expire_centisecs=200、dirty_writeback_centisecs=100。network_watchlist_enabled 为空，不能判定已验证为0；其余设置仍需按项核对。 |
| 小 I/O 期间 idle-poll | ARM收益已测，正式接入已运行 | 6轮32 MiB/4096次8 KiB+fsync+完整读回，off/on中位23.07→32.11 MiB/s，QEMU CPU秒1.63→2.10。动态SCHED_IDLE双loop在忙时开启、空闲1.6秒后退出、off清除watcher；正式Manager已解除ARM跳过并自动启动watcher。仅证明该负载，不宣称游戏/CDN同幅提升。 |
| GLES / Vulkan 游戏图形 | 部分，基础实机通过 | Apple M4 GLES3.0、Vulkan1.3.0 枚举及游戏画面；不保证系统 GL 路径提供 GLES3.1 compute，也不是默认全部 ANGLE/Metal。 |
| descriptor / pNext / YUV / context / texture 修复 | 部分专项通过 | native EGL overlay 检查 NSOpenGL/CGL 真正当前上下文并传播失败，27个原生测试含静默失败负对照通过。其余descriptor/pNext/YUV/texture故障分支仍按具体证据判断。 |
| guest Vulkan khrfix / bindlock | 正式自动修复和真实GPU通过 | 原ARM库6组KHR更新错误已复现；v2修复后12组GPU读回、pNext、4线程4096对image/buffer共8192绑定均通过。原生controlled-map并发用例证明锁范围；已通过正式自动制作/缓存/挂载/zygote重启；客体reboot后同QEMU由bootwatch直接cache挂载，无NEED_UPLOAD，hostcache hash/mtime保持；没有重复制作。 |
| GPU 内存 / ASTC / staging 优化 | 条件适用，收益未证 | staging 初始 128→16 MiB 保留；进程驻留节省未测。Apple UMA 未必命中 WC cached 替换；ReBAR 隐藏正常启动强制关；BC3 默认关且缺能力门禁。 |
| 视频解码与纹理回收 | 硬件helper与dispatcher/Context专项通过，整体部分 | D7/D8修复、H264/HEVC真实硬解normal/ASan、原缺陷负控通过；VP8/VP9真实解码、纹理池、异常流和长期内存仍待验收。 |
| 本地 crash、无自动 metrics / 更新 / feature 拉取 | 源码对齐，实机不完整 | 最终策略保留；受控本地崩溃报告、保留上限和对应宿主联网专项仍未验收。客体/游戏流量另计。 |
| 精简工具栏、Retina、窗口显隐 | 部分，主窗实机通过 | show→hide→show、主窗/工具栏、guest boot ID 连续性通过；新增副屏 guard、跨屏缩放和焦点恢复未完整实测。headless 不能原地变 Qt。 |
| 宿主输入法 | 部分 | IME 自动安装/选择；console→guest 预编辑/提交/删除/退格 5 项往返通过。Cocoa helper 的 Accessibility 预检为false，未执行原生按键注入；候选词、组合键、死键、repeat、焦点释放、surrounding-text 仍未完整验收。 |
| AVD 增强 SHM 截图 | 模拟器侧实机通过，扩展矩阵部分通过 | G2修复未初始化cache及失败发布，真实GPU的RGBA/RGBX/BGRA源、RGBA/BGRA目标、拒绝/旧帧保持/恢复和GetPixels边界通过。Manager四方向三consumer收证与设置恢复完成，exactADB像素矩阵false保留；尚不证明旋转像素、无撕裂或MAA消费。新包双引擎结果见文末。 |
| MAA Verge 实际使用截图增强 | **未确认** | 00:28:51 重测 AVD+MaaFwAdb 成功，UI 实际方式未确认；当时 worker 未见对应 SHM 映射。不推定用户之后的新连接状态。 |
| GUI / CLI / MCP | 入口保留，实机部分 | 原 CLI 20、MCP工具11、HTTP注册26，当前均未删入口。真实 create/初始化/正常stop/start/APK install，MCP握手/list/get/check已过；最新1955组合已装配并实测CLI启停/自动修复；其余GUI交互和写操作未全部实测。 |
| 日志采集、保留上限与安全清理 | Darwin修复已构建入新包 | libproc/vnode扫描及PID启动时间复验、跨进程目录锁、同UID孤立writer保护与数量/总量/race回归通过；新包实际采集logcat。未删除原游戏包的活动日志做试验。 |
| 多实例、端口/锁/进程隔离 | 有实现，实机未闭环 | 原契约和 Darwin 身份检查保留；无两台真实实例隔离/并发结果。不能绕过内存保护补成绩。 |
| 官方 SDK 补全与许可/哈希 | 随包入口已补齐 | maintenance提供许可、固定组件status/repair、3镜像回落、Range、双哈希及原子替换；保留原许可收据。真实status全部verified；损坏修复/弱网及回落主要由34项隔离测试覆盖。 |
| 一键问题包、脱敏配置复制 | 随包入口已补齐 | collect-info/report-config已随包，问题ZIP实际生成；只读live报告识别Apple M4/16GiB及B服2.7.81。截图/clipboard需显式选项；本轮没有覆盖剪贴板或导出当前游戏截图；真实日志ZIP的home路径、邮箱及常见认证值模式检查通过。 |
| 更新、中断恢复、用户状态保护 | 真实本地ZIP闭环通过 | 目录单元事务保留Qt内部链接及4种代码签名xattr；真实core/.app安装、rename后断点recover、签名及PACKAGE清单通过，8项SDK/用户数据hash保留。远程发行服务未测；长期任意回滚并非原版能力。 |
| 本地整包和签名 | G2新包完整性与双引擎实机通过 | ModAVD-gapfix-readback含D7/D8/D9、2个gfxstream及AEMU收据，418clone/508payload verify、Qt/headless开机及各80start/80stop/40cycles、数据保持/正常停机通过；生产RAM仍缺，不表示全部补齐。 |
| B服《明日方舟》回归 | 安装/首启过，实战未验收 | 用户APK ZIP/ABI/安装通过，有真实游戏画面；按用户安排暂缓登录回归，战斗/自动化任务/帧率/长稳/重启无完整结果。 |

Windows/WHPX 的 XSAVE、TSC/APIC、WSA、DISM、VS，以及 CUDA、AMD X3D 绑大 L3 核、x86 ARM 转译 cpuinfo/执行桥，不要求在 Apple Silicon 逐字复制。对应的用户目标仍须独立验收；交由 macOS 默认调度不能报告为复现了 Windows 绑核/优先级的收益。

## 详细依据与本次验证

- [核心89项审计](ports/macos/qa/CORE-PARITY-AUDIT.md)：最终源码、撤回关系、编译平台条件和每项证据。
- [Manager审计](ports/macos/qa/MANAGER-PARITY-AUDIT.md)：原入口、平台实现、构建与运行包差异。
- [runtime审计](ports/macos/qa/RUNTIME-PARITY-AUDIT.md)：组件/客体调优/SHM/IME/随包工具。
- 本轮重跑 `package.py verify` 成功，489个payload entries；日志 `.macos-work/qa/parity-package-verify-20261101.log`。工具仍输出 `runtimeValidation=pending`，没有擅自把它改成全部通过。
- 本轮重验最新 Manager `.app` deep/strict ad-hoc 签名通过；比较SHA确认运行包与最新dist的CLI/GUI后端不同，WKWebView helper相同。
- 本轮runtime/package/capture/guest_checks定向合成测试65项通过（3.851秒）；既有port QA完整记录73项通过。既有选择性核心测试89通过、2个上游Mac扩容用例主动跳过；Go test/vet、10项前端策略与17个更新顶层用例通过。这些不等于全上游suite和全部用户功能通过。
- 文档完整性复核确认核心表65Q+23G+1C无遗漏/重复；Manager与固定基准入口集合和顺序一致。新增及更新文档的本地链接、空白和潜在凭据模式检查通过，机器记录为`.macos-work/qa/parity-audit-document-check-20261101.json`。

本轮未覆盖原游戏包、修改MAA设置或处理游戏协议/登录；启停、扩盘及驱动试验仅针对隔离QA实例。内存实验也使用独立目录和端口。

## 补齐顺序

1. 已完成HVF SIMD映射、Darwin活动日志、native EGL绑定检测并回归，重建/签名与产物关联。
2. ARM guest Vulkan已接入并通过真实GPU；完成安全物理回收后端及释放/重用/并发完整性试验。
3. 最新1955已独立装配并完成启停、完整复制后数据保持及受控kill恢复；GUI其余交互、双实例隔离继续验收。
4. 已补齐组件补全/问题包/脱敏报告，真实核心与`.app`本地更新/恢复闭环已通过；后续新组合继续按指纹验收。
5. 验收Cocoa IME、SHM消费端、图形/视频故障分支；按用户登录安排，仅用B服《明日方舟》做任务/帧率/长稳。磁盘、网络、内存和虚拟化收益分别用本机对照测量。

2026-11-01 后续隔离QA记录：曾因错误删除`version_num.cache`触发固定AOSP重建副本userdata，源QA未改。已按完整stopped AVD复制重新验证旧sentinel/32GiB/µLauncher保持；直接原因及复制边界见`.macos-work/qa/avd-clone-reset-source-audit-20261101.md`，真实结果见`gapfix-1955-retention-20261101.json`。受控SIGKILL与恢复见`gapfix-1955-{controlled-kill,after-kill-verified}-20261101.json`。原生重启失败回滚负例已通过；本轮尝试对已加载驱动的真实mount做故障注入，预先umount失败，未执行注入，不冒称真实故障回滚通过。

后续SHM契约收证：`.macos-work/qa/manager-shm-media-contract-20261101/report.json`确认四方向固定natural1280×720、每方向3reader×12只读mapping、设置精确恢复；`ok`只表示收证与metadata，`pixelParityEstablished=false`明确保留，未赋色差容差。首轮错误旋转/静态heartbeat假设及false证据不覆盖；源契约说明见`.macos-work/qa/manager-shm-source-review-20261101/assessment.md`。D8构建、包内二进制和各专项报告hash汇总见`.macos-work/qa/d8-integration-index-20261101.json`。

D9生产补齐已完成构建和签名：新增`0009-shared-memory-recording-lifecycle.patch`，QEMU为3构建+9功能overlay，完整树`3058e5f90f327708e08e4df6eff63b28362479b3`。start/stop/析构幂等、同步callback注销及无frame队列回收的host QA为12普通+12ASan/UBSan通过、6个原代码负控全部失败；独立review38项及既有CallbackRegistry6项通过。24B SHM ABI不变，stop保留旧映射；`shm_ready`不证明stream active。backend与D8精确相同，既有硬解/GPU专项可按identity复用。新包`ModAVD-gapfix-shm`418clone、508payload verify通过，实机证据另列。首次start在后台编译期间被正常内存预检挡下（可用4.3GB<4.5GB）；未绕过门禁，原失败记录保留。

私有RAM v7已通过固定256KiB对象、精确allocation granule/serial收据与每个SG/range调用最多2次native尝试/32对象检查（非整个queue element或暂停BH的总预算）的native及成本gate。2.5GiB untouched对象构造从2MiB粒度1280objects/7.441ms变为256KiB粒度10240objects/40.631ms，释放1.190ms，region分配前55→构造后10297→释放后55（其中target10240）；单次值不作SLA，footprint不变不代表kernel metadata无成本。v7实际allocator两代native正控4608、monolithic负控2304及故障检查通过，systemwide counter非exact窗口原样保留。见`.macos-work/qa/exact-v7-native-20261101/assessment.json`和`dcr-construction-cost-20261101/assessment.json`。真实Android驻留页释放、安全与第二代验证仍在进行，生产默认未变。

D9封存独立包`ModAVD-gapfix-shm`现已完成真实双引擎验证：Qt开机34.8589秒、headless12.2905秒，各0warnings；各80次重复start、80次重复stop、40轮stop/start、停止期间真实Settings显示变化而SHM header/pixels不变、restart观察新帧及恢复HOME/活跃帧、同一boot ID全部通过。两次均graceful/synced=true关机（Qt3.9737秒、headless见stop收据），旧sentinel/32GiB/µLauncher/driver保持。证据为`.macos-work/qa/manager-shm-d9-{qt,headless}-20261101/report.json`、`gapfix-shm-retention-20261101.json`和`gapfix-shm-{qt,headless}-stop-20261101.json`。这些是专用QA实例公共console与SHM短观察，内部refcount由host专项证明；每次read重新open不能证明旧reader跨stop持有同一object。不声称ADB像素相等、帧无撕裂或MAA已消费增强。原游戏包和MAA未修改，私有RAM仍不在此包中。

补充预算边界：v7的2次native尝试/32对象计数在每个`qemu_ram_reclaim_report()`调用重置，virtio-balloon逐SG调用；一次BH暂停最多drain32个元素，每元素可有多个SG，故这些数值不构成整次暂停上限。既有`report_work_us`也不含全vCPU暂停等待、元素解析及全部候选审计扫描。正在补完整分段遥测，不把同步函数耗时误报为整次pause成本。

D9构建、包内四件套、host/上游/双引擎实机、保数据与正常停机的41项一致性复核见[D9集成索引](.macos-work/qa/d9-integration-index-20261101.json)。该索引按精确backend身份复用D8 GPU/视频证据，未将其范围扩大为D9完整游戏或生产RAM验收。

后续私有 v7 telemetry-only 客体已取得驻留物理释放：4 次释放覆盖 3 个 256 KiB 地址范围（768 KiB 去重范围），含同 offset 两代 backing；这不是当前净内存收益。完整暂停遥测也确认每 SG 的预算不构成整次 BH 上限，观察到最大 request→resume 返回 14.068 ms。冷对象全局计数仍有噪声，内核 metadata 净成本未测，生产默认未变；详见 [v7 Android 增量记录](ports/macos/qa/hvf-object-reclaim/v7-android-progress.md)。

G2新检查点：`0002-no-gl-readback-publish-only-success.patch`已完成生产构建/签名，gfxstream完整树`d14277bc86e27f74baa1a1716b1ee29f71587895`（2 overlays），backend SHA`35fbd4366185ea5985e41e495c73c910030e7522075ef23de7c6317ea6f60920`；CPU正负控8组及native v4共8模式192帧通过。新包`ModAVD-gapfix-readback`418clone/508payload verify，Qt31.794854458s/headless13.251739375s开机，各7项数据/配置/实际backend保留和7项SHM生命周期及HOME/stream恢复通过，graceful/synced停机分别3.64848575s/3.383434625s，后续确认两PID消失。失败历史、RGBX两模式契约、额外scratch/copy成本和覆盖边界见[G2验证说明](ports/macos/qa/framebuffer-readback/G2-VALIDATION.md)。G2 backend与D9已不同，旧视频结果不能写成新G2视频重跑；生产RAM和原游戏包/MAA状态不变。

G2文档快照的15份完整snapshot/169项检查及QA导出9项均通过，见[冻结审核](.macos-work/qa/g2-cross-doc-final-review-20261101/assessment.json)。该快照后另追加[单组order9/6结果](incoming/macos-qemu/experimental/REPORTING-ORDER-RESULTS.md)：B6精确完整resident窗169个，地址去重27.75MiB、累计旧PRESENT42.25MiB；4个resident和1个cold全局计数非exact，累计pause增加，净metadata/整机收益仍未证，生产默认保持9。两个实验guest均已正常停止。
