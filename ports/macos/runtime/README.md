# macOS 隔离运行与截图验收

这些是 ModAVD 移植的开发工具。默认资源目录为仓库根目录下 `.macos-work/runtime/`，不使用现有 Android SDK、AVD、ADB 密钥或默认 5037 服务。2026-10-31 用户已明确接受两份官方 SDK/ARM 镜像许可；固定 platform-tools 与系统镜像已安装并通过 `verify`，独立 QA AVD 已完成 headless 和 Qt 真实开机。实际证据及尚未覆盖的范围见下方实机状态。

## 固定组件与许可证

`components.lock.json` 保存官方归档 URL、字节数、官方 SHA-1、版本以及来源元数据的 SHA-256。下载成功后额外计算归档 SHA-256，并记录安装文件的 SHA-256；`verify` 可重新检查全部安装文件。SDK 归档直接从固定官方 URL 获取，避免 `sdkmanager install` 将同一包名解析为日后更新的 revision。提供 `sdkmanager-list` 与交互式 `sdkmanager-licenses` 入口，使用锁定的官方 arm64 命令行工具，不自动输入 `yes`。

| 组件 | 固定版本 | 下载大小 | 许可证 |
| --- | --- | --- | --- |
| macOS platform-tools / adb | 37.0.1 | 16,110,554 B | android-sdk-license |
| macOS arm64 commandline-tools | 23.0，构建 16111833 | 155,384,151 B | android-sdk-license |
| Android 14 Google APIs arm64-v8a | API 34，revision 14 | 1,610,393,229 B | android-sdk-arm-dbt-license |

完整条款原文随工具保存，取自 2026-10-31 的 Google 官方仓库元数据，未修改条款文字：

| 许可证全文 | 来源 | 文本 SHA-256 |
| --- | --- | --- |
| [android-sdk-license](licenses/android-sdk-license.txt) | [repository2-3.xml](https://dl.google.com/android/repository/repository2-3.xml) | `1f8729233617b193fd619213792ae16a41b95d2bbbf525dfe66998252ba68b16` |
| [android-sdk-arm-dbt-license](licenses/android-sdk-arm-dbt-license.txt) | [sys-img2-3.xml](https://dl.google.com/android/repository/sys-img/google_apis/sys-img2-3.xml) | `0682da42e0ca119ce29503c265587f8ae17277094f9262b54c1d2bb99557c6f5` |

先展示条款，只有人已阅读并明确接受这些条款后才执行相应 `accept-license`。接受记录仅保存在本任务目录；不从其他 SDK 复制许可，不因自动化授权推断法律条款已经接受。

```sh
python3 ports/macos/runtime/runtime.py licenses --id android-sdk-license
python3 ports/macos/runtime/runtime.py licenses --id android-sdk-arm-dbt-license

# 仅在人明确接受对应完整条款后执行：
python3 ports/macos/runtime/runtime.py accept-license --id android-sdk-license \
  --sha256 1f8729233617b193fd619213792ae16a41b95d2bbbf525dfe66998252ba68b16
python3 ports/macos/runtime/runtime.py accept-license --id android-sdk-arm-dbt-license \
  --sha256 0682da42e0ca119ce29503c265587f8ae17277094f9262b54c1d2bb99557c6f5
```

## 组件准备与开机

所有命令可在子命令前传 `--root /绝对路径` 改用其他独立目录。非空且没有本工具标记的目录会被拒绝；不覆盖未验证的已存在组件。

```sh
python3 ports/macos/runtime/runtime.py info
python3 ports/macos/runtime/runtime.py install platform-tools

# 可选，只有需要官方 sdkmanager 时才下载：
python3 ports/macos/runtime/runtime.py install commandline-tools
python3 ports/macos/runtime/runtime.py sdkmanager-list --java-home /锁定的JDK路径
python3 ports/macos/runtime/runtime.py sdkmanager-licenses --java-home /锁定的JDK路径

# 等核心编译可行，并得到真实的 macOS emulator 产物后才执行大镜像下载：
python3 ports/macos/runtime/runtime.py install system-image --core-ready /产物/emulator
python3 ports/macos/runtime/runtime.py verify platform-tools
python3 ports/macos/runtime/runtime.py verify system-image
python3 ports/macos/runtime/runtime.py create-avd
python3 ports/macos/runtime/runtime.py start --emulator /产物/emulator --headless
python3 ports/macos/runtime/runtime.py wait-boot --seconds 180
python3 ports/macos/runtime/runtime.py shm-check --samples 5
python3 ports/macos/runtime/runtime.py stop
python3 ports/macos/runtime/runtime.py stop-server

# 上一实例正常停止后，省略 --headless 重新验证带窗口运行：
python3 ports/macos/runtime/runtime.py start --emulator /产物/emulator
```

镜像下载前会执行 `--core-ready` 的 `-version`，须返回 Android Emulator 版本信息。这个门禁只确认运行入口存在；它不代替编译结果、签名、HVF 或开机验收。

运行配置采用独立命名的 QA AVD、1280×720、4 GiB 客体内存、6 GiB 数据分区。ADB server 使用 `127.0.0.1:20050`，实例使用 console 20060、ADB transport 20061、带 token 的 gRPC 20062。启动前检查端口占用并执行 `-accel-check`。不接管已有监听端口，不终止共享 ADB server，不自动安装或启动游戏。

ADB server 禁用 mDNS 自动连接和其他 emulator 端口扫描；`--one-device` 将 USB 枚举限制为本任务的 emulator 串号。所有设备命令都显式指定私有服务与串号，并在停止/截图前核实记录的 PID、进程启动时间及 guest AVD 名称。正常停止先 `shell sync`，再 `emu kill`。

启动参数沿用 ModAVD 的冷启动、host GPU、关闭声音与 QuickbootFileBacked、Vulkan 批量描述符更新、磁盘 write-through 和 free-page-reporting 组合。Darwin 显式设 `ANDROID_EMU_VK_HIDE_REBAR=0`，避免继承 NVIDIA 专用设置后隐藏 Apple 统一内存的 host-visible 类型。这组配置已用于本机开机与下述客体采集；参数存在和成功开机不等于各项优化效果已验证。

## 空间停线

准备下载/解压前按锁定大小和 ZIP 展开大小检查空间，下载过程中持续检查。保留 20 GiB 是本任务保护阈值，不是 macOS 构建峰值估算。断点归档使用 `.part` 并支持 HTTP Range 续传，校验不符不解压；安装完成会删除下载归档，仅保留安装内容与校验收据。

启动模拟器后自动启动独立 watchdog，每 2 秒检查余量。低于 20 GiB 时先尝试同步并退出本任务实例；guest 不可达时，仅对启动时间仍一致的任务进程发送 SIGTERM。监控有采样和关机延迟，不能保证可用空间精确停在 20 GiB。不会删除个人文件、其他实例、源码或构建断点。

## POSIX SHM 验证

`shm-check` 仅对本工具启动的实例执行 `adb emu screenrecord webrtc start`，严格解析 `videmulator20060` 与 `OK`，调用原生 C++ 探针读取，再在退出时停止流。POSIX 直接使用返回的名称，**不加 Windows 的 `SHM_` 前缀**。

帧协议为小端 24 字节头：`uint32 width, height, fps, frameNumber; uint64 timestampUs`，随后是紧密排列的 `width × height × 4` 字节 BGRA。探针检查尺寸、时间戳、映射长度和负载边界，输出 JSON 样本、内容指纹及帧号是否变化。静态界面可能停止发布新帧，默认不要求帧号递增；单独运行探针时可用 `--require-progress` 检验动态画面。

双读头部一致只表示复制前后头部未变；该上游接口没有锁或序列锁，不能据此保证任意时刻整帧无撕裂。默认探针只输出样本和指纹，不保存或上传像素；本次专项验收另将真实 SHM 桌面帧保存为本地 PNG。不注入其他产品运行时。

## 2026-10-31 实机状态

| 项目 | 已有证据与边界 |
| --- | --- |
| 官方许可与 SDK | 两份接受收据位于 `.macos-work/runtime/receipts/`。官方 macOS platform-tools 37.0.1 与 Android 14 Google APIs `arm64-v8a` API 34 revision 14 已安装，并按安装收据重新校验通过；可选 commandline-tools 不计入本次已安装范围。 |
| 真实开机 | 同一独立 QA AVD 分别完成 headless 与 Qt 启动；`logs/emulator.log` 记录两次 `Boot completed`，客体采集确认 ARM64、API 34 与 `boot_completed=1`。这不是 Manager 新建实例生命周期的验收。 |
| 真实 POSIX SHM | `.macos-work/qa/shm-first-boot.log` 记录 `videmulator20060` 的 1280×720、24 字节头和 BGRA 负载；桌面帧保存为 `.macos-work/qa/shm-first-boot.png`。该次静态画面的帧号未变化，尚未验证动态帧、所有颜色/旋转或并发消费者。 |
| GLES / Vulkan | SurfaceFlinger 报告 Apple M4 GLES 3.0，Vulkan 枚举到 Apple M4、API 1.3.0；仅确认设备枚举，尚未覆盖真实游戏绘制。 |
| 客体网络 | 客体经 `10.0.2.2` 从宿主 loopback HTTP 服务读取 33,792 B 固定载荷，SHA-256 相符，任务临时目录已清理；尚未覆盖游戏 CDN、吞吐和并发稳定性。 |
| Balloon / reporting | driver symlink 可见 `virtio_balloon`，但 device/features 读取被拒绝，未确认 reporting feature 协商，也未验证物理内存归还。 |

客体原始报告、哈希、权限受限项及内核配置之间尚未解释的差异见 [GUEST-CHECKS.md](../qa/GUEST-CHECKS.md)。Qt 窗口首次显隐记录另存于 `.macos-work/qa/window-visibility-first.json`；D0004 后续第二屏显隐修复的增量构建已成功返回 0，日志为 `.macos-work/build-window-fix.log`，仍需修复后重新验收。

首轮完整包曾以 406 个 APFS clone、0 个普通复制完成装配，核心目录与 `.app` 的严格签名检查通过，但该轮使用了较早的 Manager 和 D0004 输入，不能代表最终组合。后续 Manager 产物位于 `.macos-work/manager-dist/`，补丁 SHA-256 为 `acc3c046e9eecfdc85711c2bf4322358c91776d8025b91d654cebfb8a4240a09`；最新输入已另装配至 `.macos-work/package/ModAVD-validated`，同为 406 clone、0 普通复制。该组合的运行验证以及 Manager `create/start/stop` 生命周期仍按独立验收记录确认。本页不宣称完整宿主 IME 往返、明日方舟或物理内存回收已经通过。

## 合成验证

```sh
python3 -m unittest discover -s ports/macos/qa -p 'test_*.py' -v
```

测试只在临时目录和自建 POSIX SHM 上运行，覆盖校验错误、路径穿越、执行权限、许可证下载门禁、低空间仍可构造停机环境、陈旧 PID 拒绝操作，以及正确/截断/无效尺寸/静态帧/Windows 前缀。不会启动 ADB、模拟器或连接现有设备。
