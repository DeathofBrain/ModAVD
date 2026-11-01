# ModAVDManager macOS ARM64 移植

保留现有 Go 后端、网页界面、CLI、MCP 和协议 1，只替换宿主平台接口。源码基于 `ModAVD/ModAVDManager` 的固定提交 `21c2a3ffa22eb37a5e39e61b1b53f2258d0f3e7a`，普通 diff 在 `incoming/macos-manager/0001-darwin-arm64.patch`。

| 接口 | macOS 实现 |
| --- | --- |
| 进程、启动时间、命令行、监听端口归属 | `libproc`、`sysctl`；停止前核对 PID 与启动时间 |
| 跨进程互斥、后台启动 | `flock`、`setsid`、短命重执行 helper |
| 管理器窗口 | AppKit + WKWebView，复用原网页；原生 APK 选择器与错误提示 |
| 模拟器窗口显隐 | 控制台 `window show/hide`，依赖 `incoming/macos-qemu` 中的窗口补丁 |
| CDK 加密 | AES-GCM；每用户包装密钥存入 Keychain，不通过命令行传递密钥 |
| AVD / SDK | Android 14 Google APIs `arm64-v8a`、`darwin-aarch64` QEMU、无 `.exe` 的程序路径 |
| 共享内存名 | POSIX `videmulator<console-port>`，沿用 24 字节头和 BGRA 像素 ABI |

实例编号、私有 ADB 20050、脚本 ADB 20049、实例 console/ADB/gRPC 端口和 JSON 退出码保留原契约。ARM64 客体不执行 x86_64 native bridge、cpuinfo 伪装或 x86 Vulkan 二进制修补；不迁移 Windows 实例数据。macOS 调度由系统负责。客体 idlepoll 沿用按磁盘请求率动态启停的守护逻辑；原生 ARM64/HVF 小块写入回归已验证收益。

客体 Vulkan 修复已增加 API34 Google APIs arm64-v8a revision14 的独立 profile：完整原库与结果 SHA256 固定，本机从客体原库生成补丁，不分发修改后的客体库。ARM64 与 x86 的主机、客体缓存名称分开，挂载脚本、状态报告与缓存校验均使用选定架构的哈希；沿用原 zygote 重启及运行中应用/安装任务门禁。真实 ARM64 GPU 读回与四线程绑定回归通过后才启用此 profile。

2182eac1 集成 QA 包已通过自动生成/上传/挂载、客体重启复用 cache，以及重复 start 不重启 zygote 的实机检查。最新1955b54a另对已挂载库核对实际 SHA，重启失败明确报错，并按 mountinfo/inode 身份安全回滚；主机全套检查、签名构建及该版新根实机装配通过，完整 AVD 复制、sync 后单次 SIGKILL/离线镜像 check/重启保留已有证据；live restart失败注入尚未执行到，版本边界见 [验证记录](VALIDATION.md)。

当前最新组合为 `.macos-work/package/ModAVD-gapfix-readback`：Manager仍为1955b54a，QEMU沿用D9 tree `3058e5f90f327708e08e4df6eff63b28362479b3`，新增Darwin gfxstream0002 noGL成功发布补丁（G2，非原G0002 compute）。gfx overlay tree为`d14277bc86e27f74baa1a1716b1ee29f71587895`，新backend为`35fbd4366185ea5985e41e495c73c910030e7522075ef23de7c6317ea6f60920`。严格签名及418 APFS clones/508 payload校验通过。3GiB/2CPU Qt/headless分别31.7949/13.2517秒开机，retention7项和SHM启停7项各自全过，HOME/流恢复；synced/graceful关闭3.6485/3.3834秒且零warning，最终PID/console消失另有收据。旧哨兵、32GiB、µLauncher、修复驱动和生产order9保持。

G2保持IColorBuffer的void虚接口，增加非虚bool入口；noGL复用独立scratch，读失败或部分写后失败均不发布，保留last-good cache，初始零cache不算有效帧。CPU baseline/candidate×GLES1/0×normal/ASan八组与原生sealed D9/G2×normal/driver-ASan四wrapper、八GL/noGL模式通过预期契约，每模式24帧，另测拒绝/恢复及GetPixels边界。RGBX在noGL归一alpha255，GL按同源GPU实际X字节精确核对；旧统一alpha假设导致的QA失败保留。ASan仅仪表新驱动/契约替身，未注入真实GPU故障，见[读回工具](../qa/framebuffer-readback/README.md)。D9主机专项12普通+12ASan/UBSan、6原码负控与Registry6项仍按旧收据保留；旧backend `67812b82…` 的VT结果不当作G2 fresh验证。独立ADB/SHM像素相等、Android guestVulkanOnly、MAA、无撕裂和长稳仍待验收。live不观察内部refcount，每次重开映射不证明持久reader；`shm_ready`仅代表映射存在。

G2的[集中验证记录](../qa/framebuffer-readback/G2-VALIDATION.md)链接706项新包独审，覆盖负载/签名/补丁/源码身份与两引擎raw收据；没有将文件内部一致扩为ADB精确像素等价。

private RAM v7 的固定256KiB对象和精确收据已取得4次驻留释放：累计1MiB、3个去重范围768KiB，含同offset两代；故障271次全rollback/9resident保持，覆盖尾部1834对象458.5MiB全cold。它们不等于整体净RAM收益：严格全guest物理仍false，cold联合噪声10/262，metadata净成本未测，构造region峰10297。2次尝试/32检查逐SG重置，不是BH/pause总cap；最大暂停观察值也不是限额。Manager生产写读order9；旧private v6实读10，v7无逐launch新sysfs读测；冻结binary的A9/B6对照收证进行；首次参数写入失败已正常停机、未进测量，retry1进行，无比较结论。实验未进入生产组合且默认未变，完整身份/边界见[最新记录](../qa/hvf-object-reclaim/v7-android-progress.md)和[验证记录](VALIDATION.md#private-ram-v7-组件与客体边界)。

Darwin 启动显式设置 `ANDROID_EMU_VK_HIDE_REBAR=0`，保留 MoltenVK 统一内存对客体的可映射属性；NVIDIA 驱动缓存环境变量仅在 Windows 注入。运行记录目录与固定核心的 `ConfigDirs` 规则保持一致，控制台认证使用原生用户目录。

Darwin 的 `pid_<pid>.ini` 查找使用 `HOME/Library/Caches/TemporaryItems/avd/running`；仅当 HOME 为空时回退到模拟器用户目录的 `avd/running`。用户设置与控制台令牌继续使用各自原路径。

## 构建

需要 Apple Silicon、Go 启动器、Python 3、Git 和带 macOS SDK 的 Xcode。脚本锁定 Go `1.26.5`，不修改系统选中的 Developer Directory。默认使用 `/Applications/Xcode.app/Contents/Developer`，可通过 `--developer-dir` 更改。

```sh
ports/macos/manager/build.sh
python3 ports/macos/manager/smoke.py
```

默认源码在 `.macos-work/manager`，输出在 `.macos-work/manager-dist`。`--source` 与 `--output` 可更改目录。新源码会从固定 Git 提交准备；已存在源码必须与固定提交加补丁完全相符，额外修改会被拒绝并保留。已有且没有构建标记的非空输出目录也会被拒绝。

构建默认执行 `go test ./...` 与 `go vet ./...`，然后生成：

```text
manager-dist/
  tools/modavd
  tools/modavd-manager
  tools/modavd-webview
  ModAVD.app/Contents/MacOS/ModAVD
  ModAVD.app/Contents/MacOS/modavd-webview
  LICENSE
  SHA256SUMS.json
```

内存预算与卡片菜单策略另有无浏览器依赖的前端回归，可在源码目录运行 `node --test internal/ui/checks_frontend_test.mjs`；本轮 10 项通过。

将 `tools/` 和 `ModAVD.app` 放到完整的 macOS ModAVD 根目录旁。应用包会向上寻找含 `sdk/` 或 `mas-avd.json` 的根目录，也可显式运行：

```sh
.macos-work/manager-dist/tools/modavd --root /绝对路径/ModAVD list --json
.macos-work/manager-dist/ModAVD.app/Contents/MacOS/ModAVD --root /绝对路径/ModAVD
```

此脚本只构建管理器。模拟器、平台工具、Android 镜像、IME 和游戏由其他构建或运行流程提供。构建输出采用本地 ad-hoc 签名；发行所需 Developer ID 签名、公证与下载隔离验收尚未完成。

## 已验证与待验收

本机已通过全部 Darwin Go 测试、`go vet`、Windows amd64 生产源码交叉编译和 Windows 测试源码编译。Windows 测试未在 Windows 上执行。

主页默认 6 GiB 内存检查不足时显示“内存需调整”，各实例的开机按钮按其当前配置和后端返回的内存开销判断。其他阻断条件保留。Restart 在关机后由后端重新检查可用内存，运行中窗口显隐与关机保持可用；创建、启动与 Resume 的后端检查不变。

新增测试实际验证了 PID 重用拒绝、当前进程命令行、TCP 监听者 PID、文件锁互斥、子进程转交 launchd 与独立进程组、ARM64 AVD 创建/删除、原子替换、更新包执行权限和平台清单拒绝。共用 CLI/MCP/HTTP 测试保留。Windows AUTO-MAS 字节 oracle 与写入真实 Keychain 的 HTTP CDK 测试在 Darwin 跳过；当前 Keychain API 的交互授权与 CDK 往返需人工验收。

更新事务测试已在 Darwin 覆盖 `.app` 资源和执行位、部分替换后的完成或退回旧版、重命名冲突消除后的再次恢复，以及 SDK、AVD 和日志保留；模块竞态检查通过。Darwin 在下载、解包、增量与安装备份复制时按每 MiB / 文件检查至少 20 GiB 可用空间，空间低或探测失败立即停止，保留下载或恢复所需状态；Windows 保留原预算与 I/O 行为。

Swift 启动器已编译，`.app` 已通过 `codesign --verify --deep --strict`。`smoke.py` 用临时根目录运行实际管理器，验证版本、登录、空列表、Host/Origin、单实例与退出清理；只删除该次测试自己创建的状态和锁文件，不启动 ADB 或模拟器。

原生 WKWebView 实例主页与电脑检查诊断对话框已在隔离根目录打开；较早完整包已完成实际实例初始化、关机与带窗口启动，明日方舟已安装并首启至协议页。当前重建 GUI 的内存提示与按钮、其余 WKWebView 交互与 APK 选择，以及登录后的游戏回归仍需分别验收。G2 Qt/headless 已有独立 SHM 公开启停实测；D8 多 reader/固定尺寸契约重测通过，精确 ADB 像素相等仍未成立，MAA/MAA Verge 的实际增强接入仍未确认。macOS 更新使用下述独立签名更新包契约，完整 bundle 先由 `ports/macos/release.py` 生成更新 ZIP。成功安装后的长期用户回滚不属于原版更新器契约；中断恢复保留事务备份，完成后清理。


## macOS 签名更新包

`release.py` 从已验证完整包选择 `sdk/emulator`、`ModAVD.app`、整个 `tools`，以及组件、许可、provenance 和允许的根级发行文档；SDK platform-tools、系统镜像与用户状态不进入更新载荷。transport 清单为 `modavd-update-manifest.json`（`modavd-macos-update-v1`），原包的 `modavd-manifest.json`、`PACKAGE-MANIFEST.json` 和 owner marker 作为载荷保持一致。

```sh
.macos-work/manager-dist/tools/modavd --root /绝对路径/ModAVD update prepare --package /绝对路径/release.zip --sha256 64位摘要
.macos-work/manager-dist/tools/modavd --root /绝对路径/ModAVD update install
.macos-work/manager-dist/tools/modavd --root /绝对路径/ModAVD update recover
```

本地选择必须给出 ZIP 摘要；Manager 先复制并核对选择的字节，再解包。准备后安装和恢复无需原 ZIP、网络或读取 CDK 设置。发行文件逐项核对大小、SHA256、执行权限和签名扩展属性；必需入口必须是真实 ARM64 Mach-O，原生文件以及 `.app`、emulator 平面包均通过 `codesign --verify --strict`，包级另做 deep 检查。本地 ad-hoc 签名证明内容完整性，发布者身份仍由明确选择的摘要或受信发行渠道确定，尚无 Developer ID / 公证验收。

Qt/framework 的内部相对链接可保留；逐分量解析链接与 `..`，拒绝越过所属更新单元、悬空、循环、绝对路径、链接路径祖先、大小写冲突和特殊文件。平面 emulator 的 generic code 签名保存在白名单 `com.apple.cs.*` 扩展属性，清单以 base64 传输、提取后复原，避免普通 ZIP 丢失其签名。

安装将 `.app`、emulator、tools 等作为目录单元重命名，每一步写事务日志；恢复重新核对分布在 stage/目标位置的完整候选与签名，再补完，候选损坏则按事务备份还原。SDK、AVD、日志、缓存与实例配置保留。Windows 原有 AUTO-MAS 事务路径保留。

Darwin 日志清理用 libproc 查真实打开的文件描述符并复验进程启动时间，保护本 Manager、其他 Manager、孤儿 qemu/logcat 及可读的手动 writer；受保护系统服务的不可读描述符不阻止清理，相关进程不可读时停止本次清理。跨进程目录锁覆盖清理到新输出打开的窗口，活动日志不计入数量和总量裁剪。

复制本地实例时须正常停止并保留完整 descriptor 和 `.avd` 状态，仅重写 descriptor 的新根目录。`version_num.cache` 缺失会触发核心重建 userdata/encryption 等 qcow2，不能作为可丢弃缓存；其他兼容标记、base/overlay 与 encryption 必须一并保留。以旧文件哨兵和实际容量保持验证复制成功，不能以“能开机”代替保数据验收。
