# macOS 随包维护工具

Python 3.9+ 标准库实现，直接维护已有 ModAVD macOS 包，不要求开发 runtime 标记，不会启动或停止 ADB、模拟器、游戏或其他应用。无需第三方 Python 依赖。入口在导入其他模块前设置 `sys.dont_write_bytecode=True`，启动器同时使用 `python3 -B`，避免向不可变安装目录写入 `__pycache__`。

## 安装包集成约定

将以下文件逐个复制到包内 `tools/maintenance/`，保持相对目录，不复制测试或缓存：

```text
maintenance.py
components.py
diagnostics.py
adb_readonly.py
components.lock.json
README.md
licenses/android-sdk-license.txt
licenses/android-sdk-arm-dbt-license.txt
```

这些是随包静态文件，应纳入 `PACKAGE-MANIFEST.json`。安装包根目录保留 `.modavd-macos-package.json`、`mas-avd.json`。组件修复接受现有已装配包，不调用开发用 `runtime.py.initialize()`，也不更改该模块的目录保护语义。

三个启动器可按如下方式映射。`<包根>` 必须由启动器自身所在位置计算，且整个路径加引号：

```sh
# tools/fetch-components：默认修复两项必需组件；额外参数原样传递
exec python3 -B "<包根>/tools/maintenance/maintenance.py" --root "<包根>" components repair "$@"

# tools/collect-info：启动器可提供桌面上不重名的默认 --output，也可要求用户指定
exec python3 -B "<包根>/tools/maintenance/maintenance.py" --root "<包根>" collect "$@"

# tools/report-config：默认只输出报告；添加 --live 查游戏，--clipboard 复制
exec python3 -B "<包根>/tools/maintenance/maintenance.py" --root "<包根>" report "$@"
```

许可界面须保留独立入口，不能在 `fetch-components` 前自动调用 `accept-license`。现有包内 `provenance/runtime/` 已有用户明确接受的两份同哈希收据时直接复用，不重新要求接受，不重写原收据。

## 补全与修复组件

默认两项与原随包用途一致。不会将 IME、µLauncher、第三方 APK、自编模拟器或新的系统镜像冒充 Google 官方组件，也不提供会自动追新版本的 `sdkmanager install`：

| 组件 | 固定版本 | 官方归档 | 字节数 |
| --- | --- | --- | --- |
| platform-tools | 37.0.1 / Darwin | `platform-tools_r37.0.1-darwin.zip` | 16,110,554 |
| system-image | API 34 / Google APIs / arm64-v8a / r14 | `sys-img/google_apis/arm64-v8a-34_r14.zip` | 1,610,393,229 |

`components.lock.json` 继承开发 runtime 的官方 URL、大小、官方元数据 SHA-1、版本和许可文本哈希，并额外锁定本次已校验官方归档的 SHA-256。这两个 SHA-256 来自 2026-10-31 官方下载的安装收据，不宣称是 Google 发布的 SHA-256。仅两者和大小全部相符的字节可安装。

```sh
python3 -B tools/maintenance/maintenance.py --root . components status
python3 -B tools/maintenance/maintenance.py --root . components repair
python3 -B tools/maintenance/maintenance.py --root . components repair --component platform-tools
```

`status` 只读，重新核对收据中全部文件 SHA-256、文件集合和符号链接。它报告 `missing`、`unverified`、`damaged` 或 `verified`。修复会替换整项组件，只使用固定原始归档，不信任本地现有损坏文件。已验证的同版本跳过。

下载依次尝试：Google 中国下载 `googledownloads.cn` → 腾讯云第三方镜像 `mirrors.cloud.tencent.com/AndroidSDK/` → Google 官方 `dl.google.com`。三者 URL 的相对路径都来自同一个 Google 官方归档，且必须匹配同一组哈希。镜像可能缺文件或不可达，工具会回落；实际可达性尚未逐一联网验收。重定向只接受这三个固定域名。

断点按镜像分别保存在 `.modavd-maintenance/downloads/*.part`；使用 `Accept-Encoding: identity` 与严格 `Content-Range`，服务器忽略 Range 时从头覆盖该镜像断点。网络中断保留断点；完整但哈希错误的断点不会再次复用。归档经验证后才更名并进入解压，默认保留校验通过的缓存，便于离线再次修复。

ZIP 在 `.modavd-maintenance/stage-*` 解压；拒绝路径穿越、绝对路径、反斜杠、大小写冲突、重复路径、链接、特殊文件、加密文件与超预算展开，并核对内部 `Pkg.Revision`。新安装用同文件系统 rename 发布；已有目录用 Darwin `renamex_np(RENAME_SWAP)` 原子交换，失败保留原组件。不采用先删除旧目录再解压的方式。新安装收据原子写入 `.modavd-maintenance/receipts/`；不重写 `provenance/` 或 `PACKAGE-MANIFEST.json`。

同一锁定 SDK 归档应恢复原来的静态组件字节。存在 `PACKAGE-MANIFEST.json` 时，仅对照所选固定 SDK 子树的文件集合、哈希、大小和权限，发布前恢复其原清单权限；`status` 同时识别权限损坏。原包清单没有该 SDK 子树，或与当前锁定归档不同，都会拒绝发布并要求重新装配相符的包，不能通过扫描全包并重写清单来重新信任本地内容。本实现不修改原清单。新缓存、暂存和收据均属于动态目录 `.modavd-maintenance/`，装配/校验应排除该目录。

修复在下载前及发布前核对本包 SDK 的实际进程路径。包内 QEMU 或私有 ADB 仍运行时拒绝修复，提示用户自行关闭；不会关闭进程，不连接任何 ADB。下载/解压须保留 20 GiB 空闲空间，空间不足保留断点。维护有独占文件锁；它不替代 Manager 的启动互斥，因此维护期间请勿同时启动实例。

## 许可入口

```sh
python3 -B tools/maintenance/maintenance.py --root . licenses
python3 -B tools/maintenance/maintenance.py --root . licenses --id android-sdk-license
python3 -B tools/maintenance/maintenance.py --root . licenses --id android-sdk-arm-dbt-license
```

完整英文条款随工具保存，读取时校验其 SHA-256。没有对应同哈希接受收据的新用户必须先阅读全文。只有用户明确接受后，才自行执行：

```sh
python3 -B tools/maintenance/maintenance.py --root . accept-license \
  --id android-sdk-license \
  --confirm 1f8729233617b193fd619213792ae16a41b95d2bbbf525dfe66998252ba68b16 \
  --i-have-read-and-accept

python3 -B tools/maintenance/maintenance.py --root . accept-license \
  --id android-sdk-arm-dbt-license \
  --confirm 0682da42e0ca119ce29503c265587f8ae17277094f9262b54c1d2bb99557c6f5 \
  --i-have-read-and-accept
```

收据复用顺序为 `.modavd-maintenance/receipts/`、根目录已有 `receipts/`、`provenance/runtime/`。只复用明确包含 `id`、完整条款 `sha256` 和非空 `acceptedAt` 的记录，不从其他 SDK 推断接受，不自动输入 yes，不联网下载许可替换随包锁定全文。

## 问题包与配置报告

```sh
# 默认只读宿主和包内白名单文件；输出文件必须不存在
python3 -B tools/maintenance/maintenance.py --root . collect --output ~/Desktop/ModAVD-info.zip

# 可选：查询已经启动、已完成开机且通过归属验证的实例
python3 -B tools/maintenance/maintenance.py --root . collect --output ~/Desktop/ModAVD-live.zip --live

# 可选：完整屏幕含未经像素脱敏的画面；公开前须查看或移除 live/*.png
python3 -B tools/maintenance/maintenance.py --root . collect --output ~/Desktop/ModAVD-screen.zip --live --screenshot

python3 -B tools/maintenance/maintenance.py --root . report
python3 -B tools/maintenance/maintenance.py --root . report --live --clipboard
```

默认问题包包含：

- `summary.json`：macOS/CPU/内存等硬件规格、白名单组件版本及实例参数；无用户名、主机名、完整路径或账号字段。
- `logs/emulator-NN.txt`：仅根 `logs/` 内模拟器/Manager 命名的文本日志，以及实例内固定的 `emulator.log`、`qemu.log`、`launch.log`；最多 8 份，每份取末尾至多 2 MiB。账号/凭据整行移除，路径、邮箱、URL、IP、MAC、UUID、长标识和手机号过滤。不会递归收集任意日志目录。
- `crash/crash-NN.json`：本用户 `~/Library/Logs/DiagnosticReports/` 下，近 7 日最多 3 份、进程路径属于本包 emulator/tools/app 的 `.ips` 或 `.crash` 的白名单文本摘要。保留异常类型、信号、终止原因等；不含原始 IPS、线程内存或二进制 minidump。
- `crash/crashpad-inventory.json`：仅查看默认 `/tmp/android-<当前用户>/emu-crash-37.2.10-mas.db/{pending,completed}/`，记录近 7 日最新 3 份本用户所有的规则 UUID `.dmp` 的时间、大小和状态，不读取转储字节或保留文件名。此数据库由同版本 ModAVD 共用，无法将记录归属到某一份安装，报告会明确标注；不检查官方无 `-mas` 数据库。自定义 `ANDROID_TMP` 的数据库不在默认扫描范围内。
- `README-privacy.txt`：准确列出收集范围和可选截图的公开边界。

问题包不包含磁盘镜像、userdata、快照、ADB 密钥、gRPC token、CDK、账号配置、MAS/MAA 配置、进程环境变量或任意原始崩溃内存；不会上传。ZIP 总内容限 20 MiB，权限为 `0600`，完成后原子发布，不覆盖已有文件。输出不得位于 SDK、AVD、tools、许可、provenance 或 app 内。

`--live` 使用原始只读 ADB 协议，仅连接 `127.0.0.1` 上已存在的非 5037 私有服务；先核对监听进程确实是包内 adb，再通过清单校验过的 Manager `info` 核对实例路径、串号、QEMU 可执行文件和精确启动时间。每次查询前后复核，归属不清、实例离线或身份变化时跳过该实例，不创建服务或接管连接。仅开放 boot/API/ABI、第三方应用列表、单包版本和可选截图这些固定只读服务。ADB 不是通过可自动启动 server 的命令行客户端调用的。

配置报告默认输出到终端；`--live` 才读取已开实例的第三方包和版本（明确标注“含游戏”，不将所有应用误认作游戏）。`--clipboard` 才调用 macOS `pbcopy`。报告不读取游戏账号或应用数据。

日志中的任意自定义自由文本不能保证自动识别全部个人信息；公开前仍应查看 ZIP。截图由用户显式选择，完整画面未经像素脱敏，可能含账号，不能把带截图的问题包视为自动匿名资料。

## 验证

```sh
python3 -B -m unittest discover -s ports/macos/maintenance -p 'test_*.py' -v
```

当前 34 项测试使用临时包、模拟 HTTP、模拟实例与自建 loopback ADB 协议对端；不启动真实 ADB 或模拟器，不连接当前私有服务，不读取真实崩溃记录。覆盖许可门禁与收据复用、镜像顺序、Range 续传/重启/错误、双哈希拒绝、安全 ZIP、损坏恢复与原子交换失败保留原目录、原 SDK 清单权限恢复且不重写清单、运行进程拒绝修复、配置/日志脱敏、崩溃时间与归属过滤、Crashpad 仅 stat 不读转储、截图显式入口、5037 拒绝、只读服务白名单及响应后身份复核。真实联网镜像回落、当前游戏截图、剪贴板和真实崩溃报告尚未作为本工具的实机验收。
