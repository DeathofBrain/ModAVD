# ModAVD macOS ARM64 移植

2026-11-01 清理检查点：代码已同步到 `codex/macos-port-checkpoint`。随后按用户要求删除了本机全部 ModAVD 生成资源，包括 SDK、构建目录、全部实例和虚拟盘（含原游戏实例）。下文运行结果属于清理前的历史验收，本机现无可直接启动的 ModAVD 包；原始本地 evidence 链接已失效，保留[验收摘要](qa/validation-summary-20261101.json)与[清理回执](qa/local-resource-cleanup-20261101.json)。后续官方 AVD 基线需单独准备。

目标是 Apple Silicon 上的一套本地开发目录：原生 ARM64 emulator、HVF、Android 14 ARM64 客体、原有 Qt 模拟器窗口，以及复用原网页的 AppKit/WKWebView 管理器。CLI、MCP、私有 ADB 端口、实例编号和截图协议保留原契约。游戏验收只包含 B 服《明日方舟》；用户暂缓登录后的实战回归。MAA Verge 当前截图方式已按用户要求测试，增强消费链路尚未确认，未修改 MAA。

2026-11-01 的[完整对齐审计及补齐](../../macOS-compatibility-report.md)已修复固定 Google/AOSP 基线继承的 HVF Q30→Q20 映射、Darwin 活动日志保护及 native EGL 绑定失败检测；补齐维护工具和真实本地更新/恢复。ARM64 Vulkan 修复已在正式 Manager 启动流程自动生成/挂载，并通过真实 GPU 读回；idle-poll 已接入 ARM64 动态启动。D0007/D0008又修复视频所有权/生命周期，D0009补SHM启停，G2补截图cache初始化及失败不发布；新包双引擎通过，详情见[G2验证](qa/framebuffer-readback/G2-VALIDATION.md)。私有v7已有驻留页释放证据，生产净RAM收益、完整Cocoa IME及其余故障/性能验收仍未闭合，未宣称全功能全优化对齐。原89补丁逐项结果见[核心审计](qa/CORE-PARITY-AUDIT.md)。

核心继续使用仓库的 `qemu/mas36`、`gfxstream/mas35` 和 `crashpad/module-version22` 补丁系列，再叠加 `incoming/macos-build`、`incoming/macos-qemu` 与 `incoming/macos-gfxstream`；管理器使用固定上游提交与 `incoming/macos-manager`。不迁移 Windows AVD，不带入 x86 native bridge、WHPX 或 Windows DLL 注入流程。

## 本机入口

需要 Apple Silicon、Python 3.11+、Git 和完整 Xcode。以下命令在仓库根目录执行，默认工作区 `.macos-work/`。构建子进程通过 `DEVELOPER_DIR` 使用 Xcode，不修改系统 Developer Directory。同步、构建、运行与装配均设有 20 GiB 剩余空间停线；它是保护阈值，不是整个构建的空间预算。

先准备和编译核心：

```sh
python3 ports/macos/bootstrap.py preflight
python3 ports/macos/bootstrap.py manifest
python3 ports/macos/bootstrap.py sync --jobs 4
python3 ports/macos/bootstrap.py patch
python3 ports/macos/bootstrap.py build --jobs 4
```

默认输出位于 `.macos-work/aosp/objs/distribution/emulator/`。构建锁定 `darwin_aarch64`、Qt WebEngine、`OPTION_MINBUILD=ON`、`mas-36`，保留 ARM64 带窗口与无头 QEMU。同步使用固定提交、浅获取及 Darwin 预编译稀疏目录，源预编译重建归档和上游测试系统镜像暂缓下载。中断后保留已完成的源码与构建对象。

管理器可独立构建：

```sh
ports/macos/manager/build.sh
python3 ports/macos/manager/smoke.py
```

输出是 `.macos-work/manager-dist/`。构建方式和本机已通过的项目见 [manager/README.md](manager/README.md) 与 [manager/VALIDATION.md](manager/VALIDATION.md)。

## 隔离运行验收

固定运行组件为 macOS platform-tools 37.0.1 和 API 34 Google APIs ARM64 revision 14。先阅读两份完整条款；只有人已经明确接受对应条款，才能执行 [runtime/README.md](runtime/README.md) 中带文本指纹的 `accept-license` 命令。本工具不自动接受条款。

本工作区的两份许可已由用户明确接受并保存记录，固定平台工具与系统镜像已从官方来源安装，`verify` 均通过。以下许可步骤用于说明新 runtime 的准备流程；当前记录不需要重复接受。

```sh
python3 ports/macos/runtime/runtime.py licenses --id android-sdk-license
python3 ports/macos/runtime/runtime.py licenses --id android-sdk-arm-dbt-license

# 已明确接受对应条款、并保存接受记录之后：
python3 ports/macos/runtime/runtime.py install platform-tools

# 核心构建完成后，才解锁约 1.61 GB 的镜像下载：
python3 ports/macos/runtime/runtime.py install system-image \
  --core-ready .macos-work/aosp/objs/distribution/emulator/emulator
python3 ports/macos/runtime/runtime.py verify platform-tools
python3 ports/macos/runtime/runtime.py verify system-image
python3 ports/macos/runtime/runtime.py create-avd

python3 ports/macos/runtime/runtime.py start \
  --emulator .macos-work/aosp/objs/distribution/emulator/emulator --headless
python3 ports/macos/runtime/runtime.py wait-boot --seconds 180
python3 ports/macos/qa/guest_checks.py --root .macos-work/runtime run --network
python3 ports/macos/runtime/runtime.py shm-check --samples 5
python3 ports/macos/runtime/runtime.py stop
python3 ports/macos/runtime/runtime.py stop-server

# 无头实例已正常停止后，再验证 Qt 窗口：
python3 ports/macos/runtime/runtime.py start \
  --emulator .macos-work/aosp/objs/distribution/emulator/emulator
python3 ports/macos/runtime/runtime.py wait-boot --seconds 180
python3 ports/macos/runtime/runtime.py shm-check --samples 5
python3 ports/macos/runtime/runtime.py stop
python3 ports/macos/runtime/runtime.py stop-server
```

运行工具使用独立 SDK、AVD、ADB 密钥与私有 ADB 20050；启动前检查 HVF 与端口，停止前核对实例 PID、启动时间和 AVD 身份。开始管理器验收前须停止上述 QA 实例及其私有服务，二者使用相同的 ModAVD 私有 ADB 端口。详细的空间监控、SHM 24 字节头/BGRA 格式和探针限制见运行说明。

已完成核心全量编译、安装与本地签名，官方 API 34 ARM64 客体在 headless 和 Qt 两条路径均已真实开机。首轮 Qt `show/hide/show` 保持同一 guest boot ID；副屏窗口显隐 guard 已增量构建（`.macos-work/build-window-fix.log`），尚未实际创建副屏复验。动态 SHM 10样本已观察到帧号509→528及像素指纹变化；Manager 实例的既有流也成功只读导出1280×720游戏PNG。未覆盖旋转、无GL专项、所有颜色及并发无撕裂。GLES 为 Apple M4 ES 3.0，Vulkan 枚举为 Apple M4 / API 1.3.0，33,792 B loopback HTTP 传输哈希相符。详细证据和权限限制见 [客体检查记录](qa/GUEST-CHECKS.md#2026-10-31-实机采集)。

## 本地目录装配

核心、管理器和已获许可的运行组件齐备后，执行：

```sh
python3 ports/macos/package.py fetch-ime
python3 ports/macos/package.py fetch-launcher
python3 ports/macos/package.py assemble
python3 ports/macos/package.py verify
```

IME 固定为官方 ModAVDHostIME v1.0.0，20,748 B，SHA-256 为 `912c9c9724e63fa606fa5d4e4212cef0e24148b013923ba49d1ebf8cfd38e0e2`。轻量桌面 µLauncher 固定为 0.2.12 / versionCode 57，4,099,556 B，SHA-256 为 `f67124dad5009016dd1fce379d79b20ea93a46cefb5759002f3fa149d50b018f`。两者的官方来源、固定哈希和许可证来源见 [components.lock.json](components.lock.json)。

µLauncher 从 F-Droid 固定 APK URL 获取，字节哈希与开发者 GitHub 同版 asset 公布的 SHA-256 一致。固定源码提交 `f09595f7af935952b41eff9395d2ca56209ada36` 的许可证为 MIT，`minSdk=21`。实际 APK 包含 DEX、没有 `lib/`、原生库文件或 ELF 负载，因此没有 x86 专用 JNI 依赖；Manager 真实实例已完成安装、默认桌面和首次初始化。原始 MIT 全文和 APK 内嵌的 ConstraintLayout Apache-2.0 全文随装配保留。

`assemble` 按需获取这两个固定小 APK，不下载核心、管理器、SDK 或游戏。轻量桌面默认随包提供；`--without-launcher` 可明确省略，管理器仍将它视为可选组件。所有输入和输出均可通过 `--help` 中的路径参数替换。

默认装配结果：

```text
.macos-work/package/ModAVD/
  ModAVD.app/
  tools/{modavd,modavd-manager,modavd-webview,modavd-maintenance}
  tools/{fetch-components,collect-info,report-config}
  tools/maintenance/
  sdk/emulator/
  sdk/platform-tools/
  sdk/system-images/android-34/google_apis/arm64-v8a/
  components/modavd-ime.apk
  components/de.jrpie.android.launcher_57.apk
  licenses/
  provenance/
  modavd-manifest.json
  PACKAGE-MANIFEST.json
  README.zh-CN.txt
```

默认只生成目录，不生成 ZIP。APFS 上优先使用独立 inode 的 copy-on-write clone，其他文件系统回退为普通复制；不会与源码或其他实例共享可变硬链接。相对内部符号链接、执行位与核心完整目录会保留；绝对、外部、悬空链接和特殊文件会被拒绝。

装配先校验 ARM64 可执行格式、核心版本/ModAVD 特征、管理器补丁指纹、组件收据与许可证文本，再在临时目录生成版本、补丁哈希、当前源码状态和文件 SHA-256 清单。成功后原子发布，已有输出一律拒绝覆盖，包括先前由本工具生成的目录。重复构建应选择新的 `--output`，现有产物使用 `verify` 检查。装配不会更新用户安装或删除用户实例。

历史游戏包 `.macos-work/package/ModAVD-validated` 保留原样。补齐包 `ModAVD-gapfix-native` 已通过真实实例初始化、Qt/headless 启停、16→32 GiB 保数据扩容及前后 qcow2 检查。后续 `ModAVD-gapfix-integrated` 包含 ARM64 Vulkan/idlepoll Manager（补丁 `2182eac11f98e0261f758846f1d59a329b9de46d1a745f2a5333f454f23f0c68`），417 个 APFS clone、0 个普通复制、507 项负载校验通过；自动修复后的12组描述符GPU读回及四线程8192次绑定通过，重复 start 未改变 zygote/SystemUI PID。后续诊断版 `.macos-work/package/ModAVD-gapfix-1955` 已另装配（Manager补丁 `1955b54a9c6916647daf6354fb28687e342cb10d10082f87e355827ea4afc91d`，同417clone/507payload），通过完整复制的旧sentinel/32GiB/µLauncher保持，以及guest sync后受控SIGKILL的qcow2检查和重启数据保持。它修正mounted SHA检测和restart失败回滚，主机负例通过；目录名称不表示全部验收完成。

保留整个目录结构，双击 `ModAVD.app`，或显式指定根目录：

```sh
.macos-work/package/ModAVD/tools/modavd \
  --root "$PWD/.macos-work/package/ModAVD" check --json
.macos-work/package/ModAVD/ModAVD.app/Contents/MacOS/ModAVD \
  --root "$PWD/.macos-work/package/ModAVD"
```

应用也会沿可执行文件的父目录发现 `sdk/`。管理器运行后会生成 `avd/`、`mas-avd.json`、日志和缓存；装配校验只读取负载目录，用户状态单独保留。

## 验证边界

管理器的 Go 测试、vet、实际进程/HTTP 冒烟与本地 ad-hoc 签名已通过。同步、运行和装配工具的 33 项测试已通过，包含原生跨进程 POSIX SHM、真实 Git 浅取/稀疏检出与重叠补丁恢复、损坏归档拒绝、原子禁止覆盖、权限/符号链接保留、用户状态隔离，以及轻量桌面的哈希拒绝、原生库拒绝和可选省略；这些临时目录测试不能单独证明 Android 运行效果，实机证据按上文记录。

```sh
python3 -m unittest discover -s ports/macos/qa -p 'test_*.py' -v
```

核心与 Manager 基础生命周期、动态 SHM、console IME 五项往返、默认桌面、B服 APK 安装首启、保数据扩容、ARM Vulkan 和本地真实更新已有各自实机证据。Cocoa 原生按键测试 helper 的 Accessibility 预检尚未获授权，没有执行原生注入；console 往返不替代 Cocoa 会话。用户暂缓游戏登录后实战，副屏/多实例/图形视频故障边界及更广泛性能仍须验收。私有v4/v5已验证真实Android暂停/回滚/读零/重用和网络并发；v6独立2MiB对象有native机制正证；后续私有v7已有四个resident事务/三个256KiB去重范围的释放证据，生产净收益仍未闭合，默认不变。候选/累计提交或mincore归零不是物理收益。装配清单中的 `not_run_by_assembler` 仅表示装配器不执行运行验收。Developer ID/公证不在个人本地使用范围，远程发行服务未验收。

## 随包维护与本地更新

`tools/fetch-components`、`tools/collect-info`、`tools/report-config` 提供固定 SDK 补全、问题包和脱敏报告；参数与数据范围见 [维护说明](maintenance/README.md)。本轮生成的实际日志 ZIP 通过路径、邮箱和常见认证值模式检查，不自动截图、复制剪贴板或上传。

`ports/macos/release.py` 从已校验目录生成不含 SDK/实例的本地更新 ZIP。使用 `tools/modavd --root <安装根> update prepare --package <ZIP> --sha256 <SHA256>`，成功后运行同根 `update install`；中断执行 `update recover`。真实 Qt 链接、Mach-O、`.app` 和四种签名 xattr 的安装/中断恢复已通过，SDK/用户状态哈希保持，见 [Manager 验证](manager/VALIDATION.md)。

此前D8独立包为`.macos-work/package/ModAVD-gapfix-media`：418clone并verify、Qt32.87秒开机，旧sentinel/32GiB/home/driver保持，正常sync关机。QEMU完整树`f0ad4acfabc8ed00711847b393cea5515fe10ca0`，AEMU声明树`04a678d0a729c6f9870e5f2c8149256294489298`；生产backend SHA`67812b82a0bbfcad9b6a65184b45d18b848ef66867b05bacb90ba26a29ec6d99`。实际GL/无GL FrameBuffer和H264/HEVC硬解normal/ASan结果见[视频/GPU汇总](../../.macos-work/qa/d8-native-gpu-video-suite-20261101.json)。完整QEMU单CPU SIMD又通过10暂停/恢复及3snapshot循环；这不覆盖宿主睡眠。SHM扩展矩阵的exactADB像素比较false保留，尚未作为通过项。

后续SHM契约收证：`.macos-work/qa/manager-shm-media-contract-20261101/report.json`确认四方向固定natural1280×720、每方向3reader×12只读mapping、设置精确恢复；`ok`只表示收证与metadata，`pixelParityEstablished=false`明确保留，未赋色差容差。首轮错误旋转/静态heartbeat假设及false证据不覆盖；源契约说明见`.macos-work/qa/manager-shm-source-review-20261101/assessment.md`。D8构建、包内二进制和各专项报告hash汇总见`.macos-work/qa/d8-integration-index-20261101.json`。

D9生产补齐已完成构建和签名：新增`0009-shared-memory-recording-lifecycle.patch`，QEMU为3构建+9功能overlay，完整树`3058e5f90f327708e08e4df6eff63b28362479b3`。start/stop/析构幂等、同步callback注销及无frame队列回收的host QA为12普通+12ASan/UBSan通过、6个原代码负控全部失败；独立review38项及既有CallbackRegistry6项通过。24B SHM ABI不变，stop保留旧映射；`shm_ready`不证明stream active。backend与D8精确相同，既有硬解/GPU专项可按identity复用。新包`ModAVD-gapfix-shm`418clone、508payload verify通过，实机证据另列。首次start在后台编译期间被正常内存预检挡下（可用4.3GB<4.5GB）；未绕过门禁，原失败记录保留。

私有RAM v7已通过固定256KiB对象、精确allocation granule/serial收据与每个SG/range调用最多2次native尝试/32对象检查（非整个queue element或暂停BH的总预算）的native及成本gate。2.5GiB untouched对象构造从2MiB粒度1280objects/7.441ms变为256KiB粒度10240objects/40.631ms，释放1.190ms，region分配前55→构造后10297→释放后55（其中target10240）；单次值不作SLA，footprint不变不代表kernel metadata无成本。v7实际allocator两代native正控4608、monolithic负控2304及故障检查通过，systemwide counter非exact窗口原样保留。见`.macos-work/qa/exact-v7-native-20261101/assessment.json`和`dcr-construction-cost-20261101/assessment.json`。真实Android驻留页释放、安全与第二代验证仍在进行，生产默认未变。

D9封存独立包`ModAVD-gapfix-shm`现已完成真实双引擎验证：Qt开机34.8589秒、headless12.2905秒，各0warnings；各80次重复start、80次重复stop、40轮stop/start、停止期间真实Settings显示变化而SHM header/pixels不变、restart观察新帧及恢复HOME/活跃帧、同一boot ID全部通过。两次均graceful/synced=true关机（Qt3.9737秒、headless见stop收据），旧sentinel/32GiB/µLauncher/driver保持。证据为`.macos-work/qa/manager-shm-d9-{qt,headless}-20261101/report.json`、`gapfix-shm-retention-20261101.json`和`gapfix-shm-{qt,headless}-stop-20261101.json`。这些是专用QA实例公共console与SHM短观察，内部refcount由host专项证明；每次read重新open不能证明旧reader跨stop持有同一object。不声称ADB像素相等、帧无撕裂或MAA已消费增强。原游戏包和MAA未修改，私有RAM仍不在此包中。

补充预算边界：v7的2次native尝试/32对象计数在每个`qemu_ram_reclaim_report()`调用重置，virtio-balloon逐SG调用；一次BH暂停最多drain32个元素，每元素可有多个SG，故这些数值不构成整次暂停上限。既有`report_work_us`也不含全vCPU暂停等待、元素解析及全部候选审计扫描。正在补完整分段遥测，不把同步函数耗时误报为整次pause成本。

后续私有 v7 telemetry-only 客体已取得驻留物理释放：4 次释放覆盖 3 个 256 KiB 地址范围（768 KiB 去重范围），含同 offset 两代 backing；这不是当前净内存收益。完整暂停遥测也确认每 SG 的预算不构成整次 BH 上限，观察到最大 request→resume 返回 14.068 ms。冷对象全局计数仍有噪声，内核 metadata 净成本未测，生产默认未变；详见 [v7 Android 增量记录](qa/hvf-object-reclaim/v7-android-progress.md)。

G2新检查点：`0002-no-gl-readback-publish-only-success.patch`已完成生产构建/签名，gfxstream完整树`d14277bc86e27f74baa1a1716b1ee29f71587895`（2 overlays），backend SHA`35fbd4366185ea5985e41e495c73c910030e7522075ef23de7c6317ea6f60920`；CPU正负控8组及native v4共8模式192帧通过。新包`ModAVD-gapfix-readback`418clone/508payload verify，Qt31.794854458s/headless13.251739375s开机，各7项数据/配置/实际backend保留和7项SHM生命周期及HOME/stream恢复通过，graceful/synced停机分别3.64848575s/3.383434625s，后续确认两PID消失。失败历史、RGBX两模式契约、额外scratch/copy成本和覆盖边界见[G2验证说明](qa/framebuffer-readback/G2-VALIDATION.md)。G2 backend与D9已不同，旧视频结果不能写成新G2视频重跑；生产RAM和原游戏包/MAA状态不变。
