# macOS 功能与优点验收矩阵

核查日期：2026-11-01。对象为固定 AOSP 37.2.10 / mas-36 源码、QEMU 65 + gfxstream 23 + Crashpad 1 个原补丁，另有 QEMU 侧 9 个 Darwin 功能补丁及 gfxstream 侧 2 个 Darwin overlay。最新 G2（Darwin gfxstream 0002，并非原 G0002 compute 补丁）修复 noGL 失败帧发布；`ModAVD-gapfix-readback` 新根通过严格签名、418 clones / 508 payload 校验，Qt/headless 分别 31.7949/13.2517 秒开机，保留与 SHM 公开启停各 7 项通过并 synced/graceful 退出。Manager 仍为 1955b54a，QEMU 仍为 D9。新 backend `35fbd436…` 已独立通过 CPU 失败契约与原生 GL/noGL 矩阵；旧 D8/D9 VT 结果仍只属于 `67812b82…`。Android SHM 与 ADB 精确像素相等仍未成立，MAA 接入未确认。原有游戏实例保持旧包；保数据复制、扩容、idlepoll、Vulkan 自动修复及已同步单次强杀恢复按各自版本记账。private RAM v7 已取得 4 次驻留对象释放（3 个去重范围、含同 offset 两代）及故障/冷尾独审；整体净 RAM 收益仍未验收，严格全局计数仍 false、metadata 净成本未测，生产默认未变。

完整对齐审计与补齐顺序见[当前总报告](../../macOS-compatibility-report.md)，89 项重新核查见[核心审计](qa/CORE-PARITY-AUDIT.md)。D0006 已修复两引擎继承的 HVF Q30→Q20 SIMD 错映射，独立真实 HVF 双向寄存器测试及双目标构建/签名通过，隔离 QA 包已使用该核心；原有游戏实例没有被覆盖。此错误继承自固定 Google/AOSP 上游；另查 2026-10-27 的 emu-main-dev tip `cf0f40a0aad1aa3fca68f2f89372525c8d28ca68` 仍为 case30→Q20，限定该开发分支提交，不泛指全部 Google 发行版。证据为 `.macos-work/qa/google-qemu-current-branches-hvf-map-20261101.json`；[再次源码核查](../../.macos-work/qa/hvf-origin-answer-recheck-20261101.json)确认固定base与该Google提交的hvf.c完整SHA相同、均Q30→Q20，当前Darwin为Q30→Q30。

**89 个补丁已回放，不等于 89 项效果已在 macOS 生效。** 原系列含撤回、注释、诊断、Windows 专用修复和有条件的 GPU 实验。最明确的未闭合优点是实际物理内存回收；图形能力、计时/中断开销及磁盘/网络收益也必须用本机测量。Windows 的吞吐、帧率、退出次数和内存数字不能直接移作 Apple Silicon 的结果。

这里的“保留”只指最终源码中保留该行为；“已适配”表示有 Darwin 实现与局部测试；“上游等价”表示已有另一条宿主实现实现相同任务，仍需集成验证；“待实现”表示有明确的生产代码缺口。“条件适用”按实际后端和硬件能力决定。下表分别记录已取得的实机证据与尚未完成的验收，基础开机不能替代整组效果验证。

已确定采用原生 ARM64 Android、新建本地实例和本地 ad-hoc 产物。x86 转译桥、Windows AVD 迁移不属于此目标的缺陷；Developer ID 和发行公证不作为个人本地使用的验收项。游戏回归只用《明日方舟》。

## 剩余缺口与验收

| 编号 / 用户可见目标 | 当前实现与证据 | 剩余缺口 / 验收依据 |
| --- | --- | --- |
| A01 原生加速、无头和窗口运行 | 核心构建/签名、HVF 检查及 headless/Qt 开机通过；D0006 修复 Q30 映射并通过真实 HVF 双向测试。隔离 QA 的 Manager 3 GiB 实例正常启停，2182eac1 集成包完成 ARM64 Vulkan 自动启动与客体重启闭环。 | 1955b54a 新根实际启动、保数据复制及受控强杀后恢复通过；副屏 guard 尚未以新副屏实测。 |
| A02 合理核数、低虚拟化开销与稳定计时 | D0003 按宿主核数、HVF/GICv2上限裁剪，真实16→8核开机通过。HVF已有ARM虚拟定时器/WFI/快照偏移；修复Q30后完整QEMU单CPU忙worker完成10次pause/resume、3次snapshot save/load/delete，2,135,867,594轮无错。 | 单worker SIMD证据不覆盖每vCPU、clock monotonicity、宿主睡眠/唤醒或持续游戏。1/2/4/8负载与中断/退出开销仍待测；当前ranchu为QEMU `arm_gic`，未增加HVF内核GIC，不移用WHPX内核APIC降低退出数字。 |
| A03 GLES / Vulkan 游戏兼容与图形稳定 | 通用修复保留，MoltenVK 为 Darwin Vulkan 默认 ICD。ARM64 encoder 原库的 6 组 KHR template 更新错误由真实 GPU 负控复现；73ddfe09 修复后 12 组 GPU 读回、pNext 和 4 线程 8192 bind 均通过；Manager 自动生成/挂载、重启缓存和无重复 zygote 重启已在 2182eac1 QA 包通过。 | 这是特定 encoder 修复闭环，游戏绘制、全部格式/扩展/内存类型和故障边界仍待测。API34 的 host GLES 不自动等于 ANGLE/Metal；系统 GL 路径不能保证 GLES 3.1 compute。 |
| A04 安卓释放内存后真正归还宿主 | v7 固定 256 KiB 独立对象、精确 granule/serial 收据，每个 SG/range 调用最多2次尝试/32对象检查；原生组件与实际客体正确性证据分别保留。后续 PID2276 有4次驻留释放：累计1MiB、3个去重范围768KiB，含同offset不同旧object的两代；271次map-fault事务全rollback，9次驻留窗口保持旧对象和resident。 | **生产整体净收益仍未验收、默认未启用。** 正向冷窗口10/262有全局free/spec噪声，严格全guest判定仍false；累计量不是当前净省。131个4MiB SG尾部1834对象/458.5MiB全cold，不是释放量，也未发现覆盖尾部饥饿。2/32逐SG重置，不是BH/pause cap；metadata净成本未测，region峰10297。冻结binary的A9/B6阶数对照收证进行中；首次参数写入失败已正常停机、未进测量，retry1进行，无比较结论，见[最新边界](qa/hvf-object-reclaim/v7-android-progress.md)。 |
| A05 强杀后磁盘可用、紧凑建盘和保数据扩容 | 共用 Q0006/0007、Q0061/0063/0064 与 D0002 保留。隔离 mas_3 经 Manager 从 16→32 GiB，块设备及 ext4 同步增长，重启后 4 MiB 随机哨兵 SHA256 不变。1955b54a 完整复制保留兼容标记后，旧哨兵/32 GiB/µLauncher 保持；sync 后单次 SIGKILL、3 种 qcow2 离线 check 及重启保留均通过。 | 未同步应用写入、任意崩溃、新盘物理占用/峰值及广泛 I/O 矩阵仍待测。此前误删 version_num.cache 的失败副本仍单独记账，见下文。 |
| A06 下载吞吐、连接失败及时返回与多连接稳定 | 通用slirp缓冲、SO_ERROR和ICMP fallback上限保留；D0001真实socket选项检查通过。隔离ARM64 loopback的160活跃TCP、32MiB、8×4MiB、half-close、errno111/104、160UDP及恢复八项全字节检查通过，最终active0、同boot且清理完成；v5重跑与memory probe有4.274秒host窗口重叠。见[网络检查](qa/NETWORK-CHECKS.md)。 | overlap只指整段host窗口，非每case同时触发；pre-ASG原运行与v4 overlap0保留。dead-peer keepalive、外部黑洞/OOB、ICMP fallback并发及CDN/长稳仍待测，不证明相对提速或生产物理收益。 |
| A07 精简 Qt 界面、Retina 与运行中显隐 | Q0027/0054、Q0031/0034 保留；D0004 增加同步 console `window show/hide`。首轮真实 Qt show/hide/show 的窗口表与 boot ID 确认主窗/工具栏显隐且客体继续运行。后续副屏修复已增量编译成功。 | 副屏修复重启后复验、Retina/跨屏/缩放无裁切与焦点恢复；不能把首轮主窗结果扩大为全部窗口路径通过。真正 headless 无窗口，返回 KO 符合设计，不能声称可直接变成 GUI 实例。 |
| A08 宿主输入法、脚本输入与按键一致性 | Q0058/0059、D0005 保留；Manager 安装并选择 IME。真实 Settings EditText 的 preedit/finish/中文ABC commit/delete/key67 共5项 console→guest 往返通过。 | Cocoa 真实中文/日文会话当前被 Accessibility 权限拒绝阻断，preflight 为 accessibilityTrusted=false；候选、组合键、死键、repeat、焦点释放等未验证。surrounding-text/replacement 查询仍不完整，console 不能替代原生输入会话。 |
| A09 增强共享内存截图与无 GL 读回 | G2 保持虚接口 void，增加非虚 bool 读取入口；仅 noGL 预分配并复用 scratch，失败/部分写后失败保留 last-good cache 且不回调。CPU baseline/candidate×GLES1/0×normal/ASan 共8组：baseline按预期失败、candidate全过。sealed D9/G2×normal/driver-ASan共4个原生wrapper、8个GL/noGL模式全过，每模式24帧，覆盖RGBA/RGBX/BGRA源与RGBA/BGRA目标；另有拒绝/恢复及GetPixels边界。G2 Qt/headless各通过SHM生命周期7项并正常同步退出；D9主机专项12普通+12ASan/UBSan及6旧码负控保留。 | 初始零cache不算有效帧；bool不普遍检测全部GL驱动错误。RGBX的noGL alpha固定255，GL按同源GPU实际X字节精确核对，未加容差；旧错误统一alpha断言仍记失败。ASan仅仪表驱动/契约替身，非生产库。独立ADB/SHM像素相等、Android guestVulkanOnly、MAA、并发销毁、无撕裂和长稳仍待验收。live不观察内部引用数，每次重开映射不证明持久reader；`shm_ready`仅代表映射存在。见[读回工具](qa/framebuffer-readback/README.md)及[SHM工具](qa/shm-lifecycle/README.md)。 |
| A10 视频解码和纹理生命周期稳定 | D0007 修复 VT IDR 数组所有权；D0008 清理 H264/HEVC/VPx dispatcher、先销毁 dispatcher 再释放共享页，并补 HEVC 快照计数。历史 D8/D9 backend `67812b82…` 的 VT helper normal/ASan 每轮56个硬件会话、448帧、5,898,240字节精确比较及输入撤权通过；D8所有权/Context计数测试与原缺陷负控通过，D7 legacy TU证据保留。 | 最新 G2 backend 为 `35fbd436…`，本轮没有重跑 VT，旧结果不能当作新 backend 的 fresh VT 证据。helper仅为128×96 CPU I420；计数替身不替代真实Android codec/GPU纹理/快照。VP8/VP9、异常/大分辨率/重排流、回退和长稳仍待测。见[视频工具](qa/native-vt/README.md)与[生命周期工具](qa/media-lifecycle/README.md)。 |
| A11 本地诊断、不自动上传/拉取核心元数据 | 核心禁止 crash/usage 上传、核心更新和 feature 下载的行为保留。随包 collect-info/report-config 已实现；实际使用日志的问题 ZIP 通过有限隐私检查，活动日志由 libproc FD/进程身份与跨进程锁保护，数量和总量清理均有回归。 | 受控崩溃、尾日志、Crashpad 报告生命周期及实际无请求观察待测；自动过滤不能保证识别任意日志自由文本中的个人信息。可选截图未作自动匿名处理。 |
| A12 管理器 GUI、CLI/MCP、多实例和恢复 | Darwin 入口、按实例内存判定与 discovery 修复已构建；原生 CLI/GUI/Swift helper 严格签名。真实 signed full-core ZIP 的本地 prepare/install、断点 recover 和损坏候选回滚通过，8 类 SDK/用户哨兵及 metadata 保留。 | 1955b54a 已新根装配并启动；双实例/全部 WKWebView 交互/Keychain 授权待测。在线发布端尚无 mac-arm64 release 时不能声称远端升级可用；长期任意回滚非原功能。 |
| A13 可复核的完整本地目录与《明日方舟》 | 本地装配保留许可/收据，随包 fetch-components、collect-info、report-config 已加入；G2 QA包418 APFS clones、508 payload校验通过，Qt/headless实际启动且旧哨兵/32GiB/µLauncher/修复驱动保持。原游戏包B服2.7.81已安装并显示真实画面。 | 游戏按用户决定暂缓登录回归，资源完成/战斗/任务/长稳/游戏重启未验收。镜像实际联网回落、全新用户补件/许可流程和剪贴板待实测。 |

2026-10-31 已取得官方 API 34 ARM64 客体的基础实机证据：4 vCPU、开机完成，SurfaceFlinger 报 Apple M4 GLES 3.0，Vulkan 枚举到 Apple M4 / API 1.3.0，33,792 B 客体至宿主 loopback HTTP 载荷哈希相符且任务临时文件已清理。virtio driver 可见，但 device/features 因客体权限不可读；page-reporting 配置仅作事实记录，未验证协商或物理回收。这些结果只覆盖 A01/A03/A06 的窄范围基础项，其余验收继续按表执行。报告位置、哈希和局限见 [客体检查记录](qa/GUEST-CHECKS.md#2026-10-31-实机采集)。

2026-11-01 后续只读采样已确认 virtio_balloon negotiated reporting bit5=1、order9，补足上段旧证据的协商不确定性；仍未证明物理回收。真实 `/data` 为4 KiB ext4块、1,048,576 inode，块设备逻辑/物理扇区为512 B；dirty_expire_centisecs=200、dirty_writeback_centisecs=100。具体证据和边界见 [runtime对齐审计](qa/RUNTIME-PARITY-AUDIT.md)。

## Apple GPU：三个开关不能按 NVIDIA 用法照搬

| 项目 | 当前源码的准确行为 | Apple Silicon 上的结论与后续动作 |
| --- | --- | --- |
| WC → cached：G0011/0012，`ANDROID_EMU_VK_WC_AS_CACHED` | 除精确 `0` 外默认开。只把 `HOST_VISIBLE && !HOST_CACHED && !DEVICE_LOCAL` 的分配替换为同 heap 的 cached coherent 类型；import、external/blob 和 image dedicated 分配等保持原状。 | Apple unified memory 的 MoltenVK shared 类型本就可见、coherent、cached 且 device-local，通常不满足替换条件。代码保留并不意味着有 NVIDIA/WHPX 同幅提升；需记录实际 memory types 与命中日志，不能虚构一个 Apple WC 加速结果。 |
| 隐藏 ReBAR：G0020，`ANDROID_EMU_VK_HIDE_REBAR=1` | 默认关，没有 OS/vendor guard。对每个同时具有 `DEVICE_LOCAL|HOST_VISIBLE` 的类型，清除 guest 所见的 `HOST_VISIBLE/HOST_COHERENT/HOST_CACHED`，保留索引/heap。 | 这是 PCIe 独显上传路径的实验，不是 Apple unified memory 的等价优化。MoltenVK shared 类型也会被隐藏，而 private/memoryless 不能提供 host-visible 替代，可能使 guest 无 host-visible 类型。**Darwin Manager 已强制设为 `0`，覆盖继承的 `1`；NVIDIA 专用 shader-cache 三变量仅 Windows 注入。** 已有实际子进程回归。核心本身尚无能力门禁，直接启动 emulator 仍须检查继承环境。 若将其产品化，应按实际内存拓扑拒绝/跳过不适用设备，并测 feature masks、映射和导入。 |
| ASTC → BC3：G0022，`ANDROID_EMU_VK_ASTC_BC3=1` | 默认关；启用选择现有 NewBc3 GPU decoder。相对解成 RGBA8，BC3 的输出纹理字节量为 1/4；源 ASTC 仍保留，还有中间/计算开销。 | 不能断言“Apple 支持原生 ASTC，所以永远不会运行此路径”。当前 `needEmulatedAstc()` 要求 linear + optimal 特性，而 MoltenVK compressed format 的 linear 特性为 0，仍可能进入默认 GPU 仿真。BC 能力依赖 Metal `supportsBCTextureCompression`，开关本身没有自动完成此检查。**保持关闭，待能力检查、格式创建、画质、总内存和吞吐实测。** 不能声称整个显存或进程节省 75%。 |

图形路径还需明确：`emugl_config.cpp` 中 host GLES 是 desktop GL translation；Vulkan host 在 Darwin 默认选择 MoltenVK；API 37+ 的 macOS `auto` 会偏向 `swangle`，但当前该模式设置 `ANGLE_DEFAULT_PLATFORM=swiftshader`，属于软件路径。GuestAngle 是另一条可选择的客体转换路径。这些机制不能合并写成“默认 Metal 加速全部 GLES”。本次使用 API 34，仍须以启动日志确认实际选择。

G0002 的 ESSL 3.10 compute → GLSL 4.30 重试，仅在原编译失败且 `!isGles2Gles()` 时触发。macOS 系统 OpenGL 上限及 compute 能力不满足可移植保证；ANGLE GLES 路径还会绕开该重试。涉及 compute 的功能应在选定的 GuestAngle/Vulkan 或其他已测后端上验收。

核查位置（相对 AOSP checkout）：

- `hardware/google/gfxstream/host/vulkan/vk_decoder_global_state.cpp`：WC 类型选择、`needEmulatedAstc()`。
- `hardware/google/gfxstream/host/vulkan/vk_emulated_physical_device_memory.cpp`：ReBAR guest flags。
- `hardware/google/gfxstream/host/vulkan/vk_common_operations.cpp`、`host/vulkan/emulated_textures/compressed_image_info.cpp`：ASTC decoder 选择与输出。
- `external/moltenvk/MoltenVK/MoltenVK/API/mvk_datatypes.h`、`GPUObjects/MVKDevice.mm`、`GPUObjects/MVKPixelFormats.mm`：memory types、BC 能力、压缩格式 linear 特性。
- `external/qemu/android/android-ui/modules/aemu-gl-init/src/android/opengl/emugl_config.cpp`：GLES/Vulkan 后端选择。

## 更新器、维护工具与 AVD 复制边界

Darwin 使用 mac-arm64 asset、macos/arm64 MirrorChyan 参数和本机程序名，拒绝 win-x64。新增本地入口 `update prepare --package ZIP --sha256 HEX`、`update install`、`update recover`；本地操作不读取 CDK 设置。真实更新包使用 schema1 / modavd-macos-update-v1 的 `modavd-update-manifest.json`，与仅供装配的 PACKAGE-MANIFEST 分工明确。前者列出完整文件、受限单元内相对链接、签名所需的白名单 xattrs，并严格检查 ARM64 Mach-O 及 codesign，不能靠重写清单接受坏签名或 shell 替身。

安装以 emulator/app/tools/rootmanifest 为事务单元，保护 platform-tools、系统镜像、AVD、日志及用户配置。真实完整核心 ZIP 已在隔离 installed/recovered 根通过 prepare/install、离线中断恢复、严格签名和 package verify，8 个 SDK/用户哨兵 SHA256 及实例 metadata 不变。成功后事务备份被清理，recover 仍是中断恢复，不是长期任意回滚；远端尚无本机 release 时不能宣称在线分发已经可用。下载/解包/基包复制/备份继续采用 Darwin 20 GiB 持续空间停线，Windows 原预算不变。详见 [Manager 验证](manager/VALIDATION.md)。

随包 [维护工具](maintenance/README.md) 已提供固定组件校验/修复、许可收据复用、Google 中国→腾讯→Google 回落、Range 续传、安全解包、完整 SDK 清单校验与原子交换，以及问题 ZIP 和脱敏文字报告。真实 QA 包两组件 status 均 verified；使用日志的问题包通过限定范围的路径/邮箱/凭据检查。live 查询只连接已存在并确认归属的私有 ADB，不启动服务；5037 不使用。镜像实际可达性、全新用户完整下载、真实剪贴板和受控崩溃仍待独立实测，自动日志过滤也不保证消除任意自由文本中的全部隐私。

**AVD 复制必须完整保留兼容状态。** 新集成根曾误删 version_num.cache，触发源码 drive-share.cpp:106–126 的 reset_version_number_cache，继而 :183–198 重新创建 userdata/encryption 等 qcow2；该副本旧数据和哨兵丢失，不算迁移保数据。旧源保持完整，先前 16→32 GiB 扩容证据独立有效。正确方法是正常停止后复制完整 descriptor 与 `.avd`（bases/overlays/encryption/snapshots/全部版本标记），只改 descriptor 的根路径；先核对 backing/hash，再以旧哨兵和实际容量验证新根。qemu-version.txt 缺失仅重写版本，read-snapshot.txt 关联 read-only snapshot，不能将版本/缓存标记一概列为可丢弃。源码定位见 [复制复位审计](../../.macos-work/qa/avd-clone-reset-source-audit-20261101.md)。后续 1955b54a 新根按完整复制执行，旧哨兵 `87b20f4f…`、32 GiB 和 µLauncher 实际保持；又经 sync 后单次 SIGKILL、三种 qcow2 离线 check 无错误及重启复核，旧哨兵/容量/HOME/驱动均保持。证据为 `.macos-work/qa/gapfix-1955-{retention,controlled-kill,after-kill-verified}-20261101.json`；不推定未同步写入或任意崩溃均安全。

## 本地小写 I/O 与 ARM64 Vulkan 证据

idlepoll 已恢复原动态 daemon：请求率达到 400/s 才启动每 vCPU 的 SCHED_IDLE loop，低于 200/s 持续 1 秒后停止，off 时全部退出。隔离 ARM64/HVF 的 off/on 各3轮、每轮 32 MiB（4096×8 KiB + fsync）读回全部正确；吞吐中位数 23.0706→32.1132 MiB/s，QEMU CPU 中位数 1.63→2.10 秒/32 MiB。真实观察到两个 SCHED_IDLE loop，空闲1.6秒后仅 watcher 保留，off 后全部退出。该结果只覆盖本地负载，不宣称游戏/CDN 提速。

ARM64 Vulkan 原库 d6188011 与候选73ddfe09 的精确 profile 已进入 Manager，沿用游戏前台/安装/上传后重新检查等门禁。2182eac1 QA 包首次启动 NEED_UPLOAD→精确生成→push→MOUNTED 并重启 zygote；GPU12组/pNext/4线程8192bind通过；重复start保持boot/zygote/SystemUI身份；客体 reboot 后从客体cache挂载，无再次上传、宿主cache hash/mtime不变，系统界面恢复且无warning。最新1955b54a补充 mounted SHA核对与restart失败安全回滚，已完成主机测试、签名构建和新根实机自动生成挂载，重启后从cache重挂且无warning；live restart失败注入因前置正常umount失败而未到达，不能记为实机回滚通过。证据见 [运行时审计](qa/RUNTIME-PARITY-AUDIT.md) 与 [Manager验证](manager/VALIDATION.md)。

## 内存回收是实现缺口，实验不是完成标记

当前 `virtio_balloon_handle_report()` 在返还报告元素前保有 guest 报告的空闲页，但只按单个 scatter/gather entry 处理，不会把四个独立 4 KiB entry 自动合成 Apple 16 KiB 宿主页。现有对齐/边界跳过规则必须保留，不能向外取整覆盖活页。

64 MiB 原生 HVF 实验中，`MADV_DONTNEED` / `MADV_FREE` 返回成功后内容、resident 与 footprint 没有立即下降；`MADV_FREE_REUSABLE` 只降低 footprint 计账，guest 重用没有可靠的 `REUSE` 生命周期；`MADV_ZERO` 令内容归零但没有证明物理归还。单 vCPU 停止执行期间的显式 HVF unmap → fresh backing → remap 能降低 resident 并保留测试范围外的数据，但实验没有 Android、多 vCPU、renderer/DMA、快照或故障注入。

v2 冻结采样与后续 private RAM 整机事务、独立对象机制均独立于生产。v7 固定 256 KiB granule、精确 allocation/serial 收据、两代与故障检查通过 172 项外部原生核验；4,608 个正窗口中 4,556 个为精确 +16 free+speculative pages，2,304 个 monolithic 对照中 2,282 个为精确 0，严格全局计数结论仍为 false。没有改容差，也不以 footprint/mincore/累计提交替代物理收益。每个 `qemu_ram_reclaim_report` SG/range 调用最多尝试 2 个对象、检查 32 个对象；计数逐 SG 重置，不限制整个 queue element、32-element BH、连续 BH 或整次 pause，原生组件 gate 不证明完整调用者的总预算。`report_work_us` 仅记录该函数内工作，不含停核等待、SG 映射/审计、归还队列或恢复 CPU，不能作为整体暂停时延。2.5 GiB untouched 构造单次观察为 2 MiB 粒度 1,280 对象/7.441 ms，256 KiB 粒度 10,240 对象/40.631 ms；后者释放 1.190 ms；region 在 2 MiB 粒度为55→1337→55，在256 KiB粒度为55→10297→55（target对象10240），明确包含构造峰值。该成本测量不含 HVF/guest/页触摸，不是时延上限，footprint 不变也不证明内核元数据零成本。见 [机制与边界](qa/hvf-object-reclaim/mechanism-assessment.md)、`exact-v7-native-20261101/assessment.json` 与 `dcr-construction-cost-20261101/assessment.json`。

冻结的 [v7 客体记录](../../incoming/macos-qemu/experimental/evidence/20261101-v7-reclaim-guest/capture.json) 为 PID91872，已正常停止。256/512 MiB 各 3 轮、每轮 8 秒的 live/读零/重用分别通过 148/149 次 live 检查；追加 512 MiB ×5轮×15秒通过 419 次，均零错并清理。网络 8 case 通过，与追加 memory probe 的 host 窗口重叠 9.232410 秒，不代表每 case 同时回收。[SIMD/快照记录](../../incoming/macos-qemu/experimental/evidence/20261101-v7-simd-snapshot/capture.json) 为同进程/同 boot 的单 CPU worker：10 次 stop/start、3 次快照、26 个 live 边界、2,164,996,438 次检查零错并清理；负例只验证 probe 敏感性。这份早期冻结运行的262次提交对应262个distinct offset，旧ref均1、PRESENT均0，cleanup/query error均0；该次没有驻留释放或同offset第二代证据，不能覆盖后续独立窗口。161 个 SG 中 131 个尝试2/检查2、剩余3.5MiB，30 个尝试0/检查16；32 次检查上限未触发，cold tail 未被查询，不足以判断 hot starvation。实验不属于这 9 个生产 QEMU 功能补丁。


后续[独立客体与尾部检查](qa/hvf-object-reclaim/v7-android-progress.md)不并入上述 PID91872。PID2276 的266次commit中262次cold、4次旧PRESENT=16个16KiB宿主页；4次均旧ref1、HVA resident 256KiB→0、free精确+16页/spec0、无query/cleanup error，覆盖3个去重256KiB范围并含同offset两代不同旧object。累计1MiB和去重768KiB不是当前净省；cold窗口联合free/spec非exact10/262，严格全guest仍false。正向/map-fault封存身份与物理/计时链由67项独审通过；fault客体271次全rollback/0commit、9次resident保持，不能记成成功释放。

另一个只查询诊断的131个4MiB SG尾部，共1834个不同256KiB对象/458.5MiB，PRESENT/PAGEDOUT/REUSABLE全0；159项独审通过。仅这些覆盖尾部未发现驻留候选被冷首部挤掉，不能外推全部SG或把458.5MiB算作收益。19个插桩BH最多64次native尝试/498对象检查；request→resume最大14.068ms、插桩BH最大15.144ms均为观察值，不是限额。内核object metadata净成本未测，生产保持显式实验默认off。

[reporting阶数源码核查](../../incoming/macos-qemu/experimental/REPORTING-ORDER-ASSESSMENT.md)确认4KiB guest页下，order9/10的最小buddy块分别2/4MiB；Manager写读9，旧private v6实读10。v7日志含4MiB SG且裸启动不执行Manager设置，但没有各launch新的sysfs读测，不能冒充直接读到10。参数是最低扫描/通知阶数，降低它纳入已有较小空闲块，不拆分已有大块、不自动flush或改变host256KiB粒度。历史v6 buddy容量不证明v7 host驻留/安全资格；冻结binary的A9/B6对照收证进行：首次参数写入失败已正常停机且未进入测量，retry1进行；尚无比较结论。

## 原补丁逐项覆盖索引

下表完整覆盖 65 个 QEMU、23 个 gfxstream 和 1 个 Crashpad 补丁。编号直接对应各系列文件名前缀，目录见 [series.json](../../patches/series.json)。同组编号逐项列出，撤回和后续修正按最终行为解释；不将历史实验累计成多个独立优点。

| QEMU 编号 | 最终效果与 Darwin 状态 | 验收 |
| --- | --- | --- |
| Q0001 | WHPX runtime TSC 写回修复；ARM HVF 不执行 x86/WHPX 寄存器代码。已有 ARM counter/timer 路径，未证明暂停语义/开销全部等价。 | A02 |
| Q0002、Q0003、Q0004 | VS Build Tools、Windows AUTOMOC 路径、缺 DISM 构建支持；Windows 构建专用。macOS 使用 Xcode/CMake 与独立 SDK/链接器适配。 | A01 |
| Q0005、Q0014、Q0015 | Q0005 被 Q0014 撤回；最终 Q0015 修 Windows `file://` URI 失败处理。Darwin 使用 POSIX shared-memory/file 路径，不使用 Win32 URL 转换；坏 handle 仍需端到端拒绝测试。 | A09 |
| Q0006、Q0007 | userdata write-through 与按分区大小设置 qcow2 L2 cache；共用代码保留。 | A05 |
| Q0008、Q0012 | 启动即共享视频被撤回；最终按需启动 screenrecord/webrtc 流。 | A09 |
| Q0009 | 未显式指定 `-logcat` 时默认静默转发；共用代码保留。 | A11 |
| Q0010 | Win10 WHPX XSAVE/AVX 可选保留；ARM 指令集与 HVF 路径不同，无对应 x86 开关需求。 | A02 |
| Q0011、Q0029 | WHPX VM-exit / TSC 诊断开关不在 ARM HVF 生效；尚无此次新增的同粒度 HVF 统计实现。已有通用日志不等于 exit 分布证据。 | A02、A11 |
| Q0013 | slirp TCP buffers 从 8 KiB 增至 128 KiB；共用代码保留，窗口缩放等协议限制未因此消失。 | A06 |
| Q0016、Q0019、Q0065 | Win32 poll 的 urgent-data 映射、128 socket 截断及无 node 删除崩溃。POSIX 直接使用动态 poll 集合，移除未知 fd 时检查 node；不执行 WSA 分支。属于上游不同实现，仍测关闭/高并发。 | A06 |
| Q0017、Q0021、Q0023、Q0028、Q0030 | WHPX 停表、pending interrupt、TSC deadline、暂停补偿及 load/reset 后状态；ARM 无 TSC/APIC 同名状态，已有 HVF timer/迁移状态代码。不能据此声称快照/暂停回归已通过。 | A02 |
| Q0018 | 非阻塞 connect 用 SO_ERROR 判断结果；共用代码保留。 | A06 |
| Q0020 | ICMP 无法创建时 UDP fallback 10 秒超时、最多 64 个；仅 fallback 时生效，Darwin 可走原生 ICMP socket，实际路线须记录。 | A06 |
| Q0022、Q0024 | WHPX 内核 LAPIC 支持及默认开启；ARM ranchu 当前仍为 QEMU GICv2，未增加 HVF GIC offload，不移用原 Windows 降退出数字。 | A02 |
| Q0025 | 出站连接短 keepalive 与失败复位；基础补丁的 Darwin idle 配置缺口已由 D0001 补齐。 | A06 |
| Q0026 | x86 核数由 6 上调；原实现不扩展 ARM，D0003 提供 ARM 原生上限。Apple 无 SMT，不套“逻辑核数一半”公式。 | A02 |
| Q0027、Q0054 | 精简工具栏，后续去 Overview；`ANDROID_EMU_FULL_TOOLBAR=1` 可回完整栏，共用 Qt 代码保留。 | A07 |
| Q0031、Q0034 | DPI 变化重建 toolbar backing store、窗口未显示前获取正确 screen DPR；共用 Qt 代码保留，Retina/跨屏未测。 | A07 |
| Q0032 | `ANDROID_EMU_VK_CACHED_COHERENT=1` 的可选 coherent 内存实验入口，默认不改行为。与 WC 默认替换不同，不当作 Apple 通用提速配置。 | A03 |
| Q0033 | 显式冷启的 toast 改为日志；共用行为保留，默认冷启仍有日志。 | A07、A11 |
| Q0035 | free-page reporting 协议保留，Windows discard 后端不在 Darwin 执行；当前 Darwin advice 尚未实现等效物理回收。 | **A04 待实现** |
| Q0036、Q0038、Q0039、Q0045、Q0049 | 自有 crash DB、最近报告本地保留、禁止上传、相关帮助一致；以最终禁止上传策略为准，共用源码保留。 | A11 |
| Q0037、Q0040、Q0041、Q0044 | ColorLogSink flush、单次 stdio 输出、Windows stderr 设置与后续注释；通用 flush/单次输出保留，Win32 CRT 修正不适用 Darwin。POSIX stderr 原生语义不同，不代表崩溃尾日志已实测。 | A11 |
| Q0042、Q0043 | CUDA 纹理注册/映射失败与销毁未交付帧归还；Apple 不走 CUDA，有上游 VideoToolbox/软件替代。 | A10 |
| Q0046、Q0047、Q0048 | 禁 usage 上传、核心自动更新检查及远端 feature-pattern 下载；共用源码保留，缓存/随包 feature patterns 仍可应用。 | A11 |
| Q0050、Q0051、Q0052 | VPx/H.264/HEVC queued frame 的纹理归还、按 frame 而非当前 decoder 模式判定、快照 replay 丢帧归还；通用路径保留，CUDA 触发案例不直接等于 Mac 覆盖。 | A10 |
| Q0053 | 稀疏盘创建与空间预检；Windows NTFS cache 特例不能当 APFS 提速依据，D0002 补实际卷能力判断。 | A05 |
| Q0055 | 通用 Qt PNG 图标保留，Windows PE `.ico` 资源部分不适用。macOS manager `.app` 未由本补丁提供 Finder/Dock 图标。 | A07 |
| Q0056、Q0057 | Win32 多 iovec bounce，最终仅合并到 64 KiB；Darwin `file-posix.c` 已有 preadv/pwritev，配置启用 `CONFIG_PREADV`，不支持时聚合至线性 buffer。相同优化目的已有上游路径，性能另测。 | A05 |
| Q0058、Q0059、Q0060 | IME v1 pipe、console 命令、窗口宿主组合输入；前两项共用，原 Q0060 的 Windows 限制由 D0005 扩至 Cocoa。 | A08 |
| Q0061 | 新建 ext4 inode 上限 1,048,576，降低大盘 inode table 开销；共用建盘代码保留，只影响新盘。 | A05 |
| Q0062 | execution-bridge writable-system marker 支持保留；原生 ARM guest 不需要该 x86 翻译安装流程，不是此移植缺陷。 | 范围已确定 |
| Q0063、Q0064 | 保留 userdata overlay 扩容、不缩小/不擦除；修正新盘 resize inode 布局。隔离 ARM64 mas_3 的 16→32 GiB 及随机哨兵保留已通过；不据此修复旧错误 inode 布局或宣称跨根迁移通过。 | A05 |

| gfxstream 编号 | 最终效果与 Darwin 状态 | 验收 |
| --- | --- | --- |
| G0001 | 丢弃 payload 已丢失的 raw descriptor-template 调用，避免 host 越界；共用保留。这是防崩溃，不会凭空恢复丢失的绘制更新。 | A03 |
| G0002 | ESSL310 compute 编译失败时可选 GLSL430 重试；代码保留，macOS 系统 GL/ANGLE 条件见上文，不能保证该 fallback 可用。 | A03 |
| G0003、G0004、G0005、G0006 | 早期无 GL 读回与降日志补丁经 G0005 撤回，再由 G0006 完整处理同步/异步、缓存帧与 BGRA/RGBA；只按最终 G0006 验收。 | A03、A09 |
| G0007 | 尊重 ColorBuffer `prefersDedicatedAllocation`；默认保留，可 `ANDROID_EMU_VK_IGNORE_PREFERRED_DEDICATED=1` 恢复旧行为，依宿主能力触发。Windows 黑帧测量不能移作 Mac 结果。 | A03 |
| G0008、G0009 | descriptor array offset 修复与限量命中日志；通用 Vulkan 路径保留。 | A03 |
| G0010 | host-visible 分配类型/大小限量诊断；保留，可用于确认 Apple 实际内存类型，不等于性能改善。 | A03 |
| G0011、G0012 | WC→cached 实验改为默认启用；条件性保留，Apple shared 类型通常不命中，见 GPU 表。 | A03 |
| G0013、G0014、G0015、G0016、G0017、G0018 | FrameBuffer 锁、YUV 编译失败重试、context bind 失败传播/恢复与 owner 诊断为共用修正；WGL retry/GetLastError 只在 Windows。G0014 的统一失败清理也改变 CGL/GLX 行为，Mac 尤须测失败后恢复，不能整组标作 Windows-only。 | A03、A10 |
| G0019 | 不支持扩展的 query-only pNext 结构解码，避免未知结构直接 abort；共用保留，不因此提供对应 Vulkan 扩展功能。 | A03 |
| G0020 | 核心 ReBAR flags 实验默认关、无硬件防护；Darwin Manager 明确强制 `0` 并通过继承环境回归。 | A03 / 核心能力门禁待补 |
| G0021 | `vkBindImageMemory` 失败细节诊断；通用保留，不修复驱动 OOM 本身。 | A03 |
| G0022 | ASTC→BC3 可选实验，默认关；不是 Apple 全显存 75% 节省保证，格式能力和实际仿真路径待测。 | A03 / 能力门禁待补 |
| G0023 | staging 初始 16 MiB，按需增长且至多 128 MiB；共用保留，超过原上限的 transfer 仍失败。分配容量降低可静态确认，驻留/进程节省须测。 | A03、A04 |

| Crashpad 编号 | 最终效果与 Darwin 状态 | 验收 |
| --- | --- | --- |
| C0001 | 恢复 Win32 `GetModuleVersionAndType()`，避免 Windows 报告收集时读未初始化版本信息；Darwin 使用 Crashpad Mach-O/Mach task 路径，无此 Win32 version API。原生 handler 与受控崩溃仍须验证。 | A11 |

## 9 个 Darwin QEMU 功能补丁与支持补丁

| 编号 | 生产改动 | 已有证据 | 整机缺口 |
| --- | --- | --- | --- |
| D0001 | Darwin `TCP_KEEPALIVE` idle + `TCP_KEEPINTVL` / `TCP_KEEPCNT` | 编译实际 helper，真实 socket 设置/读回。 | A06 的客体黑洞/断线行为。 |
| D0002 | `getattrlist` 卷能力判断，确认支持稀疏文件后采用稀疏 userdata 空间规则 | 真实 APFS 卷能力与临时稀疏文件检查。 | A05 新盘/扩容/磁盘峰值与实际错误处理。 |
| D0003 | ARM guest 核数限制为宿主/HVF/GICv2 允许值 | 使用真实 HVF CPU 上限接口与 helper 检查；4 vCPU 客体已开机。 | A02 其余核数、计时/开销与游戏负载。 |
| D0004 | Qt console `window show/hide`，完成后同步回应 | 接口 shim 与首轮真实 Qt 主窗显隐、boot ID 保持；副屏修复增量构建通过。 | A07 副屏修复重启复验、焦点及跨屏/缩放。 |
| D0005 | Cocoa native key / IME 进入原 v1 通道，跟踪按键身份避免文本与按键双发 | AppKit + Qt stub 编译、物理按键/路由检查；console 客体往返通过。 | 真实 Cocoa 会话被 Accessibility 权限拒绝阻断，仍未验收。 |
| D0006 | 修复 HVF ARM64 case30 错映射至 Q20，改为 Q30 | 全32项映射、原错误负控和真实HVF双向测试通过；完整QEMU单CPUworker10暂停/恢复+3snapshot循环、2,135,867,594轮无错。双引擎构建/签名，隔离QA包已采用。 | 单CPU数据保持不等于每vCPU/宿主睡眠/全部异常/游戏长稳；完整QEMU主动损坏Q30的负控仅证探针敏感性，不冒称旧QEMU binary负控。 |
| D0007 | H.264/HEVC helper 与 legacy H264 的 IDR `new[]` 改用 `unique_ptr<uint8_t[]>` | 生产双引擎构建/严格签名；历史 D7/D8 backend 的真实 VT helper、legacy TU normal/ASan 与原始缺陷负控按各自版本通过，dyld 身份及前后哈希已核对。 | A10 的 Android/GPU/异常流/长稳边界；legacy 单独 TU 测试不证明默认路径选择。 |
| D0008 | H264/HEVC/VPx dispatcher 释放、Context 先销毁 dispatcher 再释放共享页、HEVC 快照计数 | 实际 TU 的 ASan 计数测试覆盖 120 次 dispatcher 周期、80 次三 codec 组合快照往返及原始负控；生产双引擎构建/签名、fresh 包 Qt 开机与 D8 原生 VT/FrameBuffer 测试通过。 | 计数替身不代表真实 decoder/GPU/Android MediaContext 或快照集成；异常输入、并发销毁和长期运行仍待测。 |
| D0009 | SHM start/stop/析构幂等、单次引用所有权、同步 callback 注销和无 frame 时有界队列回收；保留 24 字节 ABI 与 stop 后旧映射 | 12 普通+12 ASan/UBSan 通过、6 原代码负控均失败；新 D9 `android-emu_unittests` 既有 Registry 6 项通过并核对链接身份。生产双引擎构建/签名、fresh 418 clones/508 payload 包和 Qt/headless 公开启停操作分别通过，均 synced/graceful 退出。 | 主机 renderer/clock 等使用 shims；非全 GPU sanitizer/TSan。live 未观察内部引用数或持久 reader，同映射存在不等于 active stream；Android noGL、像素相等及长期并发另验。 |

D 编号对应 [incoming/macos-qemu](../../incoming/macos-qemu/) 中的9个功能补丁；D0008同时依赖[AEMU声明补丁](../../incoming/macos-aemu/)。[Darwin gfxstream补丁](../../incoming/macos-gfxstream/)另记为0001 CGL校验及0002 noGL成功发布（G2），不与原G0002 compute混同。`macos-build/0001–0003`为SDK27、ARM64 linker/最低macOS11与安装签名支持。G2沿用D9 QEMU tree `3058e5f90f327708e08e4df6eff63b28362479b3`、AEMU tree `04a678d0a729c6f9870e5f2c8149256294489298`；gfx overlay tree为`d14277bc86e27f74baa1a1716b1ee29f71587895`，不是git HEAD tree。构建/签名见[新收据](../../.macos-work/qa/g0002-no-gl-build-20261101/installed.json)，原生/失败契约及早期失败见[最终索引](../../.macos-work/qa/gfxstream-d9-readback-candidate-20261101/verification-final.json)。旧D9构建/SHM与D8 VT专项仍按其原收据保留。

G2的[集中验证记录](qa/framebuffer-readback/G2-VALIDATION.md)汇总修复、原生QA与双引擎；[新包独立审核](../../.macos-work/qa/g2-package-independent-review-20261101/assessment.json)706项全过，覆盖508项payload、签名、补丁/源码/构建身份与两引擎原始收据；未扩大Android像素或净RAM结论。

## 记录验收结果的最低要求

每个 A 编号记录核心/管理器哈希、组件锁、macOS/SoC/GPU、后端与 feature/env、AVD 配置、操作步骤、预期/实测、日志路径与失败样本。性能对比使用同一主机、镜像、分辨率、内存、游戏场景与运行时长，报告冷/热状态和波动；不能把别的游戏或 Windows 数字当作本次通过。

A01 的核心直接启动已取得证据，接下来完成最终装配组合的 Manager 生命周期、副屏修复复验、A02–A13 的剩余验收，并补 A04 生产实现。只要物理回收未实现，或关键运行项目尚无证据，当前状态就应写为“移植进行中”，不写“所有功能与优点全部通过”。
