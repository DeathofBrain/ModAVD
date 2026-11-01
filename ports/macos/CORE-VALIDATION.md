# 核心真实构建与入口验证

日期：2026-11-01。本机 Apple M4 / ARM64，macOS 26.6.2，16 GiB 内存；Xcode 27.1 / 27A9275，SDK 27.0。只通过子进程 `DEVELOPER_DIR` 选择 Xcode。

## D9及后续G2检查点

当前生产QEMU完整树`3058e5f90f327708e08e4df6eff63b28362479b3`、gfxstream树`d14277bc86e27f74baa1a1716b1ee29f71587895`（2个overlay）、AEMU声明树`04a678d0a729c6f9870e5f2c8149256294489298`。G2成功读回才发布补丁已编入main backend，install/strip和目录deep strict签名通过；[G2构建收据](../../.macos-work/qa/g0002-no-gl-build-20261101/installed.json)记录精确哈希。D7三个VideoToolbox数组owner及D8 dispatcher/Context销毁顺序、HEVC snapshot计数仍在；下述D8及初次构建哈希为历史快照，其视频结果没有在新G2 backend上重跑。

[生产D8 native suite](../../.macos-work/qa/d8-native-gpu-video-suite-20261101.json)的硬件H264/HEVC helper普通/ASan各56会话、448解码帧和5,898,240B参考字节一致；真实FrameBuffer GL/无GL普通/ASan各16帧通过，RGBA/BGRA、alpha、GPU读回/GetPixels，实际dyld backend/loader/MoltenVK身份确认。driver插桩不代表backend/框架全部插桩；Android视频服务/VP8/VP9/纹理池/异常码流未由此证明。实际dispatcher/Context ASan及原负控另见[生命周期suite](../../.macos-work/qa/media-lifecycle-d0008-suite-20261101.json)。

新包`ModAVD-gapfix-media`418clone并verify，Qt32.87秒开机；旧sentinel/32GiB/home/driver保持且sync正常关机3.74秒。首轮SHM四方向/多reader测试保留false，未将exactADB像素/旋转判为通过。私有v5完整QEMU SIMDworker又经10暂停/恢复、3snapshot循环，2,135,867,594轮无错；范围为单CPU忙worker，不含宿主睡眠。私有RAM实验独立于本节生产产物，不能混用其二进制哈希。

D0009已进一步修复SHM start重复增加recording引用、stop不注销callback及无帧时注册队列耗尽的问题；24B头与BGRA协议不变，stop保留旧映射，因此`shm_ready`只表示映射存在。真实源码/函数host QA的12普通+12ASan/UBSan、6个原代码负控、38项独立review和既有6项CallbackRegistry测试均通过。main双引擎已install/strip并deep strict签名；[D9收据](../../.macos-work/qa/core-shm-lifecycle-update-20261101.json)记录新哈希。backend与D8逐字相同，复用上述精确backend的GPU/视频证据；host测试不代替D9引擎实机验证。

## 产物与构建

核心为固定 AOSP 37.2.10，`Pkg.BuildId=mas-36`。62 个固定项目已检出，原 QEMU 65 + gfxstream 23 + Crashpad 1 个补丁已回放，再应用 3 个 Darwin 构建与 9 个 QEMU 功能 overlay，另有2个gfxstream overlay（native-bind、noGL成功发布）和1个配套AEMU声明overlay；原89个补丁未修改。

`python3 ports/macos/bootstrap.py build --jobs 4` 已真实完成 configure、编译与 `install/strip`，最后返回 0。产物目录为 `.macos-work/aosp/objs/distribution/emulator/`。没有创建源码或二进制 ZIP；虽然上游 ALL 目标仍编译部分测试和 fishtank 对象，本工具不再单独执行 fishtank distribution 构建。

实际处理的构建问题：SDK 27 的新 TBD 架构由 Xcode 原生 ld 解析；保留 pinned Clang 并设 ARM64 deployment 11.0；独立 FlatBuffers CMake 继承固定工具 PATH；窗口 console 的新接口补上无 Qt 测试实现；共享库安装签名采用真实 target 文件名、启用 CMP0087，并检查 codesign 的失败返回。

上游的 5 个 e2fs 预编译工具包含未签名 ARM64 slice，安装后显式 ad-hoc 签名。`emulator/{emulator,resources}` 被 macOS 识别为 flat bundle，须在全部文件安装后签整个目录，不能仅签同名入口文件。

## 实际通过的检查

| 检查 | 结果 |
| --- | --- |
| Launcher `-version` / `-no-window -version` | 均返回 0，37.2.10.0 / mas-36 |
| Launcher `-accel-check` | 返回 0，`Hypervisor.Framework OS X Version 26.6` |
| 窗口 / headless QEMU `-version` | 两个实际引擎均完成装载、返回 0；使用 launcher 契约中的 `DYLD_LIBRARY_PATH=lib64:lib64/qt/lib` |
| Mach-O 架构 | 76 个文件全部含 ARM64，73 个 thin ARM64，3 个 universal |
| 严格签名 | 76/76 `codesign --verify --strict` 通过；整个目录 `--deep --strict` 通过 |
| 静态依赖 | 941 条 load 引用，218 条非系统引用均可按 launcher 搜索路径解析至包内；无非系统绝对 LOAD/ID/RPATH，无外指或悬空链接 |
| 文件核心单元测试 | 77 通过、2 项上游主动跳过 |
| socket 核心单元测试 | 4 通过 |
| console 核心单元测试 | 8 通过，含新增窗口无 UI/多余参数拒绝回归 |

两项跳过为 `Ext4ResizeTest.enlarge` / `contract`，源码主动跳过 Mac ARM 测试。因此不将此测试集作为真实客体扩容通过的证据。顶层 CTest 在 gfxstream 的测试发现阶段超时，所选 3 个测试程序改为直接运行；没有宣称整个上游测试套件通过。

直接启动 QEMU 引擎时必须提供 launcher 所设库路径；裸引擎没有独立 SDK 搜索环境，不能将裸调用的 dyld 失败当作缺失包内库。Launcher 版本输出与实际引擎装载分别验证，前者不代替后者。

软件 Vulkan 链的 `libLLVM.dylib`、`libz3.4.15.dylib`、`libzstd.1.dylib` 最低系统版本为 macOS 15。本次本机满足；不能仅因核心 deployment 为 11.0，就宣称全套软件渲染支持 macOS 11–14。

## 可复核记录

- `.macos-work/build.log`：构建、修复重试、最终安装与目录签名。
- `.macos-work/core-smoke-final.json`：实际入口输出、最终 3 个主入口 SHA-256。
- `.macos-work/core-unit.log`：3 个选定测试程序输出。
- `.macos-work/qa/core-macho-audit-final.json`：逐文件架构、SHA-256、依赖及签名。
- `.macos-work/qa/core-macho-audit.json`：修复前诊断保留。
- `.macos-work/aosp/.repo/modavd-checkouts.json` / `modavd-patches.json`：固定检出与 overlay 收据。

## 后续增量构建与客体验收

D0004 增补隐藏状态下新建/更新副屏的 guard，增量编译、安装与最终目录签名成功，日志为 `.macos-work/build-window-fix.log`。当时 8 个 overlay 的完整树为 `6eb1eba73a0db10525857713258b868b3a96981b`；`.macos-work/qa/core-window-update.json` 记录当时 3 个主入口 SHA-256。上表的 76 文件依赖审计及 `core-smoke-final.json` 为更早构建快照，不将其旧哈希当作后续增量产物的哈希。

D0006 修复 pinned AOSP base 继承的 HVF `case 30 → Q20` 错映射，使 32 个 Q 寄存器一一对应。当时9 个 overlay 的完整树为 `d052e22a497222b0e2c948346423a6d5538dc060`。`verify-hvf-simd.py` 直接提取生产函数，包含 SDK 枚举编译校验全部 32 项，并以恢复原缺陷作为必须失败的负控；随后独立签名 helper 创建自己的 HVF VM / 单 vCPU / 32 KiB 内存，16 轮验证全部 128-bit canonical API put/get 与 guest STR/LDR，共 32 次 guest 运行，全部通过。它没有接触现有 QEMU、ADB 或 guest，也不覆盖 QEMU 完整 put/get 函数、Android、快照和睡眠恢复。

该修复已完成 `build --jobs 4` 增量 `install/strip`、5 个导入工具重签和最终目录 `--deep --strict` 校验，退出码 0；日志为 `.macos-work/build-hvf-simd-fix.log`。新 launcher / Qt QEMU / headless QEMU SHA-256 分别为 `17541df14dd7e7d9a9c792288bfed0cff1db01f23db22b61712d98217453a6bf`、`21069a1ae22603150fd323198adbd216c902042d590f1b11a03128d88ed359db`、`89c043b4320587886b74203e864ca513be6f4aaeda3ee3fca9f572ce2c05212d`；独立证据为 [SIMD 测试](../../.macos-work/qa/hvf-simd-roundtrip-fixed-20261101.json) 与 [构建收据](../../.macos-work/qa/core-hvf-simd-update-20261101.json)。原游戏包未替换；当时的Android/游戏记录仍针对D0006前核心，不能自动转作新构建验收。历史D8包的独立结果见本页首节。

gfxstream另新增1个Darwin native-bind overlay，原23补丁树后完整树为 `ea665af11444f3a33c10d22896fa27e53b862a62`。native helper核对当前线程NSOpenGL和CGL真实绑定结果并返回bool，EGL直接传播；保留core-profile nil pbuffer路径。完整production native源的27项private-context测试通过，bootstrap的9项测试通过；`.macos-work/build-egl-bind-check.log` 记录增量编译、安装和deep strict签名exit0。[构建收据](../../.macos-work/qa/core-egl-bind-update-20261101.json)中最新launcher为 `79be12a3e58736bb25a0181c4e1f5b7a3d155bd42cd41598b0ec6b0f8990828a`，两个QEMU哈希与上述D0006相同；实际承载修改的 `lib64/libgfxstream_backend.dylib` 为 `97e60edf0cf1422159583bf1f05d2ffb982773fdb0bade53c219ae9aed7b315a`。该测试不覆盖完整EGL/Android渲染、context loss/OOM或像素输出；原游戏包同样未替换；D8独立包已验收。

此前核心在 `.macos-work/package/ModAVD-validated` 中真实冷启，请求 16 核被裁剪为 8 核，21.467 秒开机，guest present/online 均为 `0-7`。证据为 `.macos-work/runtime/logs/hvf-core-limit-20261031T153905Z.json`。此前 headless/Qt 的 4 核运行、窗口 show/hide/show 保持 guest boot ID、真实静态/动态 SHM 与 IME console 客体往返也已完成，分别见 `.macos-work/qa/window-visibility-first.json`、`shm-dynamic.json`、`ime-console-roundtrip.json` 与 [客体检查记录](qa/GUEST-CHECKS.md)。

用户已明确接受两份官方 SDK/ARM 镜像条款；adb 37.0.1 与 API34 ARM64 r14 已安装并全文件复核。B 服明日方舟已安装，guest 确认 primary ABI 为 arm64-v8a，首次启动到协议页；用户选择暂不登录，未接受游戏条款。这些结果尚不证明完整 Cocoa 组合输入、物理内存回收收益或游戏实战通过。

后续SHM契约收证：`.macos-work/qa/manager-shm-media-contract-20261101/report.json`确认四方向固定natural1280×720、每方向3reader×12只读mapping、设置精确恢复；`ok`只表示收证与metadata，`pixelParityEstablished=false`明确保留，未赋色差容差。首轮错误旋转/静态heartbeat假设及false证据不覆盖；源契约说明见`.macos-work/qa/manager-shm-source-review-20261101/assessment.md`。D8构建、包内二进制和各专项报告hash汇总见`.macos-work/qa/d8-integration-index-20261101.json`。

D9封存独立包`ModAVD-gapfix-shm`现已完成真实双引擎验证：Qt开机34.8589秒、headless12.2905秒，各0warnings；各80次重复start、80次重复stop、40轮stop/start、停止期间真实Settings显示变化而SHM header/pixels不变、restart观察新帧及恢复HOME/活跃帧、同一boot ID全部通过。两次均graceful/synced=true关机（Qt3.9737秒、headless见stop收据），旧sentinel/32GiB/µLauncher/driver保持。证据为`.macos-work/qa/manager-shm-d9-{qt,headless}-20261101/report.json`、`gapfix-shm-retention-20261101.json`和`gapfix-shm-{qt,headless}-stop-20261101.json`。这些是专用QA实例公共console与SHM短观察，内部refcount由host专项证明；每次read重新open不能证明旧reader跨stop持有同一object。不声称ADB像素相等、帧无撕裂或MAA已消费增强。原游戏包和MAA未修改，私有RAM仍不在此包中。

D9产物和测试身份的41项一致性检查见[集成索引](../../.macos-work/qa/d9-integration-index-20261101.json)，全部通过；其复核脚本与报告并存。原89表仍65Q+23G+1C。

G2新检查点：`0002-no-gl-readback-publish-only-success.patch`已完成生产构建/签名，gfxstream完整树`d14277bc86e27f74baa1a1716b1ee29f71587895`（2 overlays），backend SHA`35fbd4366185ea5985e41e495c73c910030e7522075ef23de7c6317ea6f60920`；CPU正负控8组及native v4共8模式192帧通过。新包`ModAVD-gapfix-readback`418clone/508payload verify，Qt31.794854458s/headless13.251739375s开机，各7项数据/配置/实际backend保留和7项SHM生命周期及HOME/stream恢复通过，graceful/synced停机分别3.64848575s/3.383434625s，后续确认两PID消失。失败历史、RGBX两模式契约、额外scratch/copy成本和覆盖边界见[G2验证说明](qa/framebuffer-readback/G2-VALIDATION.md)。G2 backend与D9已不同，旧视频结果不能写成新G2视频重跑；生产RAM和原游戏包/MAA状态不变。
