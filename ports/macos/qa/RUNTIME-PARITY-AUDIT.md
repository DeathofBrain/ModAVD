# macOS 运行时、随包工具与客体验收对齐审计

审计日期：2026-11-01（Asia/Shanghai）。基准为原 package/README.md、四个随包 PowerShell 脚本、固定 mas-36 / Emulator37.2.10，以及 Manager21c2a3ffa22eb37a5e39e61b1b53f2258d0f3e7a。00:47 的原始阶段仅对原游戏实例只读采集；后续启动、维护、更新、扩容和 Vulkan 验收全部使用隔离 QA 根。原游戏实例未被这些操作接管，未读取认证 token 值。

**基础原生运行与多项原版功能已有闭环，仍未达到所有功能和优化全部对齐。** 随包组件修复/诊断工具、活动日志保护、真实签名更新/中断恢复、16→32 GiB 保数据扩容、ARM64 idlepoll 及 Vulkan 自动修复已补齐并有分项证据。生产宿主回收的整体净收益仍未验收；Cocoa 真实输入被权限拒绝阻断；MAA增强协议、未同步写入下崩溃恢复、广泛网络/图形/视频边界与游戏长稳仍未验收。1955b54a 已完成完整 AVD 复制保留及 sync 后单次强杀恢复。新根的一次错误 AVD 复制触发数据复位，不算保数据迁移。

“已实测”只适用于所列条件；“未证明等价”表示存在跳过/替代路径而效果尚不明确；“实现缺口”表示缺少对应生产入口或后端；“未实测”不直接等同于功能失败。开发用 QA 工具本身不计作用户功能；新随包maintenance入口与真实signed updater则按各自证据计入。

## 版本与证据边界

| 对象 | 核对结果 |
| --- | --- |
| 原游戏运行包 | `.macos-work/package/ModAVD-validated`，0.36.0/mac-arm64，原有游戏验证实例保持原样；不能把后续隔离 QA 证据记到它的原 Manager 上。 |
| 原游戏包 Manager | patch acc3c046e9eecfdc85711c2bf4322358c91776d8025b91d654cebfb8a4240a09；未在本轮覆盖。 |
| 最新 Manager 构建 | Manager patch仍为1955b54a9c6916647daf6354fb28687e342cb10d10082f87e355827ea4afc91d。最新G2组合`.macos-work/package/ModAVD-gapfix-readback`通过418 APFS clones / 508 payload校验；3GiB/2CPU的mas_3经Qt/headless分别31.7949/13.2517秒开机、零warning，保留7项及SHM公开启停7项各自通过，synced/graceful退出。旧哨兵/32GiB/µLauncher/修复驱动保持；D9旧包和1955单次强杀结果按版本保留。 |
| 核心版本边界 | G2沿用D9 QEMU tree `3058e5f90f327708e08e4df6eff63b28362479b3`（3构建+9功能）与AEMU tree `04a678d0a729c6f9870e5f2c8149256294489298`；gfx新增Darwin0002成功发布补丁，overlay tree `d14277bc86e27f74baa1a1716b1ee29f71587895`。FrameBuffer/ColorBuffer一起重新编译链接，backend SHA `35fbd4366185ea5985e41e495c73c910030e7522075ef23de7c6317ea6f60920`，严格deep codesign通过，见`g0002-no-gl-build-20261101/installed.json`。这是新backend，不复用旧D8/D9 VT为fresh证据。private RAM实验未进入生产组合，原游戏包未替换。 |
| 原游戏客体 | mas_0 / QEMU54065 / console20000 / 私有ADB20050，为原始只读采样对象；后续 QA 使用 mas_3、其他私有服务及明确 PID/启动身份。 |
| 包完整性 | 原包489项immutable payload校验通过；旧集成QA为417 clones/507 payload，历史D9和最新G2各为418 clones/508 payload。verify中的runtimeValidation:pending是装配器固定字段，不替代外部实机证据。 |
| 旧独立 QA 实例 | `modavd_macos_qa_585a72ee` / console `20060`，曾使用 4 GiB、6 GiB 数据盘；另有更新核心的 2 GiB、请求 16 vCPU 测试。不能将其内存、磁盘或耗时与当前 Manager 的 3 GiB、32 GiB 实例混为一套配置。 |

核心所有原补丁的逐项宿主对齐由同目录 `CORE-PARITY-AUDIT.md` 及核心专项记录承担；本报告聚焦运行时、包与客体闭环，不以“补丁已回放”推定收益全部成立。

## 原随包功能与本轮工具的区别

基准文件为 [原随包说明](../../../package/README.md)、[组件补全脚本](../../../package/fetch-components.ps1)、[问题包脚本](../../../package/collect-info.ps1)、[配置报告脚本](../../../package/report-config.ps1) 和 [执行桥脚本](../../../package/modavd-bridge.ps1)。原游戏包的tools只有原三个程序；后续隔离QA包已加入fetch-components、collect-info、report-config与maintenance静态文件，全部纳入完整清单。

| 原用户功能 | macOS 实现与最新证据 | 对齐结论 |
| --- | --- | --- |
| 同意 Google 许可后补齐 adb/镜像，断点续传、校验、暂存安装和镜像回落 | 随包 tools/fetch-components / maintenance 实现锁定版本、许可全文/收据复用、Google中国→腾讯→Google、Range续传、双哈希、安全暂存、原清单权限核对及原子交换。实际QA包两组件status均verified，34项维护fixture通过。 | **实现已补齐。** 各镜像真实可达性、新用户完整许可/下载与弱网长时续传仍待独立验收。 |
| 缺组件时直接在本包补全 | maintenance接纳已有包，不需要开发runtime标记；包内QEMU/ADB仍运行时拒绝修复，不替用户关闭。只恢复锁定SDK字节，核对原PACKAGE-MANIFEST，不扫描重写清单来接受本地内容。 | **实现已补齐。** 缺失/损坏恢复、原子交换失败保留等有临时真实目录回归，现包完整组件的status已实测；全新下载场景待测。 |
| 一键问题 ZIP | tools/collect-info默认收集白名单宿主/实例参数、有限尾日志、近七日崩溃摘要及Crashpad仅stat清单；不读dmp。--live仅查询已存在且身份核对的私有ADB，截图需额外--screenshot。实际使用日志ZIP通过有限路径/邮箱/凭据检查。 | **已随包并有实际QA。** 不保证任意自由文本完全匿名；受控崩溃、真实截图/崩溃条目和全部隐私边界仍需针对性验收。 |
| 生成兼容性配置文字并复制到剪贴板 | tools/report-config输出脱敏规格/组件/实例，--live可读取已运行实例的第三方包版本，--clipboard显式调用pbcopy；原始只读ADB协议不启动服务，拒绝5037并复核PID/路径/启动身份。 | **已随包。** 真实QA的report与live查询有记录；剪贴板实际粘贴未验收。 |
| x86 客体 arm64 执行桥的装/卸/核对 | 当前目标改用原生 `arm64-v8a` API 34，客体 `ro.dalvik.vm.native.bridge=0`。无需将 ARM 指令再翻译到 x86。 | **目标不适用。** 这是明确的架构范围变化，不能把未移植 PowerShell 执行桥算作当前原生 ARM 目标失败。也不表示可运行任意 x86 Android 应用。 |
| 发布包排除 Google adb/镜像，由用户本机下载 | 当前完整本地目录复制本机已接受许可的SDK并保留收据；随包维护入口已能处理用户补件。 | **交付形态仍需区分。** 本地完整包已验证；无Google组件的对外主包及全新用户下载流程尚未形成发行验收。 |

新增的 `bootstrap.py`、`package.py`、`runtime.py`、`guest_checks.py`、两种帧导出器和合成测试，是本轮构建/验证设施；原版没有这些开发入口。原版已有的固定组件、私有 ADB、首次初始化和增强截图协议，才是此处需要核对的产品行为。

## 固定 Google 组件与实际首次初始化

| 项目 | 基准与结果 | 尚未覆盖的范围 |
| --- | --- | --- |
| adb | 固定37.0.1/Darwin，官方SHA1及本地归档SHA256已锁定；随包组件status重新验证全部安装字节和权限。 | 真实弱网/长时续传、镜像端点回落及全新用户缺件恢复待验收。 |
| 系统镜像 | 同为 Android 14 / API 34 / Google APIs / r14；由原 `x86_64` 换成目标所需 `arm64-v8a`。官方 ARM 归档 1,610,393,229 B、SHA-1 `2fe8b46d419a3400e30f31b0152b241b50c8b99f`；本地 archive SHA-256 `bd1c323cbdd3c9dd55dde54588b298d172fd126fe16e127a7bd7aeab6897890a`。 | 架构变化已真实开机；不能由同 API/revision 推定 ARM guest 图形库修复、所有系统 app 行为与 x86 相同。 |
| 许可 | 已有两份明确接受收据及完整条款；随包licenses/accept-license独立入口并校验条款hash，旧收据同hash复用。 | 不替新用户接受条款；全新用户明确阅读/接受的交互流程仍待实测。 |
| 可选 SDK 管理器 | 锁定 macOS ARM64 commandline-tools 23.0 / build 16111833；本轮未计入已安装组件。 | 原包并不要求它；不把未装可选工具当作核心缺陷。 |
| IME APK | 固定 `org.modavd.ime` v1.0.0，20,748 B，SHA-256 `912c9c9724e63fa606fa5d4e4212cef0e24148b013923ba49d1ebf8cfd38e0e2`。当前客体已安装且默认 IME 为 `org.modavd.ime/.HostImeService`。 | 真实 Cocoa 输入法会话仍待验收。 |
| µLauncher | 固定 0.2.12 / versionCode 57，4,099,556 B，SHA-256 `f67124dad5009016dd1fce379d79b20ea93a46cefb5759002f3fa149d50b018f`；静态检查为无原生库的 DEX，许可已随包。当前客体已安装，HOME resolve 为 `de.jrpie.android.launcher/.ui.HomeActivity`。 | 当前“已安装/已设 HOME”已闭合，不能继续沿用旧文档“待安装”；1955b54a 完整复制与受控强杀后的重启 HOME 保留通过；全部 onboarding/权限/异常回退仍待测。 |
| Google 服务 | 当前只读 `pm path` 确认 GMS 与 GSF 存在。`com.android.vending` 指向 `/product/app/LicenseChecker/LicenseChecker.apk`。 | 仅凭 `com.android.vending` 包名不能判定完整 Play 商店、支付或 Google 登录可用；Google APIs 镜像不等同于 Play Store 镜像。未做账号登录/支付测试。 |

`runtime/components.lock.json` SHA-256 为 `a6bc0f0ca807e92a4d975fed9a95892477541d7998a6c1d2a4d5a2313060b1b1`；IME/Launcher 锁文件 SHA-256 为 `1db93878658999054bf389d1c5c5aaac55c1377914a1016d34cce6395b733945`。许可下载、路径/归属、安全解压和合成 SHM 的测试，不代替真实用户初装全流程。

## ARM 优化与架构边界

| 原行为 / 源码位置 | Darwin ARM 当前做法 | 影响与结论 |
| --- | --- | --- |
| ensureVulkanFix / guest libvulkan_enc khrfix/bindlock2 | 不再跳过ARM。独立ARM64 profile仅接受4409384字节d6188011原库并生成73ddfe09，全部哈希/缓存/挂载/状态沿用选中profile。原库GPU负控6组KHR更新重复首值；候选12组buffer/image/texel读回、pNext、4线程8192bind通过。2182eac1 QA Manager首次上传挂载、客体重启复用cache及重复start不重启zygote均通过。 | **特定ARM64镜像修复已实测。** 应用/安装及上传后游戏出现门禁保留。1955b54a另校验mounted SHA，重启失败返回FAILED并安全回滚，仅在唯一mountinfo及inode未变时卸载；该版主机负例/构建、新根实机生成挂载与cache重挂通过；live restart失败注入因前置umount失败未到达，不记实机回滚通过。 |
| setupIdlepoll / mas-idlepoll.sh | 已恢复ARM64动态daemon：400 requests/s启动SCHED_IDLE loop，低于200/s持续1秒停loop，off清空。隔离ARM64/HVF off/on各3轮32MiB、4096×8KiB+fsync全部读回正确；中位23.0706→32.1132MiB/s，QEMU CPU1.63→2.10秒/32MiB。2个loop实际为SCHED_IDLE，空闲1.6秒后仅watcher留存，off后全退出。 | **本机窄负载正确性/收益实测。** 不借此宣称游戏CDN或任意磁盘吞吐提高；宿主CPU代价已一并记录。 |
| 宿主 above-normal priority | `tuneHostScheduling()` 在 ARM 直接采用 macOS 默认调度。 | **策略改变，未验证同等收益。** 没有本轮 macOS QoS/优先级替代与同机争用负载证据；不要求照搬 Win32 API，也不应宣传同等调度优化已实现。 |
| AMD X3D 大 L3 CCD 亲和性 | ARM 不做该绑核。 | **目标不适用。** 当前 Apple M4 无原 x86 AMD X3D CCD 拓扑；不能把“不适用 X3D”扩大为 Apple 性能核/能效核调度已经最优。 |
| ARM 转译层 `/proc/cpuinfo` bind | ARM 不执行 `cpuinfo-bind.sh`。 | **目标不适用。** 原脚本修 x86 ARM 翻译层的一核视图；当前原生 ARM 的 `/sys/devices/system/cpu/online=0-3`。这不影响仍需独立验证的游戏 CPU 拓扑/画质选择。 |
| free-page reporting → 宿主物理释放 | 协议、驱动、参数存在，但 Darwin 生产路径仍缺少已证明安全有效的物理回收后端。 | **生产实现缺口。** 本次新证据已确认 reporting feature 与 order，不能再说“完全未协商”；仍不能把它提升为 resident/physical memory 已还给宿主。独立 HVF backing 替换实验不是生产整机闭环。 |

## 本次只读客体实测

采样起点 `2026-10-31T16:47:42.969244+00:00`（北京时间 11-01 00:47:42），对象为上述 `mas_0` / PID 54065。通过包内 adb 指定 `127.0.0.1:20050` 与 `emulator-20000`，命令前后核对归属。只读取指定属性、文件系统/sysfs 信息、设置值与包路径；未执行 setup、输入、强制 repaint 或压力负载。[独立白名单证据与采样身份](../../../.macos-work/qa/runtime-parity-guest-20261101.json) 已保存，不含认证 token 或账号资料。

| 读取项 | 实际结果 | 可作出的结论 |
| --- | --- | --- |
| ABI / API / boot / CPU | `arm64-v8a` / `34` / `sys.boot_completed=1` / `cpu_online=0-3`；native bridge `0`。 | 当前 Manager 客体真实原生 ARM64、已开机、4 vCPU。另有更新核心请求 16 核被正确裁到 8 核并开机的独立实测，不应再笼统写“只有 4 核证据”。 |
| `/data` | `/dev/block/dm-39`，mount 明确为 ext4；33,109,872 KiB，使用率采样时 41%；4,096 B filesystem block，inode 总数 1,048,576。 | 32 GiB 配置的新盘已格式化/使用；新盘 inode 上限在真实客体得到支持。toybox `stat -f` 的 Type 文本为 ext3，但该系列共用 magic；不能据此误判挂载为 ext3。 |
| 逻辑/物理扇区 | `/sys/block/vda` 至 `vde` 的 `queue/logical_block_size`、`physical_block_size` **全部为 512 B**。 | 4 KiB filesystem block **不等于 4K-native sector**。现有实例不能算“4K 扇区优化已实测”；分区起始偏移/对齐尚未采集。原紧凑建盘/扩容补丁也不能替代此项。 |
| write cache | vda–vde 均 `write through`。 | 当前 guest 看见写穿模式；不证明强杀后应用文件或 qcow2 一定完整。 |
| dirty 回写 | root 只读核对 `dirty_expire_centisecs=200`，`dirty_writeback_centisecs=100`。 | Manager 的该项原调优在当前客体已设置。最初非 root 读取为 Permission denied，已用只读 root 命令澄清，未写参数。 |
| balloon/reporting | `virtio23` 绑定 `virtio_balloon`、device `0x0005`；features 如下，`page_reporting_order=9`。 | sysfs features 按 bit 0 起列出，第 5 位为 1；pinned `include/standard-headers/linux/virtio_balloon.h` 定义 `VIRTIO_BALLOON_F_REPORTING=5`，故已具备协商证据。**物理回收效果仍未成立。** |
| 其他持久设置 | `hide_error_dialogs=1`；`network_watchlist_enabled` 读回为空，非明确 `0`。 | 前者已验证；后者不能记作关闭成功，需要另核对设置写入及 API 34 服务实际行为。本次未修改。 |
| IME / HOME / 游戏安装 | IME 为默认并出现在列表中；µLauncher 为 HOME；B 服游戏 APK 安装路径存在。 | 真实安装/默认选择已确认；不是 IME Cocoa 会话或游戏战斗验收。 |

不含 token 的 reporting 原始白名单输出：

```text
page_reporting_order=9
/sys/bus/virtio/devices/virtio23 driver=../../../../../../bus/virtio/drivers/virtio_balloon
device=0x0005
features=0100010000000000000000000000110010000000000000000000000000000000
```

旧 `GUEST-CHECKS.md` 所引报告的 balloon `device/features: Permission denied` 是当时权限范围下的事实；它不再代表当前 root 只读检查的全部证据。旧报告仍可保留，不应篡改成它当时读取成功。

## SHM、IME 与游戏：已闭合部分和剩余边界

| 项目 | 最新实际证据 | 仍缺少的验收 |
| --- | --- | --- |
| SHM 生产端静态/动态帧 | [动态采样](../../../.macos-work/qa/shm-dynamic.json) 为 `videmulator20060`，10 次采样中帧号 509→528，时间戳与内容指纹发生变化，1280×720、header fps=60、24 字节头、BGRA；fps字段不是实测帧率，不应继续照搬旧文档“仅静态帧、动态未测”。 | GL/Vulkan 各读回路径的故障/恢复、旋转/尺寸变化、所有颜色/alpha、并发消费者、长时间持续读。双读头部一致不证明整帧无撕裂；原协议没有序列锁。 |
| Manager 实例真实 SHM | [003 截图证据](../../../.macos-work/qa/manager-game-frame-003/evidence.json) 已核对 `mas_0`、PID/启动时间、AVD、私有服务与包；只借用 `videmulator20000`，帧号 59959，1280×720，PNG/hash 正确。仅保存 runtime 白名单，未导出 `grpc.token`。 | 这是模拟器产物/开发消费者验证，不是 MAA 已连接增强截图的证明。`--reuse-stream` 不 start/stop 借用流。 |
| D8 media 包 SHM 矩阵 | `manager-shm-media-20261101/report.json` 明确 `ok=false`。四个旋转阶段均有 3 个 reader，各 12 次有效映射读取且相互同帧；QA 设置已精确恢复，旧哨兵与 HOME 保留。 | 精确 ADB 像素/旋转/帧推进矩阵未通过。SHM 固定 natural 1280×720，不随 ADB 横竖尺寸交换；静态画面不推进，RGB 多见 ±1、alpha 相同，像素差异尚未归因。既不记为通过，也不直接判定生产缺陷；无序列锁仍不能保证无撕裂。 |
| D8 SHM 契约重测 | `manager-shm-media-contract-20261101/report.json` 的 `ok=true` 表示采集完成、固定 natural 尺寸/BGRA 元数据契约通过、保留与设置恢复；保留原首轮 false。源码核对确认只在 posted-frame callback 更新，fps 为建议值，不能要求静态图固定推进。 | `pixelParityEstablished=false`，未使用像素容差。ADB 与 SHM 为独立捕获，未配对同次 composition；所有可比较旋转只记录观察，不选一个假设为正确。契约通过不等于精确像素相等或无撕裂。 |
| D9 SHM 主机生命周期 | `d0009-shm-lifecycle-v4-20261101/report.json` 为实际 Registry/MessageChannel/POSIX SharedMemory/VideoFrameSharer TU 及精确生产 start/stop/GPU 函数；12 普通+12 ASan/UBSan 通过，6 原代码负控全部失败。既有 `CallbackRegistryTest` 6 项在 D9 新链接的 `android-emu_unittests` 全通过，archive/object/符号身份核对。 | renderer/clock/readpixels 等为明确 shims，不是完整 GPU/Android/TSan；macOS LSan 不支持而关闭。24B ABI 和 stop 后旧映射保留，存在映射不表示正在出帧。 |
| D9 两引擎实机公开启停 | `manager-shm-d9-{qt,headless}-20261101/report.json` 使用隔离 PID80181/PID86230 / 私有 ADB20670；两引擎各完成80次重复start、80次重复stop、停止后guest画面变化但完整header/payload不变、恢复新帧及40次启停，七项均true，各自boot UUID保持且HOME/出帧恢复，随后synced/graceful退出。runner SHA `15407293…` 与独立离线评审一致。 | 只证明 console 公开操作与短窗口观察，`internalRefcountObserved=false`；每次重开映射不能证明持久 reader 或同一个 POSIX 对象。`shm_ready` 只表示映射存在。像素相等、MAA、Android noGL 与长期并发另验。8 项 mock 是 harness 验证，不能替代这里的实机证据。 |
| D8 原生 FrameBuffer GPU 读回 | 实际生产 backend normal/ASan 各运行 GL 与 Vulkan-only，每模式 16 帧、64×48 RGBA/BGRA、变化 alpha/行序，源 GPU 读回与最新 `getPixels` 字节精确。noGL 另比较直接回调，GL 允许 prior/null 回调；每轮分别 589,824 / 393,216 字节。实际 dyld backend/loader/MoltenVK 路径和前后哈希均确认。 | **原生 FrameBuffer 窄范围通过。** ASan 仅仪表新驱动，使用继承 SDK 对应 Xcode compiler/linker；生产库/Apple framework 不改。该结果不覆盖 Android SHM、尺寸/旋转、其他格式、并发销毁或游戏。见[工具](framebuffer-readback/README.md)。 |
| G2 原生读回与失败发布 | 新backend与sealed D9各跑normal/driver-ASan，4个wrapper×GL/noGL全部通过，每模式24帧、六种源/目标组合，共192矩阵帧。noGL另测R8/宽/高不匹配后的cache保留/恢复，GetPixels 0/17/oversized与halo，G2初始零cache/无callback；CPU8组baseline按预期失败、candidate全过。 | GL RGBX保留实际GPU X，noGL归一255，均逐字节精确；v1编译错、v2/v3错误统一alpha断言失败均保留。没有真实GPU故障注入、任意并发销毁或完整生产库ASan。见[可复用工具](framebuffer-readback/README.md)与[最终身份索引](../../../.macos-work/qa/gfxstream-d9-readback-candidate-20261101/verification-final.json)。 |
| G2 Qt/headless 实机 | 两引擎各有retention7及SHM lifecycle7项通过，启动31.7949/13.2517秒、关闭3.6485/3.3834秒，零warning且synced/graceful；进程与console最终消失另有post-stop收据。 | 原生noGL不是Android guestVulkanOnly；未证明精确ADB像素/无撕裂、持久reader或内部refcount。Qt首轮非root读sysfs被拒的异常仅有后补转录，改用su0读取后v2通过，未改生产代码。 |
| MAA 实际接入 | [00:16 首测](../../../.macos-work/qa/maa-current-screenshot-check-20261101.json) 明确 `RawByNc`、577 ms；[00:28:51 重测](../../../.macos-work/qa/maa-current-screenshot-recheck-20261101.json) 为新 core 的 MaaFwAdbController，UI 1280×720/1055 ms，仍等待核心确认，设备方式“未确认”。当时 worker 82134 的 lsof 无目标 SHM，vmmap 仅合计 48 KiB 共享内存，没有约 3.7 MiB 截图映射。 | **未确认 AVD SHM 增强接入。** 这些是带时间边界的结果；不能推定用户随后重启/换 core 的新连接仍为何种方式。此次审计没有继续截图、追踪新 worker 或改用户配置。单次耗时也不能作协议性能对比。 |
| console IME 往返 | 真实编辑器5步console→guest往返通过，Manager安装/默认IME确认。Cocoa preflight列出中文/日文来源，但accessibilityTrusted=false。 | **真实Cocoa输入被权限拒绝阻断。** 尚无系统候选/preedit/提交、修饰键、焦点、repeat或非文本无双发闭环；console和stub不能代替。 |
| B 服 APK 静态与安装 | 本地 1,886,746,222 B，SHA-256 `9106db6d9038d11a732cdc35773e68d79895834283718895c6f929d496577df9`；manifest `com.hypergryph.arknights.bilibili` / 2.7.81 / code 200；ZIP CRC 与 ARM ELF 检查通过。`arknights-install.json` 明确 `Success`，当前只读 pm path 仍存在。 | 官方 API 未提供整包 SHA-256；静态检查没有完成 APK 密码学签名验证。不能把文件名/大小吻合写成官方签名认证。 |
| 游戏实际显示 | 初次启动/协议页已有记录；003 PNG 显示 Ver2.7.81 资源解包 52%（6279/12091 MB）。故旧游戏 README 中“尚未实际安装或运行”已落后于最新证据。 | 没有据此完成资源结束、登录、战斗、音视频、长时间稳定、恢复/重启或性能 A/B。本次未查看账号或改变当前画面，不能推定用户后续操作状态。 |
| GLES / Vulkan | 原枚举为Apple M4/GLES3.0与Vulkan1.3；隔离ARM64 encoder原库/修复GPU对照及Manager自动cache闭环见上表。 | 不扩大成全部Metal/GLES compute、格式/扩展、软件回退、VideoToolbox生命周期或游戏长稳通过。 |
| VideoToolbox CPU 解码与清理 | D8 backend 的 fresh helper normal/ASan 每轮 H.264/HEVC 各 28 硬件会话、448 帧、5,898,240 字节精确比较通过，覆盖复用、pending-output、仅析构清理与输入页撤权；实际 dyld 与源码/库前后哈希均确认。D7 `1d1257c8…` 的完整 legacy TU normal/ASan 各 30 会话、180 帧、170 次输出比较和 Google baseline allocation-mismatch 负控保留历史证据，未对 D8 重跑 legacy。 | **D8 helper 窄范围原生实测通过。** ASan 仅覆盖新 harness，生产库/Apple framework 仪表不改；legacy 不由默认 factory 选中。Android service/MediaContext、GPU texture、VP8/VP9、异常/大尺寸/重排流、fallback、游戏/长稳或 Apple 内部分配泄漏仍独立待测。 |
| D8 dispatcher / Context 生命周期 | 实际 dispatcher 源码 120 次周期后 VPx/H264/HEVC 全部 live=0；完整实际 Context TU 的 ASan 计数测试 3,424 checks、80 次快照往返，覆盖 8 种 codec 存在组合、先销毁再释放共享页和 HEVC 计数；原始析构/load/count 负控分别按预期失败。 | **所有权与控制流测试通过。** codec/factory/allocator/Stream 为计数替身，不能当作真实 decoder、GPU 或 Android MediaContext/快照集成证据；历史畸形 HEVC snapshot 不自动修复。见[工具](media-lifecycle/README.md)。 |
| 网络 | 隔离 ARM64 客体 loopback 八项实测通过：160 条同时活跃 TCP、单连接 32 MiB、8×4 MiB、half-close、拒绝 errno111、RST errno104、160 UDP socket、失败后恢复。全字节检查零错误、最终 active=0、boot ID 保持、临时文件清理。原 pre-ASG 运行及 v4 启用事务运行保留；v5 重跑与 memory probe 的 host 窗口有 4.274183875 秒重叠。 | 重叠只针对整段 host 运行，不证明每个网络 case 都与回收同时发生；v4 先前 overlap=0 原样保留。无相对上游提速/CDN 或生产物理收益结论；dead-peer keepalive、外部黑洞、OOB、ICMP fallback 并发和长稳仍待测。 |

## 磁盘、生命周期与优化效果不能从“能开机”推定

| 检查项 | 已有证据 | 判定 |
| --- | --- | --- |
| 新建、初始化与一次后续启动 | `manager-create-0-3G.json` 已为 initialized=true、µLauncher；`manager-start-0.json` 记录实际 launched=true、booted=true、boot 32.76 s、host_ime=true。当前相同进程仍可只读查询。 | **已部分实测。** 首个创建和后续启动已有证据，不能继续统称生命周期完全未测。最终版本的全启停删除、多实例/端口/锁隔离、恢复仍不能由单实例推出。 |
| 超额核数裁剪 | `hvf-core-limit-20261031T153905Z.json` 使用更新后包核心，请求 16 核，实际 0–7、日志明确上限 8、开机成功；sync+emu kill 正常退出、配置哈希恢复、默认 5037 listener 未变。 | **8 核上限实测通过。** 仍无全核数组合、睡眠/唤醒、暂停/恢复和持续游戏开销矩阵；WHPX exit 减少率不适用于 HVF。 |
| 窗口显隐 | 旧 QA 的 show→hide→show 记录确认窗口列表变化、boot ID 不变；更新后 D0004 核心重新开机、Manager 当前带窗口运行。 | 旧主窗测试有效；更新后的全部副屏、Retina、跨屏和焦点路径没有新的显隐序列，不把旧 hash 证据扩大到修复后全部边界。 |
| 稀疏/紧凑建盘 | 406 APFS clone / 0 普通复制证明装配采用 clone；原 APFS helper、Q0061 inode 限制代码存在，当前真实 /data inode=1,048,576。 | **部分实测。** APFS clone 是包复制方法，不是 guest 磁盘随机 I/O 性能证明；新盘实际物理占用、峰值与不同写入大小收益未测。 |
| 保数据扩容 | gapfix-disk-before/after-20261101.json记录隔离mas_3从16→32GiB，块设备17179869184→34359738368字节、ext4 block count4194304→8388608；重启后4MiB随机哨兵87b20f4f…SHA256不变。 | **该受控扩容闭环通过。** 新建32GiB与旧32GiB预置哨兵不算扩容证据；另一次跨根复制误删version_num.cache触发复位，不算迁移保数据，见下文。 |
| 4K 扇区/对齐与 I/O 优化 | 当前真实扇区 512 B，FS block 4096 B；POSIX preadv/pwritev 与 write-through 共用代码存在。 | **4K-native 不成立，分区对齐/I/O 收益未测。** 不将 4 KiB 文件系统块、64 KiB qcow2 cluster 或 host 16 KiB page 混称为 4K sector。 |
| 强杀后数据/镜像可用 | write-through 与 200/100 回写参数已读取，正常 sync/退出路径有独立 QA 证据。 | **已同步的单次受控强杀恢复通过。** 1955b54a guest sync 后 SIGKILL，停止后对 userdata/encryptionkey/cache 三种 qcow2 离线 check 无错，重启后旧哨兵/32 GiB/HOME/驱动保留且无 warning。未同步应用写入及任意崩溃仍待测；未在运行盘上离线 check。 |
| 宿主内存回收 | v7原生组件、实际guest live/读零/重用/网络/SIMD窄范围正确性分别通过；后续4次驻留对象释放精确free+16/spec0，3个去重256KiB范围含同offset两代。271次fault全rollback、9次resident保持；131个4MiB SG尾部1834对象/458.5MiB全cold，独审67/159项分别通过。 | **生产整体净收益仍未验收、默认off。** 正向cold窗口free/spec联合非exact10/262，严格全guest仍false；累计1MiB不是当前净省。2/32逐SG重置，不是BH/pause cap；14.068/15.144ms仅观察。256KiB构造region55→10297→55、metadata净成本未测。Manager写读9；v6裸客体实读10，v7仅4MiB SG相容、无逐launch新sysfs读测；两阶公平比较未运行。见[最新边界](hvf-object-reclaim/v7-android-progress.md)。 |
| 无自动上传与本地诊断 | 禁metrics/crash upload/核心update/feature下载行为保留；随包collect/report、实际有限隐私ZIP与活动日志清理保护已有证据。 | 受控崩溃、Crashpad报告保留/尾日志及实际无自动请求观察仍待测。诊断工具只收集白名单，不自动上传。 |
| 发行签名、公证、更新 | 本地ad-hoc严格签名；schema1签名更新清单支持受限相对链接、ARM64 Mach-O和签名xattrs。本地prepare --package/--sha256、真实完整核心ZIP安装及离线中断恢复已在隔离根通过，8类SDK/用户哨兵及metadata不变。 | **本地完整包更新/恢复已实测。** 远端mac-arm64 release尚不能由本地成功推定，Developer ID/公证不在个人本地目标内；recover不是长期任意回滚。 |

## AVD 克隆复位与正确保数据边界

ModAVD-gapfix-native→ModAVD-gapfix-integrated复制错误删除version_num.cache，目标配置仍32G但实际/data约16G，旧哨兵不见且IME重新安装。该次不是迁移保数据成功，不能覆盖旧源。先前16→32GiB扩容证据独立有效。

源码external/qemu/android-qemu2-glue/drive-share.cpp:106–126在version_num.cache缺失、不可读、解析失败或sysimg版本变化时置reset_version_number_cache；:183–198因此调用bdrv_img_create重建userdata/encrypt/cache等qcow2。qemu-version.txt缺失在main-common.c:1867仅重写版本，不直接wipe；read-snapshot.txt在drive-share.cpp:510/555参与read-only snapshot判断。不要根据文件后缀或“可生成”推断可无损删除。

正确复制须先正常停止，保留完整descriptor与整个.avd（base/overlay/encryption/snapshots/全部兼容标记），只重写descriptor指向新根的path；逐一核对backing与哈希，启动后验证旧哨兵/容量/应用状态及重启保持。当前源的三组qcow2 backing均为同目录相对路径，无需重写qcow2。源码审计见[AVD克隆定位](../../../.macos-work/qa/avd-clone-reset-source-audit-20261101.md)。后续 1955b54a 新根已按此完整复制并启动，旧哨兵 `87b20f4f…`、实际 32 GiB 和 µLauncher HOME 均保持；又经 guest sync 后单次 SIGKILL、停止状态下三种 qcow2 check 和新 QEMU 重启复核，哨兵/容量/HOME/驱动均保持。证据为 `.macos-work/qa/gapfix-1955-{retention,controlled-kill,after-kill-verified}-20261101.json`；此成功与早先错误副本分开。

## 本轮应保留的具体缺口

1. 完成 Darwin 生产物理内存回收与整机释放/重用/并发/失败回滚；private 候选及原生实验单独记录，不写成当前生产 backend 通过。
2. 1955b54a 最终组合、完整 stopped AVD 复制和 sync 后受控强杀已通过窄范围实机检查；live restart失败回滚注入尚未到达，全部 GUI/双实例/Keychain 交互仍待验收。2182eac1 与错误复制复位分别记账。
3. 随包维护已实现，仍需全新许可/组件下载、真实镜像回落、长时续传、剪贴板和受控崩溃实测。
4. MAA实际增强协议尚未确认；Cocoa真实输入被Accessibility权限拒绝阻断。G2新backend已独立通过原生FrameBuffer GL/noGL与失败发布契约，Qt/headless各有SHM公开启停证据；旧D8/D9 VT仅归属于旧backend。D8固定尺寸元数据契约通过，Android SHM/ADB精确像素相等仍未成立。Android noGL、未同步写入/任意崩溃、外部网络、Android视频/异常流和游戏战斗/长稳仍待测。
5. idlepoll本地小写收益不扩大到游戏CDN；默认宿主调度与所有其它性能优点仍需针对本机争用/负载A/B。

最新G2的[集中验证记录](framebuffer-readback/G2-VALIDATION.md)及[新包独立审核](../../../.macos-work/qa/g2-package-independent-review-20261101/assessment.json)记录706项通过，包括508项payload、签名/源码身份与两引擎raw收据；恢复PNG内部一致不证明ADB像素相等。

## 证据索引与验证范围

| 证据 | 说明 |
| --- | --- |
| [原 README](../../../README.md)、[原随包说明](../../../package/README.md) | 原产品用户功能、隐私、组件分发、磁盘/内存优点基准。 |
| [本地包验证](../../../.macos-work/qa/parity-package-verify-20261101.log) / `PACKAGE-MANIFEST.json` | 当前 immutable payload 与产物版本；不读取 runtime 凭据文件的值。 |
| [更新核心记录](../../../.macos-work/qa/core-window-update.json) / [真实 8 核上限](../../../.macos-work/runtime/logs/hvf-core-limit-20261031T153905Z.json) | 更新核心 hash 和真实启动/正常退出；替换早期“裁核未实测”的过时结论。 |
| [动态 SHM](../../../.macos-work/qa/shm-dynamic.json) | SHA-256 `8fdcbbdd6e01cb544ca97e42ea415b00e2a485b4f5843d54ea69aa19895909b3`。 |
| [console IME](../../../.macos-work/qa/ime-console-roundtrip.json) | SHA-256 `0341427f56308006b09d526326f20731b73e44ff435d3cbff6354481889a2baa`。 |
| [游戏 SHM 003](../../../.macos-work/qa/manager-game-frame-003/evidence.json) | SHA-256 `57703f2dfc7703e6709980ff1ca0c1b49f54a5bffbcff83e1f4647b34645ca18`；PNG 为资源解包画面。 |
| [MAA 重测](../../../.macos-work/qa/maa-current-screenshot-recheck-20261101.json) | SHA-256 `4daa32d2d7d2d7f9690c94a7aa3e078bd38df37947188f5270adac5609119552`，只描述当时连接，不描述后续新 worker。 |
| [原始客体报告说明](GUEST-CHECKS.md) | 旧权限范围、网络 payload/GPU 枚举；与本报告的新 root 只读证据分别保留。 |
| [本次只读客体证据](../../../.macos-work/qa/runtime-parity-guest-20261101.json) | 00:47:42 的采样身份、reporting、inode、扇区、dirty、watchlist 等白名单结果；包含后续只读 root 澄清。 |
| [维护工具说明](../maintenance/README.md) / gapfix-maintenance-status/collect-20261101.json / gapfix-native-used-info-privacy-20261101.json | 随包维护契约、真实组件校验及有限隐私ZIP；不覆盖任意自由文本的全部隐私。 |
| [Manager当前验证](../manager/VALIDATION.md) / manager-arm64-final-validation-20261101.json | 1955b54a主机测试/严格构建/新根保留与受控强杀；2182eac1的GPU实机细项独立记账。 |
| gapfix-disk-before/after-20261101.json / gapfix-manager-set32-20261101.json | 16→32GiB、实际块设备/ext4增长与4MiB哨兵保留；与失败的跨根复制分开。 |
| gapfix-smallio-ab-20261101.json / gapfix-idlepoll-lifecycle-20261101.json | 小写/fsync A/B及实际SCHED_IDLE loop生命周期，不是CDN证据。 |
| [网络客体矩阵](../../../.macos-work/qa/network-matrix-guest-20261101.json) / [产物身份侧车](../../../.macos-work/qa/network-matrix-guest-provenance-20261101.json) / [网络检查](NETWORK-CHECKS.md) | 160 活跃 TCP、32 MiB 单连接、8×4 MiB、half-close/拒绝/RST、160 UDP、恢复均通过；PID58200、同 boot ID、pre-ASG media 候选、RECLAIM 未启用。非 CDN/相对提速/物理回收证据。 |
| [生产 VT 汇总](../../../.macos-work/qa/native-vt-production-suite-20261101.json) / [生产构建收据](../../../.macos-work/qa/core-videotoolbox-update-20261101.json) / [仓库化验证工具](native-vt/README.md) | 新 fixtures、生产 main helper normal/ASan、完整 legacy TU normal/ASan 和 Google-baseline 预期失败负控。各报告存于 `native-vt-production-*-20261101/report.json`；记录精确 backend SHA、源码哈希、dyld 实际路径与隔离的启动环境，缺失/错误加载身份不算通过。 |
| [G2 构建/签名](../../../.macos-work/qa/g0002-no-gl-build-20261101/installed.json) / [失败契约与原生最终索引](../../../.macos-work/qa/gfxstream-d9-readback-candidate-20261101/verification-final.json) | 新backend `35fbd436…`；CPU8组、原生4wrapper/8mode全通过，保留v1–v3 QA失败。工具导出与已测源码逐字节一致。 |
| [G2 Qt保留](../../../.macos-work/qa/g0002-no-gl-build-20261101/qt-retention-v2.json) / [headless保留](../../../.macos-work/qa/g0002-no-gl-build-20261101/headless-retention.json) / [最终退出](../../../.macos-work/qa/g0002-no-gl-build-20261101/post-stop-processes.json) | 各7项通过；对应目录qt-shm-lifecycle/report.json、headless-shm-lifecycle/report.json各7项及HOME/stream恢复通过，qt-stop/headless-stop均正常同步。即时stop回执中的短时pid_after仍保留。 |
| [D8 原生 GPU/VT 汇总](../../../.macos-work/qa/d8-native-gpu-video-suite-20261101.json) / [D8 构建收据](../../../.macos-work/qa/core-video-lifecycle-update-20261101.json) | D8 `67812b82…` fresh FrameBuffer normal/ASan、GL/noGL 及 VT helper normal/ASan 均通过；生产库和 Apple framework 未重新仪表。D7 legacy 历史证据不扩大到 D8。 |
| [D8 生命周期测试](../../../.macos-work/qa/media-lifecycle-d0008-suite-20261101.json) / [工具](media-lifecycle/README.md) | 实际 dispatcher/Context TU、计数替身和原始负控；不是实际 Android decoder/GPU 测试。 |
| gapfix-media-assemble/verify-20261101.log / gapfix-media-start/retention-stop-20261101.json | D8 fresh 组合完整性、32.87 秒 Qt 开机、旧哨兵/32 GiB/HOME/驱动保留与 sync 后正常退出。 |
| [D8 SHM 首轮](../../../.macos-work/qa/manager-shm-media-20261101/report.json) | ok=false；多 reader 有效读取、静态帧推进和精确像素/旋转断言分别记账，QA 设置恢复。 |
| [D8 SHM 契约重测](../../../.macos-work/qa/manager-shm-media-contract-20261101/report.json) | 固定尺寸/BGRA元数据、保留与恢复通过；pixelParityEstablished=false，独立捕获未配对，不使用容差或改写首轮。 |
| [D9 构建收据](../../../.macos-work/qa/core-shm-lifecycle-update-20261101.json) / gapfix-shm-assemble/verify/start-after-build/headless-start-20261101.json | 3构建+9功能overlay、双引擎严格签名、D8相同backend；418clone/508payload与Qt/headless34.8589/12.2905秒，首次内存预检拒绝记录保留。 |
| [D9 主机生命周期](../../../.macos-work/qa/d0009-shm-lifecycle-v4-20261101/report.json) / [既有 Registry 回归](../../../.macos-work/qa/d0009-upstream-registry-20261101/assessment.json) | 12普通+12ASan/UBSan、6原代码负控及D9新链接既有6项通过；含明确shims，不是整机sanitizer。 |
| [D9 Qt 生命周期](../../../.macos-work/qa/manager-shm-d9-qt-20261101/report.json) / [headless生命周期](../../../.macos-work/qa/manager-shm-d9-headless-20261101/report.json) / [runner离线评审](../../../.macos-work/qa/d0009-live-lifecycle-final-review-20261101/assessment.json) | 两引擎各自公开重复启停、stop冻结/restart新帧/同boot/恢复通过；gapfix-shm-{qt,headless}-stop记录正常同步退出。离线8项mock仅验证harness，内部引用数、持久reader、像素相等/无撕裂未证。 |
| [v7 原生机制](../../../.macos-work/qa/exact-v7-native-20261101/assessment.json) / [构造成本](../../../.macos-work/qa/dcr-construction-cost-20261101/assessment.json) | 256KiB精确对象/收据与两代/故障组件gate；严格全局counter仍false，单次untouched构造成本独立记录。原生组件不证明full caller预算或Android驻留物理收益。 |
| [v7 客体冻结记录](../../../incoming/macos-qemu/experimental/evidence/20261101-v7-reclaim-guest/capture.json) / [汇总](../../../incoming/macos-qemu/experimental/evidence/20261101-v7-reclaim-guest/summary.json) / [SIMD与快照](../../../incoming/macos-qemu/experimental/evidence/20261101-v7-simd-snapshot/capture.json) | PID91872正常停止；256/512MiB各3轮×8秒为148/149次live检查，追加512MiB×5轮×15秒为419次，live/读零/重用零错并清理。网络8case通过，与追加probe的host窗口重叠9.232410秒，非每case同时回收。同进程/同boot单CPU SIMD worker 10次启停、3次snapshot、26个live边界、2,164,996,438检查零错并清理；负例仅probe敏感性。该早期运行262提交/262distinct offset全旧ref1/PRESENT0，cleanup/query error0，当次无驻留或同offset第二代；后续PID2276等证据另列。161SG=131个attempt2/inspect2/remaining3.5MiB+30个attempt0/inspect16；inspect32未触发，cold tail未查询，不能判hot starvation。单SG最大report_work_us1027，整次pause未测；后续另launch遥测独立。 |
| [v7 后续驻留/故障/尾部边界](hvf-object-reclaim/v7-android-progress.md) / [67项独审](../../../.macos-work/qa/v7-timing-positive-independent-review-20261101/assessment.json) / [159项独审](../../../.macos-work/qa/v7-cold-tail-independent-review-20261101/assessment.json) | 4resident commit、3去重范围/同offset两代；271fault全rollback/9resident保持；131个4MiB SG尾部1834对象458.5MiB全cold。严格全guest物理仍false，非整体净RAM收益；perSG预算、region构造峰和minimum reporting阶数边界保留。[阶数源码](../../../incoming/macos-qemu/experimental/REPORTING-ORDER-ASSESSMENT.md)与进行中的冻结binary A9/B6对照均无比较结论；首次参数写入失败已正常停机、未进测量，retry1进行。 |
| [HVF 对象实验](hvf-object-reclaim/README.md) / [离线严格复核](../../../.macos-work/qa/hvf-object-reclaim-export-20261101/assessment-v2.json) | 同 raw 数据的 4,624 项映射/对象身份校验通过，物理 exact=false；未重跑 native，原始报告不改。private native 可行性不代表生产默认启用。 |
| guest-vulkan-original/ 与 guest-vulkan-candidate-v2/ | 同镜像encoder GPU负控与修复；旧exec-out客户端退出码限制见Manager验证。 |
| gapfix-integrated-start/adopt-verified/boot-cache-20261101.json / guest-vulkan-auto-manager/ | 2182eac1集成QA自动生成/挂载、GPU12组、缓存重启、无重复zygote重启。 |
| gapfix-1955-retention/controlled-kill/after-kill-verified-20261101.json | 1955b54a 新根完整复制、sync 后单次 SIGKILL、停止后离线 qcow2 check 及重启旧哨兵/32 GiB/HOME/驱动保留；不保证未同步写入。 |
| gapfix-1955-vulkan-restart-rollback-preflight-20261101.json / gapfix-1955-update-prepare-20261101.json | restart失败实机注入未到达；同0.36.0升级被正确拒绝，未install，不称最新升级通过。 |
| gapfix-cocoa-preflight-20261101.json | accessibilityTrusted=false，真实Cocoa输入受系统权限阻断。 |
| [本次定向合成测试日志](../../../.macos-work/qa/runtime-parity-tests-20261101.log) | 65 项，3.851 s，退出码 0 / OK；从已完成工具结果落地，未为写日志重复运行测试。 |

本次执行 `python3 -m unittest ports.macos.qa.test_runtime ports.macos.qa.test_package ports.macos.qa.test_capture_manager ports.macos.qa.test_capture_frame ports.macos.qa.test_guest_checks -q`，**65 项全部通过，3.851 s，退出码 0**，结果见上表日志。这些测试只使用临时 fixture/自建 SHM，不连接现有 guest，不证明表内未测的整机效果。此前 `port-tests-final.log` 的 73 项通过同样只作开发验证，不能代替用户功能和优化测量。
